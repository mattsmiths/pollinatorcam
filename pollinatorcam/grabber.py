"""
Grab images from camera

- buffer last N frames
- every N seconds, analyze frame for potential triggering
- if triggered, save buffer and continue saving frames
- if not triggered, stop saving
"""

import argparse
import copy
import datetime
import json
import logging
import os
import time

import cv2
import numpy
import systemd.daemon

import tfliteserve

from . import cvcapture
from . import config
from . import dahuacam
#from . import gstcapture
from . import logger
from . import trigger


# cfg data:
# - rois: [(left, top, size),...] if None, auto-compute 1
#  left/top 0-1 scaled by width/height
#  size 0-1 scaled by min(width, height)
# - detector: kwargs used for making detector
# - recording: kwargs used for making recorder
default_cfg = {
    'rois': None,
    'detector': {
        'n_std': 3.0,
        'min_dev': 0.1,
        'threshold': 0.6,
        'allow': {'insects': True},
    },
    'recording': {
        'duty_cycle': 0.1,
        'post_time': 1.0,
        'min_time': 3.0,
        'max_time': 10.0,
    },
}

data_dir = '/mnt/data/'


class Grabber:
    def __init__(
            self, ip, name=None, retry=False,
            fake_detection=False, save_all_detections=True,
            in_systemd=False):
        self.cam = dahuacam.DahuaCamera(ip)
        # TODO do this every startup?
        self.cam.set_current_time()
        if name is None:
            name = self.cam.get_name()
        self.ip = ip

        # TODO configure camera: see dahuacam for needed updates
        #dahuacam.initial_configuration(self.cam, reboot=False)

        logging.info("Starting capture thread: %s", self.ip)
        self.ip = ip
        self.retry = retry
        self.fake_detection = fake_detection
        if self.fake_detection:
            self.last_detection = time.monotonic() - 5.0
        self.start_capture_thread()
        self.crop = None

        self.name = name
        logging.info("Connecting to tfliteserve as %s", self.name)
        self.client = tfliteserve.Client(self.name)

        self.vdir = os.path.join(data_dir, 'videos', self.name)
        if not os.path.exists(self.vdir):
            os.makedirs(self.vdir)

        self.mdir = os.path.join(data_dir, 'detections', self.name)
        if not os.path.exists(self.mdir):
            os.makedirs(self.mdir)

        self.analyze_every_n = 10
        self.frame_count = -1

        self.save_all_detections = save_all_detections
        if self.save_all_detections:
            self.analysis_logger = logger.AnalysisResultsSaver(
                os.path.join(data_dir, 'rawdetections', self.name))

        self.in_systemd = in_systemd
        if self.in_systemd:
            systemd.daemon.notify(systemd.daemon.Notification.READY)
            self.reset_watchdog()
        logging.info("Process in systemd? %s", self.in_systemd)

        self.cfg = default_cfg
        self.cfg_mtime = None
        self.reload_config(force=True)

        self.build_trigger()

    def reload_config(self, force=False):
        mtime = config.get_modified_time(self.name)
        if not force and mtime == self.cfg_mtime:
            # config doesn't exist or was already loaded
            return
        logging.info("Reloading config...")
        old_cfg = copy.deepcopy(self.cfg)
        self.cfg = config.load_config(self.name, self.cfg)
        self.cfg_mtime = mtime
        if mtime is None:
            config.save_config(self.cfg, self.name)
        if self.cfg == old_cfg:
            return
        if (
                (self.cfg['rois'] != old_cfg['rois']) or
                (self.cfg['detector'] != old_cfg['detector'])):
            # force crop to be regenerated
            self.crop = None
        if self.cfg['recording'] != old_cfg['recording']:
            self.build_trigger()

    def build_trigger(self):
        if hasattr(self, 'trigger'):
            logging.debug("existing trigger found, deleting")
            del self.trigger
        logging.debug("Building trigger")
        #self.trigger = trigger.TriggeredRecording(
        #    self.cam.rtsp_url(channel=1, subtype=0),
        #    self.vdir, self.name,
        #    0.1, 1.0, 3.0, 10.0)
        self.trigger = trigger.TriggeredRecording(
            self.cam.rtsp_url(channel=1, subtype=0),
            self.vdir, self.name,
            **self.cfg['recording'])

    def start_capture_thread(self):
        self.capture_thread = cvcapture.CVCaptureThread(
            cam=self.cam, retry=self.retry)
        self.analyze_every_n = 10
        #self.capture_thread = gstcapture.GstCaptureThread(
        #    url=self.cam.rtsp_url(channel=1, subtype=1))
        #self.analyze_every_n = 1
        self.capture_thread.start()

    def __del__(self):
        self.capture_thread.stop()

    def build_crop(self, example_image):
        _, th, tw, _ = self.client.buffers.meta['input']['shape']
        h, w = example_image.shape[:2]
        logging.debug(
            "Building crop for image[%s, %s] to [%s, %s]", h, w, th, tw)
        coords = []
        if self.cfg['rois'] is None:
            # use 1 central roi
            if h > w:
                t = (h // 2) - (w // 2)
                b = t + w
            else:
                t = 0
                b = h
            if w > h:
                l = (w // 2) - (h // 2)
                r = l + h
            else:
                l = 0
                r = w
            logging.debug("ROI: %s, %s, %s, %s", t, b, l, r)
            coords.append((t, b, l, r))
        else:
            for roi in self.cfg['rois']:
                fl, ft, fdim = roi
                if h < w:
                    dim = int(h * fdim)
                else:
                    dim = int(w * fdim)
                l = int(fl * w)
                t = int(ft * h)
                r = l + dim
                b = t + dim
                logging.debug("ROI: %s, %s, %s, %s", t, b, l, r)
                assert l >= 0 and l < w
                assert r > 0 and r <= w
                assert t >= 0 and t < h
                assert b > 0 and b <= h
                coords.append((t, b, l, r))

        # build rois and detectors
        rois = []
        for coord in coords:
            t, b, l, r = coord
            rois.append((
                coord,
                (slice(t, b), slice(l, r)),
                trigger.RunningThreshold(**self.cfg['detector']),
                #trigger.RunningThreshold(
                #    n_std=3.0, min_dev=0.1, threshold=0.6,
                #    allow={'insects': True}),
            ))

        def cf(image):
            for roi in rois:
                coords, slices, detector = roi
                yield (
                    coords,
                    cv2.resize(image[slices], (th, tw), interpolation=cv2.INTER_AREA),
                    detector)
        
        return cf

    def analyze_frame(self, im):
        dt = datetime.datetime.now()
        ts = dt.strftime('%y%m%d_%H%M%S_%f')
        meta = {
            'datetime': dt,
            'timestamp': ts,
        }

        #print("Analyze: %s" % ts)
        set_trigger = False
        if self.fake_detection:
            #print(im.mean())
            #t = im.mean() < 100
            if time.monotonic() - self.last_detection > 5.0:
                set_trigger = True
                self.last_detection = time.monotonic()
        else:
            set_trigger = False
            meta['detections'] = []
            meta['indices'] = []
            meta['rois'] = []
            for patch in self.crop(im):
                coords, cim, detector = patch

                # run classification on cropped image
                o = self.client.run(cim)
                #o[0, 100] = 1.0

                # run detector on classification results
                t, info = detector(o)
                if t:
                    set_trigger = True

                detections = []
                if len(info['indices']):
                    lbls = self.client.buffers.meta['labels']
                    detections = [
                        (str(lbls[i]), o[0, i]) for i in
                        sorted(
                            info['indices'], key=lambda i: o[0, i], reverse=True)]
                meta['detections'].append(detections)
                meta['indices'].append(info['indices'])
                meta['rois'].append(coords)
                
                # TODO
                #if self.save_all_detections:
                #    self.analysis_logger.save(
                #        dt, {'labels': numpy.squeeze(o), 'detection': t})


        if set_trigger:
            logging.debug("Triggered!")
            #print(meta['detections'][0][:5])
        r = self.trigger(set_trigger, meta)

        if set_trigger or r:
            # save trigger meta and last_meta
            dt = self.trigger.meta['datetime']
            d = os.path.join(self.mdir, dt.strftime('%y%m%d'))
            if not os.path.exists(d):
                os.makedirs(d)
            mfn = os.path.join(
                d,
                '%s_%s.json' % (dt.strftime('%H%M%S_%f'), self.name))
            with open(mfn, 'w') as f:
                json.dump(
                    {
                        'meta': self.trigger.meta,
                        'last_meta': self.trigger.last_meta},
                    f, indent=True, cls=logger.MetaJSONEncoder)

    def reset_watchdog(self):
        if not self.in_systemd:
            return
        systemd.daemon.notify(systemd.daemon.Notification.WATCHDOG)
        logging.debug("Reset watchdog")

    def update(self):
        try:
            # TODO wait frame period * 1.5
            r, im, ts = self.capture_thread.next_image(timeout=1.5)
        except RuntimeError as e:
            # next image timed out
            if not self.capture_thread.is_alive():
                logging.info("Restarting capture thread")
                self.start_capture_thread()
                # TODO restart record also?
            else:
                logging.info("Frame grab timed out, waiting...")
            return
        if not r or im is None:  # error
            #raise Exception("Snapshot error: %s" % im)
            logging.warning("Image error: %s", im)
            return False
        
        self.reload_config()

        # have new image
        # check status of trigger
        if not self.trigger.recorder.is_alive():
            self.build_trigger()

        self.frame_count += 1
        #print("Acquired:", self.frame_count)

        # if first frame
        if self.crop is None:
            self.crop = self.build_crop(im)

        # if frame should be checked...
        if self.frame_count % self.analyze_every_n == 0:
            # TODO need to catch errors, etc
            self.analyze_frame(im)

        # reset watchdog
        self.reset_watchdog()

    def run(self):
        while True:
            try:
                self.update()
            except KeyboardInterrupt:
                break


def cmdline_run():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-d', '--save_all_detections', action='store_true',
        help='save all detection results')
    parser.add_argument(
        '-D', '--in_systemd', action='store_true',
        help='running in sysd, reset watchdog')
    parser.add_argument(
        '-f', '--fake', default=False, action='store_true',
        help='fake client detection')
    parser.add_argument(
        '-i', '--ip', type=str, required=True,
        help='camera ip address')
    parser.add_argument(
        '-n', '--name', default=None,
        help='camera name')
    parser.add_argument(
        '-p', '--password', default=None,
        help='camera password')
    parser.add_argument(
        '-r', '--retry', default=False, action='store_true',
        help='retry on acquisition errors')
    parser.add_argument(
        '-u', '--user', default=None,
        help='camera username')
    parser.add_argument(
        '-v', '--verbose', action='store_true',
        help='enable verbose output')
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    if args.password is not None:
        os.environ['PCAM_PASSWORD'] = args.password
    if args.user is not None:
        os.environ['PCAM_USER'] = args.user

    g = Grabber(
        args.ip, args.name, args.retry,
        fake_detection=args.fake, save_all_detections=args.save_all_detections,
        in_systemd=args.in_systemd)
    g.run()

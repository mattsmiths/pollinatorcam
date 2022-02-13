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
from . import v4l2ctl


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
         'allow': '+insects',
    },
    'recording': {
        'save_video': False,
        'duty_cycle': 0.1,
        'post_time': 2.0,
        'min_time': 10.0,
        'max_time': 20.0,
        'periodic_still': 60,  # save every N seconds
    },
    'properties': {
        'fourcc': cv2.VideoWriter_fourcc(*'MJPG'),
        'fps': 30,

        #'fourcc': cv2.VideoWriter_fourcc(*'YUYV'),
        #'fps': 20,  # for 480
        #'fps': 5,  # for 1080, 720
        #'fps': 3,  # for 1944

        'frame_width': 2592,
        'frame_height': 1944,
        #'frame_width': 1920,
        #'frame_height': 1080,
        #'frame_width': 1280,
        #'frame_height': 720,
        #'frame_width': 640,
        #'frame_height': 480,

        'autofocus': 0,
        'focus': 356,

        #'frame_width': 640,
        #'frame_height': 480,
    },
}

data_dir = '/mnt/data/'


class Grabber:
    def __init__(
            self, loc, name=None, retry=False,
            fake_detection=False, in_systemd=False,
            capture_stills=True):
        # check if loc is an ip, if so, assume dahua camera
        if '.' in loc:  # TODO use more robust ip detection
            self.cam = dahuacam.DahuaCamera(loc)
            self.cam.set_current_time()
            if name is None:
                name = self.cam.get_name()
        else:
            # assume usb camera as:
            #  - /dev/videoX
            #  - X_X... (based on bus position)
            device_info = v4l2ctl.find_device_info(loc)
            logging.info("Found device_info: %s", device_info)
            if name is None:
                name = device_info['id']
            # look up bus id
            self.cam = min(
                [d for d in device_info['devices'] if '/dev/video' in d],
                key=lambda s: int(s.split('/dev/video')[1]))
            logging.info("locator string[%s] matched usb camera %s at %s", loc, name, self.cam)
        self.loc = loc

        logging.info("Starting capture thread: %s", self.loc)
        self.retry = retry
        self.fake_detection = fake_detection
        if self.fake_detection:
            logging.info("Faking detection every N seconds")
            self.last_detection = time.monotonic() - 5.0
        self.crop = None

        if '/' in name:
            self.name = name.split('/')[-1]
        else:
            self.name = name
        logging.info("Connecting to tfliteserve as %s", self.name)
        self.client = tfliteserve.Client(self.name)
        # this updates the global mapping between class and index
        trigger.set_mask_labels(self.client.buffers.meta['labels'])

        self.vdir = os.path.join(data_dir, 'videos', self.name)
        if not os.path.exists(self.vdir):
            os.makedirs(self.vdir)

        self.sdir = os.path.join(data_dir, 'stills', self.name)
        if not os.path.exists(self.sdir):
            os.makedirs(self.sdir)

        self.mdir = os.path.join(data_dir, 'detections', self.name)
        if not os.path.exists(self.mdir):
            os.makedirs(self.mdir)

        self.cdir = os.path.join(data_dir, 'configs', self.name)
        if not os.path.exists(self.cdir):
            os.makedirs(self.cdir)

        # analyze frames only every N seconds
        self.analysis_period = 1.0
        self.last_analysis_time = time.monotonic() - self.analysis_period

        #self.analyze_every_n = 30
        self.frame_count = -1

        self.capture_stills = capture_stills

        self.in_systemd = in_systemd
        if self.in_systemd:
            systemd.daemon.notify(systemd.daemon.Notification.READY)
            self.reset_watchdog()
        logging.info("Process in systemd? %s", self.in_systemd)

        self.cfg = default_cfg
        self.cfg_mtime = None
        self.reload_config(force=True)

        self.start_capture_thread()

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
        if self.cfg.get('properties', {}) != old_cfg.get('properties', {}):
            if hasattr(self, 'capture_thread'):
                self.capture_thread.set_properties(self.cfg.get('properties', {}))
        # re-save in 'log' directory
        dt = datetime.datetime.now()
        fn = os.path.join(self.cdir, dt.strftime('%y%m%d_%H%M%S_%f'))
        with open(fn, 'w') as f:
            json.dump(self.cfg, f)

    def build_trigger(self):
        if hasattr(self, 'trigger'):
            logging.debug("existing trigger found, deleting")
            del self.trigger
        logging.debug("Building trigger")
        # how to handle video recording?
        if hasattr(self.cam, 'rtsp_url'):
            url = self.cam.rtsp_url(channel=1, subtype=0)
            self.trigger = trigger.GSTTriggeredRecording(
                url,
                self.vdir, self.sdir, self.name,
                **self.cfg['recording'])
        else:
            url = self.cam
            self.trigger = trigger.CVTriggeredRecording(
                url,
                self.vdir, self.sdir, self.name,
                **self.cfg['recording'])

    def start_capture_thread(self):
        if hasattr(self, 'capture_thread'):
            self.capture_thread.stop()
        self.capture_thread = cvcapture.CVCaptureThread(
            cam=self.cam, retry=self.retry, properties=self.cfg.get('properties', {}),
            capture_period=self.analysis_period)
        #self.analyze_every_n = 10
        #self.analyze_every_n = self.cfg.get('properties', {}).get('fps', 10)
        #self.analyze_every_n = 1
        #self.capture_thread = gstcapture.GstCaptureThread(
        #    url=self.cam.rtsp_url(channel=1, subtype=1))
        #self.analyze_every_n = 1
        self.capture_thread.start()

    def __del__(self):
        if hasattr(self, 'capture_thread'):
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
                set_trigger = not self.trigger.active
                logging.info("Faking detection, flipping trigger to %s" % set_trigger)
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
                    # classifier found something, set the trigger
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

        if set_trigger:
            logging.debug("Triggered!")
            #print(meta['detections'][0][:5])
            if self.capture_stills:
                self.trigger.save_image(im)
        meta['config'] = self.cfg
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

    def generate_thumbnail(self, im):
        # save to config.thumbnail_dir
        fn = os.path.join(config.thumbnail_dir, self.name) + '.jpg'
        # only generate a thumbnail if there is none found
        if not os.path.exists(fn):
            # TODO configure downsampling
            logging.debug("Saving thumbnail to %s", fn)
            #cv2.imwrite(fn, im[::8, ::8, ::-1])
            cv2.imwrite(fn, im[::1, ::1, ::-1])
        else:
            logging.debug("Thumbnail exists, not saving to %s", fn)

    def update(self):
        try:
            # wait analysis period * 1.5
            r, im, ts = self.capture_thread.next_image(timeout=self.analysis_period * 1.5)
        except RuntimeError as e:
            # next image timed out
            if not self.capture_thread.is_alive():
                logging.info("Restarting capture thread: %s", e)
                self.start_capture_thread()
                # TODO restart record also?
            else:
                logging.info("Frame grab timed out, waiting...[%s]" % e)
            return
        if not r or im is None:  # error
            #raise Exception("Snapshot error: %s" % im)
            logging.warning("Image error: %s", im)
            return False
        
        self.reload_config()

        # have new image

        # check status of trigger
        if hasattr(self.trigger, 'recorder') and not self.trigger.recorder.is_alive():
            logging.info("Building trigger, recorder thread was stopped")
            self.build_trigger()

        # allow trigger to buffer images
        self.trigger.new_image(im)

        # TODO downsample and save image for ui to use
        self.generate_thumbnail(im)

        self.frame_count += 1
        #print("Acquired:", self.frame_count)

        # if first frame
        if self.crop is None:
            self.crop = self.build_crop(im)

        # analyze frame
        t = time.monotonic()
        self.analyze_frame(im)
        logging.debug("Analysis delay: %.4f", (t - self.last_analysis_time))
        self.last_analysis_time = t

        #if self.frame_count % self.analyze_every_n == 0:
        #    # TODO need to catch errors, etc
        #    t = time.monotonic()
        #    if hasattr(self, 'last_analysis_time'):
        #        dt = t - self.last_analysis_time
        #        print("Analysis delay: %.4f" % dt)
        #    self.last_analysis_time = t
        #    self.analyze_frame(im)

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
        '-c', '--capture_stills', default=False, action='store_true',
        help='save single images when triggered')
    parser.add_argument(
        '-D', '--in_systemd', action='store_true',
        help='running in sysd, reset watchdog')
    parser.add_argument(
        '-f', '--fake', default=False, action='store_true',
        help='fake client detection')
    parser.add_argument(
        '-l', '--loc', type=str, required=True,
        help='camera locator (ip address or /dev/videoX)')
    parser.add_argument(
        '-n', '--name', default=None,
        help='camera name (overrides automatic name detection)')
    parser.add_argument(
        '-p', '--password', default=None,
        help='camera password')
    parser.add_argument(
        '-P', '--profile', default=False, action='store_true',
        help='profile (requires yappi)')
    parser.add_argument(
        '-r', '--retry', default=False, action='store_true',
        help='retry on acquisition errors')
    parser.add_argument(
        '-t', '--thumbnails', default=False, action='store_true',
        help='save downsampled images as thumbnails')
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

    if args.profile:
        import yappi
        yappi.start()
    g = Grabber(
        args.loc, args.name, args.retry,
        fake_detection=args.fake,
        capture_stills=args.capture_stills,
        in_systemd=args.in_systemd)
    try:
        g.run()
    except KeyboardInterrupt:
        pass

    if args.profile:
        yappi.stop()
        # retrieve thread stats by their thread id (given by yappi)
        threads = yappi.get_thread_stats()
        for thread in threads:
            print(
                "Function stats for (%s) (%d)" % (thread.name, thread.id)
            )  # it is the Thread.__class__.__name__
            yappi.get_func_stats(ctx_id=thread.id).print_all()

"""
Grab images from camera

- buffer last N frames
- every N seconds, analyze frame for potential triggering
- if triggered, save buffer and continue saving frames
- if not triggered, stop saving
"""

import argparse
import datetime
import json
import logging
import os
import time

import cv2
import numpy

import tfliteserve

from . import cvcapture
from . import dahuacam
from . import gstcapture
from . import logger
from . import trigger


# TODO include this in config
data_dir = '/mnt/data/'


class Grabber:
    def __init__(
            self, ip, name=None, retry=False,
            fake_detection=False, save_all_detections=True,
            roi=None):
        # TODO use general config here
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

        #def fng(i, meta):
        #    if 'datetime' in meta:
        #        dt = meta['datetime']
        #    else:
        #        dt = datetime.datetime.now()
        #    d = os.path.join(self.vdir, dt.strftime('%y%m%d'))
        #    if not os.path.exists(d):
        #        os.makedirs(d)
        #    return os.path.join(
        #        d,
        #        '%s_%s_%i.mp4' % (dt.strftime('%H%M%S'), self.name, i))

        self.trigger = trigger.TriggeredRecording(
            self.cam.rtsp_url(channel=1, subtype=0),
            0.1, 1.0, 3.0, 10.0, self.vdir, self.name)

        #self.detector = trigger.MaskedDetection(0.5)
        self.detector = trigger.RunningThreshold(
            n_std=3.0, min_dev=0.1, threshold=0.6,
            allow={'insects': True}
            #allow={'birds': True, 'mammals': True}
        )

        self.analyze_every_n = 10
        self.frame_count = -1

        self.save_all_detections = save_all_detections
        if self.save_all_detections:
            self.analysis_logger = logger.AnalysisResultsSaver(
                os.path.join(data_dir, 'rawdetections', self.name))

        # left, right, dimension
        self.roi = roi

    def start_capture_thread(self):
        self.capture_thread = cvcapture.CVCaptureThread(
            cam=self.cam, retry=self.retry)
        self.analyze_every_n = 10
        # TODO retry
        #self.capture_thread = gstcapture.GstCaptureThread(
        #    url=self.cam.rtsp_url(channel=1, subtype=1))
        #self.analyze_every_n = 1
        self.capture_thread.start()

    def __del__(self):
        self.capture_thread.stop()

    def build_crop(self, example_image):
        _, th, tw, _ = self.client.buffers.meta['input']['shape']
        h, w = example_image.shape[:2]
        if self.roi is None:
            if h == th and w == tw:
                return lambda image: image
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
        else:
            l, t, dim = self.roi
            r = l + dim
            b = t + dim
            assert l >= 0 and l < w
            assert r > 0 and r <= w
            assert t >= 0 and t < h
            assert b > 0 and b <= h

        def cf(image):
            # TODO use client input buffer size
            return cv2.resize(image[t:b, l:r], (th, tw), interpolation=cv2.INTER_AREA)
        
        return cf

    def set_roi(self, roi):
        # TODO make this a data class
        assert len(roi) == 3
        assert all([isinstance(i, int) for i in roi])
        self.roi = roi
        self.crop = None

    def analyze_frame(self, im):
        dt = datetime.datetime.now()
        ts = dt.strftime('%y%m%d_%H%M%S_%f')
        meta = {
            'datetime': dt,
            'timestamp': ts,
        }
        if self.roi is not None:
            meta['roi'] = self.roi

        #print("Analyze: %s" % ts)
        if self.fake_detection:
            #print(im.mean())
            t = im.mean() < 100
            #if time.monotonic() - self.last_detection > 5.0:
            #    t = True
            #    self.last_detection = time.monotonic()
        else:
            cim = self.crop(im)
            o = self.client.run(cim)
            t, info = self.detector(o)

            # look up detection labels sorted by confidence
            detections = []
            if len(info['indices']):
                lbls = self.client.buffers.meta['labels']
                detections = [
                    (str(lbls[i]), o[0, i]) for i in
                    sorted(
                        info['indices'], key=lambda i: o[0, i], reverse=True)]
            if t:
                print("Triggered on:")
                for d in detections[:5]:
                    k, v = d
                    print("\t%s: %f" % (k, v))
                if len(detections) > 5:
                    print("\t...%i detections total" % len(detections))


            if self.save_all_detections:
                self.analysis_logger.save(
                    dt, {'labels': numpy.squeeze(o), 'detection': t})
            
            # add detection results to meta data
            # - roi
            # - detection indices and values sorted by value
            meta['detections'] = detections
            # - detector info (from info)
            # TODO other info
            meta['indices'] = info['indices']
        r = self.trigger(t, meta)

        if t or r:
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

        self.frame_count += 1
        #print("Acquired:", self.frame_count)

        # if first frame
        if self.crop is None:
            self.crop = self.build_crop(im)

        # if frame should be checked...
        if self.frame_count % self.analyze_every_n == 0:
            # TODO need to catch errors, etc
            self.analyze_frame(im)

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
        '-R', '--roi', default=None, type=str,
        help=(
            'camera subframe roi None to use largest square, '
            'format is left:top:dimension defining a square'))
    parser.add_argument(
        '-u', '--user', default=None,
        help='camera username')
    args = parser.parse_args()

    if args.password is not None:
        os.environ['PCAM_PASSWORD'] = args.password
    if args.user is not None:
        os.environ['PCAM_USER'] = args.user

    if args.roi is not None:
        tokens = args.roi.split(':')
        if len(tokens) != 3:
            raise ValueError(
                "Invalid roi[%s] should be left:top:dimension"
                % args.roi)
        for t in tokens:
            if not t.isdigit():
                raise ValueError(
                    "Invalid roi[%s] token[%s] is not a digit"
                    % (args.roi, t))
        roi = [int(t) for t in tokens]
    else:
        roi = None
        

    g = Grabber(
        args.ip, args.name, args.retry,
        fake_detection=args.fake, save_all_detections=args.save_all_detections,
        roi=roi)
    g.run()

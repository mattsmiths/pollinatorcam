"""
Grab images from camera

- buffer last N frames
- every N seconds, analyze frame for potential triggering
- if triggered, save buffer and continue saving frames
- if not triggered, stop saving
"""

import argparse
import datetime
import io
import logging
import os
import pickle
import threading
import time

import cv2
import numpy
import PIL.Image
import requests

import tfliteserve

from . import dahuacam
from . import gstrecorder
from . import logger
from . import trigger


# TODO include this in config
data_dir = '/mnt/data/'


class CaptureThread(threading.Thread):
    def __init__(self, *args, **kwargs):
        self.cam = kwargs.pop('cam')
        if 'retry' in kwargs:
            self.retry = kwargs.pop('retry')
        else:
            self.retry = False
        kwargs['daemon'] = kwargs.get('daemon', True)
        super(CaptureThread, self).__init__(*args, **kwargs)

        self.url = self.cam.rtsp_url(channel=1, subtype=1)
        self._start_cap()

        self.error = None
        self.keep_running = True

        self.timestamp = None
        self.image = None
        self.image_ready = threading.Condition() 

    def _start_cap(self):
        if hasattr(self, 'cap'):
            del self.cap
        self.cap = cv2.VideoCapture(self.url)

    def _read_frame(self):
        r, im = self.cap.read()
        if not r or im is None:
            raise Exception("Failed to capture: %s, %s" % (r, im))
        with self.image_ready:
            #if self.timestamp is not None:
            #    print("Frame dt:", time.time() - self.timestamp)
            self.timestamp = time.time()
            self.image = im
            self.error = None
            self.image_ready.notify()

    def run(self):
        while self.keep_running:
            try:
                self._read_frame()
            except Exception as e:
                with self.image_ready:
                    self.error = e
                    self.timestamp = time.time()
                    self.image = None
                    self.image_ready.notify()
                if not self.retry:
                    break
                logging.info("Restarting capture: %s", self.url)
                self._start_cap()

    def next_image(self, timeout=None):
        with self.image_ready:
            if not self.image_ready.wait(timeout=timeout):
                raise RuntimeError("No new image within timeout")
            if self.error is None:
                return True, self.image, self.timestamp
            return False, self.error, self.timestamp

    def stop(self):
        if self.is_alive():
            self.keep_running = False
            self.join()

    def __del__(self):
        self.stop()


class Grabber:
    def __init__(self, ip, name=None, retry=False, fake_detection=False):
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
        self.capture_thread = CaptureThread(cam=self.cam, retry=self.retry)
        self.capture_thread.start()
        self.crop = None

        self.name = name
        logging.info("Connecting to tfliteserve as %s", self.name)
        self.client = tfliteserve.Client(self.name)

        self.vdir = os.path.join(data_dir, 'videos', self.name)
        if not os.path.exists(self.vdir):
            os.makedirs(self.vdir)

        def fng(i):
            dt = datetime.datetime.now()
            d = os.path.join(self.vdir, dt.strftime('%y%m%d'))
            if not os.path.exists(d):
                os.makedirs(d)
            return os.path.join(
                d,
                '%s_%s_%i.avi' % (dt.strftime('%H%M%S'), self.name, i))

        self.trigger = trigger.TriggeredRecording(
            self.cam.ip, 0.1, 1.0, 3.0, 10.0, fng)

        # TODO set initial mask from taxonomy
        self.detector = trigger.MaskedDetection(0.5)

        self.analyze_every_n = 10
        self.frame_count = -1

        self.analysis_logger = logger.AnalysisResultsSaver(
            os.path.join(data_dir, 'detection', self.name))

    def __del__(self):
        self.capture_thread.stop()

    def build_crop(self, example_image):
        h, w = example_image.shape[:2]
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

        def cf(image):
            # TODO use client input buffer size
            return cv2.resize(image[t:b, l:r], (224, 224), interpolation=cv2.INTER_AREA)
        
        return cf

    def analyze_frame(self, im):
        dt = datetime.datetime.now()
        ts = dt.strftime('%y%m%d_%H%M%S_%f')

        print("Analyze: %s" % ts)
        if self.fake_detection:
            print(im.mean())
            t = im.mean() < 100
        else:
            cim = self.crop(im)
            o = self.client.run(cim)
            t, info = self.detector(o)
            if t:
                detections = {}
                lbls = self.client.buffers.meta['labels']
                for i in info['indices']:
                    detections[str(lbls[i])] = o[0, i]
                print("Triggered on:")
                for k in sorted(detections, key=lambda k: detections[k])[:5]:
                    print("\t%s: %f" % (k, detections[k]))
                if len(detections) > 5:
                    print("\t...%i detections total" % len(detections))
            self.analysis_logger.save(dt, {'labels': numpy.squeeze(o), 'detection': t})
        self.trigger(t)
    
    def update(self):
        try:
            # TODO wait frame period * 1.5
            r, im, ts = self.capture_thread.next_image(timeout=1.5)
        except RuntimeError as e:
            # next image timed out
            if not self.capture_thread.is_alive():
                logging.info("Restarting capture thread")
                self.capture_thread = CaptureThread(cam=self.cam, retry=self.retry)
                self.capture_thread.start()
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
        '-f', '--fake', default=False, action='store_true',
        help="fake client detection")
    parser.add_argument(
        '-i', '--ip', type=str, required=True,
        help="camera ip address")
    parser.add_argument(
        '-n', '--name', default=None,
        help="camera name")
    parser.add_argument(
        '-p', '--password', default=None,
        help='camera password')
    parser.add_argument(
        '-r', '--retry', default=False, action='store_true',
        help='retry on acquisition errors')
    parser.add_argument(
        '-u', '--user', default=None,
        help='camera username')
    args = parser.parse_args()

    if args.password is not None:
        os.environ['PCAM_PASSWORD'] = args.password
    if args.user is not None:
        os.environ['PCAM_USER'] = args.user

    g = Grabber(args.ip, args.name, args.retry, args.fake)
    g.run()

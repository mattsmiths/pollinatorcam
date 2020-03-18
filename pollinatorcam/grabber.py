"""
Grab images from camera

- buffer last N frames
- every N seconds, analyze frame for potential triggering
- if triggered, save buffer and continue saving frames
- if not triggered, stop saving
"""

import argparse
import io
import logging
import os
import threading
import time

import cv2
import numpy
import PIL.Image
import requests

import tfliteserve

from . import dahuacam
from . import gstrecorder


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
    def __init__(self, ip, name=None, retry=False):
        self.dc = dahuacam.DahuaCamera(ip)
        # TODO do this every startup?
        self.dc.set_current_time()
        if name is None:
            name = self.dc.get_name()
        self.ip = ip

        # TODO configure camera: see dahuacam for needed updates
        #dahuacam.initial_configuration(self.dc, reboot=False)

        logging.info("Starting capture thread: %s", self.ip)
        #self.snapshot_thread = SnapshotThread(ip=ip, retry=retry)
        #self.snapshot_thread.start()
        self.ip = ip
        self.retry = retry
        self.capture_thread = CaptureThread(cam=self.dc, retry=self.retry)
        self.capture_thread.start()

        self.name = name
        logging.info("Connecting to tfliteserve as %s", self.name)
        self.client = tfliteserve.Client(self.name)

        # TODO update: recorder is part of triggerer now
        # start initial recorder
        self.recorder_index = -1
        self.recorder = None
        self.next_recorder()

        self.analyze_every_n = 1
        self.frame_count = -1

        self.last_frame = time.monotonic()

        self.saving = False
        self.hold_on = 1.0
        self._last_untrigger = None
        self.crop = None

    def next_recorder(self):
        if self.recorder is not None:
            self.recorder.stop_recording()
        self.recorder_index += 1
        self.recorder = gstrecorder.Recorder(
            ip=self.ip, filename='test%05i.mp4' % self.recorder_index)
        self.recorder.start()

    def __del__(self):
        #self.snapshot_thread.stop()
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
            return cv2.resize(image[t:b, l:r], (224, 224), interpolation=cv2.INTER_AREA)
        
        return cf

    def analyze_frame(self, im):
        cim = self.crop(im)
        #print("Image[%s]: (%s, %s)" % (cim.shape, cim.min(), cim.max()))
        o = self.client.run(cim)
        if numpy.any(o) > 0.5:  # TODO parse results
            li = o.argmax()
            print("Detected:", self.client.buffers.meta['labels'][li])
            #return True
        return False
    
    def update(self):
        #r, im, ts = self.snapshot_thread.next_snapshot()
        try:
            # TODO wait frame period * 1.5
            r, im, ts = self.capture_thread.next_image(timeout=1.5)
        except RuntimeError as e:
            # next image timed out
            if not self.capture_thread.is_alive():
                logging.info("Restarting capture thread")
                self.capture_thread = CaptureThread(cam=self.dc, retry=self.retry)
                self.capture_thread.start()
                # TODO restart record also?
            else:
                logging.info("Frame grab timed out, waiting...")
            return
        if not r or im is None:  # error
            #raise Exception("Snapshot error: %s" % im)
            logging.warning("Image error: %s", im)
            return False

        self.last_frame = ts
        self.frame_count += 1
        #print("Acquired:", self.frame_count)

        # if first frame
        if self.crop is None:
            self.crop = self.build_crop(im)

        # if frame should be checked...
        if self.frame_count % self.analyze_every_n == 0:
            # analyze frame
            triggered = self.analyze_frame(im)
            #print("Trigger:", triggered)

            if triggered and not self.saving:
                # start saving
                # TODO associate timestamp, results, and recorder filename
                self.recorder.start_recording()
                self.saving = True
                fn = '%s_%i.avi' % (self.name, self.frame_count)
                h, w = im.shape[:2]
                logging.info("Started saving to %s", fn)
                self._last_untrigger = None
                self.hold_on = 1.0
            elif not triggered and self.saving:
                t = time.time()
                if self._last_untrigger is not None:
                    self.hold_on -= t - self._last_untrigger
                self._last_untrigger = t
                if self.hold_on < 0.0:
                    # TODO continue saving for 1 more second?
                    # stop saving
                    self.recorder.stop_recording()
                    self.saving = False
                    # advance recorder to next filename
                    self.next_recorder()
                    logging.info("Stopped saving at frame %i", self.frame_count)
                    self.hold_on = 1.0
                    self._last_untrigger = None

    def run(self):
        while True:
            try:
                self.update()
            except KeyboardInterrupt:
                break


def cmdline_run():
    parser = argparse.ArgumentParser()
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

    g = Grabber(args.ip, args.name)
    g.run()

"""
Grab images from camera

- buffer last N frames
- every N seconds, analyze frame for potential triggering
- if triggered, save buffer and continue saving frames
- if not triggered, stop saving
"""

import argparse
import io
import os
import threading
import time

import cv2
import numpy
import PIL.Image
import requests

import tfliteserve

from . import gstrecorder


class CaptureThread(threading.Thread):
    def __init__(self, *args, **kwargs):
        ip = kwargs.pop('ip')
        if 'retry' in kwargs:
            self.retry = kwargs.pop('retry')
        else:
            self.retry = False
        kwargs['daemon'] = kwargs.get('daemon', True)
        super(CaptureThread, self).__init__(*args, **kwargs)

        self.url = build_camera_url(ip, channel=1, subtype=1)
        self.cap = cv2.VideoCapture(self.url)

        self.error = None
        self.keep_running = True

        self.timestamp = None
        self.image = None
        self.image_ready = threading.Condition() 

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
                print("Restarting capture")
                del self.cap
                self.cap = cv2.VideoCapture(self.url)

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


class SnapshotThread(threading.Thread):
    def __init__(self, *args, **kwargs):
        ip = kwargs.pop('ip')
        if 'retry' in kwargs:
            self.retry = kwargs.pop('retry')
        else:
            self.retry = False
        kwargs['daemon'] = kwargs.get('daemon', True)
        super(SnapshotThread, self).__init__(*args, **kwargs)

        # authentication
        self.da = requests.auth.HTTPDigestAuth(
            os.environ['PCAM_USER'], os.environ['PCAM_PASSWORD'])
        self.url = "http://{user}:{password}@{ip}/cgi-bin/snapshot.cgi".format(
            user=os.environ['PCAM_USER'],
            password=os.environ['PCAM_PASSWORD'],
            ip=ip)
        self.timestamp = None
        self.snapshot = None
        self.error = None
        self.keep_requesting = True

        #self.lock = threading.Lock()
        self.snap_ready = threading.Condition() 

    def _request_snapshot(self):
        r = requests.get(self.url, auth=self.da)
        #with self.lock:
        with self.snap_ready:
            self.timestamp = time.time()
            if r.status_code != 200:
                self.error = r
                self.snapshot = None
            else:
                self.snapshot = numpy.array(PIL.Image.open(io.BytesIO(r.content)))
                self.error = None
            self.snap_ready.notify()

    def run(self):
        while self.keep_requesting:
            try:
                self._request_snapshot()
            except Exception as e:
                with self.snap_ready:
                    self.error = e
                    self.timestamp = time.time()
                    self.snapshot = None
                    self.snap_ready.notify()
                if not self.retry:
                    break

    def next_snapshot(self, timeout=None):
        #with self.lock:
        with self.snap_ready:
            if not self.snap_ready.wait(timeout=timeout):
                raise RuntimeError("No new snapshot within timeout")
            if self.error is None:
                return True, self.snapshot, self.timestamp
            return False, self.error, self.timestamp

    def stop(self):
        if self.is_alive():
            self.keep_requesting = False
            self.join()

    def __del__(self):
        self.stop()


# TODO use dahuacam
def build_camera_url(
        ip, user=None, password=None, channel=1, subtype=0):
    if user is None:
        user = os.environ['PCAM_USER']
    if password is None:
        password = os.environ['PCAM_PASSWORD']
    return (
        "rtsp://{user}:{password}@{ip}:554"
        "/cam/realmonitor?channel={channel}&subtype={subtype}".format(
            user=user,
            password=password,
            ip=ip,
            channel=channel,
            subtype=subtype))


class Grabber:
    def __init__(self, ip, name=None, retry=False):
        """
        Make (and start) snapshot thread
        On new snapshots, acquire
        """
        if name is None:
            name = ip
        self.ip = ip


        self.url = build_camera_url(ip)

        self.fps = 5

        # TODO configure camera
        # TODO turn OFF motion detection it slows snapshots
        # TODO setup snapshot framerate and destination
        # TODO set datetime
        # set camera fps
        da = requests.auth.HTTPDigestAuth(
            os.environ['PCAM_USER'], os.environ['PCAM_PASSWORD'])
        burl = "http://{user}:{password}@{ip}".format(
            user=os.environ['PCAM_USER'],
            password=os.environ['PCAM_PASSWORD'],
            ip=ip)
        r = requests.get(
            burl +
            '/cgi-bin/configManager.cgi?action=setConfig&Encode[0]'
            '.MainFormat[0].Video.FPS={fps}'.format(fps=self.fps),
            auth=da)
        if r.status_code != 200:
            raise ValueError("Failed to set framerate to %s: %s" % (fps, r))
        # set gop
        # TODO gop must be HIGH to allow valve turn on at any time
        # is there a way around this that doesn't inflate the video
        # size?
        gop = 1
        r = requests.get(
            burl +
            '/cgi-bin/configManager.cgi?action=setConfig&Encode[0]'
            '.MainFormat[0].Video.GOP={gop}'.format(gop=gop),
            auth=da)
        if r.status_code != 200:
            raise ValueError("Failed to set gop to %s: %s" % (gop, r))

        # set extra format fps
        efps = 1
        r = requests.get(
            burl +
            '/cgi-bin/configManager.cgi?action=setConfig&Encode[0]'
            '.ExtraFormat[0].Video.FPS={efps}'.format(efps=efps),
            auth=da)
        if r.status_code != 200:
            raise ValueError("Failed to set sub framerate to %s: %s" % (fps, r))



        print("Creating capture thread")
        #self.snapshot_thread = SnapshotThread(ip=ip, retry=retry)
        #self.snapshot_thread.start()
        self.ip = ip
        self.retry = retry
        self.capture_thread = CaptureThread(ip=self.ip, retry=self.retry)
        self.capture_thread.start()

        self.name = name
        print("Connecting to tfliteserve")
        self.client = tfliteserve.Client(self.name)

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
        print("Done __init__")

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
        print("Image[%s]: (%s, %s)" % (cim.shape, cim.min(), cim.max()))
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
                print("Restarting capture thread")
                self.capture_thread = CaptureThread(ip=self.ip, retry=self.retry)
                self.capture_thread.start()
                # TODO restart record also?
            else:
                print("Frame grab timed out, waiting...")
            return
        if not r or im is None:  # error
            #raise Exception("Snapshot error: %s" % im)
            print("Image error: %s" % im)
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
            print("Analyzing frame")
            triggered = self.analyze_frame(im)
            print("Trigger:", triggered)

            if triggered and not self.saving:
                # start saving
                # TODO associate timestamp, results, and recorder filename
                self.recorder.start_recording()
                self.saving = True
                fn = '%s_%i.avi' % (self.name, self.frame_count)
                h, w = im.shape[:2]
                print("Started saving to %s" % fn)
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
                    print("Stopped saving at frame %i" % self.frame_count)
                    self.hold_on = 1.0
                    self._last_untrigger = None

        print("Done")

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

"""
Grab images from camera

- buffer last N frames
- every N seconds, analyze frame for potential triggering
- if triggered, save buffer and continue saving frames
- if not triggered, stop saving
"""

import argparse
import os
import threading
import time

import cv2
import numpy
import requests

import tfliteserve


class CircularBuffer:
    def __init__(self, n_frames, example_image):
        shape = [n_frames, ] + list(example_image.shape)
        self.buffer = numpy.zeros(shape)
        self.n_frames = n_frames
        self.index = 0

    def add_frame(self, frame):
        self.buffer[self.index] = frame
        self.index = (self.index + 1) % self.n_frames

    def get_frames(self):
        # start with oldest frame
        i = (self.index + 1) % self.n_frames
        for _ in range(n_frames):
            yield self.buffer[i]
            i = (i + 1) % self.n_frames

    def reset(self):
        self.buffers[:] = 0
        self.index = 0


class CaptureThread(threading.Thread):
    def __init__(self, url):
        super(CaptureThread, self).__init__(
            daemon=True)
        self.frame = None
        self.url = url
        self.running = threading.Event()
        self.frame_lock = threading.Lock()

    def run(self):
        self.cap = cv2.VideoCapture(self.url)
        self.running.set()
        while self.running.is_set():
            r, im = self.cap.read()
            if not r:
                continue
            with self.frame_lock:
                self.frame = im
    
    def stop(self):
        self.running.clear()
        self.join()

    def get_frame(self):
        with self.frame_lock:
            if self.frame is None:
                return None
            return numpy.copy(self.frame)


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
    def __init__(self, ip, name=None):
        if name is None:
            name = ip

        self.url = build_camera_url(ip)

        self.fps = 5
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

        print("Creating capture thread")
        self.cap = CaptureThread(self.url)
        self.cap.start()
        #self.cap = cv2.VideoCapture(self.url)
        #self.cap = cv2.VideoCapture('/home/graham/Desktop/v1m188.mp4')

        self.name = name
        print("Connecting to tfliteserve")
        self.client = tfliteserve.Client(self.name)

        self.n_buffered_frames = 5
        self.analyze_every_n = 5  # analyze every n_th frame
        self.frame_count = -1

        self.fps_period = 1. / self.fps
        self.last_frame = time.monotonic()

        self.fourcc = cv2.VideoWriter_fourcc(*'X264')

        self.saving = None
        self.crop = None
        self.circular_buffer = None
        print("Done __init__")

    def __del__(self):
        self.cap.stop()

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
            #li = o.argmax()
            #print("Detected:", self.client.buffers.meta['labels'][li])
            return True
        return False
    
    def update(self):
        # if fps period passed...
        t = time.monotonic()
        dt = (t - self.last_frame)
        if dt < self.fps_period:
            # TODO sleep?
            time.sleep(0.03)
            #if dt > 0.1:
            #    time.sleep(0.05)
            return

        # grab frame
        im = self.cap.get_frame()
        if im is None:
            return
        #r, im = self.cap.read()
        #if not r:
        #    return
        self.last_frame = t
        self.frame_count += 1
        #print("Acquired:", self.frame_count)

        # if first frame
        if self.crop is None:
            self.crop = self.build_crop(im)
            self.circular_buffer = CircularBuffer(self.n_buffered_frames, im)

        # if frame should be checked...
        if self.frame_count % self.analyze_every_n == 0:
            # analyze frame
            print("Analyzing frame")
            triggered = self.analyze_frame(im)
            print("Trigger:", triggered)

            if triggered and self.saving is None:
                # start saving
                fn = '%s_%i.avi' % (self.name, self.frame_count)
                h, w = im.shape[:2]
                print("Started saving to %s" % fn)
                #self.saving = cv2.VideoWriter(
                #    fn, self.fourcc, 30, (w, h), 1)
            elif not triggered and self.saving is not None:
                # stop saving
                print("Stopped saving at frame %i" % self.frame_count)
                self.saving = None

        if self.saving is not None:
            print("Saving...")
            self.saving.write(im)

        # add to buffer  # TODO only when not saving?
        print("Adding frame to buffer")
        self.circular_buffer.add_frame(im)
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
        '-u', '--user', default=None,
        help='camera username')
    args = parser.parse_args()

    if args.password is not None:
        os.environ['PCAM_PASSWORD'] = args.password
    if args.user is not None:
        os.environ['PCAM_USER'] = args.user

    g = Grabber(args.ip, args.name)
    g.run()

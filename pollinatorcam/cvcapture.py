import logging
import threading
import time

import cv2


class CVCaptureThread(threading.Thread):
    def __init__(self, *args, **kwargs):
        self.cam = kwargs.pop('cam')
        if 'retry' in kwargs:
            self.retry = kwargs.pop('retry')
        else:
            self.retry = False
        kwargs['daemon'] = kwargs.get('daemon', True)
        super(CVCaptureThread, self).__init__(*args, **kwargs)

        if hasattr(self.cam, 'rtsp_url'):
            self.url = self.cam.rtsp_url(channel=1, subtype=1)
        else:
            self.url = self.cam
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
        # convert to rgb
        if not r or im is None:
            raise Exception("Failed to capture: %s, %s" % (r, im))
        with self.image_ready:
            #if self.timestamp is not None:
            #    print("Frame dt:", time.time() - self.timestamp)
            self.timestamp = time.time()
            self.image = im[:, :, ::-1]  # BGR to RGB
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

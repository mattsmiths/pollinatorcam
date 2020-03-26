import os
import subprocess
import sys
import time
import threading

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib, GObject


url_string = "rtsp://{user}:{password}@{ip}:554/cam/realmonitor?channel=1&subtype=0"

#default_pre_record_time = 1000000000
default_pre_record_time = 1000
#cmd_string = (
#    'rtspsrc location="{url}" !'
#    'queue min-threshold-time={pre_record_time} !'
#    'valve name=valve !'
#    'rtph265depay !'
#    'h265parse !'
#    'mp4mux !'
#    'filesink location={filename}')

cmd_string = (
    'rtspsrc location="{url}" latency={pre_record_time} !'
    'valve name=valve !'
    'rtph265depay !'
    'h265parse !'
    'mp4mux !'
    'filesink location={filename}')

#cmd_string = (
#    'rtspsrc location="{url}" latency={pre_record_time} !'
#    'rtph265depay !'
#    'h265parse !'
#    'mp4mux !'
#    'valve name=valve !'
#    'filesink location={filename}')


class Recorder(threading.Thread):
    _inited = False
    def __init__(self, *args, **kwargs):
        ip = kwargs.pop('ip')
        filename = kwargs.pop('filename')
        if 'user' in kwargs:
            user = kwargs.pop('user')
        else:
            user = os.environ['PCAM_USER']
        if 'password' in kwargs:
            password = kargs.pop('password')
        else:
            password = os.environ['PCAM_PASSWORD']
        if 'pre_record_time' in kwargs:
            pre_record_time = kwargs.pop('pre_record_time')
        else:
            pre_record_time = default_pre_record_time
        super(Recorder, self).__init__(*args, **kwargs)

        if not self._inited or not Gst.is_initialized():
            Gst.init([])
            self._inited = True

        url = url_string.format(user=user, password=password, ip=ip)
        cmd = cmd_string.format(
            url=url, pre_record_time=pre_record_time,
            filename=filename)
        self.pipeline = Gst.parse_launch(cmd)

        self.valve = self.pipeline.get_child_by_name("valve")
        self.valve.set_property('drop', True)
        #self.sink = self.pipeline.get_child_by_name("sink")
        #self.sink.set_property('location', filename)

        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self._on_message_cb = self.bus.connect("message", self.on_message)

        self.start_time = None
        self.recording = False

    def teardown(self):
        if hasattr(self, 'bus'):
            print("!!! Deleting !!!")
            self.bus.disconnect(self._on_message_cb)
            self.bus.remove_signal_watch()
            del self.bus

    def __del__(self):
        self.teardown()

    def on_message(self, bus, message):
        t = message.type
        if t == Gst.MessageType.EOS:
            self.pipeline.set_state(Gst.State.NULL)
            print("!!! End of stream !!!")
            self.playmode = False
            self.loop.quit()
        elif t == Gst.MessageType.ERROR:
            self.pipeline.set_state(Gst.State.NULL)
            err, debug = message.parse_error()
            print("Error: %s[%s]" % (err, debug))
            self.playmode = False
            self.loop.quit()
        #print(t, message)

    def start_recording(self):
        print("Starting recording")
        #self.sink.set_property('location', filename)
        self.start_time = time.monotonic()
        self.valve.set_property('drop', False)
        self.recording = True

    def stop_recording(self, and_join=True):
        self.pipeline.send_event(Gst.Event.new_eos())
        self.recording = False
        if and_join:
            self.join()
            self.teardown()

    def run(self):
        self.playmode = True
        self.loop = GLib.MainLoop()
        self.pipeline.set_state(Gst.State.PLAYING)
        self.loop.run()
        self.playmode = False


def test_recorder(ip='192.168.0.4'):
    class Ticker:
        def tick(self):
            self.t0 = time.monotonic()

        def tock(self):
            self.dt = time.monotonic() - self.t0
            return self.dt

    t = Ticker()
    for fn in ('test_file.mp4', 'test_file2.mp4'):
        # create recorder instance
        r = Recorder(filename=fn, ip=ip)
        # start running (begins filling circular buffer)
        r.start()
        time.sleep(1)  # wait a bit

        # start actually recording (frames will be delayed by buffer)
        t.tick()
        r.start_recording()
        t.tock()
        print("Start recording took", t.dt)

        time.sleep(3)
        # stop recording and join started thread
        t.tick()
        r.stop_recording(and_join=False)
        t.tock()
        print("Stop recording took", t.dt)

        t.tick()
        r.join()
        t.tock()
        print("Joining took", t.dt)
        time.sleep(2)


def test_for_open_files(ip='192.168.0.103'):
    # get process id
    pid = os.getpid()
    get_open_files = lambda: len(
        subprocess.check_output(
            ['lsof', '-p', str(pid)]).decode('ascii').splitlines())
    tnof = None
    for index in range(10):
        fn = '%04i.mp4' % index
        print("Index: %i, fn: %s" % (index, fn))
        r = Recorder(filename=fn, ip=ip, pre_record_time=1000)
        print("\tStarting")
        r.start()
        #time.sleep(1.0)
        r.start_recording()

        print("\tRecording...")
        time.sleep(2.0)

        print("\tStopping...")
        r.stop_recording()

        # print number of open files
        nof = get_open_files()
        if tnof is None:
            tnof = nof
        print("\tDone, open Files: %i" % (nof, ))
        if nof != tnof:
            raise Exception("File open leak")
        #time.sleep(1.0)


if __name__ == '__main__':
    ip = None
    if len(sys.argv) > 1:
        ip = sys.argv[1]
    #test_recorder(ip)
    test_for_open_files(ip)

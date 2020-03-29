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
#default_pre_record_time = 3 * Gst.SECOND  # TODO configure
#cmd_string = (
#    'rtspsrc location="{url} latency=50000000" !'
#    'queue max-size-buffers=0 max-size-bytes=0 max-size-time={pre_record_time} leaky=2 min-threshold-time={pre_record_time} !'
#    'valve name=valve !'
#    'rtph265depay !'
#    'h265parse !'
#    'mp4mux !'
#    'filesink location={filename}')

cmd_string = (  # TODO configure queue latency/max-size-time/etc?
    #'rtspsrc name=src0 location="{url}" latency=0 tcp-timeout=0 teardown-timeout=0 timeout=0 drop-on-latency=true max-rtcp-rtp-time-offset=-1 max-ts-offset=10000000000 ! '
    'rtspsrc name=src0 location="{url}" ! '
    'capsfilter name=caps0 caps=application/x-rtp,media=video ! '
    #'queue name=queue0 leaky=2 max-size-bytes=0 max-size-buffers=0 max-size-time=1000000000 ! '
    'queue name=queue1 max-size-bytes=0 max-size-buffers=0 max-size-time=3000000000 ! '
    'queue name=queue0 max-size-time=1000000 min-threshold-time=1000000000 ! '  # this is the 'delay'
    'fakesink name=fakesink0 sync=false '
)

#default_pre_record_time = 4000
#cmd_string = (
#    'rtspsrc location="{url}" latency={pre_record_time} ! '
#    'valve name=valve ! '
#    'rtph265depay ! '
#    'h265parse ! '
#    'mp4mux ! '
#    'filesink location={filename}')

#default_pre_record_time = 2000
#cmd_string = (
#    'rtspsrc location="{url}" ! '
#    'rtpjitterbuffer latency={pre_record_time} ! '
#    'valve name=valve ! '
#    'rtph265depay ! '
#    'h265parse ! '
#    'mp4mux ! '
#    'filesink location={filename}')

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
        self.url = kwargs.pop('url')
        if 'daemon' not in kwargs:
            kwargs['daemon'] = True
        super(Recorder, self).__init__(*args, **kwargs)

        if not self._inited or not Gst.is_initialized():
            Gst.init([])
            self._inited = True

        #self.pipeline = Gst.Pipeline.new('filewriter')
        self.pipeline = Gst.parse_launch(
            cmd_string.format(url=self.url))

        self.queue = self.pipeline.get_child_by_name("queue0")
        self.fakesink = self.pipeline.get_child_by_name("fakesink0")

        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self._on_message_cb = self.bus.connect("message", self.on_message)

        self.filename = None
        self.playmode = False

    def teardown(self):
        if hasattr(self, 'bus'):
            self.bus.disconnect(self._on_message_cb)
            self.bus.remove_signal_watch()
            del self.bus

    def __del__(self):
        # TODO need clean shutdown
        if self.playmode:
            self.stop_pipeline()

    def on_message(self, bus, message):
        t = message.type
        if t & Gst.MessageType.EOS:
            print("!!! End of stream !!!")
            if self.filename is not None:
                self.stop_filesink()
            else:
                self.pipeline.set_state(Gst.State.NULL)
                self.playmode = False
                self.loop.quit()
        elif t == Gst.MessageType.ERROR:
            self.pipeline.set_state(Gst.State.NULL)
            err, debug = message.parse_error()
            print("Error: %s[%s]" % (err, debug))
            self.playmode = False
            self.loop.quit()
        elif t & Gst.MessageType.LATENCY:
            print("Latency message:", t)
        #print(t, message)

    def stop_element(self, element):
        #print("stop_element")
        element.set_state(Gst.State.NULL)
        return GLib.SOURCE_REMOVE  # needed?

    def stop_filesink(self):
        #print("=======================")
        #print("==== stop filesink ====")
        #print("=======================")
        self.depay.set_locked_state(True)
        self.parse.set_locked_state(True)
        self.mux.set_locked_state(True)
        self.filesink.set_locked_state(True)

        self.depay.set_state(Gst.State.NULL)
        self.parse.set_state(Gst.State.NULL)
        self.mux.set_state(Gst.State.NULL)
        self.filesink.set_state(Gst.State.NULL)

        self.pipeline.remove(self.depay)
        self.pipeline.remove(self.parse)
        self.pipeline.remove(self.mux)
        self.pipeline.remove(self.filesink)

        self.filename = None

    def create_filesink(self, fn):
        # TODO use GstBin instead
        self.depay = Gst.ElementFactory.make('rtph265depay', 'depay0')
        self.parse = Gst.ElementFactory.make('h265parse', 'parse0')
        self.mux = Gst.ElementFactory.make('mp4mux', 'mux0')
        self.filesink = Gst.ElementFactory.make('filesink', 'filesink0')
        self.filesink.set_property('location', fn)
        self.filesink.set_property('async', False)  # don't close async
        # TODO TEST sync False
        self.filesink.set_property('sync', False)  # don't drop non-synced buffered
        # TEST ts_offset + to delay rendering: nope, nope, nope
        #self.filesink.set_property('ts-offset', 3 * Gst.SECOND)
        #self.filesink.set_property('max-lateness', -1)
        #self.filesink.set_property('render-delay', 3 * Gst.SECOND)
        self.filename = fn

        self.pipeline.add(self.depay, self.parse, self.mux, self.filesink)

        self.depay.link(self.parse)
        self.parse.link(self.mux)
        self.mux.link(self.filesink)

    def insert_filesink(self, pad, info, fn):
        print("insert_filesink")
        peer = pad.get_peer()
        pad.unlink(peer)
        self.pipeline.remove(self.fakesink)
        GLib.idle_add(self.stop_element, self.fakesink)

        self.create_filesink(fn)

        # link pad [rtsp src pad] to depay
        pad.link(self.depay.get_static_pad('sink'))
        self.depay.sync_state_with_parent()
        self.parse.sync_state_with_parent()
        self.mux.sync_state_with_parent()
        self.filesink.sync_state_with_parent()
        return Gst.PadProbeReturn.REMOVE  # don't call again

    def insert_fakesink(self, pad, info):
        #print("insert_fakesink")
        peer = pad.get_peer()
        pad.unlink(peer)

        m = Gst.Event.new_eos()
        r = peer.send_event(m)
        if not r:
            print("Failed sending eos to insert_fakesink")

        self.fakesink = Gst.ElementFactory.make('fakesink', 'fakesink0')
        self.pipeline.add(self.fakesink)

        pad.link(self.fakesink.get_static_pad('sink'))
        self.fakesink.sync_state_with_parent()
        return Gst.PadProbeReturn.REMOVE

    def start_saving(self, fn):
        #print("++++++++++++++++++++++")
        #print("++++ Start saving ++++")
        #print("++++++++++++++++++++++")
        # get src pad of queue
        src_pad = self.queue.get_static_pad('src')
        src_pad.add_probe(Gst.PadProbeType.IDLE, self.insert_filesink, fn)
        return

    def stop_saving(self):
        #print("---------------------")
        #print("---- Stop saving ----")
        #print("---------------------")
        src_pad = self.queue.get_static_pad('src')
        src_pad.add_probe(Gst.PadProbeType.IDLE, self.insert_fakesink)
        return

    def stop_pipeline(self, and_join=True):
        m = Gst.Event.new_eos()
        #print("Made EOS")
        r = self.pipeline.send_event(m)
        if not r:
            print("Failed to send eos to pipeline")
        #print("send_event(EOS) = %s" % r)
        #print("Sent EOS")
        if and_join:
            self.join()
            self.teardown()

    def print_pipeline_states(self, and_pads=False):
        for i in range(self.pipeline.get_children_count()):
            try:
                node = self.pipeline.get_child_by_index(i)
                s = node.get_state(0.001)[1]
                if s == Gst.State.NULL:
                    ss = '__ null __'
                elif s == Gst.State.PLAYING:
                    ss = '++ PLAYING ++'
                else:
                    ss = '  %s  ' % s.value_nick
                print(i, '\t', ss, '\t', node.name)
                if not and_pads:
                    continue
                for p in node.pads:
                    print("\t" * 5, p.name, p.is_active())
            except Exception as e:
                print(i, 'ERROR', e)
    
    #def periodic_cb(self):
    #    return GLib.SOURCE_REMOVE
    #    self.print_pipeline_states()
    #    return GLib.SOURCE_CONTINUE

    def _set_latency(self):
        print("Set latency")
        self.pipeline.set_latency(1 * Gst.SECOND)
        print(self.pipeline.get_latency())
        return GLib.SOURCE_REMOVE

    def run(self):
        self.playmode = True
        self.loop = GLib.MainLoop()
        #GLib.timeout_add(1000, self.periodic_cb)

        self.pipeline.set_state(Gst.State.PLAYING)
        GLib.timeout_add(500, self._set_latency)
        self.loop.run()
        self.playmode = False


class OldRecorder(threading.Thread):
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
        super(OldRecorder, self).__init__(*args, **kwargs)

        if not self._inited or not Gst.is_initialized():
            Gst.init([])
            self._inited = True

        url = url_string.format(user=user, password=password, ip=ip)
        cmd = cmd_string.format(
            url=url, pre_record_time=pre_record_time,
            filename=filename)
        self.pipeline = Gst.parse_launch(cmd)
        #self.pipeline.set_latency(pre_record_time * 1000000)

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
        #print(self.pipeline.get_latency())

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
    url = url_string.format(
        user=os.environ['PCAM_USER'],
        password=os.environ['PCAM_PASSWORD'],
        ip=ip)

    class Ticker:
        def tick(self):
            self.t0 = time.monotonic()

        def tock(self):
            self.dt = time.monotonic() - self.t0
            return self.dt

    t = Ticker()
    for fn in ('test_file.mp4', 'test_file2.mp4'):
        # create recorder instance
        r = Recorder(url=url)
        # start running (begins filling circular buffer)
        r.start()
        time.sleep(1)  # wait a bit

        # start actually recording (frames will be delayed by buffer)
        t.tick()
        r.start_saving(fn)
        t.tock()
        print("Start recording took", t.dt)

        time.sleep(3)
        # stop recording and join started thread
        t.tick()
        r.stop_saving()
        t.tock()
        print("Stop recording took", t.dt)

        t.tick()
        r.stop_pipeline()
        t.tock()
        print("Joining took", t.dt)
        time.sleep(2)


def test_for_open_files(ip='192.168.0.103'):
    url = url_string.format(
        user=os.environ['PCAM_USER'],
        password=os.environ['PCAM_PASSWORD'],
        ip=ip)

    # get process id
    pid = os.getpid()
    get_open_files = lambda: len(
        subprocess.check_output(
            ['lsof', '-p', str(pid)]).decode('ascii').splitlines())
    tnof = None
    for index in range(10):
        fn = '%04i.mp4' % index
        print("Index: %i, fn: %s" % (index, fn))
        #r = Recorder(filename=fn, ip=ip, pre_record_time=1000)
        r = Recorder(url=url)
        print("\tStarting")
        r.start()
        #time.sleep(1.0)
        r.start_saving(fn)

        print("\tRecording...")
        time.sleep(2.0)

        print("\tStopping...")
        r.stop_saving()

        print("\tClosing")
        time.sleep(1.0)
        r.stop_pipeline()

        # print number of open files
        nof = get_open_files()
        if tnof is None:
            tnof = nof
        print("\tDone, open Files: %i" % (nof, ))
        #if nof != tnof:
        #    raise Exception("File open leak")
        #time.sleep(1.0)


if __name__ == '__main__':
    ip = None
    if len(sys.argv) > 1:
        ip = sys.argv[1]
    #test_recorder(ip)
    test_for_open_files(ip)

import os
import subprocess
import sys
import time
import threading

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib, GObject

import numpy


url_string = "rtsp://{user}:{password}@{ip}:554/cam/realmonitor?channel=1&subtype=1"

#default_pre_record_time = 1000000000
#default_pre_record_time = 3 * Gst.SECOND  # TODO configure

cmd_string = (  # TODO configure queue latency/max-size-time/etc?
    #'rtspsrc name=src0 location="{url}" latency=0 tcp-timeout=0 teardown-timeout=0 timeout=0 drop-on-latency=true max-rtcp-rtp-time-offset=-1 max-ts-offset=10000000000 ! '
    'rtspsrc name=src0 location="{url}" ! '
    'rtph265depay name=depay0 ! '
    'h265parse name=parse0 ! '
    #'libde265dec name=dec1 ! '
    'avdec_h265 name=dec0 ! '
    'videoconvert ! '
    'videorate ! '
    'video/x-raw,framerate=1/1 ! '
    'videoscale ! '
    'video/x-raw,width=330,height=225 ! '
    'videocrop left=-1 right=-1 top=-1 bottom=-1 ! '
    'video/x-raw,width=224,height=224 ! '
    #'videocrop left=53 right=227 top=0 bottom=224 ! '
    'appsink name=appsink0 max-buffers=1 drop=true emit-signals=true sync=false async=false '
)


class GstCaptureThread(threading.Thread):
    _inited = False
    def __init__(self, *args, **kwargs):
        self.url = kwargs.pop('url')
        # TODO retry
        if 'daemon' not in kwargs:
            kwargs['daemon'] = True
        super(GstCaptureThread, self).__init__(*args, **kwargs)

        if not self._inited or not Gst.is_initialized():
            Gst.init([])
            self._inited = True

        cmd = cmd_string.format(url=self.url)
        print(cmd)
        self.pipeline = Gst.parse_launch(cmd)

        self.appsink = self.pipeline.get_child_by_name("appsink0")
        if self.appsink is not None:
            #caps = Gst.caps_from_string(
            #    'video/x-raw, format=(string){BGR, GRAY8}; '
            #    'video/x-bayer,format=(string){rggb,bggr,grbg,gbrg}')
            caps = Gst.caps_from_string('video/x-raw, format=(string)RGB')
            self.appsink.set_property('caps', caps)
            self.appsink.connect("new-sample", self.new_buffer, self.appsink)

        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self._on_message_cb = self.bus.connect("message", self.on_message)

        self.playmode = False

        self.timestamp = None
        self.image = None
        self.error = None
        self.image_ready = threading.Condition() 

    def gst_to_opencv(self, sample):
        # This leaks memory
        buf = sample.get_buffer()
        caps = sample.get_caps()
        # Print Height, Width and Format
        # print(caps.get_structure(0).get_value('format'))
        # print(caps.get_structure(0).get_value('height'))
        # print(caps.get_structure(0).get_value('width'))

        arr = numpy.ndarray(
            (caps.get_structure(0).get_value('height'),
             caps.get_structure(0).get_value('width'),
             3),
            buffer=buf.extract_dup(0, buf.get_size()),
            dtype=numpy.uint8)
        print(arr.shape, arr.dtype, arr.mean())
        with self.image_ready:
            self.timestamp = time.time()
            self.image = arr.copy()
            self.error = None
            self.image_ready.notify()
        del arr
        #self.new_image = True
        #return arr

    def new_buffer(self, sink, _):
        sample = sink.emit("pull-sample")
        self.gst_to_opencv(sample)
        return Gst.FlowReturn.OK

    def teardown(self):
        if hasattr(self, 'bus'):
            self.bus.disconnect(self._on_message_cb)
            self.bus.remove_signal_watch()
            del self.bus

    def __del__(self):
        if self.playmode:
            self.stop_pipeline()

    def on_message(self, bus, message):
        t = message.type
        if t & Gst.MessageType.EOS:
            print("!!! End of stream !!!")
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
    
    def stop(self):
        self.stop_pipeline(and_join=True)

    def next_image(self, timeout=None):
        with self.image_ready:
            if not self.image_ready.wait(timeout=timeout):
                raise RuntimeError("No new image within timeout")
            if self.error is None:
                return True, self.image, self.timestamp
            return False, self.error, self.timestamp


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
        #GLib.timeout_add(500, self._set_latency)
        self.loop.run()
        self.playmode = False


def test_capture(ip='192.168.0.120'):
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

    cap = GstCaptureThread(url=url)
    cap.start()
    for _ in range(15):
        try:
            im = cap.next_image()
        except KeyboardInterrupt:
            pass
    cap.stop()
    return


def test_for_open_files(ip='192.168.0.103'):
    return  # TODO rewrite to test for memory leak
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
    ip = '192.168.0.120'
    if len(sys.argv) > 1:
        ip = sys.argv[1]
    test_capture(ip)
    #test_for_open_files(ip)

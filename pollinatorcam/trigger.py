"""
Trigger:
    - takes in labels/predictions
        - compare against label mask (initally based on taxonomy)
        - turns trigger on/off
    - called periodically (in ioloop)
        - if rising edge, start recording
        - if falling edge, record (+1 second)
        - if still triggered, record at some max duty cycle
        - if no trigger, nothing...

So states will be:
    not triggered, not recording
    not triggered, post-recording
    trigger started, start recording
    triggered, recording
    triggered, duty cycle limited
    trigger stopped, if not recording, record 1 second

Duty cycle limit only during triggered period
    record initial 10 seconds
    if still tiggered, hold off for 90 seconds
    if during hold off, trigger falls, re-start recording
    if hold off finished, restart recording
"""

import datetime
#import json
import logging
import time
import os

import numpy

from . import gstrecorder


N_CLASSES = 2988
mask_consts = {
    'insects': [(True, ('slice', 75, 1067)), (True, 2291)],
    'birds': [(True, ('slice', 1103, 1589)), ],
    'mammals': [(True, ('slice', 1589, 1638)), ],
}


def make_allow(insects=False, birds=False, mammals=False):
    allow = numpy.zeros(N_CLASSES)
    if insects:
        allow[75:1067] = 1
        allow[2291] = 1
    if birds:
        allow[1103:1589] = 1
    if mammals:
        allow[1589:1638] = 1
    return allow


def update_mask(mask, valence, operation):
    """
    valence is True/False for allow/deny
    operation is:
        index range: ['slice', 75, 1067]
        individual index: 42
        list of indices:  [3, 1, 4]
    """
    if isinstance(operation, int):
        mask[operation] = valence
    elif isinstance(operation, (list, tuple)):
        if len(operation) == 0:
            return mask
        if operation[0] == 'slice':
            mask[slice(*operation[1:])] = valence
        else:
            mask[operation] = valence
    elif isinstance(operation, str):
        if operation not in mask_consts:
            raise ValueError("Unknown update_mask operation: %s" % (operation, ))
        for op in mask_consts[operation]:
            mask = update_mask(mask, *op)
    else:
        raise ValueError("Unknown update_mask operation: %s" % (operation, ))
    return mask


def make_allow_mask(*ops):
    """
    ops are: (True/False, operation) (see update_mask)
    """
    # if first op is deny (or missing) allow all
    if (len(ops) == 0) or (not ops[0][0]):
        mask = numpy.ones(N_CLASSES, dtype=bool)
    else:  # else (first op is allow) start by denying all
        mask = numpy.zeros(N_CLASSES, dtype=bool)
    for op in ops:
        mask = update_mask(mask, *op)
    logging.debug("Made allow mask: %s", mask)
    return mask


class RunningThreshold:
    def __init__(self, min_n=10, n_std=3.0, min_dev=0.1, threshold=0.9, allow=None):
        self.min_n = min_n
        self.n_std = n_std
        self.min_dev = min_dev
        self.static_threshold = threshold
        if isinstance(allow, (list, tuple)):
            allow = make_allow_mask(*allow)
        self.allow = allow

        self.buffers = None
        self.mean = None
        self.std = None
        self.thresholds = None

    def make_buffers(self, b):
        self.buffers = numpy.empty((self.min_n, len(b)))
        self.index = -self.min_n
        self.thresholds = numpy.ones_like(b) * self.static_threshold
        if self.allow is None:
            self.allow = numpy.ones_like(b, dtype=bool)
    
    def update_buffers(self, b):
        if self.buffers is None:
            self.make_buffers(b)
        self.buffers[self.index] = b
        if self.index < 0:  # incomplete buffers
            self.index += 1
            # use default thresholds or don't trigger
            self.mean = None
            self.std = None
        else:
            self.index = (self.index + 1) % self.min_n
            # recompute mean and std
            self.mean = numpy.mean(self.buffers, axis=0)
            self.std = numpy.std(self.buffers, axis=0)

    def check(self, b):
        b = numpy.squeeze(b)
        self.update_buffers(b)
        d = b > self.thresholds
        if self.mean is not None:
            dev = self.std * self.n_std
            dev[dev < self.min_dev] = self.min_dev
            # use running avg
            d = numpy.logical_or(
                d,
                numpy.abs(b - self.mean) > dev)
        md = numpy.logical_and(d, self.allow)
        info = {
            'masked_detection': md,
            'indices': numpy.nonzero(md)[0],
        }
        return numpy.any(md), info
    
    def __call__(self, b):
        return self.check(b)


class Trigger:
    def __init__(
            self, duty_cycle, post_time, min_time, max_time):
        self.duty_cycle = duty_cycle
        self.min_time = min_time
        self.max_time = max_time
        self.post_time = post_time
        self.total_time = max_time + post_time
        self.triggered = False

        self.times = {}
        self.meta = {}

        self.active = None

    def activate(self, t):
        self.times['start'] = t
        self.active = True
    
    def deactivate(self, t):
        self.active = False

    def rising_edge(self):
        self.times['rising'] = time.monotonic()
        if not self.active:
            self.activate(self.times['rising'])
            return True
        return False

    def falling_edge(self):
        self.times['falling'] = time.monotonic()
        if 'hold_off' in self.times:
            del self.times['hold_off']
        if not self.active:
            self.activate(self.times['falling'])
            return True
        return False

    def high(self):
        t = time.monotonic()
        if 'rising' not in self.times:
            self.rising_edge()
        # check duty cycle
        if self.active:
            if t - self.times['start'] >= self.max_time:
                # stop recording, go into hold off
                self.deactivate(t)
                self.times['hold_off'] = t + 1. / self.duty_cycle * self.total_time
        else:
            if 'hold_off' in self.times and t >= self.times['hold_off']:
                self.activate(t)
                return True
        return False

    def low(self):
        if self.active:
            t = time.monotonic()
            if 'falling' not in self.times:
                self.falling_edge()
            # stop after post_record and min_time
            if (
                    (t - self.times['falling'] >= self.post_time) and
                    (t - self.times['start'] >= self.min_time)):
                self.deactivate(t)
        return False

    def set_trigger(self, trigger, meta):
        self.last_meta = self.meta
        self.meta = meta
        if self.triggered:
            if trigger:
                self.meta['state'] = 'high'
                r = self.high()
            else:
                self.meta['state'] = 'falling_edge'
                r = self.falling_edge()
        else:
            if trigger:
                self.meta['state'] = 'rising_edge'
                r = self.rising_edge()
            else:
                self.meta['state'] = 'low'
                r = self.low()
        self.triggered = trigger
        return r

    def __call__(self, trigger, meta):
        return self.set_trigger(trigger, meta)


class TriggeredRecording(Trigger):
    def __init__(
            self, url, directory, name,
            duty_cycle=0.1, post_time=1.0, min_time=3.0, max_time=10.0):
        self.directory = directory
        self.name = name
        #self.filename_gen = filename_gen
        super(TriggeredRecording, self).__init__(
            duty_cycle, post_time, min_time, max_time)

        #self.ip = ip
        self.url = url
        # TODO pre record time
        self.index = -1
        #self.recorder_index = -1
        #self.recorder = None
        #self.next_recorder()
        self.recorder = gstrecorder.Recorder(url=self.url)
        self.recorder.start()

        self.filename = None

    #def next_recorder(self):
    #    if self.recorder is not None:
    #        print("~~~ Stop recording ~~~")
    #        t = time.monotonic()
    #        print(
    #            "Recorded %s second long video" %
    #            (t - self.recorder.start_time))
    #        self.recorder.stop_recording()
    #    self.recorder_index += 1
    #    fn = self.filename_gen(self.recorder_index)
    #    logging.info("Buffering to %s", fn)
    #    print("~~~ Buffering to %s ~~~" % fn)
    #    self.recorder = gstrecorder.Recorder(ip=self.ip, filename=fn)
    #    self.recorder.start()

    def video_filename(self, meta):
        if 'datetime' in meta:
            dt = meta['datetime']
        else:
            dt = datetime.datetime.now()
        d = os.path.join(self.directory, dt.strftime('%y%m%d'))
        if not os.path.exists(d):
            os.makedirs(d)
        return os.path.join(
            d,
            '%s_%s.mp4' % (dt.strftime('%H%M%S_%f'), self.name))

    def activate(self, t):
        super(TriggeredRecording, self).activate(t)
        if self.recorder.filename is not None:
            self.recorder.stop_saving()  # TODO instead switch files?

        # make new filename
        self.index += 1
        self.meta['video_index'] = self.index
        self.meta['camera_name'] = self.name
        vfn = self.video_filename(self.meta)
        self.meta['filename'] = vfn
        #fn = self.filename_gen(self.index, self.meta)

        # TODO wait for stop_saving to finish?

        # start saving
        logging.info("Saving to %s", vfn)
        print("~~~ Started recording [%s] ~~~" % vfn)
        self.recorder.start_saving(vfn)
        self.filename = vfn

        # save meta (and last_meta) data here
        #mfn = os.path.splitext(vfn)[0] + '.json'
        #with open(mfn, 'w') as f:
        #    json.dump(
        #        {'meta': self.meta, 'last_meta': self.last_meta},
        #        f, indent=True, cls=MetaEncoder)

        #if self.recorder.recording:
        #    self.next_recorder()
        ## TODO log filename, time
        #self.recorder.start_recording()

    def deactivate(self, t):
        super(TriggeredRecording, self).deactivate(t)
        #print("~~~ Deactivate ~~~")
        #self.next_recorder()
        if self.recorder.filename is not None:
            print("~~~ Stop recording ~~~")
            self.recorder.stop_saving()
            self.filename = None


def test():

    def run_trigger(trig, N, ts_func, tick=0.001): 
        # run trigger for N seconds, monitor on/off times
        st = time.monotonic()
        stats = {
            'start': st,
            'on_times': [],
            'off_times': [],
            'on_time': 0.,
            'off_time': 0.,
        }
        t = st
        s = None
        last_state_change_time = None
        while t - st <= 1.0:
            dt = t - st
            trig.set_trigger(ts_func(dt))
            if s is not None:
                if s and not trig.active:  # trigger deactivated
                    stats['on_time'] += (
                        t - last_state_change_time)
                    last_state_change_time = t
                    stats['off_times'].append(dt)
                elif not s and trig.active:  # trigger activated
                    stats['off_time'] += (
                        t - last_state_change_time)
                    last_state_change_time = t
                    stats['on_times'].append(dt)
            else:
                if trig.active:
                    stats['on_times'].append(dt)
                else:
                    stats['off_times'].append(dt)
                last_state_change_time = t
            s = trig.active
            time.sleep(tick)
            t = time.monotonic()

        # add last period
        if trig.active:
            stats['on_time'] += (t - last_state_change_time)
        else:
            stats['off_time'] += (t - last_state_change_time)

        stats['duty'] = stats['on_time'] / N
        return stats


    N = 1.0
    duty = 0.01
    post_time = 0.001
    min_time = 0.005
    max_time = 0.01

    acceptable_duty_error = 0.01

    # test all on
    trig = Trigger(duty, post_time, min_time, max_time)
    stats = run_trigger(trig, N, lambda dt: True)
    assert abs(stats['duty'] - duty) < acceptable_duty_error

    # test all off
    trig = Trigger(duty, post_time, min_time, max_time)
    stats = run_trigger(trig, N, lambda dt: False)
    assert stats['duty'] < acceptable_duty_error

    # test min time
    N = 0.015
    trig = Trigger(duty, post_time, min_time, max_time)
    stats = run_trigger(trig, N, lambda dt: dt < 0.002, tick=0.0001)
    assert abs(stats['on_time'] - min_time) < 0.005

    # test max time
    trig = Trigger(duty, post_time, min_time, max_time)
    stats = run_trigger(trig, N, lambda dt: True, tick=0.0001)
    assert abs(stats['on_time'] - max_time) < 0.005

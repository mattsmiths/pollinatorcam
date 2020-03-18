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

import logging
import time

import numpy

from . import gstrecorder


class MaskedDetection:
    def __init__(self, threshold, initial_mask=None):
        self.threshold = threshold
        # TODO set initial mask from taxonomy
        self.mask = initial_mask

    def set_labels(self, labels):
        if self.mask is None:
            #self.mask = numpy.ones_like(labels)
            # TODO hard coding insects here
            self.mask = numpy.zeros_like(labels)
            self.mask[0, 75:1067] = 1
            self.mask[0, 2291] = 1
        # TODO add filtering for false positives here
        # TODO add smoothing here
        md = numpy.logical_and(labels > self.threshold, self.mask)
        return numpy.any(md)

    def __call__(self, labels):
        return self.set_labels(labels)
    

class Trigger:
    def __init__(
            self, duty_cycle, post_time, min_time):
        self.duty_cycle = duty_cycle
        self.min_time = min_time
        self.post_time = post_time
        self.triggered = False

        self.times = {}

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

    def falling_edge(self):
        self.times['falling'] = time.monotonic()
        if 'hold_off' in self.times:
            del self.times['hold_off']
        if not self.active:
            self.activate(self.times['falling'])

    def high(self):
        t = time.monotonic()
        if 'rising' not in self.times:
            self.rising_edge()
        # check duty cycle
        if self.active:
            if t - self.times['start'] >= self.min_time:
                # stop recording, go into hold off
                self.deactivate(t)
                self.times['hold_off'] = t + 1. / self.duty_cycle * self.min_time
        else:
            if 'hold_off' in self.times and t >= self.times['hold_off']:
                self.activate(t)

    def low(self):
        if self.active:
            t = time.monotonic()
            if 'falling' not in self.times:
                self.falling_edge()
            # stop after post_record
            if t - self.times['falling'] >= self.post_time:
                self.deactivate(t)

    def set_trigger(self, trigger):
        if self.triggered:
            if trigger:
                self.high()
            else:
                self.falling_edge()
        else:
            if trigger:
                self.rising_edge()
            else:
                self.low()
        self.triggered = trigger

    def __call__(self, trigger):
        return self.set_trigger(trigger)


class TriggeredRecording(Trigger):
    def __init__(self, ip, duty_cycle, post_time, min_time, filename_gen):
        self.filename_gen = filename_gen
        super(TriggeredRecording, self).__init__(
            duty_cycle, post_time, min_time)

        self.ip = ip
        # TODO pre record time
        self.recorder_index = -1
        self.recorder = None
        self.next_recorder()

    def next_recorder(self):
        if self.recorder is not None:
            self.recorder.stop_recording()
        self.recorder_index += 1
        fn = self.filename_gen(self.recorder_index)
        logging.info("Buffering to %s", fn)
        self.recorder = gstrecorder.Recorder(ip=self.ip, filename=fn)
        self.recorder.start()

    def activate(self, t):
        super(TriggeredRecording, self).activate(t)
        if self.recorder.recording:
            self.next_recorder()
        # TODO log filename, time
        self.recorder.start_recording()

    def deactivate(self, t):
        super(TriggeredRecording, self).deactivate(t)
        self.next_recorder()


def test():

    def run_triggerer(trig, N, ts_func): 
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
            trig.set_trigger(ts_func(t))
            dt = t - st
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
            time.sleep(0.001)
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
    min_time = 0.01

    acceptable_error = 0.01

    # test all on
    trig = Triggerer(duty, post_time, min_time)
    stats = run_triggerer(trig, N, lambda t: True)
    assert abs(stats['duty'] - duty) / duty < acceptable_error

    # test all off
    trig = Triggerer(duty, post_time, min_time)
    stats = run_triggerer(trig, N, lambda t: False)
    assert stats['duty'] < acceptable_error

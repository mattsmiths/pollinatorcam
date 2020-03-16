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

import time


class MaskedDetection:
    def __init__(self, threshold, initial_mask=None):
        self.threshold = threshold
        # TODO set initial mask from taxonomy
        self.mask = initial_mask

    def set_labels(self, labels):
        if self.mask is None:
            self.mask = numpy.ones_like(labels)
        # TODO add filtering for false positives here
        # TODO add smoothing here
        return numpy.any(numpy.logical_and(labels > threshold, self.mask))
    

class Trigger:
    def __init__(
            self, duty_cycle, post_time, min_time):
        self.duty_cycle = duty_cycle
        self.min_time = min_time
        self.post_time = post_time
        self.triggered = False

        self.times = {}

        # TODO pull out recorder for easier testing
        #self.recorder_index = -1
        #self.recorder = None 
        #self.next_recorder()
        self.active = None

    def activate(self, t):
        self.times['start'] = t
        self.active = True
    
    def deactivate(self, t):
        self.active = False

    #def next_recorder(self):
    #    if self.recorder is not None:
    #        self.recorder.stop_recording()
    #    self.recorder_index += 1
    #    self.recorder = gstrecorder.Recorder(
    #        ip=self.ip, filename='test%05i.mp4' % self.recorder_index)
    #    self.recorder.start()

    def rising_edge(self):
        self.times['rising'] = time.monotonic()
        if not self.active:
            self.activate(self.times['rising'])
        # start recording
        #if not self.recorder.recording:
        #    self.recorder.start_recording()
        #    self.times['start'] = self.times['rising']

    def falling_edge(self):
        self.times['falling'] = time.monotonic()
        if 'hold_off' in self.times:
            del self.times['hold_off']
        if not self.active:
            self.activate(self.times['falling'])
        # if not recording, start
        #if not self.recorder.recording:
        #    self.recorder.start_recording()
        #    self.times['start'] = self.times['falling']
        #self._falling_edge_time = time.monotonic()

    def high(self):
        t = time.monotonic()
        if 'rising' not in self.times:
            self.rising_edge()
        # check duty cycle
        #if self.recorder.recording:
        if self.active:
            if t - self.times['start'] >= self.min_time:
                self.deactivate(t)
                # stop recording, go into hold off
                #self.next_recorder()
                self.times['hold_off'] = t + 1. / self.duty_cycle * self.min_time
                #self.times['hold_off'] = t + (1. - self.duty_cycle) * self.min_time
        else:
            if 'hold_off' in self.times and t >= self.times['hold_off']:
                self.activate(t)
                #self.recorder.start_recording()
                #self.times['start'] = t

    def low(self):
        #if self.recorder.recording:
        if self.active:
            t = time.monotonic()
            if 'falling' not in self.times:
                self.falling_edge()
            # stop after post_record
            if t - self.times['falling'] >= self.post_time:
                self.deactivate(t)
                #self.next_recorder()

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

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
    
    def set_mask(self, mask):
        self.mask = mask


class TriggeredRecorder:
    def __init__(
            self, duty_cycle, pre_record, post_record,
            min_record):
        self.duty_cycle = duty_cycle
        self.min_record = min_record
        self.pre_record = pre_record
        self.post_record = post_record
        self.triggered = False

        self.edge_times = {}

        self.recorder_index = -1
        self.recorder = None 
        self.next_recorder()

    def next_recorder(self):
        if self.recorder is not None:
            self.recorder.stop_recording()
        self.recorder_index += 1
        self.recorder = gstrecorder.Recorder(
            ip=self.ip, filename='test%05i.mp4' % self.recorder_index)
        self.recorder.start()

    def rising_edge(self):
        self.edge_times['rising'] = time.monotonic()
        # start recording
        if not self.recorder.recording:
            self.recorder.start_recording()

    def falling_edge(self):
        self.edge_times['falling'] = time.monotonic()
        # if not recording, start
        if not self.recorder.recording:
            self.recorder.start_recording()
        self._falling_edge_time = time.monotonic()

    def high(self):
        t = time.monotonic()
        if 'rising' not in self.edge_times:
            self.edge_times['rising'] = t
        # check duty cycle
        if self.recorder.recording:  # not in hold-off
            if t - self.start_time >= self.min_record:
                self.stop_recording()
                self.next_recorder()
                # TODO compute next turn-on time
        else:  # in hold-off
            # TODO turn back on based on duty cycle
            pass

    def low(self):
        if self.recorder.recording:
            t = time.monotonic()
            if 'falling' not in self.edge_times:
                self.edge_times['falling'] = t
            # stop after post_record
            if t - self.edge_times['falling'] >= self.post_record:
                self.stop_recording()
                self.next_recorder()

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

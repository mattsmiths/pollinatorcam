import datetime
import os
import struct

import numpy


class AnalysisResultsSaver:
    def __init__(self, data_dir):
        self.file = None
        self.data_dir = data_dir

    def __del__(self):
        if self.file is not None:
            self.file.close()

    def check_file(self, timestamp, record):
        if self.file is not None:
            if self.file.datetime.hour == timestamp.hour:
                return
            else:
                self.file.close()
                self.file = None
        d = os.path.join(self.data_dir, timestamp.strftime('%y%m%d'))
        if not os.path.exists(d):
            os.makedirs(d)
        # TODO better filename
        fn = os.path.join(d, '%02i.raw' % timestamp.hour)
        self.file = open(fn, 'ab')
        self.file.datetime = timestamp

    def save(self, timestamp, record):
        assert isinstance(timestamp, datetime.datetime)

        self.check_file(timestamp, record)

        # pack data, write to file
        # byte 0 = detection bool
        # byte 1:8 = timestamp
        # byte 9:? = 8 bytes per item
        bs = (
            struct.pack('b', record['detection']) +
            struct.pack('d', timestamp.timestamp()) +
            record['labels'].tobytes())
        self.file.write(bs)

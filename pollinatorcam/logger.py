import datetime
import os

import h5py
import numpy


class AnalysisResultsSaver:
    def __init__(self, data_dir):
        self.file = None
        self.index = None
        self.n_records = 3600
        self.extend_size = 10
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
        # TODO handle 'truncated file' by writing to a new file
        fn = os.path.join(d, '%s.hdf5' % timestamp.hour)
        self.file = h5py.File(fn, 'a')
        self.file.datetime = timestamp
        # check if datasets already exist
        if 'labels' in self.file:
            for k in ('detections', 'times'):
                assert k in self.file
            self.index = None
        else:
            self.file.create_dataset(
                'labels', (self.n_records, len(record['labels'])), 'f8')
            self.file.create_dataset(
                'detections', (self.n_records,), 'bool')
            self.file.create_dataset(
                'times', (self.n_records,), 'f8')
            self.index = None
        return

    def check_index(self):
        if self.index is None:
            # find first non-zero timestamp
            nzs = numpy.nonzero(self.file['times'])[0]
            if len(nzs) == 0:
                self.index = 0
            else:
                self.index = nzs[-1] + 1
        if self.index >= self.n_records:
            # extend datasets by some extend_size
            print("Extending %s" % type(self))
            n = None
            for k in ('labels', 'detections', 'times'):
                if n is None:
                    n = self.file[k].shape[0] + self.extend_size
                self.file['labels'].resize(n, axis=0)

    def save(self, timestamp, record):
        assert isinstance(timestamp, datetime.datetime)

        self.check_file(timestamp, record)
        self.check_index()

        self.file['labels'][self.index] = record['labels']
        self.file['detections'][self.index] = record['detection']
        self.file['times'][self.index] = timestamp.timestamp()
        self.index += 1

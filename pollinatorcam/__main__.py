import sys

from . import discover
from . import grabber


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'discover':
        sys.argv.pop(1)
        discover.cmdline_run()
    else:
        grabber.cmdline_run()

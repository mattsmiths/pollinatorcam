#!/bin/bash

source /home/pi/.bashrc
source /home/pi/.virtualenvs/pollinatorcam/bin/activate

cd /home/pi/r/cbs-ntcore/pollinatorcam

# exec here to use same PID to allow systemd watchdog
exec python3 -m pollinatorcam -i $1 -rdD

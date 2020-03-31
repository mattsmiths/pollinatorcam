#!/bin/bash

source /home/pi/.bashrc
source /home/pi/.virtualenvs/pollinatorcam/bin/activate

cd /home/pi/r/cbs-ntcore/pollinatorcam

MY_IP=`ip -o -4 addr list eth0 | awk '{print $4}' | cut -d/ -f1`

python3 -m pollinatorcam discover -i $MY_IP/24

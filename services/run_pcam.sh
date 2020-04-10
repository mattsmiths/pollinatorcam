#!/bin/bash

source /home/pi/.bashrc
source /home/pi/.virtualenvs/pollinatorcam/bin/activate

cd /home/pi/r/cbs-ntcore/pollinatorcam

python3 -m pollinatorcam -i $1 -rd

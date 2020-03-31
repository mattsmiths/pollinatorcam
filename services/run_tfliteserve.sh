#!/bin/bash

source /home/pi/.virtualenvs/pollinatorcam/bin/activate

cd /home/pi/r/braingram/tfliteserve

python3 -m tfliteserve -m 200123_2035/model.tflite -l 200123_2035/labels.txt -j -1

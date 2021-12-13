#!/bin/bash

source $HOME/.virtualenvs/pollinatorcam/bin/activate

cd $HOME/r/braingram/tfliteserve

# TODO add environment variable to disable EDGE
python3 -m tfliteserve -m 200123_2035/model.tflite -l 200123_2035/labels.txt -j -1
#python -m tfliteserve -m 200123_2035/model_edgetpu.tflite -l 200123_2035/labels.txt -e -j -1

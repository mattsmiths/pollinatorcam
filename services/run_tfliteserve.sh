#!/bin/bash

source $HOME/.virtualenvs/pollinatorcam/bin/activate

cd $HOME/r/braingram/tfliteserve

# TODO add environment variable to disable EDGE
#python3 -m tfliteserve -m 200123_2035/model.tflite -l 200123_2035/labels.txt -j -1
#python -m tfliteserve -m 200123_2035/model_edgetpu.tflite -l 200123_2035/labels.txt -e -j -1
#python -m tfliteserve -m 220214/efficientdet-lite_320x320_iNat_II_edgetpu.tflite -l 220214/efficientdet-lite_320x320_iNat_II-labels.txt -e -j -1 -T detector
#python -m tfliteserve -m 220214_one_class/efficientdet-lite_320x320_iNat_insectDetect_edgetpu.tflite -l 220214_one_class/efficientdet-lite_320x320_iNat_insectDetect-labels.txt -e -j -1 -T detector
#python -m tfliteserve -m 220214_one_class/efficientdet-lite_320x320_iNat_insectDetect.tflite -l 220214_one_class/efficientdet-lite_320x320_iNat_insectDetect-labels.txt -j -1 -T detector
#python -m tfliteserve -m tflite_20220630_1/model.tflite -l tflite_20220630_1/labels.txt -j -1 -T detector

# Read in model options
CLASSES=`cat /home/pi/Desktop/configs | jq '.classes'`
CORAL=`cat /home/pi/Desktop/configs | jq '.coral'`

if [[ $CLASSES == *"multi"* ]]; then
	if [ $CORAL == 0 ]; then
		python -m tfliteserve -m tflite_2023/ssd_multi.tflite -l tflite_2023/multi.txt -j -1 -T detector
	else
		python -m tfliteserve -m tflite_2023/ssd_multi_edge.tflite -l tflite_2023/multi.txt -e -j -1 -T detector
	fi
else
	if [ $CORAL == 0 ]; then
		python -m tfliteserve -m tflite_2023/ssd_single.tflite -l tflite_2023/single.txt -j -1 -T detector
	else
		python -m tfliteserve -m tflite_2023/ssd_single_edge.tflite -l tflite_2023/single.txt -e -j -1 -T detector
	fi
fi

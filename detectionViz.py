#%%
import json
from ssl import HAS_TLSv1_1
import cv2 as cv
import os
from matplotlib import pyplot as plt
import numpy as np
import matplotlib
# %%
f1 = '/media/actormsofficial/Samsung USB/detections/1_3_3_4/220308/172134_601889_1_3_3_4.json'
in1 = open(f1,'r')
det = json.load(in1)
# %%

fileName = '/media/actormsofficial/Samsung USB/'+det['meta']['still_filename'].split('mnt/data/')[1]
image1 = cv.cvtColor(cv.imread(fileName),cv.COLOR_BGR2RGB)
sz1 = np.shape(image1)

fig,ax = plt.subplots()


for bb in det['meta']['bboxes'][0][0]:
    if bb[1] > 0.35:
        xy1 = (bb[2][0]*sz1[0],bb[2][1]*sz1[1])
        xy2 = (bb[2][2]*sz1[0],bb[2][3]*sz1[1])
        h1 = xy2[0]-xy1[0]
        w1 = xy2[1]-xy1[1]
        
        xy1 = [xy1[1],xy1[0]]
        rect = matplotlib.patches.Rectangle(xy1,w1,h1,linewidth=4,edgecolor='r',facecolor='none')
        ax.add_patch(rect)

ax.imshow(image1)

# %%

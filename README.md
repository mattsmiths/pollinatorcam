# Data

- snapshots:
  - full res (~0.7 MB per image)
  - 1 every 10 seconods
  - save detection results
- short videos
  - 5 fps
  - 1 sec pre-record
  - trigger processed 1/sec
  - max length?
  - max duty cycle?


## Format

- partitions:
  - per group (separate storage: mac address?)
  - camera (ip or mac address?)
  - time (requires RTC per group, to second)
- types
  - detection results: npy
  - snapshots: jpg
  - videos: mp4


# System architecture

- hardware:
  - Coral Dev board (in enclosure)
    - connected storage
    - power supply
    - interface?
  - Security cameras
    - POE
    - ethernet feedthroughs

- nodes: 
  - tensorflow processor
  - (N) grabbers
  - FTP server


# Camera setup

- Adjust lens to correct focus
- One time setup
  - user/password
  - extraformat dimensions
  - extraformat fps
  - extraformat h265
  - extraformat bitrate
  - mainformat dimensions
  - mainformat fps
  - mainformat h265
  - mainformat bitrate
  - disable motion detection
  - remove videowidget overlays
  - enable ftp settings
  - snapshot schedule
  - snapshot saving
  - snapshot period


# Failure modes

- Camera dies
- Main node dies (loss of data)
- Stream disconnects
  - gstreamer drops
  - substream drops
- out of storage
- TPU hangs
- TPU disconnects
- Grabber disconnects from main node (sharedmem failure)
- Saving video fails

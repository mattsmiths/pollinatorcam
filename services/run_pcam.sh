#!/bin/bash

source $HOME/.bashrc
source $HOME/.virtualenvs/pollinatorcam/bin/activate

cd $HOME/r/cbs-ntcore/pollinatorcam

# exec here to use same PID to allow systemd watchdog
exec python3 -m pollinatorcam -l $1 -rdD

import re
import subprocess


def get_device_info():
    o = subprocess.check_output(['v4l2-ctl', '--list-devices']).decode('ascii')
    # output contains names (and busses)
    # followed by devices (on new lines) then a blank line
    device_name = None
    device_info = {}
    for l in o.splitlines():
        # skip blank lines
        if len(l.strip()) == 0:
            continue
        
        # if this is a device name, add to devices
        if '/dev/' in l:
            if device_name not in device_info:
                raise Exception(
                    "Failed to parse v4l2-ctl output, missing device name")
            device_info[device_name]['devices'].append(l.strip())
        else:  # not a device line and not blank so this is the next name and bus
            print(l)
            # parse info line into name and bus
            device_name = re.search('(.*)\(', l).groups()[0].strip()
            # get bus in form: usb-0000:01:00.0-1.4.3.4'
            bus = re.search('\((.*)\)', l).groups()[0]
            device_info[device_name] = {
                'info': l,
                'devices': [],
                'bus': bus,
            }
    return device_info

"""
Periodically (every 10 minutes?) scan network
    Find ips on network (some may be previously saved)
    Attempt connection to ip as camera
        If camera, start systemd service, record ip as camera
        If not camera, record ip as not camera

Keep track of:
    Connection

Cache format: key = ip, value = name (if camera), False if not camera

if ip is in base_filename, don't pay attention to scan results
    if true or name: start/make sure service is running
    if false: ignore
if ip is not in base, check config
    if true or name: start/make sure service is running
    if false: ignore
when a new ip is found, check if it's a camera and add it to the config
"""

import argparse
import json
import logging
import os
import re
import subprocess
import time

from . import config
from . import dahuacam


default_cidr = '10.1.1.0/24'
ip_regex = re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})')
cfg_name = 'ips.json'
# dictionary where keys=ips, value=dict
#   is_camera=True/False
#   is_configured=True/False
#   name=camera name (if a camera)
#   service={Active: True/False, UpTime: N}
#   skip=True/False (if not present, assume false)


def scan_network_for_ips(cidr=None):
    if cidr is None:
        cidr = default_cidr
    cmd = "nmap -nsP {cidr}".format(cidr=cidr).split()
    logging.debug("Running scan command: %s", cmd)
    o = subprocess.run(cmd, stdout=subprocess.PIPE, check=True)
    logging.debug("Parsing scan command output")
    for l in o.stdout.decode('ascii').splitlines():
        logging.debug("Parsing line: %s", l.strip())
        ms = ip_regex.findall(l)
        if len(ms):
            logging.debug("Scan found ip: %s", ms[0])
            yield ms[0]


def check_if_camera(ip):
    """Check if the provided ip is a configured camera
    Returns:
        is_camera
        is_configured
        camera name
    """
    logging.debug("Checking if ip[%s] is a camera", ip)
    dc = dahuacam.DahuaCamera(ip)
    try:
        n = dc.get_name()
        logging.debug("Camera returned name: %s", n)
        mn = dahuacam.mac_address_to_name(dc)
        if len(n) != 12:
            logging.error("Camera name isn't 12 chars")
            return True, False, n
        logging.debug("Camera name from mac: %s", mn)
        if mn != n:
            logging.error(
                "Camera %s isn't configured: %s != %s" % (ip, n, mn))
            return True, False, n
        return True, True, n
    except Exception as e:
        logging.debug("IP returned error: %s", e)
        return False, False, ''


def verify_camera_service(ip):
    # compute systemd service name
    name = 'pcam@%s' % ip
    logging.debug("Checking status of %s service", name)

    # check if service is running
    cmd = 'sudo systemctl is-active %s --quiet' % name
    logging.debug("Running %s", cmd)
    o = subprocess.run(cmd.split())
    logging.debug("Return code %i", o.returncode)
    if o.returncode != 0:
        logging.info("Service %s not running, starting...", name)
        # not running, try starting
        cmd = 'sudo systemctl start %s' % name
        try:
            o = subprocess.run(cmd.split(), check=True)
            return True
        except Exception as e:
            logging.error("Failed to start service %s: %s", name, e)
            return False
    else:
        return True


def verify_nas_config(ip):
    logging.debug("Checking NAS config for %s", ip)
    dc = dahuacam.DahuaCamera(ip)
    nas_ip = dc.get_config('NAS[0].Address').strip().split('=')[1]
    logging.debug("NAS host ip = %s", nas_ip)
    hip = dahuacam.get_host_ip(ip)
    if nas_ip != hip:
        logging.info("Setting NAS host ip to %s for %s", hip, ip)
        dahuacam.set_snap_config(
            dc, {'user': 'ipcam', 'enable': True, 'ip': hip})


def status_of_all_camera_services():
    cmd = (
        "sudo systemctl show "
        "--property=Id,ActiveState,ActiveEnterTimestampMonotonic pcam@*")
    o = subprocess.run(cmd.split(), stdout=subprocess.PIPE, check=True)
    cams = {}
    cam_ip = None
    t = time.monotonic()
    for l in o.stdout.decode('ascii').splitlines():
        if len(l.strip()) == 0:
            continue
        k, v = l.strip().split("=")
        if k == 'Id':
            cam_ip = '.'.join(v.split('@')[1].split('.')[:-1])
            cams[cam_ip] = {}
        elif k == 'ActiveState':
            cams[cam_ip]['Active'] = v == 'active'
        else:
            cams[cam_ip]['Uptime'] = t - int(v) / 1000000.
    return cams


def check_cameras(cidr=None):
    # dictionary where keys=ips, value=dict
    #   is_camera=True/False
    #   is_configured=True/False
    #   name=camera name (if a camera)
    #   service={Active: True/False, UpTime: N}
    #   skip=True/False (if not present, assume false)
    cfg = config.load_config(cfg_name, {})
    network_ips = scan_network_for_ips(cidr)
    services = status_of_all_camera_services()

    new_cfg = {}
    # TODO error catching, save on error?
    for ip in network_ips:
        # is blacklisted?
        if ip in cfg and cfg[ip].get('skip', False):
            new_cfg[ip] = cfg[ip]
            continue

        is_camera, is_configured, name = check_if_camera(ip)
        cam = {
            'is_camera': is_camera,
            'is_configured': is_configured,
            'name': name,
        }

        # service running?
        cam['service'] = services.get(ip, {'Active': False, 'Uptime': 0})

        # verify nas config
        if is_camera and is_configured:
            verify_nas_config(ip)
        new_cfg[ip] = cam

    config.save_config(new_cfg, cfg_name)


def cmdline_run():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-i', '--ips', type=str, default="",
        help="ips to scan (as cidr)")
    parser.add_argument(
        '-v', '--verbose', action='store_true',
        help="enable verbose logging")
    args = parser.parse_args()
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    # TODO verify cidr

    if len(args.ips):
        cidr = args.ips
    else:
        cidr = None

    #time running of check_cameras
    t0 = time.monotonic()
    check_cameras(cidr)
    t1 = time.monotonic()
    logging.debug("check_cameras took %0.4f seconds", t1 - t0)


if __name__ == '__main__':
    cmdline_run()

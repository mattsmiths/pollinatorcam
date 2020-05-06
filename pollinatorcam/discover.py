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

from . import dahuacam


default_cidr = '10.1.1.0/24'
ip_regex = re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})')

base_filename = '~/.pcam/ips.json'
tmp_filename = '/dev/shm/pcam/ips.json'  # should be on a tmpfs


class ConfigLoadError(Exception):
    pass


def load_config(fn):
    fn = os.path.expanduser(fn)
    logging.debug("Loading config from: %s", fn)
    if not os.path.exists(fn):
        logging.info("No config found, returning empty dict")
        return {}
    with open(fn, 'r') as f:
        return json.load(f)


def load_cascaded_config():
    # load temporary config (from tmpfs)
    try:
        config = load_config(tmp_filename)
    except Exception as e:
        logging.error(
            "Falling back to blank config after failing to load %s: %s",
            tmp_filename, e)
        config = {}

    # overwrite with hard-coded config
    try:
        config.update(load_config(base_filename))
    except Exception as e:
        logging.error(
            "Failed to update config with base %s: %s",
            base_filename, e)
        raise ConfigLoadError("hard-coded config load failed")
    return config


def save_config(config, fn):
    fn = os.path.expanduser(fn)
    dn = os.path.dirname(fn)
    logging.debug("Saving config to: %s", fn)
    if not os.path.exists(dn):
        logging.debug("Making directory for config: %s", fn)
        os.makedirs(dn)
    with open(fn, 'w') as f:
        json.dump(config, f)


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
    camera name if configured camera
    None if camera but not configured
    False if not camra
    """
    logging.debug("Checking if ip[%s] is a camera", ip)
    dc = dahuacam.DahuaCamera(ip)
    try:
        n = dc.get_name()
        logging.debug("Camera returned name: %s", n)
        mn = dahuacam.mac_address_to_name(dc)
        if len(n) != 12:
            logging.error("Camera name isn't 12 chars")
            return None
        logging.debug("Camera name from mac: %s", mn)
        if mn != n:
            logging.error(
                "Camera %s isn't configured: %s != %s" % (ip, n, mn))
            return None
        return n
    except Exception as e:
        logging.debug("IP returned error: %s", e)
        return False


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
    dc = DahuaCamera(ip)
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
    prevent_save = False
    try:
        config = load_cascaded_config()
    except ConfigLoadError as e:
        # if failed to load config don't overwrite
        logging.warning("Failed to load config: %s", e)
        prevent_save = True
        # use blank (scan all ips)
        config = {}

    # only save if config has changed
    should_save = False

    # find all ips on network
    for ip in scan_network_for_ips(cidr):
        if ip in config:
            # ip is either a camera [already added above]
            # or is set as not-a-camera [skip]
            logging.debug(
                "Skipping check_if_camera on ip %s: %s", ip, config[ip])
            continue
       
        # new ip, mark to save config
        should_save = True

        # check if ip is a camera
        try:
            r = check_if_camera(ip)
            if r is not None:
                config[ip] = r
        except Exception as e:
            logging.warning(
                "Failed check_if_camera(%s): %s",
                ip, e)

    # check all cameras have running services
    for ip in list(config.keys()):
        if config[ip] is not False:  # this is a camera
            r = True
            try:
                r = verify_camera_service(ip)
                # TODO cache this for ui so it doesn't have to poll services
            except Exception as e:
                logging.warning(
                    "Failed verify_camera_service(%s): %s",
                    ip, e)
                r = False
            # verify NAS ip points to this computer
            try:
                verify_nas_config(ip)
            except Exception as e:
                logging.warning(
                    "Failed verify_nas_config(%s): %s",
                    ip, e)
            if not r:
                # failed to start service, delete from config
                # to allow additional attempts at starting
                del config[ip]

    if should_save and not prevent_save:
        save_config(config, tmp_filename)


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

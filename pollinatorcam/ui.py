"""
Flask won't serve up /mnt/data nicely (no dir listing)
So use apache or nginx etc to serve

Functions
- camera status
  - name & ip
  - systemd service status and up time
  - recording state (can I get this?)
  - link to open in vlc [make from ip & PCAM_* env vars]
  - link to most recent snapshot
  - link to data for yesterday & today [requires static file serving]
- system status
  - disk space
  - temperature
  - weather...
"""

import datetime
import shutil
import os

import flask

from . import discover
from . import grabber


app = flask.Flask('pcam')
#app.config["DEBUG"] = True


@app.route("/", methods=["GET"])
def index():
    return ""


@app.route("/temperature", methods=["GET"])
def temperature():
    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
        return flask.jsonify(int(f.read().strip()) / 1000.)


@app.route("/disk_usage", methods=["GET"])
def disk_info():
    du = shutil.disk_usage(grabber.data_dir)
    return flask.jsonify({
        'total': du.total,
        'used': du.used,
        'free': du.free,
    })


@app.route("/cameras", methods=["GET"])
def camera_status():
    # load config key=ip, value=name [or False if not a camera]
    cfg = discover.load_cascaded_config()
    ips_to_names = {k: cfg[k] for k in cfg if isinstance(cfg[k], str)}

    # get systemd status and uptime of all ips
    service_states = discover.status_of_all_camera_services()

    cams = {}
    for ip in ips_to_names:
        s = service_states.get(ip, {})
        cams[ip] = {
            'name': ips_to_names[ip],
            'active': s.get('Active', False),
            'uptime': s.get('Uptime', -1),
        }
    return flask.jsonify(cams)


@app.route("/snapshot/<name>", methods=["GET"])
def snapshot(name):
    path = os.path.join(grabber.data_dir, name)
    # get most recent day
    path = os.path.join(
        path,
        max([sd for sd in os.listdir(path) if '-' in sd]),
        'pic_001')
    # get most recent image
    path = os.path.join(path, max(os.listdir(path)))
    return flask.send_file(path, mimetype='image/jpg')


def run_ui(**kwargs):
    kwargs['host'] = kwargs.get('host', '0.0.0.0')
    kwargs['port'] = kwargs.get('port', 5000)
    print("Running on %s:%i" % (kwargs['host'], kwargs['port']))
    app.run(**kwargs)


def cmdline_run():
    run_ui()

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
import glob
import shutil
import os

import flask

from . import discover
from . import grabber


app = flask.Flask('pcam')


@app.route("/", methods=["GET"])
def index():
    # TODO fix this, make it a relative path
    path = '/home/pi/r/cbs-ntcore/pollinatorcam/index.html'
    return flask.send_file(path, mimetype='text/html')


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
@app.route("/cameras/", methods=["GET"])
@app.route("/cameras/<date>", methods=["GET"])
def camera_list(date=None):
    if date is None:
        date = datetime.datetime.now()
    else:
        try:
            date = datetime.datetime.fromisoformat(date)
        except ValueError:
            return flask.abort(400)

    ts = date.strftime('%y%m%d')
    # load config key=ip, value=name [or False if not a camera]
    cfg = discover.load_cascaded_config()
    ips_to_names = {k: cfg[k] for k in cfg if isinstance(cfg[k], str)}

    # get systemd status and uptime of all ips
    service_states = discover.status_of_all_camera_services()

    detections_path = os.path.join(grabber.data_dir, 'detections')

    cams = []
    for ip in ips_to_names:
        s = service_states.get(ip, {})
        name = ips_to_names[ip]
        detections = sorted(glob.glob(os.path.join(
            detections_path,
            name,
            ts,
            '*',
        )))
        cams.append({
            'ip': ip,
            'name': name,
            'active': s.get('Active', False),
            'uptime': s.get('Uptime', -1),
            'detections': detections,
        })
    cams.sort(key=lambda c: c['name'])
    return flask.jsonify(cams)


@app.route("/snapshot/<name>", methods=["GET"])
@app.route("/snapshot/<name>/", methods=["GET"])
@app.route("/snapshot/<name>/<date>", methods=["GET"])
def snapshot(name, date=None):
    most_recent = True
    if date is None:
        date = datetime.datetime.now()
    else:
        try:
            date = datetime.datetime.fromisoformat(date)
        except ValueError:
            return flask.abort(400)
        most_recent = False

    # get most recent day
    path = os.path.join(
        grabber.data_dir,
        name,
        date.strftime('%Y-%m-%d'),
        'pic_001')
    if most_recent:
        fn_glob = os.path.join(path, '*.jpg')
    else:
        fn_glob = os.path.join(path, date.strftime('%H.%M') + '*.jpg')
    fns = sorted(glob.glob(fn_glob))
    if len(fns) == 0:
        return flask.abort(404)
    return flask.send_file(fns[-1], mimetype='image/jpg')


def run_ui(**kwargs):
    kwargs['host'] = kwargs.get('host', '0.0.0.0')
    kwargs['port'] = kwargs.get('port', 5000)
    print("Running on %s:%i" % (kwargs['host'], kwargs['port']))
    #app.config["DEBUG"] = True
    #app.debug = True
    app.run(**kwargs)


def cmdline_run():
    run_ui()

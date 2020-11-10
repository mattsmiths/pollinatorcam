import datetime
import glob
import logging
import os
import re
import sqlite3


data_dir = '/media/graham/377CDC5E2ECAB822'
dbfn = 'pcam.sqlite'
force = True
debug = True


if debug:
    logging.basicConfig(level=logging.DEBUG)


def get_modules():
    mps = sorted(glob.glob(os.path.join(data_dir, 'Module*')))
    modules = {}
    for mp in mps:
        index = int(os.path.split(mp)[-1].split('Module')[-1])
        if index in modules:
            raise Exception(f"Found 2 modules with same index {index}")
        modules[index] = mp
    return modules


def table_exists(db, table_name):
    res = db.execute("SELECT name from sqlite_master WHERE type='table';")
    for r in res:
        if r[0] == table_name:
            return True
    return False


def index_cameras(db, force=False):
    # check if camera table already exists
    if table_exists(db, 'cameras'):
        if not force:
            logging.warning("table cameras already exists, skipping")
            return False
        db.execute("DROP TABLE cameras;");
    logging.info("creating cameras table")
    db.execute(
        "CREATE TABLE cameras ("
        "camera_id INTEGER PRIMARY KEY,"
        "mac TEXT,"
        "module INTEGER"
        ");")
    nc = 0
    modules = get_modules()
    for module_index in modules:
        module_path = modules[module_index]
        logging.debug(f"Searching module {module_index} at {module_path}")
        camera_paths = sorted(glob.glob(os.path.join(module_path, '001f*')))
        for camera_path in camera_paths:
            macaddr = os.path.split(camera_path)[-1]
            logging.debug(f"Found camera {macaddr} in module {module_index}")
            db.execute(
                "INSERT INTO cameras (mac, module) VALUES (?, ?);",
                (macaddr, module_index))
            nc += 1
    logging.info(f"Found {nc} cameras")


def get_cameras(db, by_module_mac=True):
    if not table_exists(db, 'cameras'):
        raise Exception("Database missing cameras tabls")
    modules = get_modules()
    cameras = []
    for camera_row in db.execute('SELECT * FROM cameras'):
        camera_id, macaddr, module_index = camera_row
        cameras.append({
            'id': camera_id,
            'macaddr': macaddr,
            'module_path': modules[module_index],
            'module_index': module_index,
        })
    if by_module_mac:
        d = {}
        for c in cameras:
            mi = c['module_index']
            if mi not in d:
                d[mi] = {}
            if c['macaddr'] in d[mi]:
                raise Exception(
                    "cameras are not unique {} appears >1 in module {}".format(
                        c['macaddr'], mi))
            d[mi][c['macaddr']] = c
        cameras = d
    return cameras


def index_configs(db, force=False):
    if table_exists(db, 'configs'):
        if not force:
            logging.warning("table configs already exists, skipping")
            return False
        db.execute("DROP TABLE configs;");
    logging.info("creating configs table")
    db.execute(
        "CREATE TABLE configs ("
        "config_id INTEGER PRIMARY KEY,"
        "camera_id INTEGER,"
        "timestamp TIMESTAMP,"
        "path TEXT"
        ");")

    modules = get_modules()
    cameras = get_cameras(db, by_module_mac=True)
    for module_index in modules:
        module_path = modules[module_index]
        logging.debug(f"indexing module {module_index} at {module_path}")
        camera_config_paths = sorted(glob.glob(os.path.join(module_path, 'configs/001f*')))
        for camera_config_path in camera_config_paths:
            logging.debug(f"indexing camera_path {camera_config_path}")
            macaddr = os.path.split(camera_config_path)[-1]
            config_paths = sorted(glob.glob(os.path.join(camera_config_path, '*_*_*')))
            camera_id = cameras[module_index][macaddr]['id']
            for config_path in config_paths:
                rpath = os.path.relpath(config_path, data_dir)
                ts = os.path.split(rpath)[-1]
                dt = datetime.datetime.strptime(ts, '%y%m%d_%H%M%S_%f')
                logging.debug(f"Found config for camera {camera_id} at {dt} in file {rpath}")
                db.execute(
                    "INSERT INTO configs (camera_id, timestamp, path) VALUES (?, ?, ?)",
                    (camera_id, dt, rpath))


def index_detections(db, force=False):
    if table_exists(db, 'detections'):
        if not force:
            logging.warning("table detections already exists, skipping")
            return False
        db.execute("DROP TABLE detections;");
    logging.info("creating detections table")
    db.execute(
        "CREATE TABLE detections ("
        "detection_id INTEGER PRIMARY KEY,"
        "camera_id INTEGER,"
        "timestamp TIMESTAMP,"
        "path TEXT"
        ");")

    modules = get_modules()
    cameras = get_cameras(db, by_module_mac=True)
    for module_index in modules:
        module_path = modules[module_index]
        logging.debug(f"indexing module {module_index} at {module_path}")
        camera_paths = sorted(glob.glob(os.path.join(module_path, 'detections/001f*')))
        for camera_path in camera_paths:
            logging.debug(f"indexing camera_path {camera_path}")
            macaddr = os.path.split(camera_path)[-1]
            detection_paths = sorted(glob.glob(os.path.join(camera_path, '*', '*.json')))
            camera_id = cameras[module_index][macaddr]['id']
            for detection_path in detection_paths:
                rpath = os.path.relpath(detection_path, data_dir)
                ts = '_'.join('_'.join(rpath.split(os.path.sep)[-2:]).split('_')[:-1])
                dt = datetime.datetime.strptime(ts, '%y%m%d_%H%M%S_%f')
                logging.debug(f"Found detection for camera {camera_id} at {dt} in file {rpath}")
                db.execute(
                    "INSERT INTO detections (camera_id, timestamp, path) VALUES (?, ?, ?)",
                    (camera_id, dt, rpath))


def index_videos(db, force=False):
    if table_exists(db, 'videos'):
        if not force:
            logging.warning("table videos already exists, skipping")
            return False
        db.execute("DROP TABLE videos;");
    logging.info("creating videos table")
    db.execute(
        "CREATE TABLE videos ("
        "video_id INTEGER PRIMARY KEY,"
        "camera_id INTEGER,"
        "timestamp TIMESTAMP,"
        "path TEXT"
        ");")

    modules = get_modules()
    cameras = get_cameras(db, by_module_mac=True)
    for module_index in modules:
        module_path = modules[module_index]
        logging.debug(f"indexing module {module_index} at {module_path}")
        camera_paths = sorted(glob.glob(os.path.join(module_path, 'videos/001f*')))
        for camera_path in camera_paths:
            logging.debug(f"indexing camera_path {camera_path}")
            macaddr = os.path.split(camera_path)[-1]
            video_paths = sorted(glob.glob(os.path.join(camera_path, '*', '*.mp4')))
            camera_id = cameras[module_index][macaddr]['id']
            for video_path in video_paths:
                rpath = os.path.relpath(video_path, data_dir)
                ts = '_'.join('_'.join(rpath.split(os.path.sep)[-2:]).split('_')[:-1])
                dt = datetime.datetime.strptime(ts, '%y%m%d_%H%M%S_%f')
                logging.debug(f"Found video for camera {camera_id} at {dt} in file {rpath}")
                db.execute(
                    "INSERT INTO videos (camera_id, timestamp, path) VALUES (?, ?, ?)",
                    (camera_id, dt, rpath))


def index_stills(db, force=False):
    if table_exists(db, 'stills'):
        if not force:
            logging.warning("table stills already exists, skipping")
            return False
        db.execute("DROP TABLE stills;");
    logging.info("creating stills table")
    db.execute(
        "CREATE TABLE stills ("
        "still_id INTEGER PRIMARY KEY,"
        "camera_id INTEGER,"
        "timestamp TIMESTAMP,"
        "path TEXT"
        ");")

    modules = get_modules()
    cameras = get_cameras(db, by_module_mac=True)
    for module_index in modules:
        module_path = modules[module_index]
        logging.debug(f"indexing module {module_index} at {module_path}")
        camera_paths = sorted(glob.glob(os.path.join(module_path, '001f*')))
        for camera_path in camera_paths:
            logging.debug(f"indexing camera_path {camera_path}")
            macaddr = os.path.split(camera_path)[-1]
            still_paths = sorted(glob.glob(os.path.join(camera_path, '*-*-*', 'pic_001', '*.jpg')))
            camera_id = cameras[module_index][macaddr]['id']
            for still_path in still_paths:
                rpath = os.path.relpath(still_path, data_dir)
                ds, _, fn = rpath.split(os.path.sep)[-3:]
                ts = '_'.join((ds, fn.split('[')[0]))
                dt = datetime.datetime.strptime(ts, '%Y-%m-%d_%H.%M.%S')
                logging.debug(f"Found still for camera {camera_id} at {dt} in file {rpath}")
                db.execute(
                    "INSERT INTO stills (camera_id, timestamp, path) VALUES (?, ?, ?)",
                    (camera_id, dt, rpath))


if __name__ == '__main__':
    with sqlite3.connect(dbfn) as db:
        index_cameras(db, force)
        index_configs(db, force)
        index_detections(db, force)
        index_videos(db, force)
        index_stills(db, force)
        db.commit()

"""
Run select on sqlite3 database to get images (and annotations) to annotate
Symlink images to temporary folder
Run labelme to annotate images
Parse labelme annotations
Save annotations to database
"""

import copy
import glob
import json
import logging
import math
import os
import sqlite3
import subprocess


camera_id = 0
date = '2020-07-31'
first_hour = 5
last_hour = 20

data_dir = '/media/graham/377CDC5E2ECAB822'
db_fn = 'pcam.sqlite3'
tempdir = 'tmp'

day = datetime.datetime.strptime(date, '%Y-%m-%d')
min_time = day + datetime.timedelta(hours=first_hour)
max_time = day + datetime.timedelta(hours=last_hour)

min_time = datetime.timedelta(days=
max_time = 

db = sqlite3.connect(db_fn, detect_types=sqlite3.PARSE_DECLTYPES)

logging.basicConfig(level=logging.DEBUG)

tags = {0: 'note', 1: 'start', 2: 'end'}
labels = {0: 'note', 1: 'flower', 2: 'pollinator'}
annotation_template = {
    "version": "4.5.6",
    "shapes": [],
    "imagePath": "",  # fill with tfn
    "imageData": None,
    "imageHeight": 1944,
    "imageWidth": 2592,
}
shape_template = {
    "label": "",  # fill with label
    "points": [],  # append [x, y] as list
    "groupd_id": None,
    "shape_type": "point",
    "flags": {},
}
flags_template = {name: False for name in iter(tags.values())}

rtags = {v: k for (k, v) in tags.items()}
rlabels = {v: k for (k, v) in labels.items()}


def table_exists(db, table_name):
    res = db.execute("SELECT name from sqlite_master WHERE type='table';")
    for r in res:
        if r[0] == table_name:
            return True
    return False

# open database
# check if tables exist, if not create
# - tag_names: (tag id[int], tag name[str])
if not table_exists(db, 'tag_names'):
    logging.info("database missing tag_names table, adding...")
    db.execute(
        "CREATE TABLE tag_names ("
        "tag_id INTEGER PRIMARY KEY,"
        "name TEXT"
        ");")
    for tag_id in tags:
        db.execute(
            "INSERT INTO tag_names (tag_id, name) VALUES (?, ?);",
            (tag_id, tags[tag_id]))
    db.commit()
# - label_names: (label id[int], label name[str])
if not table_exists(db, 'label_names'):
    logging.info("database missing label_names table, adding...")
    db.execute(
        "CREATE TABLE label_names ("
        "label_id INTEGER PRIMARY KEY,"
        "name TEXT"
        ");")
    for label_id in labels:
        db.execute(
            "INSERT INTO label_names (label_id, name) VALUES (?, ?);",
            (label_id, labels[label_id]))
    db.commit()
# - tags: (still id[int], tag id[int])  # can be multiple per image
if not table_exists(db, 'tags'):
    logging.info("database missing tags table, adding...")
    db.execute(
        "CREATE TABLE tags ("
        "annotation_id INTEGER PRIMARY KEY,"
        "still_id INTEGER,"
        "tag_id INTEGER"
        ");")
    db.commit()
# - labels: (still id[int], label id[int], x[int], y[int])
if not table_exists(db, 'labels'):
    logging.info("database missing labels table, adding...")
    db.execute(
        "CREATE TABLE labels ("
        "annotation_id INTEGER PRIMARY KEY,"
        "still_id INTEGER,"
        "label_id INTEGER,"
        "x INTEGER,"
        "y INTEGER"
        ");")
    db.commit()

# get fns from database selecting for camera and time
file_infos = []
for s in db.execute(
        "SELECT * FROM stills WHERE "
        "camera_id=? AND "
        "timestamp>=? AND timestamp<=?",
        (camera_id, min_time, max_time)):
    still_id, camera_id, timestamp, path = s
    file_infos.append({
        'path': os.path.join(data_dir, path),
        'timestamp': timestamp,
        'camera_id': camera_id,
        'still_id': still_id})
#images_dir = 'images/'
#fns = sorted(glob.glob(os.path.join(images_dir + '*')))

if not os.path.exists(tempdir):
    os.makedirs(tempdir)

# clean up files in temp directory
for tfn in os.listdir(tempdir):
    os.remove(os.path.join(tempdir, tfn))

# symlink files to temp directory
#ndigits = int(math.log10(len(fns)) + 1)
ndigits = int(math.log10(len(file_infos)) + 1)
fn_indices = {}
for (index, fi) in enumerate(file_info):
    fn = fi['path']
    ts = fi['timestamp'].strftime('%y%m%d_%H%M')
    ext = os.path.splitext(fn)[1].strip('.')

    # make descriptive filename: add time
    tfn = '.'.join((
        str(index).zfill(ndigits) + '_' + ts,
        ext))

    os.symlink(os.path.abspath(fn), os.path.join(tempdir, tfn))
    fn_indices[tfn] = index

    previous_tags = []
    for r in db.execute("SELECT tag_id FROM tags WHERE still_id=?", (index, )):
        logging.debug(f"Found previous tag {r} for {index}")
        previous_tags.append(tags[r[0]])

    previous_labels = []
    for r in db.execute("SELECT label_id, x, y FROM labels WHERE still_id=?", (index, )):
        logging.debug(f"Found previous point {r} for {index}")
        previous_labels.append({
            'name': labels[r[0]],
            'xy': (r[1], r[2]),
        })

    # write out json for any previous annotations
    if len(previous_tags) or len(previous_labels):
        annotation = copy.deepcopy(annotation_template)
        annotation['flags'] = copy.deepcopy(flags_template)
        for tag in previous_tags:
            annotation['flags'][tag] = True
        for label in previous_labels:
            shape = copy.deepcopy(shape_template)
            shape["label"] = label["name"]
            shape["points"].append(label["xy"])
            annotation['shapes'].append(shape)
        annotation["imagePath"] = tfn
        jfn = os.path.join(tempdir, os.path.splitext(tfn)[0] + ".json")
        with open(jfn, "w") as f:
            json.dump(annotation, f)

# run labelme to annotate images
cmd = [
    "labelme",
    tempdir,
    "--nodata",
    "--autosave",
    "--flags",
    ",".join(sorted(list(tags.values()))),
    "--labels",
    ",".join(sorted(list(labels.values()))),
]
subprocess.check_call(cmd)

# parse annotations
annotation_filenames = sorted(glob.glob(os.path.join(tempdir, '*.json')))
for afn in annotation_filenames:
    # load and parse annotation
    with open(afn, 'r') as f:
        data = json.load(f)

        still_id = fn_indices[data["imagePath"]]
        logging.debug(f"Found annotations for {still_id}")

        # save flags
        flags = data['flags']
        for flag in data["flags"]:
            if data["flags"][flag]:
                tag_id = rtags[flag]
                # add flag/tag to database (if not already there)
                if not db.execute(
                        "SELECT tag_id FROM tags WHERE "
                        "still_id=? AND tag_id=?", (still_id, tag_id)).fetchone():
                    logging.debug(f"\tinserting tag {tag_id} into database")
                    db.execute(
                        "INSERT INTO tags (still_id, tag_id) VALUES (?, ?);",
                        (still_id, tag_id))
                    db.commit()

        # save labels
        for s in data['shapes']:
            if s['shape_type'] != 'point':
                logging.warning(f"\tinvalid shape type {s['shape_type']}")
                continue
            pts = s['points']
            assert len(pts) == 1
            x, y = pts[0]
            label_id = rlabels[s['label']]
            datum = (still_id, label_id, int(x), int(y))
            if not db.execute(
                    "SELECT label_id FROM labels WHERE "
                    "still_id=? AND label_id=? AND x=? AND y=?", datum).fetchone():
                logging.debug(f"\tinserting point{datum[1:]} into database")
                db.execute(
                    "INSERT INTO labels (still_id, label_id, x, y) "
                    "VALUES (?, ?, ?, ?);", datum)
                db.commit()

# write annotations to disk
db.commit()
db.close()

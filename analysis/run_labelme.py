"""
Run select on sqlite3 database to get images (and annotations) to annotate
Symlink images to temporary folder
Run labelme to annotate images
Parse labelme annotations
Save annotations to database

run_labelme.py
  -c <camera_id>
  -d <date as YYMMDD>
  -f <first hour> -l <last hour>
  -s <data source/directory>
  -D <database file path>

cmd args take precedence

if cmd args are not there check env variables
PCAM_LM_CAMERA_ID
PCAM_LM_DATE
PCAM_LM_FIRST_HOUR
PCAM_LM_LAST_HOUR
PCAM_LM_DATA_DIR
PCAM_LM_DATABASE_FILENAME

finally use defaults

if a day successfully finishes, increment the day and save to env
"""

import argparse
import copy
import datetime
import glob
import json
import logging
import math
import os
import sqlite3
import subprocess


options = [
    ('camera_id', 'c', int(os.environ.get('PCAM_LM_CAMERA_ID', '10'))),
    ('date', 'd', os.environ.get('PCAM_LM_DATE', '200923')),
    ('first_hour', 'f', int(os.environ.get('PCAM_LM_FIRST_HOUR', '5'))),
    ('last_hour', 'l', int(os.environ.get('PCAM_LM_LAST_HOUR', '20'))),
    ('data_dir', 'D', os.environ.get(
        'PCAM_LM_DATA_DIR', '/media/graham/377CDC5E2ECAB822')),
    ('database_filename', 'b', os.environ.get(
        'PCAM_LM_DATABASE_FILENAME', 'pcam.sqlite')),
    ('tmp_dir', 't', os.environ.get('PCAM_LM_TMP_DIR', 'tmp')),
]

parser = argparse.ArgumentParser()
for option in options:
    name, short_name, default = option
    parser.add_argument(
        f'-{short_name}', f'--{name}', default=default, type=type(default))

parser.add_argument(
    '-v', '--verbose',
    default=os.environ.get('PCAM_LM_VERBOSE', False), action='store_true')

args = parser.parse_args()
if args.verbose:
    logging.basicConfig(level=logging.DEBUG)
logging.info(f"Running with options: {vars(args)}")

day = datetime.datetime.strptime(args.date, '%y%m%d')
min_time = day + datetime.timedelta(hours=args.first_hour)
max_time = day + datetime.timedelta(hours=args.last_hour)

db = sqlite3.connect(args.database_filename, detect_types=sqlite3.PARSE_DECLTYPES)


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
        "timestamp>=? AND timestamp<=?;",
        (args.camera_id, min_time, max_time)):
    still_id, args.camera_id, timestamp, path = s
    file_infos.append({
        'path': os.path.join(args.data_dir, path),
        'timestamp': timestamp,
        'camera_id': args.camera_id,
        'still_id': still_id})
if len(file_infos) == 0:
    raise Exception("No files found")
print("{} files found".format(len(file_infos)))
#images_dir = 'images/'
#fns = sorted(glob.glob(os.path.join(images_dir + '*')))

if not os.path.exists(args.tmp_dir):
    os.makedirs(args.tmp_dir)

# clean up files in temp directory
for tfn in os.listdir(args.tmp_dir):
    os.remove(os.path.join(args.tmp_dir, tfn))

# symlink files to temp directory
#ndigits = int(math.log10(len(fns)) + 1)
ndigits = int(math.log10(len(file_infos)) + 1)
fn_indices = {}
previously_annotated_images = set()
for (index, fi) in enumerate(file_infos):
    fn = fi['path']
    ts = fi['timestamp'].strftime('%y%m%d_%H%M')
    still_id = fi['still_id']
    ext = os.path.splitext(fn)[1].strip('.')

    # make descriptive filename: add time
    tfn = '.'.join((
        str(index).zfill(ndigits) +
        f'_{args.camera_id}_{ts}',
        ext))

    os.symlink(os.path.abspath(fn), os.path.join(args.tmp_dir, tfn))
    fn_indices[tfn] = index

    previous_tags = []
    for r in db.execute("SELECT tag_id FROM tags WHERE still_id=?", (still_id, )):
        logging.debug(f"Found previous tag {r} for {still_id}")
        previously_annotated_images.add(still_id)
        previous_tags.append(tags[r[0]])

    previous_labels = []
    for r in db.execute("SELECT label_id, x, y FROM labels WHERE still_id=?", (still_id, )):
        logging.debug(f"Found previous point {r} for {still_id}")
        previously_annotated_images.add(still_id)
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
        jfn = os.path.join(args.tmp_dir, os.path.splitext(tfn)[0] + ".json")
        with open(jfn, "w") as f:
            json.dump(annotation, f)

# run labelme to annotate images
cmd = [
    "labelme",
    args.tmp_dir,
    "--config",
    "labelmerc",
    "--flags",
    ",".join(sorted(list(tags.values()))),
    "--labels",
    ",".join(sorted(list(labels.values()))),
]
subprocess.check_call(cmd)

# remove all old annotations for this camera/date
for still_id in previously_annotated_images:
    logging.debug(f"Removing previous annotations for {still_id}")
    db.execute("DELETE FROM tags WHERE still_id=?", (still_id, ))
    db.execute("DELETE FROM labels WHERE still_id=?", (still_id, ))

# parse annotations
annotation_filenames = sorted(glob.glob(os.path.join(args.tmp_dir, '*.json')))
for afn in annotation_filenames:
    # load and parse annotation
    with open(afn, 'r') as f:
        data = json.load(f)

        index = fn_indices[data["imagePath"]]
        info = file_infos[index]
        still_id = info['still_id']
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

# everything finished, increment date
next_day = day + datetime.timedelta(days=1)
next_day_str = next_day.strftime('%y%m%d')
logging.info(f"Setting date environment variable to {next_day_str}")
os.environ.set('PCAM_LM_DATE', next_day_str)

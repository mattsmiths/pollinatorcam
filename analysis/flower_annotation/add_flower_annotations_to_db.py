import datetime
import glob
import json
import os
import sqlite3


database_filename = '../pcam.sqlite'
annotations_db_fn = '../210318.sqlite'
annotation_directory = 'flower_annotations_210512'

db = sqlite3.connect(args.database_filename, detect_types=sqlite3.PARSE_DECLTYPES)


def find_still_id(camera, timestamp):
    return 0
    s = timestamp.strftime('%Y-%m-%d %H:%M:')
    start = s + '00'
    end = s + '59'
    stills = db.execute(
        'SELECT still_id FROM stills "
        "WHERE camera_id=? AND timestamp>=? AND timestamp<=?',
        camera, start, end).fetchall()
    assert len(stills) == 1, f"Failed to resolve still: {stills}"
    return stills[0]


def load_json_annotations(directory):
    annotations = []
    for fn in sorted(glob.glob(os.path.join(directory, '*.json'))):
        with open(fn, 'r') as f:
            d = json.load(f)

            # read in flags/tags
            tags = {}
            for k in d['flags']:
                if d['flags'][k]:
                    tags[k] = True

            # read in shapes, make sure all are rectangles
            bboxes = []
            for shape in d['shapes']:
                if shape['shape_type'] != 'rectangle':
                    raise Exception(
                        f"Invalid non-rectangle shape: {shape['shape_type']}")
                (x0, y0), (x1, y1) = shape['points'][0], shape['points'][1]
                top, bottom = (y0, y1) if y0 < y1 else (y1, y0)
                left, right = (x0, x1) if x0 < x1 else (x1, x0)
                bboxes.append({
                    'top': top, 'left': left,
                    'bottom': bottom, 'right': right,
                    'label': shape['label']})

            # find corresponding still_id using filename:
            # <index>_<camera_id>_<yymmdd_hhmm>.jpg
            tokens = d['imagePath'].split('.')[0].split('_')
            assert len(tokens) == 4, "Invalid number of filename tokens"
            camera_id = int(tokens[1])
            timestamp_string = "_".join((tokens[2], tokens[3]))
            timestamp = datetime.datetime.strptime(
                timestamp_string, '%y%m%d_%H%M')
            still_id = find_still_id(camera_id, timestamp)

            annotations.append({
                'still_id': still_id,
                'timestamp': timestamp,
                'bboxes': bboxes,
                'tags': tags,
            })
    return annotations


def index_annotations(annotations):
    all_tags = set([])
    bbox_labels = set([])
    for a in annotations:
        for t in a['tags']:
            all_tags.add(t)
        for b in a['bboxes']:
            bbox_labels.add(b['label'])
    return sorted(list(all_tags)), sorted(list(bbox_labels))


def table_exists(db, table_name):
    res = db.execute("SELECT name from sqlite_master WHERE type='table';")
    for r in res:
        if r[0] == table_name:
            return True
    return False


def read_tag_names(db):
    # get existing tag names
    tag_names_by_code = dict(db.execute(
        "SELECT (tag_id, name) FROM tag_names").fetchall())
    tag_codes_by_name = {v: k for k, v in tag_names_by_code}
    assert len(tag_names_by_code) == len(tag_codes_by_name)
    return tag_names_by_code, tag_codes_by_name


def read_bbox_labels(db):
    bbox_labels_by_code = dict(db.execute(
        "SELECT (bbox_label_id, name) FROM bbox_labels").fetchall())
    bbox_labels_by_name = {v: k for k, v in bbox_labels_by_code}
    assert len(bbox_labels_by_code) == len(bbox_labels_by_name)
    return bbox_labels_by_code, bbox_labels_by_name


def add_tag_names_to_db(db, tags):
    # get existing tag names
    tag_names_by_code, tag_codes_by_name = read_tag_names(db)

    # skip adding tag names that are already in the database
    for tag in tags:
        if tag in tag_codes_by_name:
            continue
        db.execute(
            "INSERT INTO tag_names (name) VALUES (?);",
            (tag, ))
    db.commit()


def add_bbox_labels_to_db(db, bbox_labels):
    # if no bbox_labels table exists, create it
    if not table_exists(db, 'bbox_labels'):
        db.execute(
            "CREATE TABLE bbox_labels ("
            "bbox_label_id INTEGER PRIMARY KEY,"
            "name TEXT"
            ");")
        db.commit()
    # get existing bbox labels
    bbox_labels_by_code, bbox_codes_by_label = read_bbox_labels()

    # skip adding bbox labels that are already in the database
    for bbox_label in bbox_labels:
        if bbox_label in bbox_codes_by_label:
            continue
        db.execute(
            "INSERT INTO bbox_labels (name) VALUES (?);",
            (bbox_label, ))
    db.commit()


def add_annotations_to_db(db, annotations):
    if not table_exists(db, 'tags'):
        raise Exception("database missing tags table")
    if not table_exists(cb, 'bboxes'):
        db.execute(
            "CREATE TABLE bboxes ("
            "bbox_id INTEGER PRIMARY KEY,"
            "still_id INTEGER,"
            "label_id INTEGER,"
            "left REAL,"
            "top REAL,"
            "right REAL,"
            "bottom, REAL"
            ");")
        db.commit()
    tags_by_code, tags_by_name = read_tag_names()
    labels_by_code, labels_by_label = read_bbox_labels()
    for a in annotations:
        print(f"Annotations for {a['still_id']}")
        for b in a['bboxes']:
            code = labels_by_label[b['label']]
            print(f"\tbounding box for {b['label']}")
            db.execute(
                "INSERT INTO bboxes "
                "(still_id, label_id, left, top, right, bottom) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (a['still_id'], code, b['left'], b['top'], b['right'], b['bottom']))
        for t in a['tags']:
            if not a['tags'][t]:
                continue
            print(f"\ttag {t}")
            code = tags_by_name[t]
            db.execute(
                "INSERT INTO tags (still_id, tag_id) "
                "VALUES (?, ?)", (a['still_id'], code))
        db.commit()


if __name__ == '__main__':
    annotations = load_json_annotations(annotation_directory)

    # get lists of all unique tags and bbox labels
    all_tags, all_labels = index_annotations(annotations)

    # add these lists to the db
    add_tag_names_to_db(annotation_db, all_tags)
    add_bbox_labels_to_db(annotation_db, all_labels)

    # using the code dictionaries, add all annotations to db
    add_annotations_to_db(db, annotations)

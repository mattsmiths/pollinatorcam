import sqlite3


primary_keys_by_table = {
    'cameras': 'camera_id',
    'configs': 'config_id',
    'detections': 'detection_id',
    'videos': 'video_id',
    'stills': 'still_id',
    'crude_results': 'still_id',
    'tags': 'annotation_id',
    'tag_names': 'tag_id',
    'labels': 'annotation_id',
    'label_names': 'label_id',
    'bboxes': 'bbox_id',
    'bbox_labels': 'bbox_label_id',
}

schema_by_table = {
    # skip cameras, configs, detections, videos, stills, crude_results
    'tags': "CREATE TABLE tags (annotation_id INTEGER PRIMARY KEY, still_id INTEGER, tag_id INTEGER);",
    'tag_names': "CREATE TABLE tag_names (tag_id INTEGER PRIMARY KEY, name TEXT);",
    'labels': "CREATE TABLE labels (annotation_id INTEGER PRIMARY KEY, still_id INTEGER, label_id INTEGER, x INTEGER, y INTEGER);",
    'label_names': "CREATE TABLE label_names (label_id INTEGER PRIMARY KEY, name TEXT);",
    'bboxes': "CREATE TABLE bboxes (bbox_id INTEGER PRIMARY KEY, still_id INTEGER, label_id INTEGER, left REAL, top REAL, right REAL, bottom REAL);",
    'bbox_labels': "CREATE TABLE bbox_labels (bbox_label_id INTEGER PRIMARY KEY, name TEXT);",
}

db_fn = 'pcam.sqlite'

db = sqlite3.connect(db_fn, detect_types=sqlite3.PARSE_DECLTYPES)

# look for tables with missing primary keys
# make a new table with the correct primary key
# check for data where the key is None
# prompt user asking them to confirm
# copy over data

# save database
for table_row in db.execute("SELECT * FROM sqlite_master WHERE type='table';").fetchall():
    _, name, _, _, sql = table_row

    print(f"Found table: {name}")

    # check for a column listed as a primary key
    if 'PRIMARY KEY' in sql:
        print(f"\thas PRIMARY KEY: {sql}")
        continue

    # missing primary key
    if name not in primary_keys_by_table:
        raise Exception(f"Found unknown table[{name}] with missing primary key")

    # check for values where what should be the primary key is None
    primary_key = primary_keys_by_table[name]
    bad_values = db.execute(f'SELECT * FROM {name} WHERE {primary_key} IS ?', (None, )).fetchall()
    if len(bad_values):
        print("\tfound {len(bad_values)} rows where primary key[{primary_key}] is None")
        print("\t\tExample: {bad_values[0]}")
        response = input("\tWould you like to delete these rows? [y]es/[n]o")
        if len(response) and response.strip().lower()[0] == 'y':
            print("\t\tDeleting {len(bad_values)} rows")
            db.execute('DELETE FROM {name} WHERE {primary_key} IS ?', (None, ))
            db.commit()
            bad_values = db.execute(f'SELECT * FROM {name} WHERE {primary_key} IS ?', (None, )).fetchall()
            if len(bad_values):
                raise Exception("Failed to delete bad values")

    # get number of values from old table
    n_values = db.execute(f'SELECT COUNT(*) FROM {name};').fetchone()[0]

    # make a new table with correct schema
    new_name = name + '_new'
    if new_name in primary_keys_by_table:
        raise Exception(f"Failed to make unique new name [{new_name}]")

    if name not in schema_by_table:
        raise Exception(f"Missing schema for table {name}")

    schema = schema_by_table[name]
    db.execute(schema)

    # copy over values
    db.execute(f"INSERT INTO {new_name} SELECT * FROM {name}")
    db.commit()
    new_n_values = db.execute(f'SELECT COUNT(*) FROM {new_name};').fetchone()[0]
    if new_n_values != n_values:
        raise Exception(f"Failed to copy over values: {new_n_values} != {n_values}")

    # drop old table
    db.execute(f"DROP TABLE {name};")
    db.commit()
    # rename new to old
    db.execute(f"ALTER TABLE {new_name} RENAME TO {name};")
    db.commit()


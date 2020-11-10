import sqlite3

db = sqlite3.connect('pcam.sqlite', detect_types=sqlite3.PARSE_DECLTYPES)

# count camera/module pairs
cameras = [r for r in db.execute("SELECT camera_id, mac, module FROM cameras").fetchall()]
print("{} camera/modules pairs".format(len(cameras)))

# count unique macaddrs
macaddrs = set([r[1] for r in cameras])
print("{} unique camera mac addresses".format(len(macaddrs)))


def get_timestamps(table, camera_id):
    return [
        r[0] for r in
        db.execute(
            f"SELECT timestamp FROM {table} WHERE camera_id=?",
            (camera_id, )).fetchall()]

# for each camera/module pair
camera_data = {}
for camera in cameras:
    # print out module, macaddr, id
    camera_id, mac, module_id = camera
    print(f"Camera {camera_id}, mac={mac}, module={module_id}")

    config_timestamps = get_timestamps('configs', camera_id)
    detection_timestamps = get_timestamps('detections', camera_id)
    video_timestamps = get_timestamps('videos', camera_id)
    still_timestamps = get_timestamps('stills', camera_id)

    print("\t{} config changes".format(len(config_timestamps)))
    print("\t{} detection events".format(len(detection_timestamps)))
    print("\t{} videos".format(len(video_timestamps)))
    print("\t{} stills".format(len(still_timestamps)))

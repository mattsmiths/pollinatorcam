import datetime
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


def format_breaks(still_blocks):
    st = still_blocks[0][0]
    et = still_blocks[-1][1]
    days = (et - st).days

    bi = 0
    #b = still_blocks[0]
    td = datetime.timedelta(days=1)
    cursor = st
    s = ''
    while cursor < et:
        l, r = cursor, cursor + td
        if bi < len(still_blocks):
            bl, br = still_blocks[bi]
            if l >= bl and r <= br:  # in block
                s += '*'
            elif l > br:  # off end of block, check next block
                bi += 1
                continue
            elif r < bl:  # not yet at next block, missing data
                s += ' '
            else:  # partial
                s += '-'
        cursor = r
    return s


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

    if len(still_timestamps) > 999:
        # find periods with consistent stills
        still_blocks = []
        block_start = still_timestamps[0]
        ps = still_timestamps[0]
        for s in still_timestamps[1:]:
            if (s - ps).seconds > 90:  # new block
                still_blocks.append((block_start, s))
                block_start = s
            ps = s
        if len(still_blocks) and still_blocks[-1][1] != s:
            still_blocks.append((block_start, s))

        n_still_breaks = max(len(still_blocks) - 1, 0)
    else:
        print("\t skipping {} < 1000 stills".format(len(still_timestamps)))
        continue

    print("\t{} config changes".format(len(config_timestamps)))
    print("\t{} detection events".format(len(detection_timestamps)))
    print("\t{} videos".format(len(video_timestamps)))
    print("\t{} stills".format(len(still_timestamps)))
    print(f"\t\t{n_still_breaks} breaks")
    print("\t\t|{}|".format(format_breaks(still_blocks)))
    camera_data[camera_id] = {
        'mac': mac,
        'module': module_id,
        'configs': config_timestamps,
        'detections': detection_timestamps,
        'videos': video_timestamps,
        'stills': still_timestamps,
        'still_blocks': still_blocks,
        'n_still_breaks': n_still_breaks,
    }

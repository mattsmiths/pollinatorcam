# Maybe just use amcrest for a fuller api and hack together an
# api for JUST what I need
# - whatever is needed for camera configuration
# - fps control

import os

import requests


def build_camera_url(
        ip, user=None, password=None, channel=1, subtype=0):
    if user is None:
        user = os.environ['PCAM_USER']
    if password is None:
        password = os.environ['PCAM_PASSWORD']
    return (
        "rtsp://{user}:{password}@{ip}:554"
        "/cam/realmonitor?channel={channel}&subtype={subtype}".format(
            user=user,
            password=password,
            ip=ip,
            channel=channel,
            subtype=subtype))


class DahuaCamera:
    def __init__(self, ip, user=None, password=None):
        if user is None:
            user = os.environ['PCAM_USER']
        if password is None:
            password = os.environ['PCAM_PASSWORD']
        self.user = user
        self.password = password
        self.ip = ip

        self.session = requests.Session()
        self.session.auth = requests.auth.HTTPDigestAuth(
            self.user, self.password)

    def rtsp_url(self, channel=1, subtype=0):
        return (
            "rtsp://{user}:{password}@{ip}:554"
            "/cam/realmonitor?channel={channel}&subtype={subtype}".format(
                user=self.user,
                password=self.password,
                ip=self.ip,
                channel=channel,
                subtype=subtype))

    def get_input_caps(self, channel=1):
        url = (
            "http://{ip}/cgi-bin/devVideoInput.cgi?"
            "action=getCaps&channel={channel}".format(
                ip=self.ip, channel=channel))
        r = self.session.get(url)
        # TODO parse text, check return code
        return r.text

    # getConfig = get_input_options, get_config_caps, get_encode_config
    def get_config(self, parameter):
        """Returns video and audio"""
        url = (
            "http://{ip}/cgi-bin/configManager.cgi?"
            "action=getConfig&name={parameter}".format(
                ip=self.ip,
                parameter=parameter))
        r = self.session.get(url)
        # TODO parse text, check return code
        return r.text

    def get_input_options(self):
        # TODO parse text, check return code
        return self.get_config('VideoInOptions')

    def set_options(self, **kwargs):
        """Set video and audio input options or encode config"""
        url = (
            "http://{ip}/cgi-bin/configManager.cgi?"
            "action=setConfig".format(ip=self.ip))
        if len(kwargs) == 0:
            raise ValueError("No parameters provided")
        for k in kwargs:
            v = kwargs[k]
            url += "&%s=%s" % (k, v)
        r = self.session.get(url)
        # TODO parse text, check return code

    def get_config_caps(self):
        """Returns video and audio"""
        url = (
            "http://{ip}/cgi-bin/encode.cgi?"
            "action=getConfigCaps".format(ip=self.ip))
        r = self.session.get(url)
        # TODO parse text, check return code
        return r.text

    def get_encode_config(self):
        """Returns video and audio"""
        # TODO parse text, check return code
        return self.get_config('Encode')

    def get_video_standard(self):
        # TODO parse text, check return code
        return self.get_config('VideoStandard')

    def get_video_widget(self):
        # TODO parse text, check return code
        return self.get_config('VideoWidget')

    def get_network_interfaces(self):
        url = (
            "http://{ip}/cgi-bin/netApp.cgi?"
            "action=getInterfaces".format(ip=self.ip))
        r = self.session.get(url)
        # TODO parse text, check return code
        return r.text

    def get_upnp_status(self):
        url = (
            "http://{ip}/cgi-bin/netApp.cgi?"
            "action=getUPnPStatus".format(ip=self.ip))
        r = self.session.get(url)
        # TODO parse text, check return code
        return r.text

    def get_network_config(self):
        # TODO parse text, check return code
        return self.get_config('Network')

    def get_pppoe_config(self):
        # TODO parse text, check return code
        return self.get_config('PPPoE')

    def get_ddns_config(self):
        # TODO parse text, check return code
        return self.get_config('DDNS')

    def get_email_config(self):
        # TODO parse text, check return code
        return self.get_config('Email')

    def get_wlan_config(self):
        # TODO parse text, check return code
        return self.get_config('WLan')

    def get_upnp_config(self):
        # TODO parse text, check return code
        return self.get_config('UPnP')

    def get_ntp_config(self):
        # TODO parse text, check return code
        return self.get_config('NTP')

    def get_alarm_server_config(self):
        # TODO parse text, check return code
        return self.get_config('AlarmServer')

    def get_alarm_config(self):
        # TODO parse text, check return code
        # TODO bad request, not sure if this is just not supported
        return self.get_config('Alarm')

    def get_alarm_out_config(self):
        # TODO parse text, check return code
        # TODO bad request, not sure if this is just not supported
        return self.get_config('AlarmOut')

    def get_alarm_url(self, action):
        url = (
            "http://{ip}/cgi-bin/alarm.cgi?"
            "action={action}".format(ip=self.ip, action=action))
        r = self.session.get(url)
        # TODO parse text, check return code
        return r.text

    def get_alarm_in_slots(self):
        return self.get_alarm_url('getInSlots')

    def get_alarm_out_slots(self):
        return self.get_alarm_url('getOutSlots')

    def get_alarm_in_states(self):
        return self.get_alarm_url('getInStates')

    def get_alarm_out_states(self):
        return self.get_alarm_url('getOutStates')

    def get_motion_detect_config(self):
        # TODO parse text, check return code
        return self.get_config('MotionDetect')

    def get_blind_detect_config(self):
        # TODO parse text, check return code
        return self.get_config('BlindDetect')

    def get_loss_detect_config(self):
        # TODO parse text, check return code
        return self.get_config('LossDetect')

    def get_event_indices(self, code):
        if code not in ('VideoMotion', 'VideoLoss', 'VideoBlind'):
            raise ValueError("Invalid code")
        url = (
            "http://{ip}/cgi-bin/eventManager.cgi?"
            "action=getEventIndexes&code={code}".format(
                ip=self.ip, code=code))
        r = self.session.get(url)
        # TODO parse text, check return code
        return r.text

    # Skipped PTZ

    def get_record_config(self):
        return self.get_config('Record')

    def get_record_mode_config(self):
        return self.get_config('RecordMode')

    def get_snap_config(self):
        return self.get_config('Snap')

    def get_general_config(self):
        return self.get_config('General')

    def get_current_time(self):
        url = (
            "http://{ip}/cgi-bin/global.cgi?"
            "action=getCurrentTime".format(ip=self.ip))
        r = self.session.get(url)
        # TODO parse text, check return code
        return r.text

    def set_current_time(self, new_datetime=None):
        # TODO get current datetime
        # convert to timestamp
        ts = None
        raise NotImplementedError()
        url = (
            "http://{ip}/cgi-bin/global.cgi?"
            "action=setCurrentTime&time={ts}".format(ip=self.ip, ts=ts))
        r = self.session.get(url)
        # TODO parse text, check return code
        return r.text

    def get_locales_config(self):
        return self.get_config('Locales')

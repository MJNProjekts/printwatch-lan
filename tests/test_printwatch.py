"""Unit tests for PrintWatch LAN logic (no printer, no network). Run: python -m unittest -v"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import printwatch as pw  # noqa: E402

T0 = datetime(2026, 10, 24, 21, 0).astimezone()


def fresh(lang="en", first_layer=True):
    st = pw.PrinterState("P1S", pw.Lang(lang), first_layer)
    st.connected = True
    st.update({"print": {"gcode_state": "IDLE"}}, T0)
    return st


class TestStateMachine(unittest.TestCase):
    def test_greeting_idle(self):
        st = pw.PrinterState("P1S", pw.Lang("en"))
        msgs = st.update({"print": {"gcode_state": "IDLE"}}, T0)
        self.assertEqual(msgs, ["🖨 PrintWatch connected to P1S. Printer: idle"])

    def test_full_cycle(self):
        st = fresh()
        m = st.update({"print": {"gcode_state": "PREPARE", "subtask_name": "a", "total_layer_num": 10,
                                 "mc_remaining_time": 0}}, T0)
        self.assertIn("Print started: a", m[0])
        self.assertIn("Preparing", m[0])           # no "0 min" ETA while preparing
        self.assertNotIn("0 min", m[0])
        self.assertEqual(st.update({"print": {"gcode_state": "RUNNING", "layer_num": 1,
                                              "mc_remaining_time": 30}}, T0), [])
        m = st.update({"print": {"layer_num": 2}}, T0 + timedelta(minutes=5))
        self.assertIn("First layer done", m[0])
        self.assertIn("done ~21:35", m[0])
        m = st.update({"print": {"gcode_state": "FINISH"}}, T0 + timedelta(minutes=40))
        self.assertIn("Print finished: a", m[0])
        self.assertIn("40 min", m[0])

    def test_pause_with_error_and_resume(self):
        st = fresh()
        st.update({"print": {"gcode_state": "RUNNING", "subtask_name": "b", "layer_num": 5}}, T0)
        m = st.update({"print": {"gcode_state": "PAUSE", "print_error": 0x07008011}}, T0)
        self.assertIn("PAUSED", m[0])
        self.assertIn("0700 8011", m[0])
        m = st.update({"print": {"gcode_state": "RUNNING", "print_error": 0}}, T0)
        self.assertIn("resumed", m[0])

    def test_failed(self):
        st = fresh()
        st.update({"print": {"gcode_state": "RUNNING", "subtask_name": "c"}}, T0)
        m = st.update({"print": {"gcode_state": "FAILED", "print_error": 0x0300400D}},
                      T0 + timedelta(minutes=10))
        self.assertIn("0300 400D", m[0])
        self.assertIn("After: 10 min", m[0])

    def test_restart_mid_print_no_duplicates(self):
        st = pw.PrinterState("P1S", pw.Lang("en"))
        m = st.update({"print": {"gcode_state": "RUNNING", "subtask_name": "d", "layer_num": 30,
                                 "total_layer_num": 100}}, T0)
        self.assertIn("Printing: d", m[0])
        self.assertEqual(st.update({"print": {"layer_num": 31}}, T0), [])

    def test_first_layer_disabled(self):
        st = fresh(first_layer=False)
        st.update({"print": {"gcode_state": "RUNNING", "layer_num": 1}}, T0)
        self.assertEqual(st.update({"print": {"layer_num": 2}}, T0), [])

    def test_error_while_running_once(self):
        st = fresh()
        st.update({"print": {"gcode_state": "RUNNING"}}, T0)
        self.assertEqual(len(st.update({"print": {"print_error": 123}}, T0)), 1)
        self.assertEqual(st.update({"print": {"print_error": 123}}, T0), [])

    def test_offline_alert_only_during_print(self):
        st = fresh()
        st.on_disconnect(T0)
        self.assertEqual(st.tick(T0 + timedelta(minutes=10)), [])     # idle: no alert
        st = fresh()
        st.update({"print": {"gcode_state": "RUNNING"}}, T0)
        st.on_disconnect(T0)
        self.assertEqual(st.tick(T0 + timedelta(minutes=3)), [])
        self.assertEqual(len(st.tick(T0 + timedelta(minutes=6))), 1)
        self.assertEqual(st.tick(T0 + timedelta(minutes=9)), [])
        self.assertEqual(len(st.on_connect()), 1)

    def test_latvian(self):
        st = fresh("lv")
        m = st.update({"print": {"gcode_state": "RUNNING", "subtask_name": "e"}}, T0)
        self.assertIn("Druka sākta: e", m[0])

    def test_start_ignores_layer_count_of_previous_job(self):
        st = fresh()
        st.update({"print": {"gcode_state": "RUNNING", "total_layer_num": 25}}, T0)
        st.update({"print": {"gcode_state": "FINISH"}}, T0)
        m = st.update({"print": {"gcode_state": "PREPARE", "subtask_name": "new"}}, T0)
        self.assertNotIn("25", m[0])
        st.update({"print": {"total_layer_num": 345}}, T0)
        self.assertIn("345", st.status_text(T0))

    def test_duration_across_dst_change(self):

        st = fresh()
        start = datetime(2026, 10, 25, 0, 0, tzinfo=timezone(timedelta(hours=2)))   # CEST
        st.update({"print": {"gcode_state": "RUNNING", "subtask_name": "dst"}}, start)
        end = datetime(2026, 10, 25, 3, 0, tzinfo=timezone(timedelta(hours=1)))    # CET, 4 h later
        m = st.update({"print": {"gcode_state": "FINISH"}}, end)
        self.assertIn("4 h", m[0])

    def test_start_ignores_remaining_time_of_previous_job(self):
        st = fresh()
        st.update({"print": {"gcode_state": "RUNNING", "mc_remaining_time": 150}}, T0)
        st.update({"print": {"gcode_state": "FAILED"}}, T0)
        m = st.update({"print": {"gcode_state": "RUNNING", "subtask_name": "short"}}, T0)
        self.assertNotIn("2 h 30", m[0])
        self.assertIn("first layer", m[0])

    def test_status_hides_targets_when_idle(self):
        st = fresh()
        st.update({"print": {"nozzle_temper": 38, "nozzle_target_temper": 38}}, T0)
        self.assertNotIn("target", st.status_text(T0))


class TestCommands(unittest.TestCase):
    def setUp(self):
        self.st = fresh()
        self.st.update({"print": {"gcode_state": "RUNNING", "subtask_name": "x",
                                  "lights_report": [{"node": "chamber_light", "mode": "off"}]}}, T0)
        self.sent = []
        self.h = pw.CommandHandler(self.st, lambda p: self.sent.append(p) or True,
                                   lambda: b"\xff\xd8x\xff\xd9")

    def test_aliases_both_languages(self):
        for c in ("/status", "/p1s", "/status@mybot"):
            self.assertIn("printing", self.h.handle(c)[0])

    def test_photo(self):
        text, img = self.h.handle("/foto")
        self.assertEqual(img[:2], b"\xff\xd8")
        self.assertIn("light is off", text)

    def test_light_toggle(self):
        self.h.handle("/light")
        self.assertEqual(self.sent[-1]["system"]["led_mode"], "on")

    def test_stop_requires_confirmation(self):
        self.h.handle("/confirmstop", now_ts=100)
        self.assertEqual(self.sent, [])
        self.h.handle("/stop", now_ts=100)
        self.h.handle("/confirmstop", now_ts=200)          # too late
        self.assertEqual(self.sent, [])
        self.h.handle("/stop", now_ts=300)
        self.h.handle("/stopja", now_ts=320)
        self.assertEqual(self.sent[-1]["print"]["command"], "stop")

    def test_pause_only_when_running(self):
        self.h.handle("/pause")
        self.assertEqual(self.sent[-1]["print"]["command"], "pause")
        self.st.update({"print": {"gcode_state": "IDLE"}}, T0)
        n = len(self.sent)
        self.h.handle("/pause")
        self.assertEqual(len(self.sent), n)

    def test_photo_waits_after_light_toggle(self):
        waits = []
        self.h.sleep = waits.append
        self.h.handle("/light", now_ts=1000)
        self.h.handle("/photo", now_ts=1001)
        self.assertAlmostEqual(waits[0], 2.0)
        self.h.handle("/photo", now_ts=1010)        # long after: no wait
        self.assertEqual(len(waits), 1)

    def test_pause_from_bot_is_explained(self):
        self.h.handle("/pause")
        m = self.st.update({"print": {"gcode_state": "PAUSE"}}, pw.now_local())
        self.assertIn("your /pause", m[0])
        self.assertNotIn("screen", m[0])

    def test_stop_from_bot_is_cancel_not_error(self):
        self.h.handle("/stop", now_ts=1)
        self.h.handle("/stopja", now_ts=2)
        now = pw.now_local()
        m = self.st.update({"print": {"print_error": 0x0300400C}}, now)
        m += self.st.update({"print": {"gcode_state": "FAILED"}}, now)
        self.assertEqual(len(m), 1)
        self.assertIn("cancelled", m[0])
        self.assertIn("/confirmstop", m[0])
        self.assertNotIn("0300", m[0])

    def test_stale_photo_is_flagged(self):
        h = pw.CommandHandler(self.st, lambda p: True, lambda: (b"\xff\xd8x\xff\xd9", False))
        self.assertIn("older frame", h.handle("/photo")[0])

    def test_unknown_is_help(self):
        self.assertIn("commands", self.h.handle("/xyz")[0].lower())


class TestAutoPhoto(unittest.TestCase):
    def start(self, rem, auto=True):
        st = fresh()
        st.auto_photo = auto
        st.update({"print": {"gcode_state": "PREPARE", "subtask_name": "a"}}, T0)
        st.update({"print": {"gcode_state": "RUNNING", "layer_num": 1, "mc_percent": 0,
                             "mc_remaining_time": rem}}, T0)
        st.update({"print": {"layer_num": 2, "mc_percent": 1, "mc_remaining_time": rem}}, T0)
        return st

    def take(self, st):
        n = len(st.photo_queue)
        st.photo_queue.clear()
        return n

    def test_short_print_every_10_percent(self):
        st = self.start(30)
        self.assertEqual(st.ap_mode, "pct")
        self.assertEqual(self.take(st), 1)                     # first layer photo
        st.update({"print": {"mc_percent": 9}}, T0)
        self.assertEqual(self.take(st), 0)
        st.update({"print": {"mc_percent": 10}}, T0)
        self.assertEqual(self.take(st), 1)
        st.update({"print": {"mc_percent": 15}}, T0)
        self.assertEqual(self.take(st), 0)
        st.update({"print": {"mc_percent": 23}}, T0)           # jump over a step: one photo
        self.assertEqual(self.take(st), 1)
        st.update({"print": {"mc_percent": 29}}, T0)
        self.assertEqual(self.take(st), 0)
        st.update({"print": {"gcode_state": "FINISH", "mc_percent": 100}}, T0)
        self.assertEqual(self.take(st), 1)                     # finish photo

    def test_long_print_every_15_minutes(self):
        st = self.start(300)
        self.assertEqual(st.ap_mode, "time")
        self.assertEqual(self.take(st), 1)
        st.update({"print": {"mc_percent": 20}}, T0)
        self.assertEqual(self.take(st), 0)                     # no % photos for long prints
        st.tick(T0 + timedelta(minutes=10))
        self.assertEqual(self.take(st), 0)
        st.tick(T0 + timedelta(minutes=15))
        self.assertEqual(self.take(st), 1)
        st.tick(T0 + timedelta(minutes=25))
        self.assertEqual(self.take(st), 0)
        st.tick(T0 + timedelta(minutes=31))
        self.assertEqual(self.take(st), 1)

    def test_no_photos_while_paused_but_photo_on_pause(self):
        st = self.start(300)
        self.take(st)
        st.update({"print": {"gcode_state": "PAUSE"}}, T0 + timedelta(minutes=5))
        self.assertEqual(self.take(st), 1)                     # pause photo
        st.tick(T0 + timedelta(minutes=40))
        self.assertEqual(self.take(st), 0)

    def test_off(self):
        st = self.start(30, auto=False)
        st.update({"print": {"mc_percent": 50}}, T0)
        st.update({"print": {"gcode_state": "FINISH"}}, T0)
        self.assertEqual(self.take(st), 0)

    def test_no_photo_before_first_layer(self):
        st = fresh()
        st.update({"print": {"gcode_state": "RUNNING", "layer_num": 1, "mc_percent": 0,
                             "mc_remaining_time": 30}}, T0)
        self.assertEqual(self.take(st), 0)

    def test_restart_mid_print_sets_rhythm_without_photo(self):
        st = pw.PrinterState("P1S", pw.Lang("en"))
        st.connected = True
        st.update({"print": {"gcode_state": "RUNNING", "layer_num": 30, "mc_percent": 50,
                             "mc_remaining_time": 200}}, T0)
        self.assertEqual(st.ap_mode, "time")                   # 400 min total
        self.assertEqual(self.take(st), 0)

    def test_status_and_toggle_command(self):
        st = self.start(300)
        self.assertIn("Auto photo: every 15 min", st.status_text(T0))
        h = pw.CommandHandler(st, lambda p: True, lambda: b"")
        self.assertIn("off", h.handle("/autofoto")[0])
        self.assertEqual(len(st.photo_queue), 0)
        self.assertIn("Auto photo: off", st.status_text(T0))
        self.assertIn("every 15 min", h.handle("/autophoto")[0])


class TestSpeedAndStatus(unittest.TestCase):
    def setUp(self):
        self.st = fresh()
        self.st.update({"print": {"gcode_state": "RUNNING", "subtask_name": "x", "spd_lvl": 2,
                                  "wifi_signal": "-57dBm", "ams": {"ams": [], "tray_now": "254"},
                                  "vt_tray": {"id": "254", "tray_type": "PETG"}}}, T0)
        self.sent = []
        self.h = pw.CommandHandler(self.st, lambda p: self.sent.append(p) or True, lambda: b"")

    def test_status_shows_speed_filament_wifi(self):
        txt = self.st.status_text(T0)
        self.assertIn("Speed: Standard (100%)", txt)
        self.assertIn("Filament: PETG", txt)
        self.assertIn("Wi-Fi: -57dBm", txt)

    def test_ams_presence(self):
        self.st.update({"print": {"ams": {"ams_exist_bits": "0"}}}, T0)
        self.assertIn("AMS: not connected", self.st.status_text(T0))
        self.st.update({"print": {"ams": {"ams_exist_bits": "1"}}}, T0)
        self.assertIn("AMS: connected", self.st.status_text(T0))
        self.st.update({"print": {"ams": {"ams_exist_bits": "3"}}}, T0)
        self.assertIn("AMS: connected (2 units)", self.st.status_text(T0))
        st = fresh("lv")
        self.assertNotIn("AMS", st.status_text(T0))            # not reported yet: no line
        st.update({"print": {"ams": {"ams_exist_bits": "0", "ams": []}}}, T0)
        self.assertIn("AMS: nav pieslēgts", st.status_text(T0))

    def test_timelapse_on_off(self):
        self.st.update({"print": {"ipcam": {"timelapse": "disable"}, "sdcard": True}}, T0)
        self.assertIn("Timelapse: off", self.st.status_text(T0))
        self.st.update({"print": {"ipcam": {"timelapse": "enable"}}}, T0)
        self.assertIn("Timelapse: on", self.st.status_text(T0))
        self.assertNotIn("SD card", self.st.status_text(T0))
        self.st.update({"print": {"sdcard": False}}, T0)
        self.assertIn("no SD card", self.st.status_text(T0))

    def test_no_wrong_duration_after_restart_mid_print(self):
        st = pw.PrinterState("P1S", pw.Lang("en"))
        st.connected = True
        st.update({"print": {"gcode_state": "RUNNING", "subtask_name": "r", "layer_num": 30}}, T0)
        self.assertNotIn("Printing for", st.status_text(T0 + timedelta(minutes=3)))
        m = st.update({"print": {"gcode_state": "FINISH"}}, T0 + timedelta(minutes=9))
        self.assertIn("Print finished", m[0])
        self.assertNotIn("9 min", m[0])

    def test_filament_hidden_when_ams_slot_in_use(self):
        self.st.update({"print": {"ams": {"tray_now": "1"}}}, T0)
        self.assertNotIn("Filament", self.st.status_text(T0))

    def test_speed_without_argument_shows_current_and_options(self):
        txt = self.h.handle("/speed")[0]
        self.assertIn("Standard (100%)", txt)
        self.assertIn("4 – Ludicrous", txt.replace("/speed", ""))
        self.assertEqual(self.sent, [])

    def test_speed_change_variants(self):
        for arg, lvl in (("3", "3"), ("sport", "3"), ("124%", "3"), ("klusais", "1"), ("166", "4")):
            self.h.handle(f"/speed {arg}")
            self.assertEqual(self.sent[-1]["print"]["command"], "print_speed")
            self.assertEqual(self.sent[-1]["print"]["param"], lvl)

    def test_speed_bad_value_and_idle(self):
        self.assertIn("Unknown speed", self.h.handle("/speed 9")[0])
        self.st.update({"print": {"gcode_state": "IDLE"}}, T0)
        self.assertIn("only during a print", self.h.handle("/speed 3")[0])
        self.assertEqual(self.sent, [])

    def test_tappable_form_without_space(self):
        self.assertIn("/speed4 – Ludicrous", self.h.handle("/speed")[0])
        self.h.handle("/speed3")
        self.assertEqual(self.sent[-1]["print"]["param"], "3")
        self.h.handle("/atrums1@mybot")
        self.assertEqual(self.sent[-1]["print"]["param"], "1")

    def test_trailing_punctuation_ignored(self):
        self.assertIn("Standard (100%)", self.h.handle("/speed,")[0])
        self.assertIn("Standard (100%)", self.h.handle("/speed!")[0])

    def test_latvian_alias(self):
        self.h.handle("/atrums 1")
        self.assertEqual(self.sent[-1]["print"]["param"], "1")

    def test_speed_change_is_reported_once(self):
        m = self.st.update({"print": {"spd_lvl": 3, "spd_mag": 124}}, T0)
        self.assertEqual(m, ["⚡ Speed changed: Sport (124%)"])
        self.assertEqual(self.st.update({"print": {"spd_lvl": 3}}, T0), [])

    def test_no_speed_message_at_job_start(self):
        self.st.update({"print": {"gcode_state": "FINISH"}}, T0)
        self.st.update({"print": {"spd_lvl": 3}}, T0)
        m = self.st.update({"print": {"gcode_state": "PREPARE", "spd_lvl": 2}}, T0)
        self.assertFalse(any("Speed changed" in x for x in m))


class FakeFiles:
    def __init__(self, items):
        self.items, self.got = items, []

    def list_videos(self):
        return self.items

    def get_video(self, name):
        self.got.append(name)
        return b"AVI-" + name.encode()


class TestTimelapse(unittest.TestCase):
    NEW, OLD = "video_2026-11-01_20-00-00.avi", "video_2026-10-31_12-00-00.avi"

    def setUp(self):
        self.st = fresh()
        self.files = FakeFiles([(self.NEW, 5 * 2**20), (self.OLD, 3 * 2**20)])
        self.notes = []
        self.h = pw.CommandHandler(self.st, lambda p: True, lambda: b"", self.files)
        self.h.notify = self.notes.append

    def test_pick_videos_sorts_newest_first(self):
        names = ["/timelapse/" + self.OLD, self.NEW, "thumbnail", "x.jpg"]
        self.assertEqual(pw.pick_videos(names), [self.NEW, self.OLD])

    def test_latest_video(self):
        text, att = self.h.handle("/timelapse")
        self.assertEqual(att["filename"], self.NEW)
        self.assertEqual(att["document"], b"AVI-" + self.NEW.encode())
        self.assertIn("Downloading", self.notes[0])

    def test_second_video_tappable(self):
        self.assertEqual(self.h.handle("/timelapse2")[1]["filename"], self.OLD)
        self.assertEqual(self.h.handle("/video 2")[1]["filename"], self.OLD)

    def test_list(self):
        text, att = self.h.handle("/timelapses")
        self.assertIsNone(att)
        self.assertIn("/timelapse1 – newest · 5.0 MB", text)
        self.assertIn("/timelapse2 – 3.0 MB", text)
        self.assertNotIn("2026", text)                       # printer clock is not trusted
        self.assertIn("not synced", text)
        self.assertEqual(self.files.got, [])

    def test_not_during_print(self):
        self.st.update({"print": {"gcode_state": "RUNNING"}}, T0)
        self.assertIn("still be recording", self.h.handle("/timelapse")[0])
        self.assertEqual(self.files.got, [])
        self.assertIn("newest", self.h.handle("/timelapses")[0])   # list still works
        self.assertEqual(self.h.handle("/timelapse2")[1]["filename"], self.OLD)   # older ones too

    def test_none_and_bad_number(self):
        self.assertIn("Unknown video number", self.h.handle("/timelapse9")[0])
        h = pw.CommandHandler(self.st, lambda p: True, lambda: b"", FakeFiles([]))
        self.assertIn("No timelapse videos", h.handle("/timelapse")[0])

    def test_too_big_is_not_downloaded(self):
        self.files.items = [(self.NEW, 80 * 2**20)]
        self.assertIn("50 MB", self.h.handle("/timelapse")[0])
        self.assertEqual(self.files.got, [])

    def test_empty_download_not_sent(self):
        self.files.get_video = lambda name: b""
        text, att = self.h.handle("/timelapse")
        self.assertIsNone(att)
        self.assertIn("empty file", text)

    def test_caption_uses_print_name(self):
        self.st.update({"print": {"gcode_state": "RUNNING", "subtask_name": "brelks",
                                  "ipcam": {"timelapse": "enable"}}}, T0)
        # printer resets the flag after the print: the caption still uses the print name
        self.st.update({"print": {"gcode_state": "FINISH", "ipcam": {"timelapse": "disable"}}}, T0)
        self.assertEqual(self.h.handle("/timelapse")[0], "🎞 Timelapse: brelks")
        self.assertEqual(self.h.handle("/timelapse2")[0], "🎞 Timelapse #2 from the SD card")
        self.st.update({"print": {"gcode_state": "PREPARE", "subtask_name": "next"}}, T0)
        self.st.update({"print": {"gcode_state": "FINISH"}}, T0)       # print without timelapse
        self.assertEqual(self.h.handle("/timelapse")[0], "🎞 Timelapse #1 from the SD card")

    def test_converted_to_mp4(self):
        self.h.convert = lambda data: b"MP4"
        text, att = self.h.handle("/timelapse")
        self.assertEqual(att, {"video": b"MP4", "filename": self.NEW[:-4] + ".mp4"})

    def test_conversion_failure_sends_avi(self):
        self.h.convert = lambda data: None
        self.assertEqual(self.h.handle("/timelapse")[1]["filename"], self.NEW)

    def test_big_avi_allowed_when_converting(self):
        self.files.items = [(self.NEW, 80 * 2**20)]
        self.h.convert = lambda data: b"MP4"
        self.assertIn("video", self.h.handle("/timelapse")[1])

    def test_error_is_reported(self):
        class Broken:
            def list_videos(self):
                raise OSError("timed out")
        h = pw.CommandHandler(self.st, lambda p: True, lambda: b"", Broken())
        self.assertIn("timed out", h.handle("/timelapse")[0])


class TestDump(unittest.TestCase):
    def test_redact_removes_private_fields(self):
        rep = {"print": {"gcode_state": "IDLE", "wifi_signal": "-60dBm",
                         "net": {"info": [{"ip": 123, "mask": 456}]},
                         "ipcam": {"rtsp_url": "rtsps://192.168.1.50/x", "timelapse": "disable"},
                         "upgrade_state": {"sn": "01P00A123", "status": "IDLE"},
                         "note": "printer 01P00A123 at 192.168.1.50",
                         "ams": {"ams": [{"tray": [{"tag_uid": "AB12", "remain": 80}]}]}}}
        out = json.dumps(pw.redact(rep, ["01P00A123", "192.168.1.50"]))
        for secret in ("01P00A123", "192.168.1.50", "AB12", '"ip": 123'):
            self.assertNotIn(secret, out)
        self.assertIn("-60dBm", out)
        self.assertIn('"remain": 80', out)
        self.assertIn('"timelapse": "disable"', out)


class TestViewers(unittest.TestCase):
    """View-only users: they may look, never control the printer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "viewers.json"
        self.st = fresh()
        self.st.update({"print": {"gcode_state": "RUNNING", "subtask_name": "x", "mc_percent": 40,
                                  "lights_report": [{"node": "chamber_light", "mode": "off"}]}}, T0)
        self.sent, self.dm = [], []
        self.h = pw.CommandHandler(self.st, lambda p: self.sent.append(p) or True,
                                   lambda: b"\xff\xd8x\xff\xd9",
                                   FakeFiles([("video_2026-11-01_20-00-00.avi", 1000)]))
        self.h.viewers = pw.Viewers("1000", self.path)
        self.h.send_to = lambda cid, text: self.dm.append((cid, text))

    def tearDown(self):
        self.tmp.cleanup()

    def add_viewer(self, cid="2000", first_name="Anna", **kw):
        n = self.h.viewers.request({"id": int(cid), "first_name": first_name, "type": "private", **kw})
        self.h.handle(f"/allow{n}")
        return n

    def test_roles(self):
        vw = self.h.viewers
        self.assertEqual(vw.role("1000"), "admin")
        self.assertIsNone(vw.role("2000"))
        self.add_viewer()
        self.assertEqual(vw.role(2000), "viewer")

    def test_viewer_cannot_control(self):
        for c in ("/light", "/gaisma", "/pause", "/resume", "/stop", "/confirmstop", "/stopja",
                  "/speed3", "/speed 1", "/autophoto", "/viewers", "/allow1", "/remove1", "/nonemt1"):
            text, att = self.h.handle(c, now_ts=100, role="viewer")
            self.assertIn("View-only", text, c)
            self.assertIsNone(att)
        self.assertEqual(self.sent, [])            # nothing ever reached the printer
        self.assertTrue(self.st.auto_photo)        # /autophoto did not toggle

    def test_viewer_stop_confirm_cannot_use_owner_stop(self):
        self.h.handle("/stop", now_ts=100)                       # owner asks to stop...
        self.h.handle("/confirmstop", now_ts=110, role="viewer")  # ...a viewer must not confirm it
        self.assertEqual(self.sent, [])

    def test_viewer_can_look(self):
        self.assertIn("printing", self.h.handle("/status", role="viewer")[0])
        self.assertIn("printing", self.h.handle("/p1s", role="viewer")[0])
        text, img = self.h.handle("/foto", role="viewer")
        self.assertEqual(img[:2], b"\xff\xd8")
        self.assertIn("light is off", text)
        self.assertNotIn("/light", text)           # no hint to a command a viewer can't use
        self.assertIn("Timelapse videos", self.h.handle("/timelapses", role="viewer")[0])
        help_text = self.h.handle("/help", role="viewer")[0]
        self.assertIn("view only", help_text)
        self.assertNotIn("/stop", help_text)

    def test_viewer_photo_switches_light_briefly(self):
        self.h.viewer_light = True
        self.h.sleep = lambda s: None
        text, img = self.h.handle("/photo", now_ts=1000, role="viewer")
        self.assertEqual(img[:2], b"\xff\xd8")
        modes = [x["system"]["led_mode"] for x in self.sent]
        self.assertEqual(modes, ["on", "off"])               # switched on, then always back off
        self.assertIn("switched on for this photo", text)

    def test_viewer_photo_light_is_rate_limited(self):
        self.h.viewer_light = True
        self.h.sleep = lambda s: None
        self.h.handle("/photo", now_ts=1000, role="viewer")
        text, img = self.h.handle("/photo", now_ts=1030, role="viewer")
        self.assertEqual(len(self.sent), 2)                  # no new light commands
        self.assertIn("next photo with light in 91 s", text)
        self.h.handle("/photo", now_ts=1121, role="viewer")
        self.assertEqual(len(self.sent), 4)

    def test_viewer_photo_light_off_restored_even_if_camera_fails(self):
        self.h.viewer_light = True
        self.h.sleep = lambda s: None
        def broken():
            raise OSError("camera timeout")
        self.h.grab_photo = broken
        text, img = self.h.handle("/photo", now_ts=1000, role="viewer")
        self.assertIn("camera timeout", text)
        self.assertEqual([x["system"]["led_mode"] for x in self.sent], ["on", "off"])

    def test_viewer_photo_light_not_touched_when_on_or_disabled(self):
        self.h.sleep = lambda s: None
        self.h.handle("/photo", now_ts=1000, role="viewer")  # feature off
        self.h.viewer_light = True
        self.st.update({"print": {"lights_report": [{"node": "chamber_light", "mode": "on"}]}}, T0)
        self.h.handle("/photo", now_ts=1000, role="viewer")  # light already on
        self.h.handle("/photo", now_ts=1000)                 # owner photo never switches the light
        self.assertEqual(self.sent, [])

    def test_viewer_status_hides_auto_photo(self):
        self.assertIn("Auto photo", self.h.handle("/status")[0])
        self.assertNotIn("Auto photo", self.h.handle("/status", role="viewer")[0])

    def test_request_allow_and_welcome(self):
        vw = self.h.viewers
        n = vw.request({"id": 2000, "first_name": "Anna", "username": "anna", "type": "private"})
        self.assertEqual(n, 1)
        self.assertIsNone(vw.request({"id": 2000, "first_name": "Anna"}))   # asks once per run
        text, _ = self.h.handle("/allow1")
        self.assertIn("Anna (@anna) can now watch P1S", text)
        self.assertEqual(self.dm[-1][0], "2000")
        self.assertIn("view only", self.dm[-1][1])
        self.assertIn("Unknown request", self.h.handle("/allow1")[0])        # used up

    def test_owner_is_never_a_viewer(self):
        self.assertIsNone(self.h.viewers.request({"id": 1000, "first_name": "Me"}))

    def test_saved_and_reloaded(self):
        self.add_viewer("2000", "Anna")
        self.add_viewer("-1001234", None, title="Family", type="group")    # a group works too
        again = pw.Viewers("1000", self.path)
        self.assertEqual(again.ids(), ["2000", "-1001234"])
        self.assertIn("[group]", again.items[1]["name"])

    def test_list_and_remove(self):
        self.assertIn("No view-only users", self.h.handle("/viewers")[0])
        self.add_viewer("2000", "Anna")
        self.add_viewer("3000", "Ben")
        text = self.h.handle("/viewers")[0]
        self.assertIn("1. Anna – remove: /remove1", text)
        self.assertIn("2. Ben", text)
        text = self.h.handle("/remove1")[0]
        self.assertIn("Anna can no longer", text)
        self.assertEqual(self.dm[-1], ("2000", "🔒 Your access to P1S has been removed."))
        self.assertEqual(pw.Viewers("1000", self.path).ids(), ["3000"])
        self.assertIn("Unknown viewer", self.h.handle("/remove5")[0])
        # removed person may ask again
        self.assertIsNotNone(self.h.viewers.request({"id": 2000, "first_name": "Anna"}))

    def test_max_viewers(self):
        for i in range(pw.MAX_VIEWERS):
            self.add_viewer(str(5000 + i), f"U{i}")
        n = self.h.viewers.request({"id": 9999, "first_name": "Late"})
        self.assertIn("maximum", self.h.handle(f"/allow{n}")[0])
        self.assertEqual(len(self.h.viewers.items), pw.MAX_VIEWERS)

    def test_names_are_html_safe(self):
        n = self.h.viewers.request({"id": 2000, "first_name": "<b>x</b>"})
        self.assertNotIn("<b>x", self.h.viewers.pending[n]["name"])

    def test_latvian_viewer(self):
        self.st.L = pw.Lang("lv")
        self.assertIn("Tikai skatīšanās", self.h.handle("/gaisma", role="viewer")[0])
        self.add_viewer()
        self.assertIn("noņemt: /nonemt1", self.h.handle("/skatitaji")[0])

    def test_broken_file_is_ignored(self):
        self.path.write_text("not json")
        self.assertEqual(pw.Viewers("1000", self.path).ids(), [])


class TestConfig(unittest.TestCase):
    def test_legacy_format_is_converted(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text(json.dumps({"telegram_chat_id": "133", "p1s": {
                "ip": "192.168.1.83", "serial": "01P", "access_code": "abc", "telegram_token": "T"}}))
            cfg = pw.load_config(p)
            self.assertEqual(cfg["printer"]["ip"], "192.168.1.83")
            self.assertEqual(cfg["telegram"]["bot_token"], "T")
            self.assertEqual(cfg["telegram"]["chat_id"], "133")
            self.assertEqual(pw.config_problems(cfg), [])

    def test_missing_fields_reported(self):
        self.assertIn("printer.ip", pw.config_problems({}))


if __name__ == "__main__":
    unittest.main()

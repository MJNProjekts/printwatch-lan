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

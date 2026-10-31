#!/usr/bin/env python3
"""
PrintWatch LAN - Telegram notifications and remote control for Bambu Lab P1/A1 printers
in LAN-only mode. No Bambu cloud, no Home Assistant: your computer talks to the printer
on your local network and sends messages to your own Telegram bot.

Not affiliated with or endorsed by Bambu Lab.

Usage:
  python printwatch.py --setup     guided setup (printer + Telegram bot)
  python printwatch.py             run the monitor
  python printwatch.py --demo      print example messages without a printer
  python printwatch.py --version

License: MIT. See LICENSE.
"""

import json
import re
import socket
import ssl
import struct
import sys
import time
import warnings
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

__version__ = "0.3.0-beta"

# Harmless warnings on macOS system Python (LibreSSL) and paho-mqtt 2.x
warnings.filterwarnings("ignore", message=".*OpenSSL 1.1.1.*")
warnings.filterwarnings("ignore", message=".*Callback API version 1 is deprecated.*")

import requests  # noqa: E402

# Windows consoles may not support emoji: never crash on printing
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass

CONFIG_PATH = Path(__file__).with_name("config.json")
ACTIVE = {"PREPARE", "RUNNING", "PAUSE"}
CANCELLED = 0x0300400C   # Bambu print_error code for "print was cancelled" (stop from screen, app or bot)
DONE = {"IDLE", "FINISH", "FAILED"}
OFFLINE_ALERT_SEC = 5 * 60
STOP_CONFIRM_SEC = 60
LIGHT_SETTLE_SEC = 3   # wait after toggling the light before taking a photo
AUTO_PHOTO_LONG_MIN = 120   # prints expected to take longer get a photo every AUTO_PHOTO_EVERY_MIN
AUTO_PHOTO_EVERY_MIN = 15
AUTO_PHOTO_EVERY_PCT = 10   # shorter prints get a photo every N % of progress

# ---------------------------------------------------------------------------
# Texts (English default, Latvian optional)
# ---------------------------------------------------------------------------

TEXT = {
    "en": {
        "states": {"IDLE": "idle", "PREPARE": "preparing", "RUNNING": "printing", "PAUSE": "paused",
                   "FINISH": "finished", "FAILED": "failed / stopped", "SLICING": "slicing"},
        "tomorrow": " (tomorrow)", "in_days": " (+{d} d)",
        "layer": "layer {a}/{b}",
        "eta": "\nRemaining: {dur} → done ~{clock}{day}",
        "greet_active": "🖨 PrintWatch connected to {printer}.\nPrinting: {name} ({progress})",
        "greet_idle": "🖨 PrintWatch connected to {printer}. Printer: {state}",
        "started": "▶️ Print started: {name}",
        "layers": "\nLayers: {n}",
        "preparing_note": "\nPreparing (heating, calibration). Finish time follows with the first layer.",
        "eta_later_note": "\nFinish time follows with the first layer.",
        "resumed": "▶️ Print resumed: {name} ({progress})",
        "paused": "⏸ PAUSED: {name} ({progress})",
        "error_code": "\nError code: {code}",
        "hms": "\nHMS: {codes}",
        "pause_no_code": "\n(no error code, probably paused from the screen or by G-code)",
        "pause_by_bot": "\n(paused by your /pause command)",
        "failed": "❌ Print stopped / failed: {name} ({progress})",
        "cancelled": "⏹ Print cancelled: {name} ({progress})",
        "cancelled_by_bot": "\nStopped with your /confirmstop command.",
        "after": "\nAfter: {dur}",
        "finished": "✅ Print finished: {name}",
        "duration": "\nDuration: {dur}",
        "first_layer": "1️⃣ First layer done: {name}\nGood moment to check adhesion.",
        "error_running": "⚠️ Printer reports an error while printing: {code} ({name})",
        "back_online": "📡 {printer} is back online.",
        "offline": "📡 {printer} unreachable for {m} min during a print ({name}). Check the printer or Wi-Fi.",
        "not_connected": "🖨 Printer not connected yet. Check that it is on and on the same network.",
        "st_title": "🖨 <b>{printer}: {state}</b>",
        "st_conn_lost": " (⚠️ connection lost)",
        "st_file": "File: {name}",
        "st_progress": "Progress: {p}",
        "st_printing_for": "Printing for: {dur}",
        "st_nozzle": "Nozzle: {t:.0f}°C",
        "st_bed": "Bed: {t:.0f}°C",
        "st_target": " (target {t:.0f}°C)",
        "st_light": "Light: {v}",
        "on": "on", "off": "off",
        "photo_caption": "📷 {state}",
        "photo_dark": "\n(light is off, image may be dark: {cmd})",
        "photo_stale": "\n(⚠️ the camera returned only an older frame, the image may not be current)",
        "photo_fail": "📷 Could not get an image: {err}\nCheck that the printer camera is enabled "
                      "and this computer is on the same network.",
        "light_on": "💡 Light: turning on", "light_off": "💡 Light: turning off",
        "no_conn": "No connection to the printer.",
        "pause_no": "Can't pause: printer is {state}.",
        "pause_sent": "⏸ Pause command sent.",
        "resume_no": "Nothing to resume: printer is {state}.",
        "resume_sent": "▶️ Resume command sent.",
        "stop_no": "Nothing to stop: printer is {state}.",
        "stop_confirm": "⚠️ Really stop <b>{name}</b>? The print cannot be resumed.\n"
                        "Confirm with {cmd} within 60 seconds.",
        "stop_expired": "Confirmation expired. Send {cmd} first.",
        "stop_sent": "⏹ Stop command sent.",
        "help": ("<b>PrintWatch LAN commands</b>\n"
                 "/status – state, progress, finish time, temperatures\n"
                 "/photo – camera snapshot\n"
                 "/light – toggle the chamber light\n"
                 "/autophoto – automatic photos during prints on/off\n"
                 "/pause, /resume – pause and resume\n"
                 "/stop – stop the print (confirm with /confirmstop within 60 s)\n\n"
                 "If control commands have no effect, enable Developer mode in the printer's "
                 "LAN settings."),
        "menu": [("status", "Printer status"), ("photo", "Camera snapshot"),
                 ("light", "Toggle light"), ("pause", "Pause print"), ("resume", "Resume print"),
                 ("stop", "Stop print (asks to confirm)"),
                 ("autophoto", "Auto photos on/off"), ("help", "Commands")],
        "cmd_light": "/light", "cmd_stop": "/stop", "cmd_confirm": "/confirmstop",
        "cmd_auto": "/autophoto",
        "auto_prefix": "🔁 Auto photo · ",
        "st_auto": "Auto photo: {v}",
        "auto_time": "every {m} min", "auto_pct": "every {p}%",
        "auto_wait": "from the first layer", "auto_state_off": "off",
        "auto_on": "🔁 Auto photo on: every {m} min for prints over {h} h, every {p}% for shorter ones.",
        "auto_off": "🔁 Auto photo off. Turn it on again with {cmd}.",
    },
    "lv": {
        "states": {"IDLE": "gaida", "PREPARE": "sagatavo", "RUNNING": "drukā", "PAUSE": "pauzē",
                   "FINISH": "pabeigts", "FAILED": "kļūda / apturēts", "SLICING": "slicē"},
        "tomorrow": " (rīt)", "in_days": " (+{d} d.)",
        "layer": "slānis {a}/{b}",
        "eta": "\nAtlicis: {dur} → gatavs ~{clock}{day}",
        "greet_active": "🖨 PrintWatch pievienojies: {printer}.\nDrukā: {name} ({progress})",
        "greet_idle": "🖨 PrintWatch pievienojies: {printer}. Printeris: {state}",
        "started": "▶️ Druka sākta: {name}",
        "layers": "\nSlāņi: {n}",
        "preparing_note": "\nSagatavo (sildīšana, kalibrēšana). Beigu laiks būs ziņā par pirmo slāni.",
        "eta_later_note": "\nBeigu laiks būs ziņā par pirmo slāni.",
        "resumed": "▶️ Druka turpināta: {name} ({progress})",
        "paused": "⏸ PAUZE: {name} ({progress})",
        "error_code": "\nKļūdas kods: {code}",
        "hms": "\nHMS: {codes}",
        "pause_no_code": "\n(bez kļūdas koda, iespējams, pauze no ekrāna vai G-koda)",
        "pause_by_bot": "\n(pauze ar tavu /pauze komandu)",
        "failed": "❌ Druka apturēta / kļūda: {name} ({progress})",
        "cancelled": "⏹ Druka atcelta: {name} ({progress})",
        "cancelled_by_bot": "\nApturēta ar tavu /stopja komandu.",
        "after": "\nPēc: {dur}",
        "finished": "✅ Druka pabeigta: {name}",
        "duration": "\nIlgums: {dur}",
        "first_layer": "1️⃣ Pirmais slānis gatavs: {name}\nLabs brīdis pārbaudīt, vai viss pielīp.",
        "error_running": "⚠️ Printeris ziņo kļūdu drukas laikā: {code} ({name})",
        "back_online": "📡 {printer} atkal tīklā.",
        "offline": "📡 {printer} nav sasniedzams jau {m} min drukas laikā ({name}). "
                   "Pārbaudi printeri vai Wi-Fi.",
        "not_connected": "🖨 Printeris vēl nav pievienots. Pārbaudi, vai tas ieslēgts un tajā pašā tīklā.",
        "st_title": "🖨 <b>{printer}: {state}</b>",
        "st_conn_lost": " (⚠️ savienojums pārtrūcis)",
        "st_file": "Fails: {name}",
        "st_progress": "Progress: {p}",
        "st_printing_for": "Drukā jau: {dur}",
        "st_nozzle": "Sprausla: {t:.0f}°C",
        "st_bed": "Galds: {t:.0f}°C",
        "st_target": " (mērķis {t:.0f}°C)",
        "st_light": "Gaisma: {v}",
        "on": "ieslēgta", "off": "izslēgta",
        "photo_caption": "📷 {state}",
        "photo_dark": "\n(gaisma izslēgta, attēls var būt tumšs: {cmd})",
        "photo_stale": "\n(⚠️ kamera atdeva tikai vecāku kadru, attēls var nebūt aktuāls)",
        "photo_fail": "📷 Neizdevās iegūt attēlu: {err}\nPārbaudi, vai printera kamera ir ieslēgta "
                      "un dators ir tajā pašā tīklā.",
        "light_on": "💡 Gaisma: ieslēdzu", "light_off": "💡 Gaisma: izslēdzu",
        "no_conn": "Nav savienojuma ar printeri.",
        "pause_no": "Pauze nav iespējama: printeris {state}.",
        "pause_sent": "⏸ Pauzes komanda nosūtīta.",
        "resume_no": "Nav ko turpināt: printeris {state}.",
        "resume_sent": "▶️ Turpināšanas komanda nosūtīta.",
        "stop_no": "Nav ko apturēt: printeris {state}.",
        "stop_confirm": "⚠️ Tiešām apturēt <b>{name}</b>? Drukāšanu nevarēs atsākt.\n"
                        "Apstiprini ar {cmd} 60 sekunžu laikā.",
        "stop_expired": "Apstiprinājuma laiks beidzies. Vispirms sūti {cmd}.",
        "stop_sent": "⏹ Apturēšanas komanda nosūtīta.",
        "help": ("<b>PrintWatch LAN komandas</b>\n"
                 "/p1s – stāvoklis, progress, beigu laiks, temperatūras\n"
                 "/foto – kameras attēls\n"
                 "/gaisma – ieslēgt / izslēgt gaismu\n"
                 "/autofoto – automātiskie foto drukas laikā ieslēgt / izslēgt\n"
                 "/pauze, /turpinat – pauze un turpināšana\n"
                 "/stop – apturēt (jāapstiprina ar /stopja 60 s laikā)\n\n"
                 "Ja vadības komandas neiedarbojas, printera LAN iestatījumos ieslēdz Developer mode."),
        "menu": [("p1s", "Printera stāvoklis"), ("foto", "Kameras attēls"),
                 ("gaisma", "Ieslēgt / izslēgt gaismu"), ("pauze", "Pauzēt drukāšanu"),
                 ("turpinat", "Turpināt pēc pauzes"), ("stop", "Apturēt (ar apstiprinājumu)"),
                 ("autofoto", "Auto foto ieslēgt / izslēgt"), ("help", "Komandu saraksts")],
        "cmd_light": "/gaisma", "cmd_stop": "/stop", "cmd_confirm": "/stopja",
        "cmd_auto": "/autofoto",
        "auto_prefix": "🔁 Auto foto · ",
        "st_auto": "Auto foto: {v}",
        "auto_time": "ik {m} min", "auto_pct": "ik {p}%",
        "auto_wait": "no pirmā slāņa", "auto_state_off": "izslēgts",
        "auto_on": "🔁 Auto foto ieslēgts: ik {m} min drukām virs {h} h, ik {p}% īsākām.",
        "auto_off": "🔁 Auto foto izslēgts. Ieslēgt atkal: {cmd}.",
    },
}

# Both languages' command names always work
COMMAND_ALIASES = {
    "/status": "status", "/p1s": "status", "/start": "status",
    "/photo": "photo", "/foto": "photo",
    "/light": "light", "/gaisma": "light",
    "/pause": "pause", "/pauze": "pause",
    "/resume": "resume", "/turpinat": "resume", "/turpināt": "resume",
    "/stop": "stop",
    "/confirmstop": "confirmstop", "/stopja": "confirmstop",
    "/autophoto": "autophoto", "/autofoto": "autophoto",
    "/help": "help",
}


class Lang:
    def __init__(self, code="en"):
        self.code = code if code in TEXT else "en"
        self.t = TEXT[self.code]

    def __call__(self, key, **kw):
        return self.t[key].format(**kw) if kw else self.t[key]

    def state(self, s):
        return self.t["states"].get(s, s or "?")


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def fmt_minutes(m):
    m = int(m or 0)
    h, mm = divmod(m, 60)
    return f"{h} h {mm:02d} min" if h else f"{mm} min"


def fmt_error(code):
    """print_error as shown on the printer screen, e.g. 0700 8011."""
    try:
        h = f"{int(code):08X}"
        return f"{h[:4]} {h[4:]}"
    except (TypeError, ValueError):
        return str(code)


def fmt_hms(hms):
    out = []
    for e in hms or []:
        try:
            a, c = int(e.get("attr", 0)), int(e.get("code", 0))
            s = f"{a:08X}{c:08X}"
            out.append("_".join(s[i:i + 4] for i in range(0, 16, 4)))
        except (TypeError, ValueError, AttributeError):
            continue
    return out


def light_state(data):
    for node in data.get("lights_report") or []:
        if isinstance(node, dict) and node.get("node") == "chamber_light":
            return node.get("mode")
    return None


# ---------------------------------------------------------------------------
# Printer state machine (pure logic, testable without a printer)
# ---------------------------------------------------------------------------

def now_local():
    """Timezone-aware local time, so durations and finish times stay correct across DST changes."""
    return datetime.now().astimezone()


class PrinterState:
    def __init__(self, name="P1S", lang=None, first_layer=True, auto_photo=True):
        self.printer = name
        self.L = lang or Lang("en")
        self.first_layer = first_layer
        self.data = {}
        self.state = None
        self.started_at = None
        self.first_layer_sent = False
        self.last_error = 0
        self.greeted = False
        self.connected = False
        self.disconnected_at = None
        self.offline_alerted = False
        self.remote_pause_at = None   # set when PrintWatch itself sends a pause command
        self.remote_stop_at = None    # set when PrintWatch itself sends a stop command
        self.auto_photo = auto_photo
        self.photo_queue = deque()    # auto photo requests, sent by the main loop
        self.ap_mode = None           # "time" (long print) or "pct" (short print), set at the first layer
        self.ap_last_at = None
        self.ap_next_pct = 0

    def request_photo(self):
        if self.auto_photo:
            self.photo_queue.append(True)

    def _auto_photo_setup(self, now):
        """Choose the auto photo rhythm from the expected total print time (at the first layer
        the printer's estimate is current)."""
        pct = int(self.data.get("mc_percent") or 0)
        try:
            rem = int(self.data.get("mc_remaining_time") or 0)
        except (TypeError, ValueError):
            rem = 0
        total = rem * 100 / (100 - pct) if rem > 0 and pct < 100 else None
        self.ap_mode = "pct" if total is not None and total <= AUTO_PHOTO_LONG_MIN else "time"
        self.ap_last_at = now
        self.ap_next_pct = (pct // AUTO_PHOTO_EVERY_PCT + 1) * AUTO_PHOTO_EVERY_PCT

    def auto_photo_text(self):
        L = self.L
        if not self.auto_photo:
            return L("auto_state_off")
        if self.ap_mode == "time":
            return L("auto_time", m=AUTO_PHOTO_EVERY_MIN)
        if self.ap_mode == "pct":
            return L("auto_pct", p=AUTO_PHOTO_EVERY_PCT)
        return L("auto_wait")

    def job_name(self):
        n = self.data.get("subtask_name") or self.data.get("gcode_file") or "?"
        return n.rsplit("/", 1)[-1]

    def progress(self):
        d = self.data
        parts = []
        if d.get("mc_percent") is not None:
            parts.append(f"{d['mc_percent']}%")
        if d.get("total_layer_num"):
            parts.append(self.L("layer", a=d.get("layer_num", 0), b=d["total_layer_num"]))
        return ", ".join(parts)

    def eta(self, now):
        rem = self.data.get("mc_remaining_time")
        try:
            if rem in (None, "") or int(rem) <= 0:
                return ""
        except (TypeError, ValueError):
            return ""
        end = now + timedelta(minutes=int(rem))
        if end.tzinfo is not None:
            end = end.astimezone()   # re-apply local offset in case DST changes before the finish
        days = (end.date() - now.date()).days
        day = "" if days == 0 else self.L("tomorrow") if days == 1 else self.L("in_days", d=days)
        return self.L("eta", dur=fmt_minutes(rem), clock=f"{end:%H:%M}", day=day)

    def update(self, report, now=None):
        """Feed one MQTT report; returns a list of messages to send."""
        now = now or now_local()
        L = self.L
        msgs = []
        p = report.get("print") or {}
        if not p:
            return msgs
        self.data.update(p)
        new = self.data.get("gcode_state")
        if not new:
            return msgs
        old = self.state

        if not self.greeted:  # first report after start: short status only
            self.greeted = True
            self.state = new
            if new in ACTIVE:
                self.started_at = now
                self.first_layer_sent = int(self.data.get("layer_num") or 0) >= 2
                if self.first_layer_sent:
                    self._auto_photo_setup(now)
                msgs.append(L("greet_active", printer=self.printer, name=self.job_name(),
                              progress=self.progress()) + self.eta(now))
            else:
                msgs.append(L("greet_idle", printer=self.printer, state=L.state(new)))
            return msgs

        if new in ("PREPARE", "RUNNING") and (old in DONE or old is None):
            self.started_at = now
            self.first_layer_sent = False
            self.last_error = 0
            self.ap_mode = None
            # like the layer count, the remaining time may still be from the previous job:
            # use it only if it arrived in this same report
            eta = self.eta(now) if "mc_remaining_time" in p else ""
            if not eta:
                eta = L("preparing_note") if new == "PREPARE" else L("eta_later_note")
            # total_layer_num in self.data may still be from the previous job; trust it only
            # when it arrives in this same report (otherwise it is shown in /status later)
            layers = p.get("total_layer_num")
            msgs.append(L("started", name=self.job_name())
                        + (L("layers", n=layers) if layers else "") + eta)

        elif new == "RUNNING" and old == "PAUSE":
            msgs.append(L("resumed", name=self.job_name(), progress=self.progress()))
            self.ap_last_at = now

        elif new == "PAUSE" and old != "PAUSE":
            err = int(self.data.get("print_error") or 0)
            hms = fmt_hms(self.data.get("hms"))
            msg = L("paused", name=self.job_name(), progress=self.progress())
            if err:
                msg += L("error_code", code=fmt_error(err))
                self.last_error = err
            if hms:
                msg += L("hms", codes=", ".join(hms))
            by_bot = self.remote_pause_at and (now - self.remote_pause_at).total_seconds() < 60
            if by_bot and not err:
                msg += L("pause_by_bot")
            elif not err and not hms:
                msg += L("pause_no_code")
            self.remote_pause_at = None
            msgs.append(msg)
            self.request_photo()

        elif new == "FAILED" and old != "FAILED":
            err = int(self.data.get("print_error") or 0)
            by_bot = self.remote_stop_at and (now - self.remote_stop_at).total_seconds() < 60
            self.remote_stop_at = None
            if err == CANCELLED or (by_bot and not err):
                msg = L("cancelled", name=self.job_name(), progress=self.progress())
                if by_bot:
                    msg += L("cancelled_by_bot")
            else:
                msg = L("failed", name=self.job_name(), progress=self.progress())
                self.request_photo()
            if err and err != CANCELLED:
                msg += L("error_code", code=fmt_error(err))
            if self.started_at:
                msg += L("after", dur=fmt_minutes((now - self.started_at).total_seconds() / 60))
            msgs.append(msg)
            self.started_at = None
            self.ap_mode = None

        elif new == "FINISH" and old in ACTIVE:
            msg = L("finished", name=self.job_name())
            if self.started_at:
                msg += L("duration", dur=fmt_minutes((now - self.started_at).total_seconds() / 60))
            msgs.append(msg)
            self.started_at = None
            self.ap_mode = None
            self.request_photo()

        if (new == "RUNNING" and self.first_layer and not self.first_layer_sent
                and int(self.data.get("layer_num") or 0) >= 2):
            self.first_layer_sent = True
            msgs.append(L("first_layer", name=self.job_name()) + self.eta(now))

        # automatic photos: one at the first layer, then every N min (long print) or N % (short)
        if new == "RUNNING" and self.ap_mode is None and int(self.data.get("layer_num") or 0) >= 2:
            self._auto_photo_setup(now)
            self.request_photo()
        elif new == "RUNNING" and self.ap_mode == "pct":
            pct = int(self.data.get("mc_percent") or 0)
            if self.ap_next_pct <= pct < 100:
                self.ap_next_pct = (pct // AUTO_PHOTO_EVERY_PCT + 1) * AUTO_PHOTO_EVERY_PCT
                self.request_photo()

        if new == "RUNNING":
            err = int(self.data.get("print_error") or 0)
            if err and err != self.last_error and err != CANCELLED:
                self.last_error = err
                msgs.append(L("error_running", code=fmt_error(err), name=self.job_name()))

        self.state = new
        return msgs

    def status_text(self, now=None):
        now = now or now_local()
        L, d = self.L, self.data
        if not self.connected and not d:
            return L("not_connected")
        state = d.get("gcode_state", "?")
        lines = [L("st_title", printer=self.printer, state=L.state(state))
                 + ("" if self.connected else L("st_conn_lost"))]
        if state in ACTIVE:
            lines.append(L("st_file", name=self.job_name()))
            if self.progress():
                lines.append(L("st_progress", p=self.progress()))
            if self.eta(now):
                lines.append(self.eta(now).strip())
            if self.started_at:
                lines.append(L("st_printing_for",
                               dur=fmt_minutes((now - self.started_at).total_seconds() / 60)))
            lines.append(L("st_auto", v=self.auto_photo_text()))
        for key, cur, tgt in (("st_nozzle", "nozzle_temper", "nozzle_target_temper"),
                              ("st_bed", "bed_temper", "bed_target_temper")):
            if d.get(cur) is not None:
                line = L(key, t=float(d[cur]))
                if d.get(tgt) and state in ACTIVE:
                    line += L("st_target", t=float(d[tgt]))
                lines.append(line)
        light = light_state(d)
        if light:
            lines.append(L("st_light", v=L("on") if light == "on" else L("off")))
        err = int(d.get("print_error") or 0)
        if err:
            lines.append(L("error_code", code=fmt_error(err)).strip())
        return "\n".join(lines)

    def on_connect(self):
        msgs = [self.L("back_online", printer=self.printer)] if self.offline_alerted else []
        self.connected = True
        self.disconnected_at = None
        self.offline_alerted = False
        return msgs

    def on_disconnect(self, now=None):
        self.connected = False
        self.disconnected_at = now or now_local()

    def tick(self, now=None):
        now = now or now_local()
        if (self.auto_photo and self.connected and self.state == "RUNNING" and self.ap_mode == "time"
                and (now - self.ap_last_at).total_seconds() >= AUTO_PHOTO_EVERY_MIN * 60):
            self.ap_last_at = now
            self.request_photo()
        if (not self.connected and self.disconnected_at and not self.offline_alerted
                and self.state in ACTIVE
                and (now - self.disconnected_at).total_seconds() >= OFFLINE_ALERT_SEC):
            self.offline_alerted = True
            return [self.L("offline", printer=self.printer, m=OFFLINE_ALERT_SEC // 60,
                           name=self.job_name())]
        return []


# ---------------------------------------------------------------------------
# Printer commands and camera
# ---------------------------------------------------------------------------

def mqtt_command(kind):
    seq = str(int(time.time()))
    if kind in ("pause", "resume", "stop"):
        return {"print": {"sequence_id": seq, "command": kind, "param": ""}}
    if kind in ("light_on", "light_off"):
        return {"system": {"sequence_id": seq, "command": "ledctrl", "led_node": "chamber_light",
                           "led_mode": "on" if kind == "light_on" else "off",
                           "led_on_time": 500, "led_off_time": 500,
                           "loop_times": 0, "interval_time": 0}}
    raise ValueError(kind)


def grab_camera_jpeg(ip, access_code, timeout=12, settle=2.5, max_frames=10, with_info=False):
    """A fresh JPEG frame from a P1/A1 series camera over the local network (port 6000).

    The camera first replays its last buffered frame, which can be hours old (e.g. from the
    end of the last print). So we keep reading for `settle` seconds and return the newest frame.
    with_info=True returns (jpeg, fresh) where fresh=False means only an old frame was received."""
    auth = struct.pack("<IIII", 0x40, 0x3000, 0, 0)
    auth += b"bblp".ljust(32, b"\0") + access_code.encode().ljust(32, b"\0")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # printer uses a self-signed certificate

    def read_exact(sock, n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("camera closed the connection")
            buf += chunk
        return buf

    frames = []
    with socket.create_connection((ip, 6000), timeout=timeout) as raw:
        with ctx.wrap_socket(raw, server_hostname=ip) as sock:
            sock.sendall(auth)
            t_first = None
            while len(frames) < max_frames:
                try:
                    header = read_exact(sock, 16)
                    size = int.from_bytes(header[0:3], "little")
                    if not 0 < size < 5_000_000:
                        raise ValueError(f"unexpected frame size {size}")
                    frame = read_exact(sock, size)
                except (ConnectionError, socket.timeout, ssl.SSLError, OSError):
                    if frames:
                        break          # keep what we already have
                    raise
                if frame[:2] == b"\xff\xd8" and frame[-2:] == b"\xff\xd9":
                    frames.append(frame)
                if frames and t_first is None:
                    t_first = time.time()
                    sock.settimeout(4)  # later frames arrive about once a second
                if len(frames) >= 2 and time.time() - t_first >= settle:
                    break
    if not frames:
        raise ValueError("received data is not a JPEG image")
    fresh = len(frames) >= 2 and frames[-1] != frames[0]
    return (frames[-1], fresh) if with_info else frames[-1]


class CommandHandler:
    """Telegram command logic. handle() returns (text, jpeg_or_None)."""

    def __init__(self, state, publish, grab_photo):
        self.st = state
        self.publish = publish          # publish(dict) -> bool
        self.grab_photo = grab_photo    # grab_photo() -> bytes
        self.stop_pending_until = 0.0
        self.light_changed_at = 0.0
        self.sleep = time.sleep

    def handle(self, text, now_ts=None):
        now_ts = now_ts or time.time()
        L = self.st.L
        cmd = COMMAND_ALIASES.get(text.strip().split()[0].split("@")[0].lower(), "help")
        state = self.st.data.get("gcode_state")

        if cmd == "status":
            return self.st.status_text(), None
        if cmd == "photo":
            wait = self.light_changed_at + LIGHT_SETTLE_SEC - now_ts
            if wait > 0:
                self.sleep(wait)    # let the light really change before the snapshot
            try:
                res = self.grab_photo()
            except Exception as e:
                return L("photo_fail", err=e), None
            img, fresh = res if isinstance(res, tuple) else (res, True)
            cap = L("photo_caption", state=L.state(state))
            if state in ACTIVE:
                cap += f" · {self.st.job_name()} ({self.st.progress()})"
            if light_state(self.st.data) == "off":
                cap += L("photo_dark", cmd=L("cmd_light"))
            if not fresh:
                cap += L("photo_stale")
            return cap, img
        if cmd == "light":
            new = "light_off" if light_state(self.st.data) == "on" else "light_on"
            if not self.publish(mqtt_command(new)):
                return L("no_conn"), None
            self.light_changed_at = now_ts
            return L("light_on") if new == "light_on" else L("light_off"), None
        if cmd == "autophoto":
            self.st.auto_photo = not self.st.auto_photo
            if self.st.auto_photo:
                return L("auto_on", m=AUTO_PHOTO_EVERY_MIN, h=AUTO_PHOTO_LONG_MIN // 60,
                         p=AUTO_PHOTO_EVERY_PCT), None
            self.st.photo_queue.clear()
            return L("auto_off", cmd=L("cmd_auto")), None
        if cmd == "pause":
            if state != "RUNNING":
                return L("pause_no", state=L.state(state)), None
            if not self.publish(mqtt_command("pause")):
                return L("no_conn"), None
            self.st.remote_pause_at = now_local()
            return L("pause_sent"), None
        if cmd == "resume":
            if state != "PAUSE":
                return L("resume_no", state=L.state(state)), None
            return (L("resume_sent") if self.publish(mqtt_command("resume")) else L("no_conn")), None
        if cmd == "stop":
            if state not in ACTIVE:
                return L("stop_no", state=L.state(state)), None
            self.stop_pending_until = now_ts + STOP_CONFIRM_SEC
            return L("stop_confirm", name=self.st.job_name(), cmd=L("cmd_confirm")), None
        if cmd == "confirmstop":
            if now_ts > self.stop_pending_until:
                return L("stop_expired", cmd=L("cmd_stop")), None
            self.stop_pending_until = 0.0
            if not self.publish(mqtt_command("stop")):
                return L("no_conn"), None
            self.st.remote_stop_at = now_local()
            return L("stop_sent"), None
        return L("help"), None


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

class Telegram:
    def __init__(self, token, chat_id):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = str(chat_id)

    def send(self, text):
        try:
            r = requests.post(f"{self.base}/sendMessage",
                              data={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                              timeout=15)
            if not r.ok:
                print(f"Telegram error {r.status_code}: {r.text[:200]}")
            return r.ok
        except requests.RequestException as e:
            print(f"Telegram connection error: {e}")
            return False

    def send_photo(self, jpeg, caption=""):
        try:
            r = requests.post(f"{self.base}/sendPhoto",
                              data={"chat_id": self.chat_id, "caption": caption, "parse_mode": "HTML"},
                              files={"photo": ("printer.jpg", jpeg, "image/jpeg")}, timeout=30)
            return r.ok
        except requests.RequestException as e:
            print(f"Telegram photo error: {e}")
            return False

    def set_commands(self, menu):
        try:
            requests.post(f"{self.base}/setMyCommands",
                          json={"commands": [{"command": c, "description": d} for c, d in menu]},
                          timeout=15)
        except requests.RequestException:
            pass

    def get_updates(self, offset, timeout):
        params = {"timeout": int(timeout), "allowed_updates": '["message"]'}
        if offset is not None:
            params["offset"] = offset
        r = requests.get(f"{self.base}/getUpdates", params=params, timeout=timeout + 15)
        data = r.json()
        return data.get("result", []) if data.get("ok") else []

    def skip_old(self):
        try:
            upd = self.get_updates(-1, 0)
            return upd[-1]["update_id"] + 1 if upd else None
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_config(path=CONFIG_PATH):
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    # Backward compatibility with the early prototype format ({"p1s": {...}})
    if "p1s" in raw and "printer" not in raw:
        old = raw["p1s"]
        raw = {
            "language": raw.get("language", "lv"),
            "telegram": {"bot_token": old.get("telegram_token") or raw.get("telegram_token", ""),
                         "chat_id": str(raw.get("telegram_chat_id", ""))},
            "printer": {"name": "P1S", "ip": old.get("ip", ""), "serial": old.get("serial", ""),
                        "access_code": old.get("access_code", "")},
            "notifications": {"first_layer": old.get("first_layer", True)},
        }
    return raw


def save_config(cfg, path=CONFIG_PATH):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def config_problems(cfg):
    p, t = cfg.get("printer", {}), cfg.get("telegram", {})
    missing = [f"printer.{k}" for k in ("ip", "serial", "access_code") if not p.get(k)]
    missing += [f"telegram.{k}" for k in ("bot_token", "chat_id") if not t.get(k)]
    return missing


# ---------------------------------------------------------------------------
# Setup wizard
# ---------------------------------------------------------------------------

def setup(path=CONFIG_PATH):
    cfg = load_config(path)
    cfg.setdefault("printer", {})
    cfg.setdefault("telegram", {})
    cfg.setdefault("notifications", {"first_layer": True})
    pr, tg = cfg["printer"], cfg["telegram"]

    def ask(prompt, cur="", secret=False):
        shown = "****" if secret and cur else cur
        val = input(f"{prompt} [{shown}]: ").strip()
        return val or cur

    print(f"PrintWatch LAN {__version__} setup. Press Enter to keep the value in [brackets].\n")
    lang = ask("Language / Valoda (en/lv)", cfg.get("language", "en")).lower()
    cfg["language"] = "lv" if lang.startswith("l") else "en"

    print("\n1) PRINTER (LAN-only mode). Find these on the printer screen:")
    print("   IP: Settings > WLAN | Serial: Settings > Device | Access code: LAN Only Mode")
    pr["name"] = ask("Printer name shown in messages", pr.get("name", "P1S"))
    pr["ip"] = ask("Printer IP address", pr.get("ip", ""))
    pr["serial"] = ask("Printer serial number", pr.get("serial", "")).upper()
    pr["access_code"] = ask("Access code", pr.get("access_code", ""), secret=True)

    print("\n2) TELEGRAM BOT. In Telegram open @BotFather, send /newbot and copy the token.")
    tg["bot_token"] = ask("Bot token", tg.get("bot_token", ""), secret=True)
    if tg["bot_token"]:
        try:
            me = requests.get(f"https://api.telegram.org/bot{tg['bot_token']}/getMe", timeout=15).json()
            if me.get("ok"):
                print(f"   OK, bot found: @{me['result']['username']}")
                input(f"   Now open @{me['result']['username']} in Telegram, press START, "
                      "then press Enter here...")
                updates = Telegram(tg["bot_token"], "").get_updates(None, 0)
                chats = {str(u["message"]["chat"]["id"]):
                         u["message"]["chat"].get("first_name") or u["message"]["chat"].get("title", "")
                         for u in updates if "message" in u}
                if len(chats) == 1:
                    tg["chat_id"] = next(iter(chats))
                    print(f"   Chat found: {next(iter(chats.values()))} ({tg['chat_id']})")
                elif len(chats) > 1:
                    for cid, nm in chats.items():
                        print(f"   {cid}  {nm}")
            else:
                print("   WARNING: Telegram does not recognise this token.")
        except requests.RequestException as e:
            print(f"   Could not reach Telegram ({e}).")
    tg["chat_id"] = ask("Chat ID", str(tg.get("chat_id", "")))

    fl = ask("Message after the first layer? (y/n)",
             "y" if cfg["notifications"].get("first_layer", True) else "n").lower()
    cfg["notifications"]["first_layer"] = fl.startswith(("y", "j"))
    ap = ask("Automatic camera photos during prints? (y/n)",
             "y" if cfg["notifications"].get("auto_photo", True) else "n").lower()
    cfg["notifications"]["auto_photo"] = ap.startswith(("y", "j"))

    cfg.pop("p1s", None)
    save_config(cfg, path)
    print(f"\nSaved to {path.name}.")
    missing = config_problems(cfg)
    if missing:
        print("Still missing:", ", ".join(missing))
    else:
        print("Start the monitor with:  python printwatch.py")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run():
    import paho.mqtt.client as mqtt

    cfg = load_config()
    missing = config_problems(cfg)
    if missing:
        print("Configuration incomplete:", ", ".join(missing))
        print("Run:  python printwatch.py --setup")
        sys.exit(1)

    L = Lang(cfg.get("language", "en"))
    pr, tgc = cfg["printer"], cfg["telegram"]
    serial = pr["serial"]
    nots = cfg.get("notifications", {})
    st = PrinterState(pr.get("name", "P1S"), L, nots.get("first_layer", True), nots.get("auto_photo", True))
    bot = Telegram(tgc["bot_token"], tgc["chat_id"])

    def send(msgs):
        for m in msgs:
            print(f"{datetime.now():%H:%M:%S} → {m.splitlines()[0]}")
            bot.send(m)

    try:  # paho-mqtt 2.x
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=f"printwatch-{int(time.time())}")
    except AttributeError:  # paho-mqtt 1.x
        client = mqtt.Client(client_id=f"printwatch-{int(time.time())}")
    client.username_pw_set("bblp", pr["access_code"])
    client.tls_set(cert_reqs=ssl.CERT_NONE)  # printer uses a self-signed certificate
    client.tls_insecure_set(True)
    client.reconnect_delay_set(min_delay=5, max_delay=120)

    def on_connect(c, userdata, flags, rc):
        if rc != 0:
            print(f"Connection refused (rc={rc}). Check the access code and serial number.")
            return
        print(f"{datetime.now():%H:%M:%S} Connected to {st.printer} at {pr['ip']}")
        c.subscribe(f"device/{serial}/report")
        # P1 series only sends changes, so request the full state once
        c.publish(f"device/{serial}/request",
                  json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))
        send(st.on_connect())

    def on_disconnect(c, userdata, rc):
        print(f"{datetime.now():%H:%M:%S} Connection lost (rc={rc}), retrying...")
        st.on_disconnect()

    def on_message(c, userdata, msg):
        try:
            report = json.loads(msg.payload.decode("utf-8", errors="ignore"))
            send(st.update(report))
        except Exception as e:  # one odd message must never stop the monitor
            print("Error handling printer message:", e)

    def publish(payload):
        if not st.connected:
            return False
        return client.publish(f"device/{serial}/request", json.dumps(payload)).rc == 0

    handler = CommandHandler(st, publish,
                             lambda: grab_camera_jpeg(pr["ip"], pr["access_code"], with_info=True))

    def send_auto_photo():
        if not st.photo_queue:
            return
        st.photo_queue.clear()   # several requests at the same moment -> one photo
        try:
            reply, img = handler.handle("/photo")
        except Exception as e:
            reply, img = str(e), None
        if img:
            bot.send_photo(img, L("auto_prefix") + reply)
            print(f"{datetime.now():%H:%M:%S} → auto photo")
        else:
            print(f"{datetime.now():%H:%M:%S} auto photo failed: {reply.splitlines()[0] if reply else ''}")

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.connect_async(pr["ip"], 8883, keepalive=60)
    client.loop_start()

    bot.set_commands(L("menu"))
    offset = bot.skip_old()
    print(f"PrintWatch LAN {__version__} running. Stop with Ctrl + C")
    print(f"Auto photo: {'on' if st.auto_photo else 'off'} (change with /autophoto)")
    try:
        while True:
            try:
                updates = bot.get_updates(offset, 15)
            except Exception:
                updates = []
                time.sleep(5)
            for u in updates:
                offset = u["update_id"] + 1
                m = u.get("message") or {}
                text = m.get("text", "")
                if str(m.get("chat", {}).get("id")) != bot.chat_id or not text.startswith("/"):
                    continue  # ignore everyone else: this bot can control a printer
                try:
                    reply, img = handler.handle(text)
                except Exception as e:
                    reply, img = f"Error: {e}", None
                if img:
                    bot.send_photo(img, reply)
                else:
                    bot.send(reply)
                first = re.sub(r"<[^>]+>", "", reply.splitlines()[0]) if reply else ""
                print(f"{datetime.now():%H:%M:%S} command {text.split()[0]} → {first}")
            send(st.tick())
            send_auto_photo()
    except KeyboardInterrupt:
        client.loop_stop()
        client.disconnect()


def demo(lang="en"):
    """Simulates one print cycle and prints the messages (no printer, no Telegram)."""
    st = PrinterState("P1S", Lang(lang))
    t0 = datetime(2026, 10, 22, 21, 0).astimezone()
    seq = [
        (0, {"print": {"gcode_state": "IDLE"}}),
        (1, {"print": {"gcode_state": "PREPARE", "subtask_name": "bracket_PETG",
                       "total_layer_num": 225, "mc_remaining_time": 0, "layer_num": 0}}),
        (8, {"print": {"gcode_state": "RUNNING", "layer_num": 1, "mc_percent": 0,
                       "mc_remaining_time": 460}}),
        (12, {"print": {"layer_num": 2, "mc_percent": 1, "mc_remaining_time": 450}}),
        (200, {"print": {"gcode_state": "PAUSE", "print_error": 0x07008011, "mc_percent": 40,
                         "layer_num": 90}}),
        (215, {"print": {"gcode_state": "RUNNING", "print_error": 0}}),
        (470, {"print": {"gcode_state": "FINISH", "mc_percent": 100, "layer_num": 225}}),
    ]
    for minute, rep in seq:
        for m in st.update(rep, t0 + timedelta(minutes=minute)):
            print(f"--- {t0 + timedelta(minutes=minute):%H:%M}\n{m}")
        if st.photo_queue:
            st.photo_queue.clear()
            print("    [📷 auto photo]")


if __name__ == "__main__":
    if "--version" in sys.argv:
        print(__version__)
    elif "--demo" in sys.argv:
        demo("lv" if "lv" in sys.argv else "en")
    elif "--setup" in sys.argv:
        setup()
    else:
        run()

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
  python printwatch.py --files     list the printer's SD card (timelapse videos) over FTPS
  python printwatch.py --dump      save one full printer report to printer_dump.json
                                   (private fields removed), e.g. for bug reports
  python printwatch.py --version

License: MIT. See LICENSE.
"""

import ftplib
import io
import shutil
import subprocess
import tempfile
import html
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

__version__ = "0.4.0-beta"

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
VIEWERS_PATH = Path(__file__).with_name("viewers.json")   # view-only users, managed from Telegram
ACTIVE = {"PREPARE", "RUNNING", "PAUSE"}
CANCELLED = 0x0300400C   # Bambu print_error code for "print was cancelled" (stop from screen, app or bot)
DONE = {"IDLE", "FINISH", "FAILED"}
OFFLINE_ALERT_SEC = 5 * 60
STOP_CONFIRM_SEC = 60
LIGHT_SETTLE_SEC = 3   # wait after toggling the light before taking a photo
VIEWER_LIGHT_COOLDOWN = 120   # a viewer's /photo may switch the light on at most once per 2 min
AUTO_PHOTO_LONG_MIN = 120   # prints expected to take longer get a photo every AUTO_PHOTO_EVERY_MIN
AUTO_PHOTO_EVERY_MIN = 15
AUTO_PHOTO_EVERY_PCT = 10   # shorter prints get a photo every N % of progress
SPEED_NAMES = {1: "Silent", 2: "Standard", 3: "Sport", 4: "Ludicrous"}   # Bambu speed levels (spd_lvl)
SPEED_PCT = {1: 50, 2: 100, 3: 124, 4: 166}
TG_UPLOAD_LIMIT = 50 * 1024 * 1024   # Telegram bots can send files up to 50 MB
CONVERT_MAX = 300 * 1024 * 1024      # with MP4 conversion, bigger AVIs are fine (MP4 is ~3x smaller)
VIDEO_EXT = (".avi", ".mp4", ".mov")
MAX_VIEWERS = 10
MAX_PENDING_REQUESTS = 20
# what a view-only user may do: look, never control the printer
VIEWER_COMMANDS = {"status", "photo", "timelapse", "timelapses", "help"}
SPEED_WORDS = {"silent": 1, "klusais": 1, "standard": 2, "standarts": 2, "sport": 3, "sports": 3,
               "ludicrous": 4, "trakais": 4}

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
                 "/speed – print speed (/speed 1–4)\n"
                 "/timelapse – last timelapse video, /timelapses – list\n"
                 "/pause, /resume – pause and resume\n"
                 "/stop – stop the print (confirm with /confirmstop within 60 s)\n"
                 "/viewers – view-only users\n\n"
                 "If control commands have no effect, enable Developer mode in the printer's "
                 "LAN settings."),
        "menu": [("status", "Printer status"), ("photo", "Camera snapshot"),
                 ("light", "Toggle light"), ("pause", "Pause print"), ("resume", "Resume print"),
                 ("stop", "Stop print (asks to confirm)"),
                 ("speed", "Print speed"), ("timelapse", "Last timelapse video"),
                 ("autophoto", "Auto photos on/off"), ("viewers", "View-only users"),
                 ("help", "Commands")],
        "cmd_light": "/light", "cmd_stop": "/stop", "cmd_confirm": "/confirmstop",
        "cmd_auto": "/autophoto", "cmd_speed": "/speed", "cmd_tl": "/timelapse", "cmd_tls": "/timelapses",
        "tl_busy": "The newest timelapse may still be recording: it is completed when the print ends. "
                   "Older videos can be downloaded now: {cmd}",
        "tl_none": "🎞 No timelapse videos on the SD card. Tick Timelapse when sending a print "
                   "in Bambu Studio (an SD card is required).",
        "tl_list_title": "🎞 Timelapse videos on the SD card (newest first):",
        "tl_bad": "Unknown video number. See the list: {cmd}",
        "tl_too_big": "🎞 {name} is {mb}, over Telegram's 50 MB limit for bots. Copy it from the SD card instead.",
        "tl_downloading": "🎞 Downloading {name} ({mb}) from the printer...",
        "tl_caption": "🎞 Timelapse: {name}",
        "tl_caption_n": "🎞 Timelapse #{n} from the SD card",
        "tl_newest": "newest",
        "tl_clock_note": "(Video file names use the printer's own clock, which is not synced in LAN-only mode.)",
        "tl_fail": "🎞 Could not read the SD card: {err}\nCheck that an SD card is inserted. "
                   "Some firmware needs Developer mode for file access in LAN-only mode.",
        "st_speed": "Speed: {v}", "st_filament": "Filament: {v}", "st_wifi": "Wi-Fi: {v}",
        "st_timelapse": "Timelapse: {v}", "st_no_sd": " (⚠️ no SD card)",
        "st_ams": "AMS: {v}", "ams_none": "not connected", "ams_one": "connected",
        "ams_many": "connected ({n} units)",
        "speed_now": "⚡ Speed: {v}", "speed_unknown": "unknown",
        "speed_help": "\nChange during a print:\n{cmd}1 – Silent (50%)\n{cmd}2 – Standard (100%)\n"
                      "{cmd}3 – Sport (124%)\n{cmd}4 – Ludicrous (166%)",
        "speed_no": "Speed can be changed only during a print (printer is {state}).",
        "speed_bad": "Unknown speed. Use {cmd}1, {cmd}2, {cmd}3 or {cmd}4.",
        "speed_sent": "⚡ Speed command sent: {v}. The printer confirms in a moment.",
        "speed_changed": "⚡ Speed changed: {v}",
        "auto_prefix": "🔁 Auto photo · ",
        "st_auto": "Auto photo: {v}",
        "auto_time": "every {m} min", "auto_pct": "every {p}%",
        "auto_wait": "from the first layer", "auto_state_off": "off",
        "auto_on": "🔁 Auto photo on: every {m} min for prints over {h} h, every {p}% for shorter ones.",
        "auto_off": "🔁 Auto photo off. Turn it on again with {cmd}.",
        "photo_dark_viewer": "\n(light is off, image may be dark)",
        "photo_lit_viewer": "\n(light switched on for this photo, then off again)",
        "photo_light_wait": "\n(next photo with light in {s} s)",
        "viewer_help": ("<b>PrintWatch LAN (view only)</b>\n"
                        "/status – state, progress, finish time\n"
                        "/photo – camera snapshot\n"
                        "/timelapse – last timelapse video, /timelapses – list\n\n"
                        "You also get a message when a print starts, finishes or needs attention."),
        "viewer_menu": [("status", "Printer status"), ("photo", "Camera snapshot"),
                        ("timelapse", "Last timelapse video"), ("timelapses", "Timelapse list"),
                        ("help", "Commands")],
        "viewer_denied": "🔒 View-only access: only the printer owner can use this command.",
        "req_user": "🔒 This is a private PrintWatch bot. Your request has been sent to the owner.",
        "req_admin": "👤 Access request: {who}\nAllow view only (status, photo, timelapse, messages): {cmd}{n}",
        "allow_bad": "Unknown request number. New requests arrive here when someone presses Start in the bot.",
        "allow_full": "Already {n} viewers, that is the maximum. Remove one first: {cmd}",
        "allow_done": "✅ {who} can now watch {printer} (view only).",
        "viewer_welcome": "👋 You can now watch {printer} (view only).\n\n",
        "remove_bad": "Unknown viewer number. See the list: {cmd}",
        "remove_done": "🗑 {who} can no longer watch {printer}.",
        "viewer_removed": "🔒 Your access to {printer} has been removed.",
        "viewers_title": "👥 <b>View-only users</b> (status, photo, timelapse, messages):",
        "viewers_line": "{n}. {who} – remove: {cmd}{n}",
        "viewers_none": ("👥 No view-only users yet.\nTo add one: the person opens the bot in Telegram and "
                         "presses Start. You then get a request here with a tap-to-allow command."),
        "cmd_allow": "/allow", "cmd_remove": "/remove", "cmd_viewers": "/viewers",
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
                 "/atrums – drukas ātrums (/atrums 1–4)\n"
                 "/video – pēdējais timelapse video, /videos – saraksts\n"
                 "/pauze, /turpinat – pauze un turpināšana\n"
                 "/stop – apturēt (jāapstiprina ar /stopja 60 s laikā)\n"
                 "/skatitaji – lietotāji ar skatīšanās tiesībām\n\n"
                 "Ja vadības komandas neiedarbojas, printera LAN iestatījumos ieslēdz Developer mode."),
        "menu": [("p1s", "Printera stāvoklis"), ("foto", "Kameras attēls"),
                 ("gaisma", "Ieslēgt / izslēgt gaismu"), ("pauze", "Pauzēt drukāšanu"),
                 ("turpinat", "Turpināt pēc pauzes"), ("stop", "Apturēt (ar apstiprinājumu)"),
                 ("atrums", "Drukas ātrums"), ("video", "Pēdējais timelapse video"), ("autofoto", "Auto foto ieslēgt / izslēgt"),
                 ("skatitaji", "Skatītāji (tikai skatīties)"), ("help", "Komandu saraksts")],
        "cmd_light": "/gaisma", "cmd_stop": "/stop", "cmd_confirm": "/stopja",
        "cmd_auto": "/autofoto", "cmd_speed": "/atrums", "cmd_tl": "/video", "cmd_tls": "/videos",
        "tl_busy": "Jaunākais timelapse, iespējams, vēl tiek ierakstīts: tas tiek pabeigts drukas beigās. "
                   "Vecākos video var lejupielādēt jau tagad: {cmd}",
        "tl_none": "🎞 SD kartē nav timelapse video. Bambu Studio, sūtot druku, atzīmē Timelapse "
                   "(vajadzīga SD karte).",
        "tl_list_title": "🎞 Timelapse video SD kartē (jaunākie pirmie):",
        "tl_bad": "Nezināms video numurs. Saraksts: {cmd}",
        "tl_too_big": "🎞 {name} ir {mb}, vairāk par Telegram 50 MB limitu botiem. Kopē to no SD kartes.",
        "tl_downloading": "🎞 Lejupielādēju {name} ({mb}) no printera...",
        "tl_caption": "🎞 Timelapse: {name}",
        "tl_caption_n": "🎞 Timelapse #{n} no SD kartes",
        "tl_newest": "jaunākais",
        "tl_clock_note": "(Video nosaukumos ir printera iekšējais laiks, kas LAN-only režīmā netiek sinhronizēts.)",
        "tl_fail": "🎞 Neizdevās nolasīt SD karti: {err}\nPārbaudi, vai SD karte ir ielikta. "
                   "Dažām firmware versijām LAN režīmā failu piekļuvei vajag Developer mode.",
        "st_speed": "Ātrums: {v}", "st_filament": "Filaments: {v}", "st_wifi": "Wi-Fi: {v}",
        "st_timelapse": "Timelapse: {v}", "st_no_sd": " (⚠️ nav SD kartes)",
        "st_ams": "AMS: {v}", "ams_none": "nav pieslēgts", "ams_one": "pieslēgts",
        "ams_many": "pieslēgti {n} bloki",
        "speed_now": "⚡ Ātrums: {v}", "speed_unknown": "nav zināms",
        "speed_help": "\nMainīt drukas laikā:\n{cmd}1 – Silent (50%)\n{cmd}2 – Standard (100%)\n"
                      "{cmd}3 – Sport (124%)\n{cmd}4 – Ludicrous (166%)",
        "speed_no": "Ātrumu var mainīt tikai drukas laikā (printeris {state}).",
        "speed_bad": "Nezināms ātrums. Izmanto {cmd}1, {cmd}2, {cmd}3 vai {cmd}4.",
        "speed_sent": "⚡ Ātruma komanda nosūtīta: {v}. Printeris apstiprinās pēc brīža.",
        "speed_changed": "⚡ Ātrums mainīts: {v}",
        "auto_prefix": "🔁 Auto foto · ",
        "st_auto": "Auto foto: {v}",
        "auto_time": "ik {m} min", "auto_pct": "ik {p}%",
        "auto_wait": "no pirmā slāņa", "auto_state_off": "izslēgts",
        "auto_on": "🔁 Auto foto ieslēgts: ik {m} min drukām virs {h} h, ik {p}% īsākām.",
        "auto_off": "🔁 Auto foto izslēgts. Ieslēgt atkal: {cmd}.",
        "photo_dark_viewer": "\n(gaisma izslēgta, attēls var būt tumšs)",
        "photo_lit_viewer": "\n(gaisma uz brīdi ieslēgta šim foto un atkal izslēgta)",
        "photo_light_wait": "\n(nākamais foto ar gaismu pēc {s} s)",
        "viewer_help": ("<b>PrintWatch LAN (tikai skatīšanās)</b>\n"
                        "/p1s – stāvoklis, progress, beigu laiks\n"
                        "/foto – kameras attēls\n"
                        "/video – pēdējais timelapse video, /videos – saraksts\n\n"
                        "Ziņu saņemsi arī, kad druka sākas, beidzas vai vajag uzmanību."),
        "viewer_menu": [("p1s", "Printera stāvoklis"), ("foto", "Kameras attēls"),
                        ("video", "Pēdējais timelapse video"), ("videos", "Timelapse saraksts"),
                        ("help", "Komandu saraksts")],
        "viewer_denied": "🔒 Tikai skatīšanās tiesības: šo komandu var lietot tikai printera īpašnieks.",
        "req_user": "🔒 Šis ir privāts PrintWatch bots. Tavs pieprasījums nosūtīts īpašniekam.",
        "req_admin": "👤 Piekļuves pieprasījums: {who}\nAtļaut tikai skatīties (stāvoklis, foto, timelapse, ziņas): {cmd}{n}",
        "allow_bad": "Nezināms pieprasījuma numurs. Pieprasījumi atnāk šeit, kad kāds botā nospiež Start.",
        "allow_full": "Jau ir {n} skatītāji, tas ir maksimums. Vispirms noņem kādu: {cmd}",
        "allow_done": "✅ {who} tagad var skatīties {printer} (tikai skatīšanās).",
        "viewer_welcome": "👋 Tagad vari skatīties {printer} (tikai skatīšanās).\n\n",
        "remove_bad": "Nezināms skatītāja numurs. Saraksts: {cmd}",
        "remove_done": "🗑 {who} vairs nevar skatīties {printer}.",
        "viewer_removed": "🔒 Tava piekļuve {printer} ir noņemta.",
        "viewers_title": "👥 <b>Skatītāji</b> (stāvoklis, foto, timelapse, ziņas):",
        "viewers_line": "{n}. {who} – noņemt: {cmd}{n}",
        "viewers_none": ("👥 Skatītāju vēl nav.\nKā pievienot: cilvēks Telegram atver botu un nospiež Start. "
                         "Tad šeit atnāks pieprasījums ar komandu, ko vienkārši pieskarties."),
        "cmd_allow": "/atlaut", "cmd_remove": "/nonemt", "cmd_viewers": "/skatitaji",
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
    "/speed": "speed", "/atrums": "speed", "/ātrums": "speed",
    "/timelapse": "timelapse", "/video": "timelapse",
    "/timelapses": "timelapses", "/videos": "timelapses",
    "/viewers": "viewers", "/skatitaji": "viewers", "/skatītāji": "viewers",
    "/allow": "allow", "/atlaut": "allow", "/atļaut": "allow",
    "/remove": "remove", "/nonemt": "remove", "/noņemt": "remove",
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


def speed_label(lvl):
    return f"{SPEED_NAMES[lvl]} ({SPEED_PCT[lvl]}%)"


def parse_speed(arg):
    """'3', 'sport', '124', '124%' -> 3; None if unknown."""
    a = str(arg).strip().lower().rstrip("%")
    if a in SPEED_WORDS:
        return SPEED_WORDS[a]
    try:
        n = int(a)
    except ValueError:
        return None
    if n in SPEED_NAMES:
        return n
    for lvl, pct in SPEED_PCT.items():
        if n == pct:
            return lvl
    return None


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
        self.print_had_timelapse = False   # timelapse was on during the last print (seen by PrintWatch)
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

    def speed_text(self):
        try:
            lvl = int(self.data.get("spd_lvl"))
        except (TypeError, ValueError):
            return None
        return speed_label(lvl) if lvl in SPEED_NAMES else None

    def ams_count(self):
        """Number of connected AMS units, or None if the printer has not reported it."""
        ams = self.data.get("ams")
        if not isinstance(ams, dict):
            return None
        bits = ams.get("ams_exist_bits")
        if bits not in (None, ""):
            try:
                return bin(int(str(bits), 16)).count("1")
            except ValueError:
                pass
        units = ams.get("ams")
        return len(units) if isinstance(units, list) else None

    def filament_text(self):
        tray_now = (self.data.get("ams") or {}).get("tray_now")
        vt = self.data.get("vt_tray")
        if tray_now in (None, "254") and isinstance(vt, dict) and vt.get("tray_type"):
            return vt["tray_type"]   # external spool (no AMS in use)
        return None

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
        old_spd = self.data.get("spd_lvl")
        self.data.update(p)
        new = self.data.get("gcode_state")
        if not new:
            return msgs
        old = self.state

        if not self.greeted:  # first report after start: short status only
            self.greeted = True
            self.state = new
            if new in ACTIVE:
                self.started_at = None   # real start time unknown after a restart mid-print
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
            self.print_had_timelapse = False
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

        ipcam = self.data.get("ipcam")
        if new in ACTIVE and isinstance(ipcam, dict) and ipcam.get("timelapse") == "enable":
            self.print_had_timelapse = True

        if ("spd_lvl" in p and old_spd is not None and str(p["spd_lvl"]) != str(old_spd)
                and new in ACTIVE and old in ACTIVE and self.speed_text()):
            msgs.append(L("speed_changed", v=self.speed_text()))

        self.state = new
        return msgs

    def status_text(self, now=None, viewer=False):
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
            if self.speed_text():
                lines.append(L("st_speed", v=self.speed_text()))
            if not viewer:   # auto photos go to the owner only
                lines.append(L("st_auto", v=self.auto_photo_text()))
        for key, cur, tgt in (("st_nozzle", "nozzle_temper", "nozzle_target_temper"),
                              ("st_bed", "bed_temper", "bed_target_temper")):
            if d.get(cur) is not None:
                line = L(key, t=float(d[cur]))
                if d.get(tgt) and state in ACTIVE:
                    line += L("st_target", t=float(d[tgt]))
                lines.append(line)
        tl = (d.get("ipcam") or {}).get("timelapse") if isinstance(d.get("ipcam"), dict) else None
        if tl in ("enable", "disable"):
            line = L("st_timelapse", v=L("on") if tl == "enable" else L("off"))
            if tl == "enable" and d.get("sdcard") is False:
                line += L("st_no_sd")
            lines.append(line)
        n_ams = self.ams_count()
        if n_ams is not None:
            v = L("ams_none") if n_ams == 0 else L("ams_one") if n_ams == 1 else L("ams_many", n=n_ams)
            lines.append(L("st_ams", v=v))
        if self.filament_text():
            lines.append(L("st_filament", v=self.filament_text()))
        if d.get("wifi_signal"):
            lines.append(L("st_wifi", v=d["wifi_signal"]))
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

def mqtt_command(kind, param=None):
    seq = str(int(time.time()))
    if kind == "speed":
        return {"print": {"sequence_id": seq, "command": "print_speed", "param": str(int(param))}}
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


def find_ffmpeg():
    """Path to ffmpeg: from the system, or from the optional imageio-ffmpeg package."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def avi_to_mp4(data, ffmpeg, timeout=300):
    """Bambu P1/A1 timelapses are MJPEG .avi, which macOS and Telegram can't play.
    Returns H.264 .mp4 bytes, or None if conversion fails."""
    with tempfile.TemporaryDirectory() as d:
        src, dst = Path(d) / "in.avi", Path(d) / "out.mp4"
        src.write_bytes(data)
        try:
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(dst)],
                           check=True, timeout=timeout, capture_output=True)
            out = dst.read_bytes()
        except (OSError, subprocess.SubprocessError):
            return None
    return out or None


def fmt_mb(size):
    return f"{size / 1048576:.1f} MB" if size else "? MB"


def pick_videos(names):
    """Video file names, newest first (Bambu names them video_YYYY-MM-DD_HH-MM-SS)."""
    out = {str(n).rsplit("/", 1)[-1] for n in names}
    return sorted((n for n in out if n.lower().endswith(VIDEO_EXT)), reverse=True)


class ImplicitFTPS(ftplib.FTP_TLS):
    """FTP over implicit TLS (port 990), as used by Bambu Lab printers in LAN mode.
    The data connection reuses the control connection's TLS session, which the printer requires."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sock = None

    @property
    def sock(self):
        return self._sock

    @sock.setter
    def sock(self, value):
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value)
        self._sock = value

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
        return conn, size


class PrinterFiles:
    """Read-only access to the printer's SD card over FTPS (user bblp, password = access code)."""

    def __init__(self, ip, access_code, timeout=30):
        self.ip, self.code, self.timeout = ip, access_code, timeout

    def _open(self):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE   # printer uses a self-signed certificate
        ftp = ImplicitFTPS(context=ctx, timeout=self.timeout)
        ftp.connect(self.ip, 990)
        ftp.login("bblp", self.code)
        ftp.prot_p()
        ftp.voidcmd("TYPE I")
        return ftp

    @staticmethod
    def _names(ftp, path):
        try:
            names = ftp.nlst(path)
        except ftplib.error_perm:
            names = []
        if not names:                      # some servers answer only LIST
            lines = []
            try:
                ftp.retrlines(f"LIST {path}", lines.append)
            except ftplib.error_perm:
                return []
            names = [ln.split(None, 8)[-1] for ln in lines if ln.strip() and len(ln.split()) >= 9]
        return names

    @staticmethod
    def _size(ftp, path):
        try:
            ftp.voidcmd("TYPE I")   # listing switches to ASCII, where servers refuse SIZE
            return ftp.size(path)
        except ftplib.all_errors:
            return None

    def list_videos(self):
        """[(name, size_bytes_or_None)], newest first."""
        ftp = self._open()
        try:
            items = [(n, self._size(ftp, f"/timelapse/{n}"))
                     for n in pick_videos(self._names(ftp, "/timelapse"))]
            return [(n, sz) for n, sz in items if sz != 0]   # skip empty files (interrupted prints)
        finally:
            try:
                ftp.quit()
            except ftplib.all_errors:
                pass

    def get_video(self, name):
        ftp = self._open()
        buf = io.BytesIO()
        try:
            ftp.retrbinary(f"RETR /timelapse/{name}", buf.write)
        finally:
            try:
                ftp.quit()
            except ftplib.all_errors:
                pass
        return buf.getvalue()

    def root(self):
        ftp = self._open()
        try:
            return sorted(n.rsplit("/", 1)[-1] for n in self._names(ftp, "/"))
        finally:
            try:
                ftp.quit()
            except ftplib.all_errors:
                pass


def chat_label(chat):
    """Readable, HTML-safe name for a Telegram chat: 'Anna (@anna)' or a group title."""
    name = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("title") or "?"
    if chat.get("username"):
        name += f" (@{chat['username']})"
    if chat.get("type") in ("group", "supergroup"):
        name += " [group]"
    return html.escape(name)


class Viewers:
    """View-only Telegram chats: they get the automatic messages and may use /status, /photo and
    /timelapse, but can never control the printer. The owner (config chat_id) adds them from
    Telegram: the person presses Start in the bot, the owner taps /allowN.
    Stored in viewers.json next to the script, so config.json is never rewritten."""

    def __init__(self, admin_id, path=None):
        self.admin = str(admin_id)
        self.path = path
        self.items = []          # [{"id": "123", "name": "Anna (@anna)"}]
        self.pending = {}        # request number -> {"id", "name"}
        self.requested = set()   # chat ids that already asked during this run (no spam to the owner)
        self.next_n = 1
        self.load()

    def load(self):
        if not self.path or not Path(self.path).exists():
            return
        try:
            raw = json.loads(Path(self.path).read_text(encoding="utf-8"))
            self.items = [{"id": str(v["id"]), "name": str(v.get("name", ""))}
                          for v in raw.get("viewers", []) if str(v.get("id", "")) not in ("", self.admin)]
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            print(f"Could not read {Path(self.path).name}: {e}")

    def save(self):
        if self.path:
            Path(self.path).write_text(json.dumps({"viewers": self.items}, indent=2, ensure_ascii=False),
                                       encoding="utf-8")

    def ids(self):
        return [v["id"] for v in self.items]

    def role(self, chat_id):
        cid = str(chat_id)
        if cid == self.admin:
            return "admin"
        return "viewer" if cid in self.ids() else None

    def request(self, chat):
        """An unknown chat wrote to the bot. Returns the message for the owner, or None
        if this chat already asked during this run (or too many requests are open)."""
        cid = str(chat.get("id", ""))
        if not cid or cid == self.admin or cid in self.ids() or cid in self.requested:
            return None
        if len(self.pending) >= MAX_PENDING_REQUESTS:
            return None
        self.requested.add(cid)
        n = self.next_n
        self.next_n += 1
        self.pending[n] = {"id": cid, "name": chat_label(chat)}
        return n

    def allow(self, n):
        """Returns the added viewer, 'full', or None for an unknown request number."""
        v = self.pending.get(n)
        if not v:
            return None
        if v["id"] not in self.ids() and len(self.items) >= MAX_VIEWERS:
            return "full"
        self.pending.pop(n)
        if v["id"] not in self.ids():
            self.items.append(v)
            self.save()
        return v

    def remove(self, n):
        if not 1 <= n <= len(self.items):
            return None
        v = self.items.pop(n - 1)
        self.requested.discard(v["id"])   # may ask again later
        self.save()
        return v


class CommandHandler:
    """Telegram command logic. handle() returns (text, attachment): attachment is None,
    JPEG bytes (photo) or {"document": bytes, "filename": str} (video file)."""

    def __init__(self, state, publish, grab_photo, files=None):
        self.st = state
        self.publish = publish          # publish(dict) -> bool
        self.grab_photo = grab_photo    # grab_photo() -> bytes
        self.files = files              # PrinterFiles-like: list_videos(), get_video(name)
        self.notify = lambda text: None # sends an interim message (e.g. "downloading...")
        self.viewers = None             # Viewers, or None when view-only users are not used
        self.send_to = lambda chat_id, text: None   # message to another chat (viewer welcome)
        self.convert = None             # convert(avi_bytes) -> mp4 bytes or None
        self.stop_pending_until = 0.0
        self.light_changed_at = 0.0
        self.viewer_light = False       # viewer /photo may switch the light on briefly (enabled in run())
        self.viewer_light_at = -VIEWER_LIGHT_COOLDOWN
        self.sleep = time.sleep

    def timelapse(self, text, cmd, state):
        L = self.st.L
        if self.files is None:
            return L("tl_fail", err="no access"), None
        parts = text.split()
        arg = "list" if cmd == "timelapses" else (parts[1] if len(parts) > 1 else "1")
        try:
            items = self.files.list_videos()
        except Exception as e:
            return L("tl_fail", err=e), None
        if not items:
            return L("tl_none"), None
        if arg == "list":
            lines = [L("tl_list_title")]
            for i, (n, sz) in enumerate(items[:5], 1):
                label = f"{L('tl_newest')} · {fmt_mb(sz)}" if i == 1 else fmt_mb(sz)
                lines.append(f"{L('cmd_tl')}{i} – {label}")
            lines.append(L("tl_clock_note"))
            return "\n".join(lines), None
        try:
            idx = int(arg)
        except ValueError:
            idx = 0
        if not 1 <= idx <= len(items):
            return L("tl_bad", cmd=L("cmd_tls")), None
        if idx == 1 and state in ACTIVE:   # the newest video may be the one being recorded now
            return L("tl_busy", cmd=L("cmd_tls")), None
        name, size = items[idx - 1]
        limit = CONVERT_MAX if self.convert else TG_UPLOAD_LIMIT
        if size and size > limit:
            return L("tl_too_big", name=name, mb=fmt_mb(size)), None
        self.notify(L("tl_downloading", name=name, mb=fmt_mb(size)))
        try:
            data = self.files.get_video(name)
        except Exception as e:
            return L("tl_fail", err=e), None
        if not data:
            return L("tl_fail", err="empty file"), None
        job = self.st.job_name()
        if idx == 1 and job != "?" and self.st.print_had_timelapse:
            caption = L("tl_caption", name=job)
        else:
            caption = L("tl_caption_n", n=idx)
        if self.convert and name.lower().endswith(".avi"):
            mp4 = self.convert(data)
            if mp4 and len(mp4) <= TG_UPLOAD_LIMIT:
                return caption, {"video": mp4, "filename": name[:-4] + ".mp4"}
        if len(data) > TG_UPLOAD_LIMIT:
            return L("tl_too_big", name=name, mb=fmt_mb(len(data))), None
        return caption, {"document": data, "filename": name}

    def manage_viewers(self, cmd, text):
        L = self.st.L
        vw = self.viewers
        if vw is None:
            return L("help")
        m = re.search(r"(\d+)", text.split()[0] + " " + " ".join(text.split()[1:2]))
        n = int(m.group(1)) if m else 0
        if cmd == "allow":
            v = vw.allow(n)
            if v is None:
                return L("allow_bad")
            if v == "full":
                return L("allow_full", n=MAX_VIEWERS, cmd=L("cmd_viewers"))
            self.send_to(v["id"], L("viewer_welcome", printer=self.st.printer) + L("viewer_help"))
            return L("allow_done", who=v["name"], printer=self.st.printer)
        if cmd == "remove":
            v = vw.remove(n)
            if v is None:
                return L("remove_bad", cmd=L("cmd_viewers"))
            self.send_to(v["id"], L("viewer_removed", printer=self.st.printer))
            return L("remove_done", who=v["name"], printer=self.st.printer)
        if not vw.items:
            return L("viewers_none")
        lines = [L("viewers_title")]
        for i, v in enumerate(vw.items, 1):
            lines.append(L("viewers_line", n=i, who=v["name"], cmd=L("cmd_remove")))
        return "\n".join(lines)

    def handle(self, text, now_ts=None, role="admin"):
        """role: "admin" (config chat_id, full control) or "viewer" (look only)."""
        now_ts = now_ts or time.time()
        L = self.st.L
        # "/speed3" (tappable in Telegram) is the same as "/speed 3"
        m = re.match(r"^(/(?:speed|atrums|ātrums))([1-4])(@\S+)?(\s|$)", text.strip(), re.IGNORECASE)
        if m:
            text = f"{m.group(1)} {m.group(2)}"
        m = re.match(r"^(/(?:timelapse|video))(\d+)(@\S+)?(\s|$)", text.strip(), re.IGNORECASE)
        if m:
            text = f"{m.group(1)} {m.group(2)}"
        m = re.match(r"^(/(?:allow|atlaut|atļaut|remove|nonemt|noņemt))(\d+)(@\S+)?(\s|$)", text.strip(),
                     re.IGNORECASE)
        if m:
            text = f"{m.group(1)} {m.group(2)}"
        word = text.strip().split()[0].split("@")[0].lower().rstrip(".,!?;:")   # "/viewers," -> "/viewers"
        cmd = COMMAND_ALIASES.get(word, "help")
        state = self.st.data.get("gcode_state")
        viewer = role != "admin"

        if viewer:   # never let a view-only user reach a control command
            if cmd == "help":
                return L("viewer_help"), None
            if cmd not in VIEWER_COMMANDS:
                return L("viewer_denied"), None
        if cmd in ("viewers", "allow", "remove"):
            return self.manage_viewers(cmd, text), None

        if cmd == "status":
            return self.st.status_text(viewer=viewer), None
        if cmd == "photo":
            # A viewer can't use /light, so a viewer's photo may switch the light on for the snapshot
            # and off again. Rate-limited, because it is the one thing a viewer can do to the printer.
            lit, light_wait = False, 0
            if viewer and self.viewer_light and light_state(self.st.data) == "off":
                left = self.viewer_light_at + VIEWER_LIGHT_COOLDOWN - now_ts
                if left > 0:
                    light_wait = int(left) + 1
                elif self.publish(mqtt_command("light_on")):
                    lit = True
                    self.viewer_light_at = self.light_changed_at = now_ts
                    print(f"{datetime.now():%H:%M:%S} viewer photo: light on for the snapshot")
            wait = self.light_changed_at + LIGHT_SETTLE_SEC - now_ts
            if wait > 0:
                self.sleep(wait)    # let the light really change before the snapshot
            try:
                res = self.grab_photo()
            except Exception as e:
                return L("photo_fail", err=e), None
            finally:
                if lit:
                    self.publish(mqtt_command("light_off"))   # always restore the previous state
                    self.light_changed_at = now_ts + LIGHT_SETTLE_SEC
            img, fresh = res if isinstance(res, tuple) else (res, True)
            cap = L("photo_caption", state=L.state(state))
            if state in ACTIVE:
                cap += f" · {self.st.job_name()} ({self.st.progress()})"
            if lit:
                cap += L("photo_lit_viewer")
            elif light_state(self.st.data) == "off":
                cap += L("photo_dark_viewer") if viewer else L("photo_dark", cmd=L("cmd_light"))
                if light_wait:
                    cap += L("photo_light_wait", s=light_wait)
            if not fresh:
                cap += L("photo_stale")
            return cap, img
        if cmd == "light":
            new = "light_off" if light_state(self.st.data) == "on" else "light_on"
            if not self.publish(mqtt_command(new)):
                return L("no_conn"), None
            self.light_changed_at = now_ts
            return L("light_on") if new == "light_on" else L("light_off"), None
        if cmd in ("timelapse", "timelapses"):
            return self.timelapse(text, cmd, state)
        if cmd == "speed":
            parts = text.split()
            if len(parts) < 2:
                cur = self.st.speed_text() or L("speed_unknown")
                return L("speed_now", v=cur) + L("speed_help", cmd=L("cmd_speed")), None
            lvl = parse_speed(parts[1])
            if lvl is None:
                return L("speed_bad", cmd=L("cmd_speed")), None
            if state not in ACTIVE:
                return L("speed_no", state=L.state(state)), None
            if not self.publish(mqtt_command("speed", lvl)):
                return L("no_conn"), None
            return L("speed_sent", v=speed_label(lvl)), None
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

    def send(self, text, chat_id=None):
        try:
            r = requests.post(f"{self.base}/sendMessage",
                              data={"chat_id": chat_id or self.chat_id, "text": text, "parse_mode": "HTML"},
                              timeout=15)
            if not r.ok:
                print(f"Telegram error {r.status_code}: {r.text[:200]}")
            return r.ok
        except requests.RequestException as e:
            print(f"Telegram connection error: {e}")
            return False

    def send_photo(self, jpeg, caption="", chat_id=None):
        try:
            r = requests.post(f"{self.base}/sendPhoto",
                              data={"chat_id": chat_id or self.chat_id, "caption": caption, "parse_mode": "HTML"},
                              files={"photo": ("printer.jpg", jpeg, "image/jpeg")}, timeout=30)
            return r.ok
        except requests.RequestException as e:
            print(f"Telegram photo error: {e}")
            return False

    def send_video(self, data, filename, caption="", chat_id=None):
        try:
            r = requests.post(f"{self.base}/sendVideo",
                              data={"chat_id": chat_id or self.chat_id, "caption": caption, "parse_mode": "HTML",
                                    "supports_streaming": "true"},
                              files={"video": (filename, data, "video/mp4")}, timeout=300)
            if not r.ok:
                print(f"Telegram error {r.status_code}: {r.text[:200]}")
            return r.ok
        except requests.RequestException as e:
            print(f"Telegram video error: {e}")
            return False

    def send_document(self, data, filename, caption="", chat_id=None):
        try:
            r = requests.post(f"{self.base}/sendDocument",
                              data={"chat_id": chat_id or self.chat_id, "caption": caption, "parse_mode": "HTML"},
                              files={"document": (filename, data, "application/octet-stream")}, timeout=300)
            if not r.ok:
                print(f"Telegram error {r.status_code}: {r.text[:200]}")
            return r.ok
        except requests.RequestException as e:
            print(f"Telegram file error: {e}")
            return False

    def set_commands(self, menu, chat_id=None):
        """Command menu; with chat_id only for that chat (the owner gets the full menu)."""
        body = {"commands": [{"command": c, "description": d} for c, d in menu]}
        if chat_id:
            body["scope"] = {"type": "chat", "chat_id": chat_id}
        try:
            requests.post(f"{self.base}/setMyCommands", json=body, timeout=15)
        except requests.RequestException:
            pass

    def username(self):
        try:
            r = requests.get(f"{self.base}/getMe", timeout=15).json()
            return r["result"].get("username", "") if r.get("ok") else ""
        except (requests.RequestException, ValueError, KeyError):
            return ""

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
    viewers = Viewers(tgc["chat_id"], VIEWERS_PATH)
    allow_requests = tgc.get("viewer_requests", True)
    viewer_light = tgc.get("viewer_light_photo", True)

    def send(msgs, owner_only=False):
        """Automatic messages go to the owner and to all view-only users."""
        for m in msgs:
            print(f"{datetime.now():%H:%M:%S} → {m.splitlines()[0]}")
            bot.send(m)
            if not owner_only:
                for vid in viewers.ids():
                    bot.send(m, vid)

    def update(report):
        greeted = st.greeted
        msgs = st.update(report)
        if not greeted and st.greeted and msgs:   # "PrintWatch connected": owner only, no restart spam
            send(msgs[:1], owner_only=True)
            msgs = msgs[1:]
        send(msgs)

    client = make_mqtt_client(pr)
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
            update(report)
        except Exception as e:  # one odd message must never stop the monitor
            print("Error handling printer message:", e)

    def publish(payload):
        if not st.connected:
            return False
        return client.publish(f"device/{serial}/request", json.dumps(payload)).rc == 0

    handler = CommandHandler(st, publish,
                             lambda: grab_camera_jpeg(pr["ip"], pr["access_code"], with_info=True),
                             PrinterFiles(pr["ip"], pr["access_code"]))
    handler.notify = bot.send
    handler.viewers = viewers
    handler.viewer_light = viewer_light
    handler.send_to = lambda chat_id, text: bot.send(text, chat_id)
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        handler.convert = lambda data: avi_to_mp4(data, ffmpeg)

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

    bot.set_commands(L("viewer_menu"))                # everyone else: view-only menu
    bot.set_commands(L("menu"), tgc["chat_id"])       # the owner: full menu
    me = bot.username().lower()
    offset = bot.skip_old()
    print(f"PrintWatch LAN {__version__} running. Stop with Ctrl + C")
    print(f"View-only users: {len(viewers.items)} (manage with /viewers)")
    print(f"Auto photo: {'on' if st.auto_photo else 'off'} (change with /autophoto)")
    print("Timelapse videos: sent as MP4" if ffmpeg else
          "Timelapse videos: sent as .avi (for playable MP4:  pip install imageio-ffmpeg)")
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
                chat = m.get("chat", {})
                cid = str(chat.get("id", ""))
                if not text.startswith("/"):
                    continue
                target = text.split()[0].partition("@")[2].lower()
                if target and me and target != me:
                    continue  # a command for another bot in the same group
                role = viewers.role(cid)
                if role is None:
                    # strangers never get printer data; the owner may allow view-only access
                    n = viewers.request(chat) if allow_requests else None
                    if n:
                        bot.send(L("req_user"), cid)
                        bot.send(L("req_admin", who=viewers.pending[n]["name"], cmd=L("cmd_allow"), n=n))
                        print(f"{datetime.now():%H:%M:%S} access request #{n} from a new chat")
                    continue
                handler.notify = lambda t, c=cid: bot.send(t, c)
                try:
                    reply, img = handler.handle(text, role=role)
                except Exception as e:
                    reply, img = f"Error: {e}", None
                if isinstance(img, dict) and "video" in img:
                    if not bot.send_video(img["video"], img["filename"], reply, cid):
                        bot.send_document(img["video"], img["filename"], reply, cid)
                elif isinstance(img, dict):
                    bot.send_document(img["document"], img["filename"], reply, cid)
                elif img:
                    bot.send_photo(img, reply, cid)
                else:
                    bot.send(reply, cid)
                first = re.sub(r"<[^>]+>", "", reply.splitlines()[0]) if reply else ""
                who = "" if role == "admin" else " (viewer)"
                print(f"{datetime.now():%H:%M:%S} command {text.split()[0]}{who} → {first}")
            send(st.tick())
            send_auto_photo()
    except KeyboardInterrupt:
        client.loop_stop()
        client.disconnect()


def make_mqtt_client(pr):
    import paho.mqtt.client as mqtt
    try:  # paho-mqtt 2.x
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=f"printwatch-{int(time.time())}")
    except AttributeError:  # paho-mqtt 1.x
        client = mqtt.Client(client_id=f"printwatch-{int(time.time())}")
    client.username_pw_set("bblp", pr["access_code"])
    client.tls_set(cert_reqs=ssl.CERT_NONE)  # printer uses a self-signed certificate
    client.tls_insecure_set(True)
    return client


# ---------------------------------------------------------------------------
# --dump: one full printer report, for bug reports and new features
# ---------------------------------------------------------------------------

PRIVATE_KEYS = {"ip", "mask", "gateway", "net", "sn", "serial", "dev_id", "ssid", "rtsp_url",
                "tag_uid", "tray_uuid"}


def redact(obj, secrets=()):
    """Copy of a printer report with private fields (network, serial numbers) removed."""
    secrets = [str(x) for x in secrets if x]
    if isinstance(obj, dict):
        return {k: "<removed>" if str(k).lower() in PRIVATE_KEYS else redact(v, secrets)
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v, secrets) for v in obj]
    if isinstance(obj, str):
        for x in secrets:
            obj = obj.replace(x, "<removed>")
    return obj


def dump(path=None, max_wait=20):
    cfg = load_config()
    missing = [m for m in config_problems(cfg) if m.startswith("printer.")]
    if missing:
        print("Configuration incomplete:", ", ".join(missing))
        print("Run:  python printwatch.py --setup")
        sys.exit(1)
    pr = cfg["printer"]
    serial = pr["serial"]
    out = Path(path) if path else CONFIG_PATH.with_name("printer_dump.json")
    got = {}

    def on_connect(c, userdata, flags, rc):
        if rc != 0:
            print(f"Connection refused (rc={rc}). Check the access code and serial number.")
            return
        c.subscribe(f"device/{serial}/report")
        c.publish(f"device/{serial}/request",
                  json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))
        c.publish(f"device/{serial}/request",
                  json.dumps({"info": {"sequence_id": "0", "command": "get_version"}}))

    def on_message(c, userdata, msg):
        try:
            report = json.loads(msg.payload.decode("utf-8", errors="ignore"))
        except ValueError:
            return
        for k, v in report.items():
            if isinstance(v, dict) and isinstance(got.get(k), dict):
                got[k].update(v)
            else:
                got[k] = v

    client = make_mqtt_client(pr)
    client.on_connect = on_connect
    client.on_message = on_message
    print(f"Connecting to the printer and reading its full report (up to {max_wait} s)...")
    try:
        client.connect(pr["ip"], 8883, keepalive=30)
    except OSError as e:
        print(f"Could not reach the printer: {e}")
        sys.exit(1)
    client.loop_start()
    t0 = time.time()
    while time.time() - t0 < max_wait:
        if "gcode_state" in got.get("print", {}) and "info" in got:
            break
        time.sleep(0.5)
    time.sleep(2)   # a moment for late parts of the report
    client.loop_stop()
    client.disconnect()

    if not got.get("print"):
        print("No report received. Is the printer on and in LAN-only mode?")
        sys.exit(1)
    data = {"printwatch_version": __version__,
            "created": now_local().isoformat(timespec="seconds"),
            "note": "PrintWatch LAN --dump. Private fields are replaced with <removed>."}
    data.update(redact(got, [serial, pr["ip"], pr.get("access_code")]))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
    keys = sorted(got["print"])
    print(f"Saved {out.name}: {len(keys)} fields in the printer report.")
    print("Fields:", ", ".join(keys))
    print("Private fields were removed, but please look through the file before sharing it.")


def files_cli():
    cfg = load_config()
    missing = [m for m in config_problems(cfg) if m.startswith("printer.")]
    if missing:
        print("Configuration incomplete:", ", ".join(missing))
        sys.exit(1)
    pr = cfg["printer"]
    pf = PrinterFiles(pr["ip"], pr["access_code"])
    print("Connecting to the printer's SD card over FTPS (port 990)...")
    try:
        root = pf.root()
        videos = pf.list_videos()
    except Exception as e:
        print(f"Could not read the SD card: {e}")
        print("Check that an SD card is inserted. Some firmware needs Developer mode for file access.")
        sys.exit(1)
    print("SD card:", ", ".join(root) if root else "(empty)")
    if videos:
        print(f"Timelapse videos ({len(videos)}, newest first):")
        for n, sz in videos[:10]:
            print(f"  {n}  {fmt_mb(sz)}")
    else:
        print("No timelapse videos. Tick Timelapse when sending a print in Bambu Studio.")


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
    elif "--files" in sys.argv:
        files_cli()
    elif "--dump" in sys.argv:
        dump()
    elif "--setup" in sys.argv:
        setup()
    else:
        run()

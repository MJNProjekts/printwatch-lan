# Changelog

## 0.3.1-beta (2026-11-01)

### Added
- **Timelapse videos from the SD card in LAN-only mode**, which Bambu Studio and Handy can't show:
  - `/timelapse` (`/video`) sends the newest video, `/timelapses` (`/videos`) lists the last 5
    (tap `/timelapse2` etc.). Files are read over FTPS (port 990).
  - Bambu's MJPEG `.avi` (which macOS and Telegram can't play) is converted to MP4 and sent as a
    playable video, about 3x smaller. Needs ffmpeg (`imageio-ffmpeg`, now in `requirements.txt`);
    without it the `.avi` is sent as a file. Telegram allows bots files up to 50 MB.
  - During a print only the newest video (possibly still recording) is held back.
  - The printer's clock is not synced in LAN-only mode, so file name dates are not shown; the newest
    video is labelled with the print name.
- `/speed` (`/atrums`) shows the print speed and changes it during a print:
  `/speed1` Silent (50%), `/speed2` Standard (100%), `/speed3` Sport (124%), `/speed4` Ludicrous (166%).
  `/speed 3`, `/speed sport` and `/speed 124` work too.
- A message when the speed changes during a print (from the bot or the printer screen).
- `/status` shows speed, timelapse on/off (with a warning if there is no SD card), whether an AMS
  is connected, filament type (external spool) and Wi-Fi signal.
- `python printwatch.py --files` lists the SD card and timelapse videos in the terminal.
- `python printwatch.py --dump` saves one full printer report to `printer_dump.json` with private
  fields (IP, network, serial numbers) removed, for bug reports and new features.
- README: "Help wanted" section for AMS owners and other printer models.

### Fixed
- After restarting the monitor in the middle of a print, `/status` showed "Printing for" and the
  finish message showed "Duration" counted from the restart. They are now left out when the real
  start time is unknown.

## 0.3.0-beta (2026-10-31)

### Added
- **Automatic camera photos during a print**, so problems like spaghetti are noticed in time:
  - one photo when the first layer is done
  - prints expected to take over 2 h: a photo every 15 minutes
  - shorter prints: a photo every 10 % of progress
  - a photo when the print pauses, fails or finishes
- `/autophoto` (`/autofoto`) turns automatic photos on or off without restarting.
- `/status` shows the current auto photo rhythm.
- Setup asks whether to enable automatic photos (`notifications.auto_photo`, default on).
- The terminal log shows each command's reply (e.g. whether auto photo is now on or off).

## 0.2.3-beta (2026-10-29)

### Fixed
- "Print started" could show the remaining time of the **previous** (failed or stopped) print,
  e.g. "2 h 30 min" for a 21-minute job. The finish time now comes with the first-layer message
  when the start report has no fresh estimate.

## 0.2.2-beta (2026-10-25)

### Fixed
- "Print started" message could show the layer count of the **previous** print (the printer sends
  the new count a moment later). The count is now shown only when it is current; `/status` always has it.
- A pause made with `/pause` was described as "probably paused from the screen or by G-code".
  It now says the pause came from your `/pause` command.
- Stopping a print (`/stop` + `/confirmstop`, or from the printer screen) was reported as an error
  (code 0300 400C) in two messages. It is now one "⏹ Print cancelled" message.
- Print durations and finish times are now correct when a print runs across a daylight-saving change.

## 0.2.1-beta (2026-10-24)
- `/photo` could show an old frame: the camera first replays its last buffered frame (it can be hours old, e.g. from the end of the last print). PrintWatch now reads the camera for ~2.5 s and sends the newest frame, and warns if only an old frame was received
- `/photo` right after `/light` waits a few seconds so the light has really changed

## 0.2.0-beta (2026-10-24)
First public beta.
- Automatic Telegram messages: print started, first layer done, paused (with error code), resumed, failed, finished, printer offline during a print
- Commands: `/status`, `/photo`, `/light`, `/pause`, `/resume`, `/stop` + `/confirmstop` (Latvian aliases included)
- English and Latvian messages
- Guided setup with bot token check and automatic chat ID detection
- Bot answers only the configured chat
- Unit tests for the printer logic

## 0.1 (private prototype)
- Notifications only, tested on a Bambu Lab P1S in LAN-only mode

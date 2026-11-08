# PrintWatch LAN

**Telegram notifications, camera snapshots and remote control for Bambu Lab P1/A1 printers in LAN-only mode.**
No Bambu cloud, no Home Assistant, no port forwarding. Your computer talks to the printer on your
local network and sends messages to *your own* Telegram bot.

> **Status: public beta (v0.4.0-beta).** Tested on a Bambu Lab **P1S** in LAN-only mode.
> A1 / A1 mini / P1P use the same protocol and should work, but are untested. X1 series camera is not supported yet.
> Reports from other models are very welcome.

*Not affiliated with or endorsed by Bambu Lab. "Bambu Lab" is a trademark of its owner.*

🇱🇻 [Latviski](README.lv.md)

---

## Why

In LAN-only mode the Bambu Handy app can't reach your printer, so you lose phone notifications and
remote camera. PrintWatch LAN brings them back while keeping the printer off the internet:

```
Printer (Wi-Fi, LAN only) ──MQTT/TLS──▶ your computer ──HTTPS──▶ Telegram ──▶ your phone
```

Only your computer needs internet access. The printer never talks to any cloud.

## Features

**Automatic messages**

| | Event |
|---|---|
| ▶️ | Print started (file name, layer count) |
| 1️⃣ | First layer done, with expected finish time (optional) |
| ⏸ | Paused, with the printer's error code (e.g. filament runout) |
| ▶️ | Resumed |
| ❌ | Failed / stopped, with error code |
| ✅ | Finished, with total duration |
| 📷 | Automatic photos: at the first layer, then every 15 min (prints over 2 h) or every 10 % (shorter prints), and on pause, failure and finish |
| 📡 | Printer unreachable for 5 min *during a print* |

**Timelapse in LAN-only mode:** Bambu Studio can't browse the SD card in LAN-only mode, but PrintWatch can. Tick *Timelapse* when sending a print (SD card required), then send `/timelapse` after the print. Bambu records MJPEG `.avi`, which macOS and Telegram can't play, so PrintWatch converts it to MP4 (installed with `requirements.txt` via `imageio-ffmpeg`). `python printwatch.py --files` lists the card in the terminal.

**Commands** (English and Latvian names both work)

| Command | Latvian | What it does |
|---|---|---|
| `/status` | `/p1s` | State, progress, layer, finish time, temperatures |
| `/photo` | `/foto` | Camera snapshot |
| `/light` | `/gaisma` | Toggle the chamber light |
| `/autophoto` | `/autofoto` | Automatic photos on/off |
| `/timelapse` | `/video` | Newest timelapse video from the SD card (`/timelapses` lists the last 5) |
| `/speed` | `/atrums` | Show print speed; `/speed 1`–`4` changes it (Silent / Standard / Sport / Ludicrous) |
| `/pause` | `/pauze` | Pause the print |
| `/resume` | `/turpinat` | Resume after pause |
| `/stop` | `/stop` | Stop the print, **requires** `/confirmstop` (`/stopja`) within 60 s |
| `/viewers` | `/skatitaji` | View-only users: list and remove |
| `/help` | `/help` | Command list |

Example:

```
🖨 P1S: printing
File: bracket_PETG
Progress: 29%, layer 4/25
Remaining: 42 min → done ~18:05
Speed: Standard (100%)
Nozzle: 250°C (target 250°C)
Bed: 70°C (target 70°C)
Filament: PETG
Wi-Fi: -57dBm
Light: on
```

## View-only users (family, friends, a group)

Let other people follow your prints without giving them control. A view-only user:

- gets the automatic messages (started, first layer, paused, failed, finished, offline), **but not the automatic photos**
- can use `/status`, `/photo`, `/timelapse` and `/timelapses`
- can **never** use `/light`, `/pause`, `/resume`, `/stop`, `/speed`, `/autophoto` or `/viewers`

How to add someone (no IDs to type, nothing to edit):

1. The person opens your bot in Telegram and presses **Start**. They get "This is a private bot, your request has been sent".
2. You get `👤 Access request: Anna (@anna)` with a command like `/allow1`. Tap it.
3. Anna gets a welcome message with her commands.

`/viewers` lists everyone with a tap-to-remove command (`/remove1`). Up to 10 viewers.
A **Telegram group** works too: add the bot to the group, send `/start@YourBot` there and allow the request;
everyone in the group can then look. Viewers are stored in `viewers.json` next to the script (not in
`config.json`, listed in `.gitignore`). To turn off access requests completely, set
`"viewer_requests": false` in the `telegram` section of `config.json`.

A viewer can't switch the light, so when it is off, a viewer's `/photo` switches it on for the snapshot and off again (at most once per 2 minutes). This is the only thing a viewer can do to the printer. To turn it off: `"viewer_light_photo": false` in the `telegram` section.

## Requirements

- Bambu Lab P1S / P1P / A1 / A1 mini in **LAN-only mode**
- A computer that stays on, **on the same network** as the printer (Mac, Windows, Linux, Raspberry Pi)
- Python 3.9 or newer
- A Telegram account

## Installation

```bash
git clone https://github.com/MJNProjekts/printwatch-lan.git
cd printwatch-lan
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Setup (about 5 minutes)

1. **Create a Telegram bot:** in Telegram open **@BotFather** → `/newbot` → choose a name →
   copy the **token**. Keep it private.
2. **Find the printer details** on the printer screen:
   - **IP address:** Settings → WLAN
   - **Serial number:** Settings → Device
   - **Access code:** Settings → LAN Only Mode
3. Run the guided setup:
   ```bash
   python printwatch.py --setup
   ```
   It asks for the values above, checks the bot token, and detects your chat ID automatically
   after you press **Start** in your bot.

Settings are stored in `config.json` next to the script (see `config.example.json`).

## Running

```bash
python printwatch.py
```

You should receive `🖨 PrintWatch connected to P1S. Printer: idle` in Telegram. Send `/status`.

**Keep the computer awake:**
- **macOS:** `caffeinate -is python printwatch.py` (lid open, on power)
- **Windows:** Settings → System → Power → Sleep: *Never* (when plugged in)
- **Linux / Raspberry Pi:** run it as a systemd service:

```ini
# /etc/systemd/system/printwatch.service
[Unit]
Description=PrintWatch LAN
After=network-online.target

[Service]
WorkingDirectory=/home/pi/printwatch-lan
ExecStart=/home/pi/printwatch-lan/venv/bin/python printwatch.py
Restart=always
User=pi

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now printwatch
```

Restarting the monitor in the middle of a print is safe: it reports the current state without duplicate messages.

**Try it without a printer:** `python printwatch.py --demo` prints example messages.

## Security

- Only **your own chat ID** can control the printer. View-only users you allowed can look, never control. Everyone else gets no printer data at all; their first message only sends you an access request.
- `/stop` always requires a second confirmation.
- `config.json` contains your access code and bot token **in plain text**. It is listed in `.gitignore`; never share or commit it.
- The printer uses a self-signed certificate, so TLS certificate verification is disabled for the
  printer connection only. This is standard for local Bambu Lab connections and is limited to your LAN.
- Nothing is sent anywhere except to the Telegram Bot API.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Connection refused (rc=5)` | Wrong access code or serial number |
| Nothing after "running" | Computer can't reach the printer IP: same network? IP changed? Consider a DHCP reservation |
| Connected, but no Telegram message | Wrong token or chat ID, or you haven't pressed **Start** in the bot |
| `/photo` fails | Check that the printer camera is enabled; only P1/A1 series are supported |
| `/light`, `/pause`, `/stop` have no effect | Newer firmware may require **Developer mode** in the LAN-only settings for third-party control |
| Need to report a bug or check what your printer sends | `python printwatch.py --dump` saves one full printer report to `printer_dump.json` with private fields removed. Look through it before sharing |
| Another program uses the same bot | Two programs can't read commands from the same bot. Use a separate bot for PrintWatch |

## Development

```bash
python -m unittest discover -s tests -v
```

The printer logic (`PrinterState`, `CommandHandler`) is pure Python and fully testable without a printer.

## Help wanted: AMS owners and other models

PrintWatch is developed on a P1S **without AMS**. It already shows whether an AMS is connected,
but AMS features (slot overview with filament type, colour and remaining %, low-filament warning)
need real data from AMS users.

If you have an **AMS / AMS lite**, or an **A1 / A1 mini / P1P**, you can help in 2 minutes:

1. Run `python printwatch.py --dump` while the printer is on (ideally during a print).
2. Open `printer_dump.json` and check it. IP, network and serial numbers are already removed.
3. Open an [issue](https://github.com/MJNProjekts/printwatch-lan/issues) titled
   "Dump: <your model> + AMS", attach the file and tell what you would like the bot to show.

## Roadmap

- [ ] AMS: slot overview and low-filament warning (needs data from AMS users, see above)
- [ ] Multiple printers in one bot
- [ ] Inline buttons instead of typed commands
- [ ] Browser-based setup page and automatic printer discovery
- [ ] Packaged app for macOS / Windows
- [ ] X1 series camera

Ideas and bug reports: please open an issue.

## License

MIT, see [LICENSE](LICENSE).

# PrintWatch LAN

**Telegram notifications, camera snapshots and remote control for Bambu Lab P1/A1 printers in LAN-only mode.**
No Bambu cloud, no Home Assistant, no port forwarding. Your computer talks to the printer on your
local network and sends messages to *your own* Telegram bot.

> **Status: public beta (v0.2.3-beta).** Tested on a Bambu Lab **P1S** in LAN-only mode.
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
| 📡 | Printer unreachable for 5 min *during a print* |

**Commands** (English and Latvian names both work)

| Command | Latvian | What it does |
|---|---|---|
| `/status` | `/p1s` | State, progress, layer, finish time, temperatures |
| `/photo` | `/foto` | Camera snapshot |
| `/light` | `/gaisma` | Toggle the chamber light |
| `/pause` | `/pauze` | Pause the print |
| `/resume` | `/turpinat` | Resume after pause |
| `/stop` | `/stop` | Stop the print, **requires** `/confirmstop` (`/stopja`) within 60 s |
| `/help` | `/help` | Command list |

Example:

```
🖨 P1S: printing
File: bracket_PETG
Progress: 29%, layer 4/25
Remaining: 42 min → done ~18:05
Nozzle: 250°C (target 250°C)
Bed: 70°C (target 70°C)
Light: on
```

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

- The bot **only answers your own chat ID** and ignores everyone else, because it can pause and stop the printer.
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
| Another program uses the same bot | Two programs can't read commands from the same bot. Use a separate bot for PrintWatch |

## Development

```bash
python -m unittest discover -s tests -v
```

The printer logic (`PrinterState`, `CommandHandler`) is pure Python and fully testable without a printer.

## Roadmap

- [ ] Multiple printers in one bot
- [ ] Inline buttons instead of typed commands
- [ ] Browser-based setup page and automatic printer discovery
- [ ] Packaged app for macOS / Windows
- [ ] X1 series camera

Ideas and bug reports: please open an issue.

## License

MIT, see [LICENSE](LICENSE).

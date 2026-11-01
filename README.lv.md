# PrintWatch LAN (latviski)

Telegram paziņojumi, kameras attēli un attālināta vadība Bambu Lab P1/A1 printeriem **LAN-only režīmā**.
Bez Bambu mākoņa un bez Home Assistant: dators sazinās ar printeri vietējā tīklā un sūta ziņas uz **tavu** Telegram botu.

> Publiska beta versija (v0.3.1-beta). Pārbaudīts ar **P1S**. Nav saistīts ar Bambu Lab.

Pilna dokumentācija angliski: [README.md](README.md)

## Ātrais starts

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
python printwatch.py --setup      # izvēlies valodu "lv"
python printwatch.py              # macOS: caffeinate -is python printwatch.py
```

Iestatīšanai vajag:
- **Telegram bota tokenu** (@BotFather → `/newbot`)
- No printera ekrāna: **IP** (Settings → WLAN), **sērijas numuru** (Settings → Device), **Access Code** (LAN Only Mode)

Chat ID vednis noteiks automātiski, kad botā nospiedīsi **Start**.

## Komandas

`/p1s` stāvoklis · `/foto` kameras attēls · `/gaisma` gaisma · `/autofoto` auto foto ieslēgt / izslēgt · `/atrums` ātrums (`/atrums 1`–`4`) · `/video` pēdējais timelapse video (`/videos` saraksts) · `/pauze` · `/turpinat` · `/stop` (apstiprina ar `/stopja`) · `/help`

**Auto foto:** pie pirmā slāņa, tad ik 15 min (drukām virs 2 h) vai ik 10 % (īsākām), kā arī pie pauzes, kļūdas un beigām.

Darbojas arī angļu nosaukumi (`/status`, `/photo`, `/light` ...).

## Palīdzība: AMS lietotāji

PrintWatch tiek izstrādāts uz P1S **bez AMS**. Ja tev ir AMS vai A1 / P1P, palaid
`python printwatch.py --dump` (vēlams drukas laikā) un pievieno failu `printer_dump.json`
jaunā [issue](https://github.com/MJNProjekts/printwatch-lan/issues). IP un sērijas numuri tiek aizvākti automātiski,
bet pirms sūtīšanas fails tomēr jāpārskata.

## Drošība

Bots atbild **tikai tavam** chat ID. `config.json` satur Access Code un bota tokenu, **nekad to nepublicē**.

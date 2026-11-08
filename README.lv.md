# PrintWatch LAN (latviski)

Telegram paziņojumi, kameras attēli un attālināta vadība Bambu Lab P1/A1 printeriem **LAN-only režīmā**.
Bez Bambu mākoņa un bez Home Assistant: dators sazinās ar printeri vietējā tīklā un sūta ziņas uz **tavu** Telegram botu.

> Publiska beta versija (v0.4.0-beta). Pārbaudīts ar **P1S**. Nav saistīts ar Bambu Lab.

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

## Skatītāji (ģimene, draugi, grupa)

Citi var sekot drukām, bet nevar vadīt printeri. Skatītājs:
- saņem automātiskās ziņas (sākta, pirmais slānis, pauze, kļūda, pabeigta), **bet ne automātiskos foto**
- var lietot `/p1s`, `/foto`, `/video`, `/videos`
- **nevar** lietot `/gaisma`, `/pauze`, `/turpinat`, `/stop`, `/atrums`, `/autofoto`, `/skatitaji`

Kā pievienot:
1. Cilvēks Telegram atver tavu botu un nospiež **Start**.
2. Tev atnāk `👤 Piekļuves pieprasījums: ...` ar komandu, piem., `/atlaut1`. Pieskaries tai.
3. Skatītājs saņem sveicienu ar savām komandām.

`/skatitaji` rāda sarakstu un noņemšanas komandas (`/nonemt1`). Līdz 10 skatītājiem. Der arī Telegram **grupa**.
Skatītāji glabājas `viewers.json` (nevis `config.json`).

## Palīdzība: AMS lietotāji

PrintWatch tiek izstrādāts uz P1S **bez AMS**. Ja tev ir AMS vai A1 / P1P, palaid
`python printwatch.py --dump` (vēlams drukas laikā) un pievieno failu `printer_dump.json`
jaunā [issue](https://github.com/MJNProjekts/printwatch-lan/issues). IP un sērijas numuri tiek aizvākti automātiski,
bet pirms sūtīšanas fails tomēr jāpārskata.

## Drošība

Printeri vadīt var **tikai tavs** chat ID; skatītāji var tikai skatīties, svešie nesaņem neko. `config.json` satur Access Code un bota tokenu, **nekad to nepublicē**.

# auto-transcribe

Audio recordings → local Whisper transcription → Claude CLI summary → Obsidian notes. Fully automated on macOS.

录音 → 本地 Whisper 转录 → Claude CLI 总结 → Obsidian 笔记。macOS 全自动管道。

## How It Works

```
① Plaud 设备          ② iPhone 录音           ③ Mac 录音
   │                     │                      │
   ▼                     ▼                      ▼
 Plaud 云端        iCloud 录音收件箱          手动拖入
   │                     │                      │
 pull-plaud.py      sync-icloud.py              │
 (every 60s)         (every 60s)                │
   │                     │                      │
   └─────────────────────┼──────────────────────┘
                         ▼
                      inbox/
                         │
               launchd detects new file
                         │
                    process.py
                         │
           ┌─────────────┼─────────────┐
           ▼             ▼             ▼
      Vibe/Whisper   Claude CLI    Obsidian
     (transcribe)   (summarize)   (markdown)
```

## Features

- **Local transcription** — Whisper via [Vibe](https://thewh1teagle.github.io/vibe/) (whisper.cpp + CoreML/Metal), fast on Apple Silicon
- **AI summaries** — Claude CLI auto-categorizes recordings (meeting / memo / content) and generates structured notes
- **Obsidian output** — Markdown notes with YAML frontmatter
- **Plaud integration** — Auto-pulls recordings from Plaud cloud via reverse-engineered web API (inspired by [openplaud](https://github.com/openplaud/openplaud) and [plaud-toolkit](https://github.com/sergivalverde/plaud-toolkit))
- **iCloud sync** — Record on iPhone → save to iCloud Drive → auto-processed on Mac
- **Voice Memos sync** — Picks up new macOS Voice Memos recordings
- **Fully automated** — 4 launchd services, zero manual steps after setup

## Background Services

| Service | Trigger | Action |
|---------|---------|--------|
| `auto-transcribe` | New file in `inbox/` | Transcribe → Summarize → Save to Obsidian |
| `sync-icloud` | Every 60s | iCloud recording inbox → `inbox/` |
| `pull-plaud` | Every 60s | Plaud cloud → `inbox/` (MP3) |
| `weekly-report` | Weekly | Generate weekly recording summary |

## Prerequisites

- **macOS** (Apple Silicon recommended)
- **Python 3.10+** with [Full Disk Access](https://support.apple.com/guide/mac-help/mchl211c911f/mac)
- **[ffmpeg](https://ffmpeg.org/)** — `brew install ffmpeg`
- **[Vibe](https://thewh1teagle.github.io/vibe/)** — Download the app (includes the `sona` CLI)
- **[Claude CLI](https://docs.anthropic.com/en/docs/claude-code)** — `npm install -g @anthropic-ai/claude-code`

## Quick Start

```bash
# Clone
git clone https://github.com/jaredlowcy/auto-transcribe.git
cd auto-transcribe

# Install dependencies
pip3 install -r requirements.txt

# Copy and edit config
cp config.example.yaml config.yaml
nano config.yaml

# Run installer (sets up launchd services)
python3 install.py

# Test — drop any audio file into inbox/
cp ~/some-recording.m4a inbox/
```

## Plaud Setup

Plaud recordings are pulled directly from the Plaud cloud API. No Developer API needed — uses the same web API as [web.plaud.ai](https://web.plaud.ai).

```bash
# 1. Log into web.plaud.ai with your Plaud account
# 2. Open browser DevTools (F12) → Console
# 3. Run: localStorage.getItem("tokenstr")
# 4. Save the token:
mkdir -p ~/.plaud && chmod 700 ~/.plaud
cat > ~/.plaud/config.json << 'EOF'
{
  "token": "YOUR_TOKEN_HERE",
  "region": "ap",
  "api_base": "https://api-apse1.plaud.ai"
}
EOF
chmod 600 ~/.plaud/config.json
```

**Regions:** `api.plaud.ai` (US), `api-euc1.plaud.ai` (EU), `api-apse1.plaud.ai` (Asia-Pacific)

Token is valid for ~300 days. The script warns you in the logs when it's about to expire.

## iPhone Setup

1. Record with Voice Memos or any recording app
2. Tap **Share** → **Save to Files** → **iCloud Drive** → **录音收件箱** folder
3. Mac syncs automatically within 60 seconds

## Configuration

See [`config.example.yaml`](config.example.yaml) for all options.

| Setting | Default | Description |
|---------|---------|-------------|
| `base_dir` | `~/auto-transcribe` | Project root |
| `obsidian_output` | `~/Documents/Obsidian Vault/录音笔记` | Output directory |
| `whisper_language` | `zh` | Transcription language |
| `claude_timeout` | `1800` | Claude CLI timeout (seconds) |

## Supported Formats

**Audio:** `.m4a` `.mp3` `.wav` `.flac` `.ogg` `.aac`

**Video** (audio extracted via ffmpeg): `.mp4` `.mov` `.mkv` `.avi` `.webm`

## Project Structure

```
auto-transcribe/
├── process.py             # Main pipeline: transcribe → summarize → Obsidian
├── pull-plaud.py          # Plaud cloud API → inbox/
├── sync-icloud.py         # iCloud recording inbox → inbox/
├── sync-voicememos.py     # macOS Voice Memos → inbox/
├── weekly-report.py       # Weekly recording summary
├── config.py              # Config loader
├── config.example.yaml    # Config template
├── install.py             # Installer (sets up launchd)
├── prompts/               # Claude prompt templates
│   ├── meeting.md         # Meeting summary prompt
│   ├── memo.md            # Memo prompt
│   └── content.md         # Content/podcast prompt
├── templates/             # launchd plist templates
├── inbox/                 # Drop audio files here
├── processing/            # Currently being processed
├── done/                  # Completed originals
├── failed/                # Failed files (transcript preserved)
└── transcripts/           # Raw transcription text backup
```

## Logs

```bash
tail -f /tmp/auto-transcribe-out.log   # Transcription pipeline
tail -f /tmp/plaud-pull-out.log        # Plaud sync
tail -f /tmp/icloud-sync-out.log       # iCloud sync
```

## Acknowledgments

- [openplaud/openplaud](https://github.com/openplaud/openplaud) — Plaud API reference
- [sergivalverde/plaud-toolkit](https://github.com/sergivalverde/plaud-toolkit) — Plaud API endpoints and auth flow
- [thewh1teagle/vibe](https://github.com/thewh1teagle/vibe) — Local Whisper transcription

## License

[MIT](LICENSE) — Copyright (c) 2026 Jared Low

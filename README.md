# auto-transcribe

Two fully automated pipelines on macOS: (1) audio recordings → local Whisper → Claude AI summary → Obsidian, (2) social media links via Telegram → download/scrape → AI analysis → Obsidian.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                     PIPELINE 1: RECORDINGS                         │
│                                                                    │
│  ① Plaud Device     ② iPhone Recording    ③ Mac / Drop File       │
│       │                    │                     │                  │
│       ▼                    ▼                     ▼                  │
│   Plaud Cloud      iCloud 录音收件箱          inbox/               │
│       │                    │                     │                  │
│  pull-plaud.py       sync-icloud.py              │                  │
│   (every 5min)        (every 60s)                │                  │
│       │                    │                     │                  │
│       └────────────────────┼─────────────────────┘                  │
│                            ▼                                        │
│                         inbox/                                      │
│                            │                                        │
│                  launchd (WatchPaths)                                │
│                            │                                        │
│                       process.py                                    │
│                            │                                        │
│              ┌─────────────┼─────────────────┐                      │
│              ▼             ▼                 ▼                      │
│        Vibe/Whisper    Claude CLI         Obsidian                  │
│       (CoreML/Metal)  (classify +     recording-notes/             │
│        local GPU       title +                                      │
│        transcribe)     summarize)                                   │
│                                                                    │
├─────────────────────────────────────────────────────────────────────┤
│                  PIPELINE 2: SOCIAL MEDIA                          │
│                                                                    │
│              Telegram Bot / OmniGet                                │
│                       │                                            │
│             telegram-capture.py                                    │
│                       │                                            │
│          ┌────────────┼────────────┐                               │
│          ▼            ▼            ▼                               │
│     Text/Image    Short Video    Long Video                        │
│     (Threads,     (<5min)        (≥5min)                           │
│      IG post,         │              │                             │
│      article)    ┌────┘              │                             │
│          │       ▼                   ▼                             │
│          │    yt-dlp download     yt-dlp download                  │
│          │       │                   │                             │
│          │       ▼                   ▼                             │
│          │    inbox/ ──────────→ inbox/                             │
│          │       │                   │                             │
│          │    process.py          process.py                       │
│          │    (Whisper +          (Whisper +                        │
│          │     video-short.md)    video-long.md)                   │
│          │       │                   │                             │
│          ▼       ▼                   ▼                             │
│       Obsidian: social-captures/                                   │
│                                                                    │
│  YouTube: subtitle API first → fallback to Whisper                 │
│  Ads: auto-detect (URL params) or manual "ad" prefix               │
└─────────────────────────────────────────────────────────────────────┘
```

## Features

### Recording Pipeline
- **100% local transcription** — Whisper via [Vibe](https://thewh1teagle.github.io/vibe/) (whisper.cpp + CoreML/Metal), fast on Apple Silicon
- **AI summaries** — Claude CLI auto-classifies (meeting / memo / content / 1on1 / class / call / daylog / discussion) and generates structured notes
- **9 scene-specific prompts** — each recording type gets a tailored summary format
- **Plaud integration** — auto-pulls from Plaud cloud via reverse-engineered web API
- **iCloud sync** — record on iPhone → auto-processed on Mac
- **VAD silence stripping** — optional Silero VAD removes dead air before transcription
- **Quality detection** — auto-retries with different language if transcript quality is poor
- **Estimated timestamps** — `[MM:SS]` markers in full transcript for quick navigation

### Social Media Pipeline
- **Telegram Bot** — send any link, get structured notes in Obsidian
- **14 platforms supported** — YouTube, Bilibili, TikTok, Douyin, Instagram (Reels + posts), Threads, Twitter/X, Xiaohongshu, and any web page
- **YouTube subtitle priority** — fetches official subtitles via API (10x faster than downloading + Whisper)
- **Smart prompt routing** — short video (<5min) gets framework breakdown (Hook/Structure/CTA), long video (≥5min) gets deep study notes
- **Ad creative analysis** — auto-detect ads via URL params (`ad_id`, `campaign_id`, `utm_medium=cpc`) or manual `ad` prefix
- **Zero-token text capture** — text/image posts saved directly without Claude analysis
- **Platform-specific scraping** — Threads (Playwright), Instagram (Embed API), Douyin (iesdouyin fallback)

### Infrastructure
- **5 launchd services** — fully automated, zero manual steps
- **Menu bar app** — real-time transcription status + YouTube download
- **Token usage logging** — estimated input/output tokens per Claude call
- **Crash recovery** — orphan files in `processing/` auto-restored to inbox
- **Process lock** — prevents duplicate launchd instances

## Background Services

| Service | Trigger | Action |
|---------|---------|--------|
| `com.jared.auto-transcribe` | WatchPaths: `inbox/` | Transcribe → Summarize → Save to Obsidian |
| `com.jared.sync-icloud` | Every 60s | iCloud recording inbox → `inbox/` |
| `com.auto-transcribe.pull-plaud` | Every 5min | Plaud cloud → `inbox/` |
| `com.jared.auto-transcribe-menubar` | RunAtLoad + KeepAlive | Menu bar status app |
| `com.auto-transcribe.weekly-report` | Weekly | Generate weekly summary |

## Obsidian Output

| Folder | Content | Filename Format |
|--------|---------|-----------------|
| `social-captures/` | Social media (text + video transcriptions) | `{date}-{platform}-{title}.md` |
| `social-captures/` | Ad creatives | `{date}-{platform}-ad-{title}.md` |
| `recording-notes/` | Personal recordings | `{date}-{type}-{title}.md` |

Example frontmatter:
```yaml
---
date: 2026-04-13
platform: tiktok
type: video-short
url: https://...
duration: 3min
tags: [social-capture, tiktok, 短视频]
---
```

## Prompt Routing

```
Social media video (has platform):
  ├── is_ad? ──────────→ ad.md (creative breakdown)
  ├── <5min? ──────────→ video-short.md (Hook/Structure/CTA + copyable formula)
  └── ≥5min? ──────────→ video-long.md (deep notes by topic)

Recording (no platform):
  └── Claude auto-classifies → meeting / memo / 1on1 / class / call /
                                daylog / discussion / content

Text/image content:
  └── Saved directly to Obsidian (zero Claude tokens)
```

### Prompt Templates

| Template | Scene | Key Output |
|----------|-------|------------|
| `meeting.md` | Multi-person meeting | Agenda → Decisions → Action items |
| `1on1.md` | 1-on-1 conversation | Discussion points → Follow-ups |
| `class.md` | Classroom teaching | Learning objectives → Key points |
| `call.md` | Phone call | Purpose → Key points → Next steps |
| `discussion.md` | Deep discussion/debate | Positions → Arguments → Consensus |
| `daylog.md` | Daily miscellaneous recording | Timeline → TODOs |
| `content.md` | Podcast/lecture | Core insights → Takeaways |
| `memo.md` | Personal voice memo | Ideas organized → TODOs |
| `video-short.md` | Short video (<5min) | Hook/Structure/CTA breakdown + copyable formula |
| `video-long.md` | Long video (≥5min) | Core insights → Topic outline → Tools → Action items |
| `ad.md` | Ad creative analysis | Product → Hook/Selling point/CTA → Reusable techniques |
| `weekly-report.md` | Weekly report | Completed → Next week plan |

## Prerequisites

- **macOS** (Apple Silicon recommended for GPU acceleration)
- **Python 3.13** with [Full Disk Access](https://support.apple.com/guide/mac-help/mchl211c911f/mac)
- **[ffmpeg](https://ffmpeg.org/)** — `brew install ffmpeg`
- **[Vibe](https://thewh1teagle.github.io/vibe/)** — download the app (includes `sona` CLI)
- **[Claude CLI](https://docs.anthropic.com/en/docs/claude-code)** — `npm install -g @anthropic-ai/claude-code`
- **[yt-dlp](https://github.com/yt-dlp/yt-dlp)** — `brew install yt-dlp`

## Quick Start

```bash
# Clone
git clone <repo-url>
cd auto-transcribe

# Install Python dependencies
pip3.13 install httpx lxml pyyaml youtube-transcript-api
pip3.13 install python-telegram-bot  # for Telegram bot
pip3.13 install rumps                # for menu bar app

# Copy and edit config
cp config.example.yaml config.yaml
nano config.yaml

# Run installer (sets up launchd services)
python3.13 install.py

# Test — drop any audio file into inbox/
cp ~/some-recording.m4a inbox/
tail -f /tmp/auto-transcribe-out.log
```

## Telegram Bot Usage

```
# Basic — send any link
https://youtube.com/watch?v=xxx

# Specify language
en https://youtube.com/watch?v=xxx

# Mark as ad creative
ad https://instagram.com/reel/xxx

# Combine
ad en https://youtube.com/watch?v=xxx
```

Supported platforms: YouTube, Bilibili, TikTok, Douyin, Instagram (Reels + posts), Threads, Twitter/X, Xiaohongshu, and any web page.

## iPhone Setup

**Current:** Plaud / Voice Memos → Share → Save to Files → iCloud Drive → 录音收件箱 → Mac auto-processes within 60s

**Plaud users:** Just record → Plaud cloud syncs → `pull-plaud.py` auto-pulls every 5 minutes

## Plaud Setup

```bash
# 1. Log into web.plaud.ai
# 2. DevTools (F12) → Console → localStorage.getItem("tokenstr")
# 3. Save token:
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

Regions: `api.plaud.ai` (US), `api-euc1.plaud.ai` (EU), `api-apse1.plaud.ai` (Asia-Pacific). Token valid ~300 days.

## Configuration

See [`config.example.yaml`](config.example.yaml) for all options.

| Setting | Default | Description |
|---------|---------|-------------|
| `base_dir` | `~/auto-transcribe` | Project root |
| `obsidian_output` | `Obsidian Vault/recording-notes` | Recording notes output |
| `captures_output` | `Obsidian Vault/social-captures` | Social media output |
| `whisper_language` | `zh` | Default transcription language |
| `claude_timeout` | `1800` | Claude CLI timeout (seconds) |
| `claude_max_retries` | `3` | Max retry attempts |
| `use_vad` | `false` | Enable Silero VAD silence stripping |

## Supported Formats

**Audio:** `.m4a` `.mp3` `.wav` `.flac` `.ogg` `.aac` `.opus` `.wma`

**Video** (audio extracted via ffmpeg): `.mp4` `.mov` `.mkv` `.avi` `.webm` `.ts` `.3gp`

## Project Structure

```
auto-transcribe/
├── process.py               # Main pipeline: transcribe → summarize → Obsidian
├── telegram-capture.py      # Telegram Bot: links → download/scrape → classify
├── config.py                # Config loader (YAML → dataclass)
├── config.yaml              # User config (gitignored)
├── config.example.yaml      # Config template
├── status_db.py             # SQLite status DB (process.py writes, menubar reads)
├── menubar.py               # macOS menu bar app (rumps)
├── sync-icloud.py           # iCloud recording inbox → inbox/
├── pull-plaud.py            # Plaud cloud API → inbox/
├── sync-voicememos.py       # macOS Voice Memos → inbox/
├── weekly-report.py         # Weekly recording summary generator
├── install.py               # Installer (launchd setup)
├── create-shortcut.py       # iOS Shortcuts generator
├── prompts/                 # Claude prompt templates
│   ├── meeting.md           #   Multi-person meeting
│   ├── memo.md              #   Personal voice memo
│   ├── content.md           #   Podcast/lecture
│   ├── 1on1.md              #   1-on-1 conversation
│   ├── class.md             #   Classroom teaching
│   ├── call.md              #   Phone call
│   ├── daylog.md            #   Daily miscellaneous
│   ├── discussion.md        #   Deep discussion/debate
│   ├── video-short.md       #   Short video framework breakdown
│   ├── video-long.md        #   Long video deep notes
│   ├── ad.md                #   Ad creative analysis
│   ├── weekly-report.md     #   Weekly summary
│   ├── capture.md           #   Generic web article (text, no Claude)
│   ├── social-organic.md    #   FB/IG organic post (text, no Claude)
│   ├── twitter.md           #   Twitter/X (text, no Claude)
│   └── xiaohongshu.md       #   Xiaohongshu (text, no Claude)
├── inbox/                   # Drop audio files here
├── processing/              # Currently being processed
├── failed/                  # Failed files (transcript preserved)
├── transcripts/             # Raw transcription text backup (with timestamps)
└── logs/                    # Project logs (launchd logs at /tmp/)
```

## Costs

| Component | Cost |
|-----------|------|
| Local Whisper transcription | Free (runs on your GPU) |
| YouTube subtitle extraction | Free (API) |
| Text/image content capture | Free (no Claude call) |
| Claude analysis per recording/video | ~1,800 tokens (~$0.02) |
| Token usage | Logged in `/tmp/auto-transcribe-out.log` |

## Logs

```bash
tail -f /tmp/auto-transcribe-out.log   # Transcription pipeline
tail -f /tmp/icloud-sync-out.log       # iCloud sync
tail -f /tmp/plaud-pull-out.log        # Plaud sync
```

## launchd Gotchas

- **StandardOutPath must point to `/tmp/`** — pointing to project dir causes exit 78
- **WatchPaths can't monitor iCloud paths** — TCC permission issues on macOS Sequoia+, use StartInterval polling instead
- **Use `python3.13` full path** — Full Disk Access is bound to the specific binary
- **Python 3.13 needs Full Disk Access** — System Settings → Privacy → Full Disk Access → drag in `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13`

### Reload services

```bash
launchctl remove com.jared.auto-transcribe
launchctl remove com.jared.sync-icloud
launchctl load ~/Library/LaunchAgents/com.jared.auto-transcribe.plist
launchctl load ~/Library/LaunchAgents/com.jared.sync-icloud.plist
launchctl list | grep com.jared
```

## Acknowledgments

- [openplaud/openplaud](https://github.com/openplaud/openplaud) — Plaud API reference
- [sergivalverde/plaud-toolkit](https://github.com/sergivalverde/plaud-toolkit) — Plaud API endpoints and auth flow
- [thewh1teagle/vibe](https://github.com/thewh1teagle/vibe) — Local Whisper transcription with CoreML
- [danielmiessler/fabric](https://github.com/danielmiessler/fabric) — Prompt design inspiration (extract_wisdom pattern)

## License

[MIT](LICENSE) — Copyright (c) 2026 Jared Low

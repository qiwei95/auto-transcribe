# auto-transcribe

Audio recordings → local Whisper transcription → Claude CLI summary → Obsidian notes. Fully automated on macOS.

录音 → 本地 Whisper 转录 → Claude CLI 总结 → Obsidian 笔记。macOS 全自动管道。

## Features / 功能

- **Local transcription** — Whisper via [Vibe](https://thewh1teagle.github.io/vibe/) (whisper.cpp + CoreML), fast on Apple Silicon
- **AI summaries** — Claude CLI generates structured meeting notes, memos, or content summaries
- **Obsidian output** — Markdown notes with frontmatter, auto-categorized (meeting/content/memo)
- **Fully automated** — macOS launchd watches your inbox folder, processes new files automatically
- **iCloud sync** — Record on iPhone, save to iCloud Drive, auto-synced to Mac for processing
- **Voice Memos sync** — Automatically picks up new Mac Voice Memos recordings
- **Plaud integration** *(WIP)* — Pull recordings from Plaud.ai API

---

- **本地转录** — 通过 Vibe (whisper.cpp + CoreML) 使用 Whisper，Apple Silicon 上很快
- **AI 总结** — Claude CLI 生成结构化会议纪要、备忘录或内容整理
- **Obsidian 输出** — 带 frontmatter 的 Markdown 笔记，自动分类（会议/内容/备忘）
- **全自动** — macOS launchd 监听 inbox 文件夹，有新文件自动处理
- **iCloud 同步** — iPhone 录音存到 iCloud Drive，自动同步到 Mac 处理
- **语音备忘录同步** — 自动获取 Mac 语音备忘录新录音
- **Plaud 集成** *（开发中）* — 从 Plaud.ai API 拉取录音

## Prerequisites / 前置要求

- **macOS** (Apple Silicon recommended / 推荐 Apple Silicon)
- **Python 3.10+**
- **[ffmpeg](https://ffmpeg.org/)** — `brew install ffmpeg`
- **[Vibe](https://thewh1teagle.github.io/vibe/)** — Download the app, it includes the `sona` CLI
- **[Claude CLI](https://docs.anthropic.com/en/docs/claude-code)** — `npm install -g @anthropic-ai/claude-code`

## Quick Start / 快速开始

```bash
# 1. Clone / 克隆
git clone https://github.com/YOUR_USERNAME/auto-transcribe.git
cd auto-transcribe

# 2. Install Python dependencies / 安装 Python 依赖
pip3 install -r requirements.txt

# 3. Run installer / 运行安装脚本
python3 install.py

# 4. Edit config / 编辑配置
#    Adjust paths to match your setup / 调整路径匹配你的环境
nano config.yaml

# 5. Load launchd services / 加载 launchd 服务
launchctl load ~/Library/LaunchAgents/com.auto-transcribe.plist
launchctl load ~/Library/LaunchAgents/com.auto-transcribe.sync-icloud.plist
launchctl load ~/Library/LaunchAgents/com.auto-transcribe.sync-voicememos.plist

# 6. Test / 测试
#    Drop an audio file into inbox/ / 往 inbox/ 放一个音频文件
cp ~/some-recording.m4a inbox/
```

## How It Works / 工作原理

```
iPhone / Mac
    │
    ├─ iCloud Drive/录音收件箱/  ──→  sync-icloud.py  ──→  inbox/
    ├─ Voice Memos               ──→  sync-voicememos.py ──→  inbox/
    └─ Plaud.ai (WIP)           ──→  pull-plaud.py  ──→  inbox/
                                                            │
                                                      process.py
                                                            │
                                                    ┌───────┴───────┐
                                                    │               │
                                              Vibe/Whisper    Claude CLI
                                              (transcribe)    (summarize)
                                                    │               │
                                                    └───────┬───────┘
                                                            │
                                                    Obsidian Vault
                                                    (Markdown notes)
```

Three launchd services run in the background:

| Service | Trigger | Action |
|---------|---------|--------|
| `com.auto-transcribe` | New file in `inbox/` | Transcribe → Summarize → Save to Obsidian |
| `com.auto-transcribe.sync-icloud` | New file in iCloud inbox | Copy from iCloud → `inbox/` |
| `com.auto-transcribe.sync-voicememos` | New Voice Memo | Copy from Voice Memos → `inbox/` |

三个 launchd 服务在后台运行：

| 服务 | 触发条件 | 动作 |
|------|---------|------|
| `com.auto-transcribe` | `inbox/` 有新文件 | 转录 → 总结 → 存入 Obsidian |
| `com.auto-transcribe.sync-icloud` | iCloud 收件箱有新文件 | 从 iCloud 复制到 `inbox/` |
| `com.auto-transcribe.sync-voicememos` | 有新语音备忘录 | 从语音备忘录复制到 `inbox/` |

## Configuration / 配置

Copy and edit the example config:

```bash
cp config.example.yaml config.yaml
nano config.yaml
```

Key settings / 关键配置:

| Setting | Default | Description |
|---------|---------|-------------|
| `base_dir` | `~/auto-transcribe` | Project root / 项目根目录 |
| `obsidian_output` | `~/Documents/Obsidian Vault/录音笔记` | Where notes go / 笔记输出目录 |
| `whisper_language` | `zh` | Transcription language / 转录语言 |
| `claude_timeout` | `1800` | Claude CLI timeout in seconds / 超时秒数 |

See [`config.example.yaml`](config.example.yaml) for all options.

## Supported Formats / 支持的格式

**Audio:** `.m4a` `.mp3` `.wav` `.flac` `.ogg` `.aac`

**Video** (audio extracted automatically): `.mp4` `.mov` `.mkv` `.avi` `.webm`

## iPhone Setup / iPhone 设置

Record on iPhone → save to iCloud Drive → auto-synced to Mac:

1. Open **Voice Memos** or any recording app
2. After recording, tap **Share** → **Save to Files**
3. Choose **iCloud Drive** → folder matching your `icloud_inbox` config (default: `录音收件箱`)
4. Mac syncs automatically → `sync-icloud.py` copies to `inbox/` → `process.py` handles the rest

在 iPhone 录音 → 存到 iCloud Drive → 自动同步到 Mac：

1. 打开**语音备忘录**或任何录音 app
2. 录完后点**分享** → **存储到"文件"**
3. 选择 **iCloud Drive** → 对应 `icloud_inbox` 配置的文件夹（默认：`录音收件箱`）
4. Mac 自动同步 → `sync-icloud.py` 复制到 `inbox/` → `process.py` 完成后续处理

## Logs / 日志

```bash
# View live logs / 查看实时日志
tail -f ~/auto-transcribe/logs/launchd-out.log

# Check for errors / 查看错误
tail -f ~/auto-transcribe/logs/launchd-err.log
```

## Plaud Integration (WIP) / Plaud 集成（实验中）

Plaud.ai recorder support is experimental. Authentication works, but recording download is not yet implemented.

Plaud.ai 录音器支持是实验性的。认证已完成，但录音下载尚未实现。

To set up: get API credentials at [platform.plaud.ai](https://platform.plaud.ai) and add to `.env`:

```
PLAUD_CLIENT_ID=your_client_id
PLAUD_SECRET_KEY=your_secret_key
```

## Project Structure / 项目结构

```
auto-transcribe/
├── process.py           # Main pipeline / 主管道
├── sync-icloud.py       # iCloud sync / iCloud 同步
├── sync-voicememos.py   # Voice Memos sync / 语音备忘录同步
├── pull-plaud.py        # Plaud API (WIP) / Plaud API（开发中）
├── config.py            # Config loader / 配置加载
├── config.example.yaml  # Config template / 配置模板
├── install.py           # Installer / 安装脚本
├── prompts/             # Claude prompt templates / Claude 提示词模板
│   └── meeting.md       # Meeting summary prompt / 会议总结提示词
├── templates/           # launchd plist templates / launchd 模板
├── inbox/               # Drop audio files here / 放音频文件的地方
├── processing/          # Currently processing / 正在处理中
├── done/                # Completed originals / 处理完成的原件
├── failed/              # Failed files / 处理失败的文件
├── transcripts/         # Raw transcription text / 原始转录文本
└── logs/                # launchd logs / 运行日志
```

## License

[MIT](LICENSE) - Copyright (c) 2026 Jared Low

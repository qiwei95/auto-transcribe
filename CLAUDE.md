# Auto-Transcribe 录音自动转录管道

## 项目概述

监听文件夹中的新录音 → 本地 Whisper 转录 → Claude CLI 总结 → 存入 Obsidian Vault。
Telegram bot 接收社交媒体链接 → 自动下载音频/抓取文本 → 同样进入转录管道或保存到 Obsidian。

## 技术栈

- **语言**：Python 3.13
- **转录**：Vibe / sona CLI（whisper.cpp + CoreML/Metal，Apple Silicon GPU 加速）
- **音频转换**：ffmpeg 8.0
- **总结**：Claude CLI (`claude -p`)
- **社交媒体捕获**：Telegram bot + yt-dlp
- **后台触发**：macOS launchd
- **输出**：Obsidian Markdown

## 目录结构

```
inbox/        → 待处理录音放这里
processing/   → 正在处理中（防止重复处理）
done/         → 处理完成的原始文件
failed/       → 处理失败的文件（保留转录结果）
transcripts/  → 转录文本备份
prompts/      → Claude 总结用的 prompt 模板（按场景分类）
templates/    → launchd plist 模板
```

## 关键路径

- 主脚本：`process.py`（inbox 有新文件时自动触发）
- Telegram bot：`telegram-capture.py`（接收链接 → 音频下载到 inbox / 文本存 Obsidian）
- 菜单栏 App：`menubar.py`（rumps，显示实时状态 + YouTube 下载）
- 状态数据库：`status_db.py`（SQLite，process.py 写 / menubar.py 读）
- iCloud 同步：`sync-icloud.py`（每 60 秒轮询 iCloud 录音收件箱 → 本地 inbox）
- Plaud 拉取：`pull-plaud.py`（通过 Plaud 网页 API 直接拉取录音到 inbox）
- 配置：`config.yaml`（必须存在，否则默认路径会错）
- Obsidian 输出：`~/Documents/Obsidian Vault/录音笔记/`（录音）、`~/Documents/Obsidian Vault/Captures/`（社交媒体文本）

## launchd 服务

| 服务 | 触发方式 | 作用 |
|------|---------|------|
| `com.jared.auto-transcribe` | WatchPaths: inbox/ | 转录 + 总结 + 存 Obsidian |
| `com.jared.sync-icloud` | StartInterval: 60秒 | iCloud 录音收件箱 → 本地 inbox |
| `com.auto-transcribe.pull-plaud` | StartInterval: 300秒 | Plaud 云端 → 本地 inbox |
| `com.jared.auto-transcribe-menubar` | RunAtLoad + KeepAlive | 菜单栏状态 App |
| `com.jared.telegram-capture` | RunAtLoad + KeepAlive | Telegram 内容捕获 bot |

### 重新加载服务

```bash
# 单个服务重载
launchctl remove com.jared.telegram-capture
launchctl load ~/Library/LaunchAgents/com.jared.telegram-capture.plist

# 确认状态（exit code 为 0 = 正常）
launchctl list | grep -E "auto-transcribe|telegram|plaud|sync-icloud"
```

### 查看日志

```bash
tail -20 /tmp/auto-transcribe-out.log   # 转录
tail -20 /tmp/icloud-sync-out.log       # iCloud 同步
tail -20 /tmp/telegram-capture-out.log  # Telegram bot
```

## launchd 踩坑记录

- **StandardOutPath 必须指向 /tmp/**：指向项目目录会导致 exit 78
- **WatchPaths 不能监听 iCloud 路径**：macOS Sequoia+ 有 TCC 权限问题，改用 StartInterval 轮询
- **ProgramArguments 用 python3.13**：完全磁盘访问权限绑定的是 python3.13，不要用 python3 symlink
- **Python 3.13 需要"完全磁盘访问权限"**：系统设置 → 隐私与安全 → 拖入 `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13`
- **Telegram bot 需要 EnvironmentVariables**：launchd 不读 .env，token 直接写在 plist 的 EnvironmentVariables 里

## 开发注意事项

- 转录语言参数用 `zh`（中英马混合内容），Telegram 用户可用前缀 `en`/`zh` 覆盖
- iCloud 文件需要等下载完成才能处理（检测文件大小稳定 5 秒）
- `claude -p` 调用需要重试逻辑（最多 3 次）
- 处理失败时保留已有的转录结果，只标记总结失败
- 短视频时间戳是按字符数等比估算的，不精确
- Plaud token 有效期约 300 天，过期后需从 web.plaud.ai 重新获取

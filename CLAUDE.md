# Auto-Transcribe 录音自动转录管道

## 项目概述

监听文件夹中的新录音 → 本地 Whisper 转录 → Claude CLI 总结 → 存入 Obsidian Vault。

## 技术栈

- **语言**：Python 3.13
- **转录**：Vibe / sona CLI（whisper.cpp + CoreML/Metal，Apple Silicon GPU 加速）
- **音频转换**：ffmpeg 8.0
- **总结**：Claude CLI (`claude -p`)
- **后台触发**：macOS launchd
- **输出**：Obsidian Markdown

## 目录结构

```
inbox/        → 待处理录音放这里
processing/   → 正在处理中（防止重复处理）
done/         → 处理完成的原始文件
failed/       → 处理失败的文件（保留转录结果）
transcripts/  → 转录文本备份
logs/         → 项目内日志（仅供参考，launchd 日志在 /tmp/）
prompts/      → Claude 总结用的 prompt 模板
```

## 关键路径

- 主脚本：`process.py`（inbox 有新文件时自动触发）
- iCloud 同步：`sync-icloud.py`（每 60 秒轮询 iCloud 录音收件箱 → 本地 inbox）
- Plaud 拉取：`pull-plaud.py`（已弃用——Developer API 不开放，改用 App 导出到 iCloud 录音收件箱）
- 配置：`config.yaml`（必须存在，否则默认路径会错）
- Obsidian 输出：`~/Documents/Obsidian Vault/录音笔记/`
- iCloud 收件箱：`~/Library/Mobile Documents/com~apple~CloudDocs/录音收件箱/`

## launchd 服务

| 服务 | 触发方式 | 作用 |
|------|---------|------|
| `com.jared.auto-transcribe` | WatchPaths: inbox/ | 转录 + 总结 + 存 Obsidian |
| `com.jared.sync-icloud` | StartInterval: 60秒轮询 | iCloud 录音收件箱 → 本地 inbox |

### launchd 踩坑记录

- **StandardOutPath 必须指向 /tmp/**：指向项目目录会导致 exit 78（launchd 已知权限 bug，进程不执行）
- **WatchPaths 不能监听 iCloud 路径**：`Mobile Documents` 路径在 macOS Sequoia+ 下有 TCC 权限问题，改用 StartInterval 轮询
- **ProgramArguments 用 python3.13**：不要用 python3 symlink，完全磁盘访问权限绑定的是 python3.13
- **config.py 用 `Path(__file__)` 优先查找 config.yaml**：launchd 的 WorkingDirectory 不一定生效
- **Python 3.13 需要"完全磁盘访问权限"**：系统设置 → 隐私与安全 → 完全磁盘访问权限 → 拖入 `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13`

### 重新加载服务

```bash
launchctl remove com.jared.sync-icloud
launchctl remove com.jared.auto-transcribe
launchctl load ~/Library/LaunchAgents/com.jared.sync-icloud.plist
launchctl load ~/Library/LaunchAgents/com.jared.auto-transcribe.plist
launchctl list | grep com.jared  # 确认 exit code 为 0
```

### 查看日志

```bash
tail -20 /tmp/icloud-sync-out.log    # sync 日志
tail -20 /tmp/auto-transcribe-out.log # 转录日志
```

## 开发注意事项

- 转录语言参数用 `zh`（中英马混合内容）
- iCloud 文件需要等下载完成才能处理（检测文件大小稳定 5 秒）
- 用 json 文件记录已处理文件的 hash，防止重复处理
- `claude -p` 调用需要重试逻辑（最多 3 次）
- 处理失败时保留已有的转录结果，只标记总结失败
- Manglish（中英马混合）录音本地 Whisper 转录质量有限，复杂录音建议在 Plaud App 内查看

## 可复用的 Skill

- `whisper-transcribe`：已有的转录 skill
- `meeting-1on1`：会议总结 skill，可在 Claude CLI 总结步骤中复用

## 实现阶段

1. **Phase 1** ✅：核心管道（Vibe 转录 + Claude CLI 总结 + Obsidian）
2. **Phase 2** ✅：launchd 自动触发（inbox 有新文件自动处理）
3. **Phase 3** ✅：iCloud 同步 + Plaud 集成（Plaud 录音通过 App 导出到 iCloud 录音收件箱，Developer API 不开放申请）
4. **Phase 3.5** 🔶：iOS 快捷指令（简化 iPhone 分享流程）
5. **Phase 4**：智能场景识别 + prompt 优化

## iPhone 使用方法

**当前方式**：Plaud/语音备忘录 → 分享 → 存储到"文件" → iCloud Drive → 录音收件箱 → Mac 每 60 秒自动处理

**待优化**：iOS 快捷指令「Save to Recording Inbox」— 分享 → 选快捷指令 → 自动存到录音收件箱（2 步完成）

## Plaud 集成说明

- Plaud Desktop App 已安装（v1.1.7），用于录制 Mac 上的会议（Zoom/Teams/Slack）
- Plaud 手机录音存在 Plaud 云端，不同步到 iCloud 或本地文件系统
- Plaud Developer API 不开放申请（Private Beta）
- 实际 API 域名：`api-apse1.plaud.ai`（亚太区），用户 API 非开发者 API
- **使用流程**：Plaud App 录完 → 手动分享/导出 → 存到 iCloud 录音收件箱 → 自动处理

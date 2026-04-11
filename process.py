#!/usr/bin/env python3
"""
Auto-Transcribe Pipeline
录音自动转录 → Claude CLI 总结 → Obsidian Vault

用法：python process.py
"""

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

# launchd 环境下 stdout 不会自动刷新，强制无缓冲
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# ── 配置 ─────────────────────────────────────────
from config import load_config

_cfg = load_config()

BASE_DIR = _cfg.base_dir
INBOX = BASE_DIR / "inbox"
PROCESSING = BASE_DIR / "processing"
DONE = BASE_DIR / "done"
FAILED = BASE_DIR / "failed"
TRANSCRIPTS = BASE_DIR / "transcripts"
LOGS = BASE_DIR / "logs"
PROMPTS = BASE_DIR / "prompts"

OBSIDIAN_OUTPUT = _cfg.obsidian_output
PROCESSED_DB = BASE_DIR / "processed.json"

SONA_CLI = _cfg.sona_cli
SONA_MODEL = _cfg.sona_model
WHISPER_LANGUAGE = _cfg.whisper_language

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".flac", ".ogg", ".aac"}
ALL_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS

FILE_STABLE_SECONDS = _cfg.file_stable_seconds
FILE_STABLE_INTERVAL = 1  # 检测间隔
CLAUDE_MAX_RETRIES = _cfg.claude_max_retries
CLAUDE_TIMEOUT = _cfg.claude_timeout
MAX_TRANSCRIPT_CHARS = _cfg.max_transcript_chars
USE_VAD = _cfg.use_vad


# ── 工具函数 ──────────────────────────────────────

def log(msg: str):
    """打印带时间戳的日志"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def file_hash(path: Path) -> str:
    """计算文件 SHA256 前 16 位"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def load_processed() -> dict:
    """加载已处理文件记录（JSON 损坏时返回空 dict 而非崩溃）"""
    if PROCESSED_DB.exists():
        try:
            return json.loads(PROCESSED_DB.read_text())
        except (json.JSONDecodeError, ValueError):
            log("⚠ processed.json 损坏，备份后重置")
            backup = PROCESSED_DB.with_suffix(".json.bak")
            shutil.copy2(PROCESSED_DB, backup)
            return {}
    return {}


def save_processed(db: dict):
    """保存已处理文件记录（原子写入：先写临时文件再 rename）"""
    fd, tmp = tempfile.mkstemp(dir=PROCESSED_DB.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(db, f, indent=2, ensure_ascii=False)
        os.replace(tmp, PROCESSED_DB)
    except BaseException:
        os.unlink(tmp)
        raise


def notify(title: str, message: str):
    """macOS 原生通知（参数化传入，防注入）"""
    script = (
        'on run argv\n'
        '  display notification (item 2 of argv) with title (item 1 of argv)\n'
        'end run'
    )
    subprocess.run(
        ["osascript", "-e", script, title, message],
        capture_output=True,
    )


def get_audio_duration(path: Path) -> float:
    """用 ffprobe 获取音频时长（秒）"""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


# ── 核心步骤 ──────────────────────────────────────

def wait_for_file_ready(path: Path) -> bool:
    """等待文件下载完成（iCloud 同步场景）"""
    log(f"  等待文件就绪: {path.name}")
    prev_size = -1
    stable_count = 0

    for _ in range(60):  # 最多等 60 秒
        if not path.exists():
            return False
        size = path.stat().st_size
        if size == prev_size and size > 0:
            stable_count += 1
            if stable_count >= FILE_STABLE_SECONDS:
                return True
        else:
            stable_count = 0
        prev_size = size
        time.sleep(FILE_STABLE_INTERVAL)

    log(f"  ⚠ 文件等待超时: {path.name}")
    return False


def extract_audio(video_path: Path) -> Path:
    """从视频提取音频"""
    audio_path = video_path.with_suffix(".m4a")
    log(f"  ffmpeg 提取音频: {video_path.name} → {audio_path.name}")
    result = subprocess.run(
        ["ffmpeg", "-i", str(video_path), "-vn", "-acodec", "copy",
         str(audio_path), "-y"],
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败: {result.stderr.decode().strip()}")
    return audio_path


def strip_silence(audio_path: Path) -> Path | None:
    """用 Silero VAD 去掉无声段落，减少转录时间和噪音

    返回处理后的临时文件路径，失败时返回 None（使用原始文件继续）。
    """
    if not USE_VAD:
        return None

    log(f"  VAD 静音剥离中: {audio_path.name}")
    try:
        import numpy as np
        import torch

        # ffmpeg 转为 16kHz mono WAV（Silero VAD 要求的格式）
        tmp_wav = audio_path.with_suffix(".vad-input.wav")
        result = subprocess.run(
            ["ffmpeg", "-i", str(audio_path), "-ar", "16000", "-ac", "1",
             "-f", "wav", str(tmp_wav), "-y"],
            capture_output=True, timeout=120,
        )
        if result.returncode != 0:
            log("  ⚠ VAD: ffmpeg 转换失败，跳过")
            return None

        # 读取 WAV 数据（跳过 44 字节 header）
        raw = np.fromfile(str(tmp_wav), dtype=np.int16)[22:]  # 简易跳过 header
        audio_tensor = torch.from_numpy(raw.copy()).float() / 32768.0

        # 加载 Silero VAD 模型
        model, utils = torch.hub.load(
            "snakers4/silero-vad", "silero_vad",
            trust_repo=True, verbose=False,
        )
        get_speech_timestamps = utils[0]

        # 检测语音段
        speech_timestamps = get_speech_timestamps(
            audio_tensor, model, sampling_rate=16000,
        )

        if not speech_timestamps:
            log("  ⚠ VAD: 未检测到语音，跳过")
            tmp_wav.unlink(missing_ok=True)
            return None

        # 拼接有声段
        chunks = []
        for ts in speech_timestamps:
            chunks.append(audio_tensor[ts["start"]:ts["end"]])
        cleaned = torch.cat(chunks)

        # 写入临时 WAV
        tmp_cleaned = audio_path.with_suffix(".vad-cleaned.wav")
        import wave
        with wave.open(str(tmp_cleaned), "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes((cleaned * 32768).to(torch.int16).numpy().tobytes())

        # 清理中间文件
        tmp_wav.unlink(missing_ok=True)

        original_sec = len(audio_tensor) / 16000
        cleaned_sec = len(cleaned) / 16000
        log(f"  VAD 完成: {original_sec:.0f}s → {cleaned_sec:.0f}s "
            f"(去掉 {original_sec - cleaned_sec:.0f}s 静音)")
        return tmp_cleaned

    except Exception as e:
        log(f"  ⚠ VAD 失败 ({e})，使用原始文件继续")
        # 清理可能残留的临时文件
        for suffix in (".vad-input.wav", ".vad-cleaned.wav"):
            audio_path.with_suffix(suffix).unlink(missing_ok=True)
        return None


def transcribe(audio_path: Path) -> str:
    """用 Vibe (sona CLI / whisper.cpp + CoreML) 本地转录"""
    log(f"  Vibe 转录中: {audio_path.name}")

    if not SONA_CLI.exists():
        raise RuntimeError(f"Vibe CLI 不存在: {SONA_CLI}")
    if not SONA_MODEL.exists():
        raise RuntimeError(f"Whisper 模型不存在: {SONA_MODEL}")

    result = subprocess.run(
        [str(SONA_CLI), "transcribe", str(SONA_MODEL), str(audio_path),
         "--language", WHISPER_LANGUAGE],
        capture_output=True, text=True, timeout=1800,  # 30 分钟超时
    )

    if result.returncode != 0:
        raise RuntimeError(f"转录失败: {result.stderr.strip()}")

    text = result.stdout.strip()
    log(f"  转录完成: {len(text)} 字")
    return text


def classify(transcript: str) -> str:
    """用 Claude CLI 判断录音类型：meeting / content / memo"""
    log("  分类中...")
    snippet = transcript[:1500]
    prompt = (
        "根据以下转录内容，判断录音类型。只回答一个词：\n"
        "- meeting（多人会议/讨论）\n"
        "- content（播客/讲座/课堂/新闻评论）\n"
        "- memo（个人语音备忘/想法）\n\n"
        f"转录内容：\n{snippet}"
    )
    try:
        result = subprocess.run(
            ["claude", "-p", "-"],
            input=prompt,
            capture_output=True, text=True,
            timeout=30,
        )
        if result.returncode == 0:
            answer = result.stdout.strip().lower()
            for scene in ("meeting", "content", "memo"):
                if scene in answer:
                    log(f"  分类结果: {scene}")
                    return scene
    except (subprocess.TimeoutExpired, Exception) as e:
        log(f"  ⚠ 分类失败: {e}")

    log("  分类结果: memo (fallback)")
    return "memo"


def generate_title(transcript: str) -> str:
    """用 Claude CLI 生成语义化标题"""
    log("  生成标题中...")
    snippet = transcript[:2000]
    prompt = (
        "根据以下转录内容生成一个简洁的中文标题（最多 30 字）。\n"
        "只输出标题本身，不要引号、冒号或解释。\n\n"
        f"转录内容：\n{snippet}"
    )
    try:
        result = subprocess.run(
            ["claude", "-p", "-"],
            input=prompt,
            capture_output=True, text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            title = result.stdout.strip()
            # 清洗：去掉引号、冒号、换行
            title = title.split("\n")[0]
            for ch in '"\'「」《》:：':
                title = title.replace(ch, "")
            title = title.strip()
            if title:
                log(f"  标题: {title[:50]}")
                return title[:50]
    except (subprocess.TimeoutExpired, Exception) as e:
        log(f"  ⚠ 标题生成失败: {e}")

    return ""


def summarize(transcript: str, source_name: str, scene: str = "memo") -> str:
    """用 Claude CLI 生成总结"""
    log(f"  Claude CLI 总结中 (场景: {scene})...")

    # 长度保护
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        log(f"  ⚠ 转录文本过长 ({len(transcript)} 字)，截断到 {MAX_TRANSCRIPT_CHARS}")
        transcript = transcript[:MAX_TRANSCRIPT_CHARS] + "\n\n[... 转录内容过长，已截断 ...]"

    # 按场景选 prompt
    prompt_file = PROMPTS / f"{scene}.md"
    if not prompt_file.exists():
        prompt_file = PROMPTS / "memo.md"
    if prompt_file.exists():
        system_prompt = prompt_file.read_text()
    else:
        system_prompt = DEFAULT_SUMMARY_PROMPT

    full_prompt = f"{system_prompt}\n\n## 转录内容\n\n{transcript}"

    for attempt in range(1, CLAUDE_MAX_RETRIES + 1):
        try:
            result = subprocess.run(
                ["claude", "-p", "-"],
                input=full_prompt,
                capture_output=True, text=True,
                timeout=CLAUDE_TIMEOUT,
            )
            if result.returncode == 0 and result.stdout.strip():
                log(f"  总结完成 (第 {attempt} 次)")
                return result.stdout.strip()
            log(f"  ⚠ Claude 返回为空 (第 {attempt} 次)")
        except subprocess.TimeoutExpired:
            log(f"  ⚠ Claude 超时 (第 {attempt} 次)")
        except Exception as e:
            log(f"  ⚠ Claude 错误: {e} (第 {attempt} 次)")

        if attempt < CLAUDE_MAX_RETRIES:
            time.sleep(5)

    return ""


def detect_note_type(summary: str) -> str:
    """从总结标题判断笔记类型"""
    first_line = summary.split("\n")[0].lower()
    if "会议纪要" in first_line:
        return "meeting"
    if "内容整理" in first_line:
        return "content"
    return "memo"


def write_obsidian_note(summary: str, transcript: str, source_name: str,
                        duration_sec: float, title: str = "",
                        scene: str = "memo") -> Path:
    """写入 Obsidian Vault"""
    today = datetime.now().strftime("%Y-%m-%d")
    duration_min = int(duration_sec / 60)

    # 语义化标题 → 文件名；失败时 fallback 到原始文件名
    if title:
        safe_name = "".join(
            c for c in title.replace(" ", "-").replace("/", "-")
            if c.isalnum() or c in "-_" or "\u4e00" <= c <= "\u9fff"
        ).lower()
    if not title or not safe_name:
        safe_name = source_name.rsplit(".", 1)[0].replace(" ", "-").lower()

    note_name = f"{today}-{safe_name}.md"
    note_path = OBSIDIAN_OUTPUT / note_name

    # 避免文件名冲突
    counter = 1
    while note_path.exists():
        note_name = f"{today}-{safe_name}-{counter}.md"
        note_path = OBSIDIAN_OUTPUT / note_name
        counter += 1

    note_type = scene if scene in ("meeting", "content", "memo") else "memo"
    type_tag = {"meeting": "会议", "content": "内容", "memo": "备忘"}[note_type]

    frontmatter = f"""---
date: {today}
source: {source_name}
duration: {duration_min}min
type: {note_type}
tags:
  - 录音笔记
  - 录音笔记/{type_tag}
  - auto-transcribe
---"""

    content = f"{frontmatter}\n\n{summary}\n\n---\n\n## 完整转录\n\n{transcript}"
    note_path.write_text(content)
    log(f"  Obsidian 笔记: {note_path.name} (类型: {type_tag})")
    return note_path


# ── 默认 Prompt ──────────────────────────────────
# TODO: Jared 来定义总结风格，写到 prompts/meeting.md
DEFAULT_SUMMARY_PROMPT = """请将以下会议/录音转录整理成结构化的会议纪要。

要求：
1. 用中文输出
2. 格式如下：

# 会议纪要：[根据内容拟定标题]

## 背景
简要说明会议/录音的背景和目的（1-2 句话）

## 讨论内容
### 议题 1
...
### 议题 2
...
（按实际内容分议题整理）

## 决策
- 列出所有明确的决策

## 行动项
- [ ] 谁做什么，截止日期（如果提到的话）

注意：
- 保留关键数字、人名、日期
- 如果是个人语音备忘，改用「想法整理」格式，不需要行动项
- 如果有中英马混合语言，统一用中文整理，专有名词保留英文"""


# ── 主流程 ────────────────────────────────────────

def process_file(path: Path, db: dict) -> bool:
    """处理单个文件，返回是否成功"""
    source_name = path.name
    log(f"处理: {source_name}")

    # 移到 processing
    proc_path = PROCESSING / source_name
    shutil.move(str(path), str(proc_path))

    audio_path = proc_path
    extracted_audio = None

    try:
        # 视频 → 音频
        if proc_path.suffix.lower() in VIDEO_EXTENSIONS:
            audio_path = extract_audio(proc_path)
            extracted_audio = audio_path

        # 获取时长
        duration = get_audio_duration(audio_path)
        log(f"  时长: {int(duration/60)}分{int(duration%60)}秒")

        # VAD 静音剥离（可选）
        vad_cleaned = strip_silence(audio_path)
        transcribe_path = vad_cleaned or audio_path

        # 转录
        transcript = transcribe(transcribe_path)

        # 清理 VAD 临时文件
        if vad_cleaned and vad_cleaned.exists():
            vad_cleaned.unlink()
        if not transcript:
            raise RuntimeError("转录结果为空")

        # 保存转录文本
        transcript_path = TRANSCRIPTS / f"{source_name}.txt"
        transcript_path.write_text(transcript)

        # 分类
        scene = classify(transcript)

        # 生成语义化标题
        title = generate_title(transcript)

        # Claude 总结
        summary = summarize(transcript, source_name, scene)
        if not summary:
            # 总结失败但转录成功，仍然保存转录结果到 Obsidian
            log("  ⚠ 总结失败，仅保存转录结果")
            summary = "# 转录记录（总结失败）\n\n> Claude 总结失败，以下是原始转录"

        # 写入 Obsidian
        note_path = write_obsidian_note(
            summary, transcript, source_name, duration,
            title=title, scene=scene,
        )

        # 清理提取的音频
        if extracted_audio and extracted_audio.exists():
            extracted_audio.unlink()

        # 移到 done（处理同名冲突）
        done_path = DONE / source_name
        if done_path.exists():
            stem = proc_path.stem
            suffix = proc_path.suffix
            done_path = DONE / f"{stem}-{int(time.time())}{suffix}"
        shutil.move(str(proc_path), str(done_path))

        # 记录已处理
        db[source_name] = {
            "hash": file_hash(done_path),
            "processed_at": datetime.now().isoformat(),
            "note": str(note_path.name),
        }
        save_processed(db)

        notify("转录完成", f"{source_name} → {note_path.name}")
        log(f"✓ 完成: {source_name}")
        return True

    except Exception as e:
        log(f"✗ 失败: {source_name} — {e}")

        # 移到 failed（处理同名冲突）
        if proc_path.exists():
            fail_path = FAILED / source_name
            if fail_path.exists():
                stem = proc_path.stem
                suffix = proc_path.suffix
                fail_path = FAILED / f"{stem}-{int(time.time())}{suffix}"
            shutil.move(str(proc_path), str(fail_path))

        # 清理提取的音频
        if extracted_audio and extracted_audio.exists():
            extracted_audio.unlink()

        notify("转录失败", f"{source_name}: {e}")
        return False


def main():
    log("=== Auto-Transcribe 启动 ===")

    # 确保目录存在
    for d in [INBOX, PROCESSING, DONE, FAILED, TRANSCRIPTS, LOGS, PROMPTS,
              OBSIDIAN_OUTPUT]:
        d.mkdir(parents=True, exist_ok=True)

    # 进程锁：防止 launchd 同时启动多个实例
    lock_file = open(BASE_DIR / ".process.lock", "a")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("⚠ 另一个实例正在运行，退出")
        lock_file.close()
        return

    db = load_processed()
    files_found = 0
    files_ok = 0

    # 扫描 inbox
    for f in sorted(INBOX.iterdir()):
        if f.name.startswith("."):
            continue
        if "/" in f.name or "\\" in f.name or ".." in f.name:
            log(f"跳过（可疑文件名）: {f.name}")
            continue
        if f.suffix.lower() not in ALL_EXTENSIONS:
            continue
        if f.name in db:
            log(f"跳过（已处理）: {f.name}")
            continue

        # 等文件就绪
        if not wait_for_file_ready(f):
            log(f"跳过（文件未就绪）: {f.name}")
            continue

        files_found += 1
        if process_file(f, db):
            files_ok += 1

    log(f"=== 完成: {files_ok}/{files_found} 个文件处理成功 ===")


if __name__ == "__main__":
    main()

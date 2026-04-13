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
import re
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
from status_db import add_job, update_job, mark_stale_jobs

_cfg = load_config()

BASE_DIR = _cfg.base_dir
INBOX = BASE_DIR / "inbox"
PROCESSING = BASE_DIR / "processing"
DONE = BASE_DIR / "done"
FAILED = BASE_DIR / "failed"
TRANSCRIPTS = BASE_DIR / "transcripts"
LOGS = BASE_DIR / "logs"
PROMPTS = BASE_DIR / "prompts"

OBSIDIAN_OUTPUT = _cfg.obsidian_output          # recording-notes（录音）
SOCIAL_OUTPUT = _cfg.captures_output             # social-captures（社交媒体）
PROCESSED_DB = BASE_DIR / "processed.json"

SONA_CLI = _cfg.sona_cli
SONA_MODEL = _cfg.sona_model
WHISPER_LANGUAGE = _cfg.whisper_language

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".ts", ".3gp"}
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".flac", ".ogg", ".aac", ".opus", ".wma"}
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


def notify_telegram(chat_id: int, text: str) -> None:
    """转录完成后通知 Telegram 用户（静默失败，不影响主流程）"""
    token = _cfg.telegram_bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token or not chat_id:
        return
    try:
        import httpx
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception as e:
        log(f"  ⚠ Telegram 通知失败: {e}")


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

def wait_for_file_ready(path: Path) -> Path | None:
    """等待文件下载完成，返回就绪的路径（可能因改名而变化），失败返回 None。

    处理两种场景：
    1. iCloud 同步 — 文件大小逐渐增长，等稳定
    2. OmniGet 下载 — 文件可能被改名（如 .m4a → .mp3），需要追踪新路径
    """
    log(f"  等待文件就绪: {path.name}")
    prev_size = -1
    stable_count = 0
    disappeared_count = 0

    for _ in range(60):  # 最多等 60 秒
        if not path.exists():
            disappeared_count += 1
            if disappeared_count <= 5:
                # 文件消失，可能正在被改名，搜索同名不同后缀的文件
                candidates = [
                    c for c in path.parent.glob(f"{path.stem}.*")
                    if c.suffix.lower() in ALL_EXTENSIONS and c.is_file()
                ]
                if len(candidates) == 1:
                    path = candidates[0]
                    log(f"  文件已改名: → {path.name}")
                    prev_size = -1
                    stable_count = 0
                    disappeared_count = 0
                    continue
                time.sleep(FILE_STABLE_INTERVAL)
                continue
            # 消失超过 5 秒，真的没了
            log(f"  ⚠ 文件已消失: {path.name}")
            return None

        disappeared_count = 0
        size = path.stat().st_size
        if size == prev_size and size > 0:
            stable_count += 1
            if stable_count >= FILE_STABLE_SECONDS:
                return path
        else:
            stable_count = 0
        prev_size = size
        time.sleep(FILE_STABLE_INTERVAL)

    log(f"  ⚠ 文件等待超时: {path.name}")
    return None


def extract_audio(video_path: Path) -> Path:
    """从视频提取音频（stream copy 优先，不兼容时回退到 AAC 重编码）"""
    audio_path = video_path.with_suffix(".m4a")
    log(f"  ffmpeg 提取音频: {video_path.name} → {audio_path.name}")

    # 尝试 stream copy（快，无损）
    result = subprocess.run(
        ["ffmpeg", "-i", str(video_path), "-vn", "-acodec", "copy",
         str(audio_path), "-y"],
        capture_output=True,
    )
    if result.returncode == 0:
        return audio_path

    # 回退：重编码为 AAC（兼容 webm/opus/flac 等来源）
    log("  stream copy 失败，重编码为 AAC...")
    result = subprocess.run(
        ["ffmpeg", "-i", str(video_path), "-vn", "-acodec", "aac",
         "-b:a", "128k", str(audio_path), "-y"],
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

        # 用 wave 模块正确读取 WAV 数据（支持任意长度 header）
        import wave as wave_mod
        with wave_mod.open(str(tmp_wav), "rb") as wf:
            raw = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
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


# ── YouTube 字幕提取 ──────────────────────────────

def fetch_youtube_transcript(url: str, language: str | None = None) -> str | None:
    """用 youtube-transcript-api 抓 YouTube 字幕，成功返回文本，失败返回 None"""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        # 从 URL 提取 video_id
        parsed = __import__("urllib.parse", fromlist=["urlparse", "parse_qs"])
        u = parsed.urlparse(url)
        if "youtu.be" in (u.hostname or ""):
            video_id = u.path.lstrip("/")
        else:
            video_id = parsed.parse_qs(u.query).get("v", [""])[0]

        if not video_id:
            return None

        log(f"  YouTube 字幕提取: {video_id}")

        # 优先语言：用户指定 > zh > en > 任意
        lang_prefs = []
        if language and language not in ("auto",):
            lang_prefs.append(language)
        lang_prefs.extend(["zh-Hans", "zh-Hant", "zh", "en"])

        ytt = YouTubeTranscriptApi()
        transcript_data = ytt.fetch(video_id, languages=lang_prefs)
        # 带时间戳格式：[MM:SS] 文本
        lines = []
        for entry in transcript_data:
            ts = _seconds_to_mmss(entry.start)
            lines.append(f"[{ts}] {entry.text}")
        text = "\n".join(lines)

        if len(text) < 50:
            log("  字幕内容过短，跳过")
            return None

        log(f"  YouTube 字幕成功: {len(text)} 字")
        return text

    except Exception as e:
        log(f"  YouTube 字幕失败 ({e})，回退 Whisper 转录")
        return None


CHUNK_MINUTES = 360  # 6 小时以上才分段（sona/whisper.cpp 能自己处理长音频）
CHUNK_TIMEOUT = 7200  # 每段转录超时（秒）


def split_audio(audio_path: Path, chunk_minutes: int = CHUNK_MINUTES) -> list[Path]:
    """用 ffmpeg 把长音频切成等长小段"""
    duration = get_audio_duration(audio_path)
    chunk_sec = chunk_minutes * 60

    if duration <= chunk_sec:
        return []

    total_chunks = int(duration // chunk_sec) + (1 if duration % chunk_sec > 0 else 0)
    log(f"  音频过长 ({int(duration/60)}分钟)，切成 {total_chunks} 段（每段 {chunk_minutes} 分钟）")

    chunks = []
    for i in range(total_chunks):
        start = i * chunk_sec
        chunk_path = audio_path.with_suffix(f".chunk{i}.wav")
        result = subprocess.run(
            ["ffmpeg", "-i", str(audio_path), "-ss", str(start),
             "-t", str(chunk_sec),
             str(chunk_path), "-y"],
            capture_output=True, timeout=120,
        )
        if result.returncode == 0 and chunk_path.exists():
            chunks.append(chunk_path)
        else:
            log(f"  ⚠ 切割第 {i+1} 段失败")

    return chunks


def _build_sona_cmd(audio: Path, language: str | None) -> list[str]:
    """构建 sona 转录命令"""
    cmd = [str(SONA_CLI), "transcribe", str(SONA_MODEL), str(audio)]
    lang = language or WHISPER_LANGUAGE
    # auto/zh 都用 zh — Whisper zh 模式会自动保留英文原文
    if lang == "auto":
        lang = "zh"
    cmd.extend(["--language", lang])
    return cmd


def _seconds_to_mmss(seconds: float) -> str:
    """秒数 → MM:SS 格式"""
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def add_estimated_timestamps(text: str, duration_sec: float) -> str:
    """给纯文本转录加上估算时间戳

    按句子分段，根据字符数占比等比分配时间。
    不是精确的，但足够让读者快速定位。
    """
    if duration_sec <= 0:
        return text

    # 按句号/问号/感叹号/换行分段
    segments = re.split(r'(?<=[。！？.!?\n])', text)
    segments = [s.strip() for s in segments if s.strip()]

    if not segments:
        return text

    # 按字符数占比分配时间
    total_chars = sum(len(s) for s in segments)
    if total_chars == 0:
        return text

    lines = []
    current_sec = 0.0
    for seg in segments:
        ts = _seconds_to_mmss(current_sec)
        lines.append(f"[{ts}] {seg}")
        current_sec += (len(seg) / total_chars) * duration_sec

    return "\n".join(lines)


def transcribe(audio_path: Path, language: str | None = None) -> str:
    """用 Vibe (sona CLI / whisper.cpp + CoreML) 本地转录

    language: None=用全局配置, "auto"=自动检测, "en"/"zh"等=指定语言
    """
    if not SONA_CLI.exists():
        raise RuntimeError(f"Vibe CLI 不存在: {SONA_CLI}")
    if not SONA_MODEL.exists():
        raise RuntimeError(f"Whisper 模型不存在: {SONA_MODEL}")

    lang_label = language or WHISPER_LANGUAGE
    log(f"  Vibe 转录中: {audio_path.name} (语言={lang_label})")

    # 长音频分段转录
    chunks = split_audio(audio_path)
    if chunks:
        texts = []
        for i, chunk in enumerate(chunks):
            log(f"  转录第 {i+1}/{len(chunks)} 段...")
            try:
                result = subprocess.run(
                    _build_sona_cmd(chunk, language),
                    capture_output=True, text=True, timeout=CHUNK_TIMEOUT,
                )
                if result.returncode == 0 and result.stdout.strip():
                    texts.append(result.stdout.strip())
                else:
                    log(f"  ⚠ 第 {i+1} 段转录为空")
            except subprocess.TimeoutExpired:
                log(f"  ⚠ 第 {i+1} 段转录超时，跳过")
            finally:
                chunk.unlink(missing_ok=True)

        if not texts:
            raise RuntimeError("所有分段转录均失败")

        text = "\n\n".join(texts)
        log(f"  转录完成: {len(text)} 字 ({len(chunks)} 段合并)")
        return text

    # 短音频直接转录
    result = subprocess.run(
        _build_sona_cmd(audio_path, language),
        capture_output=True, text=True, timeout=CHUNK_TIMEOUT,
    )

    if result.returncode != 0:
        raise RuntimeError(f"转录失败: {result.stderr.strip()}")

    text = result.stdout.strip()
    log(f"  转录完成: {len(text)} 字")
    return text


def check_transcript_quality(transcript: str, duration_sec: float) -> tuple[bool, str]:
    """检查转录质量，返回 (是否合格, 原因)"""
    # 1. 过短：5 分钟音频应至少有 200 字
    expected_min_chars = max(50, int(duration_sec / 60) * 40)
    if len(transcript) < expected_min_chars:
        return (False, f"过短: {len(transcript)} 字 (期望 ≥{expected_min_chars})")

    # 2. 重复句子：whisper 乱码时经常重复同一句
    sentences = [s.strip() for s in re.split(r'[。！？\n.!?]', transcript) if len(s.strip()) > 5]
    if sentences:
        from collections import Counter
        counts = Counter(sentences)
        most_common_count = counts.most_common(1)[0][1]
        repeat_ratio = most_common_count / len(sentences)
        if repeat_ratio > 0.3 and most_common_count > 3:
            return (False, f"重复句子: 最多重复 {most_common_count} 次, 占 {repeat_ratio:.0%}")

    return (True, "")


VALID_SCENES = (
    "meeting", "content", "memo", "1on1", "class", "call",
    "daylog", "discussion", "video", "video-short", "video-long", "ad",
)
TYPE_TAGS = {
    "meeting": "会议", "content": "内容", "memo": "备忘",
    "1on1": "一对一", "class": "课堂", "call": "通话",
    "daylog": "日志", "discussion": "研讨", "video": "视频",
    "video-short": "短视频", "video-long": "长视频", "ad": "广告",
}

# 社交媒体视频的 prompt 路由阈值（秒）
SHORT_VIDEO_THRESHOLD = 300  # 5 分钟


def select_prompt(platform: str, duration_sec: float, is_ad: bool) -> str | None:
    """社交媒体视频 → 选 prompt 模板名；录音 → 返回 None（走旧分类）

    返回值对应 prompts/ 目录下的文件名（不含 .md）。
    """
    if not platform:
        return None  # 录音来源，让 Claude 分类

    if is_ad:
        return "ad"

    if duration_sec < SHORT_VIDEO_THRESHOLD:
        return "video-short"
    return "video-long"


def classify(transcript: str) -> str:
    """用 Claude CLI 判断录音类型"""
    log("  分类中...")
    snippet = transcript[:1500]
    prompt = (
        "根据以下转录内容，判断录音类型。只回答一个词：\n"
        "- meeting（多人会议/讨论）\n"
        "- content（播客/讲座/新闻评论）\n"
        "- memo（个人语音备忘/想法）\n"
        "- 1on1（一对一谈话/面谈）\n"
        "- class（课堂教学/授课）\n"
        "- call（电话通话）\n"
        "- daylog（一天的杂项录音/日志）\n"
        "- discussion（深度讨论/研讨/辩论）\n\n"
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
            for scene in VALID_SCENES:
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


def analyze_with_claude(
    transcript: str, source_name: str, scene: str | None = None,
) -> tuple[str, str, str]:
    """一次 Claude 调用完成分类+标题+总结，返回 (scene, title, summary)"""

    # 长度保护
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        log(f"  ⚠ 转录文本过长 ({len(transcript)} 字)，截断到 {MAX_TRANSCRIPT_CHARS}")
        transcript = transcript[:MAX_TRANSCRIPT_CHARS] + "\n\n[... 转录内容过长，已截断 ...]"

    # 按场景选 prompt
    need_classify = scene is None
    if need_classify:
        scene = "memo"  # 默认，会被 Claude 返回值覆盖

    prompt_file = PROMPTS / f"{scene}.md"
    if not prompt_file.exists():
        prompt_file = PROMPTS / "memo.md"
    if prompt_file.exists():
        scene_prompt = prompt_file.read_text()
    else:
        scene_prompt = DEFAULT_SUMMARY_PROMPT

    # 构建合并 prompt
    parts = []
    if need_classify:
        valid = ", ".join(VALID_SCENES)
        parts.append(
            f"首先，判断这段录音的类型，从以下选项中选一个：{valid}\n"
            "在第一行输出：===SCENE=== 类型名\n\n"
        )
    parts.append("然后，生成一个简洁的中文标题（最多 30 字）。\n")
    parts.append("在单独一行输出：===TITLE=== 标题内容\n\n")
    parts.append("最后，按以下要求整理内容：\n\n")
    parts.append(scene_prompt)
    parts.append(f"\n\n## 转录内容\n\n{transcript}")

    full_prompt = "".join(parts)
    input_chars = len(full_prompt)
    log(f"  Claude CLI 合并分析中 (场景: {scene}, 输入: {input_chars} 字)...")

    for attempt in range(1, CLAUDE_MAX_RETRIES + 1):
        try:
            result = subprocess.run(
                ["claude", "-p", "-"],
                input=full_prompt,
                capture_output=True, text=True,
                timeout=CLAUDE_TIMEOUT,
            )
            if result.returncode == 0 and result.stdout.strip():
                output = result.stdout.strip()
                output_chars = len(output)
                # 粗略估算 token（中英混合约 2 字符/token）
                est_input = input_chars // 2
                est_output = output_chars // 2
                log(f"  Token 估算: ~{est_input} input + ~{est_output} output = ~{est_input + est_output} total")
                return _parse_claude_output(output, scene, need_classify)
            log(f"  ⚠ Claude 返回为空 (第 {attempt} 次)")
        except subprocess.TimeoutExpired:
            log(f"  ⚠ Claude 超时 (第 {attempt} 次)")
        except Exception as e:
            log(f"  ⚠ Claude 错误: {e} (第 {attempt} 次)")

        if attempt < CLAUDE_MAX_RETRIES:
            time.sleep(5)

    return (scene, "", "")


def _parse_claude_output(
    output: str, default_scene: str, need_classify: bool,
) -> tuple[str, str, str]:
    """从合并输出中解析 scene, title, summary"""
    scene = default_scene
    title = ""
    summary_lines = []

    for line in output.splitlines():
        if line.startswith("===SCENE==="):
            raw = line.split("===SCENE===", 1)[1].strip().lower()
            for s in VALID_SCENES:
                if s in raw:
                    scene = s
                    break
            continue
        if line.startswith("===TITLE==="):
            title = line.split("===TITLE===", 1)[1].strip()
            # 清洗标题
            for ch in '"\'「」《》:：':
                title = title.replace(ch, "")
            title = title.strip()[:50]
            continue
        if line.startswith("===META===") or line.startswith("===SUMMARY==="):
            continue  # 新模板的分隔符，跳过
        if line.startswith("content_type:") or line.startswith("intent:"):
            continue  # ===META=== 块里的字段，跳过
        summary_lines.append(line)

    summary = "\n".join(summary_lines).strip()
    if title:
        log(f"  标题: {title[:50]}")
    log(f"  分类: {scene}, 总结: {len(summary)} 字")
    return (scene, title, summary)


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
                        scene: str = "memo", source_url: str = "",
                        platform: str = "", is_ad: bool = False) -> Path:
    """写入 Obsidian Vault

    有 platform → social-captures/（社交媒体视频）
    无 platform → recording-notes/（个人录音）
    """
    today = datetime.now().strftime("%Y-%m-%d")
    duration_min = int(duration_sec / 60)

    # 语义化标题 → 文件名安全版
    safe_title = ""
    if title:
        safe_title = "".join(
            c for c in title.replace(" ", "-").replace("/", "-")
            if c.isalnum() or c in "-_" or "\u4e00" <= c <= "\u9fff"
        ).lower()
    if not safe_title:
        safe_title = source_name.rsplit(".", 1)[0].replace(" ", "-").lower()

    note_type = scene if scene in VALID_SCENES else "memo"
    type_tag = TYPE_TAGS.get(note_type, note_type)

    if platform:
        # 社交媒体：date-platform-[ad-]title.md → social-captures/
        ad_prefix = "ad-" if is_ad else ""
        note_name = f"{today}-{platform}-{ad_prefix}{safe_title}.md"
        output_dir = SOCIAL_OUTPUT
        tags = ["social-capture", platform, type_tag]
        frontmatter = (
            f"---\n"
            f"date: {today}\n"
            f"platform: {platform}\n"
            f"type: {note_type}\n"
            f"url: {source_url}\n"
            f"duration: {duration_min}min\n"
            f"tags: [{', '.join(tags)}]\n"
            f"---"
        )
    else:
        # 录音：date-type-title.md → recording-notes/
        note_name = f"{today}-{note_type}-{safe_title}.md"
        output_dir = OBSIDIAN_OUTPUT
        tags = ["recording-note", type_tag]
        url_line = f"\nurl: {source_url}" if source_url else ""
        frontmatter = (
            f"---\n"
            f"date: {today}\n"
            f"source: {source_name}{url_line}\n"
            f"duration: {duration_min}min\n"
            f"type: {note_type}\n"
            f"tags: [{', '.join(tags)}]\n"
            f"---"
        )

    note_path = output_dir / note_name

    # 避免文件名冲突
    counter = 1
    while note_path.exists():
        stem = note_name.rsplit(".md", 1)[0]
        note_path = output_dir / f"{stem}-{counter}.md"
        counter += 1

    content = f"{frontmatter}\n\n{summary}\n\n---\n\n## 完整转录\n\n{transcript}"
    note_path.write_text(content)
    log(f"  Obsidian 笔记: {note_path.name} → {output_dir.name}/")
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

    # 读取 sidecar .meta 文件（Telegram bot 写入的元数据）
    meta_sidecar = Path(str(path) + ".meta")
    override_language: str | None = None
    source_url: str = ""
    source_platform: str = ""
    chat_id: int = 0
    is_ad: bool = False
    if meta_sidecar.exists():
        try:
            import json as _json
            meta = _json.loads(meta_sidecar.read_text())
            override_language = meta.get("language") or None
            source_url = meta.get("url", "")
            source_platform = meta.get("platform", "")
            chat_id = meta.get("chat_id", 0)
            is_ad = meta.get("is_ad", False)
            ad_label = " [广告]" if is_ad else ""
            log(f"  meta sidecar: 语言={override_language}, 平台={source_platform}{ad_label}")
        except Exception:
            pass
        meta_sidecar.unlink()
    # 兼容旧的 .lang sidecar
    lang_sidecar = Path(str(path) + ".lang")
    if lang_sidecar.exists() and not override_language:
        override_language = lang_sidecar.read_text().strip().lower() or None
        lang_sidecar.unlink()

    # 注册到状态数据库
    job_id = add_job(source_name)

    # 移到 processing（处理同名冲突）
    proc_path = PROCESSING / source_name
    if proc_path.exists():
        proc_path = PROCESSING / f"{path.stem}-{int(time.time())}{path.suffix}"
        source_name = proc_path.name
    shutil.move(str(path), str(proc_path))

    audio_path = proc_path
    extracted_audio = None

    try:
        # 视频 → 音频
        if proc_path.suffix.lower() in VIDEO_EXTENSIONS:
            update_job(job_id, "extracting")
            notify("开始处理", f"提取音频: {source_name}")
            audio_path = extract_audio(proc_path)
            extracted_audio = audio_path

        # 获取时长
        duration = get_audio_duration(audio_path)
        update_job(job_id, "transcribing", duration_sec=duration)
        log(f"  时长: {int(duration/60)}分{int(duration%60)}秒")
        notify("转录中", f"{source_name} ({int(duration/60)}分钟)")

        # YouTube 字幕优先（比 Whisper 更快更准）
        transcript = None
        vad_cleaned = None
        if source_platform == "youtube" and source_url:
            transcript = fetch_youtube_transcript(source_url, override_language)

        if not transcript:
            # VAD 静音剥离（可选）
            vad_cleaned = strip_silence(audio_path)
            transcribe_path = vad_cleaned or audio_path

            # 转录
            transcript = transcribe(transcribe_path, language=override_language)

        # 清理 VAD 临时文件
        if vad_cleaned and vad_cleaned.exists():
            vad_cleaned.unlink()
        if not transcript:
            raise RuntimeError("转录结果为空")

        # 质量检测 + 语言重试
        quality_ok, quality_reason = check_transcript_quality(transcript, duration)
        if not quality_ok:
            retry_lang = "en" if (override_language or WHISPER_LANGUAGE) in ("zh", "auto") else "zh"
            log(f"  ⚠ 转录质量差 ({quality_reason})，用 {retry_lang} 重试...")
            retry_transcript = transcribe(transcribe_path, language=retry_lang)
            if retry_transcript:
                retry_ok, _ = check_transcript_quality(retry_transcript, duration)
                if retry_ok or len(retry_transcript) > len(transcript):
                    transcript = retry_transcript
                    log(f"  重试成功，采用 {retry_lang} 转录结果")

        # 给 Claude 用的纯文本（不带时间戳，避免干扰分析）
        transcript_plain = transcript
        # YouTube 字幕自带 [MM:SS] 时间戳，strip 掉给 Claude
        if source_platform == "youtube" and transcript.startswith("["):
            transcript_plain = re.sub(r'\[\d{2}:\d{2}\]\s*', '', transcript)

        # 保存转录文本（带时间戳版本）
        transcript_with_ts = transcript
        # sona 转录是纯文本，加上估算时间戳
        if not transcript.startswith("["):
            transcript_with_ts = add_estimated_timestamps(transcript, duration)

        transcript_path = TRANSCRIPTS / f"{source_name}.txt"
        transcript_path.write_text(transcript_with_ts)

        # 合并分析：1 次 Claude 调用完成分类+标题+总结
        update_job(job_id, "summarizing")
        notify("AI 分析中", f"{source_name} → Claude 分析")
        # 社交媒体视频：按 platform + duration + is_ad 选 prompt
        # 录音：select_prompt 返回 None，让 Claude 自己分类
        preset_scene = select_prompt(source_platform, duration, is_ad)
        scene, title, summary = analyze_with_claude(
            transcript_plain, source_name, scene=preset_scene,
        )
        if not summary:
            log("  ⚠ 总结失败，仅保存转录结果")
            summary = "# 转录记录（总结失败）\n\n> Claude 总结失败，以下是原始转录"

        # 写入 Obsidian（完整转录用带时间戳版本）
        update_job(job_id, "saving")
        note_path = write_obsidian_note(
            summary, transcript_with_ts, source_name, duration,
            title=title, scene=scene, source_url=source_url,
            platform=source_platform, is_ad=is_ad,
        )

        # 清理提取的音频
        if extracted_audio and extracted_audio.exists():
            extracted_audio.unlink()

        # 记录已处理（先持久化再删文件，防崩溃丢记录）
        db[source_name] = {
            "hash": file_hash(proc_path),
            "processed_at": datetime.now().isoformat(),
            "note": str(note_path.name),
        }
        save_processed(db)

        # 删除原始文件（transcript + Obsidian 笔记已保存，不再留副本）
        proc_path.unlink()

        update_job(job_id, "done", note_name=str(note_path.name))
        notify("转录完成", f"{source_name} → {note_path.name}")

        # Telegram 来源：发送完成通知 + 摘要预览
        if chat_id:
            preview = summary[:300] + "..." if len(summary) > 300 else summary
            notify_telegram(chat_id, f"✅ 转录完成\n\n{preview}")

        log(f"✓ 完成: {source_name}")
        return True

    except Exception as e:
        update_job(job_id, "failed", error=str(e))
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
              OBSIDIAN_OUTPUT, SOCIAL_OUTPUT]:
        d.mkdir(parents=True, exist_ok=True)

    # 进程锁：防止 launchd 同时启动多个实例
    lock_file = open(BASE_DIR / ".process.lock", "a")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("⚠ 另一个实例正在运行，退出")
        lock_file.close()
        return

    try:
        # 崩溃恢复：processing/ 里的孤儿文件移回 inbox
        orphans = list(PROCESSING.iterdir())
        orphan_stems = {o.stem for o in orphans}
        for orphan in orphans:
            if orphan.name.startswith(".") or ".chunk" in orphan.name:
                # 删掉分段残留
                if ".chunk" in orphan.name:
                    orphan.unlink(missing_ok=True)
                continue
            # 如果是从视频提取的残留音频（同 stem 有视频文件在），直接删
            if (orphan.suffix.lower() in AUDIO_EXTENSIONS
                    and any(o.suffix.lower() in VIDEO_EXTENSIONS
                            and o.stem == orphan.stem for o in orphans)):
                log(f"清理提取残留: {orphan.name}")
                orphan.unlink(missing_ok=True)
                continue
            if orphan.suffix.lower() in ALL_EXTENSIONS:
                log(f"恢复孤儿文件: {orphan.name} → inbox/")
                dest = INBOX / orphan.name
                if dest.exists():
                    dest = INBOX / f"{orphan.stem}-recovered{orphan.suffix}"
                shutil.move(str(orphan), str(dest))

        # 清理超时的 status_db 记录
        mark_stale_jobs(timeout_minutes=30)

        db = load_processed()
        files_found = 0
        files_ok = 0

        # 扫描 inbox（含子文件夹，兼容 OmniGet 按平台分类）
        # 处理完成后重新扫描一次，捕获处理期间新到的文件
        for scan_round in range(2):
            if scan_round == 1:
                new_files = [
                    f for f in sorted(INBOX.rglob("*"))
                    if f.is_file() and not f.name.startswith(".")
                    and f.suffix.lower() in ALL_EXTENSIONS
                    and f.name not in db
                ]
                if not new_files:
                    break
                log(f"发现处理期间新到的 {len(new_files)} 个文件，重新扫描")

            for f in sorted(INBOX.rglob("*")):
                if f.is_dir():
                    continue
                if f.name.startswith("."):
                    continue
                if ".." in f.name:
                    log(f"跳过（可疑文件名）: {f.name}")
                    continue
                if f.suffix.lower() not in ALL_EXTENSIONS:
                    continue
                if f.name in db:
                    log(f"清理已处理文件: {f.name}")
                    f.unlink(missing_ok=True)
                    continue

                # 等文件就绪（返回可能已改名的路径）
                ready_path = wait_for_file_ready(f)
                if ready_path is None:
                    log(f"跳过（文件未就绪）: {f.name}")
                    continue

                files_found += 1
                if process_file(ready_path, db):
                    files_ok += 1

        log(f"=== 完成: {files_ok}/{files_found} 个文件处理成功 ===")
    finally:
        lock_file.close()


if __name__ == "__main__":
    main()

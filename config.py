#!/usr/bin/env python3
"""
配置加载模块 / Configuration loader

搜索顺序 / Search order:
1. ./config.yaml (项目目录)
2. ~/.config/auto-transcribe/config.yaml (用户目录)
3. 全用默认值 / Fall back to defaults
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Config:
    # 路径 / Paths
    base_dir: Path = field(default_factory=lambda: Path.home() / "auto-transcribe")
    obsidian_output: Path = field(
        default_factory=lambda: Path.home() / "Documents" / "Obsidian Vault" / "录音笔记"
    )

    # Whisper / Vibe
    sona_cli: Path = field(
        default_factory=lambda: Path("/Applications/vibe.app/Contents/MacOS/sona")
    )
    sona_model: Path = field(
        default_factory=lambda: (
            Path.home() / "Library" / "Application Support"
            / "github.com.thewh1teagle.vibe" / "ggml-large-v3-turbo.bin"
        )
    )
    whisper_language: str = "zh"

    # iCloud 同步
    icloud_inbox: Path = field(
        default_factory=lambda: (
            Path.home() / "Library" / "Mobile Documents"
            / "com~apple~CloudDocs" / "录音收件箱"
        )
    )

    # Voice Memos
    voice_memos_dir: Path = field(
        default_factory=lambda: (
            Path.home() / "Library" / "Application Support"
            / "com.apple.voicememos" / "Recordings"
        )
    )

    # Plaud (实验性 / experimental)
    plaud_client_id: str = ""
    plaud_secret_key: str = ""

    # 行为参数 / Behavior
    file_stable_seconds: int = 5
    claude_max_retries: int = 3
    claude_timeout: int = 1800
    max_transcript_chars: int = 80000  # ~20k tokens，防止超长录音打爆 Claude
    use_vad: bool = False  # Silero VAD 静音剥离（需要 onnxruntime）


def load_config() -> Config:
    """加载配置，找不到文件则用默认值"""
    candidates = [
        Path(__file__).resolve().parent / "config.yaml",
        Path.cwd() / "config.yaml",
        Path.home() / ".config" / "auto-transcribe" / "config.yaml",
    ]

    raw: dict = {}
    for path in candidates:
        if path.exists():
            raw = yaml.safe_load(path.read_text()) or {}
            break

    if not raw:
        return Config()

    # 路径字段需要展开 ~
    path_fields = {
        "base_dir", "obsidian_output", "sona_cli", "sona_model",
        "icloud_inbox", "voice_memos_dir",
    }

    kwargs = {}
    for key, value in raw.items():
        if key in path_fields and isinstance(value, str):
            kwargs[key] = Path(value).expanduser()
        else:
            kwargs[key] = value

    return Config(**kwargs)

#!/usr/bin/env python3
"""
Voice Memos 同步脚本
监听 Mac Voice Memos 目录 → 复制新录音到本地 inbox/

由 launchd WatchPaths 触发
"""

import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

from config import load_config

_cfg = load_config()

VOICE_MEMOS_DIR = _cfg.voice_memos_dir
LOCAL_INBOX = _cfg.base_dir / "inbox"
BASE_DIR = _cfg.base_dir
SYNCED_DB = BASE_DIR / "voicememos-synced.json"
PROCESSED_DB = BASE_DIR / "processed.json"

FILE_STABLE_SECONDS = _cfg.file_stable_seconds


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [voicememos] {msg}")


def load_synced() -> dict:
    if SYNCED_DB.exists():
        return json.loads(SYNCED_DB.read_text())
    return {}


def save_synced(db: dict) -> None:
    import os
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=SYNCED_DB.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(db, f, indent=2, ensure_ascii=False)
        os.replace(tmp, SYNCED_DB)
    except BaseException:
        os.unlink(tmp)
        raise


def wait_for_download(path: Path) -> bool:
    """等文件下载完成（iCloud 同步）"""
    prev_size = -1
    stable_count = 0
    for _ in range(120):
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
        time.sleep(1)
    return False


def initialize_existing(db: dict) -> dict:
    """首次运行时，把已有的录音标记为"已同步"，不处理旧文件"""
    if db.get("_initialized"):
        return db

    log("首次运行，标记已有录音...")
    count = 0
    for f in VOICE_MEMOS_DIR.glob("*.m4a"):
        if f.name not in db:
            db[f.name] = {
                "synced_at": "初始化跳过",
                "skipped": True,
            }
            count += 1

    db["_initialized"] = True
    save_synced(db)
    log(f"已跳过 {count} 个旧录音")
    return db


def main() -> None:
    log("扫描 Voice Memos...")

    if not VOICE_MEMOS_DIR.exists():
        log(f"Voice Memos 目录不存在: {VOICE_MEMOS_DIR}")
        return

    LOCAL_INBOX.mkdir(parents=True, exist_ok=True)
    db = load_synced()
    db = initialize_existing(db)

    # 加载已处理记录，避免重复同步
    processed = {}
    if PROCESSED_DB.exists():
        try:
            processed = json.loads(PROCESSED_DB.read_text())
        except (json.JSONDecodeError, ValueError):
            pass

    count = 0
    for f in sorted(VOICE_MEMOS_DIR.glob("*.m4a")):
        if f.name in db:
            continue
        if f.name in processed:
            log(f"  跳过（已处理过）: {f.name}")
            db[f.name] = {"synced_at": "已处理跳过", "skipped": True}
            save_synced(db)
            continue

        log(f"  发现新录音: {f.name}")

        if not wait_for_download(f):
            log(f"  ⚠ 文件未就绪，跳过: {f.name}")
            continue

        # 复制到 inbox
        dest = LOCAL_INBOX / f.name
        shutil.copy2(str(f), str(dest))

        db[f.name] = {
            "synced_at": datetime.now().isoformat(),
            "skipped": False,
        }
        save_synced(db)
        log(f"  ✓ 已复制到 inbox: {f.name}")
        count += 1

    log(f"同步完成: {count} 个新录音")


if __name__ == "__main__":
    main()

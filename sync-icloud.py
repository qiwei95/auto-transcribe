#!/usr/bin/env python3
"""
iCloud 录音收件箱同步脚本
监听 iCloud Drive/录音收件箱/ → 复制到本地 inbox/

由 launchd WatchPaths 触发
"""

import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

from config import load_config

_cfg = load_config()

ICLOUD_INBOX = _cfg.icloud_inbox
LOCAL_INBOX = _cfg.base_dir / "inbox"
EXTENSIONS = {".m4a", ".mp3", ".wav", ".mp4", ".mov", ".flac", ".ogg", ".aac"}

FILE_STABLE_SECONDS = _cfg.file_stable_seconds


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [icloud-sync] {msg}")


def is_icloud_placeholder(path: Path) -> bool:
    """检查是否是 iCloud 占位符（未下载的文件）"""
    return path.name.startswith(".") and path.name.endswith(".icloud")


def wait_for_download(path: Path) -> bool:
    """等待 iCloud 文件下载完成"""
    prev_size = -1
    stable_count = 0
    for _ in range(120):  # 最多等 2 分钟
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


def main() -> None:
    log("扫描 iCloud 录音收件箱...")

    if not ICLOUD_INBOX.exists():
        log(f"iCloud 文件夹不存在: {ICLOUD_INBOX}")
        return

    LOCAL_INBOX.mkdir(parents=True, exist_ok=True)
    count = 0

    for f in sorted(ICLOUD_INBOX.iterdir()):
        # 跳过隐藏文件和占位符
        if f.name.startswith("."):
            if is_icloud_placeholder(f):
                log(f"  跳过（iCloud 未下载）: {f.name}")
            continue

        if f.suffix.lower() not in EXTENSIONS:
            continue

        # 检查是否已处理过或已存在
        dest = LOCAL_INBOX / f.name
        if dest.exists():
            log(f"  跳过（已存在）: {f.name}")
            continue
        processed_db = _cfg.base_dir / "processed.json"
        if processed_db.exists():
            try:
                processed = json.loads(processed_db.read_text())
                if f.name in processed:
                    log(f"  跳过（已处理）: {f.name}")
                    continue
            except (json.JSONDecodeError, ValueError):
                pass

        # 强制触发 iCloud 下载并等待完成
        log(f"  触发 iCloud 下载: {f.name}")
        subprocess.run(
            ["brctl", "download", str(f)],
            capture_output=True, timeout=10,
        )

        log(f"  等待文件就绪: {f.name}")
        if not wait_for_download(f):
            log(f"  ⚠ 文件未就绪，跳过: {f.name}")
            continue

        # 复制到本地 inbox，验证大小一致
        shutil.copy2(str(f), str(dest))
        if dest.stat().st_size == 0 or dest.stat().st_size != f.stat().st_size:
            log(f"  ⚠ 复制后大小不一致，删除重试: {f.name}")
            dest.unlink()
            continue

        log(f"  ✓ 已复制到 inbox: {f.name} ({f.stat().st_size} bytes)")
        count += 1

    log(f"同步完成: {count} 个新文件")


if __name__ == "__main__":
    main()

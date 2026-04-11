#!/usr/bin/env python3
"""
Plaud.ai 录音拉取脚本 / Plaud.ai recording puller

⚠ WIP / 实验性功能 — 录音列表获取和下载尚未实现
⚠ WIP / Experimental — recording list fetch and download not yet implemented

定时从 Plaud API 下载新录音到本地 inbox/

配置方式（任选其一）:
  1. config.yaml 中设置 plaud_client_id 和 plaud_secret_key
  2. ~/auto-transcribe/.env 中设置:
     PLAUD_CLIENT_ID=你的client_id
     PLAUD_SECRET_KEY=你的secret_key

用法：python pull-plaud.py
由 launchd 定时触发（每 30 分钟）

注意：需要先在 https://platform.plaud.ai 注册 Developer 账号获取 API 凭证
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError
import base64

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

from config import load_config

_cfg = load_config()

BASE_DIR = _cfg.base_dir
LOCAL_INBOX = BASE_DIR / "inbox"
PULLED_DB = BASE_DIR / "plaud-pulled.json"
ENV_FILE = BASE_DIR / ".env"

# Plaud API
API_BASE = "https://platform.plaud.ai/developer/api"


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [plaud] {msg}")


def load_env() -> dict[str, str]:
    """从 .env 文件读取配置"""
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def get_access_token(client_id: str, secret_key: str) -> str:
    """获取 Plaud Partner API access token"""
    credentials = base64.b64encode(
        f"{client_id}:{secret_key}".encode()
    ).decode()

    req = Request(
        f"{API_BASE}/oauth/partner/access-token",
        method="POST",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
        },
    )

    with urlopen(req) as resp:
        data = json.loads(resp.read())
        return data["access_token"]


def load_pulled() -> dict:
    if PULLED_DB.exists():
        return json.loads(PULLED_DB.read_text())
    return {}


def save_pulled(db: dict) -> None:
    PULLED_DB.write_text(json.dumps(db, indent=2, ensure_ascii=False))


def main() -> None:
    log("=== Plaud 录音拉取启动 ===")

    env = load_env()
    client_id = env.get("PLAUD_CLIENT_ID", "") or _cfg.plaud_client_id
    secret_key = env.get("PLAUD_SECRET_KEY", "") or _cfg.plaud_secret_key

    if not client_id or not secret_key:
        log("⚠ 未配置 Plaud API 凭证")
        log("  请在 ~/auto-transcribe/.env 中设置:")
        log("  PLAUD_CLIENT_ID=你的client_id")
        log("  PLAUD_SECRET_KEY=你的secret_key")
        log("  获取地址: https://platform.plaud.ai")
        return

    try:
        token = get_access_token(client_id, secret_key)
        log("✓ API 认证成功")
    except (URLError, KeyError) as e:
        log(f"✗ API 认证失败: {e}")
        return

    # TODO: 实现录音列表获取和下载
    # Plaud Partner API 的录音列表端点可能需要根据实际文档调整
    # 非官方 Python 客户端: pip install plaud-ai
    # 或参考: https://github.com/DmytroLitvinov/python-plaud-ai
    log("⚠ 录音拉取功能待实现——需要根据 Plaud API 文档完善")
    log("  参考: https://docs.plaud.ai")
    log("  非官方客户端: pip install plaud-ai")
    log("=== Plaud 拉取结束 ===")


if __name__ == "__main__":
    main()

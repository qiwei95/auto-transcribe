#!/usr/bin/env python3
"""
Plaud 录音自动拉取脚本

通过 Plaud 网页 API 直接下载录音到 inbox/，由现有管道自动转录。

认证方式：
  Bearer token 存放在 ~/.plaud/config.json（从 web.plaud.ai 的 localStorage 获取）

用法：python pull-plaud.py
由 launchd 定时触发（每 5 分钟）
"""

import base64
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

from config import load_config

_cfg = load_config()

BASE_DIR = _cfg.base_dir
LOCAL_INBOX = BASE_DIR / "inbox"
PULLED_DB = BASE_DIR / "plaud-pulled.json"
PLAUD_CONFIG = Path.home() / ".plaud" / "config.json"

# 允许的 Plaud API 域名（防止 config 被篡改后 token 发到别处）
ALLOWED_API_BASES = {
    "https://api-apse1.plaud.ai",
    "https://api.plaud.ai",
    "https://api-euc1.plaud.ai",
}

# 允许的下载 URL 域名后缀（S3 预签名 URL）
ALLOWED_DOWNLOAD_HOSTS = (
    ".amazonaws.com",
    ".plaud.ai",
)

# 模拟网页端请求头（API 校验 Origin）
WEB_HEADERS = {
    "Origin": "https://web.plaud.ai",
    "Referer": "https://web.plaud.ai/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}


def is_safe_download_url(url: str) -> bool:
    """检查下载 URL 是否在允许的域名范围内"""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    return any(parsed.netloc.endswith(h) for h in ALLOWED_DOWNLOAD_HOSTS)


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [plaud] {msg}")


def load_plaud_config() -> dict:
    """读取 ~/.plaud/config.json"""
    if not PLAUD_CONFIG.exists():
        return {}
    return json.loads(PLAUD_CONFIG.read_text())


def load_pulled() -> dict:
    """已拉取录音的记录（file_id → 信息），JSON 损坏时备份后重置"""
    if PULLED_DB.exists():
        try:
            return json.loads(PULLED_DB.read_text())
        except (json.JSONDecodeError, ValueError):
            log("⚠ plaud-pulled.json 损坏，备份后重置")
            import shutil
            backup = PULLED_DB.with_suffix(".json.bak")
            shutil.copy2(PULLED_DB, backup)
            return {}
    return {}


def save_pulled(db: dict) -> None:
    """原子写入：先写临时文件再 rename，断电也不会损坏"""
    fd, tmp = tempfile.mkstemp(dir=PULLED_DB.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(db, f, indent=2, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, PULLED_DB)
    except BaseException:
        os.unlink(tmp)
        raise


def api_get(url: str, token: str) -> dict | bytes:
    """发送 GET 请求到 Plaud API"""
    req = Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for k, v in WEB_HEADERS.items():
        req.add_header(k, v)

    with urlopen(req, timeout=60) as resp:
        content_type = resp.headers.get("Content-Type", "")
        data = resp.read()
        if "json" in content_type:
            return json.loads(data)
        return data


def fetch_recordings(api_base: str, token: str) -> list[dict]:
    """获取所有录音列表"""
    url = f"{api_base}/file/simple/web"
    resp = api_get(url, token)

    if not isinstance(resp, dict) or resp.get("status") != 0:
        log(f"  API 返回异常: {resp}")
        return []

    items = resp.get("data_file_list", [])

    # Plaud 自带示例录音的标题关键词
    demo_keywords = {"welcome_to_plaud", "how_to_use_plaud", "steve_jobs"}

    result = []
    for item in items:
        if item.get("is_trash", False):
            continue
        name = (item.get("filename", "") or "").lower().replace(" ", "_")
        if any(kw in name for kw in demo_keywords):
            continue
        result.append(item)
    return result


def download_mp3(api_base: str, token: str, file_id: str) -> bytes | None:
    """通过临时 URL 下载 MP3"""
    try:
        resp = api_get(f"{api_base}/file/temp-url/{file_id}", token)
        if isinstance(resp, dict) and resp.get("status") == 0:
            mp3_url = resp.get("temp_url", "")
            if mp3_url:
                if not is_safe_download_url(mp3_url):
                    log(f"  拒绝不安全的下载 URL: {mp3_url[:80]}")
                    return None
                # S3 预签名 URL 不需要 auth header
                req = Request(mp3_url)
                with urlopen(req, timeout=120) as dl_resp:
                    return dl_resp.read()
    except (HTTPError, URLError) as e:
        log(f"  MP3 下载失败: {e}")
    return None


def download_raw(api_base: str, token: str, file_id: str) -> bytes | None:
    """直接下载原始格式"""
    try:
        data = api_get(f"{api_base}/file/download/{file_id}", token)
        if isinstance(data, bytes) and len(data) > 0:
            return data
    except (HTTPError, URLError) as e:
        log(f"  原始下载失败: {e}")
    return None


def make_filename(recording: dict) -> str:
    """从录音信息生成文件名（基于 filename 字段，如 '2026-04-11 10:05:55'）"""
    name = recording.get("filename", "")

    # Plaud 的 filename 格式是 "2026-04-11 10:05:55"
    if name:
        safe = name.replace(" ", "_").replace(":", "-")
        # 只保留安全字符（字母、数字、横线、下划线、中文）
        safe = re.sub(r"[^a-zA-Z0-9\-_\u4e00-\u9fff]", "_", safe)
        safe = safe.strip("._")
        if safe:
            return safe[:100]

    # 回退：用 start_time 时间戳
    start = recording.get("start_time", 0)
    if start:
        ts = start / 1000 if start > 1e12 else start
        dt = datetime.fromtimestamp(ts)
        return dt.strftime("%Y-%m-%d_%H-%M-%S")

    return f"plaud_{int(time.time())}"


def check_token_expiry(token: str) -> None:
    """检查 token 是否快过期"""
    try:
        payload = token.split(".")[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        data = json.loads(base64.b64decode(payload))
        exp = data.get("exp", 0)
        days_left = (exp - time.time()) / 86400
        if days_left < 30:
            log(f"⚠ Token 将在 {int(days_left)} 天后过期！")
            log("  更新方法: web.plaud.ai → F12 → Console → localStorage.getItem('tokenstr')")
        elif days_left < 60:
            log(f"  Token 还有 {int(days_left)} 天有效")
    except Exception:
        pass


def main() -> None:
    log("=== Plaud 录音拉取启动 ===")

    # 读取配置
    plaud_cfg = load_plaud_config()
    token = plaud_cfg.get("token", "")
    api_base = plaud_cfg.get("api_base", "https://api-apse1.plaud.ai")

    if api_base not in ALLOWED_API_BASES:
        log(f"✗ 不信任的 api_base: {api_base}")
        log(f"  允许的值: {', '.join(sorted(ALLOWED_API_BASES))}")
        return

    if not token:
        log("✗ 未找到 Plaud token")
        log(f"  请在 {PLAUD_CONFIG} 中配置 token")
        return

    # 去掉前缀 "bearer " 如果有的话
    if token.lower().startswith("bearer "):
        token = token[7:]

    check_token_expiry(token)

    LOCAL_INBOX.mkdir(parents=True, exist_ok=True)

    # 获取录音列表
    log("获取录音列表...")
    try:
        recordings = fetch_recordings(api_base, token)
    except Exception as e:
        log(f"✗ 获取录音列表失败: {e}")
        return

    if not recordings:
        log("没有新录音")
        log("=== Plaud 拉取结束 ===")
        return

    log(f"找到 {len(recordings)} 条录音")

    # 对比已拉取记录
    pulled = load_pulled()
    new_count = 0
    skip_count = 0

    for rec in recordings:
        file_id = rec.get("id", "")
        if not file_id:
            continue

        if file_id in pulled:
            skip_count += 1
            continue

        filename = make_filename(rec)
        duration_ms = rec.get("duration", 0)
        duration_sec = duration_ms // 1000
        duration_min = duration_sec // 60
        duration_remainder = duration_sec % 60
        log(f"下载: {filename} ({duration_min}分{duration_remainder}秒)")

        # 优先下载 MP3
        audio_data = download_mp3(api_base, token, file_id)
        ext = "mp3"

        if not audio_data:
            # 回退到原始格式
            audio_data = download_raw(api_base, token, file_id)
            ext = "ogg"

        if audio_data:
            dest = LOCAL_INBOX / f"{filename}.{ext}"
            # 避免文件名冲突
            counter = 1
            while dest.exists():
                dest = LOCAL_INBOX / f"{filename}_{counter}.{ext}"
                counter += 1

            dest.write_bytes(audio_data)
            size_kb = len(audio_data) / 1024
            log(f"  ✓ 已保存: {dest.name} ({size_kb:.0f} KB)")

            pulled[file_id] = {
                "filename": dest.name,
                "downloaded_at": datetime.now().isoformat(),
                "title": rec.get("filename", ""),
                "duration": duration_sec,
            }
            save_pulled(pulled)
            new_count += 1
        else:
            log(f"  ✗ 下载失败: {filename}")

    log(f"=== 完成: {new_count} 个新录音, {skip_count} 个已跳过 ===")


if __name__ == "__main__":
    main()

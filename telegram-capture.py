#!/usr/bin/env python3
"""
Telegram 内容捕获机器人
接收链接 → 自动识别类型 → 音频下载到 inbox / 文本保存到 Obsidian Captures

用法：TELEGRAM_BOT_TOKEN=xxx python3 telegram-capture.py
"""

import asyncio
import ipaddress
import json
import os
import re
import socket
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx
from lxml import html as lxml_html
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

# launchd 环境下 stdout 不会自动刷新
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# ── 配置 ─────────────────────────────────────────
from config import load_config

_cfg = load_config()
INBOX = _cfg.base_dir / "inbox"
CAPTURES_OUTPUT = _cfg.captures_output

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or _cfg.telegram_bot_token
ALLOWED_USERS: list[int] = _cfg.telegram_allowed_users or [
    int(uid.strip())
    for uid in os.environ.get("TELEGRAM_ALLOWED_USERS", "").split(",")
    if uid.strip().isdigit()
]

# 确保目录存在
INBOX.mkdir(parents=True, exist_ok=True)
CAPTURES_OUTPUT.mkdir(parents=True, exist_ok=True)

# 短链域名（需要展开）
SHORT_LINK_DOMAINS = {
    "t.co", "bit.ly", "xhslink.com", "b23.tv",
    "vt.tiktok.com", "vm.tiktok.com", "tinyurl.com",
    "v.douyin.com",
}

# 追踪参数黑名单
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "fbclid", "igshid", "ref", "s", "t", "si", "feature",
}

# URL 提取正则（排除 file:// javascript: data:）
URL_PATTERN = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')

# 私有 IP 范围
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
]


# ── 工具函数 ──────────────────────────────────────

def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def sanitize_filename(title: str) -> str:
    """清理文件名：保留中文和字母数字，去除特殊字符，最多 50 字符"""
    cleaned = re.sub(r'[^\w\u4e00-\u9fff\u3400-\u4dbf-]', ' ', title)
    cleaned = re.sub(r'\s+', '-', cleaned.strip())
    cleaned = cleaned.strip('-').lower()
    return cleaned[:50] if cleaned else "untitled"


# ── URL 处理 ──────────────────────────────────────

async def resolve_url(url: str) -> str:
    """展开短链接，返回最终 URL"""
    parsed = urlparse(url)
    if parsed.hostname not in SHORT_LINK_DOMAINS:
        return url
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=10.0
        ) as client:
            resp = await client.head(url)
            return str(resp.url)
    except Exception as e:
        log(f"展开短链失败 {url}: {e}")
        return url


def clean_url(url: str) -> str:
    """移除追踪参数"""
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=False)
    cleaned = {k: v for k, v in params.items() if k not in TRACKING_PARAMS}
    new_query = urlencode(cleaned, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def classify_url(url: str) -> tuple[str, str]:
    """根据域名和路径判断平台和内容类型"""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()

    if "youtube.com" in host or "youtu.be" in host:
        return ("youtube", "audio")
    if "bilibili.com" in host or "b23.tv" in host:
        return ("bilibili", "audio")
    if "tiktok.com" in host:
        return ("tiktok", "audio")
    if "douyin.com" in host:
        return ("douyin", "audio")
    if "instagram.com" in host:
        if "/reel/" in path:
            return ("instagram", "audio")
        return ("instagram", "text")
    if "threads.net" in host or "threads.com" in host:
        return ("threads", "text")
    if "xiaohongshu.com" in host or "xhslink.com" in host:
        return ("xiaohongshu", "audio")
    if "twitter.com" in host or "x.com" in host:
        return ("twitter", "audio")
    return ("generic", "text")


def is_safe_url(url: str) -> tuple[bool, str]:
    """验证 URL 安全性：仅允许 http/https，阻止私有 IP"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return (False, f"不允许的协议: {parsed.scheme}")

    hostname = parsed.hostname
    if not hostname:
        return (False, "无效的 URL")

    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return (False, f"无法解析域名: {hostname}")

    for info in addr_infos:
        ip = ipaddress.ip_address(info[4][0])
        for net in _PRIVATE_NETWORKS:
            if ip in net:
                return (False, f"不允许访问内网地址: {ip}")

    return (True, "")


# ── 音频下载 ──────────────────────────────────────

# 平台 referer 映射（模仿浏览器来源，提高下载成功率）
PLATFORM_REFERERS: dict[str, str] = {
    "tiktok": "https://www.tiktok.com/",
    "douyin": "https://www.douyin.com/",
    "bilibili": "https://www.bilibili.com/",
    "instagram": "https://www.instagram.com/",
    "xiaohongshu": "https://www.xiaohongshu.com/",
    "twitter": "https://x.com/",
}


# 广告 URL 参数检测
AD_PARAMS = {"ad_id", "campaign_id", "ad_name", "adset_id", "adset_name"}
AD_UTM_VALUES = {"cpc", "paid", "ppc", "cpm", "cpv"}  # utm_medium 值


def detect_ad_url(url: str) -> bool:
    """检测 URL 参数是否包含广告标识"""
    params = parse_qs(urlparse(url).query, keep_blank_values=False)
    # 直接有广告参数
    if AD_PARAMS & set(params.keys()):
        return True
    # utm_medium 是付费类型
    utm_medium = params.get("utm_medium", [""])[0].lower()
    if utm_medium in AD_UTM_VALUES:
        return True
    return False


# 支持的语言前缀（用户可在链接前加 en/zh/ja 等指定转录语言）
LANG_CODES = {"en", "zh", "ja", "ko", "auto"}


def write_meta_sidecar(
    audio_path: Path, url: str, platform: str, language: str,
    chat_id: int = 0, is_ad: bool = False,
) -> None:
    """写 .meta JSON sidecar，传递 URL/平台/语言/chat_id/is_ad 给 process.py"""
    meta = {"url": url, "platform": platform, "language": language}
    if chat_id:
        meta["chat_id"] = chat_id
    if is_ad:
        meta["is_ad"] = True
    Path(str(audio_path) + ".meta").write_text(json.dumps(meta))


async def download_douyin_direct(url: str) -> Path | None:
    """用 iesdouyin 分享页提取视频 URL（无需 cookie/签名），再用 ffmpeg 转 mp3"""
    import json as _json

    # 从 URL 提取 video_id
    m = re.search(r'/video/(\d+)', url)
    if not m:
        log(f"抖音 URL 无法提取 video_id: {url}")
        return None

    video_id = m.group(1)
    share_url = f"https://www.iesdouyin.com/share/video/{video_id}/"
    log(f"iesdouyin 分享页提取: {share_url}")

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resp = await client.get(share_url, headers={
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
                "Referer": "https://www.douyin.com/",
            })
            resp.raise_for_status()

        # 从 HTML 提取 _ROUTER_DATA
        rm = re.search(
            r'window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*;?\s*</script>',
            resp.text, re.DOTALL,
        )
        if not rm:
            log("iesdouyin 页面无 _ROUTER_DATA")
            return None

        data = _json.loads(rm.group(1))
        loader = data.get("loaderData", {})

        # 找 videoInfoRes（key 格式不固定，遍历查找）
        item = None
        for val in loader.values():
            if isinstance(val, dict) and "videoInfoRes" in val:
                items = val["videoInfoRes"].get("item_list", [])
                if items:
                    item = items[0]
                    break

        if not item:
            log("iesdouyin 未找到 item_list")
            return None

        play_url = ""
        url_list = item.get("video", {}).get("play_addr", {}).get("url_list", [])
        if url_list:
            # playwm → play 去水印
            play_url = url_list[0].replace("/playwm/", "/play/")

        if not play_url:
            log("iesdouyin 未找到 play_addr")
            return None

        # 用标题或描述做文件名
        desc = item.get("desc", "") or video_id
        safe_name = sanitize_filename(desc)
        output_path = INBOX / f"{safe_name}.mp3"

        log(f"  ffmpeg 下载转码: {play_url[:80]}...")
        cmd = [
            "ffmpeg", "-y",
            "-headers", "Referer: https://www.douyin.com/\r\n",
            "-i", play_url,
            "-vn", "-acodec", "libmp3lame", "-q:a", "0",
            str(output_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

        if proc.returncode == 0 and output_path.exists():
            log(f"抖音下载完成: {output_path.name}")
            return output_path

        log(f"ffmpeg 转换失败: {stderr.decode()[-300:]}")
        return None

    except Exception as e:
        log(f"iesdouyin 提取失败: {e}")
        return None


async def download_audio(
    url: str, platform: str = "", language: str = "auto", chat_id: int = 0,
    is_ad: bool = False,
) -> Path | None:
    """用 yt-dlp 下载音频到 inbox/，返回文件路径或 None。下载后写 .meta sidecar"""
    output_template = str(INBOX / "%(title).50s.%(ext)s")
    cmd = [
        "yt-dlp",
        "--extract-audio", "--audio-format", "mp3", "--audio-quality", "0",
        "--output", output_template, "--no-playlist",
        "--socket-timeout", "30", "--retries", "3",
    ]
    # 抖音需要 cookie 才能下载（用导出的文件，避免每次弹 Keychain 授权）
    if platform == "douyin":
        cookie_file = Path(__file__).resolve().parent / "cookies.txt"
        if cookie_file.exists():
            cmd.extend(["--cookies", str(cookie_file)])
    # 加 referer header（学 OmniGet 做法，提高下载成功率）
    referer = PLATFORM_REFERERS.get(platform)
    if referer:
        cmd.extend(["--referer", referer])
    cmd.append(url)
    log(f"开始下载音频: {url}")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)

        if proc.returncode != 0:
            log(f"yt-dlp 失败 (code {proc.returncode}): {stderr.decode()[-500:]}")
            # 抖音 yt-dlp 经常失败，用 iesdouyin 分享页备用
            if platform == "douyin":
                pw_path = await download_douyin_direct(url)
                if pw_path:
                    write_meta_sidecar(pw_path, url, platform, language, chat_id, is_ad)
                    return pw_path
            return None

        # 从 yt-dlp 输出中找到下载的文件
        output_text = stdout.decode() + stderr.decode()
        # yt-dlp 输出格式: [ExtractAudio] Destination: path.mp3
        for line in output_text.splitlines():
            if "Destination:" in line:
                path_str = line.split("Destination:", 1)[1].strip()
                p = Path(path_str)
                if p.exists():
                    log(f"音频下载完成: {p.name}")
                    write_meta_sidecar(p, url, platform, language, chat_id, is_ad)
                    return p

        # 备用：找 inbox 中最近修改的 mp3
        mp3_files = sorted(INBOX.glob("*.mp3"), key=lambda f: f.stat().st_mtime)
        if mp3_files:
            latest = mp3_files[-1]
            if (datetime.now().timestamp() - latest.stat().st_mtime) < 60:
                log(f"音频下载完成（备用检测）: {latest.name}")
                write_meta_sidecar(latest, url, platform, language, chat_id, is_ad)
                return latest

        log("yt-dlp 完成但未找到输出文件")
        return None

    except asyncio.TimeoutError:
        log(f"yt-dlp 超时（600s）: {url}")
        return None
    except Exception as e:
        log(f"下载音频异常: {e}")
        return None


# ── 文本抓取 ──────────────────────────────────────

async def scrape_meta_tags(url: str) -> dict:
    """快速抓取 og 标签（标题、作者），作为 fallback"""
    cmd = [
        "curl", "-sL", "-H",
        "User-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15",
        url,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        page = stdout.decode("utf-8", errors="replace")

        tree = lxml_html.fromstring(page)
        og = {}
        for meta in tree.xpath("//meta[@property]"):
            prop = meta.get("property", "")
            if prop.startswith("og:"):
                og[prop] = meta.get("content", "")

        content = og.get("og:description", "")
        title = og.get("og:title", "")
        author = ""
        if " on " in title:
            author = title.split(" on ")[0].strip()
        elif title and not content:
            author = title

        return {"content": content, "title": title, "author": author}

    except Exception as e:
        log(f"meta 标签抓取失败 {url}: {e}")
        return {"content": "", "title": "", "author": ""}


async def scrape_threads_full(url: str) -> dict:
    """用 Playwright 抓取 Threads 完整内容（帖子正文 + 作者评论）"""
    log(f"Playwright 抓取 Threads: {url}")
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log("playwright 未安装，回退到 meta 标签")
        return await scrape_meta_tags(url)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
            )
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(3000)

            # 提取帖子和作者评论的所有文本块
            texts = await page.evaluate("""
                () => {
                    const blocks = [];
                    // Threads 用 span 包裹文本内容
                    document.querySelectorAll('span').forEach(el => {
                        const t = el.textContent.trim();
                        // 过滤太短的（按钮文字）和重复的
                        if (t.length > 20 && !t.startsWith('Log in')
                            && !t.startsWith('Sign up') && !t.includes('© 2')
                            && !t.startsWith('Follow')) {
                            blocks.push(t);
                        }
                    });
                    // 去重保持顺序
                    const seen = new Set();
                    return blocks.filter(b => {
                        if (seen.has(b)) return false;
                        // 去掉是其他文本子串的块
                        for (const existing of seen) {
                            if (existing.includes(b) || b.includes(existing)) {
                                seen.add(b);
                                return false;
                            }
                        }
                        seen.add(b);
                        return true;
                    });
                }
            """)

            # 提取作者信息（从 og 标签）
            author = await page.evaluate("""
                () => {
                    const el = document.querySelector('meta[property="og:title"]');
                    return el ? el.getAttribute('content') : '';
                }
            """)

            await browser.close()

            content = "\n\n".join(texts) if texts else ""
            author_name = ""
            if author and " on " in author:
                author_name = author.split(" on ")[0].strip()

            if content:
                log(f"Playwright 抓取成功: {len(content)} 字, {len(texts)} 个文本块")
                return {"content": content, "title": author or "", "author": author_name}

            log("Playwright 内容为空，回退 meta 标签")
            return await scrape_meta_tags(url)

    except Exception as e:
        log(f"Playwright 抓取失败 ({e})，回退 meta 标签")
        return await scrape_meta_tags(url)


async def scrape_defuddle(url: str) -> dict:
    """用 defuddle 抓取通用网页内容"""
    cmd = ["npx", "defuddle", "parse", url, "--markdown"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode("utf-8", errors="replace").strip()

        if len(output) < 100:
            return {"content": "", "title": ""}

        # defuddle 输出第一行通常是标题
        lines = output.splitlines()
        title = lines[0].lstrip("# ").strip() if lines else ""
        content = "\n".join(lines[1:]).strip() if len(lines) > 1 else output

        return {"content": content, "title": title}

    except Exception as e:
        log(f"defuddle 抓取失败 {url}: {e}")
        return {"content": "", "title": ""}


async def scrape_instagram_embed(url: str) -> dict:
    """用 Instagram Embed 端点抓取帖子文字（公开可访问，无需 cookie）"""
    parsed = urlparse(url)
    # 从路径提取 post_id: /p/{post_id}/ 或 /reel/{post_id}/
    match = re.search(r'/(?:p|reel)/([A-Za-z0-9_-]+)', parsed.path)
    if not match:
        log(f"Instagram URL 无法提取 post_id: {url}")
        return await scrape_meta_tags(url)

    post_id = match.group(1)
    embed_url = f"https://www.instagram.com/p/{post_id}/embed/captioned/"
    log(f"Instagram Embed 抓取: {embed_url}")

    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            headers={
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            },
        ) as client:
            resp = await client.get(embed_url)
            resp.raise_for_status()
            page = resp.text

        tree = lxml_html.fromstring(page)

        # 帖子正文在 class="Caption" 下
        caption_el = tree.xpath(
            '//*[contains(@class,"Caption")]//text()'
        )
        content = " ".join(t.strip() for t in caption_el if t.strip())

        # 作者名
        author_el = tree.xpath(
            '//*[contains(@class,"UsernameText")]//text()'
        )
        author = author_el[0].strip() if author_el else ""

        if content:
            log(f"Instagram Embed 成功: {len(content)} 字, 作者={author}")
            return {"content": content, "title": f"{author} on Instagram", "author": author}

        log("Instagram Embed 内容为空，回退 meta 标签")
        return await scrape_meta_tags(url)

    except Exception as e:
        log(f"Instagram Embed 失败 ({e})，回退 meta 标签")
        return await scrape_meta_tags(url)


async def scrape_text(url: str, platform: str) -> dict:
    """文本抓取路由"""
    if platform == "threads":
        result = await scrape_threads_full(url)
    elif platform == "instagram":
        result = await scrape_instagram_embed(url)
    else:
        result = await scrape_defuddle(url)

    if not result.get("content"):
        return {"content": "", "error": "抓取失败"}
    return result


# ── Obsidian 写入 ─────────────────────────────────

def write_capture(content: str, metadata: dict) -> Path:
    """写入 social-captures/ 笔记，返回文件路径"""
    today = datetime.now().strftime("%Y-%m-%d")
    title = metadata.get("title", "untitled")
    safe_title = sanitize_filename(title)
    platform = metadata.get("platform", "unknown")
    url = metadata.get("url", "")
    author = metadata.get("author", "")

    # 文件名：date-platform-title.md
    base_name = f"{today}-{platform}-{safe_title}"
    file_path = CAPTURES_OUTPUT / f"{base_name}.md"
    counter = 1
    while file_path.exists():
        file_path = CAPTURES_OUTPUT / f"{base_name}-{counter}.md"
        counter += 1

    # 构建 frontmatter
    tags = ["social-capture", platform, "text"]
    frontmatter = (
        f"---\n"
        f"date: {today}\n"
        f"platform: {platform}\n"
        f"type: text\n"
        f"url: {url}\n"
    )
    if author:
        frontmatter += f"author: {author}\n"
    frontmatter += (
        f"tags: [{', '.join(tags)}]\n"
        f"---"
    )

    display_title = title if title else safe_title
    note = (
        f"{frontmatter}\n\n"
        f"# {display_title}\n\n"
        f"{content}\n"
    )

    file_path.write_text(note, encoding="utf-8")
    log(f"已保存: {file_path.name} → social-captures/")
    return file_path


# ── Telegram 处理器 ───────────────────────────────

async def handle_start(update: Update, context) -> None:
    """/start 命令"""
    text = (
        "你好！我是内容捕获机器人。\n\n"
        "发送链接给我，我会自动处理：\n"
        "- 视频/音频链接 → 下载并加入转录队列\n"
        "- 文章/帖子链接 → 保存到 Obsidian\n\n"
        "支持平台：YouTube, Bilibili, TikTok, Instagram, "
        "Threads, 小红书, Twitter/X, 以及任意网页"
    )
    await update.message.reply_text(text)


async def handle_help(update: Update, context) -> None:
    """/help 命令"""
    text = (
        "使用方法：直接发送一个或多个链接\n\n"
        "音频类（自动下载转录）：\n"
        "  YouTube / Bilibili / TikTok\n"
        "  Instagram Reels / 小红书 / Twitter\n\n"
        "文本类（保存到 Obsidian）：\n"
        "  Threads / Instagram 帖子\n"
        "  任意网页文章\n\n"
        "语言指定（可选，加在链接前面）：\n"
        "  en https://... → 英文转录\n"
        "  zh https://... → 中文转录\n"
        "  不加前缀 → 自动检测语言\n\n"
        "每条消息最多处理 5 个链接。"
    )
    await update.message.reply_text(text)


async def process_single_url(
    url: str, language: str = "auto", chat_id: int = 0,
    is_ad: bool = False,
) -> str:
    """处理单个 URL，返回回复文本"""
    # 1. 展开短链
    resolved = await resolve_url(url)

    # 2. 广告检测（在清理参数之前，否则 ad 参数被删掉）
    if not is_ad:
        is_ad = detect_ad_url(resolved)

    # 3. 清理追踪参数
    cleaned = clean_url(resolved)

    # 4. 安全检查
    safe, reason = is_safe_url(cleaned)
    if not safe:
        return f"✗ URL 不安全: {reason}\n链接: {url}"

    # 5. 分类
    platform, content_type = classify_url(cleaned)
    ad_label = " [广告]" if is_ad else ""
    log(f"处理链接: {cleaned} → {platform}/{content_type}{ad_label} (语言={language})")

    # 6. 按类型处理
    if content_type == "audio":
        audio_path = await download_audio(
            cleaned, platform, language=language, chat_id=chat_id, is_ad=is_ad,
        )
        if audio_path:
            return (
                f"✓ 音频已加入转录队列\n"
                f"来源: {platform.capitalize()}\n"
                f"⏳ 转录完成后会出现在 Obsidian 录音笔记中"
            )
        # 下载失败 → 回退到文本抓取
        log(f"音频下载失败，回退文本抓取: {cleaned}")
        content_type = "text"

    # Type B: 文本抓取
    result = await scrape_text(cleaned, platform)
    if result.get("error"):
        return f"✗ 抓取失败: {result['error']}\n链接: {cleaned}"

    content = result["content"]
    title = result.get("title", "")
    author = result.get("author", "")

    metadata = {
        "title": title,
        "platform": platform,
        "url": cleaned,
        "author": author,
    }
    write_capture(content, metadata)

    # 预览（最多 500 字符）
    preview = content[:500]
    if len(content) > 500:
        preview += "..."

    source_line = platform.capitalize()
    if author:
        source_line += f" / {author}"

    return (
        f"✓ 已保存到 Obsidian Captures\n"
        f"来源: {source_line}\n"
        f"---\n"
        f"{preview}"
    )


async def handle_message(update: Update, context) -> None:
    """主消息处理器"""
    user = update.effective_user
    if not user:
        return

    # 白名单检查（空列表 = 允许所有人）
    if ALLOWED_USERS and user.id not in ALLOWED_USERS:
        log(f"未授权用户: {user.id} ({user.username})")
        await update.message.reply_text("你没有使用权限。")
        return

    text = update.message.text or ""

    # 解析前缀：支持 "ad" (广告) 和语言 (en/zh/ja 等)
    # 示例: "ad https://..." / "en https://..." / "ad en https://..."
    language = "auto"
    is_ad = False
    stripped = text.strip()
    words = stripped.split()

    # 逐个解析前缀词
    consumed = 0
    for word in words:
        w = word.lower()
        if w == "ad" and not is_ad:
            is_ad = True
            consumed += len(word) + 1
        elif w in LANG_CODES and language == "auto":
            language = w
            consumed += len(word) + 1
        else:
            break
    text = stripped[consumed:]

    urls = URL_PATTERN.findall(text)

    if not urls:
        await update.message.reply_text(
            "请发送链接\n\n"
            "💡 可加语言前缀指定转录语言：\n"
            "en https://... → 英文\n"
            "zh https://... → 中文\n"
            "不加前缀 → 自动检测"
        )
        return

    # 最多 5 个
    if len(urls) > 5:
        await update.message.reply_text("每条消息最多处理 5 个链接，已截取前 5 个。")
        urls = urls[:5]

    lang_hint = f", 语言={language}" if language != "auto" else ""
    log(f"收到 {len(urls)} 个链接 (来自 {user.username or user.id}{lang_hint})")

    for url in urls:
        # 去除尾部可能粘连的标点
        url = url.rstrip(",.;:!?)>」】）》")

        # 先发"处理中..."，完成后更新（避免长时间下载导致 Telegram 超时）
        status_msg = await update.message.reply_text(f"⏳ 处理中...\n{url}")
        try:
            reply = await process_single_url(
                url, language=language, chat_id=update.effective_chat.id, is_ad=is_ad,
            )
        except Exception as e:
            log(f"处理异常 {url}: {e}")
            reply = f"✗ 处理出错: {e}\n链接: {url}"
        try:
            await status_msg.edit_text(reply)
        except Exception:
            # edit 失败（超时/消息太旧）→ 发新消息
            await update.message.reply_text(reply)


# ── 入口 ──────────────────────────────────────────

def main() -> None:
    if not TOKEN:
        print("错误: 未设置 TELEGRAM_BOT_TOKEN 环境变量")
        sys.exit(1)

    log("Telegram 内容捕获机器人启动")
    log(f"inbox: {INBOX}")
    log(f"captures: {CAPTURES_OUTPUT}")
    log(f"白名单用户数: {len(ALLOWED_USERS) or '无限制'}")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("help", handle_help))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

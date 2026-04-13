#!/usr/bin/env python3
"""
Auto-Transcribe 菜单栏 App
在 macOS 菜单栏显示转录管道的实时状态

用法：python3.13 menubar.py
"""

import subprocess
import threading
import webbrowser
from pathlib import Path

import rumps

from config import load_config
from status_db import (
    ensure_db,
    get_current,
    get_recent,
    get_today_done,
    mark_stale_jobs,
    step_progress,
)

# ── 配置 ─────────────────────────────────────────
_cfg = load_config()
BASE_DIR = _cfg.base_dir
INBOX = BASE_DIR / "inbox"
OBSIDIAN_OUTPUT = _cfg.obsidian_output

AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".flac", ".ogg", ".aac"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
ALL_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS


def count_inbox() -> int:
    if not INBOX.exists():
        return 0
    return sum(
        1 for f in INBOX.iterdir()
        if not f.name.startswith(".")
        and f.suffix.lower() in ALL_EXTENSIONS
        and ".chunk" not in f.name
    )


class AutoTranscribeApp(rumps.App):
    def _build_menu(self):
        """构建初始菜单"""
        self.menu.clear()
        self.menu = [
            rumps.MenuItem("状态: 空闲", callback=None),
            None,  # 分隔线
            rumps.MenuItem("📋 粘贴 YouTube 链接", callback=self.paste_youtube),
            rumps.MenuItem("📂 打开 inbox", callback=self.open_inbox),
            rumps.MenuItem("📝 打开 Obsidian 笔记", callback=self.open_obsidian),
            None,
            rumps.MenuItem("今日完成: 0 个", callback=None),
            None,
            # 最近记录占位
            rumps.MenuItem("（暂无最近记录）", callback=None),
            None,
            rumps.MenuItem("■ 退出", callback=self.quit_app),
        ]

    def __init__(self):
        super().__init__(
            name="Auto-Transcribe",
            title="🎙",
            quit_button=None,
        )
        ensure_db()
        self._stale_counter = 0
        self._recent_items: list[rumps.MenuItem] = []
        self._build_menu()

    @rumps.timer(3)
    def refresh(self, _):
        """每 3 秒刷新状态"""
        # 每 20 次（约 60 秒）清理一次超时僵尸，不用每次都写数据库
        self._stale_counter += 1
        if self._stale_counter >= 20:
            mark_stale_jobs(timeout_minutes=60)
            self._stale_counter = 0

        current = get_current()
        inbox_count = count_inbox()

        if current:
            progress = step_progress(current["step"])
            filename = current["filename"]
            if len(filename) > 25:
                filename = filename[:22] + "..."
            # 计算已跑时间
            elapsed_str = ""
            time_info = ""
            try:
                from datetime import datetime
                started = datetime.fromisoformat(current["created_at"])
                elapsed = datetime.now() - started
                mins = int(elapsed.total_seconds() / 60)
                elapsed_str = f"{mins}分钟" if mins >= 1 else f"{int(elapsed.total_seconds())}秒"
                dur_sec = current.get("duration_sec") or 0
                dur_min = int(dur_sec / 60)
                time_info = f"已跑 {elapsed_str}"
                if dur_min > 0:
                    time_info += f" · 音频 {dur_min}分钟"
            except Exception:
                elapsed_str = elapsed_str or "..."
                time_info = ""
            self.title = f"🔴 {elapsed_str}"
            status_text = f"● {filename} — {progress} — {time_info}"
        elif inbox_count > 0:
            self.title = f"🎙 {inbox_count}"
            status_text = f"等待处理: {inbox_count} 个文件"
        else:
            self.title = "🎙"
            status_text = "状态: 空闲"

        # 更新第一个菜单项
        keys = list(self.menu.keys())
        if keys:
            self.menu[keys[0]].title = status_text

        # 今日完成
        today_done = get_today_done()
        today_key = [k for k in self.menu.keys() if "今日完成" in str(k)]
        if today_key:
            self.menu[today_key[0]].title = f"今日完成: {len(today_done)} 个"

        # 最近记录
        self._update_recent()

    def _update_recent(self):
        """更新最近完成的记录"""
        recent = get_recent(5)

        # 用引用列表直接删除旧条目，不依赖 key 匹配
        for item in self._recent_items:
            try:
                del self.menu[item.title]
            except KeyError:
                pass
        self._recent_items.clear()

        if not recent:
            placeholder = rumps.MenuItem("（暂无最近记录）", callback=None)
            self._recent_items.append(placeholder)
            self.menu.insert_before("■ 退出", placeholder)
            return

        seen_titles = set()
        for job in recent:
            icon = "✓" if job["step"] == "done" else "✗"
            note = job.get("note_name", "")
            label = note if note else job["filename"]
            if len(label) > 35:
                label = label[:32] + "..."
            title = f"{icon} {label}"
            # 同标题的菜单项会冲突，加序号区分
            if title in seen_titles:
                title = f"{title} ({job['id']})"
            seen_titles.add(title)
            item = rumps.MenuItem(
                title,
                callback=lambda _, n=note: self._open_note(n),
            )
            self._recent_items.append(item)
            self.menu.insert_before("■ 退出", item)

    def _open_note(self, note_name: str):
        """打开 Obsidian 笔记"""
        if not note_name:
            rumps.notification("Auto-Transcribe", "", "没有关联的笔记")
            return
        note_path = OBSIDIAN_OUTPUT / note_name
        if note_path.exists():
            subprocess.run(["open", str(note_path)])
        else:
            rumps.notification("Auto-Transcribe", "", f"笔记不存在: {note_name}")

    def paste_youtube(self, _):
        """弹出输入框，粘贴 YouTube 链接"""
        window = rumps.Window(
            message="输入 YouTube 或视频链接：",
            title="下载并转录",
            default_text="",
            ok="下载",
            cancel="取消",
            dimensions=(400, 24),
        )
        response = window.run()
        if not response.clicked:
            return

        url = response.text.strip()
        if not url:
            return

        if not url.startswith(("http://", "https://")):
            rumps.notification("Auto-Transcribe", "无效链接", "请输入完整的 URL")
            return

        rumps.notification("Auto-Transcribe", "开始下载", url[:60])
        # 后台线程下载，不阻塞菜单栏
        thread = threading.Thread(
            target=self._download_youtube,
            args=(url,),
            daemon=True,
        )
        thread.start()

    def _download_youtube(self, url: str):
        """用 yt-dlp 下载音频到 inbox"""
        try:
            output_template = str(INBOX / "%(title).50s.%(ext)s")
            result = subprocess.run(
                [
                    "yt-dlp",
                    "--extract-audio",
                    "--audio-format", "mp3",
                    "--audio-quality", "0",
                    "--output", output_template,
                    "--no-playlist",
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=600,  # 10 分钟超时
            )
            if result.returncode == 0:
                rumps.notification("Auto-Transcribe", "下载完成", "文件已放入 inbox，等待转录")
            else:
                error = result.stderr.strip()[-100:] if result.stderr else "未知错误"
                rumps.notification("Auto-Transcribe", "下载失败", error)
        except subprocess.TimeoutExpired:
            rumps.notification("Auto-Transcribe", "下载超时", "超过 10 分钟")
        except FileNotFoundError:
            rumps.notification("Auto-Transcribe", "缺少 yt-dlp", "请运行: pip install yt-dlp")
        except Exception as e:
            rumps.notification("Auto-Transcribe", "下载错误", str(e)[:100])

    def open_inbox(self, _):
        """在 Finder 中打开 inbox 文件夹"""
        subprocess.run(["open", str(INBOX)])

    def open_obsidian(self, _):
        """打开 Obsidian 笔记文件夹"""
        subprocess.run(["open", str(OBSIDIAN_OUTPUT)])

    def quit_app(self, _):
        rumps.quit_application()


if __name__ == "__main__":
    AutoTranscribeApp().run()

#!/usr/bin/env python3
"""
周报自动合成
扫描 Obsidian 录音笔记目录，收集过去 7 天的笔记，用 Claude CLI 合成周报。

用法：python weekly-report.py
"""

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

from config import load_config

_cfg = load_config()
OBSIDIAN_OUTPUT = _cfg.obsidian_output
PROMPTS = _cfg.base_dir / "prompts"
WEEKLY_DIR = OBSIDIAN_OUTPUT / "weekly"


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def collect_notes(days: int = 7) -> list[dict]:
    """收集过去 N 天的录音笔记"""
    cutoff = datetime.now() - timedelta(days=days)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    notes = []

    for f in sorted(OBSIDIAN_OUTPUT.glob("*.md")):
        # 跳过非录音笔记（开发日志等）
        name = f.name
        if not name[:4].isdigit():
            continue

        # 从文件名提取日期 (YYYY-MM-DD-xxx.md)
        date_part = name[:10]
        if date_part < cutoff_str:
            continue

        content = f.read_text()

        # 提取 frontmatter 之后的正文（跳过完整转录部分）
        body = content
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                body = parts[2]

        # 截取正文（不含"完整转录"部分）
        if "## 完整转录" in body:
            body = body[:body.index("## 完整转录")]

        notes.append({
            "filename": name,
            "date": date_part,
            "body": body.strip()[:3000],  # 每篇最多 3000 字
        })

    return notes


def generate_weekly_report(notes: list[dict]) -> str:
    """用 Claude CLI 合成周报"""
    if not notes:
        return ""

    # 日期范围
    start_date = notes[0]["date"]
    end_date = notes[-1]["date"]

    # 加载 prompt
    prompt_file = PROMPTS / "weekly-report.md"
    if prompt_file.exists():
        system_prompt = prompt_file.read_text()
    else:
        system_prompt = "将以下录音笔记汇总成一份周报。"

    # 替换日期占位符
    system_prompt = system_prompt.replace("{start_date}", start_date)
    system_prompt = system_prompt.replace("{end_date}", end_date)

    # 拼接笔记内容
    notes_text = ""
    for n in notes:
        notes_text += f"\n\n### {n['filename']} ({n['date']})\n\n{n['body']}"

    full_prompt = f"{system_prompt}\n\n## 本周录音笔记\n{notes_text}"

    # 长度保护
    max_chars = _cfg.max_transcript_chars
    if len(full_prompt) > max_chars:
        log(f"  ⚠ 内容过长 ({len(full_prompt)} 字)，截断到 {max_chars}")
        full_prompt = full_prompt[:max_chars] + "\n\n[... 内容过长，已截断 ...]"

    result = subprocess.run(
        ["claude", "-p", "-"],
        input=full_prompt,
        capture_output=True, text=True,
        timeout=_cfg.claude_timeout,
    )

    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return ""


def main():
    log("=== 周报合成 ===")

    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)

    notes = collect_notes(days=7)
    log(f"收集到 {len(notes)} 篇笔记")

    if not notes:
        log("没有笔记，跳过")
        return

    report = generate_weekly_report(notes)
    if not report:
        log("⚠ Claude 生成周报失败")
        return

    # 写入文件
    today = datetime.now()
    week_num = today.isocalendar()[1]
    filename = f"{today.year}-W{week_num:02d}-周报.md"
    report_path = WEEKLY_DIR / filename

    start_date = notes[0]["date"]
    end_date = notes[-1]["date"]

    frontmatter = f"""---
date: {today.strftime("%Y-%m-%d")}
type: weekly-report
period: {start_date} ~ {end_date}
note_count: {len(notes)}
tags:
  - 录音笔记
  - 录音笔记/周报
  - auto-transcribe
---"""

    report_path.write_text(f"{frontmatter}\n\n{report}")
    log(f"✓ 周报已写入: {report_path}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
状态数据库：process.py 和 menubar.py 的共享状态层

SQLite WAL 模式：允许一个写 + 多个读同时进行，不会互锁。
"""

import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "status.db"

# 处理步骤定义
STEPS = {
    "waiting": "等待处理",
    "extracting": "提取音频",
    "transcribing": "转录中",
    "classifying": "分类中",
    "titling": "生成标题",
    "summarizing": "Claude 总结中",
    "saving": "保存笔记",
    "done": "完成",
    "failed": "失败",
}

STEP_ORDER = list(STEPS.keys())


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """建表（幂等）"""
    conn = _connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            step TEXT NOT NULL DEFAULT 'waiting',
            step_label TEXT NOT NULL DEFAULT '等待处理',
            duration_sec REAL DEFAULT 0,
            note_name TEXT DEFAULT '',
            error TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def add_job(filename: str) -> int:
    """新增一个待处理任务，返回 job id"""
    ensure_db()
    now = datetime.now().isoformat()
    conn = _connect()
    cur = conn.execute(
        "INSERT INTO jobs (filename, step, step_label, created_at, updated_at) "
        "VALUES (?, 'waiting', '等待处理', ?, ?)",
        (filename, now, now),
    )
    job_id = cur.lastrowid
    conn.commit()
    conn.close()
    return job_id


def update_job(job_id: int, step: str, **kwargs) -> None:
    """更新任务状态"""
    label = STEPS.get(step, step)
    now = datetime.now().isoformat()
    sets = ["step = ?", "step_label = ?", "updated_at = ?"]
    vals = [step, label, now]

    for key in ("duration_sec", "note_name", "error"):
        if key in kwargs:
            sets.append(f"{key} = ?")
            vals.append(kwargs[key])

    vals.append(job_id)
    conn = _connect()
    conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", vals)
    conn.commit()
    conn.close()


def mark_stale_jobs(timeout_minutes: int = 30) -> None:
    """把超时的 job 标记为失败（崩溃恢复）"""
    ensure_db()
    conn = _connect()
    conn.execute(
        "UPDATE jobs SET step = 'failed', step_label = '失败（超时）', "
        "error = '处理超时，可能崩溃', updated_at = ? "
        "WHERE step NOT IN ('done', 'failed', 'waiting') "
        "AND updated_at < datetime('now', ? || ' minutes')",
        (datetime.now().isoformat(), f"-{timeout_minutes}"),
    )
    conn.commit()
    conn.close()


def get_current() -> dict | None:
    """获取当前正在处理的任务（非 done/failed/waiting 的最新一条）"""
    ensure_db()
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM jobs WHERE step NOT IN ('done', 'failed', 'waiting') "
        "ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_waiting_count() -> int:
    conn = _connect()
    count = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE step = 'waiting'"
    ).fetchone()[0]
    conn.close()
    return count


def get_today_done() -> list[dict]:
    """获取今日完成的任务"""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM jobs WHERE step = 'done' AND created_at LIKE ? "
        "ORDER BY updated_at DESC",
        (f"{today}%",),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent(limit: int = 5) -> list[dict]:
    """获取最近完成/失败的任务"""
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM jobs WHERE step IN ('done', 'failed') "
        "ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def step_progress(step: str) -> str:
    """返回进度文字，如 '2/4 转录中'"""
    # waiting 和 done/failed 不算在进度里
    active_steps = ["extracting", "transcribing", "classifying",
                    "titling", "summarizing", "saving"]
    if step in active_steps:
        idx = active_steps.index(step) + 1
        total = len(active_steps)
        return f"{idx}/{total} {STEPS[step]}"
    return STEPS.get(step, step)


_initialized = False


def ensure_db() -> None:
    """首次使用时建表（懒加载，避免 import 时副作用）"""
    global _initialized
    if not _initialized:
        init_db()
        _initialized = True

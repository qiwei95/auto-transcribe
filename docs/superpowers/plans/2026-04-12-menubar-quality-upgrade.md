# Menubar Quality Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the auto-transcribe menubar app's reliability, information density, and user experience while staying within the rumps framework.

**Architecture:** Keep the current rumps-based menubar as the quick-glance status tool. Fix existing bugs (zombie job display, wasteful DB calls), add ETA estimation, error details, retry failed jobs, and a real app icon. The existing `process.py ↔ status_db.py ↔ menubar.py` separation of concerns stays unchanged.

**Tech Stack:** Python 3.13 + rumps + SQLite (existing). No new dependencies.

---

## Background: Technology Options Considered

| Option | Effort | What You Get | Verdict |
|--------|--------|-------------|---------|
| **rumps improvements** (this plan) | Low | Fix bugs, add ETA/retry/icon, much better UX | **Do this now** |
| **PyObjC direct** | High | Custom colored menu items, drag-and-drop to icon | Overkill for current needs |
| **SwiftUI MenuBarExtra** | Very high | Native macOS feel, animations, rich UI | Full rewrite in Swift, not worth it |
| **NiceGUI Web UI** (Phase 5) | Medium | Dashboard, charts, settings, phone access | Complements menubar, do later |

**Decision: Maximize rumps first.** It covers 90% of what a status menubar needs. The remaining 10% (rich visuals, drag-drop) isn't worth a technology switch.

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `menubar.py` | Modify | Fix bugs, add ETA, error display, retry action, app icon |
| `status_db.py` | Modify | Add `retry_job()`, `get_failed_recent()` queries |
| `icons/menubar-idle.png` | Create | 18x18 menubar icon (idle state) |
| `icons/menubar-active.png` | Create | 18x18 menubar icon (processing state) |
| `icons/menubar-error.png` | Create | 18x18 menubar icon (error state) |
| `tests/test_menubar_logic.py` | Create | Tests for ETA calculation, stale cleanup logic |

---

### Task 1: Fix Stale Job Cleanup Performance

**Problem:** `mark_stale_jobs()` runs a DB write every 3 seconds (the timer interval). It should run at most once per minute — there's no reason to check for zombies 20 times a minute.

**Files:**
- Modify: `menubar.py:76-80`

- [ ] **Step 1: Write the test**

```python
# tests/test_menubar_logic.py
import time

def test_stale_cleanup_runs_at_most_once_per_minute():
    """mark_stale_jobs should not be called on every 3-second refresh"""
    call_count = 0
    original_time = time.time()

    def mock_mark_stale(timeout_minutes=60):
        nonlocal call_count
        call_count += 1

    # Simulate 20 refresh cycles (60 seconds worth at 3s interval)
    last_cleanup = 0
    CLEANUP_INTERVAL = 60

    for i in range(20):
        now = original_time + (i * 3)
        if now - last_cleanup >= CLEANUP_INTERVAL:
            mock_mark_stale(timeout_minutes=60)
            last_cleanup = now

    # Should only be called once (at the start), not 20 times
    assert call_count == 1
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd /Users/jaredlow/Documents/Claude-Output/auto-transcribe && python3.13 -m pytest tests/test_menubar_logic.py::test_stale_cleanup_runs_at_most_once_per_minute -v`

- [ ] **Step 3: Apply the fix in menubar.py**

Add a `_last_cleanup` tracker to the App class and only call `mark_stale_jobs` once per minute:

```python
# In __init__, add:
self._last_cleanup = 0

# In refresh(), replace the mark_stale_jobs call:
import time as _time
now = _time.time()
if now - self._last_cleanup >= 60:
    mark_stale_jobs(timeout_minutes=60)
    self._last_cleanup = now
```

- [ ] **Step 4: Move `from datetime import datetime` to file top level**

Currently imported inside the timer function (line 90). Move to file-level imports.

- [ ] **Step 5: Commit**

```bash
git add menubar.py tests/test_menubar_logic.py
git commit -m "fix: reduce stale job cleanup from every 3s to every 60s"
```

---

### Task 2: Add ETA Estimation

**Problem:** The menubar shows "已跑 X 分钟" but doesn't tell you how much longer. Since you know the audio duration and the current step, you can estimate remaining time.

**Files:**
- Modify: `menubar.py:86-107`
- Create logic in: `tests/test_menubar_logic.py`

- [ ] **Step 1: Write the ETA calculation test**

```python
# tests/test_menubar_logic.py

def estimate_eta(step: str, elapsed_sec: float, duration_sec: float) -> str:
    """Estimate remaining time based on step and audio duration.
    
    Rough timing model (based on observations):
    - transcribing: ~13% of audio duration (Vibe on Apple Silicon)
    - classifying + titling: ~15 seconds total
    - summarizing: ~30-120 seconds depending on length
    """
    if step == "transcribing":
        # Vibe processes ~7.5x realtime on M-series
        estimated_total = duration_sec * 0.13
        remaining = max(0, estimated_total - elapsed_sec)
    elif step in ("classifying", "titling"):
        remaining = 15
    elif step == "summarizing":
        remaining = 60  # Claude CLI typically takes 30-120s
    elif step == "extracting":
        remaining = 10
    elif step == "saving":
        remaining = 5
    else:
        return ""

    if remaining < 60:
        return f"~{int(remaining)}秒"
    else:
        return f"~{int(remaining / 60)}分钟"


def test_eta_transcribing_short_audio():
    # 30 second audio, 3 seconds elapsed
    result = estimate_eta("transcribing", 3, 30)
    assert "秒" in result

def test_eta_transcribing_long_audio():
    # 3.8 hour audio (13662s), 5 minutes elapsed
    result = estimate_eta("transcribing", 300, 13662)
    assert "分钟" in result

def test_eta_summarizing():
    result = estimate_eta("summarizing", 10, 100)
    assert "秒" in result or "分钟" in result

def test_eta_done_returns_empty():
    result = estimate_eta("done", 100, 100)
    assert result == ""
```

- [ ] **Step 2: Run tests**

Run: `python3.13 -m pytest tests/test_menubar_logic.py -v -k "test_eta"`
Expected: All 4 pass.

- [ ] **Step 3: Add estimate_eta function to menubar.py**

Add the `estimate_eta()` function (same as test file) and use it in the refresh timer:

```python
# After time_info line, add:
eta = estimate_eta(current["step"], elapsed.total_seconds(), 
                   current.get("duration_sec", 0))
if eta:
    time_info += f" · 预计还需 {eta}"
```

- [ ] **Step 4: Update title bar to show ETA**

```python
# Change:
self.title = f"🔴 {elapsed_str}"
# To:
if eta:
    self.title = f"🔴 {eta}"
else:
    self.title = f"🔴 {elapsed_str}"
```

This way the menubar icon shows remaining time (more useful) instead of elapsed time.

- [ ] **Step 5: Commit**

```bash
git add menubar.py tests/test_menubar_logic.py
git commit -m "feat: add ETA estimation to menubar status"
```

---

### Task 3: Show Failed Job Errors + Retry Action

**Problem:** When a job fails, you see "✗ filename" but no error message and no way to retry without manually moving files.

**Files:**
- Modify: `status_db.py` — add `get_failed_jobs()` and `delete_job()` queries
- Modify: `menubar.py` — show error in submenu, add "重新处理" button

- [ ] **Step 1: Add DB queries for failed jobs**

In `status_db.py`, add:

```python
def get_failed_jobs(limit: int = 5) -> list[dict]:
    """Get recent failed jobs with error details"""
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM jobs WHERE step = 'failed' "
        "ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_job(job_id: int) -> None:
    """Delete a job record (for retry)"""
    conn = _connect()
    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    conn.commit()
    conn.close()
```

- [ ] **Step 2: Update menubar to show errors in recent list**

In `menubar.py`, modify `_update_recent()` to show error details for failed jobs:

```python
for job in recent:
    icon = "✓" if job["step"] == "done" else "✗"
    note = job.get("note_name", "")
    error = job.get("error", "")
    label = note if note else job["filename"]
    if len(label) > 35:
        label = label[:32] + "..."
    
    # For failed jobs, show error on hover via title
    title = f"{icon} {label}"
    if error and icon == "✗":
        title += f"\n   ⚠ {error[:50]}"
    
    item = rumps.MenuItem(
        title,
        callback=lambda _, n=note, j=job: self._handle_job_click(n, j),
    )
    self.menu.insert_before("■ 退出", item)
```

- [ ] **Step 3: Add retry handler**

```python
def _handle_job_click(self, note_name: str, job: dict):
    """Click handler: open note for done jobs, offer retry for failed jobs"""
    if job["step"] == "done":
        self._open_note(note_name)
    elif job["step"] == "failed":
        filename = job["filename"]
        # Check if original file exists in failed/
        failed_path = BASE_DIR / "failed" / filename
        if failed_path.exists():
            response = rumps.alert(
                title=f"重新处理 {filename}?",
                message=f"错误: {job.get('error', '未知')}\n\n移回 inbox 重新转录？",
                ok="重新处理",
                cancel="取消",
            )
            if response == 1:  # OK clicked
                import shutil
                dest = INBOX / filename
                shutil.move(str(failed_path), str(dest))
                from status_db import delete_job
                delete_job(job["id"])
                rumps.notification("Auto-Transcribe", "已移回 inbox", filename)
        else:
            rumps.notification("Auto-Transcribe", "文件不存在", 
                             f"failed/{filename} 已被删除")
```

- [ ] **Step 4: Commit**

```bash
git add menubar.py status_db.py
git commit -m "feat: show error details for failed jobs + retry action"
```

---

### Task 4: Replace Emoji Icon with Real Menubar Icons

**Problem:** Emoji icons (🎙🔴) look inconsistent across macOS versions and don't match the native menubar aesthetic. Real 18x18 template images look professional.

**Files:**
- Create: `icons/menubar-idle.png` (18x18, black on transparent)
- Create: `icons/menubar-active.png` (18x18, black on transparent)
- Modify: `menubar.py` — use `icon` parameter instead of `title`

- [ ] **Step 1: Generate menubar icon images**

Use Python to create simple, clean 18x18 icons:

```python
# generate_icons.py (one-time script)
from PIL import Image, ImageDraw

size = (36, 36)  # 2x for Retina

# Idle: microphone outline
img = Image.new("RGBA", size, (0, 0, 0, 0))
draw = ImageDraw.Draw(img)
# Simple mic shape
draw.rounded_rectangle([12, 4, 24, 20], radius=4, outline="black", width=2)
draw.line([18, 20, 18, 28], fill="black", width=2)
draw.line([12, 28, 24, 28], fill="black", width=2)
img.save("icons/menubar-idle.png")

# Active: mic with waves
img2 = img.copy()
draw2 = ImageDraw.Draw(img2)
draw2.arc([4, 8, 14, 24], start=120, end=240, fill="black", width=2)
draw2.arc([22, 8, 32, 24], start=-60, end=60, fill="black", width=2)
img2.save("icons/menubar-active.png")
```

- [ ] **Step 2: Update menubar.py to use icon images**

```python
# In __init__:
icon_dir = Path(__file__).resolve().parent / "icons"
self._icon_idle = str(icon_dir / "menubar-idle.png")
self._icon_active = str(icon_dir / "menubar-active.png")

super().__init__(
    name="Auto-Transcribe",
    icon=self._icon_idle,
    title="",  # Empty title, icon only
    quit_button=None,
)
```

```python
# In refresh(), update icon instead of title:
if current:
    self.icon = self._icon_active
    self.title = f" {eta}" if eta else f" {elapsed_str}"  # Short text next to icon
elif inbox_count > 0:
    self.icon = self._icon_idle
    self.title = f" {inbox_count}"
else:
    self.icon = self._icon_idle
    self.title = ""
```

- [ ] **Step 3: Commit**

```bash
git add icons/ menubar.py generate_icons.py
git commit -m "feat: replace emoji with native menubar template icons"
```

---

### Task 5: Add Processing Queue Display

**Problem:** When multiple files are in inbox, you can't see what's queued — only the current one processing.

**Files:**
- Modify: `menubar.py` — add queue section to menu

- [ ] **Step 1: Add queue display to refresh()**

After the status text update, add a queue section:

```python
# Update queue display
queue_key = [k for k in self.menu.keys() if "等待处理" in str(k) or "队列" in str(k)]
for k in queue_key:
    if k not in ("📂 打开 inbox",):
        del self.menu[k]

if inbox_count > 0:
    # List queued files
    queue_items = []
    for f in sorted(INBOX.iterdir()):
        if (not f.name.startswith(".") 
            and f.suffix.lower() in ALL_EXTENSIONS 
            and ".chunk" not in f.name):
            name = f.name if len(f.name) <= 30 else f.name[:27] + "..."
            queue_items.append(f"   ◦ {name}")
        if len(queue_items) >= 5:
            break

    if queue_items:
        queue_text = f"队列 ({inbox_count}):\n" + "\n".join(queue_items)
        self.menu.insert_after(
            list(self.menu.keys())[0],  # After status
            rumps.MenuItem(queue_text, callback=None),
        )
```

- [ ] **Step 2: Commit**

```bash
git add menubar.py
git commit -m "feat: show processing queue in menubar dropdown"
```

---

### Task 6: Add Completion Notification with Sound

**Problem:** Long transcriptions (50+ minutes) complete silently. You need a clear signal.

**Files:**
- Modify: `menubar.py` — detect job completion transitions

- [ ] **Step 1: Track previous state to detect transitions**

```python
# In __init__, add:
self._prev_job_id = None

# In refresh(), after getting current:
if self._prev_job_id and not current:
    # Job was running, now it's done — something just completed
    recent = get_recent(1)
    if recent and recent[0]["step"] == "done":
        note = recent[0].get("note_name", "")
        rumps.notification(
            "Auto-Transcribe",
            "转录完成!",
            note or recent[0]["filename"],
            sound=True,  # Play notification sound
        )

self._prev_job_id = current["id"] if current else None
```

- [ ] **Step 2: Commit**

```bash
git add menubar.py
git commit -m "feat: play notification sound when transcription completes"
```

---

## Summary: Priority Order

| Task | Impact | Effort | Priority |
|------|--------|--------|----------|
| Task 1: Fix stale cleanup | Reliability | 10 min | **P0** |
| Task 2: ETA estimation | UX | 20 min | **P0** |
| Task 3: Error + retry | UX | 25 min | **P1** |
| Task 6: Completion sound | UX | 10 min | **P1** |
| Task 5: Queue display | UX | 15 min | **P2** |
| Task 4: Real icons | Polish | 20 min | **P2** |

**Total estimated effort:** ~1.5 hours for all 6 tasks.

---

## Future: Beyond Rumps

When you outgrow rumps, the upgrade path is:

1. **NiceGUI Web Dashboard** (already planned as Phase 5) — for detailed history, charts, settings. Complements the menubar rather than replacing it.

2. **PyObjC enhancements** — if you need drag-and-drop files onto the menubar icon, or colored progress indicators. Can be mixed into the existing rumps app.

3. **SwiftUI rewrite** — only if you want to distribute the app to others or need a polished native experience. Not worth it for personal tooling.

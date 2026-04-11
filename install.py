#!/usr/bin/env python3
"""
auto-transcribe 安装脚本 / Installation script

功能 / Features:
1. 检查系统依赖 / Check system dependencies
2. 创建工作目录 / Create working directories
3. 生成配置文件 / Generate config file
4. 安装 launchd 服务 / Install launchd services
"""

import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
TEMPLATES_DIR = BASE_DIR / "templates"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"


def check_command(name: str, install_hint: str) -> bool:
    """检查命令是否可用"""
    found = shutil.which(name) is not None
    status = "✓" if found else "✗"
    print(f"  {status} {name}", end="")
    if not found:
        print(f"  ← {install_hint}", end="")
    print()
    return found


def check_dependencies() -> bool:
    """检查所有依赖"""
    print("\n检查依赖 / Checking dependencies...\n")

    all_ok = True

    # Python 包
    try:
        import yaml  # noqa: F401
        print("  ✓ PyYAML")
    except ImportError:
        print("  ✗ PyYAML  ← pip3 install PyYAML")
        all_ok = False

    # 系统工具
    all_ok &= check_command("ffmpeg", "brew install ffmpeg")
    all_ok &= check_command("ffprobe", "brew install ffmpeg")

    # Vibe / sona CLI
    sona_path = Path("/Applications/vibe.app/Contents/MacOS/sona")
    if sona_path.exists():
        print("  ✓ sona (Vibe)")
    else:
        print("  ✗ sona (Vibe)  ← https://thewh1teagle.github.io/vibe/")
        all_ok = False

    # Claude CLI
    all_ok &= check_command("claude", "npm install -g @anthropic-ai/claude-code")

    if all_ok:
        print("\n  全部就绪! / All dependencies ready!")
    else:
        print("\n  ⚠ 缺少部分依赖，请先安装 / Some dependencies missing")

    return all_ok


def create_directories():
    """创建工作目录"""
    print("\n创建目录 / Creating directories...\n")
    dirs = ["inbox", "processing", "done", "failed", "transcripts", "logs", "prompts"]
    for d in dirs:
        path = BASE_DIR / d
        path.mkdir(parents=True, exist_ok=True)
        print(f"  ✓ {d}/")


def setup_config():
    """生成配置文件"""
    print("\n配置文件 / Configuration...\n")

    # 本地 config.yaml
    config_path = BASE_DIR / "config.yaml"
    example_path = BASE_DIR / "config.example.yaml"

    if config_path.exists():
        print(f"  ✓ config.yaml 已存在 / already exists")
    elif example_path.exists():
        shutil.copy2(example_path, config_path)
        print(f"  ✓ 已从模板创建 config.yaml / Created from template")
        print(f"    请编辑配置 / Please edit: {config_path}")
    else:
        print(f"  ⚠ config.example.yaml 不存在 / template missing")


def detect_python() -> str:
    """检测当前 Python 路径"""
    return sys.executable


def install_launchd():
    """安装 launchd 服务"""
    print("\nlaunchd 服务 / launchd services...\n")

    if not TEMPLATES_DIR.exists():
        print("  ⚠ templates/ 目录不存在 / templates/ directory missing")
        return

    # 读取 config 获取路径
    try:
        from config import load_config
        cfg = load_config()
    except Exception:
        cfg = None

    python_path = detect_python()
    home = str(Path.home())
    base_dir = str(BASE_DIR)
    icloud_inbox = str(cfg.icloud_inbox) if cfg else f"{home}/Library/Mobile Documents/com~apple~CloudDocs/录音收件箱"
    voice_memos_dir = str(cfg.voice_memos_dir) if cfg else f"{home}/Library/Application Support/com.apple.voicememos/Recordings"

    replacements = {
        "__HOME__": home,
        "__PYTHON__": python_path,
        "__BASE_DIR__": base_dir,
        "__ICLOUD_INBOX__": icloud_inbox,
        "__VOICE_MEMOS_DIR__": voice_memos_dir,
    }

    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)

    for template in sorted(TEMPLATES_DIR.glob("*.plist")):
        content = template.read_text()
        for placeholder, value in replacements.items():
            content = content.replace(placeholder, value)

        dest = LAUNCH_AGENTS / template.name
        dest.write_text(content)
        print(f"  ✓ {template.name} → ~/Library/LaunchAgents/")

    # 提示加载服务
    print()
    print("  要启用服务，运行 / To enable services, run:")
    for template in sorted(TEMPLATES_DIR.glob("*.plist")):
        dest = LAUNCH_AGENTS / template.name
        print(f"    launchctl load {dest}")


def main():
    print("=" * 50)
    print("  auto-transcribe 安装 / Installation")
    print("=" * 50)

    deps_ok = check_dependencies()
    create_directories()
    setup_config()
    install_launchd()

    print()
    print("=" * 50)
    if deps_ok:
        print("  安装完成! / Installation complete!")
    else:
        print("  安装完成（部分依赖缺失）")
        print("  Installation complete (some dependencies missing)")
    print("=" * 50)
    print()
    print("下一步 / Next steps:")
    print("  1. 编辑 config.yaml / Edit config.yaml")
    print("  2. 加载 launchd 服务 / Load launchd services (see commands above)")
    print("  3. 往 inbox/ 放音频试试 / Drop an audio file into inbox/ to test")
    print()


if __name__ == "__main__":
    main()

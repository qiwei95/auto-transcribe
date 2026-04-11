#!/usr/bin/env python3
"""生成 iOS 快捷指令：Save to Recording Inbox

将分享的录音文件自动存到 iCloud Drive/录音收件箱/
Mac 上 shortcuts sign + open 导入后会同步到 iPhone
"""

import plistlib
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

# 快捷指令配置
SHORTCUT_NAME = "Save to Recording Inbox"
ICLOUD_FOLDER = "/录音收件箱"

# 输出路径
BASE_DIR = Path(__file__).parent
UNSIGNED_PATH = BASE_DIR / "save-to-recording-inbox.unsigned.shortcut"
SIGNED_PATH = BASE_DIR / "save-to-recording-inbox.shortcut"


def create_shortcut_plist() -> dict:
    """构建快捷指令的 plist 结构"""

    # 动作：存储文件到 iCloud Drive/录音收件箱/
    # 不指定 WFInput，让 Shortcuts 自动用隐式输入（共享表单或 CLI --input-path）
    actions = [
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.documentpicker.save",
            "WFWorkflowActionParameters": {
                "WFFileDestinationPath": ICLOUD_FOLDER,
                "WFSaveFileOverwrite": False,
                "WFAskWhereToSave": False,
            },
        },
    ]

    shortcut = {
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowClientVersion": "2702.0.4",
        "WFWorkflowIcon": {
            "WFWorkflowIconStartColor": 4274264319,  # 蓝色
            "WFWorkflowIconGlyphNumber": 59765,  # 麦克风图标
        },
        "WFWorkflowActions": actions,
        "WFWorkflowInputContentItemClasses": [
            "WFGenericFileContentItem",
            "WFAVAssetContentItem",
        ],
        "WFWorkflowTypes": ["NCWidget", "ActionExtension"],  # 独立运行 + 共享表单
        "WFWorkflowHasShortcutInputVariables": True,
    }

    return shortcut


def main():
    print(f"=== 生成快捷指令: {SHORTCUT_NAME} ===\n")

    # 1. 生成 plist
    shortcut = create_shortcut_plist()

    with open(UNSIGNED_PATH, "wb") as f:
        plistlib.dump(shortcut, f, fmt=plistlib.FMT_BINARY)
    print(f"✓ 生成未签名文件: {UNSIGNED_PATH}")

    # 2. 签名
    try:
        result = subprocess.run(
            [
                "shortcuts", "sign",
                "--mode", "anyone",
                "--input", str(UNSIGNED_PATH),
                "--output", str(SIGNED_PATH),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"✗ 签名失败: {result.stderr}")
            sys.exit(1)
        print(f"✓ 签名完成: {SIGNED_PATH}")
    except FileNotFoundError:
        print("✗ shortcuts CLI 不存在，请确认 macOS 版本")
        sys.exit(1)

    # 3. 清理未签名文件
    UNSIGNED_PATH.unlink()
    print("✓ 清理未签名文件")

    # 4. 导入
    print(f"\n--- 导入快捷指令 ---")
    print(f"运行: open {SIGNED_PATH}")
    subprocess.run(["open", str(SIGNED_PATH)])
    print("\n快捷指令 App 会弹出导入确认，点「添加快捷指令」即可。")
    print("导入后会通过 iCloud 同步到 iPhone。")


if __name__ == "__main__":
    main()

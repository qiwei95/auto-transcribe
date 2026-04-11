#!/bin/bash
exec > /tmp/sync-debug.log 2>&1
echo "=== $(date) ==="
echo "cwd: $(pwd)"
echo "PATH: $PATH"
echo "running python..."
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 /Users/jaredlow/Documents/Claude-Output/auto-transcribe/sync-icloud.py
echo "exit: $?"

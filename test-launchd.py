#!/usr/bin/env python3
import os
with open("/tmp/launchd-test.txt", "w") as f:
    f.write(f"it works! cwd={os.getcwd()}\n")

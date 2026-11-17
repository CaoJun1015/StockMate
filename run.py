#!python
"""
调货助手 - 快速启动入口
"""

import sys
import os

# 确保能找到 src 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.main import main

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == '--smoke-test':
        from src.release_smoke import run
        run(sys.argv[2])
    else:
        main()

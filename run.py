#!python
"""
货管家 · StockMate - 快速启动入口
"""

import sys
import os

# 确保能找到 src 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.main import main

if __name__ == "__main__":
    main()

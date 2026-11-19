import os
import sys

# 让 tests 目录下的用例能 import 仓库根目录的 battery_viewer
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

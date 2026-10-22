"""`python3 -m vtx` 入口（§5.2：clone 即用与 pip 安装同码同义）。

与 vtx/vtx.py 薄启动器、console script `vtx`（vtx.cli.commands:main）三通等价。
"""
import sys

from .cli.commands import main

if __name__ == "__main__":
    sys.exit(main())

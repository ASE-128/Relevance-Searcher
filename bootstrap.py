"""解释器引导：必须在任何第三方导入之前执行。

独立成模块的三个理由：
  1) 脚本模式（python server.py）与包模式（python -m ...）复用同一套引导；
  2) 让业务模块只依赖"已经引导完成"这个前提，而不是各自重放 sys.path 注入；
  3) 顺序敏感点集中一处，重构时不会被顺手打散。

导入本模块即完成引导（模块底部调用 ensure_sys_path）。
"""

from __future__ import annotations

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENDORED_DIR = os.path.join(BASE_DIR, "_vendored")

# pywin32 的 .pth 不会被手动 sys.path 触发，需要额外补的子路径
_PYWIN32_SUBPATHS = ("win32", "win32\\lib", "pythonwin")


def _add(path: str) -> None:
    if os.path.isdir(path) and path not in sys.path:
        sys.path.insert(0, path)


def ensure_sys_path() -> None:
    """把项目根、内嵌依赖与 pywin32 路径注入 sys.path（幂等）"""
    # 1) 项目根：让扁平模块（config / textutil / pipeline …）在包模式下也能 import
    _add(BASE_DIR)
    # 2) 内嵌第三方依赖
    _add(VENDORED_DIR)
    # 3) pywin32 子路径 + DLL 目录（pywintypes312.dll 等）
    for sub in _PYWIN32_SUBPATHS:
        _add(os.path.join(VENDORED_DIR, sub))
    try:
        import pywin32_system32  # noqa: F401

        for path in pywin32_system32.__path__:
            if os.path.isdir(path):
                os.add_dll_directory(path)
    except ImportError:
        pass


ensure_sys_path()

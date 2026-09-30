#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""推送前门：确认 MCP 入口可导入且仍注册 4 个工具（离线、秒级）。

用途：把「拆分/改名后入口断链」挡在推送之前；需要联网的回归基准仍为手动运行。
用法: python scripts/check_entry.py
"""

import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import bootstrap  # noqa: F401  引导 sys.path（_vendored / pywin32）
import server


def main() -> int:
    tm = getattr(server.mcp, "_tool_manager", None)
    tools = getattr(tm, "_tools", None) if tm is not None else None
    if not tools:
        print("[check_entry] server 导入成功（无法读取内部工具表，跳过数量断言）")
        return 0
    names = sorted(tools.keys())
    print(f"[check_entry] server 导入成功；已注册工具: {names}")
    if len(names) != 4:
        print("[check_entry] 工具数异常：期望 4 个（web_search / search_multi / search_format / fetch_page）")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

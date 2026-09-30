"""集中路径与运行参数：所有 os.environ 读取点收敛在这里。

不 import bootstrap —— 只依赖标准库，任何模块都可以安全导入。
"""

from __future__ import annotations

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── 本地 SearXNG 实例 ──
SEARXNG_DIR = os.path.join(BASE_DIR, "SearXNGforWindows")
SEARXNG_PYTHON = os.path.join(SEARXNG_DIR, "python", "python.exe")
SEARXNG_WEBAPP = os.path.join(
    SEARXNG_DIR, "python", "Lib", "site-packages", "searx", "webapp.py"
)
SEARXNG_SETTINGS = os.path.join(SEARXNG_DIR, "config", "settings.yml")
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://127.0.0.1:8888")
SEARXNG_TIMEOUT = int(os.getenv("SEARXNG_TIMEOUT", "15"))
SEARXNG_AUTOSTART = os.getenv("SEARXNG_AUTOSTART", "1") not in ("0", "false", "False")
SEARXNG_READY_TIMEOUT = int(os.getenv("SEARXNG_READY_TIMEOUT", "60"))
SEARXNG_HEALTH_TIMEOUT = float(os.getenv("SEARXNG_HEALTH_TIMEOUT", "5"))

# ── 网页抓取 ──
FETCH_TIMEOUT = float(os.getenv("FETCH_TIMEOUT", "15"))
FETCH_MAX_CHARS = 4000
FETCH_MAX_CHARS_MIN = 500
FETCH_MAX_CHARS_MAX = 20000
FETCH_MIN_BLOCK_LEN = 8

# ── 搜索工具参数上限 ──
SEARCH_MAX_RESULTS = 15
SEARCH_MULTI_MAX_RESULTS = 10

# ── 客户端配置 ──
SERVER_NAME = "Relevance-Searcher"
CLIENT_CONFIG_PATH = os.path.join(BASE_DIR, "mcp-clients.json")
LAUNCHER_PATH = os.path.join(BASE_DIR, "run-server.cmd")

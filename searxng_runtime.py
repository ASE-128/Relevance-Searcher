"""本地 SearXNG 实例的生命周期：就绪探测与自动拉起。"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import httpx

import config


def is_ready(url: str | None = None, timeout: float | None = None) -> bool:
    """探测 SearXNG 是否已就绪（/healthz 未 5xx 即视为可用）"""
    base = (url or config.SEARXNG_URL).rstrip("/")
    try:
        resp = httpx.get(f"{base}/healthz", timeout=timeout or config.SEARXNG_HEALTH_TIMEOUT)
        return resp.status_code < 500
    except Exception:
        return False


def bundle_available() -> bool:
    return os.path.isfile(config.SEARXNG_PYTHON) and os.path.isfile(config.SEARXNG_WEBAPP)


def _spawn() -> subprocess.Popen | None:
    env = dict(os.environ)
    env["SEARXNG_SETTINGS_PATH"] = config.SEARXNG_SETTINGS
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # DETACHED_PROCESS：让 SearXNG 独立于 MCP 服务器进程生命周期，可多会话复用
    creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        return subprocess.Popen(
            [config.SEARXNG_PYTHON, config.SEARXNG_WEBAPP],
            cwd=config.SEARXNG_DIR,
            env=env,
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        print(f"[bootstrap] failed to start SearXNG: {exc}", file=sys.stderr)
        return None


def _wait_until_ready(proc: subprocess.Popen) -> None:
    deadline = time.monotonic() + config.SEARXNG_READY_TIMEOUT
    while time.monotonic() < deadline:
        if is_ready():
            print("[bootstrap] SearXNG ready", file=sys.stderr)
            return
        if proc.poll() is not None:
            print(f"[bootstrap] SearXNG exited early (rc={proc.returncode})", file=sys.stderr)
            return
        time.sleep(0.5)
    print(
        f"[bootstrap] SearXNG not ready within {config.SEARXNG_READY_TIMEOUT}s; proceeding",
        file=sys.stderr,
    )


def ensure_running() -> None:
    """MCP 启动时同步拉起 SearXNG（若尚未运行）

    使用 SearXNG 自带的捆绑 Python 启动 webapp.py，cwd 设为 SearXNGforWindows 根目录
    以满足其 settings.yml 解析；子进程 stdout/stderr 重定向到 DEVNULL，避免污染 MCP
    的 stdio 协议通道。
    """
    if not (config.SEARXNG_AUTOSTART and sys.platform == "win32"):
        return
    if is_ready():
        return
    if not bundle_available():
        print("[bootstrap] SearXNG bundle not found; skipping autostart", file=sys.stderr)
        return

    proc = _spawn()
    if proc is None:
        return

    print(f"[bootstrap] starting SearXNG at {config.SEARXNG_URL} ...", file=sys.stderr)
    _wait_until_ready(proc)

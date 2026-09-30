"""mcp-clients.json 的生成与幂等回写。"""

from __future__ import annotations

import json
import os

import config


def build_entry(base_dir: str | None = None) -> dict:
    """按项目根生成服务定义（启动器用绝对路径，便于整目录迁移）"""
    base = base_dir or config.BASE_DIR
    return {
        "type": "stdio",
        "command": "cmd.exe",
        "args": ["/c", os.path.join(base, "run-server.cmd")],
        "env": {"SEARXNG_URL": config.SEARXNG_URL},
    }


def ensure_client_config(cfg_path: str | None = None, base_dir: str | None = None) -> bool:
    """确保 mcp-clients.json 里的启动器路径与当前部署一致。

    Returns:
        True  写回了文件；False 已经一致、无需写回（幂等）
    """
    path = cfg_path or config.CLIENT_CONFIG_PATH
    entry = build_entry(base_dir)

    data: dict = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}

    svc = data.get(config.SERVER_NAME)
    if isinstance(svc, dict) and svc.get("args") == entry["args"]:
        return False

    data[config.SERVER_NAME] = entry
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return True

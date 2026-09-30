"""检索结果的渲染：LLM 上下文文本与 JSON。"""

from __future__ import annotations

import json


def format_as_context(results: list[dict], heading: str = "搜索结果") -> str:
    """格式化为 LLM 上下文（附域名/日期/相关度，便于判断可信度）"""
    if not results:
        return ""
    lines = [f"\n## {heading}\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. **{r['title']}**")
        lines.append(f"   URL: {r['url']}")
        meta: list[str] = []
        if r.get("domain"):
            meta.append(str(r["domain"]))
        if r.get("date"):
            meta.append(str(r["date"]))
        if isinstance(r.get("score"), (int, float)):
            meta.append(f"相关度 {r['score']:.2f}")
        if meta:
            lines.append(f"   质量: {' | '.join(meta)}")
        lines.append(f"   摘要: {r.get('snippet') or '（来源未提供摘要）'}\n")
    return "\n".join(lines)


def format_as_json(results: list[dict]) -> str:
    """格式化为 JSON 字符串"""
    return json.dumps(results, ensure_ascii=False, indent=2)

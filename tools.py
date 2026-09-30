"""MCP 工具的纯逻辑：参数规整 → 检索调用 → 渲染。

不 import FastMCP —— server.py 只把这些函数注册成 MCP 工具，因此它们可以在
没有 MCP 协议栈的情况下被单测直接调用。

参数规整集中在 normalize_*：默认值与上限的唯一来源；工具函数本身只做编排。
"""

from __future__ import annotations

import config
import formatting
import webpage
from pipeline import searcher

CATEGORIES = ("general", "science")


def _clamp(value, low: int, high: int, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return min(max(n, low), high)


def normalize_search_args(
    max_results: int = 8,
    category: str = "general",
    engines: str | None = None,
    time_range: str | None = None,
    dedup: bool = True,
    rewrite: bool = False,
    sort_by: str = "relevance",
) -> dict:
    """搜索类工具的入参规整（钳制上限、兜底非法枚举）"""
    cat = category if category in CATEGORIES else "general"
    return {
        "max_results": _clamp(max_results, 1, config.SEARCH_MAX_RESULTS, 8),
        "categories": cat,
        "engines": engines or None,
        "time_range": time_range or None,
        "dedup": bool(dedup),
        "rewrite": bool(rewrite),
        "sort_by": "recency" if sort_by == "recency" else "relevance",
        "heading": "学术搜索结果" if cat == "science" else "搜索结果",
    }


def normalize_multi_args(
    max_results: int = 5,
    engines: str | None = None,
    time_range: str | None = None,
    rewrite: bool = False,
) -> dict:
    """多组搜索的入参规整"""
    return {
        "max_results": _clamp(max_results, 1, config.SEARCH_MULTI_MAX_RESULTS, 5),
        "engines": engines or None,
        "time_range": time_range or None,
        "rewrite": bool(rewrite),
    }


def web_search(
    query: str,
    max_results: int = 8,
    region: str = "cn-zh",
    category: str = "general",
    engines: str | None = None,
    time_range: str | None = None,
    dedup: bool = True,
    rewrite: bool = False,
    sort_by: str = "relevance",
) -> str:
    """执行一次网页搜索并返回格式化文本"""
    if not query.strip():
        return "搜索关键词为空。"

    args = normalize_search_args(max_results, category, engines, time_range, dedup, rewrite, sort_by)
    results = searcher.search_sync(
        query,
        max_results=args["max_results"],
        region=region,
        categories=args["categories"],
        engines=args["engines"],
        time_range=args["time_range"],
        dedup=args["dedup"],
        rewrite=args["rewrite"],
        sort_by=args["sort_by"],
    )

    if not results:
        return (
            f"未找到与「{query}」相关的结果。"
            "（可能原因：搜索源无结果，或结果被相关性/质量过滤管线剔除——"
            "可尝试去掉「今天/最新」这类填充词、换更具体的关键词，或放宽 time_range。）"
        )

    return formatting.format_as_context(results, heading=args["heading"])


def search_multi(
    queries: list[str],
    max_results: int = 5,
    region: str = "cn-zh",
    engines: str | None = None,
    time_range: str | None = None,
    rewrite: bool = False,
) -> str:
    """并行执行多组搜索并按查询分组渲染"""
    if not queries:
        return "搜索关键词列表为空。"

    args = normalize_multi_args(max_results, engines, time_range, rewrite)
    valid_queries = [q for q in queries if q.strip()]
    if not valid_queries:
        return "搜索关键词列表为空。"

    results_list = searcher.search_multi_sync(
        valid_queries,
        max_results=args["max_results"],
        region=region,
        engines=args["engines"],
        time_range=args["time_range"],
        rewrite=args["rewrite"],
    )

    output_parts = []
    for q, results in zip(valid_queries, results_list):
        heading = f"搜索结果：「{q}」"
        if results:
            output_parts.append(formatting.format_as_context(results, heading=heading))
        else:
            output_parts.append(f"\n## {heading}\n（无结果）\n")

    return "\n".join(output_parts)


def search_format(
    query: str,
    max_results: int = 8,
    region: str = "cn-zh",
    category: str = "general",
    engines: str | None = None,
    time_range: str | None = None,
    dedup: bool = True,
    rewrite: bool = False,
    sort_by: str = "relevance",
) -> str:
    """执行一次搜索并返回 JSON 字符串"""
    if not query.strip():
        return "[]"

    args = normalize_search_args(max_results, category, engines, time_range, dedup, rewrite, sort_by)
    results = searcher.search_sync(
        query,
        max_results=args["max_results"],
        region=region,
        categories=args["categories"],
        engines=args["engines"],
        time_range=args["time_range"],
        dedup=args["dedup"],
        rewrite=args["rewrite"],
        sort_by=args["sort_by"],
    )
    return formatting.format_as_json(results)


def fetch_page(url: str, max_chars: int = config.FETCH_MAX_CHARS) -> str:
    """抓取网页正文"""
    return webpage.fetch_page_text(url, webpage.clamp_max_chars(max_chars))

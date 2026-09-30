"""MCP 搜索服务器 — 相关性搜索引擎

本文件只做三件事：引导解释器 → 注册 MCP 工具 → 启动 stdio 服务。
工具逻辑在 tools.py，网页抓取在 webpage.py，SearXNG 生命周期在 searxng_runtime.py。

搜索降级链: SearXNG → DuckDuckGo → 空结果

环境变量:
  SEARXNG_URL      本地 SearXNG 地址（默认 http://127.0.0.1:8888）
  SEARXNG_TIMEOUT  超时秒数（默认 15）
"""

# ── 第 0 步：解释器引导（必须先于任何第三方导入） ──
try:  # 包模式
    from .bootstrap import ensure_sys_path
except ImportError:  # 脚本模式（python server.py / run-server.cmd）
    from bootstrap import ensure_sys_path

ensure_sys_path()

# ── 第三方导入（依赖上面的引导） ──
from mcp.server.fastmcp import FastMCP  # noqa: E402

# ── 项目内部导入 ──
import client_config  # noqa: E402
import searxng_runtime  # noqa: E402
import tools  # noqa: E402

# ── MCP 服务器 ──
mcp = FastMCP(
    "Relevance-Searcher",
    instructions="""相关性搜索引擎，集成 SearXNG 与 DuckDuckGo 双引擎降级链。
支持普通网页搜索 (general) 和学术搜索 (science) 两种类别。
默认返回格式化的 LLM 上下文文本，便于直接注入提示词。""",
)


# ── 工具注册（函数签名与文档字符串即 MCP schema，改动需同步 tools.py） ──

@mcp.tool(
    name="web_search",
    description="搜索互联网获取实时信息。返回格式化文本，包含标题、URL 和摘要。",
)
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
    """执行一次网页搜索

    Args:
        query: 搜索关键词，尽量具体（如加上时间、地区等限定词）
        max_results: 返回结果数量（默认 8，最大 15）
        region: 区域（cn-zh / us-en / wt-wt，默认 cn-zh）
        category: 搜索类别（general 普通网页 / science 学术搜索）
        engines: 指定 SearXNG 引擎，逗号分隔（如 baidu,sogou / bing,yandex），默认自动选择
        time_range: 时间范围 24h / 7d / 30d，默认不过滤（无日期的结果保留）
        dedup: 是否按 URL 归一 + 标题指纹去重，默认 True
        rewrite: 是否先做查询归一（删「今天/最新/有什么」等填充词），默认 False
        sort_by: 排序方式 relevance（默认）或 recency（按时间半衰期衰减）

    Returns:
        格式化搜索结果文本（Markdown 格式，含域名/日期/相关度标签），可直接注入 LLM 上下文
    """
    return tools.web_search(
        query, max_results, region, category, engines, time_range, dedup, rewrite, sort_by
    )


@mcp.tool(
    name="search_multi",
    description="并行执行多组搜索，返回按查询分组的格式化结果。用于多角度、多关键词同时检索。",
)
def search_multi(
    queries: list[str],
    max_results: int = 5,
    region: str = "cn-zh",
    engines: str | None = None,
    time_range: str | None = None,
    rewrite: bool = False,
) -> str:
    """并行执行多组搜索

    Args:
        queries: 搜索关键词列表（2-5 个为宜）
        max_results: 每组返回结果数量（默认 5，最大 10）
        region: 区域（cn-zh / us-en / wt-wt，默认 cn-zh）
        engines: 指定 SearXNG 引擎，逗号分隔，默认自动选择
        time_range: 时间范围 24h / 7d / 30d，默认不过滤
        rewrite: 是否先做查询归一，默认 False

    Returns:
        多组搜索结果（每组以 ## 标题分隔）
    """
    return tools.search_multi(queries, max_results, region, engines, time_range, rewrite)


@mcp.tool(
    name="search_format",
    description="将搜索结果原始数据格式化为 JSON 字符串，供程序化使用。",
)
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
    """执行搜索并以 JSON 格式返回原始结果

    Args:
        query: 搜索关键词
        max_results: 返回结果数量（默认 8，最大 15）
        region: 区域（cn-zh / us-en / wt-wt，默认 cn-zh）
        category: 搜索类别（general / science）
        engines: 指定 SearXNG 引擎，逗号分隔，默认自动选择
        time_range: 时间范围 24h / 7d / 30d，默认不过滤
        dedup: 是否去重，默认 True
        rewrite: 是否先做查询归一，默认 False
        sort_by: relevance（默认）或 recency

    Returns:
        JSON 格式的搜索结果 [{title, url, snippet, score, domain, date}, ...]
    """
    return tools.search_format(
        query, max_results, region, category, engines, time_range, dedup, rewrite, sort_by
    )


@mcp.tool(
    name="fetch_page",
    description="根据 URL 抓取网页并提取正文文本，供 LLM 直接阅读网页内容（通常配合 web_search 返回的链接使用）。",
)
def fetch_page(url: str, max_chars: int = 4000) -> str:
    """抓取指定网页并提取正文

    Args:
        url: 网页完整 URL（http/https）
        max_chars: 返回正文的最大字符数（默认 4000，最大 20000）

    Returns:
        网页标题 + 正文纯文本，或错误提示
    """
    return tools.fetch_page(url, max_chars)


# ── 启动入口 ──

def main() -> None:
    """以 stdio 模式运行 MCP 服务器"""
    client_config.ensure_client_config()
    searxng_runtime.ensure_running()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

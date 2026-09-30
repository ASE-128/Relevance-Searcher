"""MCP 搜索服务器 — 相关性搜索引擎

通过 stdio 与 MCP 客户端通信，提供以下工具:
  - web_search: 单组搜索（支持 general / science 类别）
  - search_multi: 并行多组搜索
  - search_format: 将原始结果格式化为 LLM 上下文文本

搜索降级链: SearXNG → DuckDuckGo → 空结果

环境变量:
  SEARXNG_URL      本地 SearXNG 地址（默认 http://127.0.0.1:8888）
  SEARXNG_TIMEOUT  超时秒数（默认 15）
"""

# ── 第 0 步：将 _vendored 目录加入 sys.path（必须先于所有第三方导入） ──
import sys
import os
import time
import subprocess
import json
import re

_VENDORED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_vendored")
if os.path.isdir(_VENDORED) and _VENDORED not in sys.path:
    sys.path.insert(0, _VENDORED)

# pywin32 的 .pth 文件不会被手动 sys.path 触发，需要额外添加子路径
for _sub in ("win32", "win32\\lib", "pythonwin"):
    _p = os.path.join(_VENDORED, _sub)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

# Bootstrap pywin32 DLL 目录（pywintypes312.dll 等）
try:
    import pywin32_system32  # noqa: F401
    for _path in pywin32_system32.__path__:
        if os.path.isdir(_path):
            os.add_dll_directory(_path)
except ImportError:
    pass

# ── 第三方导入（优先从 _vendored 解析） ──
import httpx
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP

# ── 项目内部导入 ──
try:
    from .searcher import searcher
except ImportError:
    from searcher import searcher

# ── 创建 MCP 服务器 ──
mcp = FastMCP(
    "Relevance-Searcher",
    instructions="""相关性搜索引擎，集成 SearXNG 与 DuckDuckGo 双引擎降级链。
支持普通网页搜索 (general) 和学术搜索 (science) 两种类别。
默认返回格式化的 LLM 上下文文本，便于直接注入提示词。""",
)


# ── 工具定义 ──

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
    if not query.strip():
        return "搜索关键词为空。"

    max_results = min(max(max_results, 1), 15)
    categories = category if category in ("general", "science") else "general"

    results = searcher.search_sync(
        query,
        max_results=max_results,
        region=region,
        categories=categories,
        engines=engines,
        time_range=time_range,
        dedup=dedup,
        rewrite=rewrite,
        sort_by=sort_by,
    )

    if not results:
        return (
            f"未找到与「{query}」相关的结果。"
            "（可能原因：搜索源无结果，或结果被相关性/质量过滤管线剔除——"
            "可尝试去掉「今天/最新」这类填充词、换更具体的关键词，或放宽 time_range。）"
        )

    heading = "学术搜索结果" if category == "science" else "搜索结果"
    return searcher.format_as_context(results, heading=heading)


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
    if not queries:
        return "搜索关键词列表为空。"

    max_results = min(max(max_results, 1), 10)
    valid_queries = [q for q in queries if q.strip()]
    if not valid_queries:
        return "搜索关键词列表为空。"

    results_list = searcher.search_multi_sync(
        valid_queries,
        max_results=max_results,
        region=region,
        engines=engines,
        time_range=time_range,
        rewrite=rewrite,
    )

    output_parts = []
    for q, results in zip(valid_queries, results_list):
        heading = f"搜索结果：「{q}」"
        if results:
            output_parts.append(searcher.format_as_context(results, heading=heading))
        else:
            output_parts.append(f"\n## {heading}\n（无结果）\n")

    return "\n".join(output_parts)


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
    if not query.strip():
        return '[]'

    max_results = min(max(max_results, 1), 15)
    categories = category if category in ("general", "science") else "general"

    results = searcher.search_sync(
        query,
        max_results=max_results,
        region=region,
        categories=categories,
        engines=engines,
        time_range=time_range,
        dedup=dedup,
        rewrite=rewrite,
        sort_by=sort_by,
    )

    return searcher.format_as_json(results)


# ── fetch：根据链接抓取网页正文 ──

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
# 抓取时需要剔除的噪音区块（导航/广告/评论等）
_FETCH_SKIP_SELECTORS = (
    "script,style,noscript,nav,header,footer,aside,form,iframe,svg,canvas",
    ".nav,.menu,.sidebar,.ad,.ads,.advert,.advertisement,.comment,.comments,"
    ".footer,.header,.share,.related",
    "#nav,#menu,#sidebar,#footer,#header,#comments,#related",
)


def _decode_html(content: bytes) -> str:
    """按常见编码顺序解码，兼顾中文站（gbk）与 utf-8"""
    for enc in ("utf-8", "gbk", "gb2312", "big5"):
        try:
            return content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return content.decode("utf-8", errors="replace")


def _fetch_webpage(url: str, max_chars: int = 4000) -> str:
    """抓取网页并提取正文纯文本（供 LLM 阅读）"""
    if not re.match(r"https?://", url, re.I):
        return "无效 URL：仅支持 http/https 链接。"

    try:
        resp = httpx.get(url, headers=_FETCH_HEADERS, follow_redirects=True, timeout=15.0)
    except Exception as exc:
        return f"抓取失败：{exc}"

    if resp.status_code >= 400:
        return f"抓取失败：HTTP {resp.status_code}"

    soup = BeautifulSoup(_decode_html(resp.content), "html.parser")
    for node in soup.select(", ".join(_FETCH_SKIP_SELECTORS)):
        node.decompose()

    title = soup.title.get_text(strip=True) if soup.title and soup.title.get_text(strip=True) else url

    parts = []
    for el in soup.find_all(
        ["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "pre", "blockquote", "td", "figcaption"]
    ):
        text = el.get_text(" ", strip=True)
        if len(text) >= 8:
            parts.append(text)

    body = "\n".join(parts)
    if not body:
        return f"未能从 {url} 提取到正文。"

    out = f"标题：{title}\n来源：{url}\n\n{body}"
    if len(out) > max_chars:
        out = out[:max_chars] + "…[截断]"
    return out


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
    max_chars = min(max(max_chars, 500), 20000)
    return _fetch_webpage(url, max_chars)


# ── 启动入口 ──

# 基于 __file__ 解析的根目录，保证任意位置部署都可运行（不依赖 CWD / 绝对路径）
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_SEARXNG_DIR = os.path.join(_BASE_DIR, "SearXNGforWindows")
_SEARXNG_PYTHON = os.path.join(_SEARXNG_DIR, "python", "python.exe")
_SEARXNG_WEBAPP = os.path.join(
    _SEARXNG_DIR, "python", "Lib", "site-packages", "searx", "webapp.py"
)
_SEARXNG_SETTINGS = os.path.join(_SEARXNG_DIR, "config", "settings.yml")

# SearXNG 地址可通过环境变量覆盖（与 searcher 保持一致）
_SEARXNG_URL = os.getenv("SEARXNG_URL", "http://127.0.0.1:8888")
_SEARXNG_AUTOSTART = os.getenv("SEARXNG_AUTOSTART", "1") not in ("0", "false", "False")
_SEARXNG_READY_TIMEOUT = int(os.getenv("SEARXNG_READY_TIMEOUT", "60"))


def _searxng_ready() -> bool:
    """探测 SearXNG 是否已就绪（/healthz 200 即视为可用）"""
    url = _SEARXNG_URL.rstrip("/")
    try:
        resp = httpx.get(f"{url}/healthz", timeout=5.0)
        return resp.status_code < 500
    except Exception:
        return False


def _ensure_searxng() -> None:
    """MCP 启动时同步拉起 SearXNG（若尚未运行）

    使用 SearXNG 自带的捆绑 Python（3.11）启动 webapp.py，cwd 设为
    SearXNGforWindows 根目录以满足其 settings.yml 解析；子进程 stdout/stderr
    重定向到 DEVNULL，避免污染 MCP 的 stdio 协议通道。
    """
    if not (_SEARXNG_AUTOSTART and sys.platform == "win32"):
        return
    if _searxng_ready():
        return

    if not (os.path.isfile(_SEARXNG_PYTHON) and os.path.isfile(_SEARXNG_WEBAPP)):
        print("[bootstrap] SearXNG bundle not found; skipping autostart", file=sys.stderr)
        return

    env = dict(os.environ)
    env["SEARXNG_SETTINGS_PATH"] = _SEARXNG_SETTINGS
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # DETACHED_PROCESS：让 SearXNG 独立于 MCP 服务器进程生命周期，可多会话复用
    creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)

    try:
        proc = subprocess.Popen(
            [_SEARXNG_PYTHON, _SEARXNG_WEBAPP],
            cwd=_SEARXNG_DIR,
            env=env,
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        print(f"[bootstrap] failed to start SearXNG: {exc}", file=sys.stderr)
        return

    print(f"[bootstrap] starting SearXNG at {_SEARXNG_URL} ...", file=sys.stderr)
    deadline = time.monotonic() + _SEARXNG_READY_TIMEOUT
    while time.monotonic() < deadline:
        if _searxng_ready():
            print("[bootstrap] SearXNG ready", file=sys.stderr)
            return
        if proc.poll() is not None:
            print(f"[bootstrap] SearXNG exited early (rc={proc.returncode})", file=sys.stderr)
            return
        time.sleep(0.5)

    print(f"[bootstrap] SearXNG not ready within {_SEARXNG_READY_TIMEOUT}s; proceeding", file=sys.stderr)


def _ensure_client_config() -> None:
    """首次启动时把实际路径写入 mcp-clients.json（幂等）

    用 __file__ 解析出的真实项目根生成启动器绝对路径并写入服务定义，
    这样整目录迁移到任意路径后，无需手工修改 mcp-clients.json。
    仅当文件缺失或路径与当前不一致时才写回，避免重复 IO。
    """
    cfg_path = os.path.join(_BASE_DIR, "mcp-clients.json")
    launcher = os.path.join(_BASE_DIR, "run-server.cmd")
    target_args = ["/c", launcher]

    data: dict = {}
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}

    svc = data.get("Relevance-Searcher")
    if isinstance(svc, dict) and svc.get("args") == target_args:
        return  # 路径已正确，无需重写

    data["Relevance-Searcher"] = {
        "type": "stdio",
        "command": "cmd.exe",
        "args": target_args,
        "env": {"SEARXNG_URL": os.getenv("SEARXNG_URL", "http://127.0.0.1:8888")},
    }
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    """以 stdio 模式运行 MCP 服务器"""
    _ensure_client_config()
    _ensure_searxng()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

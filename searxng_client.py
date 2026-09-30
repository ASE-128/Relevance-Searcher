"""SearXNG JSON API 客户端（只负责一次 HTTP 查询）。"""

import httpx

import config
from textutil import _clean_snippet

class SearXNGClient:
    """SearXNG JSON API 客户端"""

    def __init__(self, base_url: str | None = None, timeout: int | None = None):
        self.base_url = (base_url or config.SEARXNG_URL).rstrip("/")
        self.timeout = timeout if timeout is not None else config.SEARXNG_TIMEOUT
        self._client = httpx.Client(timeout=self.timeout, follow_redirects=True)

    def search(
        self,
        query: str,
        max_results: int = 8,
        language: str = "zh-CN",
        categories: str = "general",
        engines: str | None = None,
    ) -> list[dict]:
        """同步搜索 SearXNG，返回 [{title, url, snippet}]

        可用 engines: baidu, sogou, quark, 360search, bing 等，逗号分隔。
        """
        if not query.strip():
            return []

        params = {
            "q": query,
            "format": "json",
            "language": language,
            "categories": categories,
        }
        if engines:
            params["engines"] = engines

        try:
            resp = self._client.get(f"{self.base_url}/search", params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        results = data.get("results", [])
        if not results:
            return []

        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": _clean_snippet(r.get("content", "")),
            }
            for r in results[:max_results]
            if r.get("url")
        ]


# ── 文本质量预过滤器（增强版，加入中文特定信号） ──

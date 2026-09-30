"""检索管线编排：抓取 → 去重 → 黑名单/质量过滤 → 相关性排序 → 富化 → 输出。"""

import config
import formatting
from filters import (_ACADEMIC_DOMAIN_BOOST, _ACADEMIC_PREF_DOMAINS,
                     _TUTORIAL_DOMAIN_PENALTY, _TUTORIAL_DOMAINS, DomainBlacklist,
                     QualityPreFilter)
from scoring import RelevanceScorer
from searxng_client import SearXNGClient
from textutil import (_clean_snippet, _dedup_results, _enrich_results, _extract_domain,
                      _to_searxng_lang)

DDGS_AVAILABLE = False
try:
    from ddgs import DDGS
    DDGS_AVAILABLE = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        DDGS_AVAILABLE = True
    except ImportError:
        DDGS_AVAILABLE = False

class WebSearcher:
    """多搜索引擎封装，集成完整过滤管线"""

    def __init__(self):
        self._searxng = SearXNGClient(config.SEARXNG_URL, config.SEARXNG_TIMEOUT)
        self._quality_filter = QualityPreFilter()
        self._domain_blacklist = DomainBlacklist()
        self._relevance_scorer = RelevanceScorer()
        self._ddgs = DDGS() if DDGS_AVAILABLE else None

    def _rank_academic(self, results: list[dict], query: str,
                       max_results: int) -> list[dict]:
        """science 类别专用排序：学术域加分 + 双门槛过滤"""
        if not results:
            return []

        scored = []
        for r in results:
            # 论文标题通常不按查询关键字字面出现，故学术源放低门槛；
            # 非学术源（教程/词典/博客）则必须强相关才能入选
            base = self._relevance_scorer.score(r, query)
            dom = _extract_domain(r.get("url", ""))
            is_academic = any(
                dom == d or dom.endswith("." + d) for d in _ACADEMIC_PREF_DOMAINS
            )
            is_tutorial = (not is_academic) and any(
                dom == d or dom.endswith("." + d) for d in _TUTORIAL_DOMAINS
            )
            final = base + (_ACADEMIC_DOMAIN_BOOST if is_academic else
                            -_TUTORIAL_DOMAIN_PENALTY if is_tutorial else 0.0)
            floor = 0.06 if is_academic else 0.16
            if final >= floor:
                scored.append((final, r))

        scored.sort(key=lambda x: -x[0])
        kept = [r for _, r in scored]
        if len(kept) > max_results:
            kept = kept[:max_results]
        return kept

    def search_sync(
        self,
        query: str,
        max_results: int = 8,
        region: str = "cn-zh",
        categories: str = "general",
        engines: str | None = None,
        time_range: str | None = None,
        dedup: bool = True,
        rewrite: bool = False,
        sort_by: str = "relevance",
    ) -> list[dict]:
        """同步搜索，经过完整过滤管线后返回 [{title, url, snippet, score, domain, date}]

        新增参数（都有默认值，不传即保持原有行为）：
          engines    指定 SearXNG 引擎（逗号分隔），覆盖内置引擎分组
          time_range 时间范围 24h / 7d / 30d（默认不过滤；无日期的结果保留）
          dedup      按 URL 归一 + 标题指纹去重（默认开）
          rewrite    先做查询归一（删填充词、保留引号短语）
          sort_by    relevance（默认）或 recency（按半衰期衰减重排）
        """
        if not query.strip():
            return []

        if rewrite:
            query = normalize_query(query)

        language = _to_searxng_lang(region)
        results: list[dict] = []

        # 1. SearXNG — 多引擎并行
        fetch_count = max(20, max_results * 4)
        if engines:
            engine_groups = [engines]
        elif categories == "science":
            # 学术类别：优先聚合学术数据库引擎，bing 兜底补网页覆盖面
            # （baidu/sogou 无 science 类别，故学术类别不请求它们）
            engine_groups = [
                "crossref,semantic_scholar,openalex,pubmed,core,base,unpaywall",
                "bing",
            ]
        elif language in ("zh-CN", "zh-cn", "zh"):
            # 中文查询：使用多个中文引擎
            # （quark 已被排除：其阿里 X5SEC 反爬在重复请求下必然触发验证，无法静态绕过）
            engine_groups = [
                "baidu,sogou",
                "bing",
            ]
        else:
            # 英文 general：多源聚合 + 兜底
            # （bing/yandex/searx 速度快且未封禁；单一源故障或反爬时仍可兜底）
            engine_groups = ["bing,yandex,searx"]

        seen_urls: set[str] = set()
        raw_batch: list[dict] = []

        for eg in engine_groups:
            try:
                raw = self._searxng.search(
                    query,
                    max_results=fetch_count,
                    language=language,
                    categories=categories,
                    engines=eg,
                )
                for r in raw:
                    url = r.get("url", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        raw_batch.append(r)
            except Exception:
                continue

        if raw_batch:
            # 管线：标题级去重 → 域名黑名单 → 质量过滤 → 排序 → 截断
            if dedup:
                raw_batch = _dedup_results(raw_batch)
            filtered = self._domain_blacklist.filter(raw_batch)
            filtered = self._quality_filter.filter(filtered)
            if categories == "science":
                results = self._rank_academic(filtered, query, max_results)
            else:
                results = self._relevance_scorer.filter_and_sort(
                    filtered, query, min_score=0.12
                )

        if results:
            results = _enrich_results(results, time_range=time_range, sort_by=sort_by)
            if len(results) > max_results:
                results = results[:max_results]
            return results

        # 2. DuckDuckGo fallback
        if self._ddgs is None:
            return []

        try:
            # 本机可用的 ddgs 后端里仅 yandex 可达且英文质量高
            # （html/lite 在该版本已移除；google/duckduckgo/brave 等被 CN 网络屏蔽）
            raw = self._ddgs.text(
                query, region=region, max_results=max_results, backend="yandex"
            )
            results = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": _clean_snippet(r.get("body", "")),
                }
                for r in raw
                if r.get("href") and r.get("body")
            ]
            # 对 DDG 结果也做黑名单 + 质量过滤 + 去重
            results = self._domain_blacklist.filter(results)
            results = self._quality_filter.filter(results)
            if dedup:
                results = _dedup_results(results)
            # 兜底结果同样过相关性评分（阈值放宽：ddgs 返回的片段更粗糙）
            results = self._relevance_scorer.filter_and_sort(
                results, query, min_score=0.05
            )
            return _enrich_results(results, time_range=time_range, sort_by=sort_by)
        except Exception:
            return []

    def search_multi_sync(
        self,
        queries: list[str],
        max_results: int = 5,
        region: str = "cn-zh",
        engines: str | None = None,
        time_range: str | None = None,
        rewrite: bool = False,
    ) -> list[list[dict]]:
        """同步执行多组搜索"""
        return [
            self.search_sync(
                q,
                max_results=max_results,
                region=region,
                engines=engines,
                time_range=time_range,
                rewrite=rewrite,
            )
            for q in queries
        ]

    def format_as_context(self, results: list[dict], heading: str = "搜索结果") -> str:
        """向后兼容的委托；实现见 formatting.py"""
        return formatting.format_as_context(results, heading=heading)

    def format_as_json(self, results: list[dict]) -> str:
        """向后兼容的委托；实现见 formatting.py"""
        return formatting.format_as_json(results)


# 进程内单例：工具层与历史调用方共用同一实例（缓存与过滤器状态复用）
searcher = WebSearcher()


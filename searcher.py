"""向后兼容的外观层（facade）。

真正的实现已经拆到：
  textutil.py        文本工具（分词/归一/日期/去重/富化）
  filters.py         域名黑名单、参考类站点守卫、质量预过滤
  scoring.py         查询-结果相关性评分
  searxng_client.py  SearXNG JSON API 客户端
  pipeline.py        检索管线编排（WebSearcher 与 searcher 单例）
  formatting.py      结果渲染

保留本模块的原因：历史调用方与回归测试从 searcher 模块导入符号。
新代码请直接 import 上述模块。
"""

from __future__ import annotations

import config
from filters import (
    DomainBlacklist,
    QualityPreFilter,
    _ACADEMIC_DOMAIN_BOOST,
    _ACADEMIC_PREF_DOMAINS,
    _DICTIONARY_MARKERS,
    _DOMAIN_BLACKLIST,
    _REFERENCE_DOMAINS,
    _TUTORIAL_DOMAIN_PENALTY,
    _TUTORIAL_DOMAINS,
    _WEATHER_MARKERS,
    _WEATHER_ONLY_DOMAINS,
)
from formatting import format_as_context, format_as_json
from pipeline import DDGS_AVAILABLE, WebSearcher, searcher
from scoring import RelevanceScorer
from searxng_client import SearXNGClient
from textutil import (
    _clean_snippet,
    _cjk_ratio,
    _dedup_results,
    _enrich_results,
    _extract_date,
    _extract_domain,
    _has_cjk,
    _is_distinctive_en,
    _is_distinctive_zh,
    _normalize_url,
    _recency_score,
    _title_fingerprint,
    _to_searxng_lang,
    _tokenize,
    _tokenize_chinese,
    _tokenize_english,
    _within_time_range,
    normalize_query,
)

# 兼容旧的模块级常量（原 searcher.py 里读 env 得到，现由 config 统一持有）
SEARXNG_URL = config.SEARXNG_URL
SEARXNG_TIMEOUT = config.SEARXNG_TIMEOUT

"""搜索引擎 — 独立版，适配为 MCP Server

搜索优先级:
  1. SearXNG（本地元搜索引擎，需自行部署）
  2. DuckDuckGo（ddgs 库，作为 fallback）
  3. 空结果（两者均不可用时）

质量过滤体系（参考 semantic-relevance 的评分逻辑）:
  1. QualityPreFilter — 文本质量信号（乱码/重复/格式异常）
  2. DomainBlacklist — 域名级黑名单过滤
  3. ChineseContentScorer — 中文内容质量评分（SEO 检测/广告识别）
  4. RelevanceScorer — 查询-结果关键词相关性评分
  5. CompositeRanker — 综合排序（质量分 × 相关分）
"""

import os
import re
import math
import json
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional

import httpx

# ── 配置（可通过环境变量覆盖） ──
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://127.0.0.1:8888")
SEARXNG_TIMEOUT = int(os.getenv("SEARXNG_TIMEOUT", "15"))
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

# ── 中文停用词（用于相关性分析） ──
_CHINESE_STOP = frozenset({
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那", "些",
    "为", "所", "以", "能", "而", "之", "与", "及", "被", "让", "把",
    "但", "却", "可", "如果", "虽然", "因为", "所以", "不过", "而且",
    "或者", "还是", "只是", "但是", "然而", "那么", "什么", "怎么",
    "如何", "哪", "谁", "从", "对", "向", "在", "于", "当", "将",
    "已经", "还", "又", "再", "才", "就", "便", "已", "曾", "正在",
    "吗", "呢", "吧", "啊", "嘛", "噢", "哦", "嗯", "哈", "呀",
    "这个", "那个", "这些", "那些", "这里", "那里", "哪里",
    "可以", "应该", "可能", "需要", "能够", "必须", "一定",
    "通过", "进行", "使用", "包括", "以及", "其中", "其他",
    "目前", "当前", "之前", "之后", "同时", "此外", "因此",
    "用于", "基于", "关于", "针对", "按照", "根据",
})

# ── 低质量域名黑名单 ──
_DOMAIN_BLACKLIST = frozenset({
    # 游戏下载/攻略站
    "3dmgame.com", "dl.3dmgame.com", "ali213.net", "game.ali213.net",
    "游侠网", "游民星空", "gamersky.com",
    # 低质问答/爬虫站
    "zhidao.baidu.com-q", "zybang.com", "wenda.so.com",
    "jianshu.com", "toutiao.com", "sohu.com", "163.com",
    # 字典/词典爬虫站（非权威）
    "hgcha.com", "chagushici.com", "hanyuguoxue.com",
    "gushici.net", "shidianguji.com",
    # SEO 聚合站
    "ufanv.cn", "baike.sogou.com", "bajiu.cn",
    # 资源下载站
    "cr173.com", "downcc.com", "onlinedown.net",
    "pc6.com", "xitongzhijia.net",
    # 低质 AI 工具导航
    "ai-bot.cn", "aigc.cn", "aivertical.chinacloudsites.cn",
})

# ── science 学术类别的排序偏好 ──
_ACADEMIC_PREF_DOMAINS = frozenset({
    # 学术/出版方
    "arxiv.org", "doi.org", "semanticscholar.org", "openalex.org",
    "crossref.org", "core.ac.uk", "base-search.net", "researchgate.net",
    "nature.com", "springer.com", "sciencedirect.com", "cell.com",
    "science.org", "ieeexplore.ieee.org", "acm.org", "wiley.com",
    "mdpi.com", "tandfonline.com", "plos.org", "frontiersin.org",
    "elifesciences.org", "biorxiv.org", "medrxiv.org", "ssrn.com",
    # 学术检索库 / 期刊门户
    "ncbi.nlm.nih.gov", "pubmed.ncbi.nlm.nih.gov", "cnki.net",
    "wanfangdata.com.cn", "cqvip.com", "baai.ac.cn",
    # 教育/学术机构域名后缀（.edu / .edu.cn / .ac.cn / .gov.cn / .org.cn）
    "edu", "edu.cn", "ac.cn", "gov.cn", "org.cn",
})
_TUTORIAL_DOMAINS = frozenset({
    # 教程 / 博客聚合站（学术查询时降权）
    "runoob.com", "csdn.net", "zhihu.com", "cnblogs.com", "juejin.cn",
    "segmentfault.com", "51cto.com", "oschina.net", "infoq.cn",
})
_ACADEMIC_DOMAIN_BOOST = 0.28
_TUTORIAL_DOMAIN_PENALTY = 0.20

# 英文高频/无区分度词：查询拆词后只命中这些词并不代表相关
# （如 "latest AI chip news" 只命中 latest/news 的词典页不应入选）
_EN_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "of", "to", "for", "in",
    "on", "with", "at", "by", "from", "into", "about", "as", "is",
    "are", "was", "be", "been", "it", "its", "this", "that", "these",
    "those", "what", "how", "when", "where", "why", "who", "which",
    "latest", "news", "new", "today", "tomorrow", "yesterday", "now",
    "best", "top", "guide", "review", "reviews", "list", "good",
    "what", "definition", "meaning", "means",
})


def _is_distinctive_en(token: str) -> bool:
    """英文词是否具有区分度：排除停用词与纯数字/年份"""
    return bool(token) and token not in _EN_STOPWORDS and not token.isdigit()


# ── 中文填充词：命中它们不代表相关（对应 _EN_STOPWORDS 的中文部分） ──
_ZH_NOISE = frozenset({
    "今天", "今日", "昨天", "明天", "最新", "最近", "近期", "目前", "现在",
    "值得", "关注", "看点", "热点", "要点", "重要", "方向", "方面",
    "新闻", "消息", "资讯", "动态", "进展", "情况",
    "推荐", "有哪些", "有什么", "哪些", "怎么样", "如何",
    "介绍", "盘点", "汇总", "整理", "大全", "合集",
})

# 查询归一时真正可以安全删掉的填充词（比 _ZH_NOISE 保守：保留「方向/新闻」等话题词）
_QUERY_FILLER = frozenset({
    "今天", "今日", "昨天", "明天", "最近", "最新", "近期", "目前", "现在",
    "值得", "关注", "有什么", "有哪些", "哪些", "怎么样", "如何",
    "介绍", "盘点", "汇总", "整理", "推荐",
})


# 填充词用到的所有单字：横跨两个填充词的二元组（如「方向新闻」切出的「向新」）
# 全由这些字组成，属于切分噪声，不应参与打分
_ZH_NOISE_CHARS = frozenset(ch for w in _ZH_NOISE for ch in w)


def _is_distinctive_zh(token: str) -> bool:
    """中文词是否具有区分度：单字、填充词与跨词切分噪声都不算

    - 单字：「大模型」会拆出「大」，正是词典页/天气页混入的主要来源
    - 跨词噪声：「方向新闻」会切出「向新」，会稀释真实命中
    """
    if not token or len(token) < 2:
        return False
    if token in _ZH_NOISE:
        return False
    if all(ch in _ZH_NOISE_CHARS for ch in token):
        return False
    return True


def normalize_query(query: str) -> str:
    """查询归一：删填充/时效词，保留引号短语；清空时退回原查询"""
    if not query or not query.strip():
        return query
    parts = re.split(r'("[^"]+")', query.strip())
    out: list[str] = []
    for part in parts:
        if part.startswith('"') and part.endswith('"') and len(part) > 2:
            out.append(part)
            continue
        seg = part
        for w in sorted(_QUERY_FILLER, key=len, reverse=True):
            seg = seg.replace(w, " ")
        seg = re.sub(r"[的了是和与及、，。？！?,.!]+", " ", seg)
        seg = re.sub(r"\s+", " ", seg).strip()
        if seg:
            out.append(seg)
    cleaned = " ".join(out).strip()
    return cleaned or query.strip()


# ── 参考类/非内容类站点（词典、天气、字词解释）：域名 + 内容特征双判据 ──
_REFERENCE_DOMAINS = frozenset({
    "baike.baidu.com", "zdic.net", "hgcha.com", "hanyuguoxue.com",
    "gushici.net", "shidianguji.com", "chazidian.com", "guoxuedashi.net",
    "zidian.tqdhjs.com", "cidianwang.com", "hanyu.baidu.com",
    "tianqi.com", "weather.com.cn", "tianqihoubao.com", "weather.cn",
    "fanyi.baidu.com", "iciba.com", "dict.youdao.com",
    "rili.com.cn", "wannianli.tianqi.com", "gjcha.com",
})

# 纯天气站：标题里出现「天气预报」就足以判定为非内容页
_WEATHER_ONLY_DOMAINS = frozenset({
    "tianqi.com", "weather.com.cn", "tianqihoubao.com", "weather.cn",
    "rili.com.cn", "wannianli.tianqi.com",
})
_DICTIONARY_MARKERS = frozenset({
    "拼音", "部首", "笔画", "笔顺", "组词", "释义", "汉字", "甲骨文",
    "说文", "字义", "词典", "字典", "汉典", "读音", "同义词",
})
_WEATHER_MARKERS = frozenset({
    "天气预报", "气温", "降水", "湿度", "空气质量", "风力", "摄氏度", "℃", "24小时",
})

# ── 日期抽取 / 时间范围 / 时间衰减 ──
_RE_DATE_FULL = re.compile(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})")
_RE_DATE_MONTH = re.compile(r"(20\d{2})[-/年](\d{1,2})月?")
_RE_DATE_MD = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_RE_DATE_REL = re.compile(r"(\d+)\s*(分钟|小时|天)前")


def _extract_date(text: str) -> str:
    """尽力从文本抽取日期，返回 YYYY-MM-DD；抽不到返回空串"""
    if not text:
        return ""
    m = _RE_DATE_FULL.search(text)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    m = _RE_DATE_MONTH.search(text)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return f"{y:04d}-{mo:02d}-01"
    m = _RE_DATE_MD.search(text)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{datetime.now().year:04d}-{mo:02d}-{d:02d}"
    m = _RE_DATE_REL.search(text)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = (timedelta(minutes=n) if unit == "分钟"
                 else timedelta(hours=n) if unit == "小时"
                 else timedelta(days=n))
        return (datetime.now() - delta).strftime("%Y-%m-%d")
    return ""


def _within_time_range(date_str: str, time_range: str) -> bool:
    """无日期或范围非法时一律保留结果"""
    days = {"24h": 1, "7d": 7, "30d": 30}.get(time_range or "")
    if days is None or not date_str:
        return True
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        return True
    return (datetime.now() - d) <= timedelta(days=days)


def _recency_score(date_str: str, half_life_days: float = 7.0) -> float:
    """时间衰减分（0-1），无日期给 0.5 中位值"""
    if not date_str:
        return 0.5
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        return 0.5
    age_days = max((datetime.now() - d).total_seconds() / 86400.0, 0.0)
    return math.exp(-math.log(2) / half_life_days * age_days)


# ── 去重辅助 ──

def _normalize_url(url: str) -> str:
    """去掉 query / fragment，用于识别同一条目（微信的 timestamp/signature 不同）"""
    return re.sub(r"[?#].*$", "", (url or "").strip().lower()).rstrip("/")


def _title_fingerprint(title: str) -> str:
    """标题指纹：去掉标点与空白后取前 40 字"""
    t = re.sub(r"[\s\W_]+", "", (title or "").lower(), flags=re.UNICODE)
    return t[:40]


def _dedup_results(results: list[dict]) -> list[dict]:
    """按 URL 归一 + 标题指纹去重（保留先出现的一条）"""
    seen_url: set[str] = set()
    seen_title: set[str] = set()
    kept: list[dict] = []
    for r in results:
        nu = _normalize_url(r.get("url", ""))
        fp = _title_fingerprint(r.get("title", ""))
        if nu and nu in seen_url:
            continue
        if len(fp) >= 8 and fp in seen_title:
            continue
        if nu:
            seen_url.add(nu)
        if len(fp) >= 8:
            seen_title.add(fp)
        kept.append(r)
    return kept


def _enrich_results(results: list[dict], time_range: str | None = None,
                    sort_by: str = "relevance") -> list[dict]:
    """补充 domain / date / score 字段，并可选做时间过滤与时间排序"""
    enriched: list[dict] = []
    for r in results:
        item = dict(r)
        text = f"{item.get('title', '')} {item.get('snippet', '')}"
        item["domain"] = _extract_domain(item.get("url", ""))
        item["date"] = _extract_date(text) or _extract_date(item.get("url", ""))
        if item.get("score") is None:
            item["score"] = None
        if time_range and item["date"] and not _within_time_range(item["date"], time_range):
            continue
        enriched.append(item)

    def _key(x: dict):
        score = x.get("score")
        score = score if isinstance(score, (int, float)) else 0.0
        if sort_by == "recency":
            return (_recency_score(x.get("date") or ""), score)
        return (score, _recency_score(x.get("date") or ""))

    enriched.sort(key=_key, reverse=True)
    return enriched


_CJK_RE = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]')
_ALNUM_RE = re.compile(r'[A-Za-z\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]')


def _has_cjk(text: str) -> bool:
    """文本是否含中文字符"""
    return bool(_CJK_RE.search(text or ""))


def _cjk_ratio(text: str) -> float:
    """中文/字母的占比（0-1），用于判断结果主要是中文还是英文"""
    total = len(_ALNUM_RE.findall(text or ""))
    if total == 0:
        return 0.0
    return len(_CJK_RE.findall(text or "")) / total


# ── 辅助函数 ──

def _clean_snippet(text: str, max_len: int = 300) -> str:
    """清洗并截断片段文本"""
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:max_len] + "..." if len(text) > max_len else text


def _to_searxng_lang(region: str) -> str:
    """将 DDGS 风格的 region 转换为 SearXNG 可识别的 language 参数"""
    mapping = {
        "cn-zh": "zh-CN",
        "zh-cn": "zh-CN",
        "us-en": "en-US",
        "en-us": "en-US",
        "wt-wt": "wt-wt",
    }
    return mapping.get(region.lower().strip(), region)


def _extract_domain(url: str) -> str:
    """从 URL 提取域名"""
    m = re.search(r"https?://([^/]+)", url)
    return m.group(1).lower() if m else ""


def _tokenize_chinese(text: str) -> list[str]:
    """中文分词：基于字符二元组 + 已知词汇匹配的简单方案"""
    tokens = []
    # 提取连续中文字符块
    chunks = re.findall(r"[\u4e00-\u9fff]+", text)
    for chunk in chunks:
        # 二元组滑动窗口
        for i in range(len(chunk) - 1):
            bigram = chunk[i:i+2]
            if bigram not in _CHINESE_STOP:
                tokens.append(bigram)
        # 也保留单字（过滤停用词）
        for c in chunk:
            if c not in _CHINESE_STOP and len(chunk) <= 6:
                tokens.append(c)
    return tokens


def _tokenize_english(text: str) -> list[str]:
    """英文分词（保留 2 字符词：AI / ML / OS 这类缩写很关键）"""
    words = re.findall(r"[a-zA-Z][a-zA-Z'-]+", text.lower())
    return [w for w in words if w not in _CHINESE_STOP and len(w) > 1]


def _tokenize(text: str) -> list[str]:
    """通用分词（中英文混合）"""
    return _tokenize_chinese(text) + _tokenize_english(text)


# ──域名黑名单过滤器 ──

class DomainBlacklist:
    """基于域名黑名单的低质来源过滤"""

    def __init__(self, blacklist: frozenset = _DOMAIN_BLACKLIST):
        self._blacklist = blacklist

    def is_blocked(self, url: str) -> bool:
        domain = _extract_domain(url)
        if not domain:
            return False
        # 精确匹配 + 后缀匹配
        if domain in self._blacklist:
            return True
        for blocked in self._blacklist:
            if domain.endswith("." + blocked):
                return True
        return False

    def filter(self, results: list[dict]) -> list[dict]:
        return [r for r in results if not self.is_blocked(r.get("url", ""))]


# ── SearXNG 客户端 ──

class SearXNGClient:
    """SearXNG JSON API 客户端"""

    def __init__(self, base_url: str = SEARXNG_URL, timeout: int = SEARXNG_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout, follow_redirects=True)

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

class QualityPreFilter:
    """借鉴 opc_data_filtering 的文本质量信号体系 + 中文特有信号"""

    _FILTER_CONFIG: dict[str, dict[str, str]] = {
        "zh": {
            "frac_chars_replacement_symbols": "lambda x: x > 0.05",
            "entropy_unigram": "lambda x: x < 1.5",
            "frac_chars_dupe_5grams": "lambda x: x > 0.80",
            "frac_chars_dupe_6grams": "lambda x: x > 0.80",
            "frac_chars_dupe_7grams": "lambda x: x > 0.80",
            "frac_chars_dupe_8grams": "lambda x: x > 0.80",
            "frac_chars_dupe_9grams": "lambda x: x > 0.80",
            "frac_chars_dupe_10grams": "lambda x: x > 0.80",
            "frac_chars_hyperlink_html_tag": "lambda x: x > 0.5",
            "frac_chars_whitespace": "lambda x: x > 0.6",
            "num_chars": "lambda x: x < 15",
            "num_words": "lambda x: x < 5",
            # 中文新增信号
            "frac_punctuation": "lambda x: x > 0.4",       # 标点占比过高
            "frac_seo_keywords": "lambda x: x > 0.3",       # SEO 关键词密度过高
            "keyword_stuffing_score": "lambda x: x > 0.6",  # 关键词堆砌
        },
        "en": {
            "frac_chars_replacement_symbols": "lambda x: x > 0.05",
            "entropy_unigram": "lambda x: x < 1.5",
            "frac_chars_dupe_5grams": "lambda x: x > 0.80",
            "frac_chars_dupe_6grams": "lambda x: x > 0.80",
            "frac_chars_dupe_7grams": "lambda x: x > 0.80",
            "frac_chars_dupe_8grams": "lambda x: x > 0.80",
            "frac_chars_dupe_9grams": "lambda x: x > 0.80",
            "frac_chars_dupe_10grams": "lambda x: x > 0.80",
            "frac_chars_hyperlink_html_tag": "lambda x: x > 0.5",
            "frac_chars_whitespace": "lambda x: x > 0.6",
            "num_chars": "lambda x: x < 15",
            "num_words": "lambda x: x < 5",
            "frac_punctuation": "lambda x: x > 0.5",
            "frac_seo_keywords": "lambda x: x > 0.35",
            "keyword_stuffing_score": "lambda x: x > 0.65",
        },
    }

    # 中文标点集
    _CN_PUNCTUATION = frozenset(
        "，。、；：？！…—·（）【】《》""''「」『』﹃﹄﹁﹂,.;:?!-—()[]{}"
    )
    # SEO 敏感词
    _SEO_INDICATORS = frozenset({
        "下载", "免费", "教程", "推荐", "热门", "排行", "最新",
        "大全", "合集", "攻略", "秘籍", "分享", "精品", "官网",
        "报价", "价格", "怎么样", "好不好", "多少钱", "2025", "2026",
        "download", "free", "best", "top", "guide", "tutorial",
        "cheap", "review", "reviews", "coupon", "discount",
    })

    _ELLIPSIS_SYMBOLS = (
        "...", "…", "全文", "详情", "详细", "更多",
        "المزيد", "تفاصيل", "اقرأ المزيد", "もっと", "詳細", "もっと読む",
    )

    def __init__(self):
        self._compiled: dict[str, dict[str, object]] = {}
        for lang, cfg in self._FILTER_CONFIG.items():
            self._compiled[lang] = {key: eval(expr) for key, expr in cfg.items()}

    @staticmethod
    def _detect_lang(text: str) -> str:
        chinese_count = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        return "zh" if chinese_count / max(len(text), 1) > 0.05 else "en"

    @staticmethod
    def _signal_replacement_symbols(text: str) -> float | None:
        if not text:
            return None
        return text.count("\ufffd") / len(text)

    @staticmethod
    def _signal_entropy_unigram(text: str) -> float | None:
        if not text:
            return None
        freq = Counter(text)
        total = len(text)
        return -sum((c / total) * math.log2(c / total) for c in freq.values())

    @staticmethod
    def _signal_dupe_ngrams(text: str, n: int) -> float | None:
        if len(text) < n + 1:
            return None
        ngrams = [text[i:i + n] for i in range(len(text) - n + 1)]
        if not ngrams:
            return None
        seen: set[str] = set()
        dupe_chars = 0
        for ng in ngrams:
            if ng in seen:
                dupe_chars += n
            else:
                seen.add(ng)
        return dupe_chars / (len(ngrams) * n)

    @staticmethod
    def _signal_hyperlink_html(text: str) -> float | None:
        if not text:
            return None
        url_chars = sum(len(m.group(0)) for m in re.finditer(r"\(https?://\S+\)", text))
        html_chars = sum(len(m.group(0)) for m in re.finditer(r"<.*?>", text))
        return (url_chars + html_chars) / len(text)

    @staticmethod
    def _signal_whitespace(text: str) -> float | None:
        if not text:
            return None
        return sum(c.isspace() for c in text) / len(text)

    @staticmethod
    def _signal_ellipsis_lines(text: str) -> float | None:
        lines = [l for l in text.split("\n") if l.strip()]
        if not lines:
            return None
        count = sum(
            1 for l in lines
            if l.rstrip().rstrip("]】)>》").endswith(QualityPreFilter._ELLIPSIS_SYMBOLS)
        )
        return count / len(lines)

    @staticmethod
    def _signal_punctuation(text: str) -> float | None:
        """中文标点占比"""
        if not text:
            return None
        cn_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        punct = sum(1 for c in text if c in QualityPreFilter._CN_PUNCTUATION)
        return punct / max(cn_chars, 1)

    @staticmethod
    def _signal_seo_keywords(text: str) -> float | None:
        """SEO 敏感词密度"""
        if not text:
            return None
        seo_count = sum(
            1 for indicator in QualityPreFilter._SEO_INDICATORS
            if indicator in text
        )
        return seo_count / max(len(text) / 50, 1)  # 每50字符中有多少个SEO词

    @staticmethod
    def _signal_keyword_stuffing(text: str) -> float | None:
        """关键词堆砌检测：重复出现的名词短语"""
        if len(text) < 50:
            return None
        # 提取中文二元组
        cn_chunks = re.findall(r"[\u4e00-\u9fff]{2,4}", text)
        if not cn_chunks:
            return None
        counter = Counter(cn_chunks)
        total = len(cn_chunks)
        # 如果 top-3 重复项覆盖超过 40%，判定为堆砌
        top3_count = sum(count for _, count in counter.most_common(3))
        return top3_count / total

    def _compute_signals(self, text: str) -> dict[str, float]:
        s: dict[str, float] = {}
        s["num_chars"] = len(text)
        chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        if chinese_chars > len(text) * 0.3:
            s["num_words"] = chinese_chars
        else:
            s["num_words"] = len(text.split())

        def _set(name: str, value: float | None):
            if value is not None:
                s[name] = value

        _set("frac_chars_replacement_symbols", self._signal_replacement_symbols(text))
        _set("entropy_unigram", self._signal_entropy_unigram(text))
        _set("frac_chars_hyperlink_html_tag", self._signal_hyperlink_html(text))
        _set("frac_chars_whitespace", self._signal_whitespace(text))
        _set("frac_lines_end_with_readmore", self._signal_ellipsis_lines(text))
        _set("frac_punctuation", self._signal_punctuation(text))
        _set("frac_seo_keywords", self._signal_seo_keywords(text))
        _set("keyword_stuffing_score", self._signal_keyword_stuffing(text))
        for n in range(5, 11):
            _set(f"frac_chars_dupe_{n}grams", self._signal_dupe_ngrams(text, n))
        return s

    def filter(self, results: list[dict]) -> list[dict]:
        if not results:
            return results
        filtered: list[dict] = []
        for result in results:
            text = f"{result.get('title', '')}\n{result.get('snippet', '')}"
            if not text.strip():
                filtered.append(result)
                continue
            lang = self._detect_lang(text)
            signals = self._compute_signals(text)
            cfg = self._compiled.get(lang, self._compiled["en"])
            drop = False
            for key, predicate in cfg.items():
                value = signals.get(key)
                if value is None:
                    continue
                try:
                    if predicate(value):
                        drop = True
                        break
                except Exception:
                    continue
            if not drop:
                filtered.append(result)
        if not filtered:
            return results  # 防止全部被过滤
        return filtered


# ── 关键词相关性评分（参考 semantic-relevance 的 embedding 思路，用词法近似替代） ──

class RelevanceScorer:
    """基于查询-结果关键词重叠的相关性评分

    参考 semantic-relevance 的 filterItems + scoring 逻辑：
    - 提取查询关键词（类似 semantic-relevance 的 context 解析）
    - 计算 title/snippet 与关键词的匹配度（类似 embedding 相似度的词法近似）
    - 输出 0-1 相关性分数
    """

    # 标题匹配权重（参考 semantic-relevance 分类器对不同字段的加权）
    _TITLE_WEIGHT = 0.55
    _SNIPPET_WEIGHT = 0.35
    _URL_WEIGHT = 0.10

    # 精确匹配（对应 semantic-relevance 的 "exact" 关键词）
    _EXACT_BONUS = 1.5
    # 部分匹配（对应 semantic-relevance 的 "generic" 关键词）
    _PARTIAL_BONUS = 1.0

    def __init__(self, title_weight: float = 0.55, snippet_weight: float = 0.35):
        self._title_weight = title_weight
        self._snippet_weight = snippet_weight
        self._url_weight = 1.0 - title_weight - snippet_weight
        # 缓存查询分词结果
        self._query_cache: dict[str, tuple[set[str], set[str], set[str]]] = {}

    def _parse_query(self, query: str) -> tuple[set[str], set[str], set[str]]:
        """解析查询为关键词集合，返回 (中文二元组, 英文词, 精确短语)"""
        if query in self._query_cache:
            return self._query_cache[query]

        # 只保留有区分度的词：单字中文与填充词（今天/最新/新闻）不参与打分
        # 先做查询归一：填充词（今天/有什么/值得/关注）会切出「天有/有什」这类
        # 噪声二元组，直接稀释相关性分数
        query = normalize_query(query)
        cn_tokens = {t for t in _tokenize_chinese(query) if _is_distinctive_zh(t)}
        en_tokens = {t for t in _tokenize_english(query) if _is_distinctive_en(t)}

        # 精确短语：用引号括起来的部分
        exact_phrases = set()
        for m in re.finditer(r'"([^"]+)"', query):
            exact_phrases.add(m.group(1).lower())

        self._query_cache[query] = (cn_tokens, en_tokens, exact_phrases)
        return cn_tokens, en_tokens, exact_phrases

    def _compute_field_score(self, field_text: str, cn_tokens: set[str],
                              en_tokens: set[str], exact_phrases: set[str]) -> float:
        """计算单个字段与查询的匹配分数"""
        text_lower = field_text.lower()
        matches = 0.0

        # 精确短语匹配（最高权重）
        for phrase in exact_phrases:
            if phrase in text_lower:
                matches += self._EXACT_BONUS * len(phrase) / max(len(phrase.split()), 1)

        # 查询词整体作为短语匹配
        all_query = " ".join(list(cn_tokens)[:8] + list(en_tokens)[:8])
        if all_query and len(all_query) > 3:
            q_words = all_query.split()
            # 检查是否有连续 2+ 个查询词出现在字段中
            for i in range(len(q_words) - 1):
                pair = " ".join(q_words[i:i+2])
                if pair in text_lower:
                    matches += self._EXACT_BONUS

        # 中文二元组匹配
        cn_field = set(_tokenize_chinese(field_text))
        if cn_tokens and cn_field:
            cn_overlap = len(cn_tokens & cn_field)
            cn_ratio = cn_overlap / max(len(cn_tokens), 1)
            matches += cn_ratio * self._EXACT_BONUS

        # 英文词匹配
        en_field = set(_tokenize_english(field_text))
        if en_tokens and en_field:
            en_overlap = len(en_tokens & en_field)
            en_ratio = en_overlap / max(len(en_tokens), 1)
            matches += en_ratio * self._EXACT_BONUS

        # 部分匹配：单个查询字/词出现在字段中（停用词不计分）
        partial_matches = 0
        for token in cn_tokens:
            if token in field_text:
                partial_matches += 1
        for token in en_tokens:
            if not _is_distinctive_en(token):
                continue  # 只命中最新/新闻/年份这类词不算匹配
            # 词边界匹配：否则 "ai" 会命中 baike / email / detail 这类子串
            if re.search(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])", text_lower):
                partial_matches += 1
        total_tokens = len(cn_tokens) + len(en_tokens)
        if total_tokens > 0:
            partial_ratio = partial_matches / total_tokens
            matches += partial_ratio * self._PARTIAL_BONUS

        return matches

    # 内容类型不匹配信号（当查询不涉及某类别但结果涉及时的降权）
    _CATEGORY_MISMATCH_TERMS = {
        "game": frozenset({
            "游戏", "攻略", "下载", "MOD", "修改器", "补丁", "通关",
            "steam", "game", "playstation", "xbox", "nintendo",
            "关卡", "角色", "装备", "技能", "副本", "对战",
        }),
        "dictionary": frozenset({
            "拼音", "部首", "笔画", "组词", "笔顺", "释义", "汉字",
            "成语", "词语", "词汇", "词典", "字典", "pinyin",
        }),
        "shopping": frozenset({
            "价格", "报价", "购买", "商品", "店铺", "快递", "包邮",
            "正品", "促销", "打折", "优惠券", "满减",
        }),
    }

    # 如果查询不含游戏相关词，但结果含大量游戏词，就是误匹配
    _QUERY_GAME_INDICATORS = frozenset({"游戏", "攻略", "steam", "gaming"})

    def _category_penalty(self, text: str, query: str) -> float:
        """内容类型不匹配惩罚：返回 0.0-1.0 的扣分系数（1.0=不扣分，0.0=全扣）"""
        text_lower = text.lower()
        query_lower = query.lower()

        for cat, terms in self._CATEGORY_MISMATCH_TERMS.items():
            # 查询本身是否涉及此类别的指标词
            query_has_cat = any(t in query_lower for t in self._CATEGORY_MISMATCH_TERMS.get(
                # 仅对 game 类别检查查询指标
                cat, frozenset()
            ) if cat == "game") or any(t in query_lower for t in self._QUERY_GAME_INDICATORS)

            if query_has_cat:
                continue  # 查询本身就是要搜这个类别，不惩罚

            # 统计结果中匹配的类别词数量
            match_count = sum(1 for t in terms if t in text_lower)
            if match_count >= 2:
                # 2个以上类别词 → 疑似类别不匹配（原阈值 3 偏松：拼音+汉字只算 2 个）
                return max(0.0, 1.0 - (match_count * 0.15))
        return 1.0  # 不扣分

    def _is_reference_junk(self, text: str, url: str) -> bool:
        """词典/天气等参考类页面：域名 + 内容特征双判据

        只靠内容特征会误杀正常的字词类查询；只靠域名又会漏掉同站的其他页面，
        因此两者都命中才判定为垃圾结果。
        """
        dom = _extract_domain(url)
        if not dom:
            return False
        if not any(dom == d or dom.endswith("." + d) for d in _REFERENCE_DOMAINS):
            return False
        dict_hits = sum(1 for t in _DICTIONARY_MARKERS if t in text)
        weather_hits = sum(1 for t in _WEATHER_MARKERS if t in text)
        is_weather_only = any(dom == d or dom.endswith("." + d) for d in _WEATHER_ONLY_DOMAINS)
        if is_weather_only and weather_hits >= 1:
            return True
        return dict_hits >= 2 or weather_hits >= 2

    def score(self, result: dict, query: str) -> float:
        """计算单个结果与查询的相关性分数 (0.0 - 1.0)"""
        if not query.strip():
            return 0.5  # 无查询时默认中立

        cn_tokens, en_tokens, exact_phrases = self._parse_query(query)
        # 查询里没有足够的区分度词（如「今天有什么值得关注的新闻」）时不做相关性裁决：
        # 仍要经过词典/天气守卫，其余按中性分放行，避免把结果全判死
        soft_mode = (len(cn_tokens) + len(en_tokens)) == 0 and not exact_phrases

        title = result.get("title", "")
        snippet = result.get("snippet", "")
        url = result.get("url", "")

        title_score = self._compute_field_score(title, cn_tokens, en_tokens, exact_phrases)
        snippet_score = self._compute_field_score(snippet, cn_tokens, en_tokens, exact_phrases)
        url_score = self._compute_field_score(url, cn_tokens, en_tokens, exact_phrases)

        # 综合加权（参考 semantic-relevance 的多因子评分）
        composite = (
            title_score * self._title_weight +
            snippet_score * self._snippet_weight +
            url_score * self._url_weight
        )

        # 归一化到 0-1
        # 理论最大值为: (EXACT_BONUS * 2 + PARTIAL_BONUS * 1) * 1.0 = 3.0 for each field
        max_possible = (self._EXACT_BONUS * 2 + self._PARTIAL_BONUS * 1)
        normalized = min(composite / max_possible, 1.0)

        # 词典/天气等参考类页面直接判 0（今日实测：AI 查询被「大」的字典页污染）
        combined_text = f"{title}\n{snippet}"
        if self._is_reference_junk(combined_text, url):
            return 0.0

        # 内容类型不匹配扣分（参考 semantic-relevance 的分类器逻辑）
        cat_penalty = self._category_penalty(combined_text, query)
        normalized *= cat_penalty

        # 语言错配惩罚：英文查询（无汉字）返回大量中文内容时大幅降权
        lang_mismatch = (not _has_cjk(query)) and _cjk_ratio(combined_text) > 0.35
        if lang_mismatch:
            normalized *= 0.20

        # 标题包含至少一个"有区分度"查询词的保底分（停用词/年份不算，避免词典页混入）
        has_token = any(t in title for t in cn_tokens) or any(
            _is_distinctive_en(t) and t in title.lower() for t in en_tokens
        )
        if has_token and not lang_mismatch and normalized < 0.15 and cat_penalty > 0.5:
            normalized = 0.15

        # soft 模式：放行（词典/天气守卫已在上方判 0）
        if soft_mode:
            return 0.5

        return round(normalized, 4)

    def filter_and_sort(self, results: list[dict], query: str,
                         min_score: float = 0.10) -> list[dict]:
        """根据相关性过滤并排序结果"""
        if not results or not query.strip():
            return results

        scored = []
        for r in results:
            score = self.score(r, query)
            scored.append((score, r))

        # 过滤低分
        passed = [(s, r) for s, r in scored if s >= min_score]

        # 按分数降序排列
        passed.sort(key=lambda x: -x[0])

        # 分数保留在结果里，供上层排序、时间过滤与输出使用
        return [{**r, "score": s} for s, r in passed]


# ── 统一搜索入口（集成所有过滤器） ──

class WebSearcher:
    """多搜索引擎封装，集成完整过滤管线"""

    def __init__(self):
        self._searxng = SearXNGClient(SEARXNG_URL, SEARXNG_TIMEOUT)
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
        """将搜索结果格式化为 LLM 上下文（附域名/日期/相关度，便于判断可信度）"""
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

    def format_as_json(self, results: list[dict]) -> str:
        """将搜索结果格式化为 JSON 字符串"""
        return json.dumps(results, ensure_ascii=False, indent=2)


# 单例
searcher = WebSearcher()

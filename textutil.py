"""文本工具：分词、查询归一、日期抽取、去重与结果富化（纯函数，仅依赖标准库）。"""

import math
import re
from datetime import datetime, timedelta

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

_EN_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "of", "to", "for", "in",
    "on", "with", "at", "by", "from", "into", "about", "as", "is",
    "are", "was", "be", "been", "it", "its", "this", "that", "these",
    "those", "what", "how", "when", "where", "why", "who", "which",
    "latest", "news", "new", "today", "tomorrow", "yesterday", "now",
    "best", "top", "guide", "review", "reviews", "list", "good",
    "what", "definition", "meaning", "means",
})

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

def _is_distinctive_en(token: str) -> bool:
    """英文词是否具有区分度：排除停用词与纯数字/年份"""
    return bool(token) and token not in _EN_STOPWORDS and not token.isdigit()


# ── 中文填充词：命中它们不代表相关（对应 _EN_STOPWORDS 的中文部分） ──

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

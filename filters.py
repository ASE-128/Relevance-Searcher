"""过滤策略：域名黑名单、词典/天气等参考类站点守卫、文本质量预过滤。"""

import math
import re
from collections import Counter

from textutil import _extract_domain

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

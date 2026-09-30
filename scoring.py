"""查询-结果相关性评分（词法近似 semantic-relevance 的 embedding 打分）。"""

import re

from filters import (_DICTIONARY_MARKERS, _REFERENCE_DOMAINS, _WEATHER_MARKERS,
                     _WEATHER_ONLY_DOMAINS)
from textutil import (_cjk_ratio, _extract_domain, _has_cjk, _is_distinctive_en,
                      _is_distinctive_zh, _tokenize_chinese, _tokenize_english,
                      normalize_query)

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

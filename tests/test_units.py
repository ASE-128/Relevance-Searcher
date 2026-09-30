#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""拆分后的纯逻辑单测（不联网）。

覆盖：
  webpage        编解码回退、正文提取与截断、长度钳制
  client_config  幂等回写
  tools          参数规整（上限钳制 / 非法枚举 / 空输入）
  textutil       查询归一、区分度、日期抽取、去重
  filters        域名黑名单、词典/天气守卫
  scoring        词典页判 0、正常命中得分

跑法： python tests/test_units.py
"""

import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "_vendored"))
for _sub in ("win32", "win32\\lib", "pythonwin"):
    _p = os.path.join(BASE, "_vendored", _sub)
    if os.path.isdir(_p):
        sys.path.insert(0, _p)

import client_config
import config
import filters
import formatting
import textutil
import tools
import webpage
from scoring import RelevanceScorer

Q_FILLER = "今天有什么值得关注的 AI 方向新闻"
Q_DICT = "大模型 推理 芯片 融资 新闻"

DICT_PAGE = {
    "title": "大（汉语文字）_百度百科",
    "url": "https://baike.baidu.com/item/%E5%A4%A7/34128",
    "snippet": "大（拼音：dà、dài、tài）是汉语一级通用规范汉字。此字始见于商代甲骨文",
}
WEATHER_PAGE = {
    "title": "富阳天气预报,富阳7天天气预报",
    "url": "https://www.weather.com.cn/weather/101210105.shtml",
    "snippet": "富阳天气预报,富阳15天天气预报,富阳天气查询",
}
GOOD_PAGE = {
    "title": "OpenAI DevDay 2026 发布21项更新：全天候智能体 Dot",
    "url": "https://www.aisort.net/ai_news/20260929/openai-devday-2026-dots-gpt61-sol/",
    "snippet": "当地时间9月29日 OpenAI 举办 DevDay 2026，发布21项更新，由 GPT-6 Astra 驱动",
}


class WebpageTests(unittest.TestCase):
    def test_decode_utf8(self):
        self.assertEqual(webpage.decode_html("中文正文".encode("utf-8")), "中文正文")

    def test_decode_gbk_fallback(self):
        self.assertEqual(webpage.decode_html("中文正文".encode("gbk")), "中文正文")

    def test_decode_never_raises(self):
        self.assertIsInstance(webpage.decode_html(b"\xff\xfe\x00\xff"), str)

    def test_extract_text_drops_noise_and_short_blocks(self):
        html = """<html><head><title>测试标题</title></head><body>
        <script>var leak = 1;</script><nav>导航区块内容</nav>
        <p>短</p>
        <p>这是一段足够长的正文内容，应该被提取出来。</p>
        </body></html>"""
        out = webpage.extract_text(html, "https://example.com/a", 4000)
        self.assertIn("测试标题", out)
        self.assertIn("这是一段足够长的正文内容", out)
        self.assertNotIn("var leak", out)
        self.assertNotIn("导航区块内容", out)
        self.assertNotIn("\n短\n", out)

    def test_extract_text_truncates(self):
        html = "<html><head><title>t</title></head><body><p>" + "字" * 2000 + "</p></body></html>"
        out = webpage.extract_text(html, "https://example.com/b", 600)
        self.assertTrue(out.endswith("…[截断]"))
        self.assertLessEqual(len(out), 600 + len("…[截断]"))

    def test_extract_text_empty_body(self):
        out = webpage.extract_text("<html><script>x=1</script></html>", "https://example.com/c", 4000)
        self.assertIn("未能从", out)

    def test_clamp_max_chars(self):
        self.assertEqual(webpage.clamp_max_chars(1), config.FETCH_MAX_CHARS_MIN)
        self.assertEqual(webpage.clamp_max_chars(10 ** 9), config.FETCH_MAX_CHARS_MAX)
        self.assertEqual(webpage.clamp_max_chars("bad"), config.FETCH_MAX_CHARS)

    def test_invalid_url(self):
        self.assertIn("无效 URL", webpage.fetch_page_text("ftp://example.com"))


class ClientConfigTests(unittest.TestCase):
    def test_write_then_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "mcp-clients.json")
            self.assertTrue(client_config.ensure_client_config(path, base_dir=tmp))
            with open(path, encoding="utf-8") as f:
                data = f.read()
            self.assertIn(config.SERVER_NAME, data)
            self.assertIn("run-server.cmd", data)
            # 第二次：路径一致 → 不写回
            self.assertFalse(client_config.ensure_client_config(path, base_dir=tmp))
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), data)

    def test_rewrites_when_path_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "mcp-clients.json")
            client_config.ensure_client_config(path, base_dir=tmp)
            self.assertTrue(client_config.ensure_client_config(path, base_dir=os.path.join(tmp, "moved")))


class ToolsArgTests(unittest.TestCase):
    def test_clamps_upper_bound(self):
        self.assertEqual(tools.normalize_search_args(99)["max_results"], config.SEARCH_MAX_RESULTS)
        self.assertEqual(tools.normalize_multi_args(1000)["max_results"], config.SEARCH_MULTI_MAX_RESULTS)

    def test_clamps_lower_bound(self):
        self.assertEqual(tools.normalize_search_args(-5)["max_results"], 1)

    def test_bad_enum_falls_back(self):
        args = tools.normalize_search_args(category="Bogus", sort_by="weird")
        self.assertEqual(args["categories"], "general")
        self.assertEqual(args["sort_by"], "relevance")
        self.assertEqual(args["heading"], "搜索结果")

    def test_science_heading(self):
        args = tools.normalize_search_args(category="science", sort_by="recency")
        self.assertEqual(args["categories"], "science")
        self.assertEqual(args["heading"], "学术搜索结果")
        self.assertEqual(args["sort_by"], "recency")

    def test_empty_inputs_short_circuit(self):
        self.assertEqual(tools.web_search("   "), "搜索关键词为空。")
        self.assertEqual(tools.search_multi([]), "搜索关键词列表为空。")
        self.assertEqual(tools.search_multi(["  "]), "搜索关键词列表为空。")
        self.assertEqual(tools.search_format(" "), "[]")


class TextutilTests(unittest.TestCase):
    def test_normalize_query_strips_filler(self):
        self.assertEqual(textutil.normalize_query(Q_FILLER), "AI 方向新闻")

    def test_normalize_query_keeps_quotes(self):
        self.assertIn('"OpenAI DevDay"', textutil.normalize_query('最新 "OpenAI DevDay" 发布'))

    def test_normalize_query_falls_back(self):
        self.assertEqual(textutil.normalize_query("今天最新"), "今天最新")

    def test_distinctiveness(self):
        self.assertFalse(textutil._is_distinctive_zh("大"))
        self.assertFalse(textutil._is_distinctive_zh("新闻"))
        self.assertFalse(textutil._is_distinctive_zh("向新"))
        self.assertTrue(textutil._is_distinctive_zh("大模型"))
        self.assertFalse(textutil._is_distinctive_en("latest"))
        self.assertTrue(textutil._is_distinctive_en("ai"))

    def test_extract_date(self):
        self.assertEqual(textutil._extract_date("2026-09-30 报告"), "2026-09-30")
        self.assertTrue(textutil._extract_date("当地时间9月29日 OpenAI").endswith("-09-29"))
        self.assertEqual(textutil._extract_date("没有日期"), "")

    def test_dedup_by_title_and_url(self):
        dupes = [
            {"title": "AI大模型及产业发展追踪报告(2026年9月30日)", "url": "http://mp.weixin.qq.com/s?timestamp=1&signature=a"},
            {"title": "AI大模型及产业发展追踪报告(2026年9月30日)", "url": "http://mp.weixin.qq.com/s?timestamp=2&signature=b"},
            {"title": "另一条", "url": "https://example.com/x"},
        ]
        self.assertEqual(len(textutil._dedup_results(dupes)), 2)


class FiltersTests(unittest.TestCase):
    def test_domain_blacklist(self):
        bl = filters.DomainBlacklist()
        self.assertTrue(bl.is_blocked("https://www.3dmgame.com/news/1"))
        self.assertFalse(bl.is_blocked("https://github.com/openai"))

    def test_blacklist_filter(self):
        out = filters.DomainBlacklist().filter([
            {"url": "https://www.3dmgame.com/a"},
            {"url": "https://github.com/b"},
        ])
        self.assertEqual(len(out), 1)
        self.assertIn("github.com", out[0]["url"])

    def test_quality_filter_keeps_normal_result(self):
        out = filters.QualityPreFilter().filter([dict(GOOD_PAGE)])
        self.assertEqual(len(out), 1)


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.rs = RelevanceScorer()

    def test_dictionary_page_scores_zero(self):
        self.assertEqual(self.rs.score(DICT_PAGE, Q_DICT), 0.0)

    def test_weather_page_scores_zero(self):
        self.assertEqual(self.rs.score(WEATHER_PAGE, Q_FILLER), 0.0)

    def test_junk_without_topic_token_dropped(self):
        poem = {
            "title": "今天（北岛、芒克等创办诗刊）_百度百科",
            "url": "https://baike.baidu.com/item/%E4%BB%8A%E5%A4%A9/6956037",
            "snippet": "一九九零年春十多位作家在挪威奥斯陆聚会",
        }
        self.assertLess(self.rs.score(poem, Q_FILLER), 0.12)

    def test_good_hit_survives(self):
        self.assertGreater(self.rs.score(GOOD_PAGE, "OpenAI DevDay 2026 发布"), 0.12)

    def test_filter_and_sort_attaches_score(self):
        out = self.rs.filter_and_sort([dict(GOOD_PAGE), dict(DICT_PAGE)], "OpenAI DevDay 2026 发布")
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0]["score"], float)


class FormattingTests(unittest.TestCase):
    def test_context_has_quality_line(self):
        out = formatting.format_as_context([dict(GOOD_PAGE, score=0.9, domain="aisort.net", date="2026-09-29")])
        self.assertIn("质量: aisort.net | 2026-09-29 | 相关度 0.90", out)

    def test_context_marks_missing_snippet(self):
        out = formatting.format_as_context([{"title": "t", "url": "https://e.com", "snippet": ""}])
        self.assertIn("（来源未提供摘要）", out)

    def test_json_roundtrip(self):
        import json as _json
        out = formatting.format_as_json([dict(GOOD_PAGE)])
        self.assertEqual(_json.loads(out)[0]["title"], GOOD_PAGE["title"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

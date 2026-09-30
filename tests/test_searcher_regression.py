#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""relevance_search 回归基准。

跑法： python tests/test_searcher_regression.py
断言：4 组真实查询中不得出现词典类/天气类结果、不得出现同题重复。
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "_vendored"))
for _sub in ("win32", "win32\\lib", "pythonwin"):
    _p = os.path.join(BASE, "_vendored", _sub)
    if os.path.isdir(_p):
        sys.path.insert(0, _p)
sys.path.insert(0, BASE)

from datetime import datetime
from searcher import (searcher, normalize_query, _dedup_results, _extract_date,
                      _is_distinctive_zh, RelevanceScorer)

DICT_MARKERS = ("baike.baidu.com/item/%E5%A4%A7", "zdic.net", "hanyu.baidu.com",
                "cidianwang.com", "hgcha.com")
WEATHER_MARKERS = ("tianqi.com", "weather.com.cn", "tianqihoubao.com")
QUERIES = [
    "今天有什么值得关注的 AI 方向新闻",
    "2026 AI 大模型 最新进展 发布",
    "大模型 推理 芯片 融资 新闻",
    "OpenAI DevDay 2026 发布",
]


def unit_checks():
    rs = RelevanceScorer()
    q1 = "今天有什么值得关注的 AI 方向新闻"
    q3 = "大模型 推理 芯片 融资 新闻"
    y = datetime.now().year
    checks = [
        ("normalize strips filler",
         normalize_query("今天有什么值得关注的 AI 方向新闻") == "AI 方向新闻"),
        ("normalize keeps quotes",
         '"OpenAI DevDay"' in normalize_query('最新 "OpenAI DevDay" 发布')),
        ("normalize falls back", normalize_query("今天最新") == "今天最新"),
        ("single char not distinctive", not _is_distinctive_zh("大")),
        ("noise word not distinctive", not _is_distinctive_zh("新闻")),
        ("cross-filler bigram rejected", not _is_distinctive_zh("向新")),
        ("real term distinctive", _is_distinctive_zh("大模型") and _is_distinctive_zh("融资")),
        ("dictionary page dropped", rs.score({
            "title": "大（汉语文字）_百度百科",
            "url": "https://baike.baidu.com/item/%E5%A4%A7/34128",
            "snippet": "大（拼音：dà、dài、tài）是汉语一级通用规范汉字。"}, q3) == 0.0),
        ("weather page dropped", rs.score({
            "title": "富阳天气预报,富阳7天天气预报",
            "url": "https://www.weather.com.cn/weather/101210105.shtml",
            "snippet": "富阳天气预报,富阳15天天气预报"}, q1) == 0.0),
        ("junk w/o topic token dropped", rs.score({
            "title": "今天（北岛、芒克等创办诗刊）_百度百科",
            "url": "https://baike.baidu.com/item/%E4%BB%8A%E5%A4%A9/6956037",
            "snippet": "一九九零年春十多位作家在挪威奥斯陆聚会"}, q1) < 0.12),
        ("topic token keeps result", rs.score({
            "title": "2024年值得关注的10大AI创新方向",
            "url": "https://mp.weixin.qq.com/s?src=11&x=1",
            "snippet": "AI创新 应用领域"}, q1) > 0.12),
        ("dedup by title/url", len(_dedup_results([
            {"title": "AI大模型及产业发展追踪报告(2026年9月30日)", "url": "http://mp.weixin.qq.com/s?timestamp=1&signature=a"},
            {"title": "AI大模型及产业发展追踪报告(2026年9月30日)", "url": "http://mp.weixin.qq.com/s?timestamp=2&signature=b"},
        ])) == 1),
        ("date month-day", _extract_date("当地时间9月29日 OpenAI") == f"{y:04d}-09-29"),
        ("date full", _extract_date("2026-09-30 报告") == "2026-09-30"),
        ("date none", _extract_date("没有日期") == ""),
    ]
    return checks


def main():
    failures = []
    print("=== UNIT CHECKS ===")
    for name, ok in unit_checks():
        print(("PASS" if ok else "FAIL"), "|", name)
        if not ok:
            failures.append(name)

    print("=== REGRESSION ===")
    for q in QUERIES:
        try:
            res = searcher.search_sync(q, max_results=8)
        except Exception as exc:  # noqa: BLE001
            print("ERROR |", q, "|", exc)
            failures.append(q)
            continue
        titles = [r.get("title", "") for r in res]
        bad_dict = [r for r in res if any(m in r.get("url", "") for m in DICT_MARKERS)]
        bad_weather = [r for r in res if any(m in r.get("url", "") for m in WEATHER_MARKERS)]
        dup = len(titles) != len(set(titles))
        print(f"--- {q} -> {len(res)} 条; 词典类={len(bad_dict)} 天气类={len(bad_weather)} 同题重复={dup}")
        for i, r in enumerate(res, 1):
            print(f"   {i}. [{r.get('score')}] {r.get('domain')} {r.get('date') or '-'} {r.get('title', '')[:54]}")
        if bad_dict or bad_weather or dup:
            failures.append(q)

    print("=== SUMMARY ===")
    print(json.dumps({"failures": failures}, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""网页正文抓取：HTTP 取回 + 编码识别 + 正文提取。

分成两层：
  - 纯函数 decode_html / extract_text：不联网，可单测
  - fetch_page_text：负责 IO，把异常转成给模型看的错误文案
"""

from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup

import config

FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 抓取时需要剔除的噪音区块（导航/广告/评论等）
SKIP_SELECTORS = (
    "script,style,noscript,nav,header,footer,aside,form,iframe,svg,canvas",
    ".nav,.menu,.sidebar,.ad,.ads,.advert,.advertisement,.comment,.comments,"
    ".footer,.header,.share,.related",
    "#nav,#menu,#sidebar,#footer,#header,#comments,#related",
)

_TEXT_TAGS = ["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "pre", "blockquote", "td", "figcaption"]


def clamp_max_chars(value: int) -> int:
    """钳制返回正文长度：500 ~ 20000 字符"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = config.FETCH_MAX_CHARS
    return min(max(n, config.FETCH_MAX_CHARS_MIN), config.FETCH_MAX_CHARS_MAX)


def decode_html(content: bytes) -> str:
    """按常见编码顺序解码，兼顾中文站（gbk）与 utf-8"""
    for enc in ("utf-8", "gbk", "gb2312", "big5"):
        try:
            return content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return content.decode("utf-8", errors="replace")


def is_http_url(url: str) -> bool:
    return bool(re.match(r"https?://", url or "", re.I))


def extract_text(html: str, url: str, max_chars: int = config.FETCH_MAX_CHARS) -> str:
    """从 HTML 提取正文纯文本（纯函数，不联网）"""
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select(", ".join(SKIP_SELECTORS)):
        node.decompose()

    if soup.title and soup.title.get_text(strip=True):
        title = soup.title.get_text(strip=True)
    else:
        title = url

    parts = []
    for el in soup.find_all(_TEXT_TAGS):
        text = el.get_text(" ", strip=True)
        if len(text) >= config.FETCH_MIN_BLOCK_LEN:
            parts.append(text)

    body = "\n".join(parts)
    if not body:
        return f"未能从 {url} 提取到正文。"

    out = f"标题：{title}\n来源：{url}\n\n{body}"
    max_chars = clamp_max_chars(max_chars)
    if len(out) > max_chars:
        out = out[:max_chars] + "…[截断]"
    return out


def fetch_page_text(url: str, max_chars: int = config.FETCH_MAX_CHARS) -> str:
    """抓取网页并提取正文纯文本（供 LLM 阅读）"""
    if not is_http_url(url):
        return "无效 URL：仅支持 http/https 链接。"

    try:
        resp = httpx.get(
            url, headers=FETCH_HEADERS, follow_redirects=True, timeout=config.FETCH_TIMEOUT
        )
    except Exception as exc:
        return f"抓取失败：{exc}"

    if resp.status_code >= 400:
        return f"抓取失败：HTTP {resp.status_code}"

    return extract_text(decode_html(resp.content), url, max_chars)

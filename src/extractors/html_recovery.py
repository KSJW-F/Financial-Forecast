from __future__ import annotations

import logging
import re
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

BTQH_BASE_URL = "http://www.btqh.com"
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}
BROKEN_HTML_MARKERS = (
    "502 Bad Gateway",
    "503 Service Temporarily Unavailable",
    "504 Gateway Time-out",
    "Bad Gateway",
)
BTQH_CATIDS = (31, 32, 33, 35, 36, 37, 38, 39)


def is_broken_html(html: str) -> bool:
    if not html or len(html.strip()) < 80:
        return True
    lowered = html.lower()
    if any(marker.lower() in lowered for marker in BROKEN_HTML_MARKERS):
        return True
    if "提示信息" in html and "信息不存在" in html:
        return True
    return False


def parse_btqh_cms_id(html: str) -> str | None:
    match = re.search(r"api\.php\?op=count&id=(\d+)", html, re.I)
    if match:
        return match.group(1)
    return None


def fetch_btqh_article(cms_id: str) -> str | None:
    for catid in BTQH_CATIDS:
        url = f"{BTQH_BASE_URL}/index.php?m=content&c=index&a=show&catid={catid}&id={cms_id}"
        try:
            response = requests.get(url, headers=REQUEST_HEADERS, timeout=20)
            response.encoding = response.apparent_encoding or "utf-8"
            html = response.text
        except requests.RequestException as exc:
            logger.debug("btqh fetch failed %s: %s", url, exc)
            continue

        if is_broken_html(html):
            continue
        if "div class=\"t-con\"" in html or "div class='t-con'" in html:
            return html
    return None


def ensure_html_file(file_path: Path) -> str | None:
    """若本地 HTML 损坏且能解析 CMS id，则尝试从 btqh 重新拉取并覆盖。"""
    if not file_path.exists():
        return None

    html = file_path.read_text(encoding="utf-8", errors="ignore")
    if not is_broken_html(html):
        return html

    cms_id = parse_btqh_cms_id(html)
    if not cms_id:
        logger.info("broken html without cms id: %s", file_path.name)
        return html

    recovered = fetch_btqh_article(cms_id)
    if not recovered:
        return html

    file_path.write_text(recovered, encoding="utf-8")
    logger.info("recovered html from btqh cms=%s -> %s", cms_id, file_path.name)
    return recovered

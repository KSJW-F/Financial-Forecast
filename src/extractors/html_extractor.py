from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup, Tag

import config
from src.extractors.html_recovery import ensure_html_file, is_broken_html
from src.extractors.image_fetcher import (
    ensure_content_image,
    ensure_image,
    find_main_content_images,
    infer_site_base,
    is_valid_image,
)

GENERIC_TITLES = {
    "浙商期货官网",
    "融达期货(郑州)股份有限公司",
    "公司概况公司简介组织架构公司历史参控股机构联系我们",
    "紫金天风期货",
    "紫金天风期货-商品期货",
    "交子期货",
    "502 Bad Gateway",
}

TITLE_SELECTORS = [
    "div.t-con h2",
    ".t-con h2",
    ".news_show .new_tit",
    ".new_tit",
    ".right-content h5",
    "h5.text-center",
    "h1.arc-tit",
    ".work_article h1",
    "h2.con_tt",
    ".jp_yyb_box h3",
    "h1.title",
    ".article-title",
    ".con_title",
    "h3",
    "h1",
]

BODY_SELECTORS = [
    "div.right div.t-con",
    "div.t-con",
    "div.news_show div.new_content",
    "div.new_content",
    "div.tianf-standard-font",
    "div.right-content",
    "div.arc-con",
    "div.work_article1",
    "div.conten.conten_w",
    ".jp_yyb_box",
    ".con_p",
    ".article-content",
]


def extract_html(file_path: Path) -> tuple[str, str]:
    html = ensure_html_file(file_path) or file_path.read_text(encoding="utf-8", errors="ignore")
    if is_broken_html(html):
        return _broken_title(file_path), ""

    soup = BeautifulSoup(html, "lxml")

    title = _extract_title(soup, file_path, html)
    content = _extract_body(soup, file_path, html)
    return title, content


def _extract_title(soup: BeautifulSoup, file_path: Path, html: str) -> str:
    for selector in TITLE_SELECTORS:
        node = soup.select_one(selector)
        if node and node.get_text(strip=True):
            text = node.get_text(strip=True)
            if text not in GENERIC_TITLES and len(text) >= 4:
                return text

    title_tag = soup.find("title")
    if title_tag:
        text = title_tag.get_text(strip=True)
        text = re.split(r"[-_|]", text)[0].strip()
        if text and text not in GENERIC_TITLES and len(text) >= 4:
            return text

    stem = file_path.stem
    parts = stem.split("_")
    if len(parts) >= 2:
        return f"{parts[0]}研报"
    return stem


def _broken_title(file_path: Path) -> str:
    stem = file_path.stem
    parts = stem.split("_")
    if len(parts) >= 2:
        return f"{parts[0]}研报"
    return "502 Bad Gateway"


def _extract_body(soup: BeautifulSoup, file_path: Path, html: str) -> str:
    for tag in soup(["script", "style", "nav", "footer", "iframe", "header"]):
        tag.decompose()

    for selector in BODY_SELECTORS:
        node = soup.select_one(selector)
        if not node or _is_navigation_block(node):
            continue
        text = _extract_from_container(node, file_path, html)
        if text:
            return text

    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content") and len(meta["content"]) > 50:
        return meta["content"].strip()

    return ""


def _extract_from_container(container: Tag, file_path: Path, html: str) -> str:
    work = BeautifulSoup(str(container), "lxml")
    root = work.find(container.name) or work
    _strip_noise_nodes(root)

    text = root.get_text("\n", strip=True)
    lines = [line for line in text.splitlines() if line.strip()]
    if lines and lines[0].startswith("【"):
        lines = lines[1:]
    lines = [line for line in lines if not _is_chrome_line(line)]
    cleaned = "\n".join(lines).strip()

    if len(cleaned) > 80 and not _looks_like_nav_text(cleaned):
        return cleaned

    image_text = _extract_container_images(container, file_path, html)
    if image_text:
        return image_text

    if len(cleaned) > 20 and not _looks_like_nav_text(cleaned):
        return cleaned

    return ""


def _extract_container_images(container: Tag, file_path: Path, html: str) -> str:
    texts: list[str] = []
    site_base = infer_site_base(html, "")

    for img in container.find_all("img"):
        src = (img.get("src") or "").replace("\\", "/").strip()
        if not src or _is_promo_image_src(src):
            continue
        if not re.search(r"\.(png|jpe?g)(?:\?|$)", src, re.I):
            continue

        image_path = _resolve_image(file_path, src, html, site_base)
        if not image_path:
            continue

        try:
            from src.extractors.png_extractor import extract_png

            _, text = extract_png(image_path)
            if text.strip():
                texts.append(text.strip())
        except Exception:
            continue

    return "\n\n".join(texts)


def _resolve_image(file_path: Path, src: str, html: str, site_base: str) -> Path | None:
    from src.extractors.image_fetcher import resolve_local_image_path

    local_path = resolve_local_image_path(file_path, src)
    if is_valid_image(local_path):
        return local_path

    if _is_zheshang_html(html):
        fetched = ensure_content_image(file_path, base_url=config.CNZSQH_BASE_URL)
        if fetched:
            return fetched

    base = site_base or infer_site_base(html, src)
    return ensure_image(file_path, src, site_base=base)


def _extract_image_text(con_p: Tag, file_path: Path, html: str) -> str:
    return _extract_container_images(con_p, file_path, html)


def _is_zheshang_html(html: str) -> bool:
    return "con_tt" in html and "img_folder/" in html and (
        "浙商期货" in html or "cnzsqh" in html.lower()
    )


def _is_promo_image_src(src: str) -> bool:
    lowered = src.lower()
    promo_markers = (
        "style/images/",
        "top_wx",
        "logo",
        "gallery/2/c/85",
        "gallery/t/8/23085",
        "gallery/n/n/76020",
        "cfmmc.com",
    )
    return any(marker in lowered for marker in promo_markers)


def _is_chrome_line(line: str) -> bool:
    markers = ("收藏本页面", "打印", "[大", "[中", "[小", "当前位置：", "浏览次数", "下载附件")
    return any(marker in line for marker in markers)


def _looks_like_nav_text(text: str) -> bool:
    nav_markers = ("当前位置", "首页", "投资咨询", "瑞达研究", "收盘评论", "相关新闻", "返回上一页")
    hits = sum(1 for marker in nav_markers if marker in text)
    return hits >= 3 and len(text) < 500


def _strip_noise_nodes(root: Tag) -> None:
    for selector in (".jp_sm", ".arc-xgwz", "dl.location", ".location", ".load-down"):
        for node in root.select(selector):
            node.decompose()

    for title in root.find_all(["h1", "h2", "h3"], limit=3):
        title_text = title.get_text(strip=True)
        if not title_text or len(title_text) > 100:
            continue
        if re.match(r"^[\u4e00-\u9fffA-Z]{1,8}[：:]", title_text):
            title.decompose()
            continue
        if any(key in title_text for key in ("日报", "日评", "早报", "周报", "月报", "研报", "专题")):
            title.decompose()
            break


def _is_navigation_block(node: Tag) -> bool:
    text = node.get_text(" ", strip=True)
    nav_markers = ("公司简介", "股东信息", "组织架构", "下载专区", "投资者园地")
    hits = sum(1 for marker in nav_markers if marker in text)
    return hits >= 3

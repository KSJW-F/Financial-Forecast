from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://www.cnzsqh.com"
RDQH_BASE_URL = "https://www.rdqh.com"
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURES = (b"\xff\xd8\xff",)
MIN_IMAGE_BYTES = 1024
PROMO_IMAGE_MARKERS = (
    "style/images/",
    "top_wx",
    "logo",
    "gallery/2/c/85",
    "gallery/t/8/23085",
    "gallery/n/n/76020",
    "cfmmc.com",
    "img_folder/ai",
    "img_folder/min.svg",
    "img_folder/max.svg",
    "img_folder/close.svg",
)


def is_valid_png(file_path: Path) -> bool:
    return _is_valid_image(file_path, PNG_SIGNATURE)


def is_valid_image(file_path: Path) -> bool:
    return _is_valid_image(file_path, PNG_SIGNATURE, *JPEG_SIGNATURES)


def _is_valid_image(file_path: Path, *signatures: bytes) -> bool:
    if not file_path.exists() or file_path.stat().st_size < MIN_IMAGE_BYTES:
        return False
    try:
        head = file_path.read_bytes()[:8]
    except OSError:
        return False
    return any(head.startswith(signature) for signature in signatures)


def _is_promo_image(src: str) -> bool:
    lowered = src.lower()
    return any(marker in lowered for marker in PROMO_IMAGE_MARKERS)


def parse_zheshang_article_id(html: str) -> str | None:
    match = re.search(r'<h2[^>]*class="con_tt"[^>]*value="(\d+)"', html, re.I)
    if match:
        return match.group(1)
    soup = BeautifulSoup(html, "lxml")
    node = soup.select_one("h2.con_tt")
    if node and node.get("value"):
        return str(node["value"])
    return None


def parse_content_image_src(html: str) -> str | None:
    images = find_main_content_images(html)
    return images[0] if images else None


def find_main_content_images(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    images: list[str] = []

    for selector in (".con_p", ".work_article1", ".arc-con"):
        container = soup.select_one(selector)
        if not container:
            continue
        for img in container.find_all("img"):
            src = (img.get("src") or "").replace("\\", "/").strip()
            if not src or _is_promo_image(src):
                continue
            if re.search(r"\.(png|jpe?g)(?:\?|$)", src, re.I):
                images.append(src)

    if images:
        return list(dict.fromkeys(images))

    for img in soup.select(".work_article1 img, .con_p img"):
        src = (img.get("src") or "").replace("\\", "/").strip()
        if src and not _is_promo_image(src) and re.search(r"\.(png|jpe?g)", src, re.I):
            images.append(src)
    return list(dict.fromkeys(images))


def infer_site_base(html: str, src: str) -> str:
    blob = f"{html} {src}".lower()
    if "rdqh.com" in blob:
        return RDQH_BASE_URL
    if "cnzsqh" in blob:
        return DEFAULT_BASE_URL
    if "doto-futures.com" in blob:
        return "https://www.doto-futures.com"
    if "dyqh.info" in blob or "大越期货" in html:
        return "https://www.dyqh.info"
    if "ciccwmf" in blob or "中金财富" in html:
        return "https://www.ciccwmf.cn"
    if "caqh" in blob or "长安期货" in html:
        return "http://www.caqh.com"
    if (
        "hongyuanqh" in blob
        or "hyqh" in blob
        or "宏源期货" in html
        or "swhygh" in blob
    ):
        return "http://www.hongyuanqh.com"
    if "minfutures" in blob or "wkqh" in blob:
        return "https://www.minfutures.com"
    if "hlqhgs" in blob or "华龙期货" in html:
        return "http://www.hlqhgs.com"
    if "cdfco" in blob or "中衍期货" in html:
        return "https://www.cdfco.com.cn"
    if "xinhu" in blob or "新湖期货" in html:
        return "https://www.xinhu.cn"
    if "greendh" in blob or "格林大华" in html:
        return "https://www.greendh.com"
    if "mmbiz.qpic.cn" in blob:
        return "https://mp.weixin.qq.com"
    if src.startswith(("http://", "https://")):
        parsed = urlparse(src)
        return f"{parsed.scheme}://{parsed.netloc}"
    return ""


def resolve_local_image_path(html_path: Path, src: str) -> Path:
    # 站点相对路径（/download/...）在 Windows 上不能当绝对路径，否则会落到盘符根目录
    if src.startswith(("http://", "https://")) or src.startswith("/"):
        cache_dir = html_path.parent / "_images"
        cache_dir.mkdir(parents=True, exist_ok=True)
        name = Path(urlparse(src).path).name or Path(src).name
        return cache_dir / name
    return (html_path.parent / src).resolve()


def build_image_urls(image_src: str, site_base: str, article_id: str | None = None) -> list[str]:
    filename = Path(image_src).name
    urls: list[str] = []
    if image_src.startswith(("http://", "https://")):
        urls.append(image_src)
    if site_base:
        urls.append(urljoin(site_base.rstrip("/") + "/", image_src.lstrip("/")))
    if article_id and site_base == DEFAULT_BASE_URL:
        article_page = f"{site_base.rstrip('/')}/cms/article/content?id={article_id}"
        urls.extend(
            [
                urljoin(article_page + "/", image_src),
                f"{site_base.rstrip('/')}/cms/article/img_folder/{filename}",
                f"{site_base.rstrip('/')}/cms/article/content/img_folder/{filename}",
            ]
        )
    return list(dict.fromkeys(urls))


def download_binary(url: str, timeout: int = 20) -> bytes | None:
    try:
        response = requests.get(
            url,
            headers=REQUEST_HEADERS,
            timeout=timeout,
            allow_redirects=True,
        )
        if response.status_code != 200:
            return None
        content_type = response.headers.get("content-type", "").lower()
        if "text/html" in content_type and not (
            response.content.startswith(PNG_SIGNATURE)
            or response.content.startswith(JPEG_SIGNATURES[0])
        ):
            return None
        if len(response.content) < MIN_IMAGE_BYTES:
            return None
        return response.content
    except requests.RequestException as exc:
        logger.debug("Download failed %s: %s", url, exc)
        return None


def fetch_image_from_article_page(article_id: str, base_url: str = DEFAULT_BASE_URL) -> tuple[str, bytes] | None:
    article_url = f"{base_url.rstrip('/')}/cms/article/content?id={article_id}"
    try:
        response = requests.get(
            article_url,
            headers={**REQUEST_HEADERS, "Referer": base_url},
            timeout=20,
        )
        if response.status_code != 200 or "con_p" not in response.text:
            return None
        src = parse_content_image_src(response.text)
        if not src:
            return None
        image_url = urljoin(article_page + "/", src)
        content = download_binary(image_url)
        if content:
            return image_url, content
    except requests.RequestException as exc:
        logger.debug("Article page fetch failed %s: %s", article_id, exc)
    return None


def ensure_image(html_path: Path, image_src: str, site_base: str = "") -> Path | None:
    local_path = resolve_local_image_path(html_path, image_src)
    if is_valid_image(local_path):
        return local_path

    local_path.parent.mkdir(parents=True, exist_ok=True)
    html = html_path.read_text(encoding="utf-8", errors="ignore")
    base = site_base or infer_site_base(html, image_src)

    if "con_tt" in html and "img_folder/" in html:
        article_id = parse_zheshang_article_id(html)
        if article_id:
            page_result = fetch_image_from_article_page(article_id, base_url=DEFAULT_BASE_URL)
            if page_result:
                _, content = page_result
                local_path.write_bytes(content)
                if is_valid_image(local_path):
                    logger.info("Downloaded image from article page -> %s", local_path)
                    return local_path

    for url in build_image_urls(image_src, base):
        content = download_binary(url)
        if not content:
            continue
        local_path.write_bytes(content)
        if is_valid_image(local_path):
            logger.info("Downloaded image %s -> %s", url, local_path)
            return local_path

    logger.debug("Could not download image for %s (%s)", html_path, image_src)
    return local_path if is_valid_image(local_path) else None


def ensure_content_image(html_path: Path, base_url: str = DEFAULT_BASE_URL) -> Path | None:
    html = html_path.read_text(encoding="utf-8", errors="ignore")
    image_src = parse_content_image_src(html)
    if not image_src:
        return None
    return ensure_image(html_path, image_src, site_base=base_url)

from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

import config
from src.extractors.html_recovery import ensure_html_file, is_broken_html, read_html_text
from src.extractors.image_fetcher import (
    ensure_content_image,
    ensure_image,
    find_main_content_images,
    infer_site_base,
    is_valid_image,
    resolve_local_image_path,
)

logger = logging.getLogger(__name__)

GENERIC_TITLES = {
    "浙商期货官网",
    "融达期货(郑州)股份有限公司",
    "公司概况公司简介组织架构公司历史参控股机构联系我们",
    "紫金天风期货",
    "紫金天风期货-商品期货",
    "交子期货",
    "502 Bad Gateway",
    "宏源期货",
    "研究中心",
    "中衍期货官方网站",
    "华龙期货股份有限公司",
    "新湖期货",
}

TITLE_SELECTORS = [
    "#detail_box h1",
    "h1.entry_title",
    "p.zj_main2_title",
    ".zj_main2_title",
    "div.t-con h2",
    ".t-con h2",
    ".news_show .new_tit",
    ".new_tit",
    ".rightbox_cont h1",
    ".title_newsinfo h1",
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
    "#detail_box .content",
    "#detail_box",
    "div.entry_main",
    "div#content.content_show",
    "div.content_show",
    "div.newscont",
    "h3.zj_main2_r_new_txt",
    ".zj_main2_r_new_txt",
    "div.zj_main2_r",
    "div.rightbox_cont",
    "div.rich_media_content",
    "div.rich_media",
    "div.js_article",
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
    ".date1010_crydetail",
    ".about_text1",
]

SITE_BASES = {
    "长安期货": ["http://www.caqh.com", "https://www.caqh.com", "http://www.caqh.com.cn"],
    "宏源期货": [
        "http://www.hongyuanqh.com",
        "http://hongyuanqh.com",
        "http://www.hyqh.com",
        "http://www.swhygh.com",
    ],
    "大越期货": ["https://www.dyqh.info", "http://www.dyqh.info"],
    "中金财富": ["https://www.ciccwmf.cn", "http://www.ciccwmf.cn"],
    "五矿期货": ["https://www.minfutures.com", "https://www.wkqh.cn"],
    "财信期货": ["https://mp.weixin.qq.com"],
    "华龙期货": ["http://www.hlqhgs.com", "https://www.hlqhgs.com"],
    "中衍期货": ["https://www.cdfco.com.cn", "http://www.cdfco.com.cn", "http://cdn.cdfco.com.cn"],
    "新湖期货": ["https://www.xinhu.cn", "http://www.xinhu.cn"],
    "格林大华": ["https://www.greendh.com", "http://www.greendh.com"],
    "冠通期货": ["https://www.gtqh.com", "http://www.gtqh.com"],
}


def extract_html(file_path: Path) -> tuple[str, str]:
    html = ensure_html_file(file_path) or read_html_text(file_path)
    if is_broken_html(html):
        return _broken_title(file_path), ""

    # html.parser 对非法嵌套（如 h3 内大量 p）更稳；lxml 会把正文拆空
    soup = BeautifulSoup(html, "html.parser")
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
    # 宏源：正文常夹在「访问次数」与「【附件】」之间，不在标准 content 容器里
    hongyuan = _extract_hongyuan_inline(html)
    if hongyuan:
        return hongyuan

    work = BeautifulSoup(str(soup), "html.parser")
    for tag in work(["script", "style", "nav", "footer", "iframe", "header"]):
        tag.decompose()

    # 附件型早评（宏源/新湖等）：先抽附件，避免导航壳或 PDF 目录抢先返回
    attachment_text = _extract_attachments(work, file_path, html)

    body_text = ""
    for selector in BODY_SELECTORS:
        node = work.select_one(selector)
        if not node or _is_navigation_block(node):
            continue
        text = _extract_from_container(node, file_path, html)
        if text and not _looks_like_nav_text(text) and not _looks_like_pdf_index(text):
            body_text = text
            break

    if attachment_text and (
        not body_text
        or _looks_like_pdf_index(body_text)
        or len(attachment_text) > len(body_text) + 80
    ):
        return attachment_text
    if body_text:
        return body_text
    if attachment_text:
        return attachment_text

    # 页面级正文图（长安 kindeditor、财信微信图、中衍 CDN 图）
    page_images = _extract_page_images(work, file_path, html)
    if page_images:
        return page_images

    meta = work.find("meta", attrs={"name": "description"})
    if meta and meta.get("content") and len(meta["content"]) > 50:
        # 新湖等：description 只是 PDF 文件名列表时不要当正文
        if not _looks_like_pdf_index(meta["content"]):
            return meta["content"].strip()

    return ""


def _extract_hongyuan_inline(html: str) -> str:
    """宏源早评：访问次数后到【附件】前的纯文本。"""
    lowered = html.lower()
    if "hyqh" not in lowered and "hongyuanqh" not in lowered and "宏源" not in html:
        return ""
    match = re.search(
        r"访问次数：.*?</div>\s*(.*?)\s*(?:<p[^>]*>\s*【附件】|【附件】)",
        html,
        re.I | re.S,
    )
    if not match:
        return ""
    chunk = BeautifulSoup(match.group(1), "html.parser").get_text("\n", strip=True)
    chunk = re.sub(r"\n{2,}", "\n", chunk).strip()
    if len(chunk) < 30:
        return ""
    if _looks_like_nav_text(chunk):
        return ""
    return chunk


def _extract_from_container(container: Tag, file_path: Path, html: str) -> str:
    root = BeautifulSoup(str(container), "html.parser")
    node = root.find(container.name) or root
    _strip_noise_nodes(node)

    text = node.get_text("\n", strip=True)
    lines = [line for line in text.splitlines() if line.strip()]
    if lines and lines[0].startswith("【") and len(lines[0]) < 8:
        lines = lines[1:]
    lines = [line for line in lines if not _is_chrome_line(line)]
    cleaned = "\n".join(lines).strip()

    # 仅 PDF 文件名的“正文”不算有效
    if re.fullmatch(r".+\.pdf", cleaned, re.I) or _looks_like_pdf_index(cleaned):
        cleaned = ""

    if len(cleaned) > 80 and not _looks_like_nav_text(cleaned):
        return cleaned

    image_text = _extract_container_images(container, file_path, html)
    if image_text:
        return image_text

    attachment_text = _extract_attachments(container, file_path, html)
    if attachment_text:
        return attachment_text

    if len(cleaned) > 20 and not _looks_like_nav_text(cleaned):
        return cleaned

    return ""


def _img_src(img: Tag) -> str:
    for key in ("data-src", "data-original", "src"):
        value = (img.get(key) or "").strip()
        if value and not value.startswith("data:image/gif"):
            return value.replace("\\", "/")
    return ""


def _extract_container_images(container: Tag, file_path: Path, html: str) -> str:
    texts: list[str] = []
    site_base = infer_site_base(html, "") or _broker_site_base(file_path)

    for img in container.find_all("img"):
        src = _img_src(img)
        if not src or _is_promo_image_src(src):
            continue
        if not (
            re.search(r"\.(png|jpe?g|webp)(?:\?|$)", src, re.I)
            or "mmbiz" in src
            or "kindeditor/attached" in src
            or "/download/" in src
            or "cdn.cdfco.com.cn" in src
            or "/uploads/" in src
        ):
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


def _extract_page_images(soup: BeautifulSoup, file_path: Path, html: str) -> str:
    """页面级正文图：kindeditor 附件、微信 mmbiz、download 目录 PNG。"""
    candidates: list[str] = []
    for img in soup.find_all("img"):
        src = _img_src(img)
        if not src or _is_promo_image_src(src):
            continue
        if any(
            key in src
            for key in (
                "kindeditor/attached",
                "mmbiz",
                "/download/",
                "cdn.cdfco.com.cn",
                "/uploads/",
            )
        ):
            candidates.append(src)
        elif re.search(r"attached/image/.*\.(png|jpe?g|webp)", src, re.I):
            candidates.append(src)
        elif re.search(r"\.(png|jpe?g|webp)(?:\?|$)", src, re.I) and "logo" not in src.lower():
            candidates.append(src)

    for href in _attachment_hrefs(soup):
        if re.search(r"\.(png|jpe?g)(?:\?|$)", href, re.I):
            candidates.append(href)

    texts: list[str] = []
    site_base = infer_site_base(html, "") or _broker_site_base(file_path)
    for src in list(dict.fromkeys(candidates))[:6]:
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


def _attachment_hrefs(root: Tag | BeautifulSoup) -> list[str]:
    hrefs: list[str] = []
    for link in root.find_all("a", href=True):
        href = link["href"].replace("\\", "/").strip()
        if re.search(r"\.(pdf|png|jpe?g)(?:\?|$)", href, re.I):
            hrefs.append(href)
        elif "/download/" in href and re.search(r"(早评|日报|早报|研报)", link.get_text(" ", strip=True)):
            hrefs.append(href)
    return list(dict.fromkeys(hrefs))


def _extract_attachments(root: Tag | BeautifulSoup, file_path: Path, html: str) -> str:
    site_base = infer_site_base(html, "") or _broker_site_base(file_path)
    texts: list[str] = []
    # 新湖周报汇总等页面常挂多份 PDF
    limit = 6 if ("新湖" in html or "xinhu" in html.lower()) else 4
    for href in _attachment_hrefs(root)[:limit]:
        if re.search(r"\.(png|jpe?g)(?:\?|$)", href, re.I):
            image_path = _resolve_image(file_path, href, html, site_base)
            if not image_path:
                continue
            try:
                from src.extractors.png_extractor import extract_png

                _, text = extract_png(image_path)
                if text.strip():
                    texts.append(text.strip())
            except Exception:
                continue
            continue

        if re.search(r"\.pdf(?:\?|$)", href, re.I):
            pdf_path = _download_attachment(file_path, href, site_base, suffix=".pdf")
            if not pdf_path:
                continue
            try:
                from src.extractors.pdf_extractor import extract_pdf

                _, text = extract_pdf(pdf_path)
                if text.strip():
                    texts.append(text.strip())
            except Exception:
                continue
    return "\n\n".join(texts)


def _broker_site_base(file_path: Path) -> str:
    broker = file_path.stem.split("_")[0]
    bases = SITE_BASES.get(broker, [])
    return bases[0] if bases else ""


def _download_attachment(
    file_path: Path,
    href: str,
    site_base: str,
    suffix: str,
) -> Path | None:
    cache_dir = file_path.parent / "_attachments"
    cache_dir.mkdir(parents=True, exist_ok=True)
    name = Path(urlparse(href).path).name or f"attach{suffix}"
    local_path = cache_dir / name
    if local_path.exists() and local_path.stat().st_size > 1024:
        return local_path

    urls: list[str] = []
    if href.startswith(("http://", "https://")):
        urls.append(href)
    broker = file_path.stem.split("_")[0]
    for base in [site_base, *SITE_BASES.get(broker, [])]:
        if base:
            urls.append(urljoin(base.rstrip("/") + "/", href.lstrip("/")))

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    for url in list(dict.fromkeys(urls)):
        try:
            response = requests.get(url, headers=headers, timeout=25, allow_redirects=True)
            if response.status_code != 200 or len(response.content) < 1024:
                continue
            content_type = response.headers.get("content-type", "").lower()
            if "text/html" in content_type and not response.content.startswith(b"%PDF"):
                continue
            local_path.write_bytes(response.content)
            return local_path
        except requests.RequestException as exc:
            logger.debug("attachment download failed %s: %s", url, exc)
    return None


def _resolve_image(file_path: Path, src: str, html: str, site_base: str) -> Path | None:
    local_path = resolve_local_image_path(file_path, src)
    if is_valid_image(local_path):
        return local_path

    if _is_zheshang_html(html):
        fetched = ensure_content_image(file_path, base_url=config.CNZSQH_BASE_URL)
        if fetched:
            return fetched

    bases = [site_base, infer_site_base(html, src), _broker_site_base(file_path)]
    broker = file_path.stem.split("_")[0]
    bases.extend(SITE_BASES.get(broker, []))

    for base in list(dict.fromkeys([b for b in bases if b])):
        fetched = ensure_image(file_path, src, site_base=base)
        if fetched and is_valid_image(fetched):
            return fetched

    # 微信图等绝对 URL
    if src.startswith(("http://", "https://")):
        return ensure_image(file_path, src, site_base="")
    return None


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
        "icon_pdf",
        "fileTypeImages",
        "wx_follow_avatar",
        "__bg_gif",
        "/images/img1.jpg",
        "/images/img2.jpg",
        "/images/img3.jpg",
        "/images/img4.jpg",
        "/images/img5.jpg",
        "memu_",
        "but_close",
        "zj_5.svg",
        "zj_6.svg",
        "zj_7.png",
        "zj_8.png",
        "zj_9.png",
    )
    return any(marker.lower() in lowered for marker in promo_markers)


def _is_chrome_line(line: str) -> bool:
    markers = (
        "收藏本页面", "打印", "[大", "[中", "[小", "当前位置：", "浏览次数",
        "下载附件", "上一篇", "下一篇", "来源：", "发布时间：", "点击数",
    )
    return any(marker in line for marker in markers)


def _looks_like_nav_text(text: str) -> bool:
    # 宏源等「仅标题+附件链接」空壳页
    if len(text) < 800 and "【附件】" in text and (
        "访问次数" in text or "评论列表" in text or "当前位置" in text
    ):
        return True
    nav_markers = ("当前位置", "首页", "投资咨询", "瑞达研究", "收盘评论", "相关新闻", "返回上一页")
    hits = sum(1 for marker in nav_markers if marker in text)
    return hits >= 3 and len(text) < 500


def _looks_like_pdf_index(text: str) -> bool:
    """新湖等：正文只是 PDF 文件名目录。"""
    if not text:
        return False
    pdf_hits = len(re.findall(r"\.pdf", text, re.I))
    if pdf_hits >= 2 and len(text) < 600:
        return True
    if pdf_hits >= 3 and _viewpoint_like_count(text) == 0:
        return True
    return False


def _viewpoint_like_count(text: str) -> int:
    return len(
        re.findall(
            r"偏多|偏空|操作建议|操作策略|交易策略|看涨|看跌|震荡|中性",
            text,
        )
    )


def _strip_noise_nodes(root: Tag) -> None:
    for selector in (".jp_sm", ".arc-xgwz", "dl.location", ".location", ".load-down", ".sxp_box"):
        for node in root.select(selector):
            node.decompose()

    for title in root.find_all(["h1", "h2"], limit=3):
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

from __future__ import annotations

import re
from pathlib import Path

import config
from src.extractors.html_recovery import is_broken_html
from src.extractors.image_fetcher import find_main_content_images, is_valid_image, resolve_local_image_path
from src.extractors.pdf_extractor import is_chart_heavy_pdf, is_garbled_text

GENERIC_TITLES = {
    "浙商期货官网",
    "融达期货(郑州)股份有限公司",
    "公司概况公司简介组织架构公司历史参控股机构联系我们",
}


def classify_extraction_issue(
    file_path: Path,
    file_type: str,
    title: str,
    raw_content: str,
    html_raw: str | None = None,
) -> str | None:
    rel_path = _relative_path(file_path)

    if file_type == "html":
        html = html_raw
        if html is None and file_path.exists():
            from src.extractors.html_recovery import read_html_text

            html = read_html_text(file_path)

        if html:
            if is_broken_html(html):
                return f"采集失败（502/空壳页面）：{rel_path}"

            image_srcs = find_main_content_images(html)
            if image_srcs:
                missing = []
                for image_src in image_srcs:
                    png_path = resolve_local_image_path(file_path, image_src)
                    if not is_valid_image(png_path):
                        missing.append(image_src)
                if missing:
                    return f"图片未采集或损坏：{rel_path} -> {missing[0]}"

        cleaned_len = len(raw_content or "")
        if cleaned_len < 30 or title in GENERIC_TITLES:
            return f"空壳页面（无正文）：{rel_path}"

        if cleaned_len < 80 and html and find_main_content_images(html):
            return f"图片研报 OCR 未提取到正文：{rel_path}"

    if file_type == "pdf":
        if is_garbled_text(raw_content or ""):
            return f"PDF 字体编码异常（已尝试 OCR）：{rel_path}"
        if re.search(r"数据跟踪|库存周度|产业链追踪|盘面套利观察|基差及资金", title or ""):
            return f"数据跟踪型报告（无观点句）：{rel_path}"
        if is_chart_heavy_pdf(raw_content or ""):
            return f"图表型早报（无可提取文字观点）：{rel_path}"

    if file_type in {"png", "jpg", "jpeg"} and len(raw_content or "") < 30:
        return f"图片 OCR 未提取到正文：{rel_path}"

    return None


def classify_unknown_reason(
    file_path: Path,
    file_type: str,
    title: str,
    raw_content: str,
    extraction_issue: str | None = None,
) -> str:
    if extraction_issue:
        return extraction_issue
    if len(raw_content or "") < 30:
        return f"正文过短，无法识别观点：{_relative_path(file_path)}"
    if file_type == "pdf" and re.search(
        r"数据跟踪|库存周度|产业链追踪|盘面套利观察|基差及资金", title or ""
    ):
        return f"数据跟踪型报告（无观点句）：{_relative_path(file_path)}"
    if file_type == "pdf" and is_chart_heavy_pdf(raw_content or ""):
        return f"图表型早报（无可提取文字观点）：{_relative_path(file_path)}"
    return f"正文无明确趋势观点：{_relative_path(file_path)}"


def _relative_path(file_path: Path) -> str:
    try:
        return str(file_path.relative_to(config.DATA_DIR)).replace("\\", "/")
    except ValueError:
        return str(file_path).replace("\\", "/")

from __future__ import annotations

from pathlib import Path

from src.extractors.html_extractor import extract_html
from src.extractors.pdf_extractor import extract_pdf
from src.extractors.png_extractor import extract_png


def extract_article(file_path: Path) -> tuple[str, str, str]:
    suffix = file_path.suffix.lower()
    if suffix in {".html", ".htm"}:
        title, content = extract_html(file_path)
        return title, content, "html"
    if suffix == ".pdf":
        title, content = extract_pdf(file_path)
        return title, content, "pdf"
    if suffix in {".png", ".jpg", ".jpeg"}:
        title, content = extract_png(file_path)
        return title, content, "png"
    raise ValueError(f"不支持的文件格式: {file_path}")

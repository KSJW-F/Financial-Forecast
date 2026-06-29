from __future__ import annotations

from pathlib import Path

from src.extractors.ocr import ocr_file


def extract_png(file_path: Path) -> tuple[str, str]:
    content = ocr_file(file_path)
    title = file_path.stem.split("_")[0] + "图片研报"
    return title, content.strip()

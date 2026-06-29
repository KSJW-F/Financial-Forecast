#!/usr/bin/env python
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.models import Article, Prediction, SessionLocal


def chinese_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for char in text if "\u4e00" <= char <= "\u9fff") / len(text)


def main() -> None:
    session = SessionLocal()
    data_dir = ROOT / "data"

    found_cat1 = False
    for article in (
        session.query(Article)
        .join(Prediction)
        .filter(Article.broker == "浙商期货", Prediction.trend == "未知", Article.file_type == "html")
    ):
        if len(article.cleaned_content or "") >= 80:
            continue
        file_path = data_dir / article.file_path
        if not file_path.exists():
            continue
        raw = file_path.read_text(encoding="utf-8", errors="ignore")
        match = re.search(r"img_folder/[^\"'\s>]+\.png", raw, re.I)
        if not match:
            continue
        png_path = file_path.parent / match.group(0).replace("\\", "/")
        if png_path.exists():
            continue
        print("【1】浙商期货图片研报（HTML 引用 PNG 但文件不存在）")
        print(f"   HTML: {file_path}")
        print(f"   缺失 PNG: {png_path}")
        print(f"   标题: {article.title}")
        print(f"   正文长度: {len(article.cleaned_content or '')} 字")
        found_cat1 = True
        break

    if not found_cat1:
        sample = data_dir / "20250401" / "323358" / "浙商期货_323358_0.html"
        raw = sample.read_text(encoding="utf-8", errors="ignore")
        match = re.search(r"img_folder/[^\"'\s>]+\.png", raw, re.I)
        png_path = sample.parent / match.group(0) if match else None
        print("【1】浙商期货图片研报（HTML 引用 PNG 但文件不存在）")
        print(f"   HTML: {sample}")
        print(f"   缺失 PNG: {png_path}")
        print(f"   标题: 【L日报20250401】（示例）")
        print(f"   说明: img_folder 下仅有 svg，无 png 图片")

    # 2. 中原期货空壳页
    for article in (
        session.query(Article)
        .join(Prediction)
        .filter(Article.broker == "中原期货", Prediction.trend == "未知")
    ):
        if len(article.cleaned_content or "") > 20 and "公司概况" not in article.title:
            continue
        file_path = data_dir / article.file_path
        print("【2】中原期货空壳 HTML（几乎无正文）")
        print(f"   路径: {file_path}")
        print(f"   标题: {article.title}")
        print(f"   正文长度: {len(article.cleaned_content or '')} 字")
        break

    # 3. PDF 乱码
    for article in (
        session.query(Article)
        .join(Prediction)
        .filter(Article.file_type == "pdf", Prediction.trend == "未知")
    ):
        content = article.cleaned_content or ""
        if len(content) < 200 or chinese_ratio(content) >= 0.08:
            continue
        file_path = data_dir / article.file_path
        print("【3】PDF 乱码（中文占比极低）")
        print(f"   路径: {file_path}")
        print(f"   标题: {article.title}")
        print(f"   中文占比: {chinese_ratio(content):.1%}")
        print(f"   正文预览: {content[:100]}")
        break

    session.close()


if __name__ == "__main__":
    main()

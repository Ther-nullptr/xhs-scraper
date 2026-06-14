#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对 scraper.py 下载的小红书笔记图片做 OCR, 提取图中文字。

小红书很多"文字信息"其实是印在图片里的 (截图/长图/PPT 式), 这个脚本把每篇
笔记的图片逐张 OCR, 按阅读顺序拼成干净文本, 方便后续喂给 LLM 提炼 skill。

引擎: RapidOCR (PP-OCR 模型, 中英文, 纯 CPU, 离线免费)。

用法:
  python ocr.py                       # OCR output/ 下全部笔记
  python ocr.py output/某博主_xxx      # 只处理某个博主目录
  python ocr.py output/某博主_xxx/某篇  # 只处理单篇

产物 (每篇笔记目录下):
  text_ocr.md   —— caption + 各图 OCR 文本
每个博主目录下:
  _all_notes.md —— 该博主所有笔记的 OCR 文本合并 (方便整体投喂 LLM)

已存在 text_ocr.md 的笔记会跳过 (断点续传)。
"""
import sys
from pathlib import Path

from PIL import Image
import numpy as np
from rapidocr_onnxruntime import RapidOCR

BASE = Path(__file__).resolve().parent
OUT = BASE / "output"
IMG_EXT = (".webp", ".jpg", ".jpeg", ".png")

engine = RapidOCR()


def ocr_image(path: Path) -> str:
    """OCR 单张图, 按 (从上到下, 从左到右) 顺序返回文本。"""
    try:
        img = Image.open(path).convert("RGB")
    except Exception as e:
        return f"[读图失败: {e}]"
    arr = np.array(img)
    result, _ = engine(arr)
    if not result:
        return ""
    lines = []
    for box, text, _score in result:
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        lines.append((min(ys), min(xs), text))
    # 行内允许约 12px 抖动, 近似还原阅读顺序
    lines.sort(key=lambda t: (round(t[0] / 12), t[1]))
    return "\n".join(t[2] for t in lines)


def process_note(folder: Path) -> str:
    out = folder / "text_ocr.md"
    if out.exists():
        return "skip(已存在)"
    imgs = sorted(
        p for p in folder.iterdir()
        if p.suffix.lower() in IMG_EXT and p.name.startswith("img_")
    )
    if not imgs:
        return "无图片"

    parts = []
    caption = folder / "text.md"
    if caption.exists():
        parts.append(caption.read_text(encoding="utf-8").strip())
    parts.append("\n---\n## 图片文字 (OCR)\n")
    for i, p in enumerate(imgs, 1):
        txt = ocr_image(p)
        parts.append(f"### 图 {i} — {p.name}\n\n{txt}\n")

    out.write_text("\n".join(parts), encoding="utf-8")
    return f"ok ({len(imgs)} 图)"


def find_note_folders(roots: list[Path]) -> list[Path]:
    folders: set[Path] = set()
    for root in roots:
        if not root.exists():
            print(f"  跳过不存在的路径: {root}")
            continue
        if (root / "note.json").exists():          # 本身就是单篇笔记目录
            folders.add(root)
        else:                                       # 博主目录 / output 根
            for nj in root.rglob("note.json"):
                folders.add(nj.parent)
    return sorted(folders)


def build_author_summaries(note_folders: list[Path]):
    """把每个博主目录下所有 text_ocr.md 合并成 _all_notes.md。"""
    by_author: dict[Path, list[Path]] = {}
    for f in note_folders:
        by_author.setdefault(f.parent, []).append(f)
    for author_dir, notes in by_author.items():
        chunks = []
        for nf in sorted(notes):
            t = nf / "text_ocr.md"
            if t.exists():
                chunks.append(f"\n\n{'=' * 70}\n# 笔记: {nf.name}\n{'=' * 70}\n")
                chunks.append(t.read_text(encoding="utf-8"))
        if chunks:
            (author_dir / "_all_notes.md").write_text("".join(chunks), encoding="utf-8")
            print(f"  合并 -> {author_dir / '_all_notes.md'}")


def main():
    args = sys.argv[1:]
    roots = [Path(a) for a in args] if args else [OUT]
    note_folders = find_note_folders(roots)
    print(f"待处理笔记: {len(note_folders)} 篇")
    for f in note_folders:
        try:
            r = process_note(f)
        except Exception as e:
            r = f"ERR: {e}"
        print(f"  {f.name}: {r}")
    build_author_summaries(note_folders)
    print("完成。")


if __name__ == "__main__":
    main()

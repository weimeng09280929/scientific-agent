"""Provenance of stored preview text and lossless, bounded text windows."""
from __future__ import annotations

import re


TEXT_LABELS = {
    "author_abstract": "作者摘要",
    "source_abstract": "来源提供的摘要",
    "body_opening": "正文开头摘录",
    "imported_excerpt": "导入文本摘录",
    "generated_summary": "模型生成摘要",
    "unknown": "已有文本摘录（摘要来源未核实）",
}


def preview_origin(paper):
    origin = paper.metadata.get("text_origin")
    if origin in TEXT_LABELS:
        return origin
    if paper.metadata.get("manually_imported"):
        return "imported_excerpt"
    # The older upload path explicitly stored the start of the extracted body.
    if paper.source_stage == "upload" and paper.metadata.get("parse_mode"):
        return "body_opening"
    return "unknown"


def preview_label(paper):
    return TEXT_LABELS[preview_origin(paper)] if (paper.abstract or "").strip() else None


def text_windows(text, size, overlap=0):
    """Yield exact offsets, including the tail of long unbroken paragraphs."""
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            # Prefer a sentence/line break near the end, never drop the suffix.
            tail = text[start + size * 3 // 4:end]
            breaks = list(re.finditer(r"[。！？\n]|[.!?;](?=\s)", tail))
            if breaks:
                end = start + size * 3 // 4 + breaks[-1].end()
        if text[start:end].strip():
            yield start, end
        if end == len(text):
            return
        start = max(start + 1, end - overlap)

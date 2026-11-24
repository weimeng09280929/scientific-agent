"""Math formatting policy and non-destructive Markdown table parsing."""
from __future__ import annotations

import re

MATH_DOCUMENT_GUIDE = r"""
数学排版规范：正文及符号表中的行内数学统一用 \( ... \)，独立公式用
单独成行的 \[ ... \]，公式内部保留标准 LaTeX。不要把公式放进反引号或代码块。
简单符号、单位及比例可以直接写成可读文字，如 N、α、20%；上下标、分式、
求和、矩阵用公式，不用拼凑的 Unicode 上下标替代复杂数学结构。
符号表同时给出中文含义及必要的维度/条件；使用前先定义，保持同一符号含义一致。
表格公式中的集合基数用 \lvert ... \rvert，范数用 \lVert ... \rVert，
避免裸竖线破坏 Markdown 列。向量内积用 \langle ... , ... \rangle。
每个关键公式后补一条简短中文解释；长公式按等号或加号用 aligned 拆行。
不要输出 HTML 或可执行排版指令，不要因排版要求改变算法、数字与研究结论。
"""

# Protect math and inline code before treating a pipe as a table separator.
INLINE_LITERAL = re.compile(r"\\\(.*?\\\)|\\\[.*?\\\]|\$\$.*?\$\$|(?<!\\)\$[^$\n]+(?<!\\)\$|(`+).*?\1", re.S)


def split_table_row(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith(r"\|"):
        text = text[:-1]
    literals: dict[str, str] = {}

    def protect(match: re.Match) -> str:
        key = f"\x00MATH{len(literals)}\x00"
        literals[key] = match.group()
        return key

    protected = INLINE_LITERAL.sub(protect, text)
    cells = re.split(r"(?<!\\)\|", protected)
    result = []
    for cell in cells:
        cell = cell.replace(r"\|", "|")
        for key, value in literals.items():
            cell = cell.replace(key, value)
        result.append(cell.strip())
    return result

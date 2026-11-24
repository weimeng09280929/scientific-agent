"""Math formatting regressions; no model calls or project writes."""
from core.document_math import MATH_DOCUMENT_GUIDE, split_table_row


def test_table_cardinality_and_norm_are_not_columns():
    row = r"| \(C=|\mathcal{C}|\) | \(\|x\|_2\) | 类别与范数 |"
    assert split_table_row(row) == [r"\(C=|\mathcal{C}|\)", r"\(\|x\|_2\)", "类别与范数"]


def test_table_escaped_pipe_and_code():
    assert split_table_row(r"| 标题 \| 副标题 | `x|y` |") == ["标题 | 副标题", "`x|y`"]


def test_dollar_math_and_scripts_are_preserved():
    assert split_table_row(r"| $a_i^t$ | $|S|$ |") == ["$a_i^t$", "$|S|$"]


def test_generation_policy_is_shared_by_both_method_paths():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert "MATH_DOCUMENT_GUIDE" in (root / "stages/design/agents.py").read_text(encoding="utf-8")
    assert "MATH_DOCUMENT_GUIDE" in (root / "web/api.py").read_text(encoding="utf-8")
    assert r"\lvert" in MATH_DOCUMENT_GUIDE
    assert "不要因排版要求改变算法" in MATH_DOCUMENT_GUIDE

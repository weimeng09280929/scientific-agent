"""Production PDF bytes must preserve evidence, conditions and scientific units."""
import shutil
import subprocess
from xml.etree import ElementTree

import pytest
from fastapi import HTTPException


@pytest.fixture
def pdf_api(monkeypatch):
    monkeypatch.setenv("SRA_WEB_SKIP_GUARD", "1")
    from web import api

    return api


def extract_pdf(response, tmp_path, *, bbox=False, raw=False):
    executable = shutil.which("pdftotext")
    if not executable:
        pytest.skip("Poppler pdftotext is required for actual PDF text verification")
    target = tmp_path / "delivery.pdf"
    target.write_bytes(response.body)
    result = subprocess.run([executable, "-enc", "UTF-8", *(["-bbox-layout"] if bbox else []),
                             *(["-raw"] if raw else []), str(target), "-"],
                            capture_output=True, check=True, timeout=30)
    assert b"Syntax Error" not in result.stderr, result.stderr.decode("utf-8", errors="replace")
    return result.stdout.decode("utf-8")


def test_pdf_safety_keeps_complete_text(pdf_api):
    text = "引文和条件必须完整保留。" * 100 + " NOT_MEASURED_AT_300K"
    rendered = pdf_api._pdf_safe_text(text)
    assert rendered.endswith("NOT_MEASURED_AT_300K")
    assert rendered == text


def test_actual_pdf_keeps_long_evidence_conditions_code_and_tables(pdf_api, tmp_path):
    markdown = (
        "# 科研方案交付检查\n\n"
        "> " + "source evidence " * 30 + "SOURCE_NOT_MEASURED_AT_300K\n\n"
        "- " + "condition supplied by user " * 20 + "USER_CONDITION_UNCONFIRMED\n\n"
        + "plain paragraph " * 30 + "PARAGRAPH_END\n\n"
        "```\n" + "long_code_" * 35 + "CODE_END\n```\n\n"
        "| " + "long header " * 20 + "HEADER_END | source |\n"
        "|---|---|\n"
        "| " + "source detail " * 40 + "TABLE_END | 未验证 |\n"
    )
    response = pdf_api._md_to_pdf_response(markdown, "delivery")
    assert response.media_type == "application/pdf" and response.body.startswith(b"%PDF")
    text = "".join(extract_pdf(response, tmp_path).split())
    for marker in ["SOURCE_NOT_MEASURED_AT_300K", "USER_CONDITION_UNCONFIRMED",
                   "PARAGRAPH_END", "CODE_END", "HEADER_END", "TABLE_END"]:
        assert marker in text


def test_actual_pdf_retains_scientific_unit_glyphs_and_bold_table_headers(pdf_api, tmp_path, caplog):
    if not pdf_api.os.path.isfile("C:/Windows/Fonts/segoeui.ttf"):
        pytest.skip("The supported Windows scientific fallback font is unavailable")
    markdown = (
        "# 材料性质与比较条件\n\n"
        "> 实测电导率 1.2 mS cm⁻¹；计算值 1.5×10⁻³ S/cm。\n\n"
        "- Li₆PS₅Cl；温度 −25 °C；原文线索，不能认定可比。\n\n"
        "| 电导率 S cm⁻¹ | 温度 °C |\n|---|---|\n| 1.2×10⁻³ | −25 |\n"
    )
    response = pdf_api._md_to_pdf_response(markdown, "scientific")
    text = "".join(extract_pdf(response, tmp_path).split())
    for value in ["1.2mScm⁻¹", "1.5×10⁻³S/cm", "Li₆PS₅Cl", "−25°C", "电导率Scm⁻¹"]:
        assert value in text
    assert "missing the following glyphs" not in caplog.text


def test_pdf_reports_oversized_table_without_silently_truncating_it(pdf_api):
    markdown = "| source |\n|---|\n| " + "too long " * 4000 + " SOURCE_TAIL |\n"
    with pytest.raises(HTTPException) as error:
        pdf_api._md_to_pdf_response(markdown, "too-large")
    assert error.value.status_code == 422
    assert "未截断" in error.value.detail and "DOCX" in error.value.detail


@pytest.mark.parametrize("prefix,suffix", [("", ""), ("> ", ""), ("- ", ""), ("```\n", "\n```")])
def test_actual_pdf_does_not_stretch_english_or_formula_spacing(pdf_api, tmp_path, prefix, suffix):
    paragraph = "alpha beta gamma " * 4 + "LONGFORMULA" * 3 + " final evidence " * 8
    response = pdf_api._md_to_pdf_response(prefix + paragraph + suffix, "spacing")
    tree = ElementTree.fromstring(extract_pdf(response, tmp_path, bbox=True))
    words = [node for node in tree.iter() if node.tag.endswith("word")]
    alpha = next(node for node in words if node.text == "alpha")
    beta = next(node for node in words if node.text == "beta")
    assert abs(float(alpha.attrib["yMin"]) - float(beta.attrib["yMin"])) < .1
    # Segoe/SimHei have normal spaces below 7 pt; justification made this >15 pt.
    gap = float(beta.attrib["xMin"]) - float(alpha.attrib["xMax"])
    assert 0 < gap < 7


def test_actual_pdf_keeps_heading_with_body_and_numbers_every_page(pdf_api, tmp_path):
    markdown = "\n".join(["filler line"] * 41 + ["## NEXT_SECTION", "FIRST_BODY_LINE", "SECOND_BODY_LINE"])
    response = pdf_api._md_to_pdf_response(markdown, "pagination")
    tree = ElementTree.fromstring(extract_pdf(response, tmp_path, bbox=True))
    pages = [node for node in tree.iter() if node.tag.endswith("page")]
    assert len(pages) == 2
    locations = {}
    for page_number, page in enumerate(pages, 1):
        for node in page.iter():
            if node.tag.endswith("word"):
                locations[node.text] = (page_number, float(node.attrib["yMin"]))
    assert locations["NEXT_SECTION"][0] == locations["FIRST_BODY_LINE"][0] == locations["SECOND_BODY_LINE"][0]
    assert locations["FIRST_BODY_LINE"][1] - locations["NEXT_SECTION"][1] < 40
    text = "".join(extract_pdf(response, tmp_path).split())
    assert "1/2" in text and "2/2" in text


def test_actual_pdf_wraps_long_tokens_without_inserting_spaces_or_losing_them(pdf_api, tmp_path):
    doi = "10.1038/nmat3066"
    formula = "Li9.54Si1.74P1.44S11.7Cl0.3"
    url = "https://example.org/" + "long-path/" * 35 + "URL_END"
    chinese = "材料条件需要核对" * 90 + "中文尾标记"
    markdown = "\n\n".join(["文献依据" * 8 + "DOI：" + doi, "材料证据" * 8 + formula, url, chinese,
                              "| 来源 |\n|---|\n| " + url + " |"])
    response = pdf_api._md_to_pdf_response(markdown, "long-tokens")
    text = extract_pdf(response, tmp_path, raw=True)
    assert doi in text and formula in text
    compact = "".join(text.split())
    assert url in compact and chinese in compact


def test_actual_pdf_keeps_chinese_list_marker_with_its_text(pdf_api, tmp_path):
    content = "材料条件需要核对" * 25
    response = pdf_api._md_to_pdf_response("- " + content, "chinese-list")
    text = extract_pdf(response, tmp_path, raw=True)
    first_line = next(line for line in text.splitlines() if line.strip())
    assert "材料条件" in first_line and first_line.startswith("-")
    assert content in "".join(text.split())


@pytest.mark.parametrize("prefix", ["", "\n".join(["filler"] * 43) + "\n", "# 中文标题\n"])
def test_actual_pdf_establishes_font_before_ascii_after_cjk(pdf_api, tmp_path, prefix):
    if not pdf_api.os.path.isfile("C:/Windows/Fonts/segoeui.ttf"):
        pytest.skip("The supported Windows scientific fallback font is unavailable")
    # An all-CJK first line uses the fallback font. The following ASCII must
    # remain readable after its temporary font context ends, including new pages.
    markdown = prefix + "中文首行\n- ASCII_DOI_10.1038/nmat3066 中文清单\n> 温度待确认\n> Li₆PS₅Cl 1.2 mS cm⁻¹"
    response = pdf_api._md_to_pdf_response(markdown, "font-state")
    text = extract_pdf(response, tmp_path, raw=True)
    assert "ASCII_DOI_10.1038/nmat3066" in text
    assert "Li₆PS₅Cl 1.2 mS cm⁻¹" in text
    executable = shutil.which("pdftoppm")
    if executable:
        result = subprocess.run([executable, "-scale-to", "250", "-png", str(tmp_path / "delivery.pdf"),
                                 str(tmp_path / "font-state")], capture_output=True, check=True, timeout=30)
        assert result.stderr == b"", result.stderr.decode("utf-8", errors="replace")

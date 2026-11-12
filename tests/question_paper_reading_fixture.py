"""Fictional text-layer PDF and offline provider output for reading acceptance."""
from unittest.mock import patch

from fpdf import FPDF

from core.knowledge import KnowledgeStore
from core.knowledge.schema import Paper, PaperChunk
from core.workspace.schema import GuideDraft
from tests.local_backup_fixture import expect

METHOD = "The Cedar encoder takes a table and a caption as separate inputs."
LIMIT = "The Cedar encoder was not evaluated on scanned tables."
EXPERIMENT = "The experiment compared Cedar against a keyword baseline."
COMPARE = "The Birch algorithm uses the caption as its only input."
BIRCH_DOI = "10.5555/birch-caption"
PAGES = [
    "Title page\nSample fictional paper for local software acceptance.",
    "Acknowledgments\nThe fictional authors thank the reading group.",
    "Background\nA bibliography is available separately.",
    "Methods\n"+METHOD+"\nIt returns an ordered list of passages.",
    "Experimental design\n"+EXPERIMENT+"\nBoth systems received identical questions.",
    "Limitations\n"+LIMIT+"\nThis does not support a conclusion about image-only documents.",
]


def make_pdf(pages=PAGES):
    pdf = FPDF()
    for text in pages:
        pdf.add_page()
        pdf.set_font("Helvetica", size=11)
        pdf.multi_cell(0,7,text)
    return bytes(pdf.output())


def upload_pdf(s, pages=PAGES, request_id="reading-pdf"):
    # Force the production local text-layer path, bypassing the shared test
    # fixture's unrelated fake MinerU adapter.
    with patch("core.tools.mineru_is_available", return_value=False):
        return s["a"].post(
            f'/api/projects/{s["pid"]}/upload-paper',
            files={"file":("Cedar-reading.pdf", make_pdf(pages), "application/pdf")},
            data={"title":"Cedar Table Retrieval", "request_id":request_id},
        )


def seed(s):
    result=expect(upload_pdf(s))
    pdf_id=result["paper_id"]
    s["store"].save_paper(Paper(
        paper_id="birch", title="Birch Caption Retrieval", doi=BIRCH_DOI,
        abstract="This abstract describes caption retrieval without a claim of cross-domain performance.",
        metadata={"text_origin":"author_abstract","chunk_origin":"body"},
    ))
    s["store"].save_paper_chunks([PaperChunk(chunk_id="birch-method",paper_id="birch",
                                            chunk_index=0,text="Methods\n"+COMPARE)])
    s["store"].save_paper(Paper(paper_id="legacy-opening", title="Legacy opening",
        abstract="The opening paragraph introduces an example dataset.", source_stage="upload",
        metadata={"parse_mode":"offline"}))
    s["store"].save_paper(Paper(paper_id="legacy-unknown", title="Legacy unattributed",
        abstract="The source of this saved text was not recorded."))
    s["store"].save_paper(Paper(paper_id="known-abstract",title="Known abstract only",
        abstract="The authors describe a research question without experiment details.",
        metadata={"text_origin":"author_abstract"}))
    s["store"].save_paper(Paper(paper_id="model-summary", title="Generated preview",
        abstract="This generated summary is not paper text.", metadata={"text_origin":"generated_summary"}))
    for index in (1, 2):
        s["store"].save_paper(Paper(paper_id=f"twin-{index}", title="Twin Retrieval Study",
                                   doi=f"10.5555/twin-{index}"))
    for pid, name in ((s["target"],"other-project"),(s["foreign"],"other-account")):
        store=KnowledgeStore(s["api"]._CONFIG.paths.project_db(pid))
        store.save_paper(Paper(paper_id=name,title=name,doi=f"10.5555/{name}"))
        store.save_paper_chunks([PaperChunk(chunk_id=name+"-chunk",paper_id=name,chunk_index=0,text=METHOD,page=4)])
    return pdf_id


def explain(context, text):
    citations=[]
    for paper in context["papers"]:
        for excerpt in paper["excerpts"]:
            for quote in (METHOD, LIMIT, EXPERIMENT, COMPARE):
                if quote in excerpt["text"]:
                    citations.append({"paper_id":paper["paper_id"],"part":"excerpt",
                                      "excerpt_id":excerpt["excerpt_id"],"quote":quote})
                    break
            else:
                continue
            break
    if not citations:
        reply = "离线界面验收样例。本轮内容不足以判断具体方法，需补充相关原文。"
    elif {source["paper_id"] for source in citations} == {"birch"}:
        reply = "离线界面验收样例。Birch 只使用说明文字作为输入。选入片段不足以核对它的输出或效果。"
    else:
        reply = ("离线界面验收样例。Cedar 的输入是表格和说明文字，输出是按相关程度排列的段落。"
                 "选入片段不足以核对其他任务的效果。")
    return GuideDraft(reply=reply,reading_citations=citations)

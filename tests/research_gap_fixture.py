"""Fictional material for real API and browser checks, not scientific evidence."""
from core.knowledge import KnowledgeStore
from core.knowledge.schema import Paper, PaperChunk
from tests.local_backup_fixture import expect

ABSTRACT = "摘要报告比较了两种检索策略，作者指出样本只来自一种教材。"
EXCERPT = "该比较没有纳入带图表的教材，作者将其列为本次研究的局限。"
REQUEST = "请整理研究现状与研究缺口，并给出一个候选研究问题。"


def seed(s):
    for pid, name in (("scope-abstract", "只提供摘要的样本"), ("scope-excerpt", "只提供正文片段的样本")):
        s["store"].save_paper(Paper(
            paper_id=pid, title=name, abstract=ABSTRACT if pid.endswith("abstract") else None,
            metadata={"text_origin":"author_abstract"} if pid.endswith("abstract") else {"chunk_origin":"body"},
        ))
    s["store"].save_paper_chunks([
        PaperChunk(chunk_id="scope-chunk", paper_id="scope-excerpt", chunk_index=0, page=7, text=EXCERPT),
    ])
    for pid, paper in ((s["target"], "other-project-source"), (s["foreign"], "other-account-source")):
        KnowledgeStore(s["api"]._CONFIG.paths.project_db(pid)).save_paper(
            Paper(paper_id=paper, title=paper, abstract=ABSTRACT)
        )
    return expect(s["b"].post(f'/api/projects/{s["foreign"]}/conversations', json={"title": "其他账号对话"}))["conversation"]["conversation_id"]


def draft_for(context, kind):
    sources = []
    for paper in context["papers"]:
        pid = paper["paper_id"]
        if pid == "scope-abstract":
            sources.append({"paper_id": pid, "part": "abstract", "quote": ABSTRACT})
        if pid == "scope-excerpt" and paper["excerpts"]:
            sources.append({"paper_id": pid, "part": "excerpt", "quote": EXCERPT})
    gap = {
        "kind": kind, "summary": "换成带图表的教材，两种检索方法的差距还需要核对。",
        "why": "可以了解已有比较适用于哪些教材。",
        "uncertainty": "还需补充包含图表的教材资料及与原设置可比较的结果。",
        "sources": sources,
    }
    if sources:
        gap["existing_work"] = "本轮材料涉及教材检索比较，所给内容说明了样本范围。"
    return {
        "reply": "研究缺口是已有研究中仍未充分回答、值得继续核查的问题。先看这里最值得核对的一点。",
        "research_gaps": [gap],
        "suggestions": [{
            "kind": "question", "title": "带图表教材中的检索策略比较",
            "content": "在可比较的带图表教材上，两种检索策略的表现差距是否改变？",
            "gap_link": {
                "gap_index": 0, "connection": "把教材范围的不足转成可以比较的问题。",
                "value": "帮助判断比较结论能否用在别的教材上。",
                "requirements": "需要带图表教材、相同的任务和一致的评价方式。先确认能否取得可比较的文本与图表样本，再核对两种方法是否接收相同输入、是否使用同一种评价标准，以及样本范围是否足以支持讨论。",
                "uncertainty": "数据许可和计算资源均待确认。",
            },
        }],
    }

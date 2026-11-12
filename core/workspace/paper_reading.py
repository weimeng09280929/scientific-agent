"""Bounded lexical selection and verification for the existing guide context."""
from __future__ import annotations

import json
import re
from collections import Counter

from core.knowledge.paper_text import TEXT_LABELS, preview_origin, text_windows
from core.workspace.evidence_refs import ref_id
from core.workspace.reading_coverage import asked_points, coverage_signals, covered_points, named_targets, procedural
from core.workspace.service import WorkspaceError

MAX_PAPERS = 6
MAX_PLAN_PAPERS = 12
PREVIEW_CHARS = 1200
EXCERPT_CHARS = 1050
TABLE_EXCERPT_CHARS = 1600
EXCERPTS_PER_PAPER = 3
PAPER_TEXT_CHARS = 12000
INPUT_CHARS = 42000

_INTENTS = {
    "limitations": ("局限", "限制", "不足", "适用范围", "研究缺口", "没有研究", "没研究", "research gap", "limitation", "caveat", "threats to validity", "not generalize"),
    "experiments": ("实验", "结果", "对照", "基线", "评价", "数据集", "指标", "命中率", "分母", "分子", "experiment", "result", "baseline", "evaluation", "ablation", "accuracy", "denominator", "metric"),
    "method": ("方法", "步骤", "算法", "输入", "输出", "原理", "重排", "再排序", "编码方式", "相似度",
               "method", "algorithm", "procedure", "pipeline", "input", "output", "rerank", "re-rank"),
    "overview": ("主要研究", "研究什么", "贡献", "目的", "contribution", "objective", "introduction", "research question"),
}
_FOCUS = re.compile(r"\b(?:only|just)\s+(?:read|look at|use|focus on)\s+(?:this|the selected)\s+(?:paper|article)\b|(?<!不)(?:只|仅)(?:想|需|需要)?(?:看|读|阅读|分析|解释|讨论|用|参考|基于|讲)(?:一下|一篇|所选|当前)?(?:这|此|该)(?:一)?(?:篇|份)(?:论文|文章|文献)?|(?<!不)(?:只|仅)看一篇", re.I)
_DIRECT_FOCUS = re.compile(r"\b(?:only|just)\s+(?:read|look at|use|focus on)\s+|(?<!不)(?:只|仅)(?:想|需要|需)?(?:阅读|读|看|分析|参考|基于|使用|用)", re.I)
_SWITCH_PAPER = re.compile(r"换(?:成|到|读|看)|改(?:读|看)|\bswitch (?:to|papers?)\b", re.I)
_DOI = re.compile(r"(?<![\w.])10\.\d{4,9}/[-._;()/:a-z0-9]+", re.I | re.ASCII)
_TITLE = re.compile(r"《([^》]+)》")
_QUOTED_TARGET = re.compile(r'^[“"「]([^”"」]+)[”"」]')
_TITLE_LIKE = re.compile(r"^[A-Z][a-zA-Z0-9-]+(?:\s+[A-Z][a-zA-Z0-9-]+)+\b")
_QUALIFIER = re.compile(r"\b(?:not|no|only|unless|except|however|cannot|without|limited)\b|不适用|仅限|不包括|没有|不能|除非|但", re.I)
_STOP = set("这篇 论文 文献 请问 请你 解释 什么 如何 怎样 怎么 帮我 哪些 说明 是否 研究 看看 看下 paper this that the and how what does only with please explain".split())
_CUE_WORDS = {word for words in _INTENTS.values() for word in words}
_REMOVE_ZH = sorted((word for word in _STOP | _CUE_WORDS if not word.isascii()), key=lambda word:(-len(word),word))
_TABLE = re.compile(r"(?:表\s*|\btable\s+)(\d+(?:\.\d+)*)(?![\d.])", re.I)
_TABLE_CAPTION = re.compile(r"^\s*(?:表\s*|table\s+)\d+(?:\.\d+)*\s*[:：]", re.I | re.M)
_TABLE_ROWS = re.compile(r"(?m)^[^\n]*(?:\d+\.\d+[^\S\n]*){3,}")
_RESULT_QUALIFIER = re.compile(
    r"\b(?:exception|however|we observe|compared (?:with|to)|worse than|similar (?:or|to)|whereas)\b"
    r"|例外|相比|观察到|在.{0,12}条件下", re.I,
)
_SECTION = re.compile(r"(?:§\s*|\bsection\s+|第\s*)(\d+(?:\.\d+)+)(?![\d.])", re.I)
_METHOD_ROLES = (
    re.compile(r"\b(?:given (?:a|an|the)|takes? as input|encod(?:e|es|er|ing)|input (?:is|consists))\b|输入为|以.+为输入", re.I),
    re.compile(r"\b(?:candidate generation|first stage|first pass|builds? an index|coarse search)\b|初步筛选|构建索引|第一阶段", re.I),
    re.compile(r"\b(?:re[- ]?rank(?:ing|s)?|second stage|second pass|inference|post[- ]?processing|compute[sd]? (?:the )?similarit|(?:dot|inner) product|forward pass|(?:independently|randomly) sampled)\w*\b|重排|第二阶段|推理过程", re.I),
    re.compile(r"\b(?:returns?|outputs?|resulting (?:list|set)|subsequently processed)\b|输出为|返回", re.I),
)
_METHOD_HEADING = re.compile(r"(?m)^\s*\d+(?:\.\d+)*\s+(?:model|method\w*|inference|(?:passage )?retrieval|candidate|re[- ]?rank\w*|algorithm|\w+[- ]stage|training|unsupervised|self-supervised|data (?:construction|preparation))\b", re.I)
_METHOD_SEQUENCE = re.compile(r"\b(?:then (?:we )?re[- ]?rank|we then |two[- ]stage|first stage|second stage|followed by)\b|第一阶段|第二阶段", re.I)
_METHOD_SCORE = re.compile(r"\b(?:dot|inner)\s+product\b|相似度计算", re.I)
_METHOD_EXECUTION = re.compile(r"\bat run[- ]?time\b|\bduring (?:retrieval|inference)\b|检索时|推理时", re.I)
_NATURAL_METHOD = re.compile(
    r"(?:怎么|怎样|如何).{0,22}(?:形成|构造|产生|生成|得到|配对|筛|选取|计算|转换|匹配|工作|训练|采样)"
    r"|(?:样本|候选|表示|训练数据|信号|分数).{0,12}(?:哪里来|从哪来|哪来的|如何来)"
    r"|\bhow\b.{0,65}\b(?:construct|form|build|sample|generate|encode|select|train|work)\w*\b", re.I,
)
_NATURAL_RESULT = re.compile(
    r"(?:哪(?:个|种|组)更[好快高低]|差多少|高多少|低多少|各是多少|比.{0,15}更[好快高低]|去掉.{0,14}(?:会怎样|变化))"
    r"|\b(?:by how much|which (?:one |method )?(?:performs better|is faster)|how much (?:higher|lower))\b", re.I,
)
_DEFINITION = re.compile(r"指的是什么|怎么定义|什么算|是什么意思|\bwhat (?:counts as|does .{1,40} mean)\b", re.I)
_FACETS = (
    ("正样本构造", r"正(?:样本|例|对)|\bpositive (?:pairs?|instances?|examples?|samples?)\b",
     r"positive (?:pairs?|instances?|examples?|samples?).{0,30}(?:take|form|construct|generate|consist)|(?:form|construct|generate|take).{0,45}(?:as|the) positive|identical positive pairs|predicts? (?:the )?input .{0,35} itself|two (?:forward|views)|正(?:样本|例|对).{0,12}(?:构造|形成|来自)"),
    ("负样本来源", r"负(?:样本|例|对)|\bnegative (?:pairs?|instances?|examples?|samples?)\b",
     r"in[- ]batch negatives?|negatives? (?:are|come|from|sampled|drawn)|(?:use|sample|draw|take)\w* .{0,55}(?:as negatives?|negative (?:pairs?|examples?|samples?))|负(?:样本|例).{0,15}(?:来自|取自|采样)"),
    ("候选来源", r"候选.{0,10}(?:哪|怎么|怎样|如何)|(?:怎么|怎样|如何).{0,8}(?:筛出|挑出|得到候选)|\bhow\b.{0,45}\bcandidates?\b",
     r"(?:select|obtain|generate|retrieve)\w* .{0,40}candidates?|candidate generation|候选.{0,12}(?:筛选|生成|来自)"),
)
_FACET_RULES = [(label, re.compile(question, re.I), re.compile(source, re.I))
                for label, question, source in _FACETS]
_READING_REGIMES = (
    (re.compile(r"无监督|\bunsupervised\b", re.I), re.compile(r"\bunsupervised\b|无监督", re.I)),
    (re.compile(r"自监督|\bself[- ]supervised\b", re.I), re.compile(r"\bself[- ]supervised\b|自监督", re.I)),
)
_METRIC_DEFINITION = re.compile(
    r"(?:recall|retrieval accuracy|hit rate|precision)[\s-]*(?:\(\s*(?:the )?(?:percentage|fraction|proportion)|,\s*which is (?:the )?(?:percentage|fraction|proportion))"
    r"|(?:指标|命中率|准确率|召回率)(?:是指|定义为|计算为)", re.I
)
_QUERY_RATIO = re.compile(r"(?:fraction|proportion|percentage) of (?:ques(?:-\s*)?tions|queries)\b", re.I)
_PASSAGE_RATIO = re.compile(
    r"(?:fraction|proportion|percentage) of (?:(?:top\s*[\d/]+\s+)?retrieved\s+passages|positive passages in the top)",
    re.I,
)
_LAYOUT = re.compile(r"(?<=[A-Za-z])-[ \t]*\r?\n[ \t]*(?=[A-Za-z0-9])|\s+|.", re.S)
_LIGATURES = str.maketrans({"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl"})


def reading_query(text, purpose="orientation"):
    lower = text.casefold()
    tables, sections = _TABLE.findall(text), _SECTION.findall(text)
    intent = next((name for name, words in _INTENTS.items() if any(w in lower for w in words)), None)
    if tables:
        intent = "experiments"
    elif intent is None and _NATURAL_RESULT.search(text):
        intent = "experiments"
    elif intent is None and (_NATURAL_METHOD.search(text) or procedural(text)):
        intent = "method"
    elif (intent is None and _DEFINITION.search(text)
          and text.strip(" \t\r\n。？！?!") not in ("这是什么意思", "那是什么意思")):
        intent = "experiments" if re.search(r"命中|召回|准确率|\b(?:hit|recall|accuracy)\b", text, re.I) else "definition"
    elif intent is None:
        intent = {"method": "method", "comparison": "experiments"}.get(purpose, "terms")
    focus = tuple(dict.fromkeys(_normalized(w) for w in re.findall(r"[“\"「](.{2,100}?)[”\"」]", text)))
    cleaned = lower
    for word in _REMOVE_ZH:
        cleaned = cleaned.replace(word, " ")
    terms = re.findall(r"[a-z0-9][a-z0-9_-]{2,}|[\u3400-\u9fff]+", cleaned)
    tokens = []
    for term in terms:
        if term.isascii():
            if term not in _STOP | _CUE_WORDS:
                tokens.append(term)
        else:
            tokens.extend(term[i:i+2] for i in range(len(term)-1))
    return intent, tuple(dict.fromkeys(tokens))[:80], focus, tables, sections


def _score(text, query):
    intent, tokens, focus, tables, sections = query
    folded = text.casefold()
    terms = _INTENTS.get(intent, ())
    # Heading evidence comes from the actual text. Storage order isn't a section label.
    first = folded.lstrip().split("\n", 1)[0]
    heading = first if len(first) <= 100 else ""
    words = set(re.findall(r"[a-z0-9_-]+", folded))
    heading_words = set(re.findall(r"[a-z0-9_-]+", heading))
    def contains(term, value, word_set):
        if not term.isascii() or " " in term:
            return term in value
        return term in word_set or term+"s" in word_set
    table_hits = set(_TABLE.findall(folded)) if tables else set()
    focus_text = _normalized(folded) if focus else ""
    definition = intent == "definition" and re.search(
        r"\b(?:defined as|refers to|denotes|we define)\b|定义为|指的是", folded)
    return (6 * bool(definition)
            + sum(14 for term in focus if term in focus_text)
            + sum(16 for number in set(tables) & table_hits)
            + sum(18 for number in set(sections) if re.search(
                rf"(?m)^\s*{re.escape(number)}\s+[a-z\u3400-\u9fff]", folded))
            + sum(4 for term in tokens if contains(term, folded, words))
            + sum(2 + 3 * bool(heading and contains(term, heading, heading_words))
                  for term in terms if contains(term, folded, words)))


class PaperReadingTargetError(WorkspaceError):
    """A single-paper request was not accepted because its target is unresolved."""


def _title_key(text):
    return " ".join(text.casefold().split())


def _named_papers(papers, text):
    normalized = _title_key(text)
    matches = {}
    for paper in papers:
        title = _title_key(paper.title)
        if len(title) >= 2 and re.search(r"(?<![a-z0-9])" + re.escape(title) + r"(?![a-z0-9])", normalized):
            matches.setdefault(title, set()).add(paper.paper_id)
    return matches.values()


def _doi_key(text):
    doi = text.casefold().rstrip(".;:")
    while doi.endswith(")") and doi.count(")") > doi.count("("):
        doi = doi[:-1]
    return doi


def single_paper_target(papers, selected, text, *, pending_attachment=False):
    reference = _FOCUS.search(text)
    switch = _SWITCH_PAPER.search(text)
    focus = reference or _DIRECT_FOCUS.search(text) or switch
    demonstrative = text.strip(" \t\r\n。？！?!") in ("这篇", "这篇论文", "就这篇")
    if not focus and not demonstrative:
        return None
    tail = text[focus.end():].lstrip(" ，,：:") if focus else ""
    titles = _TITLE.findall(tail)
    if not titles and not reference and (quoted := _QUOTED_TARGET.match(tail)):
        titles = [quoted[1]]
    doi_keys = {_doi_key(match[0]) for match in _DOI.finditer(tail)}
    targets = [
        {p.paper_id for p in papers if _title_key(p.title) == _title_key(title)}
        for title in titles
    ]
    if not titles:
        targets.extend(_named_papers(papers, tail))
    targets.extend(
        {p.paper_id for p in papers if (stored := _DOI.search(p.doi or "")) and _doi_key(stored[0]) == doi}
        for doi in doi_keys
    )
    if targets:
        if any(not matches for matches in targets):
            raise PaperReadingTargetError("本轮指定的题名或 DOI 未匹配当前项目可用论文。请核对完整题名或 DOI，或改用已勾选的一篇。")
        candidates = set.intersection(*targets)
        if not candidates:
            raise PaperReadingTargetError("本轮指定的题名与 DOI 或多个来源不一致。请保留一个明确目标再发送。")
        if len(candidates) != 1:
            raise PaperReadingTargetError("本轮指定的题名或 DOI 匹配多篇论文。请用唯一 DOI 区分，或只勾选一篇并改为「只看这篇」。")
        return candidates.pop()
    if _TITLE_LIKE.match(tail) or re.match(r"doi\b|https?://doi\.org/|《", tail, re.I):
        raise PaperReadingTargetError("本轮指定的论文未匹配当前项目可用资料。请核对完整题名或 DOI。")
    if not reference and not demonstrative:
        # A direct read instruction or explicit DOI marker needs a resolvable
        # target. Generic requests such as “只看实验结果” are not paper selectors.
        if switch:
            if re.fullmatch(r"(?:这|此|该)(?:一)?篇(?:论文|文章|文献)?[。？！?!]?", tail.strip()) and len(set(selected)) == 1:
                return selected[0]
            raise PaperReadingTargetError("请指定要切换的论文题名或 DOI，或只勾选一篇。")
        if re.search(r"\bread\b|阅读|读$", focus[0], re.I):
            raise PaperReadingTargetError("本轮指定的论文未匹配当前项目可用资料。请核对完整题名或 DOI。")
        return None
    ids = set(selected)
    if pending_attachment:
        if ids:
            raise PaperReadingTargetError("「这篇」同时可能指已勾选的论文或待上传附件。请写明已入库论文的题名或 DOI，或只保留一个来源。")
        return None  # The message route checks the single uploaded ID after upload.
    if len(ids) != 1 or not ids <= {p.paper_id for p in papers}:
        raise PaperReadingTargetError("「这篇」无法唯一对应当前项目的一篇可用论文。请只勾选一篇，或写明完整题名或 DOI。")
    return ids.pop()


def paper_selection(papers, selected, topic, text, purpose):
    by_id = {paper.paper_id: paper for paper in papers}
    if target := single_paper_target(papers, selected, text):
        return [target], True, True
    folded = text.casefold()
    named = [p.paper_id for p in papers if len(p.title.strip()) >= 5 and p.title.strip().casefold() in folded]
    named = named or [p.paper_id for p in papers if p.doi and p.doi.casefold() in folded]
    if len(named) == 1 and (not selected or len(selected) == 1):
        return named, True, True
    query = reading_query(text, purpose)
    is_reading = bool(selected or named or purpose in ("method","comparison")
                      or re.search(r"论文|原文|这篇|文献.{0,8}(?:方法|实验|局限)|\b(?:paper|article)\b", text, re.I))
    if selected or named:
        return list(dict.fromkeys(selected or named))[:MAX_PAPERS], False, is_reading
    topic_query = reading_query(topic)
    ranked = sorted(by_id, key=lambda pid: (
        -3*_score(by_id[pid].title, query)-_score(by_id[pid].abstract or "", query),
        -3*_score(by_id[pid].title, topic_query)-_score(by_id[pid].abstract or "", topic_query),
        pid,
    ))
    return ranked[:MAX_PAPERS], False, is_reading


def _normalized(text):
    return re.sub(r"\s+", " ", text).strip().casefold()


def _scope(paper):
    preview = paper["abstract"]
    excerpts = paper["excerpts"]
    kinds = []
    if preview:
        kinds.append(TEXT_LABELS[paper["preview_origin"]] + "节选")
    if excerpts:
        kinds.append(f"{len(excerpts)} 段已保存片段")
    paper["content_available"] = "excerpts" if excerpts else "abstract" if preview else "metadata"
    paper["scope"] = "、".join(kinds) + "，未提供全文" if kinds else "仅题名和元信息，未提供摘要或正文"
    return paper


_PAGE_CONTINUATION = re.compile(r"^\s*\d{1,5}\s*\n[ \t]*[a-z]")
_FOOTNOTE = re.compile(r"(?m)^\d{1,2}(?=[A-Z])")
_PURPOSE_VERB = re.compile(r"\b(?:reduce|save|shrink|minimize|limit|lower|compress)\w*\b", re.I)


def _motivation_boundary(chunks, excerpts, motivation_refs):
    """Supply the other half of a split sentence as its own source window."""
    by_index = {chunk.chunk_index: chunk for chunk in chunks}
    for entry in excerpts:
        if entry["excerpt_id"] not in motivation_refs or entry["start"] != 0 or not _PAGE_CONTINUATION.match(entry["text"]):
            continue
        prior = by_index.get(entry["chunk_index"] - 1)
        if not prior or not prior.page or entry["page"] != prior.page + 1:
            continue
        end = len(_FOOTNOTE.split(prior.text, maxsplit=1)[0].rstrip())
        tail = prior.text[max(0, end - 360):end]
        boundaries = list(re.finditer(r"(?<=[.!?])\s+(?=[A-Z])", tail))
        if not boundaries or tail.endswith((".", "!", "?")):
            continue
        start = end - len(tail) + boundaries[-1].end()
        prefix = prior.text[start:end]
        if not _PURPOSE_VERB.search(prefix) or any(
            e["chunk_id"] == prior.chunk_id and e["start"] <= start and e["end"] >= end for e in excerpts
        ):
            continue
        if sum(len(e["text"]) for e in excerpts) + len(prefix) > PAPER_TEXT_CHARS:
            return None
        return {
            "excerpt_id": f"{prior.chunk_id}:{start}:{end}", "chunk_id": prior.chunk_id,
            "chunk_index": prior.chunk_index, "start": start, "end": end, "text": prefix,
            "page": prior.page, "origin": entry["origin"],
        }, entry
    return None


def provided_paper(paper, chunks=(), *, text="", purpose="orientation", choose_excerpts=True, candidate=False,
                   complete_motivation=False):
    query = reading_query(text, purpose)
    intent, _, _, tables, sections = query
    origin = preview_origin(paper)
    saved_preview = (paper.abstract or "").strip()
    preview = "" if origin == "generated_summary" else saved_preview
    start, end = max(text_windows(preview, PREVIEW_CHARS, 160),
                     key=lambda span: _score(preview[slice(*span)], query), default=(0,0))
    pieces = []
    normalized_preview = _normalized(saved_preview)
    ordered_chunks = sorted(chunks, key=lambda c: (c.chunk_index, c.chunk_id)) if choose_excerpts else []
    if choose_excerpts:
        for chunk in ordered_chunks:
            if paper.metadata.get("chunk_origin") != "body" and normalized_preview and _normalized(chunk.text) in normalized_preview:
                continue
            spans = list(text_windows(chunk.text, EXCERPT_CHARS, 180))
            if intent == "experiments" and _TABLE_CAPTION.search(chunk.text) and _TABLE_ROWS.search(chunk.text):
                # The caption can follow the values in extracted PDF text.
                # Preserve the imported chunk as a single source when it fits.
                if len(chunk.text) <= TABLE_EXCERPT_CHARS:
                    spans.insert(0, (0, len(chunk.text)))
            for first, last in dict.fromkeys(spans):
                part = chunk.text[first:last]
                pieces.append((_score(part, query), chunk, first, last))
    ranked = sorted(enumerate(pieces), key=lambda pair: (-pair[1][0],pair[0]))
    selected = {}
    seen = set()
    selection_limit = 5 if candidate and intent == "method" else EXCERPTS_PER_PAPER
    facets = {label: source for label, question, source in _FACET_RULES if question.search(text)}
    if intent != "method":
        facets = {}
    regime = next((source for query_term, source in _READING_REGIMES if query_term.search(text)), None)
    coverage = {}
    if facets:
        for index, (_, chunk, lo, hi) in ranked:
            part = _layout_index(chunk.text[lo:hi])[0]
            hits = {label for label, source in facets.items() if source.search(part)}
            coverage[index] = (hits, bool(regime and regime.search(part)))

    def relevance(index):
        hits, matching_regime = coverage.get(index, ((), False))
        return bool(hits and matching_regime), len(hits)

    def take(index):
        _, chunk, first, last = pieces[index]
        part = chunk.text[first:last]
        key = _normalized(part)
        if not key or key in seen or len(selected) >= selection_limit:
            return
        if any(entry["chunk_id"] == chunk.chunk_id
               and min(last,entry["end"])-max(first,entry["start"]) > 220
               for entry in selected.values()):
            return
        seen.add(key)
        selected[index] = {"excerpt_id":f"{chunk.chunk_id}:{first}:{last}", "chunk_id":chunk.chunk_id,
                           "chunk_index":chunk.chunk_index,"start":first,"end":last,"text":part,
                           "page":chunk.page if type(chunk.page) is int and chunk.page > 0 else None,
                           "origin":"body_excerpt" if paper.metadata.get("chunk_origin")=="body" else "stored_excerpt"}

    if candidate and intent == "method" and not tables and not sections and pieces:
        points = asked_points(text)
        names = named_targets(points, text)
        search = {}
        for index, (score, chunk, lo, hi) in ranked:
            part = chunk.text[lo:hi]
            signals, section_score = coverage_signals(points, part, _layout_index(part)[0], text, names=names)
            search[index] = (signals, section_score, score)
        confirmed = {key for signals, _, _ in search.values() for key in names if signals.get(key) == 6}
        for signals, _, _ in search.values():
            for key in names.keys() - confirmed:
                signals.pop(key, None)
        names = {key: names[key] for key in names if key in confirmed}
        pending = {*(p.key for p in points), *names}
        strengths = dict.fromkeys({*pending, *(k for signals, _, _ in search.values() for k in signals)}, 0)

        def choose(options, *, rescue=False):
            def priority(index):
                signals, section_score, lexical = search[index]
                pages = {e["page"] for e in selected.values()}
                page = pieces[index][1].page
                gain = sum(max(0, signals.get(key, 0) - weight) for key, weight in strengths.items())
                return (-gain - section_score,
                        -int(section_score > 1), -sum(signals.values()),
                        -int(page not in pages), -section_score, -lexical, index)

            for index in sorted(options, key=priority):
                signals, section_score, lexical = search[index]
                if (rescue and not any(signals.get(k, 0) >= 3 for k in pending)) or (
                    not signals and section_score <= 0 and not lexical
                ):
                    continue
                take(index)
                if index in selected:
                    pending.difference_update(k for k, weight in signals.items() if weight >= 3)
                    for key, weight in signals.items():
                        strengths[key] = max(strengths.get(key, 0), weight)
                    return True
            return False

        for _ in range(EXCERPTS_PER_PAPER):
            if ((names or any(p.key == "prediction_target" for p in points)) and not pending
                    or not choose(set(search) - selected.keys())):
                break
        initial = list(selected.values())
        missing_before = sorted(pending)
        # A single supplemental pass with at most two more windows; it only
        # runs when a requested point still has no strong retrieval signal.
        for _ in range(selection_limit - EXCERPTS_PER_PAPER):
            if not pending or not choose(set(search) - selected.keys(), rescue=True):
                break
        excerpts = [selected[i] for i in sorted(selected)]
        mapping = covered_points(points, [(entry, search[i][0]) for i, entry in selected.items()])
        locators = lambda entries: [{k: e[k] for k in ("excerpt_id", "page", "start", "end")} for e in entries]
        boundary = None
        if complete_motivation and len(excerpts) < selection_limit:
            boundary = _motivation_boundary(ordered_chunks, excerpts, mapping.get("motivation", []))
        if boundary:
            prefix, suffix = boundary
            excerpts.insert(excerpts.index(suffix), prefix)
            mapping["motivation"].insert(mapping["motivation"].index(suffix["excerpt_id"]), prefix["excerpt_id"])
        return _scope({
            "paper_id": paper.paper_id, "title": paper.title, "abstract": "" if excerpts else preview[start:end],
            "preview_origin": origin, "preview_start": None if excerpts or not preview else start,
            "excerpts": excerpts,
            "question_coverage": [{"id": p.key, "question": p.question, "excerpt_ids": mapping[p.key]} for p in points],
            "selection_trace": {"initial": locators(initial), "missing_before": missing_before,
                                "supplement": locators(list(selected.values())[len(initial):]),
                                "replaced": [], "missing_after": sorted(pending),
                                **({"motivation_boundary": locators(boundary)} if boundary else {})},
            "selection_note": ("检索未定位到与这些要点直接对应的片段：" + "、".join(
                [p.question for p in points if p.key in pending] +
                [name for key, name in names.items() if key in pending]) if pending else ""),
        })
    if intent == "method" and not tables and not sections:
        windows = {i: chunk.text[lo:hi] for i, (_, chunk, lo, hi) in ranked}
        roles = {i: {j for j, rx in enumerate(_METHOD_ROLES) if rx.search(part)}
                 for i, part in windows.items()}
        # Reserve space for distinct steps, prioritizing later processing and
        # explicit stages over repeated abstract/background matches.
        uncovered = set(range(len(_METHOD_ROLES)))
        for group in ({1, 2}, {0, 3}, set(range(len(_METHOD_ROLES)))):
            def method_rank(entry):
                i, (score, _, _, _) = entry
                part = windows[i]
                process = 2 in group
                computes_score = bool(process and _METHOD_SCORE.search(part))
                heading = bool(_METHOD_HEADING.search(part))
                matching_regime, supported_facets = relevance(i)
                return (
                    -int(matching_regime),
                    -int(bool(facets and heading and supported_facets)),
                    -int(bool(process and _METHOD_SEQUENCE.search(part))),
                    -int(bool(computes_score and _METHOD_EXECUTION.search(part))),
                    -int(computes_score),
                    -len(roles[i] & uncovered & group),
                    -int(heading),
                    -supported_facets,
                    -score,
                    i,
                )

            candidates = [(i, piece) for i, piece in ranked if i not in selected and roles[i] & uncovered & group
                          and not _TABLE_CAPTION.search(windows[i])
                          and not (facets and _TABLE_ROWS.search(windows[i]))]
            for index, _ in sorted(candidates, key=method_rank):
                take(index)
                if index in selected:
                    uncovered -= roles[index]
                    break
    if intent == "experiments":
        section_pages = {
            chunk.page for _, (_, chunk, lo, hi) in ranked if chunk.page is not None
            and any(re.search(rf"(?im)^\s*{re.escape(number)}\s+[a-z\u3400-\u9fff]", chunk.text[lo:hi])
                    for number in sections)
        }
        table_page = None
        table_rank = sorted(ranked, key=lambda entry: (
            -len(_TABLE_ROWS.findall(entry[1][1].text[entry[1][2]:entry[1][3]])),
            -entry[1][0], entry[0],
        ))
        for index, (_, chunk, lo, hi) in table_rank:
            part = chunk.text[lo:hi]
            captions = _TABLE.findall("\n".join(m[0] for m in _TABLE_CAPTION.finditer(part)))
            if (captions and (not tables or set(tables) & set(captions))
                    and (tables or not section_pages or chunk.page in section_pages)):
                take(index)
                if index in selected:
                    table_page = chunk.page
                    break
        for index, (_, chunk, lo, hi) in ranked:
            part = _normalized(chunk.text[lo:hi])
            if index not in selected and (_METRIC_DEFINITION.search(part) or _QUERY_RATIO.search(part)):
                take(index)
                if index in selected:
                    break
        if table_page is not None:
            for index, (_, chunk, lo, hi) in ranked:
                part = chunk.text[lo:hi]
                if (index not in selected and chunk.page == table_page and not _TABLE_CAPTION.search(part)
                        and _RESULT_QUALIFIER.search(_normalized(part))):
                    take(index)
                    if index in selected:
                        break
    remaining_rank = ranked
    if facets and regime:
        remaining_rank = sorted(ranked, key=lambda item: (*(-n for n in relevance(item[0])), -item[1][0], item[0]))
    for index, (score, chunk, first, last) in remaining_rank:
        if not score and text and not relevance(index)[1]:
            break
        take(index)
        if index in selected and len(selected) == 1 and text:
            if intent == "experiments" and sections and not tables and chunk.page is not None:
                # A section-qualified comparison often needs its same-page table
                # as well as the narrative. Keep the numbered caption and rows.
                for candidate, (_, other, lo, hi) in ranked:
                    if candidate != index and other.page == chunk.page and _TABLE_CAPTION.search(other.text[lo:hi]):
                        take(candidate)
                        if candidate in selected:
                            break
            # Reserve one slot for an immediately adjacent qualification of the best hit.
            for adjacent in (index+1,index-1):
                if not 0 <= adjacent < len(pieces):
                    continue
                _, neighbor, lo, hi = pieces[adjacent]
                if (neighbor.chunk_index in (chunk.chunk_index,chunk.chunk_index+1,chunk.chunk_index-1)
                        and _QUALIFIER.search(neighbor.text[lo:hi])):
                    take(adjacent)
                    break
        if len(selected) >= EXCERPTS_PER_PAPER:
            break
    def brief(entry):
        return {key: entry[key] for key in ("excerpt_id", "page", "start", "end")}

    def facet_hits(part):
        normalized, _ = _layout_index(part)
        return {label for label, source in facets.items() if source.search(normalized)}

    initial = [brief(entry) for entry in selected.values()]
    missing = set(facets) - set().union(*(facet_hits(entry["text"]) for entry in selected.values()))
    supplement = []
    replaced = []
    reason = sorted(missing)
    if missing:
        # One local rescue pass, using the same project chunks and the same
        # three-window cap. A supplemental window must cover a missing facet.
        candidates = []
        for index, (score, chunk, lo, hi) in ranked:
            part = chunk.text[lo:hi]
            gain = facet_hits(part) & missing
            if gain and index not in selected:
                candidates.append((-int(relevance(index)[0]), -len(gain), -score, index))
        for _, _, _, index in sorted(candidates):
            old = selected.copy()
            if len(selected) == EXCERPTS_PER_PAPER:
                # Replace only a window whose removal preserves the facets
                # already located in the first selection.
                for victim in sorted(selected, key=lambda i: (pieces[i][0], -i)):
                    other_hits = set().union(*(facet_hits(e["text"]) for i, e in selected.items() if i != victim))
                    if facet_hits(selected[victim]["text"]) <= other_hits:
                        selected.pop(victim)
                        break
            seen.clear()
            seen.update(_normalized(e["text"]) for e in selected.values())
            take(index)
            if index in selected:
                supplement = [brief(selected[index])]
                replaced = [brief(entry) for i, entry in old.items() if i not in selected]
                missing -= facet_hits(selected[index]["text"])
                break
            selected = old
    excerpts = [selected[i] for i in sorted(selected)]
    # A body preview identical to a selected excerpt adds no evidence.
    sample = preview[start:end]
    if sample and any(_normalized(sample) in _normalized(c["text"]) for c in excerpts):
        sample = ""
    note = (
        "本轮没有选到与问题匹配的已保存正文片段；这不能判断论文是否讨论过。" if pieces and not excerpts
        else "没有可供本轮选取的正文片段。" if choose_excerpts and not pieces
        else ""
    )
    if (tables or sections) and intent == "experiments" and not any(_METRIC_DEFINITION.search(e["text"]) for e in excerpts):
        note = (note + " 本轮片段没有定位到明确指标定义；仅报告能核对的原表口径，不推断分母。").strip()
    if missing:
        note += " 本轮选入的正文还未定位到" + "、".join(sorted(missing)) + "的说明，无法仅据这些片段确认。"
    if origin == "generated_summary":
        note = "库中有模型生成摘要，未作为论文原文提供。 " + note
    if paper.metadata.get("parse_error"):
        note = "解析未取得可用正文。 " + note
    return _scope({"paper_id":paper.paper_id,"title":paper.title,"abstract":sample,
                   "preview_origin":origin,"preview_start":start if sample else None,
                   "excerpts":excerpts,"selection_note":note.strip(),
                   **({"selection_trace": {"initial": initial, "missing_before": reason,
                                             "supplement": supplement, "replaced": replaced, "missing_after": sorted(missing)}}
                      if facets else {})})


def bound_papers(papers):
    """Allocate whole windows equally before redistributing unused space."""
    if not papers:
        return []
    result = [{**paper, "abstract":"", "excerpts":[]} for paper in papers]
    share = PAPER_TEXT_CHARS // len(result)
    remaining = PAPER_TEXT_CHARS
    # Prefer the top matching excerpt to the preview when both cannot fit.
    queues = [[("excerpt", c) for c in p["excerpts"]] + ([("abstract", p["abstract"])] if p["abstract"] else []) for p in papers]
    # provided_paper returns reading order. Fair passes prevent the first paper
    # from consuming the pool even when many plan sources are requested.
    used = Counter()
    for i, queue in enumerate(queues):
        for kind, value in queue[:]:
            n = len(value["text"] if kind=="excerpt" else value)
            if n + used[i] <= share:
                _add(result[i],kind,value)
                used[i] += n
                remaining -= n
                queue.remove((kind,value))
    while any(queues):
        for i, queue in enumerate(queues):
            if queue:
                kind,value=queue.pop(0)
                n=len(value["text"] if kind=="excerpt" else value)
                if n <= remaining:
                    _add(result[i],kind,value)
                    remaining -= n
    for original, reduced in zip(papers,result):
        reduced["excerpts"].sort(key=lambda c:(c["chunk_index"],c["start"]))
        if len(reduced["excerpts"]) != len(original["excerpts"]) or reduced["abstract"] != original["abstract"]:
            reduced["selection_note"] = (reduced["selection_note"]+" 部分原文因本轮证据预算未提供。").strip()
        _scope(reduced)
    return result


def _add(paper, kind, value):
    if kind == "excerpt":
        paper["excerpts"].append(value)
    else:
        paper["abstract"] = value


def numbered_papers(papers, scope):
    """Number whole delivered windows and bounded contiguous short selections."""
    def numbered(body, identity):
        return ref_id("ev-", scope, [identity, body])

    def block(text, identity):
        parts = []
        # Offer a few contiguous lines at fixed positions. The full window
        # remains available as a reference, including any conditions omitted
        # by a shorter quotation.
        if len(text) > 320:
            for lo, hi in list(text_windows(text, 300))[:5]:
                parts.append([numbered(text[lo:hi], [identity, lo, hi]), lo, hi])
        return numbered(text, identity), parts

    result = []
    for paper in papers:
        pid = paper["paper_id"]
        projected = dict(paper)
        projected.pop("denominator_contrast", None)
        projected.pop("selection_trace", None)
        projected.pop("question_coverage", None)
        if paper.get("abstract"):
            identity = [pid, "preview", paper.get("preview_origin"), paper.get("preview_start")]
            projected["preview_evidence_id"], projected["preview_evidence_parts"] = block(paper["abstract"], identity)
        projected["excerpts"] = []
        for excerpt in paper.get("excerpts", []):
            ref, parts = block(excerpt["text"], [pid, excerpt["excerpt_id"], excerpt.get("page")])
            projected["excerpts"].append({**excerpt, "evidence_id": ref, "evidence_parts": parts})
        contrast = denominator_contrast(projected)
        if contrast:
            projected["denominator_contrast"] = contrast
        result.append(projected)
    return result


def denominator_contrast(paper):
    windows = [(paper.get("preview_evidence_id"), paper.get("abstract", ""))]
    windows += [(e["evidence_id"], e["text"]) for e in paper.get("excerpts", [])]
    query = next((ref for ref, text in windows if ref and _QUERY_RATIO.search(text)), None)
    passage = next((ref for ref, text in windows if ref and _PASSAGE_RATIO.search(text)), None)
    if query is None or passage is None:
        return None
    return {
        "query_denominator_evidence_id": query,
        "passage_denominator_evidence_id": passage,
        "note": "两个原文表达的分母不同，优先按正文方法的明确定义解释正文所述指标。"
                "没有额外信息时，表注的措辞不能用于推断另一套实际统计。",
        "mathematical_example": denominator_example(),
    }


def denominator_example():
    questions, k, hits = 10, 5, 4
    return {
        "questions": questions, "passages_per_question": k, "hit_questions": hits,
        "positive_passages_if_each_hit_has_one": hits,
        "hit_question_percent": 100 * hits / questions,
        "positive_passage_percent": 100 * hits / (questions * k),
    }


def denominator_explanation(example):
    questions, k, hits = (example[key] for key in ("questions", "passages_per_question", "hit_questions"))
    return (
        f"指标口径核对例（示意）：{questions} 个问题，每题取 {k} 段。{hits} 个问题各命中一段。"
        f"按问题计是 {hits}/{questions}={example['hit_question_percent']:g}%，"
        f"按候选段落计是 {hits}/{questions*k}={example['positive_passage_percent']:g}%。"
    )


def verify_denominator_explanation(reply, papers):
    """Reject a known false equivalence, only with both supplied definitions."""
    if not any(p.get("denominator_contrast") for p in papers):
        return
    one_hit = re.compile(r"一问一段命中|一问只中一段|(?:每问|每个.{0,8}(?:问题|查询))[^。]{0,22}(?:一个|一条|一段|\b1\b)[^。]{0,12}(?:命中|相关|答案)|(?:命中|相关)[^。]{0,10}(?:一条|一段|一个)")
    equality = re.compile(r"相等|相同|一致|对得上|一样|等价")
    for sentence in re.split(r"[。！？；;\n]", reply):
        if not (one_hit.search(sentence) and re.search(r"口径|分母|比例|两者|二者|两种定义|两个指标", sentence)):
            continue
        match = equality.search(sentence)
        if (match and not re.search(r"不|没|无|非", sentence[max(0, match.start()-5):match.start()])
                and not re.search(r"\bk\s*=\s*1\b|每问只(?:取|返回)一(?:条|段)", sentence, re.I)):
            raise WorkspaceError("每个命中问题只含一个命中段落，也不能让两个指标相等。请分开核对按问题和按段落的比例，原消息已保留。")


def fit_prompt(context, system, prefix, suffix, *, fact_source=None, method_scope=None):
    """Budget the complete serialized input. Never truncate evidence after selection."""
    from core.workspace.service import WorkspaceError

    payload = dict(context)
    payload.pop("_delivered_context", None)
    if method_scope is not None:
        from core.workspace.method_evidence import method_objects

        # Paper windows are kept whole throughout budgeting. The catalog is
        # bound to the numbered projection and included in the budget.
        objects = method_objects(payload["papers"], method_scope)
        payload["method_objects"] = objects
        symbols = [(o["paper_id"], o["symbol"]) for o in objects if o["symbol"]]
        payload["method_object_notes"] = {
            "definition_coverage": ("bounded_explicit_declarations" if symbols else "no_supported_declaration_found"),
            "unresolved_identity": any(symbols.count(s) > 1 for s in symbols),
        }
    def encode():
        if fact_source:
            from core.workspace.evidence_refs import fact_catalog
            payload["user_facts"] = fact_catalog(payload, *fact_source)
        if isinstance(payload.get("context_scope"),dict) and "history_messages" in payload["context_scope"]:
            payload["context_scope"] = {**payload["context_scope"],"history_messages":len(payload.get("history",[]))}
        return prefix + json.dumps(payload, ensure_ascii=False) + suffix
    prompt = encode()
    for key in ("existing_system","paper_inventory","saved_reading","allowed_suggestions","research_route","history","records"):
        if len(system)+len(prompt) <= INPUT_CHARS:
            break
        if key in ("history","records") and payload.get(key):
            payload[key] = payload[key][-3:]
        else:
            payload.pop(key,None)
        prompt = encode()
    while len(system)+len(prompt) > INPUT_CHARS and payload.get("history"):
        payload["history"] = payload["history"][1:]
        prompt = encode()
    if len(system)+len(prompt) > INPUT_CHARS:
        raise WorkspaceError("本次问题与必要材料超出可用输入范围。请缩小问题或减少选中的论文后重新发送。")
    return payload,prompt


def _layout_index(text, *, join_hyphenation=False):
    """Normalize PDF typography only and keep a mapping to raw code points."""
    chars, spans = [], []
    for token in _LAYOUT.finditer(text):
        value = token[0]
        normalized = (" " if value.isspace() else ("" if join_hyphenation else "-") if "\n" in value else
                      "" if value == "\u00ad" else value.translate(_LIGATURES))
        chars.append(normalized)
        spans.extend([token.span()] * len(normalized))
    return "".join(chars), spans


def _locate_quote(text, quote):
    start = text.find(quote)
    if start >= 0:
        if text.find(quote, start + 1) >= 0:
            return quote, None, None
        return quote, start, start + len(quote)
    key, _ = _layout_index(quote)
    if len(key) < 4:
        return None
    comparable, spans = _layout_index(text)
    start = comparable.find(key)
    if start < 0 or comparable.find(key, start + 1) >= 0:
        return None
    lo, hi = spans[start][0], spans[start + len(key) - 1][1]
    raw = text[lo:hi]
    # A match may not begin or end inside an expanded ligature.
    if len(raw) <= 320 and _layout_index(raw)[0] == key:
        return raw, lo, hi
    return None


def resolve_quote(source, papers):
    """Return only exact final slices, never current DB content or guessed pages."""
    from core.workspace.schema import GapSourceSnapshot
    from core.workspace.service import WorkspaceError

    if source.evidence_id:
        return resolve_evidence(source, papers)
    paper = papers.get(source.paper_id)
    quote = source.quote.strip()
    if not paper or len(quote) < 4:
        raise WorkspaceError("引文不属于本次实际提供的论文，或原文为空")
    if source.part == "abstract":
        match = None if source.excerpt_id else _locate_quote(paper.get("abstract", ""), quote)
        if match is None:
            raise WorkspaceError("引文不在本次实际提供的摘要或文本摘录中")
        raw, lo, hi = match
        return GapSourceSnapshot(paper_id=source.paper_id, part=source.part, quote=raw,
                                 quote_start=lo, quote_end=hi,
                                 title=paper["title"], origin=paper.get("preview_origin","unknown"))
    matches = [(c, found) for c in paper.get("excerpts", [])
               if (not source.excerpt_id or source.excerpt_id==c.get("excerpt_id"))
               and (found := _locate_quote(c["text"], quote)) is not None]
    if not matches:
        raise WorkspaceError("引文或片段编号不在本次实际提供的原文中")
    if len(matches) != 1:
        if any(found[0] != quote for _, found in matches):
            raise WorkspaceError("排版等价引文对应多处原文，请指定本轮片段编号")
        return GapSourceSnapshot(paper_id=source.paper_id,part=source.part,quote=quote,
                                 title=paper["title"],origin="stored_excerpt")
    match,(raw,lo,hi)=matches[0]
    return GapSourceSnapshot(paper_id=source.paper_id,part=source.part,quote=raw,title=paper["title"],
                             quote_start=lo,quote_end=hi,
                             origin=match.get("origin","stored_excerpt"),
                             page=match["page"] if type(match.get("page")) is int and match["page"] > 0 else None,
                             excerpt_id=match.get("excerpt_id"),chunk_id=match.get("chunk_id"),
                             start=match.get("start"),end=match.get("end"),chunk_index=match.get("chunk_index"))


def resolve_evidence(source, papers):
    from core.workspace.schema import GapSourceSnapshot

    for paper in papers.values():
        preview = {
            "text": paper.get("abstract", ""), "origin": paper.get("preview_origin"),
            "evidence_id": paper.get("preview_evidence_id"), "evidence_parts": paper.get("preview_evidence_parts", []),
            "start": paper.get("preview_start"),
        }
        for part, item in [("abstract", preview), *(("excerpt", e) for e in paper.get("excerpts", []))]:
            if not item["text"]:
                continue
            bounds = next(((lo, hi) for ref, lo, hi in item.get("evidence_parts", []) if ref == source.evidence_id), None)
            if item.get("evidence_id") == source.evidence_id:
                bounds = (0, len(item["text"]))
            if bounds is None:
                continue
            lo, hi = bounds
            raw = item["text"][lo:hi]
            if (source.paper_id and source.paper_id != paper["paper_id"] or source.part and source.part != part
                    or source.excerpt_id and source.excerpt_id != item.get("excerpt_id") or source.quote and source.quote != raw):
                raise WorkspaceError("引文编号与提交的来源或文字不一致。请使用本轮实际提供的编号。")
            return GapSourceSnapshot(evidence_id=source.evidence_id, paper_id=paper["paper_id"], part=part,
                                     quote=raw, title=paper["title"], origin=item.get("origin"), page=item.get("page"),
                                     excerpt_id=item.get("excerpt_id"), chunk_id=item.get("chunk_id"),
                                     chunk_index=item.get("chunk_index"), start=item.get("start"), end=item.get("end"),
                                     quote_start=lo, quote_end=hi)
    raise WorkspaceError("这个引文编号不属于本次阅读材料或已经失效。原消息已保留，请核对后再请求。")


def reading_citations(draft, context):
    from core.workspace.research_coach import _check_gap_claims
    from core.workspace.service import WorkspaceError

    papers = {p["paper_id"]:p for p in context["papers"]}
    if not context.get("plan_generation") and context.get("reading_selection",{}).get("active"):
        _check_gap_claims([draft.reply], any(p.get("abstract") or p.get("excerpts") for p in papers.values()))
        verify_denominator_explanation(draft.reply, papers.values())
        if context.get("reading_candidate") and any(p.get("denominator_contrast") for p in papers.values()):
            if re.search(r"百分数\s*[/／]\s*百分点|(?:准确率|召回率|指标)(?:本身)?的单位(?:为|是)\s*百分点", draft.reply):
                raise WorkspaceError("回答混用了百分数与百分点。已保留原问题和资料。")
        if any(p.get("denominator_contrast") for p in papers.values()):
            context.setdefault("delivery_warnings", []).append(denominator_explanation(denominator_example()))
    results,seen = [],set()
    for source in draft.reading_citations:
        if source.part=="excerpt" and not (source.excerpt_id or source.evidence_id):
            raise WorkspaceError("正文引文缺少本轮片段编号，无法定位")
        found=resolve_quote(source,papers)
        key=(found.paper_id,found.part,found.excerpt_id,found.quote)
        if key not in seen:
            seen.add(key)
            results.append(found)
    return results


def apply_delivered_evidence(context, model_context=None):
    """Keep authorizing context, but validate quotes against delivered evidence."""
    final=(model_context if model_context is not None else context).get("_delivered_context")
    if final is not None:
        traces = {p["paper_id"]: p["selection_trace"] for p in context.get("papers", []) if "selection_trace" in p}
        for key in ("papers","history","paper_inventory","allowed_suggestions","user_facts"):
            context[key]=final.get(key,{} if key=="user_facts" else [])
        context["papers"] = [
            {**p, "selection_trace": {**traces[p["paper_id"]], "delivered": [
                {k: e[k] for k in ("excerpt_id", "page", "start", "end")} for e in p["excerpts"]
            ]}} if p["paper_id"] in traces else p for p in context["papers"]
        ]

"""Bounded declaration extraction and attribute-level method fact validation."""

from dataclasses import dataclass
import re
from typing import Literal

from pydantic import Field

from core.workspace.evidence_refs import ref_id
from core.workspace.paper_reading import _layout_index, resolve_quote
from core.workspace.reading_analysis import (
    _MISSING, _SUBSCRIPT, _TYPE_NOUNS, _symbol_layout, _symbol_pattern,
    _type_labels,
)
from core.workspace.schema import Contract, GapSource, GuideDraft
from core.workspace.service import WorkspaceError

_SUB = rf"(?:_(?:[A-Za-z0-9]{{1,8}}|\{{[A-Za-z0-9]{{1,8}}\}})|[{_SUBSCRIPT}]+)"
_NON_VARIABLE = r"and|or|but|of|in|to|is|it|as|by|on|if|we|he|be|at|an|no|so|do|up"
_IDENTIFIER = rf"(?!(?:{_NON_VARIABLE})\b)[A-Za-z][A-Za-z0-9]{{0,11}}(?:{_SUB})?(?![\w{{}}])"
# An unmarked identifier after an English noun phrase is more ambiguous than
# one explicitly introduced by notation or a declaration verb.
_SHORT = rf"(?!(?:{_NON_VARIABLE})\b)[A-Za-z](?:[A-Za-z0-9]{{0,1}}|[A-Za-z0-9]{{0,5}}{_SUB})(?![\w{{}}])"
_TYPE_NOUN = "|".join(_TYPE_NOUNS.values())
_ROLE_WORD = r"(?!(?:and|or|but|with|using|then|is|are)\b)[A-Za-z][A-Za-z-]*"
_ROLE = rf"(?:{_ROLE_WORD} ){{0,4}}{_ROLE_WORD}"
_STOP = r"(?=\s*(?:$|[,.!?;:)]|\band\b|\bor\b|\busing\b|\bwith\b))"
_DECLARATIONS = (
    re.compile(rf"(?P<typed>{_TYPE_NOUN})\s*\(\s*(?P<symbol>{_IDENTIFIER})\s*\)", re.I),
    re.compile(rf"\b(?P<link>the same)\s+(?P<symbol>{_IDENTIFIER})\s+(?:is|represents)\s+"
               rf"(?:instead\s+)?(?:the\s+|a\s+)?(?P<typed>{_TYPE_NOUN})", re.I),
    re.compile(rf"\b(?:where|let)\s+(?P<symbol>{_IDENTIFIER})\s*(?:is\b|be\b|=)\s*"
               rf"(?:the\s+|a\s+)?(?P<typed>{_TYPE_NOUN})", re.I),
    re.compile(rf"(?<![\w])(?P<symbol>{_SHORT})\s+(?:denotes|represents|is)\s+"
               rf"(?:the\s+|a\s+)?(?P<typed>{_TYPE_NOUN})", re.I),
    re.compile(rf"(?P<typed>{_TYPE_NOUN})\s+of\s+{_ROLE},?\s+(?P<symbol>{_SHORT})"
               rf"(?:\s+and that of\s+{_ROLE},?\s+(?P<shared>{_SHORT}))?{_STOP}", re.I),
)
_HEADING = re.compile(r"(?im)^[ \t]*(?:\d+(?:\.\d+)+[ \t]+|(?:stage|phase|step)[ \t]+\d+[:.][ \t]*)"
                      r"[A-Za-z][^\r\n]{0,78}$")
_CONDITIONAL = re.compile(r"\b(?:if|suppos\w*|assum\w*|hypothetic\w*)\b[^.!?;]{0,65}$", re.I)


@dataclass(frozen=True)
class Declaration:
    symbol: str
    type: str
    start: int
    end: int
    symbol_start: int
    symbol_end: int
    heading_start: int | None
    heading: str | None
    same_object: bool


def declared_symbols(raw):
    """Capture declarations and map normalized matches back to raw offsets."""
    text, spans = _layout_index(raw, join_hyphenation=True)
    headings = list(_HEADING.finditer(raw))
    results = {}
    for pattern in _DECLARATIONS:
        for match in pattern.finditer(text):
            if _CONDITIONAL.search(text[max(0, match.start() - 70):match.start()]):
                continue
            kind = next(name for name, typed in _TYPE_NOUNS.items() if re.fullmatch(typed, match["typed"], re.I))
            start = spans[match.start()][0]
            heading = next((h for h in reversed(headings) if h.end() <= start), None)
            for group in ("symbol", "shared"):
                if not match.groupdict().get(group):
                    continue
                lo, hi = spans[match.start(group)][0], spans[match.end(group) - 1][1]
                end = spans[match.end() - 1][1]
                if group == "symbol" and match.groupdict().get("shared"):
                    end = hi
                declaration = Declaration(raw[lo:hi], kind, start, end, lo, hi,
                                          heading.start() if heading else None,
                                          heading[0].strip() if heading else None, bool(match.groupdict().get("link")))
                results.setdefault((lo, hi), declaration)
    return sorted(results.values(), key=lambda d: d.symbol_start)


def method_objects(papers, scope):
    """Bind identities to source declarations. Only an explicit same-object link can merge loci."""
    objects = []
    for paper in papers:
        pid = paper["paper_id"]
        if not paper.get("abstract") and not paper["excerpts"]:
            continue
        objects.append({"object_id": ref_id("obj-", scope, [pid, "method"]),
                        "symbol": None, "paper_id": pid, "scope": {"kind": "method_material"},
                        "definition_evidence_ids": [], "declarations": []})
        positions = set()
        for excerpt in sorted(paper["excerpts"], key=lambda e: (e.get("chunk_index", 0), e.get("start", 0))):
            offset = excerpt.get("start") or 0
            chunk = excerpt.get("chunk_id") or excerpt["excerpt_id"]
            for d in declared_symbols(excerpt["text"]):
                position = (chunk, offset + d.symbol_start)
                if position in positions:
                    continue
                positions.add(position)
                local_scope = {"chunk_id": chunk}
                if d.heading is not None:
                    local_scope.update(kind="heading", heading=d.heading, start=offset + d.heading_start)
                else:
                    local_scope.update(kind="declaration", start=offset + d.start)
                source = {"evidence_id": excerpt["evidence_id"], "type": d.type, "symbol": d.symbol,
                          "quote_start": d.start, "quote_end": d.end, "symbol_start": d.symbol_start,
                          "symbol_end": d.symbol_end, "chunk_id": chunk, "absolute_symbol_start": position[1],
                          "absolute_start": offset + d.start, "absolute_end": offset + d.end}
                previous = [o for o in objects if o["symbol"] and o["paper_id"] == pid
                            and _symbol_layout(o["symbol"]) == _symbol_layout(d.symbol)
                            and o["scope"]["chunk_id"] == chunk
                            and ((d.heading is not None and o["scope"] == local_scope)
                                 or (d.heading is None and o["scope"]["kind"] == "declaration"
                                     and o["declarations"][-1]["evidence_id"] == excerpt["evidence_id"]))]
                if d.same_object and len(previous) == 1:
                    obj = previous[0]
                    obj["declarations"].append(source)
                    if excerpt["evidence_id"] not in obj["definition_evidence_ids"]:
                        obj["definition_evidence_ids"].append(excerpt["evidence_id"])
                elif len(positions) <= 8:
                    objects.append({"object_id": ref_id("obj-", scope, [pid, *position, excerpt["evidence_id"]]),
                                    "symbol": d.symbol, "paper_id": pid, "scope": local_scope,
                                    "definition_evidence_ids": [excerpt["evidence_id"]], "declarations": [source]})
    return objects


class MethodClaim(Contract):
    claim_id: str = Field(min_length=1, max_length=32)
    subject_id: str = Field(min_length=1, max_length=80)
    aspect: Literal["type", "operation", "output_scope", "motivation", "implementation_detail"]
    status: Literal["explicit", "inference", "missing", "conflict"]
    value: str = Field(min_length=1, max_length=240)
    type_value: Literal["binary", "continuous"] | None = None
    evidence_ids: list[str] = Field(min_length=1, max_length=4)
    point_ids: list[str] = Field(default_factory=list, max_length=6)
    participant_ids: list[str] = Field(default_factory=list, max_length=4)


class MethodCitation(Contract):
    evidence_id: str = Field(min_length=1, max_length=80)


class AspectReadingDraft(Contract):
    reply: str = Field(min_length=1, max_length=14000)
    cited_paper_ids: list[str] = Field(default_factory=list, max_length=12)
    reading_citations: list[MethodCitation] = Field(default_factory=list, max_length=8)
    method_claims: list[MethodClaim] = Field(min_length=1, max_length=12)

    def to_guidance(self):
        return GuideDraft(reply=self.reply, cited_paper_ids=self.cited_paper_ids,
                          reading_citations=[GapSource(evidence_id=c.evidence_id) for c in self.reading_citations])


ASPECT_INSTRUCTIONS = (
    "先填 method_claims，再写 reply。subject_id 和 participant_ids 只能使用 method_objects 中的对象。"
    "每条只陈述一个 aspect。操作用 participant_ids 引用原对象，不重新定义。无符号方法用 method_material 对象。"
    "type 若有可核查的二值或连续定义，用 type_value；value 仍保留易读陈述。"
    "explicit 是本轮原文写明，inference 是从原文推断。missing 仅表示本轮提供的材料未说明某项属性，"
    "禁止推成整篇论文未说明。缺少精度、存储格式或硬件实现与类型不同。只陈述直接关系到用户问题的缺项。"
    "conflict 仅用于同一对象、同一属性与作用域中有明确冲突的原文，分别引用可定位的两处声明。"
    "不要把作者的动机写成已经证明的性能因果。正文与事实须一致。"
    "声明范围以原文局部身份和定位为准。同名但独立的声明保留不同对象编号，无法确定时不要强行绑定。"
)


_GLOBAL_ABSENCE = re.compile(
    r"(?:整篇论文|全篇|本文|论文(?!片段|节选|材料)|作者)[^。！？；\n]{0,12}"
    r"(?:从未|并未|没有|未曾|未(?:说明|给出|披露|报告|提供|定义))"
    r"|\b(?:paper|authors?)\b[^.!?;\n]{0,20}\b(?:never|does not specify|did not specify)\b", re.I)
_LOCAL_ABSENCE = re.compile(r"本轮|当前(?:的)?(?:材料|片段)|所给|给定片段|provided (?:material|text|excerpt)", re.I)
_UNKNOWN = re.compile(_MISSING.pattern + r"|未(?:说明|给出|披露|报告|提供|定义|知)|没有(?:说明|给出|提供)|缺(?:少|失)?"
                      r"|\b(?:unknown|not specified|not provided|unspecified)\b", re.I)
_DETAIL = re.compile(r"精度|位宽|比特数|字节|序列化|存储格式|硬件|超参数|实现参数|舍入|数值格式|"
                     r"\b(?:precision|bit[- ]width|serialization|hardware|rounding|storage format)\b", re.I)
_ALT_TYPE = re.compile(r"(?:连续(?:嵌入|向量|表示)|continuous (?:embedding|vector|representation)).{0,18}"
                       r"(?:另(?:有|一个)|维度|定义|是否存在)|(?:另(?:有|一个)|对应|是否).{0,12}"
                       r"(?:连续(?:嵌入|向量|表示)|continuous (?:embedding|vector|representation))", re.I)
_CAUSAL = re.compile(r"(?:因此|由此|这就)(?:足以|必然|已经)?(?:证明|证实)|(?:必然|一定)(?:提升|改善|降低)"
                     r"|\b(?:proves that|necessarily improves|guarantees higher)\b", re.I)
_CAUSAL_EVIDENCE = re.compile(r"\b(?:causal (?:effect|analysis)|controlled experiment)\b|因果(?:分析|效应)|受控实验", re.I)
_OPERATION = {
    "inner_product": re.compile(r"内积|点积|\b(?:inner|dot) product\b", re.I),
    "hamming": re.compile(r"汉明(?:距离)?|\bHamming distance\b", re.I),
    "cosine": re.compile(r"余弦(?:相似度)?|\bcosine similarity\b", re.I),
}
_TYPE_NEGATION = r"(?:不再是|不是|并非|不属于|is not\b|isn't\b|not\b)\s*(?:a\s+|the\s+)?"
_INDIRECT_TYPE_PREFIX = re.compile(
    r"(?:是否|假设|未(?:说明|确认|判断)|无法(?:确认|判断)|不能(?:确认|判断)|没有(?:说明|确认))\s*[“「\"']?\s*$"
    r"|\b(?:whether|suppose|if)\s+$", re.I)


def _labels(text, patterns):
    return {name for name, pattern in patterns.items() if pattern.search(text)}


def _negated_clause(text):
    return bool(re.search(r"并非|不是|不能(?:据此)?证明|不能.*保证|不代表|不能.*推出|"
                          r"\b(?:does not prove|not necessarily|cannot prove)\b", text, re.I))


def _mentions(symbol, text):
    return bool(re.search(_symbol_pattern(_symbol_layout(symbol)), _symbol_layout(text)))


def _declaration_support(obj, sources):
    return {(d["evidence_id"], d["quote_start"], d["type"])
            for d in obj["declarations"] for s in sources
            if (s.chunk_id or s.excerpt_id) == d["chunk_id"] and s.quote_start is not None
            and (s.start or 0) + s.quote_start <= d["absolute_start"]
            and d["absolute_end"] <= (s.start or 0) + s.quote_end}


def _direct_types(text, symbol):
    """Find direct predicates of this symbol, excluding typed operands of an operation."""
    text = _symbol_layout(text)
    subject = _symbol_pattern(symbol) + r"\s*(?:的(?:类型|表示)\s*)?"
    predicate = (rf"(?:(?P<negative>{_TYPE_NEGATION})|"
                 r"(?:是|为|属于|\b(?:is|denotes|represents)\b)\s*(?:(?:a|an|the)\s+|一个|一种)?)")
    for match in re.finditer(subject + predicate + rf"(?P<typed>{_TYPE_NOUN})(?![A-Za-z0-9_])", text, re.I):
        if not _INDIRECT_TYPE_PREFIX.search(text[:match.start()]):
            yield next(iter(_type_labels(match["typed"]))), bool(match["negative"])


def _check_direct_types(assertions, obj, field):
    known = {d["type"] for d in obj["declarations"]}
    if len(known) == 1 and any((kind in known) == negative for kind, negative in assertions):
        raise WorkspaceError(f"{field}对类型的说明与本轮原文声明不一致。")


def _check_claim_types(claim, obj, sources, objects):
    by_symbol = {}
    for other in objects:
        if other["symbol"]:
            by_symbol.setdefault(_symbol_layout(other["symbol"]), []).append(other)
    subject = _symbol_layout(obj["symbol"]) if obj["symbol"] else None
    for symbol, candidates in by_symbol.items():
        assertions = list(_direct_types(claim.value, symbol))
        if not assertions:
            continue
        local = [candidate for candidate in candidates if candidate["paper_id"] == obj["paper_id"]]
        if subject is None or not local or (claim.aspect == "type" and subject != symbol):
            raise WorkspaceError("明确符号的类型断言须绑定对应对象。")
        if len(local) == 1:
            target = local[0]
        else:
            scoped = [candidate for candidate in local if _declaration_support(candidate, sources)]
            if symbol == subject and (not scoped or obj in scoped):
                target = obj
            elif symbol != subject and len(scoped) == 1:
                target = scoped[0]
            else:
                raise WorkspaceError("类型断言涉及同名对象，引用无法确认其作用域。")
        _check_direct_types(assertions, target, "方法事实")


def _check_type(claim, obj, sources, objects):
    known = {d["type"] for d in obj["declarations"]}
    cited = _declaration_support(obj, sources)
    labels = _type_labels(claim.value)
    if claim.status == "conflict":
        if len(known) != 2 or {t for _, _, t in cited} != known:
            raise WorkspaceError("类型冲突须引用同一对象和作用域的两处不相容声明。")
        if claim.type_value is not None:
            raise WorkspaceError("冲突属性不能给出单一确定类型。")
    elif claim.status == "missing":
        if known or any(other["declarations"] and other["symbol"] and _mentions(other["symbol"], claim.value)
                        for other in objects if other["paper_id"] == obj["paper_id"]):
            raise WorkspaceError("本轮已有明确类型声明，不能把该类型写成未知。")
        if labels or claim.type_value is not None:
            raise WorkspaceError("缺失类型不能同时断言确定类型。")
    elif known:
        if (len(known) != 1 or claim.type_value not in known or labels - known or _UNKNOWN.search(claim.value)
                or re.search(_TYPE_NEGATION + _TYPE_NOUNS[claim.type_value], claim.value, re.I)
                or (claim.status == "explicit" and claim.type_value not in {t for _, _, t in cited})):
            raise WorkspaceError("方法事实的类型与本轮声明或引用不一致。")
    return "type_declaration_checked" if cited else "not_mechanically_verified"


def _check_reply_types(reply, objects):
    clauses = re.split(r"[。！？；，,\n]", reply)
    keys = [_symbol_layout(obj["symbol"]) for obj in objects if obj["symbol"]]
    for obj in objects:
        if not obj["symbol"]:
            continue
        symbol = _symbol_layout(obj["symbol"])
        known = {d["type"] for d in obj["declarations"]}
        if len(known) != 1 or keys.count(symbol) > 1:
            continue
        _check_direct_types(_direct_types(reply, symbol), obj, "正文")
        for clause in clauses:
            if not _mentions(symbol, clause):
                continue
            key = _symbol_pattern(symbol) + r"\s*(?:的(?:类型|表示))?\s*"
            if _negated_clause(clause):
                continue
            typed = _type_labels(clause)
            asserted = re.search(key + r"(?:是|为|属于|\bis\b)", _symbol_layout(clause), re.I)
            if ((_UNKNOWN.search(clause) and not _DETAIL.search(clause) and
                 re.search(r"类型|表示|定义|type|representation", clause, re.I))
                    or (typed - known and len(typed) == 1 and (asserted or not _labels(clause, _OPERATION)))):
                raise WorkspaceError("正文对类型的说明与本轮方法事实不一致。")


def aspect_analysis(draft, delivered):
    papers = {p["paper_id"]: p for p in delivered["papers"]}
    objects = {o["object_id"]: o for o in delivered["method_objects"]}
    points = {p["id"] for p in delivered.get("reading_targets", [])}
    claims = set()
    properties = {}
    reply = draft.reply
    if _GLOBAL_ABSENCE.search(reply):
        raise WorkspaceError("材料缺失的结论只能限定在本轮提供的片段。")
    _check_reply_types(reply, objects.values())
    operations = set()
    for claim in draft.method_claims:
        obj = objects.get(claim.subject_id)
        if claim.claim_id in claims or obj is None or set(claim.point_ids) - points:
            raise WorkspaceError("方法事实使用了重复编号或不属于本轮的对象、要点编号。")
        claims.add(claim.claim_id)
        pid = obj["paper_id"]
        sources = [resolve_quote(GapSource(evidence_id=ref, paper_id=pid), papers) for ref in claim.evidence_ids]
        _check_claim_types(claim, obj, sources, objects.values())
        if claim.participant_ids and claim.aspect != "operation":
            raise WorkspaceError("步骤参与者只用于运算事实。")
        for participant in claim.participant_ids:
            other = objects.get(participant)
            if (not other or other["paper_id"] != pid or not other["symbol"]
                    or not any(_mentions(other["symbol"], source.quote) for source in sources)):
                raise WorkspaceError("步骤参与者须能定位到当前同一论文的运算证据。")
            if any(o["symbol"] == other["symbol"] and o["paper_id"] == pid and o != other
                   for o in objects.values()) and not _declaration_support(other, sources):
                raise WorkspaceError("步骤中存在同名对象，引用无法确认其作用域。")
        if claim.status == "missing" and (_GLOBAL_ABSENCE.search(claim.value)
                                          or not _LOCAL_ABSENCE.search(claim.value)):
            raise WorkspaceError("缺失声明必须限定于本轮材料。")
        if claim.aspect != "type" and claim.type_value is not None:
            raise WorkspaceError("类型取值只用于类型属性。")
        if claim.status == "conflict" and claim.aspect != "type":
            raise WorkspaceError("当前机械校验不能确认该属性的冲突，需人工核对同一作用域。")
        review = "not_mechanically_verified"
        if claim.aspect == "type":
            signature = (claim.status, claim.type_value)
            if claim.subject_id in properties and properties[claim.subject_id] != signature:
                raise WorkspaceError("同一对象的类型事实互相矛盾。")
            properties[claim.subject_id] = signature
            review = _check_type(claim, obj, sources, objects.values())
        elif claim.aspect == "implementation_detail" and claim.status == "missing":
            if not _DETAIL.search(claim.value) or _ALT_TYPE.search(claim.value):
                raise WorkspaceError("实现缺项须具体指向实现属性，不能把已定义的表示改成缺失。")
        elif claim.aspect == "operation" and claim.status == "explicit":
            described = _labels(claim.value, _OPERATION)
            source_ops = _labels(" ".join(s.quote for s in sources), _OPERATION)
            if described and source_ops and described - source_ops:
                raise WorkspaceError("方法事实所述运算与引用不一致。")
            operations.update(described)
        elif claim.aspect == "motivation":
            clauses = re.split(r"[。！？；;\n]", claim.value + "。" + reply)
            if any(_CAUSAL.search(c) and not _negated_clause(c) for c in clauses) and not any(
                _CAUSAL_EVIDENCE.search(s.quote) for s in sources
            ):
                raise WorkspaceError("作者动机的引用不能支持确定的性能因果结论。")
        yield {**claim.model_dump(), "mechanical_coverage": review,
               "sources": [source.model_dump() for source in sources]}
    reply_ops = _labels(reply, _OPERATION)
    if len(operations) == len(reply_ops) == 1 and operations != reply_ops and not _negated_clause(reply):
        raise WorkspaceError("正文所述运算与结构化方法事实不一致。")

"""Bounded checks and table arithmetic over the delivered reading evidence."""

from decimal import Decimal
from typing import Literal
import re

from pydantic import Field

from core.workspace.schema import Contract, DirectPaperWireDraft, GapSource
from core.workspace.service import WorkspaceError


class MethodFact(Contract):
    subject: str = Field(min_length=1, max_length=64, description="易读的事实标题")
    symbol: str | None = Field(default=None, min_length=1, max_length=24, description="有符号时照抄本轮原文的符号")
    kind: Literal["definition", "use", "observation"] | None = None
    status: Literal["explicit", "inference", "missing", "conflict"]
    value: str = Field(min_length=1, max_length=200)
    evidence_ids: list[str] = Field(min_length=1, max_length=3)
    point_ids: list[str] = Field(default_factory=list, max_length=6, description="可对应 reading_targets 中的要点编号")


class MethodReadingDraft(DirectPaperWireDraft):
    method_facts: list[MethodFact] = Field(min_length=1, max_length=12)

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        return _extend_schema(cls, "method_facts", "MethodFact", *args, **kwargs)


class TableComparison(Contract):
    evidence_id: str = Field(min_length=1, max_length=80)
    left_row: str = Field(min_length=1, max_length=100)
    right_row: str = Field(min_length=1, max_length=100)
    column: int = Field(ge=1, le=30, description="原表数值列从 1 开始，不计行名")
    operation: Literal["difference", "percentage_points", "relative_percent"]


class TableReadingDraft(DirectPaperWireDraft):
    table_comparisons: list[TableComparison] = Field(default_factory=list, max_length=6)

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        return _extend_schema(cls, "table_comparisons", "TableComparison", *args, **kwargs)


def _extend_schema(cls, field, definition, *args, **kwargs):
    # DirectPaperWireDraft advertises only the reading fields. These variants
    # add one task-specific field without reopening general guide outputs.
    schema = DirectPaperWireDraft.model_json_schema(*args, **kwargs)
    full = super(DirectPaperWireDraft, cls).model_json_schema(*args, **kwargs)
    schema["properties"][field] = full["properties"][field]
    schema["$defs"][definition] = full["$defs"][definition]
    if field in full.get("required", []):
        schema.setdefault("required", []).append(field)
    return schema


METHOD_INSTRUCTIONS = (
    "先填 method_facts，再写 reply。每项列同一个对象或符号、含义、证据编号和状态。"
    "subject 写易懂标题。有可定位的变量时，symbol 单独照抄原文符号（大小写和下标不变）；没有就留空。"
    "kind 区分 definition（定义）、use（某一步使用该对象）、observation（其他）。"
    "explicit=原文写明，inference=推断，missing=当前片段未写明，conflict=原文有互不相容的陈述。"
    "按问题选择输入、符号定义、运算、步骤和输出中的必要项；同一对象定义只列一次。"
    "原文同一个符号在下一步再次出现时沿用该定义，除非本轮原文重新定义。"
    "向量运算允许两侧类型不同。只在原文有互斥定义时列冲突，不能把不同类型参与计算当成冲突。"
    "撰写回复前逐项核对解释和结尾是否与事实表一致；已明确的定义保持明确，未给出的实现细节单独说明。"
    "正文先直接回答，通常一段短结论加两三条必要解释。"
)
TABLE_INSTRUCTIONS = (
    "比较时先核对请求的数据集、k、指标、单位和行条件。"
    "有一整块同表证据且行名、同一数值列可定位时，在 table_comparisons 提交证据编号、"
    "两条完整原表行名、从 1 开始的数值列号及操作；程序按左值减右值计算。"
    "百分率之差用 percentage_points，相对变化（以右值为基准）用 relative_percent，其他同单位差用 difference。"
    "reply 可说明原表数值、方向和科学含义。不要在 reply 写数字差值、百分点变化或相对变化，"
    "这些计算由程序单独呈现。无法唯一定位时省略该比较，说明缺少什么，不猜算。"
)

_MISSING = re.compile(r"无法(?:确认|确定|判断)|不能(?:确认|确定|判断)|不足以(?:确认|确定|判断)|尚不(?:明确|清楚)|未知|需要.{0,12}核[对实]")
_SUBSCRIPT = "₀₁₂₃₄₅₆₇₈₉ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ"
_SUBSCRIPT_MAP = str.maketrans(_SUBSCRIPT, "0123456789aehijklmnoprstuvx")
_SYMBOL = re.compile(r"[A-Za-z][A-Za-z0-9]{0,11}(?:_[A-Za-z0-9]{1,8})?")
_TYPE = {
    "binary": r"binary(?:\s+(?:code|vector|representation))?|二(?:进制|元|值)(?:码|向量|表示)?",
    "continuous": r"continuous(?:\s+(?:embedding|vector|representation))?|连续(?:嵌入|向量|表示)?",
}
_TYPE_NOUNS = {
    "binary": r"(?:binary\s+(?:code|vector|representation)|二(?:进制|元|值)(?:码|向量|表示))",
    "continuous": r"(?:continuous\s+(?:embedding|vector|representation)|连续(?:嵌入|向量|表示))",
}


def _type_labels(text):
    return {name for name, pattern in _TYPE.items() if re.search(pattern, text, re.I)}


def _symbol_layout(text):
    text = re.sub(r"_\{([A-Za-z0-9]{1,8})\}", r"_\1", text)
    return re.sub(r"[" + _SUBSCRIPT + "]+", lambda m: "_" + m[0].translate(_SUBSCRIPT_MAP), text)


def _symbol_pattern(symbol):
    # An ASCII boundary keeps case, digits and underscores distinct even
    # next to Chinese punctuation or text. Never merge hp with h_p.
    return rf"(?<![A-Za-z0-9_])(?-i:{re.escape(symbol)})(?![A-Za-z0-9_])"


def _explicit_types(symbol, quotes):
    """Recognize only local typed declarations, never infer meaning from usage."""
    if not _SYMBOL.fullmatch(symbol):
        return set()
    key = _symbol_pattern(symbol)
    word = r"(?!and\b|or\b|with\b|using\b|then\b)[\w-]+"
    noun = rf"(?:{word}\s+){{0,5}}{word}"
    result = set()
    for quote in quotes:
        text = re.sub(r"(?<=[a-z])-\s*\n\s*(?=[a-z])", "", quote, flags=re.I)
        text = _symbol_layout(re.sub(r"\s+", " ", text))
        for name, pattern in _TYPE.items():
            before = (rf"(?:{pattern})(?:\s*\(\s*{key}\s*\)|\s+of\s+{noun},?\s+{key}"
                      rf"|\s+of\s+{noun}\s+and that of\s+{noun},?\s+{key})")
            after = rf"{key}\s*(?:denotes|represents|is|=|表示|是|为)\s*(?:the\s+|a\s+)?(?:{pattern})"
            local = rf"{key}\s*(?:沿用前文定义)?\s*(?:仍然?|仍旧)?\s*(?:是|为)\s*(?:段落|查询|对象|输入|输出|候选)?(?:的)?(?:{pattern})"
            if re.search(before + "|" + after + "|" + local, text, re.I):
                result.add(name)
    return result


def delivered_symbol_definitions(papers):
    """Bind each explicit type hint to an excerpt in its own paper."""
    from core.workspace.method_evidence import declared_symbols

    result = []
    for paper in papers:
        for excerpt in paper["excerpts"]:
            declarations = declared_symbols(excerpt["text"])
            for symbol in dict.fromkeys(d.symbol for d in declarations):
                types = {d.type for d in declarations if d.symbol == symbol}
                if len(types) != 1:
                    continue
                result.append({"symbol": symbol, "type": next(iter(types)), "evidence_id": excerpt["evidence_id"]})
                if len(result) == 8:
                    return result
    return result


def method_analysis(draft, papers):
    from core.workspace.paper_reading import resolve_quote

    definitions = {}
    for fact in draft.method_facts:
        sources = [resolve_quote(GapSource(evidence_id=ref), papers) for ref in fact.evidence_ids]
        scope = {source.paper_id for source in sources}
        all_text = [text for pid in scope for text in [
            papers[pid].get("abstract", ""), *(e["text"] for e in papers[pid].get("excerpts", []))
        ]]
        subject = _symbol_layout(fact.subject.strip())
        original = fact.symbol or (subject if _SYMBOL.fullmatch(subject) else None)
        if not original and (tail := re.search(r"(?:^|\s)([A-Za-z][A-Za-z0-9_]{0,19})\s*$", subject)):
            # Earlier drafts placed the symbol at the end of a human title.
            # A trailing token counts only when the original defines it.
            if _explicit_types(tail[1], all_text):
                original = tail[1]
        symbol = _symbol_layout(original.strip()) if original else ""
        linked = bool(symbol and _SYMBOL.fullmatch(symbol) and any(
            re.search(_symbol_pattern(symbol), _symbol_layout(source.quote)) for source in sources
        ))
        stated = _explicit_types(symbol, all_text) if linked and len(scope) == 1 else set()
        coverage = ("conflicting_or_scoped_source_definitions" if len(stated) > 1 or (linked and len(scope) > 1) else
                    "explicit_type_checked" if stated else "no_recognized_type_definition" if linked else
                    "symbol_not_located" if original else "no_symbol_identified")
        claim = _explicit_types(symbol, [fact.value]) if linked else set()
        if not claim:
            labels = _type_labels(fact.value)
            if len(labels) == 1 and (fact.kind in ("definition", None) or re.search(
                _symbol_pattern(symbol), _symbol_layout(fact.value), re.I
            ) or re.fullmatch(_TYPE[next(iter(labels))], fact.value.strip(" 。."), re.I)):
                claim = labels
        if len(stated) == 1 and (fact.status in ("missing", "conflict") or claim - stated):
            raise WorkspaceError("方法说明与本轮原文的明确符号定义不一致。原问题和资料已保留。")
        if fact.status == "conflict" and len(set(fact.evidence_ids)) < 2:
            raise WorkspaceError("原文冲突需要指出两处可核对的证据。")
        if linked and fact.status == "explicit" and fact.kind in ("definition", None) and len(claim) == 1 and len(stated) < 2:
            key = (frozenset(scope), symbol)
            if key in definitions and claim != definitions[key]:
                raise WorkspaceError("同一作用域中的符号定义互斥，请先核对原文。")
            definitions[key] = claim
        if len(stated) == 1:
            if _explicit_types(symbol, [draft.reply]) - stated:
                raise WorkspaceError("回复给同一符号使用了与本轮原文不一致的定义。原问题和资料已保留。")
            for clause in re.split(r"[。！？；，,\n]", _symbol_layout(draft.reply)):
                if (re.search(_symbol_pattern(symbol), clause, re.I)
                        and re.search(r"符号|定义|二值|二进|连续|表示", clause) and _MISSING.search(clause)
                        and not re.search(r"实现|代码|浮点|精度|存储|硬件", clause)):
                    raise WorkspaceError("回复把前面已经确认的符号定义重新写成了未知。原问题和资料已保留。")
        if coverage == "explicit_type_checked" and not claim:
            coverage = "source_type_found_no_comparable_fact_value"
        yield {**fact.model_dump(), "symbol_binding": {"original": original, "identity": symbol if linked else None,
                                                       "coverage": coverage, "source_types": sorted(stated)},
               "sources": [source.model_dump() for source in sources]}


_CAPTION = re.compile(r"(?im)^\s*(?:table|表)\s*(\d+(?:\.\d+)*)\s*[:：]")
_TABLE_REFERENCE = re.compile(r"(?:表\s*|\btable\s+)(\d+(?:\.\d+)*)(?![\d.])", re.I)
_VALUE = re.compile(r"(?<!\S)([-+]?\d+(?:\.\d+)?)(%|[a-zA-Z]+)?(?=\s|$)")
_COL_GROUP = re.compile(r"(?i)\btop[ -]?(\d+)|\b(?:QA\s+Acc\.?(?:\s*\(EM\))?|index\s+size|query\s+time)\b")
_FUSED_ROW = re.compile(r"(?<=\d)(?=[A-Z][A-Z0-9]*\s+\+\s+[A-Za-z][A-Za-z0-9-]*\s+\d)")
_TOP = re.compile(r"(?i)\btop[ -]?(\d+)")
_DELTA_PHRASE = re.compile(
    r"(?i)(?:相对(?:提高|提升|下降|增加|降低|增长)(?:了|为)?|相差|差(?:值|额|距)?(?:约为|为|是)?"
    r"|(?:高|低|多|少)(?:出|了)?|(?:提高|提升|下降|增加|降低|领先|落后)(?:了)?)\s*"
    r"(?:约\s*)?[−+\-]?\s*\d+(?:\.\d+)?\s*(?:个?百分点|[%％]|\bpp\b)"
    r"|(?i:(?:higher|lower|increase[ds]?|decrease[ds]?|difference)(?:\s+(?:by|of|is))?\s*"
    r"[−+\-]?\s*\d+(?:\.\d+)?\s*(?:percentage points?|%|pp\b))"
    r"|[−+\-]\s*\d+(?:\.\d+)?\s*(?:个?百分点|percentage points?|pp\b)"
)


def _table_columns(header, count):
    """Read a literal column header or a repeated dataset group in PDF text."""
    header = re.sub(r"(?<=time)(?=[A-Z]{2}\b)", " ", " ".join(header.split()))
    groups = list(_COL_GROUP.finditer(header))
    if groups:
        dataset_tokens = header[groups[-1].end():].split()
        repeated = next((dataset_tokens[:width] for width in range(1, len(dataset_tokens) // 2 + 1)
                         if len(dataset_tokens) % width == 0 and
                         dataset_tokens[:width] * (len(dataset_tokens) // width) == dataset_tokens), [])
        if repeated and len(dataset_tokens) <= count:
            grouped = len(dataset_tokens) // len(repeated)
            if grouped + count - len(dataset_tokens) == len(groups):
                return [f"{match[0]} · {dataset}" for match in groups[:grouped] for dataset in repeated] + [
                    match[0] for match in groups[grouped:]
                ]
    last = header.split() if "|" not in header else [
        cell.strip() for cell in header.strip("| ").split("|")
    ]
    if (len(last) == count + 1 and last[0].casefold() in ("method", "model", "system", "variant", "setting")
            and len(set(last[1:])) == count):
        return last[1:]
    return []


def _table_operands(text, comparison, requested_tables):
    # Use the block before the first caption only. With multiple captions
    # this block is eligible only when the question uniquely names its table.
    captions = list(_CAPTION.finditer(text))
    if not captions or captions[0].start() == 0:
        return None
    if requested_tables and captions[0][1] not in requested_tables:
        return None
    if len(captions) > 1 and (requested_tables != {captions[0][1]}
                              or sum(c[1] == captions[0][1] for c in captions) != 1):
        return None
    grid = _FUSED_ROW.sub("\n", text[:captions[0].start()])
    rows = {}
    first_row = len(grid)
    position = 0
    for line in grid.splitlines(keepends=True):
        normalized = line.strip().strip("|").replace("|", " ")
        if values := list(_VALUE.finditer(normalized)):
            if len(values) >= 2:
                label = " ".join(normalized[:values[0].start()].split())
                tail = normalized[values[0].start():]
                if not _VALUE.sub("", tail).strip():
                    first_row = min(first_row, position)
                    rows.setdefault(label, []).append([(Decimal(v[1]), v[2] or "") for v in values])
        position += len(line)
    left = rows.get(" ".join(comparison.left_row.split()), [])
    right = rows.get(" ".join(comparison.right_row.split()), [])
    if len(left) != 1 or len(right) != 1 or comparison.left_row == comparison.right_row:
        return None
    left, right = left[0], right[0]
    if len(left) != len(right) or comparison.column > len(left):
        return None
    columns = _table_columns(grid[:first_row], len(left))
    if not columns:
        return None
    idx = comparison.column - 1
    caption_line = text[captions[0].start():].lstrip().splitlines()[0]
    return captions[0][1], columns, left[idx], right[idx], caption_line


def table_calculation(source, comparison, question=""):
    result = _table_operands(source.quote, comparison, set(_TABLE_REFERENCE.findall(question)))
    if result is None:
        return None
    table, columns, (left, left_unit), (right, right_unit), caption = result
    column = columns[comparison.column - 1]
    asked_k = set(_TOP.findall(question))
    actual_k = _TOP.search(column)
    if asked_k and (not actual_k or actual_k[1] not in asked_k):
        return None
    if " · " in column:
        dataset = column.split(" · ", 1)[1]
        candidates = {label.split(" · ", 1)[1] for label in columns if " · " in label}
        asked = {word for word in candidates if re.search(
            rf"(?<![A-Za-z0-9]){re.escape(word)}(?![A-Za-z0-9])", question, re.I
        )}
        if asked and dataset not in asked:
            return None
    if re.search(r"百分点|percentage points", question, re.I) and comparison.operation != "percentage_points":
        return None
    if re.search(r"相对(?:变化|增幅|增长|下降)|relative (?:change|increase|decrease)", question, re.I) and comparison.operation != "relative_percent":
        return None
    if left_unit != right_unit:
        return None
    delta = left - right
    unit = left_unit
    percent_column = left_unit == "%" or (
        not left_unit and re.match(r"(?i)top[ -]?\d+", column) and
        re.search(r"(?i)recall|accuracy|percentage", caption)
        and max(left, right) > 1 and 0 <= min(left, right) <= max(left, right) <= 100
    )
    if comparison.operation == "percentage_points":
        if not percent_column:
            return None
        unit = " 个百分点"
    elif comparison.operation == "relative_percent":
        if right <= 0:
            return None
        delta, unit = (delta * 100 / right).quantize(Decimal("0.01")), "%（相对右值）"
    elif percent_column:
        return None
    fmt = lambda number: format(number.normalize(), "f")
    return (f"表 {table} · {column} · {comparison.left_row} 对 {comparison.right_row}："
            f"{fmt(left)}{left_unit} 对 {fmt(right)}{right_unit}，左减右 {fmt(delta)}{unit}。")


def apply_table_calculations(draft, papers, question, review=None):
    from core.workspace.paper_reading import resolve_quote

    citations = list(draft.reading_citations)
    results = []
    refused = 0
    for item in draft.table_comparisons:
        ref = GapSource(evidence_id=item.evidence_id)
        try:
            source = resolve_quote(ref, papers)
        except WorkspaceError:
            source = None
        computed = table_calculation(source, item, question) if source else None
        if review is not None:
            review.append({"selection": item.model_dump(), "result": computed,
                           "source": source.model_dump() if computed else None})
        if not computed:
            refused += 1
            continue
        if not any(c.evidence_id == item.evidence_id for c in citations):
            if len(citations) == 8:
                refused += 1
                continue
            citations.append(ref)
        if computed not in results:
            results.append(computed)
    # Model-derived deltas cannot compete with verified arithmetic. Leave raw
    # values and unrelated explanations intact, even when arithmetic refuses.
    reply, removed = _DELTA_PHRASE.subn("（数值差以核算为准）", draft.reply)
    if review is not None and removed:
        review.append({"unverified_prose_deltas_omitted": removed})
    if refused or removed and not results:
        results.append("部分比较未能在同一表格与可核对列中定位，未计算这些差值。")
    if results:
        extra = "\n\n表格数值核算\n" + "\n".join("- " + row for row in results)
        if len(reply) + len(extra) <= 14000:
            reply += extra
    return draft.model_copy(update={"reply": reply, "reading_citations": citations})

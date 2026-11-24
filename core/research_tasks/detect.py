"""检索意图识别：只识别明确的检索授权，宁可漏判也不误启动。

规则边界：
- 明确检索请求（强动作词 + 有主题）=> 可启动任务；
- 有检索意向但缺主题或主题过短 => 澄清 / 可编辑检索范围卡片；
- 概念解释、阅读已有材料、页面操作、刷新状态 => 一律不是检索授权。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 强动作词：明确表达"去外部检索"。仅保留显式检索动词；
# "了解研究现状/看看大家在做什么"属于讨论意图，由科研引导处理，不触发检索。
_ACTION = re.compile(
    r"(检索|搜索|搜(一)?下|查询|查(一)?下|帮我查|帮我搜|调研一下|做个调研|文献综述|文献调研"
    r"|related work|literature (review|search)|paper search|find papers)",
    re.I,
)
# 明确以论文为对象（弱信号，仅配合主题使用）。
_PAPER_TARGET = re.compile(r"(论文|文献|工作|文章|papers?|articles?)", re.I)
# 非授权模式：概念解释、阅读已有材料、页面/状态操作（一票否决）。
_EXPLAIN = re.compile(r"(什么是|是什么意思|定义是|介绍一下概念|名词解释)")
_READ_LOCAL = re.compile(
    r"(我上传|已上传|上传的|已有材料|已有文献|已保存|本地|库里|库里已有).{0,12}(论文|文献|材料)?"
)
_PAGE_OP = re.compile(r"(打开|切换|刷新|下载|进入|查看).{0,8}(页面|会话|材料|文件|状态|界面|列表|记录)")
_DENY_SEARCH = re.compile(
    r"(?:不要|不用|无需|不必|暂不|先不|别|不)(?:再|重新|重复|自动|进行|启动|去|做)*\s*"
    r"(?:检索|搜索|查询|查论文|找论文|联网)|(?:do not|don't)\s+(?:search|retrieve)", re.I
)


@dataclass
class SearchIntent:
    is_search: bool = False
    explicit: bool = False  # 明确到可以直接启动
    topic: str = ""
    year_from: int | None = None
    year_to: int | None = None
    reason: str = ""  # 不启动或需澄清的原因
    matched_hint: str = ""  # 命中的动作词，写入审计

    @property
    def complete(self):
        return self.is_search and self.explicit and bool(self.topic.strip())


def _extract_topic(text: str) -> str:
    """从请求中提取检索主题：区分主题、整理动作与连接词，保留术语原样。

    - "…方向的近期论文，并总结主要方法"：连接词「并总结」之后是对结果的处理
      要求，不属于主题；主题取连接词之前的部分。
    - 「并行计算」「并联电路」是合法科研术语：「并」后接的不是整理动作词，
      不会被误当作连接词截断或删除。
    """
    m = _CONNECTIVE.search(text)
    if m:
        text = text[: m.start()]
    cleaned = re.sub(
        r"(帮我|请|麻烦|我想|我需要|能否|可以|一下|谢谢|好吗|看看|了解一下|了解|梳理|整理|"
        r"检索|搜索|搜(一)?下|查询|查(一)?下|调研|文献综述|文献调研|研究现状|"
        r"总结|归纳|对比|概述|提炼|"
        r"代表性论文|代表论文|相关工作|related work|literature review|papers?|articles?)",
        " ",
        text,
        flags=re.I,
    )
    cleaned = re.sub(r"[，。！？,.!?、：:；;（）()【】\[\]\"'“”]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    # 去掉尾部的时间/对象修饰（如"的近期论文""代表性工作"），保留术语本体。
    cleaned = re.sub(
        r"[的了]*\s*(近期|最新|代表性|代表)?\s*(论文|文献|工作|文章|papers?|articles?)?\s*$",
        "",
        cleaned,
        flags=re.I,
    )
    # 兜底：清理残留的孤立连接词（如"…方向的 并"）。
    cleaned = re.sub(r"^(?:并|以及|和|还有|然后|接着|再)\s*", "", cleaned)
    cleaned = re.sub(r"\s*(?:[,，。;；]?\s*(?:并|然后|接着|以及|还有))$", "", cleaned)
    return cleaned.strip(" 的了嘛吧呢吗啊 ")[:200]


def _extract_years(text: str) -> tuple[int | None, int | None]:
    fy = re.search(r"(20\d{2})\s*(?:年)?\s*(?:之后|以来|至今|以后|起|-|–|~|到)", text)
    ty = re.search(r"(?:-|–|~|到|至)\s*(20\d{2})", text)
    single = re.search(r"(20\d{2})\s*年", text)
    year_from = int(fy.group(1)) if fy else None
    year_to = int(ty.group(1)) if ty else None
    if year_from is None and year_to is None and single:
        # 单一年份当作起点而非硬约束。
        year_from = int(single.group(1))
    return year_from, year_to


# 元话语：讨论"要不要/需要/先/别…检索"不是检索授权；先剥离再判定。
_META_TALK = re.compile(
    r"(需要|可以|能否|要不要|是否|先|再|别|不要|还没?|没有|如果|假如|或许|可能|准备|已经|曾)"
    r"(进行|去|做|一起|开始)?(检索|搜索|查询)",
    re.I,
)

# 整理/分析类动作词：出现在连接词（并/然后/接着…）之后时，其后的内容
# 是对检索结果的处理要求，不属于主题。
_SYNTH_ACTIONS = (
    r"(总结|整理|归纳|对比|比较|梳理|分析|综述|说明|介绍|给出|列出|报告|输出|"
    r"提炼|提取|评估|推荐|标注|概述|小结)"
)
# 「并行计算」「并联电路」中"并"后接"行/联"，不在 _SYNTH_ACTIONS 内，不会误截断。
_CONNECTIVE = re.compile(
    r"[，,；;。]\s*(?:然后|接着|同时|之后|之后再|再)?\s*(?:并\s*)?" + _SYNTH_ACTIONS
    + r"|\s(?:并|然后|接着|以及)\s*" + _SYNTH_ACTIONS
)


def classify_search_request(text: str) -> SearchIntent:
    """判定一条用户消息是否为明确的检索请求。纯规则，不调用模型。"""
    cleaned = (text or "").strip()
    if not cleaned or len(cleaned) > 6000:
        return SearchIntent(reason="空输入或超长输入，不作为检索请求")
    if _DENY_SEARCH.search(cleaned):
        return SearchIntent(reason="用户明确要求不检索，保留在已有资料讨论中")
    if _EXPLAIN.search(cleaned):
        return SearchIntent(reason="概念解释请求，不作为检索授权")
    if _PAGE_OP.search(cleaned):
        # 页面/材料/状态操作（含"打开文献页面""刷新检索状态"）不是新的检索授权。
        return SearchIntent(reason="页面或状态操作，不作为检索授权")
    # 剥离元话语后再找动作词："需要检索的问题"是讨论，"帮我检索X"才是授权。
    detectable = _META_TALK.sub(" ", cleaned)

    action = _ACTION.search(detectable)
    if not action:
        return SearchIntent(reason="未发现明确的检索动作词")
    # 检索/搜索也常是研究对象的名词（“信息检索方向”“两个检索模块”）。
    # 只有这种歧义词时，要求明确动词语气；其余已有明确查找入口保持原契约。
    matches=list(_ACTION.finditer(detectable))
    if all(m.group(0) in ('检索','搜索') for m in matches) and not re.search(
        r'^\s*(?:检索|搜索)(?!模块|方法|系统|方向|领域|任务|效果|算法|引擎)'
        r'|(?:请(?:你|帮我|给我)?|帮我|替我|开始|现在|进行|执行|我想(?:要)?|我要)\s*(?:先|再|重新|去)?\s*(?:检索|搜索)', detectable):
        return SearchIntent(reason='提及检索研究对象，未明确授权发起检索，保留讨论')
    if _READ_LOCAL.search(cleaned) and len(cleaned) < 60:
        return SearchIntent(reason="指向已有本地材料，不重复外检")

    topic = _extract_topic(detectable)
    years = _extract_years(cleaned)
    has_target = bool(_PAPER_TARGET.search(cleaned))
    if not has_target and not topic:
        return SearchIntent(
            is_search=True,
            explicit=False,
            reason="有检索意向但缺少主题与对象，需要澄清",
            matched_hint=action.group(0),
        )
    if not topic or len(topic) < 2 or _GENERIC_TOPIC.match(topic):
        return SearchIntent(
            is_search=True,
            explicit=False,
            reason="缺少明确的检索主题，需要澄清",
            matched_hint=action.group(0),
        )
    # 明确的动作词 + 可用主题 => 可启动。查询执行前仍记录原始请求。
    return SearchIntent(
        is_search=True,
        explicit=True,
        topic=topic,
        year_from=years[0],
        year_to=years[1],
        matched_hint=action.group(0),
    )


# 泛指词组合：主题完全由指代/泛称构成时不算明确主题（需要澄清）。
# 真实主题（如"联邦学习隐私保护"）不会整体命中。
_GENERIC_TOPIC = re.compile(
    r"^(?:相关|这个|这些|那些|上述|该|此|一些|几篇|最新|代表性|代表)?"
    r"(?:研究)?(?:方向|领域|问题|论文|文献|工作|资料|内容|主题|东西)?$"
)

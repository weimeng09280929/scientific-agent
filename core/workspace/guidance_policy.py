"""Small, explicit guidance policy. No search, automatic adoption or execution."""

from __future__ import annotations

import re

from core.workspace.schema import ActionProposal, ReplyOption
from core.workspace.service import WorkspaceError

CAPABILITIES = {
    "automatic_search": False,
    "case_content": False,
    "execute_experiments": False,
    "generate_results_or_papers": False,
    "max_stage": "design",
    "actions": ["view_references", "open_plan", "open_settings", "adopt_suggestion"],
}

# One identity and one response policy for every website guidance mode.
ROLE = "科研指导伙伴——帮助科研新手理解问题、核对依据，逐步形成自己的研究问题和方法草案。"
BASE_OPENING = "可以从一个科研兴趣开始，也可以带着已有方向、论文或问题继续。我们一起理解问题、核对依据，逐步形成研究问题和方法草案。"


def is_paused(text):
    # Explicit conversation intent, not e.g. “暂停训练如何实现”.
    return bool(
        re.search(
            r"^(?:好的[，, ]*)?(?:先)?(?:暂停(?:一下|讨论)?|结束(?:讨论|对话)?|不用(?:继续|回答)|不需要继续|不聊了|到此为止|今天先到这(?:里)?|先这样|就这样|够了|谢谢(?:你)?(?:，先到这里)?|stop|pause)(?:吧|了)?[。！!\s]*$",
            text.strip(),
            re.I,
        )
    )


def turn_preferences(text):
    """Explicit response wishes, not a new research phase or saved user fact."""
    paused = is_paused(text)
    brief = bool(re.search(r"简短|简要|短一点|一句话|一两句|不超过\s*\d+\s*(?:字|句)", text))
    detail = not brief and bool(re.search(r"详细|深入|展开|逐步解释|举(?:个|一个)?例|用例子", text))
    confused = bool(re.search(r"没听懂|没看懂|不太懂|不是我的领域|再简单一点|听不懂", text))
    explain_only = confused or bool(re.search(r"(?:只(?:需要)?|仅(?:需)?).{0,8}(?:解释|说明|回答|讲清)", text))
    no_questions = bool(re.search(r"(?:不要|不用|无需|别)(?:再)?(?:追问|提问|问我|反问)", text))
    no_options = bool(re.search(r"(?:不要|不用|无需|别)(?:再|提供|给我)?(?:推荐|选项|按钮|卡片)", text))
    return {"paused": paused, "length": "brief" if brief else "detailed" if detail else "adaptive", "needs_reexplanation": confused,
            "explain_only": explain_only, "no_questions": no_questions,
            "no_options": no_options, "allow_next_step": not (paused or explain_only or no_options)}


def strategy(context, text=""):
    conversation = context.get("conversation", {})
    mode = conversation.get("guidance_mode", "direct")
    mode_text = (
        "【指导方式 beginner】保留用户的带我入门选择。多解释一步原因和例子，一次处理一个主要问题；不知道时给容易理解的选择或保守起点，允许不确定，不发完整问卷。"
        if mode == "beginner" else
        "【指导方式 direct】保留用户的直接处理问题选择。先解决现有任务，少讲入门背景；必要概念仍解释，用户要求深入时可以展开，不要求重走入门流程。"
    )
    if mode == "case":
        mode_text += " 历史 case 设置不代表当前提供了案例内容，不虚构案例或切换人物。"
    prefs = turn_preferences(text)
    length = {"brief": "遵守用户本轮字数或句数要求，通常几句话即可，不另补流程小结。",
              "detailed": "用户要求深入或举例时可分步骤解释并给贴合当前问题的例子；不受固定700字建议约束，不重复整份上下文。",
              "adaptive": "简单问题简短回答；复杂问题按需要展开，不用固定字数凑满篇幅。"}[prefs["length"]]
    return "\n\n".join([
        "【稳定身份】你始终是" + ROLE + "使用简体中文，不随阶段更换人物、不每轮自我介绍。",
        mode_text + " 两种方式只改变解释深度，证据和明确采纳要求相同。",
        "【本轮请求优先】先回答用户具体问了什么，再用简单语言说明理由；项目信息足够时才联系当前课题，有推进需要才建议一个具体下一步。不是四个固定标题，也不是每轮都总结、追问和发卡片。用户的解释、简短或暂停要求优先于 discussion_step；阶段仅作讨论起点，不是必须完成的清单。",
        "【易懂而准确】关键领域术语或缩写首次出现，先用普通话说明它是什么、解决什么问题，再保留正式名称。结合本学科与当前问题，不把所有学科套成机器学习；例如基线是用来比较的已有方法，混杂因素是可能同时影响比较对象和结果、让归因出错的其他因素。必要时给贴近问题的小例子，比喻不替代准确含义，不确定则说明不确定，不编定义。不解释每个常用词、不每轮复印术语表。direct也不默认用户熟悉另一领域；明确要求专业或简短时才压缩。用户没听懂或不是本领域时，先重新解释当前难点，换具体例子，不只是换词复述、不追问专业问卷、不推进下一环节。通常一次处理一个主要问题，最多两个关键追问。",
        "【步骤目的】进入新的讨论环节或请用户完成任务时，自然说明做什么、为什么现在做、需要用户给什么或选什么、预计得到什么；不机械打印四个标题。同环节普通问答不重复流程说明，未知条件可留空，讨论不等于正式确认、证明创新或完成实验。",
        "【背景使用与冲突】只使用本次提供的 brief、records、history、papers 及材料索引。已有且仍有效的条件不要重问；新说法与已保存信息不同，具体指出两处原话并针对性核对。新信息可以作为本轮讨论假设，但不能声称已经改写正式记录。conversation.prior_project_history 只是移入对话的原项目历史参考，不是目标项目的条件或文献依据；不得自动并入目标事实。新对话只能使用实际进入上下文的内容，不说记得所有对话或完整读过项目资料。",
        "【依据的区别】按需要区分用户提供的条件、材料支持的事实、解释与建议、待验证创新点候选、已采纳正式记录，不要求每段加满标签。paper_inventory 只有目录，不能据标题推断方法、结果或贡献；摘要不等于全文，片段也不等于完整阅读。无可靠出处不编引文、页码或领域公认地位；不保证创新、发表或实验结果。",
        "【推进与停下】有效信息出现后，如本轮需要推进，可简短说明已有认识与待确认处，再提出一个可选下一步；允许继续讨论或缩小范围。只解释时不追加任务、草案、追问或推进按钮；暂停时简短确认并停止推进。建议不等于执行，讨论环节变化不等于正式确认或采纳。",
        "【长度】" + length,
        "【推荐回复】suggested_replies 是用户可点击发送的话，label短、content是实际用户表达，不复制助手questions。需要解释区别时提供可选explanation（最多160字符），说明将讨论什么、预计得到概念解释/比较清单/待确认条件/候选问题/方法草案，必要时说明适用情况，必须与content一致。explanation不是额外授权，不能写已执行、自动检索、保存、采纳或保证创新与发表；执行能力只由服务器允许的actions决定。与actions合计0—4项，按需提供，不凑数；只解释或暂停可没有选项，不替用户编造资源。",
        "【允许操作】actions 只能是 view_references（已有引用）、open_plan（当前正式方案）、open_settings、adopt_suggestion（当前会话有效 question/plan 草案）。新草案用 suggestion_index，已有用 allowed_suggestions 中的 suggestion_id，不同时填写；目标 ID 和可用性由服务端核对。不输出URL或接口，不宣称已执行检索、保存或采纳；检索仍需用户走明确入口确认。",
        "【输出契约】仅返回 GuideDraft：reply, cited_paper_ids, questions, suggestions, suggested_replies, actions。questions 是助手追问；suggestions 可为空，仅 note/question/plan，最多三条，不是正式记录。plan 仍须已确认问题、有效依据及明确生成请求。不得输出当前结构没有的 innovation_pattern 等字段。",
    ])


def fallback_replies(text, context):
    """Conservative user utterances, never fabricate resources or evidence."""
    stage = context.get("conversation", {}).get("entry_stage", "orientation")
    route=context.get("research_route", {})
    if context.get("plan_generation", {}).get("allowed"):
        stage = "design"
    if is_paused(text):
        return []
    if "两周" in text or "太难" in text:
        pairs = [
            ("讨论缩小范围", "先比较有哪些可以缩小研究范围的方式，不修改正式条件。"),
            ("明确取舍", "帮我区分必须保留的目标和可以暂缓的内容。"),
        ]
    elif context.get("conversation", {}).get("discussion_step") in ("exploration", "reading", "synthesis", "question"):
        # A user-selected task takes precedence over the conversation's old entry
        # label. In particular, don't send synthesis back to keyword discussion.
        pairs = {
            "exploration": [("比较切入方向", "请比较这些切入方向各自要回答什么、需要哪些依据，不替我确认正式问题。"),
                            ("缩小选题范围", "根据已知条件讨论更小的研究范围，未知资源保持待确认。")],
            "reading": [("比较已有论文", "请比较已提供摘要或片段中的研究对象、方法与局限，引用真实资料并说明阅读范围。"),
                        ("解释阅读中的概念", "先解释当前阅读中最影响理解的概念，再说明它与研究问题的关系。")],
            "synthesis": [("比较具体研究缺口", "请区分文献明确的局限、跨论文差异和待验证设想，不把缺少证据认定为创新。"),
                          ("收窄待研究的问题", "基于刚才的资料比较，讨论一个更具体、可用方法回答的问题，先不采纳。")],
            "question": [("比较候选问题", "比较候选问题的文献依据、范围和可行性，说明还需核对什么。"),
                         ("修改候选范围", "我想调整候选问题的范围，先讨论修改，不替我确认或保存。")],
        }[context["conversation"]["discussion_step"]]
    elif stage == "literature" or "现状" in text:
        pairs = [
            ("讨论检索关键词", "先一起明确需要检索的问题和关键词，不代表已经检索。"),
            ("核对证据缺口", "哪些判断已有材料支持，哪些还需要补充文献？"),
        ]
    elif stage == "design" or "可行" in text:
        pairs = [
            ("核对方案条件", "请区分方案中已知的条件、待确认的条件和主要局限。"),
            ("比较替代方法", "有没有更简单的方法可以回答同一个研究问题？"),
        ]
    elif route.get("step") == "collect_evidence":
        pairs=[("先说明阅读目标", "先说明准备比较哪些已有工作，以及这些资料将怎样帮助缩小问题。"),
               ("资源还不确定", "先按保守资源讨论可选择的研究路线，不把设备和时间当成已确认条件。")]
    elif route.get("step") == "read_and_gap":
        pairs=[("比较已有论文", "请基于已有摘要和正文片段，比较最接近工作的研究对象、方法条件与局限，引用实际资料。"),
               ("讨论研究缺口", "基于刚才的文献比较提出少量待验证的研究问题，说明证据、可行性与仍需核查之处，不直接认定创新。")]
    elif route.get("step") == "shape_method":
        names={"computational":"计算研究","theoretical":"理论研究","experimental":"实验研究设计"}
        method=names.get(route.get("suggested_method"))
        pairs=[("比较方法路线", "围绕已确认的问题，比较可用方法与所需条件；未知信息保持待确认。"),
               ("准备方案草案", "请为当前已确认问题生成"+method+"方案，未知条件保持待确认，先形成待审阅草案。") if method else
               ("讨论验证方式", "这个问题更适合计算比较、理论论证还是实验研究设计？请说明理由，由我选择。")]
    elif stage == "question":
        pairs = [
            ("缩小问题范围", "先帮我比较研究问题可以怎样缩小，不直接替我确认。"),
            ("核对已有工作", "这个问题与最接近的已有工作应比较哪些方面？"),
        ]
    else:
        pairs = [
            ("我还不确定", "我还不确定从哪里入手，请给我两个容易理解的入手方式。"),
            ("先解释这一步", "先解释当前这一步为什么值得做，再决定如何继续。"),
        ]
    recent = "\n".join(
        m.get("content", "")
        for m in context.get("history", [])
        if m.get("role") == "user"
    )
    options = [
        ReplyOption(label=label, content=content)
        for label, content in pairs
        if content != text and content not in recent
    ]
    if len(options) < 2:
        for label, content in [
            ("换个角度讨论", "我想换个角度讨论，请比较当前思路的另一种选择。"),
            ("总结待确认事项", "请只总结目前仍需我确认的关键事项，不替我作出选择。"),
        ]:
            if content != text and content not in recent:
                options.append(ReplyOption(label=label, content=content))
    return options[:2]


def prepare(draft, text, context, *, rejected_actions=None):
    """Validate selectors before any suggestion/reply is committed."""
    allowed = {s["suggestion_id"]: s for s in context.get("allowed_suggestions", [])}
    valid_actions=[]
    for a in draft.actions:
        try:
            _validate_action(a, draft, context, allowed)
            valid_actions.append(a)
        except WorkspaceError as exc:
            if rejected_actions is None:
                raise
            # This rejects the operation, not its safety check. Citations and
            # research proposals are independently validated BEFORE this path.
            # Never publish a reply claiming an operation we refused happened.
            if re.search(r"(?:已(?:经)?|成功)(?:明确)?(?:采纳|确认(?:研究)?问题|保存(?:了)?(?:方案|条件|记录)|启动(?:了)?(?:检索|实验))", draft.reply):
                raise WorkspaceError("操作没有通过校验，不能交付声称已经采纳、保存或执行的回复") from exc
            rejected_actions.append(a.type)
    draft=draft.model_copy(update={"actions":valid_actions})
    return _prepare_display(draft,text,context,allowed)


def _validate_action(a, draft, context, allowed):
    if a.type != "adopt_suggestion":
        if a.suggestion_id is not None or a.suggestion_index is not None:
            raise WorkspaceError("此操作不接受模型提供的资源编号")
        if a.type == "view_references" and not draft.cited_paper_ids:
            raise WorkspaceError("没有实际引用，不能提供查看引用操作")
        if a.type == "open_plan" and not context.get("current_plan"):
            raise WorkspaceError("当前没有正式方案，不能提供打开方案操作")
    else:
        if (a.suggestion_id is None) == (a.suggestion_index is None):
            raise WorkspaceError("采纳操作必须唯一对应具体草案")
        candidate = (
            allowed.get(a.suggestion_id)
            if a.suggestion_id
            else (
                draft.suggestions[a.suggestion_index]
                if a.suggestion_index < len(draft.suggestions)
                else None
            )
        )
        kind = (
            candidate.get("kind")
            if isinstance(candidate, dict)
            else getattr(candidate, "kind", None)
        )
        if kind not in ("question", "plan"):
            raise WorkspaceError("采纳操作只能关联当前会话中的有效问题或方案草案")


def _prepare_display(draft,text,context,allowed):
    prefs=turn_preferences(text)
    if (prefs['paused'] or prefs['explain_only']) and (draft.actions or draft.suggestions) and re.search(r"(?:已(?:经)?|成功)(?:明确)?(?:采纳|确认(?:研究)?问题|保存(?:了)?(?:方案|条件|记录)|启动(?:了)?(?:检索|实验))",draft.reply):
        raise WorkspaceError('本轮没有执行保存或采纳，不能交付声称已执行的回复')
    if is_paused(text):
        return draft.model_copy(
            update={
                "suggested_replies": [],
                "actions": [],
                "suggestions": [],
                "questions": [],
            }
        )
    if prefs['explain_only']:
        return draft.model_copy(update={'suggested_replies':[],'actions':[],'suggestions':[],'questions':[]})
    if re.search(r"(?:不要|不用|无需)(?:再|提供|给我)?(?:推荐|选项|按钮|追问)", text):
        return draft.model_copy(
            update={"suggested_replies": [], "actions": [], "questions": []}
        )
    replies, seen, labels = [], set(), set()
    recent_user = {
        m["content"].strip()
        for m in context.get("history", [])
        if m.get("role") == "user"
    }
    known_text = (
        text + "\n" + "\n".join(m['content'] for m in context.get('history',[]) if m['role']=='user' and not m.get('background_outdated')) + "\n" + str(context.get("brief", {}))
    )
    for r in draft.suggested_replies:
        if r.explanation and re.search(r"(?<!不)(?<!不会)(?<!并非)(?:自动|立即|已经|保证|确保).{0,8}(?:检索|保存|采纳|执行|发表|创新|实验成功)|点击(?:后|即|就)(?:会)?(?:保存|采纳|执行)", r.explanation):
            raise WorkspaceError("推荐回复说明不能承诺额外执行或科研结果；请说明本条讨论的预期")
        key = r.content.strip()
        if not key or not r.label.strip():
            raise WorkspaceError("推荐回复不能为空白")
        # Conservative guard, not a semantic fact checker: don't put an invented
        # first-person resource assertion into the user's mouth. Unknown facts
        # can instead be discussed as questions via the server fallback.
        claims = re.findall(
            r"(?:我|我们)(?:已经|已|只有|有|拥有|能访问|可以使用|的预算是|的设备是)[^。！？\n]{1,100}",
            key,
        )
        if any(
            re.search(
                r"GPU|CPU|显卡|设备|预算|经费|数据|授权|权限|经验|\d|两周|小时|个月",
                claim,
                re.I,
            )
            and claim not in known_text
            for claim in claims
        ):
            continue
        if (
            key not in seen
            and key != text
            and key not in recent_user
            and r.label.strip() not in labels
        ):
            seen.add(key)
            labels.add(r.label.strip())
            replies.append(r)
    actions = list(
        {
            (a.type, a.suggestion_id, a.suggestion_index): a for a in draft.actions
        }.values()
    )
    if re.search(r"(?:就|直接)?(?:采用|采纳)(?:这个|该|此)?方案", text):
        plans = [s for s in allowed.values() if s["kind"] == "plan"]
        named = [s for s in plans if s["title"] in text]
        if context.get("eligible_plan_count", len(plans)) > 1 and len(named) != 1:
            actions = [a for a in actions if a.type != "adopt_suggestion"]
            draft = draft.model_copy(
                update={
                    "reply": draft.reply
                    + "\n\n当前存在多个方案草案，请先明确要采纳哪一条；尚未采纳。"
                }
            )
    # Only a unique eligible plan is offered for an explicit adoption request.
    if re.search(r"(?:就|直接)?(?:采用|采纳)(?:这个|该|此)?方案", text) and not actions:
        plans = [s for s in allowed.values() if s["kind"] == "plan"]
        if context.get("eligible_plan_count", len(plans)) == 1 and len(plans) == 1:
            actions.append(
                ActionProposal(
                    type="adopt_suggestion", suggestion_id=plans[0]["suggestion_id"]
                )
            )
    # Validate EVERY proposed action above, including ones not displayed. Never
    # truncate unvalidated selectors/citations to conceal a safety failure.
    replies = replies[: 4 - len(actions)]
    # Offer conservative choices when there is a real discussion/clarification
    # need, not as a mandatory footer to every explanation.
    needs_choices=bool(draft.questions or draft.suggested_replies or context.get('plan_generation',{}).get('allowed')
                       or re.search(r'下一步|不确定|从哪|开始|继续|选题|研究现状|大家在做什么|可行|采用|采纳|太难|缩小',text))
    if needs_choices and len(replies) + len(actions) < 2:
        for option in fallback_replies(text, context):
            if option.content not in {
                r.content for r in replies
            } and option.label not in {r.label for r in replies}:
                replies.append(option)
            if len(replies) + len(actions) >= 2:
                break
    from core.invocation_audit import span

    normalized = (
        replies != draft.suggested_replies
        or actions != draft.actions
        or len(draft.questions) > 2
    )
    with span(
        "phase",
        "guidance",
        "display_normalization" if normalized else "display_validated",
    ):
        pass
    return draft.model_copy(
        update={
            "suggested_replies": replies,
            "actions": actions,
            "questions": [] if prefs['no_questions'] else draft.questions[:2],
        }
    )

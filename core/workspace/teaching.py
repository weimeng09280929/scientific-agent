"""Saved case progress; existing chat, workspace and atomic adoption are authoritative.

No model, search or HTTP clients are imported here. Progress and step receipts live
in the existing project kv_store; formal questions/plans use existing WorkItems.
"""

import json
import re
from uuid import uuid4, uuid5, NAMESPACE_URL

from pydantic import Field

from core.knowledge.schema import Paper
from core.workspace.conversations import (
    ConversationService,
    ConversationNotFound,
    encode,
)
from core.workspace.materials import fingerprint
from core.workspace.schema import (
    Contract,
    Workspace,
    Message,
    Suggestion,
    ItemInput,
    Brief,
    now,
)
from core.workspace.service import KEY, RevisionConflict, WorkspaceError
from core.workspace.start import ConversationStarter, StartConversation
from core.workspace.teaching_packages import load_package, plan_for, NOTICE
from core.workspace.plans import render_plan, validate_plan, export_plan_content

CASE_KEY = "teaching.case.v1"


class CaseStart(Contract):
    request_id: str = Field(min_length=1, max_length=100)
    case_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,70}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")


class CaseChoice(Contract):
    request_id: str = Field(min_length=1, max_length=128)
    expected_version: int = Field(ge=0)
    option_id: str = Field(min_length=1, max_length=100)


class CaseFork(Contract):
    request_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    resources: str = Field(default="", max_length=2000)
    time_budget: str = Field(default="", max_length=1000)


def kv(conn, key):
    row = conn.execute("SELECT value FROM kv_store WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def put(conn, key, value):
    conn.execute(
        "INSERT OR REPLACE INTO kv_store(key,value,updated_at) VALUES (?,?,?)",
        (key, encode(value), now()),
    )


def teaching_identity(chat):
    with chat._connection() as conn:
        return kv(conn, CASE_KEY)


def guard_teaching(chat):
    if teaching_identity(chat):
        raise WorkspaceError(
            "教学项目只使用预置内容；请通过‘带着这个思路开始自己的研究’进入普通项目，再明确授权检索或生成。"
        )


def export_header(chat, *, compact=False):
    state = teaching_identity(chat)
    if not state:
        return ""
    _, p = TeachingService(chat).bound(state["conversation_id"])
    return (
        "\n\n".join(
            [
                NOTICE,
                f"案例 {p['title']} · 版本 {p['version']} · 整理 {p['organized_on']}",
                p["settings"],
                *([] if compact else [
                    "待讨论：" + "；".join(p["unconfirmed"]),
                    "来源：" + "；".join(s["title"] + " " + s["url"] for s in p["sources"]),
                ]),
            ]
        )
        + "\n\n"
    )


def start_case(starter: ConversationStarter, request: CaseStart):
    p = load_package(request.case_id, request.version)
    result = starter.start(
        StartConversation(
            request_id="teaching:" + request.request_id,
            title="教学案例 · " + p["title"],
            domain=p["domain"],
            conversation_title="案例体验 · " + p["title"],
            guidance_mode="case",
            discussion_goal=f"教学包 {p['case_id']} {p['version']} {fingerprint(p)}",
        )
    )
    chat = ConversationService(
        result["project_id"], starter.paths.project_db(result["project_id"])
    )
    with chat._connection(True) as conn:
        state = kv(conn, CASE_KEY)
        if state is None:
            cid = result["conversation_id"]
            paper_map = {}
            for s in p["sources"]:
                pid = uuid5(
                    NAMESPACE_URL, result["project_id"] + ":" + s["source_id"]
                ).hex
                paper_map[s["source_id"]] = pid
                paper = Paper(
                    paper_id=pid,
                    title=s["title"],
                    authors=s["authors"],
                    year=s["year"],
                    doi=s.get("doi"),
                    url=s["url"],
                    abstract="【教学资料·自行编写概述】" + s["summary"],
                    metadata={
                        "teaching": True,
                        "case_id": p["case_id"],
                        "case_version": p["version"],
                        "source_id": s["source_id"],
                        "reading_scope": s["reading_scope"],
                        "verified_on": s["verified_on"],
                        "facts": s["facts"],
                        "redistribution": s["redistribution"],
                        "relevance": p.get("experience", {})
                        .get("source_relations", {})
                        .get(s["source_id"], ""),
                    },
                    source_stage="research",
                )
                conn.execute(
                    "INSERT INTO papers(paper_id,content,created_at) VALUES (?,?,?)",
                    (pid, paper.model_dump_json(), now()),
                )
            state = {
                "case_id": p["case_id"],
                "version": p["version"],
                "fingerprint": fingerprint(p),
                "conversation_id": cid,
                "step": 0,
                "state_version": 0,
                "choices": {},
                "question_suggestion": None,
                "plan_suggestion": None,
                "question_item": None,
                "plan_item": None,
                "completed": False,
                "paper_map": paper_map,
            }
            conn.execute(
                "UPDATE sra_conversations SET case_id=? WHERE conversation_id=?",
                (p["case_id"], cid),
            )
            put(
                conn,
                KEY,
                Workspace(project_id=result["project_id"]).model_dump(mode="json"),
            )
            put(conn, CASE_KEY, state)
            TeachingService(chat)._message(
                conn,
                cid,
                (
                    (p["experience"]["scene"] + "\n\n" + p["settings"])
                    if p.get("experience")
                    else NOTICE
                    + "\n\n"
                    + p["settings"]
                    + "\n\n"
                    + p["steps"][0]["text"]
                ),
                "assistant",
                "teaching-opening",
            )
        elif state["fingerprint"] != fingerprint(p):
            raise RevisionConflict("体验绑定的案例版本与请求不一致")
    return {**result, "teaching": TeachingService(chat).view(result["conversation_id"])}


class TeachingService:
    def __init__(self, chat):
        self.chat = chat

    def bound(self, cid):
        self.chat.get(cid)
        state = teaching_identity(self.chat)
        if not state:
            raise ConversationNotFound("该项目不是教学案例")
        if cid != state["conversation_id"]:
            raise ConversationNotFound("教学体验不属于当前会话")
        p = load_package(state["case_id"], state["version"])
        if fingerprint(p) != state["fingerprint"]:
            raise WorkspaceError("案例绑定版本完整性校验失败，未改变学习进度")
        return state, p

    def view(self, cid):
        state, p = self.bound(cid)
        step = p["steps"][state["step"]]
        stage = (0, 1, 2, 2, 3, 3, 3)[state["step"]]
        suggestions = {s["suggestion_id"]: s for s in self.chat.suggestions(cid)}
        options = []
        for o in step["options"]:
            enabled, reason = True, ""
            target = (
                state.get(
                    "question_suggestion"
                    if o["id"] == "adopt_question"
                    else "plan_suggestion"
                )
                if o["id"] in ("adopt_question", "adopt_plan")
                else None
            )
            if o["id"] in ("adopt_question", "adopt_plan"):
                draft = suggestions.get(target)
                if not draft or (
                    not draft.get("applied_item_id")
                    and draft.get("basis_changed") is not False
                ):
                    enabled, reason = (
                        False,
                        "草案依据已改变或缺少依据；请返回核对并重新选择",
                    )
                elif draft.get("applied_item_id") and o["id"] == "adopt_plan":
                    enabled, reason = False, "已采纳，未执行实验"
            options.append(
                {
                    "id": o["id"],
                    "label": o["label"],
                    "enabled": enabled,
                    "reason": reason,
                    "kind": "discussion" if o.get("explanation") else "operation",
                }
            )
        if state["step"] == 2 and state["choices"].get("interest"):
            preferred = state["choices"]["interest"]
            options.sort(key=lambda o: o["id"] != preferred)
        experience = p.get("experience", {})
        return {
            **state,
            "notice": NOTICE,
            "title": p["title"],
            "organized_on": p["organized_on"],
            "step_title": step["title"],
            "why": step["why"],
            "text": step["text"],
            "options": options,
            "stage_index": stage,
            "stage_title": (
                "明确条件",
                "了解研究现状",
                "确认研究问题",
                "形成并审阅方案",
            )[stage],
            "stage_why": step["why"],
            "experience": experience,
            "question_candidates": [
                {
                    "key": key,
                    **value,
                    "help": experience.get("question_help", {}).get(key, ""),
                    "recommended": key == state["choices"].get("interest"),
                }
                for key, value in p["questions"].items()
            ],
            "sources": [
                {**s, "paper_id": state["paper_map"][s["source_id"]]}
                for s in p["sources"]
            ],
            "concepts": p["concepts"],
            "unconfirmed": p["unconfirmed"],
            "settings": p["settings"],
            "transfer_preview": "带入公开来源与教学参考草案；不带入教学条件，不自动确认问题或方案。",
            "reference_preview": {
                "question": p["questions"].get(state["choices"].get("question")),
                "plan": (
                    {
                        k: suggestions[state["plan_suggestion"]].get(k)
                        for k in ("title", "content", "basis_changed")
                    }
                    if state.get("plan_suggestion") in suggestions
                    else None
                ),
            },
        }

    def _message(self, conn, cid, text, role, rid, *, refs=None, suggestions=None):
        m = Message(
            role=role,
            content=text,
            area="study",
            mode="user" if role == "user" else "rules",
            content_source="user" if role == "user" else "teaching_case",
            cited_paper_ids=refs or [],
            suggestion_ids=suggestions or [],
            request_status="completed",
        )
        self.chat._insert_message(conn, cid, m.model_dump(mode="json"), rid)
        self.chat._touch(conn, cid)

    def _suggest(self, conn, state, p, kind, text, title, rid, plan=None):
        basis = self.chat.material_state()
        old = state.get("question_item" if kind == "question" else "plan_item")
        w = self.chat._workspace(conn)
        if old and not any(i.item_id == old and i.status == "active" for i in w.items):
            raise RevisionConflict("教学记录已被其他操作修订，请先核对正式材料")
        suggestion = Suggestion(
            kind=kind,
            title="【教学】" + title,
            content=text,
            plan=plan,
            supersedes_id=old,
            paper_ids=list(state["paper_map"].values()),
            related_ids=[state["question_item"]] if kind == "plan" else [],
            based_on_revision=basis["revision"],
            material_fingerprint=basis["fingerprint"],
            conversation_id=state["conversation_id"],
        )
        self.chat._insert_suggestion(
            conn,
            state["conversation_id"],
            suggestion.model_dump(mode="json"),
            rid,
            basis["fingerprint"],
        )
        state[kind + "_suggestion"] = suggestion.suggestion_id
        return suggestion

    def choose(self, cid, request: CaseChoice):
        _, p = self.bound(cid)
        receipt = "teaching.receipt:" + fingerprint(request.request_id)
        signature = fingerprint(request.model_dump(exclude={"request_id"}))
        with self.chat._connection(True) as conn:
            prior = kv(conn, receipt)
            if prior:
                if prior["signature"] != signature:
                    raise RevisionConflict("request_id 已用于不同教学选择")
                return {**prior["result"], "replayed": True}
            if self.chat._request(conn, cid, request.request_id):
                raise RevisionConflict("request_id 已用于对话消息，不能复用于教学选择")
            state = kv(conn, CASE_KEY)
            if state["state_version"] != request.expected_version:
                raise RevisionConflict("学习进度已有变化，请刷新后重新选择；未重复推进")
            option = next(
                (
                    o
                    for o in p["steps"][state["step"]]["options"]
                    if o["id"] == request.option_id
                ),
                None,
            )
            if option is None:
                raise WorkspaceError("当前步骤没有该选项，不能跳过确认")
            response, suggestion_ids = option.get("explanation", ""), []
            if "interest_key" in option:
                state["choices"]["interest"] = option["interest_key"]
                response = (
                    "你更关注“"
                    + p["questions"][option["interest_key"]]["title"]
                    + "”。先看案例资料，再决定正式问题。"
                )
            elif "question_key" in option:
                q = p["questions"][option["question_key"]]
                state["choices"]["question"] = option["question_key"]
                state["completed"] = False
                draft = self._suggest(
                    conn,
                    state,
                    p,
                    "question",
                    NOTICE + "\n\n" + q["content"],
                    q["title"],
                    request.request_id,
                )
                response, suggestion_ids = draft.content, [draft.suggestion_id]
            elif option["id"] in ("adopt_question", "adopt_plan"):
                kind = "question" if option["id"] == "adopt_question" else "plan"
                sid = state[kind + "_suggestion"]
                if not sid:
                    raise WorkspaceError("请先查看对应草案")
                result = self.chat.apply(cid, sid, _transaction=conn)
                state[kind + "_item"] = result["item"]["item_id"]
                state["completed"] = kind == "plan"
                response = (
                    "已明确采纳教学"
                    + ("问题" if kind == "question" else "方案；教学体验完成")
                    + "。未执行实验，也未验证科学结论。"
                )
                if p.get("experience"):
                    response = (
                        "已保存研究问题："
                        + p["questions"][state["choices"]["question"]]["content"]
                        if kind == "question"
                        else "已保存当前教学方案。你现在有了已确认的问题、教学方案和三份来源说明，可从顶部‘下载当前产物’导出。请与导师继续讨论真实资源、证据缺项及可行性；没有执行实验，也没有验证结论。你也可以带着这些参考开始自己的研究。"
                    )
            elif "scope_key" in option:
                if not state["question_item"]:
                    raise WorkspaceError("请先明确采用教学问题")
                previous_scope = state["choices"].get("scope")
                state["choices"]["scope"] = option["scope_key"]
                state["completed"] = False
                w = self.chat._workspace(conn)
                if previous_scope != option["scope_key"]:
                    # An unchanged selection is not a material revision.
                    self.chat.shared._append_item(
                        w,
                        ItemInput(
                            kind="note",
                            title="【教学设定】范围选择",
                            content=p["scopes"][option["scope_key"]]
                            + "；不是用户真实条件。",
                        ),
                    )
                    for i in w.items:
                        if i.kind == "plan" and i.status == "active":
                            i.needs_review = True
                    w.revision += 1
                    put(conn, KEY, w.model_dump(mode="json"))
                response = (
                    "已记录教学范围："
                    + option["label"]
                    + "。原方案保留，须重新审阅；真实条件仍待确认。"
                )
                if p.get("experience"):
                    response = (
                        "范围保持不变："
                        if previous_scope == option["scope_key"]
                        else "本次修改：" if previous_scope else "本次教学范围："
                    ) + p["experience"]["scope_changes"][option["scope_key"]]
                    if previous_scope and previous_scope != option["scope_key"]:
                        response += " 原草案保留且依据已变化；已保存的旧方案需复核。接下来整理新草案，比较后再采纳。"
            elif option["id"] == "show_plan":
                basis = self.chat.material_state()
                w = basis["workspace"]
                qid = state["question_item"]
                question = next(
                    (
                        i
                        for i in w.items
                        if i.item_id == qid
                        and i.status == "active"
                        and not i.needs_review
                    ),
                    None,
                )
                if question is None or w.focus.question != qid:
                    raise RevisionConflict("教学问题已变化或待复核，请返回确认问题")
                plan = plan_for(
                    p,
                    state["choices"]["question"],
                    state["choices"]["scope"],
                    qid,
                    state["paper_map"],
                )
                context = {
                    "plan_generation": {
                        "allowed": True,
                        "method": p["method"],
                        "question": question.model_dump(mode="json"),
                        "supersedes_id": state["plan_item"],
                    },
                    "papers": [
                        x.model_dump(mode="json")
                        for x in self.chat.shared.store.list_papers()
                    ],
                    "brief": w.brief.model_dump(),
                }
                validate_plan(plan, context, "展示预置教学方案")
                body = NOTICE + "\n\n" + p["settings"] + "\n\n" + render_plan(plan)
                draft = self._suggest(
                    conn,
                    state,
                    p,
                    "plan",
                    body,
                    p["title"] + " · " + p["scopes"][state["choices"]["scope"]],
                    request.request_id,
                    plan,
                )
                response, suggestion_ids = body, [draft.suggestion_id]
                if p.get("experience"):
                    variant = p["variants"][
                        state["choices"]["question"] + ":" + state["choices"]["scope"]
                    ]
                    response = "方案草案已准备好，尚未采纳。\n\n" + "\n\n".join(
                        [
                            "**要回答什么：** " + variant["objective"],
                            "**准备怎么研究：** " + variant["method"],
                            "**用什么证据判断：** " + variant["validation"],
                            "**需要哪些条件：** "
                            + p["scopes"][state["choices"]["scope"]]
                            + "；真实设备、时间和权限待确认。",
                            "**还有什么不确定：** " + "；".join(p["unconfirmed"]),
                        ]
                    )
            if "next" in option:
                state["step"] = option["next"]
            state["state_version"] += 1
            put(conn, CASE_KEY, state)
            self._message(conn, cid, option["label"], "user", request.request_id)
            step = p["steps"][state["step"]]
            self._message(
                conn,
                cid,
                (
                    (
                        (response + "\n\n" if response else "")
                        + ("" if state["completed"] else step["text"])
                    )
                    if p.get("experience")
                    else (response + "\n\n" if response else "")
                    + f"第 {state['step'] + 1} 步：{step['title']}\n\n{step['text']}"
                ),
                "assistant",
                request.request_id,
                refs=list(state["paper_map"].values()) if state["step"] == 1 else [],
                suggestions=suggestion_ids,
            )
            result = {
                "state_version": state["state_version"],
                "step": state["step"],
                "completed": state["completed"],
            }
            put(conn, receipt, {"signature": signature, "result": result})
        return result

    def free(self, cid, request):
        state, package = self.bound(cid)
        answer = "已保留你的问题。标准教学路径未覆盖这个自由问题，未调用模型或检索。请返回当前任务，或选择‘带着这个思路开始自己的研究’。"
        faq = package.get("experience", {}).get("faq", {})
        # Deliberately bounded explanations, not a general offline QA engine.
        if re.search(
            r"^(?:先)?(?:暂停|结束|停止|不继续|不用了)[吧。！!\s]*$", request.content
        ):
            answer = "好的，先停在这里。进度已保存；回来后可以继续当前任务，不会自动调用模型或检索。"
        elif not re.search(r"实时|联网|最新|帮我.{0,5}检索", request.content):
            for key, pattern in (
                ("why", r"为什么.{0,8}(?:选|问题)|为何.{0,8}问题"),
                (
                    "terms",
                    r"^(?:这个)?(?:术语|概念)(?:是|指)?什么(?:意思)?[？?。\s]*$|(?:基线|non.?iid|联邦学习|界面|体相|电解质).{0,6}(?:是什么|什么意思)|解释.{0,8}(?:基线|non.?iid|联邦|界面|体相)",
                ),
                ("narrow", r"范围.{0,10}(?:小|窄)|缩小|太难|两周"),
                ("evidence", r"(?:论文|资料|文献).{0,10}(?:说明|证明|支持|结论)"),
                ("conditions", r"(?:方案|研究).{0,8}(?:条件|资源|设备)|需要什么"),
            ):
                if key in faq and re.search(pattern, request.content, re.I):
                    answer = "案例讲解：" + faq[key]
                    if key == "why" and state["choices"].get("question"):
                        answer += (
                            " 当前讨论的问题是：“"
                            + package["questions"][state["choices"]["question"]][
                                "content"
                            ]
                            + "”"
                        )
                    break
        if request.selected_paper_ids:
            ids = {p.paper_id for p in self.chat.shared.store.list_papers()}
            if not set(request.selected_paper_ids) <= ids:
                raise WorkspaceError("引用论文不属于当前教学项目")
        if request.selected_item_id and not any(
            i.item_id == request.selected_item_id
            for i in self.chat.shared.load(include_chat=False).items
        ):
            raise WorkspaceError("记录不属于当前教学项目")
        signature = fingerprint(
            request.model_dump(exclude={"request_id", "expected_revision"})
        )
        basis = self.chat.material_state()
        with self.chat._connection(True) as conn:
            if kv(conn, "teaching.receipt:" + fingerprint(request.request_id)):
                raise RevisionConflict("request_id 已用于教学选择，不能复用于不同消息")
            prior = self.chat._request(conn, cid, request.request_id)
            if prior:
                if prior["payload_hash"] != signature:
                    raise RevisionConflict("request_id 已用于不同消息")
            else:
                conn.execute(
                    "INSERT INTO sra_chat_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        cid,
                        request.request_id,
                        self.chat.project_id,
                        encode(
                            request.model_dump(
                                exclude={"request_id", "expected_revision"}
                            )
                        ),
                        signature,
                        "completed",
                        None,
                        None,
                        1,
                        now(),
                        now(),
                        None,
                        None,
                        basis["fingerprint"],
                        basis["revision"],
                    ),
                )
                self._message(conn, cid, request.content, "user", request.request_id)
                self._message(
                    conn,
                    cid,
                    answer,
                    "assistant",
                    request.request_id,
                )
        return self.chat.request_view(cid, request.request_id)

    def export(self, cid):
        state, p = self.bound(cid)
        w = self.chat.shared.load(include_chat=False)
        lines = [
            "# " + p["title"],
            NOTICE,
            f"案例 {p['title']} · 版本 {p['version']} · 整理日期 {p['organized_on']}",
            p["settings"],
            "## 已明确采纳的教学记录",
        ]
        labels = {state["paper_map"][s["source_id"]]: str(n) for n, s in enumerate(p["sources"], 1)}
        for i in w.items:
            if i.kind in ("question", "plan"):
                lines += [
                    f"### {i.title} · 版本 {i.plan_version or '问题记录'} · {'当前记录' if i.status == 'active' else '历史记录'} · {'待复核' if i.needs_review else '未发现资料变更'}",
                    export_plan_content(i, labels),
                ]
        if not any(i.kind in ("question", "plan") for i in w.items):
            lines += ["尚未明确采纳问题或方案；不会把预置分支当作已完成产物。"]
        lines += ["## 来源与阅读范围"]
        for n, s in enumerate(p["sources"], 1):
            lines += [
                f"- [{n}] {s['title']} ({s['year']})；{'、'.join(s['authors'])}；{s['url']}；DOI {s.get('doi') or '未分配/未使用'}；核验 {s['verified_on']}；阅读范围 {s['reading_scope']}",
                s["summary"],
                "出处：" + "；".join(f["location"] for f in s["facts"]),
            ]
        lines += [
            "## 未验证与待讨论事项",
            *["- " + x for x in p["unconfirmed"]],
            "教学体验完成不等于研究、实验或论文完成。",
        ]
        return "\n\n".join(lines)


def fork_case(starter, chat, cid, request):
    state, p = TeachingService(chat).bound(cid)
    if not request.title.strip():
        raise WorkspaceError("请填写自己的研究主题")
    result = starter.start(
        StartConversation(
            request_id="case-fork:"
            + fingerprint({"pid": chat.project_id, "rid": request.request_id}),
            title=request.title,
            domain=p["domain"],
            discussion_goal="教学参考（不是正式主线）："
            + p["case_id"]
            + " "
            + p["version"]
            + "；用户条件指纹 "
            + fingerprint(request.model_dump(exclude={"request_id"})),
        )
    )
    dest = ConversationService(
        result["project_id"], starter.paths.project_db(result["project_id"])
    )
    with dest._connection(True) as conn:
        if not kv(conn, "teaching.reference.v1"):
            marker = {
                "case_id": p["case_id"],
                "version": p["version"],
                "notice": "教学参考，不是用户已确认的问题或方案",
                "source_titles": [s["title"] for s in p["sources"]],
                "user_conditions": {"resources": request.resources, "time_budget": request.time_budget},
            }
            transferred_ids = {}
            for source in p["sources"]:
                paper = chat.shared.store.get_paper(
                    state["paper_map"][source["source_id"]]
                ).model_copy(update={"paper_id": uuid4().hex})
                transferred_ids[state["paper_map"][source["source_id"]]] = (
                    paper.paper_id
                )
                conn.execute(
                    "INSERT INTO papers(paper_id,content,created_at) VALUES (?,?,?)",
                    (paper.paper_id, paper.model_dump_json(), now()),
                )
            w = Workspace(
                project_id=result["project_id"],
                revision=1,
                brief=Brief(
                    goal=request.title,
                    resources=request.resources,
                    time_budget=request.time_budget,
                ),
            )
            reference_text = (
                "【教学参考·未采纳】\n\n"
                + NOTICE
                + "\n\n案例 "
                + p["case_id"]
                + " "
                + p["version"]
                + "\n\n教学设定不作为你的真实条件；没有复制案例项目的正式主线或采纳状态。\n\n"
            )
            if state["choices"].get("question"):
                reference_text += (
                    "参考问题（未确认）："
                    + p["questions"][state["choices"]["question"]]["content"]
                    + "\n\n"
                )
            reference_text += "来源：\n" + "\n".join(
                s["title"] + " " + s["url"] + "；阅读范围 " + s["reading_scope"]
                for s in p["sources"]
            )
            # Reference note only: no confirmed question/plan/focus, no teaching conditions in brief.
            dest.shared._append_item(
                w,
                ItemInput(
                    kind="note",
                    title="教学来源与参考问题（不是正式主线）",
                    content=reference_text,
                    paper_ids=list(transferred_ids.values()),
                ),
                _transaction=conn,
            )
            draft = next(
                (
                    s
                    for s in chat.suggestions(cid)
                    if s["suggestion_id"] == state.get("plan_suggestion")
                ),
                None,
            )
            if draft:
                body = (
                    "【教学参考·未采纳到本项目】\n\n"
                    + f"案例 {p['case_id']} {p['version']}；来源依据待复核 {draft.get('basis_changed', True)}。所有条件仅为教学设定。\n\n"
                    + draft["content"]
                )
                for old, new in transferred_ids.items():
                    body = body.replace(old, new)
                dest.shared._append_item(
                    w,
                    ItemInput(
                        kind="note",
                        title="教学方案参考（尚未确认，不是正式方案）",
                        content=body,
                        paper_ids=list(transferred_ids.values()),
                    ),
                    _transaction=conn,
                )
            put(conn, KEY, w.model_dump(mode="json"))
            put(conn, "teaching.reference.v1", marker)
    return result

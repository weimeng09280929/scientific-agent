"""Local, immutable teaching content. No network or model dependencies."""

import json
import re
from pathlib import Path

from core.workspace.materials import fingerprint
from core.workspace.plan_schema import ResearchPlan
from core.workspace.service import WorkspaceError

ROOT = Path(__file__).resolve().parents[2] / "content" / "teaching-cases"
NOTICE = "教学案例，使用预先整理的资料；不是实时检索或实验结果。"
STEP_KEYS = [
    "orientation",
    "sources",
    "question",
    "confirm",
    "constraint",
    "draft",
    "review",
]
SECTION_KEYS = [
    "objective",
    "related_work",
    "hypothesis",
    "conditions",
    "method",
    "validation",
    "feasibility",
    "limitations",
    "references",
]


def plan_for(package, question_key, scope_key, question_id, paper_map):
    """Bind authored branch content to THIS project's real material IDs."""
    v = package["variants"][question_key + ":" + scope_key]
    refs = list(paper_map.values())
    sections = []
    content = {
        "objective": v["objective"],
        "hypothesis": "拟比较而非预设获胜；尚无结果，不保证问题创新性。",
        "conditions": package["settings"] + " 本分支：" + package["scopes"][scope_key],
        "method": v["method"],
        "validation": v["validation"],
        "feasibility": "先与导师核对权限、设备、期限和范围；只规划，不执行。",
        "limitations": "；".join(package["unconfirmed"]),
        "references": "依据仅限下列已核验阅读范围，不能补造全文结论。",
    }
    for key in SECTION_KEYS:
        statements = (
            [
                {
                    "text": s["summary"],
                    "basis": "literature",
                    "paper_ids": [paper_map[s["source_id"]]],
                    "quote": s["summary"],
                }
                for s in package["sources"]
            ]
            if key == "related_work"
            else [
                {
                    "text": content[key],
                    "basis": (
                        "proposal"
                        if key not in ("limitations", "feasibility")
                        else "unverified"
                    ),
                }
            ]
        )
        sections.append({"key": key, "statements": statements})
    return ResearchPlan.model_validate(
        {
            "method": package["method"],
            "question_id": question_id,
            "sections": sections,
            "conditions": {
                key: {"value": "待确认", "basis": "unverified"}
                for key in ("resources", "time_budget", "budget", "data_access")
            },
            "method_details": v["details"],
            "references": refs,
            "unconfirmed": package["unconfirmed"],
        }
    )


def validate_package(p):
    required = {
        "case_id",
        "version",
        "title",
        "domain",
        "method",
        "organized_on",
        "audience",
        "learning_goals",
        "settings",
        "concepts",
        "sources",
        "steps",
        "questions",
        "scopes",
        "variants",
        "unconfirmed",
        "export_notice",
    }
    if (
        set(p) - {"experience"} != required
        or not re.fullmatch(r"[a-z][a-z0-9-]{1,70}", p["case_id"])
        or not re.fullmatch(r"\d+\.\d+\.\d+", p["version"])
    ):
        raise ValueError("案例字段或标识无效")
    if "experience" in p:
        e = p["experience"]
        if set(e) != {
            "scene",
            "landscape",
            "question_help",
            "source_relations",
            "faq",
            "scope_changes",
        }:
            raise ValueError("教学表达字段无效")
        if (
            not e["scene"]
            or not e["landscape"]
            or set(e["question_help"]) != set(p["questions"])
            or set(e["source_relations"]) != {s["source_id"] for s in p["sources"]}
        ):
            raise ValueError("教学解释与材料分支不一致")
        if set(e["faq"]) != {
            "why",
            "terms",
            "narrow",
            "evidence",
            "conditions",
        } or not all(isinstance(x, str) and x.strip() for x in e["faq"].values()):
            raise ValueError("常见解释缺失")
        if set(e["scope_changes"]) != set(p["scopes"]):
            raise ValueError("范围修改解释缺失")
    if p["domain"] not in ("ai", "materials") or p["method"] not in (
        "computational",
        "experimental",
    ):
        raise ValueError("当前教学包领域或方法未接入")
    if not 3 <= len(p["sources"]) <= 5 or len(
        {s["source_id"] for s in p["sources"]}
    ) != len(p["sources"]):
        raise ValueError("来源数量或标识无效")
    for s in p["sources"]:
        for key in (
            "source_id",
            "title",
            "authors",
            "year",
            "url",
            "verified_on",
            "reading_scope",
            "summary",
            "facts",
            "redistribution",
            "verification_urls",
        ):
            if not s.get(key):
                raise ValueError("来源缺少核验信息：" + key)
        if not s["url"].startswith("https://") or not all(
            f.get("claim") and f.get("location") for f in s["facts"]
        ):
            raise ValueError("来源链接或事实位置无效")
    if (
        [s["key"] for s in p["steps"]] != STEP_KEYS
        or len(p["questions"]) < 2
        or len(p["scopes"]) < 2
    ):
        raise ValueError("教学步骤或有效分支不足")
    expected = {q + ":" + s for q in p["questions"] for s in p["scopes"]}
    if set(p["variants"]) != expected or len(
        {json.dumps(v, sort_keys=True) for v in p["variants"].values()}
    ) != len(expected):
        raise ValueError("分支方案缺失或重复")
    transitions = {
        0: {"begin": 1},
        1: {"continue": 2},
        2: {},
        3: {"adopt_question": 4, "back": 2},
        4: {},
        5: {"show_plan": 6, "scope_back": 4},
        6: {"adopt_plan": None, "scope_back": 4, "back": 2},
    }
    for index, step in enumerate(p["steps"]):
        if not all(step.get(k) for k in ("title", "why", "text", "options")) or len(
            {o["id"] for o in step["options"]}
        ) != len(step["options"]):
            raise ValueError("步骤内容缺失或选项重复")
        for o in step["options"]:
            if not o.get("label") or ("next" in o and not 0 <= o["next"] < 7):
                raise ValueError("分支转移无效")
            if (
                "question_key" in o
                and o["question_key"] not in p["questions"]
                or "scope_key" in o
                and o["scope_key"] not in p["scopes"]
            ):
                raise ValueError("分支引用无效")
            if "interest_key" in o:
                valid = (
                    index == 0
                    and o["interest_key"] in p["questions"]
                    and o.get("next") == 1
                    and not ({"question_key", "scope_key"} & set(o))
                )
            elif "question_key" in o:
                valid = index == 2 and o.get("next") == 3 and "scope_key" not in o
            elif "scope_key" in o:
                valid = index == 4 and o.get("next") == 5
            elif o["id"] in transitions[index]:
                valid = o.get("next") == transitions[index][o["id"]]
            else:
                valid = bool(o.get("explanation")) and "next" not in o
            if not valid:
                raise ValueError("教学转移不得绕过明确确认或执行未知操作")
        if not set(transitions[index]) <= {o["id"] for o in step["options"]}:
            raise ValueError("教学步骤缺少必要的确认或返回入口")
    if {o.get("question_key") for o in p["steps"][2]["options"]} - {None} != set(
        p["questions"]
    ):
        raise ValueError("存在不可选择的教学问题")
    if {o.get("scope_key") for o in p["steps"][4]["options"]} - {None} != set(
        p["scopes"]
    ):
        raise ValueError("存在不可选择的教学范围")
    for q in p["questions"]:
        for s in p["scopes"]:
            plan_for(
                p,
                q,
                s,
                "validation-question",
                {x["source_id"]: x["source_id"] for x in p["sources"]},
            )
    return p


def load_package(case_id, version):
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,70}", case_id) or not re.fullmatch(
        r"\d+\.\d+\.\d+", version
    ):
        raise WorkspaceError("案例标识或版本无效")
    try:
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        expected = manifest[case_id + "/" + version]
        p = validate_package(
            json.loads(
                (ROOT / case_id / (version + ".json")).read_text(encoding="utf-8")
            )
        )
        if (
            p["case_id"] != case_id
            or p["version"] != version
            or fingerprint(p) != expected
        ):
            raise ValueError("已发布案例被修改")
        return p
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        IndexError,
    ) as exc:
        raise WorkspaceError(
            "案例包不可用：缺失、内容校验失败或已发布版本被修改"
        ) from exc


def catalog():
    try:
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = []
    for identity in sorted(manifest):
        try:
            cid, version = identity.split("/")
            p = load_package(cid, version)
            entries.append(
                {
                    k: p[k]
                    for k in (
                        "case_id",
                        "version",
                        "title",
                        "domain",
                        "method",
                        "audience",
                        "learning_goals",
                        "organized_on",
                    )
                }
                | {"available": True, "fingerprint": fingerprint(p)}
            )
        except (WorkspaceError, ValueError):
            entries.append(
                {
                    "case_id": identity,
                    "available": False,
                    "reason": "案例包不可用，请联系维护者；未创建项目",
                }
            )
    # The directory offers one current release per case, while old bound versions
    # remain addressable through load_package. No user project is migrated.
    latest = {}
    for entry in entries:
        key = entry["case_id"]
        previous = latest.get(key)
        if previous is None or tuple(
            map(int, entry.get("version", "0.0.0").split("."))
        ) > tuple(map(int, previous.get("version", "0.0.0").split("."))):
            latest[key] = entry
    return list(latest.values())

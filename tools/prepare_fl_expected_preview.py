"""Save an explicitly hypothetical preview to the user-selected project.

No experiment runner, model call, result backfill or verified claim mutation.
Uses the live API with optimistic revision checking. Safe to rerun by title.
"""
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PID = "proj_20261014_181948_2e33e5"
BASE = f"http://127.0.0.1:8001/api/projects/{PID}"


def request(suffix, data=None, method=None):
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
    with urlopen(Request(BASE + suffix, raw, {"Content-Type": "application/json"}, method=method), timeout=30) as response:
        return json.load(response)


def save(kind, title, content, related_ids=None):
    workspace = request("/workspace")["workspace"]
    existing = next((item for item in workspace["items"] if item["kind"] == kind and item["title"] == title), None)
    if existing:
        return existing["item_id"]
    response = request("/workspace/items", {"expected_revision": workspace["revision"], "item": {
        "kind": kind, "title": title, "content": content, "related_ids": related_ids or [],
    }})
    return response["workspace"]["items"][-1]["item_id"]


def main():
    report = (ROOT / "projects" / PID / "预期实验展示_非实测.md").read_text(encoding="utf-8")
    assert "非实测" in report and "人为设定" in report
    experiments_before = request("/experiments")
    from core.knowledge import KnowledgeStore
    from core.config import ProjectPaths
    paths = ProjectPaths.from_root(ROOT)
    store = KnowledgeStore(paths.project_db(PID))
    claims_before = [c.model_dump(mode="json") for c in store.list_claims()]
    workspace = request("/workspace")["workspace"]
    brief = workspace["brief"]
    if not any(brief.values()):
        request("/workspace/brief", {"expected_revision": workspace["revision"], "brief": {
            "goal": "比较个性化联邦表征与客户端采样的作用。本次只整理方法和非实测展示，不执行训练。",
            "background": "已有18篇入库文献、候选思路与方法修订稿；原实验任务及脚本尚未构成有效联邦学习验证。",
            "resources": "本次不使用本机或远程算力；后续数据、训练框架及硬件需另行确认。",
            "time_budget": "当前完成预期验证展示稿，真实实验时间另行安排。",
        }}, "PUT")
    question = save("question", "个性化表征与采样是否改善非 IID 客户端表现？（待验证）",
        "在相同数据划分、训练与通信预算下，共享表征＋本地个性化头及异构感知采样，是否比 FedAvg 改善加权平均准确率和客户端准确率第10百分位？先聚焦静态标签偏斜。原有具体提升幅度均为待检验目标，当前没有实测支持。")
    plan = save("plan", "联邦学习公平对照方案（仅计划，本次不执行）",
        "比较 FedAvg、共享表征＋个性化头＋均匀采样、同结构＋异构感知采样；固定网络、数据划分、随机种子、参与数、本地步数和通信轮数。主要指标为加权平均准确率、客户端第10百分位、客户端间标准差，并登记计算量与通信开销。消融分别去掉采样和个性化头。真正执行后保留多种子原始日志；可能无提升或无法定论。本次按用户要求不执行任何训练；原 CIFAR/FEMNIST 结论均未验证。", [question])
    outline = save("outline", "预期实验报告（演示假设，非实测）", report, [question, plan])
    save("note", "执行边界：不运行实验，演示数值不回填结果",
        "用户本次选择只查看预期效果。预期实验报告中的全部数值由人为设定，仅用于页面和报告讨论，不是已执行的实验。未生成实际结果或支持性判断，未验证原论断，也未声称项目完成实证研究。旧 run_exp.py 不是正确的联邦学习实现，不能作为本选题的验证脚本。", [question, plan])
    # Keep the generated method version but prepend an explicit hypothesis notice.
    from core.artifacts.version import ArtifactManager, ArtifactContentStore
    from core.knowledge import ArtifactType
    artifacts = store.list_artifacts(ArtifactType.METHOD_DOC)
    if artifacts:
        latest = max(artifacts, key=lambda a: a.created_at)
        content = latest.content or (Path(latest.content_path).read_text(encoding="utf-8") if latest.content_path else "")
        label = "> **方法讨论稿，未实测**"
        if content and not content.startswith(label):
            notice = label + "：本文由模型根据项目材料生成，公式、引用与可实现性均需人工核对。所有提升幅度、收敛性及创新性表述均为待验证设想，不是已证实结论。旧实验脚本并非正确的联邦学习实现，本次没有执行实验。预期演示表单独保存，不能作为本文证据。\n\n"
            ArtifactManager(store, ArtifactContentStore(paths.project_artifacts(PID))).new_version(
                latest.artifact_group, content=notice+content,
                title="待核对方法修订稿（未实测）", created_by="user_requested_preview",
            )
    assert request("/experiments") == experiments_before, "Experiment entities unexpectedly changed"
    assert [c.model_dump(mode="json") for c in store.list_claims()] == claims_before, "Claims unexpectedly changed"
    print(json.dumps({"project_id": PID, "preview_outline_id": outline, "experiments_unchanged": True, "claims_unchanged": True, "training_executed": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()

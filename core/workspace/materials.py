"""Compatibility adapter: saved pipeline outputs are sources, not verified records.

Stable source keys and content fingerprints allow explicit, idempotent adoption
and downstream review when a source changes. This module never runs an agent.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from core.experiment_results import has_real_metrics
from core.knowledge.schema import ArtifactType, ExperimentStatus


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def text_content(artifact, project_dir: Path) -> str:
    if artifact.content is not None:
        return artifact.content[:200000]
    if not artifact.content_path:
        return ""
    from core.storage_paths import project_file
    path = project_file(artifact.content_path, project_dir)
    root = (project_dir / "artifacts").resolve()
    if not path.is_relative_to(root) or path.suffix.lower() not in (".md", ".txt"):
        return ""
    try:
        with path.open(encoding="utf-8") as stream:
            return stream.read(200000)
    except (OSError, UnicodeError):
        return ""


def experiment_plan_payload(exp) -> dict:
    # Results must not invalidate the plan; the protocol itself does.
    return {"name": exp.name, "config": {k: v for k, v in exp.config.items() if k != "user_claim_assessments"},
            "verifies_claim_ids": exp.verifies_claim_ids}


def experiment_result_payload(exp) -> dict:
    return {"status": exp.status.value, "metrics": exp.metrics, "summary": exp.result_summary,
            "anomaly_notes": exp.anomaly_notes, "completed_at": exp.completed_at.isoformat() if exp.completed_at else None}


def material_catalog(store, project_dir: Path) -> list[dict]:
    entries = []
    known_papers = {p.paper_id for p in store.list_papers()}
    def add(kind, source_id, title, content, payload, paper_ids=(), **extra):
        entries.append({"kind": kind, "source_id": source_id, "title": title[:200], "content": content[:16000],
                        "fingerprint": fingerprint(payload), "paper_ids": [p for p in dict.fromkeys(paper_ids) if p in known_papers],
                        "available": bool(content.strip()), "truncated":len(content)>16000, **extra})
    for idea in store.list_ideas():
        if idea.status == "rejected":
            continue
        add("idea", idea.idea_id, idea.text[:90], idea.text, idea.model_dump(mode="json"), idea.source_paper_ids)
    for gap in store.list_research_gaps(limit=500):
        add("gap", gap.gap_id, gap.statement[:90], gap.statement + "\n\n" + gap.detail,
            gap.model_dump(mode="json"), [e.get("paper_id") for e in gap.evidence if e.get("paper_id")])
    latest = {}
    for artifact in store.list_artifacts(ArtifactType.METHOD_DOC):
        previous = latest.get(artifact.artifact_group)
        if previous is None or artifact.version > previous.version:
            latest[artifact.artifact_group] = artifact
    for group, artifact in latest.items():
        content = text_content(artifact, project_dir)
        add("method", group, artifact.title or "已有方法文档", content,
            {"artifact_id": artifact.artifact_id, "version": artifact.version, "content": content},
            artifact_id=artifact.artifact_id, version=artifact.version)
    for exp in store.list_experiments():
        payload = experiment_plan_payload(exp)
        content = f"实验任务：{exp.name}\n\n" + json.dumps(payload, ensure_ascii=False, indent=2)
        add("experiment_plan", exp.experiment_id, exp.name, content, payload,
            warning="旧任务的目标、数据和对照条件仍需核对；关联不表示已执行。")
        if exp.status in (ExperimentStatus.COMPLETED, ExperimentStatus.FAILED, ExperimentStatus.ANOMALY_DETECTED):
            status = {ExperimentStatus.COMPLETED: "completed", ExperimentStatus.FAILED: "failed", ExperimentStatus.ANOMALY_DETECTED: "inconclusive"}[exp.status]
            # Legacy bad 'completed' rows do not become measured results.
            valid = status != "completed" or has_real_metrics(exp.metrics)
            content = exp.result_summary or exp.anomaly_notes or "已有用户结果记录，需核对原始日志。"
            add("experiment_result", exp.experiment_id, f"结果：{exp.name}", content,
                experiment_result_payload(exp), result_status=status, metrics=exp.metrics if has_real_metrics(exp.metrics) else {},
                importable=valid, warning="用户提交记录，未经系统独立复现。" if valid else "旧结果标记完成但缺少有效指标，不能关联为已完成结果。")
    return entries


def mark_changed_sources(workspace, catalog) -> None:
    current = {(entry["kind"], entry["source_id"]): entry for entry in catalog}
    stale = set()
    for item in workspace.items:
        if item.status != "active" or not item.source_ref:
            continue
        ref = item.source_ref
        source = current.get((ref.kind, ref.source_id))
        if source is None or source["fingerprint"] != ref.fingerprint:
            item.needs_review = True
            stale.add(item.item_id)
    while True:
        affected = {item.item_id for item in workspace.items
                    if item.status == "active" and set(item.related_ids) & stale}
        if affected <= stale:
            break
        stale |= affected
    for item in workspace.items:
        if item.status == "active" and item.item_id in stale:
            item.needs_review = True

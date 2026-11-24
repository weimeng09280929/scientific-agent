"""Move explicitly selected project directories to a private, recoverable archive."""
from datetime import datetime, timezone
import json
import os
import re
from uuid import uuid4


def archive_project_directories(paths, project_ids: list[str]) -> str:
    root = paths.projects.resolve()
    sources = []
    for project_id in project_ids:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", project_id):
            raise ValueError("项目编号不合法，本批未删除")
        source = paths.project_dir(project_id)
        if source.is_symlink() or source.resolve().parent != root or not source.is_dir():
            raise ValueError(f"项目目录不存在或不在项目根目录内：{project_id}；本批未删除")
        sources.append(source)
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:8]
    archive_root = paths.root / ".project-trash"
    if archive_root.is_symlink() or archive_root.resolve().parent != paths.root.resolve():
        raise ValueError("归档目录不安全，本批未删除")
    destination = archive_root / batch_id
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {"project_ids": project_ids, "created_at": datetime.now(timezone.utc).isoformat(), "status": "preparing"}
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    moved = []
    try:
        for source in sources:
            target = destination / source.name
            os.replace(source, target)
            moved.append((source, target))
        manifest["status"] = "archived"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as error:
        rollback_errors = []
        for source, target in reversed(moved):
            try:
                os.replace(target, source)
            except OSError:
                rollback_errors.append(source.name)
        if rollback_errors:
            raise RuntimeError(f"删除中止，以下项目仍在归档 {batch_id} 中，请恢复后再操作：{', '.join(rollback_errors)}") from error
        raise RuntimeError("归档未完成，已移回原项目；本批未删除") from error
    return batch_id

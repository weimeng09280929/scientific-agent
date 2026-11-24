"""Resolve restored file locations without changing material identity/fingerprints."""
import json
from pathlib import Path, PurePosixPath, PureWindowsPath

RELOCATION = ".sra-relocation.json"


def logical(value):
    return PureWindowsPath(value) if PureWindowsPath(value).drive else PurePosixPath(value)


def project_file(value, project_dir, *, data_root=None, origins=None):
    project_dir = Path(project_dir).resolve()
    root = Path(data_root).resolve() if data_root else project_dir.parent.parent
    stored = logical(value)
    if ".." in stored.parts:
        raise ValueError("文件引用含越界路径")
    marker = root / RELOCATION
    if marker.is_symlink():
        raise ValueError("恢复路径配置不安全")
    if origins is None and marker.is_file():
        config = json.loads(marker.read_text(encoding="utf-8"))
        if config.get("version") != 1 or not isinstance(config.get("origins"), list):
            raise ValueError("恢复路径配置不兼容")
        origins = config["origins"]
    # Archives retain their old project-relative references even before a move
    # between machines. Only map this project's original subtree.
    origins = [str(root), *(origins or [])]
    if origins:
        # Only this project's subtree can be mapped, including archived copies.
        relative_project = project_dir.relative_to(root)
        for origin in origins:
            base = logical(origin)
            for parent in (base / "projects" / project_dir.name, base.joinpath(*relative_project.parts)):
                try:
                    tail = stored.relative_to(parent)
                except ValueError:
                    continue
                candidate = project_dir.joinpath(*tail.parts).resolve()
                if not candidate.is_relative_to(project_dir):
                    raise ValueError("文件引用越出项目")
                return candidate
    path = Path(value)
    if not path.is_absolute():
        local = project_dir / "artifacts" / path
        path = local if local.is_file() else root / path
    return path.resolve()

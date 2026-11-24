"""Downloads must follow the newest artifact, not an old empty group."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from core.config import GlobalConfig, ProjectPaths
from core.knowledge import ArtifactType, KnowledgeStore
from core.artifacts import ArtifactContentStore, ArtifactManager


@pytest.mark.parametrize("kind,route", [(ArtifactType.METHOD_DOC,"method-doc"), (ArtifactType.PAPER_DRAFT,"paper-draft")])
def test_download_and_full_report_use_latest_group(tmp_path, monkeypatch, kind, route):
    monkeypatch.setenv("SRA_WEB_SKIP_GUARD", "1")
    from web import api
    config = GlobalConfig(paths=ProjectPaths.from_root(tmp_path), dry_run=True)
    monkeypatch.setattr(api,"_CONFIG",config)
    pid = "download-fixture"
    monkeypatch.setattr(api,"_PROJECTS",{pid:api.ProjectState(project_id=pid,topic="软件测试",domain="ai",created_at="2026-10-17")})
    store = KnowledgeStore(config.paths.project_db(pid))
    manager = ArtifactManager(store,ArtifactContentStore(config.paths.project_artifacts(pid)))
    manager.create_artifact(kind,title="old empty group",content="")
    original = manager.create_artifact(kind,title="new group",content="old content marker")
    manager.new_version(original.artifact_group,content="latest revision marker (test only)")
    app = FastAPI()
    app.add_api_route("/api/projects/{project_id}/download/{artifact_type}",api.download_artifact,methods=["GET"])
    client = TestClient(app)
    response = client.get(f"/api/projects/{pid}/download/{route}")
    assert response.status_code == 200
    assert response.text == "latest revision marker (test only)"
    report = client.get(f"/api/projects/{pid}/download/full-report")
    assert report.status_code == 200
    assert "latest revision marker (test only)" in report.text
    assert "old content marker" not in report.text

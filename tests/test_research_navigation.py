"""增量导航及已有写作产物的契约，不启动模型或后台任务。"""
import json
from pathlib import Path
import re
import subprocess

from core.config import ProjectPaths
from core.knowledge.schema import Artifact, ArtifactType, Idea
from tests.test_research_workspace import client_for


ROOT = Path(__file__).resolve().parents[1]


def navigation():
    source = ROOT / "web/static/research-navigation.js"
    script = "global.window={};require(process.argv[1]);console.log(JSON.stringify(window.SRA_NAVIGATION));"
    return json.loads(subprocess.check_output(["node", "-e", script, str(source)], text=True, encoding="utf-8"))


def test_navigation_labels_and_aliases_share_one_definition():
    data = navigation()
    html = (ROOT / "web/static/index.html").read_text(encoding="utf-8")
    items = re.findall(r'class="nav-item" data-page="([^"]+)">\s*<span>([^<]+)</span>', html)
    assert [key for key, _ in items] == ["papers", "experiments", "notes", "human", "methods", "progress", "topic-discovery", "research-report", "gaps", "writing"]
    # Dashboard/project access moved into separated conversation/project groups,
    # while page identities, deep links and keyboard shortcuts stay available.
    assert 'aria-label="最近对话"' in html and 'aria-label="项目"' in html
    assert 'id="ch-project-manage"' in html and 'id="ch-new"' in html
    assert all(data["pages"][key]["title"] == text for key, text in items)
    assert data["pages"]["claims"]["owner"] == "gaps"
    assert data["pages"]["reading-method"]["owner"] == "methods"
    assert data["pages"]["writing-method"]["area"] == "writing"
    assert all(target in set(dict(items)) | {"dashboard", "create"} for target in data["shortcuts"].values())
    assert html.index('src="/static/research-navigation.js"') < html.index('src="/static/research-guide.js"') < html.index('src="/static/app.js"')
    assert html.index('src="/static/conversation-home.js"') < html.index('src="/static/research-guide.js"')
    assert 'id="ch-new"' in html and '<summary>更多</summary>' in html
    assert data["pages"]["dashboard"]["title"]=="科研对话"
    assert data["pages"]["writing"]["title"]=="历史材料"


def test_materials_only_returns_latest_saved_artifacts_without_model_calls(tmp_path):
    def no_model():
        raise AssertionError("浏览材料不能调用模型")
    client, service = client_for(tmp_path, dry_run=False, registry_factory=no_model)
    service.store.save_idea(Idea(idea_id="i1", text="历史候选思路，不是已验证结论"))
    for version in (1, 2):
        service.store.save_artifact(Artifact(artifact_id=f"a{version}", artifact_group="draft", version=version, artifact_type=ArtifactType.PAPER_DRAFT, title="历史稿件", content=f"已保存正文 v{version}"))
    service.store.save_artifact(Artifact(artifact_id="empty", artifact_group="empty", version=1, artifact_type=ArtifactType.REVIEW_NOTE, title="无正文的修改意见"))
    response = client.get("/api/projects/p1/research-materials")
    assert response.status_code == 200
    data = response.json()
    assert data["ideas"][0]["idea_id"] == "i1"
    artifacts = {a["artifact_id"]: a for a in data["artifacts"]}
    assert set(artifacts) == {"a2", "empty"}
    assert artifacts["a2"]["content"] == "已保存正文 v2"
    assert artifacts["a2"]["available"]
    assert not artifacts["empty"]["available"] and artifacts["empty"]["issue"]
    assert service.load().revision == 0 and not service.load().items
    assert client.get("/api/projects/other/research-materials").status_code == 404


def test_materials_file_reader_is_project_scoped_and_bounded(tmp_path):
    client, service = client_for(tmp_path)
    root = ProjectPaths.from_root(tmp_path).project_artifacts("p1")
    root.mkdir(parents=True)
    valid = root / "draft.md"
    valid.write_text("正文" * 110000, encoding="utf-8")
    outside = tmp_path / "private.md"
    outside.write_text("不得被读取的私密内容", encoding="utf-8")
    for key, path in (("valid", valid), ("outside", outside), ("missing", root / "missing.md")):
        service.store.save_artifact(Artifact(artifact_id=key, artifact_group=key, version=1, artifact_type=ArtifactType.PAPER_DRAFT, title=key, content_path=str(path)))
    data = client.get("/api/projects/p1/research-materials").json()
    artifacts = {a["artifact_id"]: a for a in data["artifacts"]}
    assert artifacts["valid"]["available"] and artifacts["valid"]["truncated"]
    assert len(artifacts["valid"]["content"]) == 200000
    assert not artifacts["outside"]["available"] and "目录" in artifacts["outside"]["issue"]
    assert "不得被读取" not in json.dumps(data, ensure_ascii=False)
    assert not artifacts["missing"]["available"] and "无法读取" in artifacts["missing"]["issue"]

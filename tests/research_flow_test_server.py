"""在独立临时目录运行真实 API，供浏览器测试研究主线，不接触用户项目。"""
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["SRA_WEB_SKIP_GUARD"] = "1"
os.environ["SRA_WEB_SKIP_PROJECT_SCAN"] = "1"
os.environ["SRA_DRY_RUN"] = "true"

import core.config as configuration
import uvicorn

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="sra-flow-ui-") as temporary:
        configuration._default_config = configuration.GlobalConfig(
            paths=configuration.ProjectPaths.from_root(temporary), dry_run=True,
        )
        from web.api import app
        if "--seed-mainline" in sys.argv:
            from web import api
            from core.knowledge.schema import Paper,Idea,Experiment,ArtifactType
            from core.knowledge import KnowledgeStore
            from core.artifacts import ArtifactManager,ArtifactContentStore
            fixture=api.create_project(api.CreateProjectRequest(topic="主线衔接软件夹具（不是真实科研）",domain="ai"))
            pid=fixture["project_id"]
            store=KnowledgeStore(configuration._default_config.paths.project_db(pid))
            store.save_paper(Paper(paper_id="fixture-paper",title="Offline software fixture",abstract="不是科研证据，不来自外部检索。"))
            store.save_idea(Idea(idea_id="fixture-idea",text="测试问题：相同预算下的比较",source_paper_ids=["fixture-paper"]))
            store.save_experiment(Experiment(experiment_id="fixture-exp",name="离线流程测试任务",config={"purpose":"软件测试，不执行训练","pass_criterion":"检查记录完整性"}))
            ArtifactManager(store,ArtifactContentStore(configuration._default_config.paths.project_artifacts(pid))).create_artifact(
                ArtifactType.METHOD_DOC,title="方法软件夹具",content="测试方法说明，不是已验证方法。")
        uvicorn.run(app, host="127.0.0.1", port=8012, log_level="warning")

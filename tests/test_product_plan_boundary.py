"""Product ends at DESIGN in every mode. All projects/data are offline fixtures."""
from hashlib import sha256
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from core.config import GlobalConfig, ProjectPaths
from core.knowledge import ArtifactType, ExperimentStatus
from core.knowledge.schema import Experiment
from core.llm import LLMRegistry
from core.orchestration.graph import Graph
from core.orchestration.node import HumanResponse, NodeResult, NodeStatus
from core.product_boundary import ExecutionPolicy, ProductExecutionRestricted, inspect_boundary
from core.state.lifecycle import LifecycleStage, StageStatus
from core.workspace.schema import ItemInput, Suggestion
from core.workspace.service import KEY as WORKSPACE_KEY
from core.workflow_checkpoint import save_context
from runtime.pipeline import Pipeline, StageResult
from stages.common import ARTIFACT_MANAGER, DESIGN_METHOD_CONTENT, KNOWLEDGE_STORE
from tests.test_orchestration_graph import _NoopAgent, _NoopHuman
from tests.test_unified_workflow import unified, branch, item


def fingerprint_tree(root):
    return {str(p.relative_to(root)):sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def bounded(tmp_path,monkeypatch):
    monkeypatch.setattr(LLMRegistry,'from_config',classmethod(lambda cls,cfg:object()))
    return GlobalConfig(paths=ProjectPaths.from_root(tmp_path),dry_run=True)


def graph_factory(calls):
    def build(stage):
        calls.append(stage)
        assert stage.index() <= LifecycleStage.DESIGN.index(), 'Product built a forbidden graph'
        class Marker(_NoopAgent):
            def _execute(self,data,ctx):
                if stage==LifecycleStage.DESIGN:
                    ctx.set(DESIGN_METHOD_CONTENT,'离线方法方案，不是科研成果。')
                    ctx.get(ARTIFACT_MANAGER).create_artifact(ArtifactType.METHOD_DOC,title='Fixture plan',content='待用户关联和审阅的方案')
                return NodeResult(status=NodeStatus.SUCCESS,summary='offline fixture')
        graph=Graph(name=stage.value,stage=stage.value)
        graph.add_node(Marker(stage.value+'_fixture'))
        if stage==LifecycleStage.DESIGN:
            graph.add_node(_NoopHuman('method_review'))
            graph.add_edge(stage.value+'_fixture','method_review')
        return graph
    return build


@pytest.mark.parametrize('dry_run',[True,False])
def test_new_product_stops_at_design_after_human_confirmation(bounded,monkeypatch,dry_run):
    bounded.dry_run=dry_run
    pipeline=Pipeline(bounded);calls=[]
    monkeypatch.setattr(pipeline,'_build_stage_graph',graph_factory(calls))
    result=pipeline.run_pipeline('new','offline',human_callback=lambda req:HumanResponse(action='continue',text='approve'))
    assert result.status=='stopped' and result.current_stage is LifecycleStage.DESIGN
    assert result.completed_stages==LifecycleStage.ordered()[:3]
    assert calls==LifecycleStage.ordered()[:3]
    boundary=inspect_boundary(bounded.paths,'new')
    assert not boundary['restricted'] and boundary['reason']=='design_generated'
    assert boundary['stage_statuses']['experiment']=='not_started'
    from core.knowledge import KnowledgeStore
    store=KnowledgeStore(bounded.paths.project_db('new'))
    assert store.list_experiments()==[]
    assert len(store.list_artifacts(ArtifactType.METHOD_DOC))==1
    assert store.list_artifacts(ArtifactType.PAPER_DRAFT)==[]
    before=fingerprint_tree(bounded.paths.projects)
    again=pipeline.run_pipeline('new','changed topic must not overwrite',resume=True)
    assert again.status=='stopped' and calls==LifecycleStage.ordered()[:3]
    assert fingerprint_tree(bounded.paths.projects)==before
    with pytest.raises(ProductExecutionRestricted): pipeline.start_project('new','must not reset')
    with pytest.raises(ProductExecutionRestricted): pipeline.resume_project('new')
    assert pipeline.run_topic_discovery('new','must not reset').status=='stopped'
    assert fingerprint_tree(bounded.paths.projects)==before


@pytest.mark.parametrize('policy',['product_plan','internal_legacy',None,False])
def test_policy_cannot_be_selected_by_untyped_configuration(bounded,policy):
    with pytest.raises(TypeError,match='ExecutionPolicy'):
        Pipeline(bounded,execution_policy=policy)
    assert StageResult(stage='design',status='restricted').stage is LifecycleStage.DESIGN


@pytest.mark.parametrize('dry_run',[True,False])
def test_real_stage_graphs_stop_at_plan_with_offline_dependencies(bounded,monkeypatch,dry_run):
    from core.knowledge import KnowledgeStore
    from tests.test_workflow_reliability import WorkflowModel
    from stages.research.agents import PaperFetchAgent,PaperIngestAgent
    bounded.dry_run=dry_run
    model=WorkflowModel();model.store=KnowledgeStore(bounded.paths.project_db('actual-graphs'))
    monkeypatch.setattr(LLMRegistry,'from_config',classmethod(lambda cls,cfg:model))
    meta={'title':'Federated learning offline test fixture','abstract':'Software fixture, not real scientific evidence.','source':'sciverse_fixture','year':2026}
    monkeypatch.setattr(PaperFetchAgent,'_real_fetch',lambda *a,**kw:([dict(meta)],[]))
    monkeypatch.setattr(PaperIngestAgent,'_enrich_urls',staticmethod(lambda metas:None))
    pipeline=Pipeline(bounded)
    result=pipeline.run_pipeline('actual-graphs','联邦学习离线软件测试',domain='ai',human_callback=lambda req:HumanResponse(action='continue',text='ok'))
    assert result.status=='stopped',result.summary
    assert result.completed_stages==LifecycleStage.ordered()[:3]
    assert any(row['node_id']=='method_review' for row in result.node_history)
    assert not any(row['node_type'].startswith(('experiment_','writing_')) for row in result.node_history)
    assert model.store.list_experiments()==[]
    if dry_run:
        # Existing demo nodes persist their draft in the execution checkpoint,
        # not as a scientific artifact. The product boundary must still apply.
        assert model.store.get_kv('pipeline.context.v1')['data']['design.method_content']
        assert model.calls==[]
    else:
        assert model.store.list_artifacts(ArtifactType.METHOD_DOC)
    assert model.store.list_artifacts(ArtifactType.PAPER_DRAFT)==[]
    assert not (bounded.paths.project_dir('actual-graphs')/'experiments/experiment_plan.json').exists()
    forbidden={'ExperimentConfigSchema','OutcomeAssessSchema','OutlineSchema','SectionDraftList'}
    assert not any(call.get('output_schema',type(None)).__name__ in forbidden for call in model.calls)
    before=fingerprint_tree(bounded.paths.projects)
    assert pipeline.run_pipeline('actual-graphs','',resume=True).status=='stopped'
    assert fingerprint_tree(bounded.paths.projects)==before


def legacy_project(config,stage):
    internal=Pipeline(config,execution_policy=ExecutionPolicy.INTERNAL_LEGACY)
    session,ctx=internal.start_project('legacy','original')
    for candidate in LifecycleStage.ordered():
        if candidate!=LifecycleStage.RESEARCH:
            session.advance(candidate)
        else:
            session.start_stage(candidate)
        if candidate==stage:
            break
        session.complete_stage(reason='offline fixture')
    ctx.current_stage=stage.value
    ctx.current_node_id='experiment_review' if stage==LifecycleStage.EXPERIMENT else 'revise'
    ctx.record_node_result(ctx.current_node_id,stage.value+'_fixture','pending_human','offline legacy gate')
    if stage==LifecycleStage.WRITING:
        # This used to trigger outline repair before the old product guard.
        from stages.common import EXPERIMENT_OUTCOME,WRITING_OUTLINE
        ctx.set(EXPERIMENT_OUTCOME,{'ready_for_writing':True,'success':False})
        ctx.set(WRITING_OUTLINE,{'title':'original outline'})
    store=ctx.get(KNOWLEDGE_STORE)
    store.save_experiment(Experiment(experiment_id='old-exp',name='old',status=ExperimentStatus.COMPLETED,metrics={'fixture':0.1}))
    ctx.get(ARTIFACT_MANAGER).create_artifact(ArtifactType.PAPER_DRAFT,title='original manuscript',content='Historical manuscript bytes')
    save_context(ctx,store,config.dry_run)
    return session,ctx


@pytest.mark.parametrize('stage',[LifecycleStage.EXPERIMENT,LifecycleStage.WRITING])
@pytest.mark.parametrize('dry_run',[True,False])
def test_historical_checkpoint_never_restored_repaired_or_reexecuted(bounded,monkeypatch,stage,dry_run):
    bounded.dry_run=dry_run
    session,ctx=legacy_project(bounded,stage)
    before=fingerprint_tree(bounded.paths.projects)
    pipeline=Pipeline(bounded)
    monkeypatch.setattr(pipeline,'_build_stage_graph',lambda *args:pytest.fail('must not construct graph'))
    result=pipeline.run_pipeline('legacy','replacement',resume=True)
    assert result.status=='restricted' and result.completed_stages==[]
    with pytest.raises(ProductExecutionRestricted): pipeline.resume_project('legacy')
    with pytest.raises(ProductExecutionRestricted): pipeline.start_project('legacy','overwrite')
    single=pipeline.run_stage(session,ctx,LifecycleStage.RESEARCH)
    assert single.status=='restricted' and isinstance(single.stage,LifecycleStage)
    assert fingerprint_tree(bounded.paths.projects)==before


def test_stale_session_cannot_hide_entered_disk_stage(bounded,monkeypatch):
    from core.state.session import ProjectSession
    session,ctx=legacy_project(bounded,LifecycleStage.EXPERIMENT)
    stale=ProjectSession.create('stale',bounded.paths)
    stale.project_id='legacy'
    ctx.current_stage='design';ctx.current_node_id='method_review'
    ctx._history=[]  # Intentionally stale context; disk snapshot must still win.
    save_context(ctx,ctx.get(KNOWLEDGE_STORE),bounded.dry_run)
    pipeline=Pipeline(bounded)
    monkeypatch.setattr(pipeline,'_build_stage_graph',lambda *a:pytest.fail('stale session bypass'))
    before=fingerprint_tree(bounded.paths.projects)
    assert pipeline.run_stage(stale,ctx,LifecycleStage.RESEARCH).status=='restricted'
    assert fingerprint_tree(bounded.paths.projects)==before


@pytest.mark.parametrize('checkpoint',[[],{'history':[1]},{'data':[]},{'data':{'experiment.outcome':'invalid'}}])
def test_invalid_checkpoint_fails_closed_without_repairs(bounded,checkpoint):
    from core.knowledge import KnowledgeStore
    KnowledgeStore(bounded.paths.project_db('broken')).save_kv('pipeline.context.v1',checkpoint)
    before=fingerprint_tree(bounded.paths.projects)
    result=Pipeline(bounded).run_pipeline('broken','test',resume=True)
    assert result.status=='restricted' and result.product_boundary['reason']=='checkpoint_unreadable'
    assert fingerprint_tree(bounded.paths.projects)==before


def test_boundary_reads_close_sqlite_connection_immediately(bounded,monkeypatch):
    import sqlite3
    from core.knowledge import KnowledgeStore
    KnowledgeStore(bounded.paths.project_db('reader'))
    connect=sqlite3.connect;closed=[]
    class TrackedConnection(sqlite3.Connection):
        def close(self):
            closed.append(True)
            super().close()
    monkeypatch.setattr(sqlite3,'connect',lambda *a,**kw:connect(*a,**kw,factory=TrackedConnection))
    assert not inspect_boundary(bounded.paths,'reader')['restricted']
    assert closed==[True]


def test_workspace_transactions_close_connections_on_commit_and_conflict(unified,monkeypatch):
    import sqlite3
    from core.workspace.service import RevisionConflict
    _,_,s,_=unified
    connect=sqlite3.connect;created=[];closed=[]
    class TrackedConnection(sqlite3.Connection):
        def close(self):
            closed.append(id(self));super().close()
    def tracked(*a,**kw):
        conn=connect(*a,**kw,factory=TrackedConnection);created.append(id(conn));return conn
    monkeypatch.setattr(sqlite3,'connect',tracked)
    s.add_item(ItemInput(kind='note',title='offline note',content='resource test'),0)
    with pytest.raises(RevisionConflict):
        s.add_item(ItemInput(kind='note',title='stale note',content='must not save'),0)
    assert created and sorted(created)==sorted(closed)
    assert [i.title for i in s.load().items]==['offline note']


@pytest.mark.parametrize('stage',[LifecycleStage.EXPERIMENT,LifecycleStage.WRITING])
@pytest.mark.parametrize('dry_run',[True,False])
def test_single_stage_and_force_writing_cannot_bypass(bounded,monkeypatch,stage,dry_run):
    bounded.dry_run=dry_run
    pipeline=Pipeline(bounded);session,ctx=pipeline.start_project('single','test')
    before=fingerprint_tree(bounded.paths.projects)
    monkeypatch.setattr(pipeline,'_build_stage_graph',lambda *a:pytest.fail('forbidden graph'))
    result=pipeline.run_stage(session,ctx,stage)
    assert result.status=='restricted' and result.stage is stage
    with pytest.raises(ValueError,match='force_writing'):
        pipeline.run_pipeline('single','test',force_writing=True)
    assert fingerprint_tree(bounded.paths.projects)==before


def test_earlier_stop_and_non_success_result_are_not_lost(bounded,monkeypatch):
    pipeline=Pipeline(bounded);calls=[]
    monkeypatch.setattr(pipeline,'_build_stage_graph',graph_factory(calls))
    result=pipeline.run_pipeline('early','test',stop_before=LifecycleStage.IDEATION)
    assert result.status=='stopped' and calls==[LifecycleStage.RESEARCH]
    monkeypatch.setattr(pipeline,'run_stage',lambda *a,**k:StageResult(stage=LifecycleStage.RESEARCH,status='restricted',summary='fixture'))
    result=pipeline.run_pipeline('restricted-stage','test')
    assert result.status=='restricted' and result.completed_stages==[]


def test_human_callback_cannot_switch_context_to_forbidden_stage(bounded,monkeypatch):
    pipeline=Pipeline(bounded);session,ctx=pipeline.start_project('human','test')
    for stage in (LifecycleStage.RESEARCH,LifecycleStage.IDEATION):
        if stage!=LifecycleStage.RESEARCH: session.advance(stage)
        else: session.start_stage(stage)
        session.complete_stage(reason='offline fixture')
    session.advance(LifecycleStage.DESIGN)
    graph=Graph(name='design',stage='design');graph.add_node(_NoopHuman('method_review'))
    monkeypatch.setattr(pipeline,'_build_stage_graph',lambda stage:graph)
    def response(req):
        ctx.current_stage='writing'
        return HumanResponse(action='rollback')
    result=pipeline.run_stage(session,ctx,LifecycleStage.DESIGN,human_callback=response)
    assert result.status=='restricted'
    assert session.status_of(LifecycleStage.DESIGN)!=StageStatus.DONE


@pytest.mark.parametrize('stale',['question','plan','source'])
def test_plan_ready_requires_valid_question_plan_and_source(unified,stale):
    api,client,s,manager=unified
    q,p=branch(s)
    if stale=='source':
        source=s.store.list_ideas()[0];source.text='changed original source';s.store.save_idea(source)
    else:
        workspace=s.load()
        next(i for i in workspace.items if i.item_id==(q.item_id if stale=='question' else p.item_id)).needs_review=True
        s.store.save_kv(WORKSPACE_KEY,workspace.model_dump(mode='json'))
    responses=[client.get(f'/api/projects/{s.project_id}/{suffix}').json() for suffix in ('workspace','status','dashboard')]
    assert all(not r['workflow']['flow']['planReady'] for r in responses)
    assert all(r['workflow']['continuation']['action']=='review_record' for r in responses)
    continued=client.post(f'/api/projects/{s.project_id}/resume').json()
    assert continued['action']=='review_record' and api._PROJECTS[s.project_id].thread is None
    assert len({r['product_stage'] for r in [*responses,continued]})==1


def test_valid_plan_continue_and_old_run_only_open_plan(unified,monkeypatch):
    api,client,s,_=unified;q,p=branch(s)
    monkeypatch.setattr(api.threading,'Thread',lambda *a,**k:pytest.fail('do not create worker'))
    for suffix in ('resume','run'):
        r=client.post(f'/api/projects/{s.project_id}/{suffix}').json()
        assert r['action']=='plan_ready' and r['page']=='experiments' and r['record_id']==p.item_id
    assert s.workflow()['flow']['recordsReady'] is False
    assert s.workflow()['flow']['planReady'] is True


@pytest.mark.parametrize('kind',['result','finding','outline'])
def test_post_plan_record_creation_and_revision_are_read_only(unified,kind):
    _,client,s,_=unified;q,p=branch(s)
    before=fingerprint_tree(s.db_path.parent)
    for extra in ({},{'supersedes_id':'historical'}):
        response=client.post(f'/api/projects/{s.project_id}/workspace/items',json={'expected_revision':s.load().revision,'item':{'kind':kind,'title':'fixture','content':'not stored',**extra}})
        assert response.status_code==422 and 'detail' in response.json()
    assert fingerprint_tree(s.db_path.parent)==before


def test_backfill_old_suggestions_result_link_and_writing_guide_are_denied(unified):
    _,client,s,_=unified;q,p=branch(s)
    workspace=s.load();workspace.suggestions.append(Suggestion(suggestion_id='old',kind='outline',title='old suggestion',content='not a new draft',based_on_revision=workspace.revision))
    s.store.save_kv(WORKSPACE_KEY,workspace.model_dump(mode='json'))
    before=fingerprint_tree(s.db_path.parent)
    routes=[('experiments/backfill',{'results_json':'[]'}),
            ('workspace/suggestions/old/apply',{'expected_revision':workspace.revision}),
            ('workspace/guide',{'expected_revision':workspace.revision,'area':'writing','message':'write manuscript'}),
            ('workspace/link-material',{'expected_revision':workspace.revision,'kind':'experiment_result','source_id':'exp-a','fingerprint':'0'*64,'confirmed':True})]
    for suffix,body in routes:
        response=client.post(f'/api/projects/{s.project_id}/{suffix}',json=body)
        assert response.status_code==422
    assert fingerprint_tree(s.db_path.parent)==before


def test_model_post_plan_suggestion_is_rejected_without_saving_research_record(unified,monkeypatch):
    from web import workspace_api
    from core.workspace.schema import GuideDraft,SuggestedItem
    api,client,s,_=unified
    api._CONFIG.dry_run=False
    monkeypatch.setattr(LLMRegistry,'from_config',classmethod(lambda cls,cfg:object()))
    monkeypatch.setattr(workspace_api,'generate_guidance',lambda *a,**kw:GuideDraft(reply='fixture',suggestions=[SuggestedItem(kind='outline',title='blocked',content='must not store')]))
    before=fingerprint_tree(s.db_path.parent)
    response=client.post(f'/api/projects/{s.project_id}/workspace/guide',json={'expected_revision':s.load().revision,'area':'study','message':'review the plan'})
    assert response.status_code==422 and 'detail' in response.json()
    # Step 2 retains the already-persisted user request on invalid model output.
    # The boundary still forbids all new scientific records or writing suggestions.
    assert s.load().items==[] and s.load().suggestions==[]
    assert len(s.load().messages)==1 and s.load().messages[0].request_status=='failed'
    assert s.load().revision==0


def test_plan_guidance_routes_to_design_task_not_experiment():
    from core.workspace.guide import generate_guidance
    from core.workspace.schema import GuideDraft
    calls=[]
    class OfflineRegistry:
        def structured_output(self,**kw):
            calls.append(kw)
            return GuideDraft(reply='Review only, no experiment execution.')
    generate_guidance(OfflineRegistry(),'study','ai','review',{'topic':'offline fixture'},'orientation')
    assert calls[0]['task_type']=='design_method_formalize'


@pytest.mark.parametrize('node',['experiment_config','cp_before_run','experiment_review','revise'])
def test_pending_legacy_gate_alone_blocks_all_web_writes_and_workers(unified,monkeypatch,node):
    api,client,s,_=unified
    state=api._PROJECTS[s.project_id]
    monkeypatch.setattr(api,'_BRIDGE',api.HumanCallbackBridge())
    api._BRIDGE._pending[s.project_id]={'context':{'node_id':node}}
    before=fingerprint_tree(s.db_path.parent)
    monkeypatch.setattr(api,'Pipeline',lambda *a,**kw:pytest.fail('must not construct pipeline'))
    for endpoint in ('run','resume'):
        data=client.post(f'/api/projects/{s.project_id}/{endpoint}').json()
        assert data['action']=='view_only' and data['product_boundary']['restricted']
    with pytest.raises(HTTPException) as error:
        api.submit_human_response(s.project_id,api.HumanResponseRequest(action='continue'))
    assert error.value.status_code==422
    api._run_pipeline_thread(s.project_id,'test',True)
    api._run_topic_discovery_thread(s.project_id,'test',True)
    assert state.status=='created' and state.thread is None
    assert api._BRIDGE.get_pending(s.project_id)['context']['node_id']==node
    assert fingerprint_tree(s.db_path.parent)==before


def test_old_outcome_marker_without_stage_is_still_view_only(unified):
    _,client,s,_=unified
    s.store.save_kv('pipeline.context.v1',{'data':{'experiment.outcome':{'recommendation':'wait_for_user_results'}}})
    before=fingerprint_tree(s.db_path.parent)
    assert client.post(f'/api/projects/{s.project_id}/resume').json()['action']=='view_only'
    assert fingerprint_tree(s.db_path.parent)==before


def test_multiple_questions_and_plans_require_selection_in_all_views(unified):
    _,client,s,_=unified
    q,p=branch(s);q2=item(s,'question','Another question')
    for focus,expected in [({'question':'','plan':''},'question'),({'question':q2.item_id,'plan':''},'design')]:
        from core.workspace.schema import ResearchFocus
        s.save_focus(ResearchFocus(**focus),s.load().revision)
        for suffix in ('workspace','status','dashboard'):
            data=client.get(f'/api/projects/{s.project_id}/{suffix}').json()
            assert not data['workflow']['flow']['planReady']
            assert data['product_stage']==expected
    second=item(s,'plan','Alternative',related_ids=[q.item_id])
    s.save_focus(ResearchFocus(question=q.item_id),s.load().revision)
    data=client.post(f'/api/projects/{s.project_id}/resume').json()
    assert data['action']=='select'
    s.save_focus(ResearchFocus(question=q.item_id,plan=second.item_id),s.load().revision)
    assert client.post(f'/api/projects/{s.project_id}/resume').json()['action']=='plan_ready'


def test_legacy_plan_linked_to_two_questions_is_not_ready(unified):
    _,client,s,_=unified
    q,p=branch(s);q2=item(s,'question','Another question')
    workspace=s.load();workspace.focus.question=q.item_id;workspace.focus.plan=p.item_id
    next(i for i in workspace.items if i.item_id==p.item_id).related_ids.append(q2.item_id)
    s.store.save_kv(WORKSPACE_KEY,workspace.model_dump(mode='json'))
    for suffix in ('workspace','status','dashboard'):
        data=client.get(f'/api/projects/{s.project_id}/{suffix}').json()
        assert not data['workflow']['flow']['planReady']
        assert data['workflow']['continuation']['action']=='review_record'


def test_generated_method_requires_linking_and_explicit_revision_does_not_run_pipeline(unified,monkeypatch):
    api,client,s,manager=unified
    q,p=branch(s)
    # Unconfirmed generated material is not automatically adopted.
    workspace=s.load();workspace.items=[];workspace.focus.question='';workspace.focus.plan=''
    s.store.save_kv(WORKSPACE_KEY,workspace.model_dump(mode='json'))
    data=client.get(f'/api/projects/{s.project_id}/workspace').json()
    assert not data['workflow']['flow']['planReady'] and data['workspace']['items']==[]
    assert data['workflow']['continuation']['action']=='materials'
    old_experiments=[e.model_dump() for e in s.store.list_experiments()]
    monkeypatch.setattr(api,'_generate_method_doc',lambda *a:'独立方案修订夹具，尚未执行。')
    class InlineThread:
        def __init__(self,*,target,**kw): self.target=target
        def start(self): self.target()
    monkeypatch.setattr(api.threading,'Thread',InlineThread)
    monkeypatch.setattr(api,'Pipeline',lambda *a,**kw:pytest.fail('method revision must not launch pipeline'))
    api.regenerate_method_doc(s.project_id)
    assert api._METHOD_REGEN_JOBS[s.project_id]['status']=='done'
    assert len(s.store.list_artifacts(ArtifactType.METHOD_DOC))==2
    assert s.store.list_artifacts(ArtifactType.PAPER_DRAFT)==[]
    assert [e.model_dump() for e in s.store.list_experiments()]==old_experiments
    assert s.load().items==[]


def test_cli_uses_default_product_policy_without_internal_switch(bounded,monkeypatch):
    from runtime import cli
    calls=[]
    class ProductPipeline(Pipeline):
        def _build_stage_graph(self,stage): return graph_factory(calls)(stage)
    monkeypatch.setattr(cli,'_load_env',lambda:None)
    monkeypatch.setattr(cli,'get_config',lambda:bounded)
    monkeypatch.setattr(cli,'Pipeline',ProductPipeline)
    monkeypatch.setenv('SRA_EXECUTION_POLICY','internal_legacy')
    args=SimpleNamespace(no_dry_run=False,project_id='cli',stop_before='writing',topic='offline fixture',force_writing=False,verbose=False)
    assert cli.cmd_run(args)==0
    assert calls==LifecycleStage.ordered()[:3]
    assert inspect_boundary(bounded.paths,'cli')['stage_statuses']['experiment']=='not_started'


def test_cli_history_status_and_resume_are_read_only(bounded,monkeypatch):
    from runtime import cli
    legacy_project(bounded,LifecycleStage.WRITING)
    monkeypatch.setattr(cli,'_load_env',lambda:None)
    monkeypatch.setattr(cli,'get_config',lambda:bounded)
    before=fingerprint_tree(bounded.paths.projects)
    args=SimpleNamespace(project_id='legacy')
    assert cli.cmd_status(args)==0
    assert cli.cmd_resume(args)==1
    assert fingerprint_tree(bounded.paths.projects)==before


def test_topic_confirmation_handoff_uses_capped_default_pipeline(bounded,monkeypatch):
    from web import api
    from runtime.pipeline import PipelineResult
    state=api.ProjectState(project_id='handoff',topic='interest',domain='ai',created_at='fixture')
    monkeypatch.setattr(api,'_CONFIG',bounded);monkeypatch.setattr(api,'_PROJECTS',{'handoff':state})
    bridge=api.HumanCallbackBridge()
    monkeypatch.setattr(bridge,'make_callback',lambda *a,**kw:lambda req:HumanResponse(action='continue',text='ok'))
    monkeypatch.setattr(api,'_BRIDGE',bridge)
    calls=[]
    class HandoffPipeline(Pipeline):
        def run_topic_discovery(self,**kw):
            self.start_project(kw['project_id'],'offline interest','ai')
            return PipelineResult(project_id=kw['project_id'],status='completed',extra={'selected_topic':'offline selected topic'})
        def _build_stage_graph(self,stage): return graph_factory(calls)(stage)
    class Worker:
        def __init__(self,*,target,args,**kw): self.target=target;self.args=args;self.started=False
        def start(self): self.started=True
        def is_alive(self): return self.started
    monkeypatch.setattr(api,'Pipeline',HandoffPipeline)
    monkeypatch.setattr(api.threading,'Thread',Worker)
    api._run_topic_discovery_thread('handoff','interest',False)
    assert state.thread.started and state.thread.target==api._run_pipeline_thread
    state.thread.target(*state.thread.args)
    assert state.status=='stopped',state.error
    assert calls==LifecycleStage.ordered()[:3]
    assert inspect_boundary(bounded.paths,'handoff')['stage_statuses']['experiment']=='not_started'


@pytest.mark.parametrize('stage',[LifecycleStage.EXPERIMENT,LifecycleStage.WRITING])
def test_historical_web_entries_and_exports_keep_data_unchanged(bounded,monkeypatch,stage):
    session,ctx=legacy_project(bounded,stage)
    from web import api
    from web.workspace_api import build_workspace_router
    state=api.ProjectState(project_id='legacy',topic='original',created_at='fixture',status='stopped',domain='ai')
    monkeypatch.setattr(api,'_CONFIG',bounded);monkeypatch.setattr(api,'_PROJECTS',{'legacy':state})
    monkeypatch.setattr(api,'_BRIDGE',api.HumanCallbackBridge())
    api._BRIDGE._pending['legacy']={'context':{'node_id':ctx.current_node_id}}
    monkeypatch.setattr(api.threading,'Thread',lambda *a,**k:pytest.fail('historical worker created'))
    app=FastAPI()
    for suffix,endpoint in [('run',api.run_project),('resume',api.resume_project_task),('run-topic-discovery',api.run_topic_discovery),('human-response',api.submit_human_response)]:
        app.add_api_route('/api/projects/{project_id}/'+suffix,endpoint,methods=['POST'])
    for suffix,endpoint in [('status',api.get_status),('dashboard',api.get_dashboard),('experiments',api.list_experiments)]:
        app.add_api_route('/api/projects/{project_id}/'+suffix,endpoint,methods=['GET'])
    app.add_api_route('/api/projects/{project_id}/download/{artifact_type}',api.download_artifact,methods=['GET'])
    app.include_router(build_workspace_router(bounded,api._require_project,workflow_provider=lambda state:api._workspace_workflow(state,include_materials=True)))
    client=TestClient(app);before=fingerprint_tree(bounded.paths.projects)
    for suffix,body in [('run',{}),('resume',{}),('run-topic-discovery',{'interest':'new topic'})]:
        response=client.post('/api/projects/legacy/'+suffix,json=body)
        assert response.status_code==200 and response.json()['action']=='view_only'
    for action in ('continue','rollback','abort'):
        assert client.post('/api/projects/legacy/human-response',json={'action':action}).status_code==422
    for suffix in ('status','dashboard','workspace'):
        data=client.get('/api/projects/legacy/'+suffix).json()
        assert data['product_boundary']['restricted'] and data['workflow']['continuation']['action']=='view_only'
    assert client.get('/api/projects/legacy/experiments').json()['experiments'][0]['metrics']=={'fixture':0.1}
    for suffix in ('download/paper-draft','download/experiment-results','download/full-report','workspace/export?format=json'):
        assert client.get('/api/projects/legacy/'+suffix).status_code==200
    assert 'Historical manuscript bytes' in client.get('/api/projects/legacy/download/paper-draft').text
    assert fingerprint_tree(bounded.paths.projects)==before
    assert state.status=='stopped' and state.thread is None

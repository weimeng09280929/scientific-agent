"""Independent-review repairs: real app, disposable identities and offline work."""
from copy import deepcopy
import threading

import pytest

from tests.test_local_resource_protection import env, isolated, project, messages
from core.resource_limits import RESOURCES, ACTOR
from core.workspace import conversation_lock
from core.workspace.conversations import ConversationService, ConversationStorageError
from core.workspace.schema import GuideDraft, ItemInput
from core.workspace.service import WorkspaceService
from core.knowledge import Paper


def plan_fixture(api,p):
    db=api._CONFIG.paths.project_db(p[0]);s=WorkspaceService(p[0],db)
    s.store.save_paper(Paper(paper_id='review-paper',title='Offline review source',abstract='Software fixture only'))
    q=s.add_item(ItemInput(kind='question',title='Reviewed question',content='核对范围',paper_ids=['review-paper']),s.load().revision).items[-1]
    return db,s,{'request_id':'start-fault','content':'请生成计算研究方案',
        'plan_request':{'method':'computational','question_id':q.item_id},'wait_for_completion':False}


@pytest.mark.parametrize('stage',['context','construct','start'])
def test_unstarted_plan_releases_lease_even_if_failure_record_cannot_be_written(env,monkeypatch,stage):
    root,a,b,api=env;p=project(a);db,s,body=plan_fixture(api,p)
    from core.workspace.plans import rule_plan
    import core.workspace.conversations as module
    calls=[]
    monkeypatch.setattr('web.workspace_api.generate_guidance',lambda registry,area,domain,text,ctx,purpose:
        (calls.append(1) or rule_plan(ctx)) if ctx.get('plan_generation',{}).get('allowed') else GuideDraft(reply='离线账号可继续操作。'))
    before=s.load(include_chat=False).model_dump(mode='json')
    real_thread=threading.Thread
    class BrokenThread(real_thread):
        def __init__(self,*args,**kwargs):
            if stage=='construct' and type(getattr(kwargs.get('target'),'__self__',None)).__name__=='Context':
                raise RuntimeError('controlled thread construction failure')
            super().__init__(*args,**kwargs)
        def start(self):
            if stage=='start' and type(getattr(self._target,'__self__',None)).__name__=='Context':
                raise RuntimeError('controlled thread start failure')
            return super().start()
    attempts=[]
    def fail_record(*args,**kwargs):attempts.append(1);raise ConversationStorageError('controlled failed write')
    with monkeypatch.context() as faults:
        faults.setattr(module.threading,'Thread',BrokenThread)
        faults.setattr(ConversationService,'_fail',fail_record)
        if stage=='context':faults.setattr(module,'copy_context',lambda:(_ for _ in ()).throw(RuntimeError('controlled context failure')))
        response=a.post(messages(p),json=body)
    assert response.status_code==503 and '失败状态未能保存' in response.json()['detail']
    assert attempts==[1] and calls==[]
    assert not RESOURCES.snapshot()['heavy'] and not conversation_lock.is_held(db,p[1])
    state=a.get(messages(p).replace('/messages','/requests/start-fault')).json()
    assert state['request_id']=='start-fault' and state['status']=='interrupted'
    assert state['attempt']==1 and state['failure']['can_retry']
    assert len(state['messages'])==1 and state['suggestions']==[]
    assert s.load(include_chat=False).model_dump(mode='json')==before
    # The same real account can act again. Recover the existing request after
    # storage returns; do not reset its identity, attempt counter or user message.
    other=project(a)
    assert a.post(messages(other),json={'content':'确认恢复后的操作','request_id':'other'}).status_code==200
    recovered=a.post(messages(p).replace('/messages','/requests/start-fault/retry'),json={'wait_for_completion':True})
    assert recovered.status_code==200,recovered.text
    assert recovered.json()['attempt']==2 and len(calls)==1
    assert [m['role'] for m in recovered.json()['messages']]==['user','assistant']
    assert recovered.json()['suggestions'] and not recovered.json()['suggestions'][0]['applied_item_id']
    assert s.load(include_chat=False).model_dump(mode='json')==before


def test_worker_really_started_before_start_ack_error_keeps_ownership(env,monkeypatch):
    root,a,b,api=env;p=project(a);db,s,body=plan_fixture(api,p)
    from core.workspace.plans import rule_plan
    entered,release=threading.Event(),threading.Event();workers=[];calls=[]
    def generate(registry,area,domain,text,ctx,purpose):
        calls.append(1);entered.set();assert release.wait(15);return rule_plan(ctx)
    monkeypatch.setattr('web.workspace_api.generate_guidance',generate)
    real_thread=threading.Thread
    class LostAck(real_thread):
        def start(self):
            result=super().start()
            if type(getattr(self._target,'__self__',None)).__name__=='Context':
                workers.append(self);assert entered.wait(8);raise RuntimeError('controlled lost start acknowledgement')
            return result
    import core.workspace.conversations as module
    with monkeypatch.context() as fault:
        fault.setattr(module.threading,'Thread',LostAck)
        response=a.post(messages(p),json=body)
    try:
        assert response.status_code==503 and '可能仍在处理' in response.json()['detail']
        assert sum(RESOURCES.snapshot()['heavy'].values())==1 and conversation_lock.is_held(db,p[1])
        other=project(a)
        assert a.post(messages(other),json={'content':'不能叠加','request_id':'blocked'}).status_code==429
        assert calls==[1]
    finally:
        release.set()
        for worker in workers:worker.join(10)
    state=a.get(messages(p).replace('/messages','/requests/start-fault')).json()
    assert state['status']=='completed' and state['attempt']==1
    assert [m['role'] for m in state['messages']]==['user','assistant']
    assert not RESOURCES.snapshot()['heavy'] and not conversation_lock.is_held(db,p[1])


def test_real_api_rejected_request_can_use_reviewed_revision_without_new_identity(env,monkeypatch):
    root,a,b,api=env;p=project(a)
    calls=[]
    monkeypatch.setattr('web.workspace_api.generate_guidance',lambda *args:calls.append(1) or GuideDraft(reply='离线核对后的回复。'))
    workspace=f'/api/projects/{p[0]}/workspace';old=a.get(workspace).json()['workspace']
    body={'content':'保留文字和原编号','request_id':'rejected-revision','expected_revision':old['revision']}
    original=deepcopy(body);actor=ACTOR.set(a.get('/api/auth/me').json()['user']['id'])
    try:lease=RESOURCES.acquire()
    finally:ACTOR.reset(actor)
    try:assert a.post(messages(p),json=body).status_code==429
    finally:lease.close()
    newer=a.put(workspace+'/brief',json={'expected_revision':old['revision'],'brief':{'resources':'只有 CPU，没有 GPU'}})
    assert newer.status_code==200
    assert a.post(messages(p),json=body).status_code==409 and calls==[]
    state_url=messages(p).replace('/messages','/requests/rejected-revision')
    assert a.get(state_url).status_code==404
    body['expected_revision']=newer.json()['workspace']['revision']
    result=a.post(messages(p),json=body)
    assert result.status_code==200 and result.json()['request_id']==original['request_id']
    assert a.post(messages(p),json=original).status_code==200 # accepted identity wins over stale CAS
    assert calls==[1] and [m['role'] for m in result.json()['messages']]==['user','assistant']

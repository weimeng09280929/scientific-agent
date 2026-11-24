"""Confirmed Brief reuse through the real API/provider edge, no live models."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import json
import sqlite3
from threading import Barrier, Event

import pytest
from fastapi.testclient import TestClient
from core.workspace.schema import GuideDraft
from tests.research_partner_fixture import sample as fixture, create, send, workspace, brief
from tests.local_backup_fixture import expect, app_at, login


@pytest.fixture
def sample(tmp_path):
    with fixture(tmp_path/'source') as value: yield value


def change(s, **updates):
    w=workspace(s)
    return expect(s['a'].put(f"/api/projects/{s['pid']}/workspace/brief", json={
        'expected_revision':w['revision'],'brief':{**w['brief'],**updates}}))['workspace']


def rows(s):
    with closing(sqlite3.connect(s['api']._CONFIG.paths.project_db(s['pid']))) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {t:db.execute('SELECT * FROM '+t).fetchall() for t in
                ('sra_chat_messages','sra_chat_requests','sra_call_audit','sra_chat_suggestions') if t in tables}


def test_read_cancel_semantics_and_full_snapshot_save_clear(sample):
    s=sample;w=workspace(s);before=deepcopy(w)
    # Reads and showing an editor do not call the write endpoint.
    assert workspace(s)==before and not s['calls']
    w=change(s,goal='比较检索依据',background='已学 Python',resources='只有 CPU',time_budget='四周')
    assert w['brief_revision']==w['revision']
    old=deepcopy(w);w=change(s,resources='')
    assert w['brief']=={**old['brief'],'resources':''}
    assert w['brief_revision']==old['revision']+1
    stale=s['a'].put(f"/api/projects/{s['pid']}/workspace/brief",json={'expected_revision':old['revision'],'brief':old['brief']})
    assert stale.status_code==409 and workspace(s)==w and not s['calls']


def test_new_conversation_receives_current_background_not_other_chat(sample):
    s=sample;w=change(s,goal='检索比较',background='Python 入门',resources='只有 CPU',time_budget='四周')
    first=create(s);expect(send(s,first,'第一段对话的私有正文，不检索。',rid='first'))
    second=create(s);expect(send(s,second,'只解释当前背景，不保存新信息。',rid='second'))
    ctx=s['calls'][-1]['context']
    assert ctx['brief']==w['brief'] and ctx['history']==[]
    assert ctx['project_background']['source']=='Workspace.brief'
    assert '第一段对话的私有正文' not in json.dumps(ctx,ensure_ascii=False)
    before=workspace(s);expect(send(s,second,'我现在有 GPU，先讨论不同条件，不保存。',rid='temporary'))
    assert workspace(s)==before and s['calls'][-1]['context']['brief']==before['brief']


def test_clearing_does_not_resurrect_old_chat_as_current_condition(sample):
    from core.workspace.plans import user_basis
    s=sample;change(s,resources='只有 CPU，没有 GPU',time_budget='四周')
    cid=create(s);expect(send(s,cid,'我只有 CPU，没有 GPU，期限四周。',rid='old'))
    saved=rows(s);old_call=deepcopy(s['calls'][0]);w=change(s,resources='',time_budget='')
    assert rows(s)==saved  # No rewriting messages, audit, requests, or drafts.
    expect(send(s,cid,'只解释本次实际采用的背景，不保存。',rid='after-clear'))
    ctx=s['calls'][-1]['context'];assert ctx['brief']['resources']==ctx['brief']['time_budget']==''
    assert len(ctx['history'])==2 and all(m['background_outdated'] for m in ctx['history'])
    assert '没有 GPU' in ctx['history'][0]['content']  # History remains readable.
    assert '没有 GPU' not in user_basis(ctx,'解释依据')
    assert not any('没有 GPU' in x for x in ctx['research_route']['user_statements'])
    assert s['calls'][0]==old_call and ctx['project_background']['revision']==w['brief_revision']
    expect(send(s,cid,'我本轮临时只有 CPU，先讨论，不保存。',rid='current'))
    assert '本轮临时只有 CPU' in user_basis(s['calls'][-1]['context'],'我本轮临时只有 CPU')
    assert workspace(s)['brief']['resources']==''


def test_inflight_request_keeps_submitted_basis_when_background_changes(sample):
    s=sample;change(s,resources='只有 CPU');cid=create(s);entered=Event();finish=Event()
    def provider(ctx,text):
        entered.set();assert finish.wait(10);return GuideDraft(reply='离线事件控制的回复。')
    s['control']['result']=provider
    with ThreadPoolExecutor(1) as pool:
        running=pool.submit(send,s,cid,'只解释当前资源，不检索。','inflight')
        try:
            assert entered.wait(10);captured=deepcopy(s['calls'][0]);change(s,resources='')
            assert s['calls'][0]['context']['brief']['resources']=='只有 CPU'
        finally:finish.set()
        expect(running.result(10))
    assert len(s['calls'])==1 and s['calls'][0]==captured
    with closing(sqlite3.connect(s['api']._CONFIG.paths.project_db(s['pid']))) as db:
        row=db.execute('SELECT based_on_revision FROM sra_chat_requests WHERE request_id=?',('inflight',)).fetchone()
    assert row[0]==captured['context']['revision']<workspace(s)['brief_revision']


def test_two_simultaneous_saves_use_atomic_original_revision(sample):
    s=sample;w=change(s,goal='原目标');barrier=Barrier(2)
    def put(goal):
        barrier.wait();return s['a'].put(f"/api/projects/{s['pid']}/workspace/brief",json={
            'expected_revision':w['revision'],'brief':{**w['brief'],'goal':goal}})
    with ThreadPoolExecutor(2) as pool:responses=list(pool.map(put,['标签页一','标签页二']))
    assert sorted(r.status_code for r in responses)==[200,409]
    assert workspace(s)['revision']==w['revision']+1 and not s['calls']


def test_changed_background_marks_formal_records_not_note_or_draft(sample):
    s=sample;change(s,goal='比较检索方法',resources='只有 CPU');cid=create(s);w=workspace(s)
    q=expect(s['a'].post(f"/api/projects/{s['pid']}/workspace/items",json={'expected_revision':w['revision'],
        'item':{'kind':'question','title':'检索方法的比较问题','content':'比较两种已有检索方法。','paper_ids':['abstract-only']}}))['workspace']['items'][-1]
    draft=expect(send(s,cid,'请生成计算研究方案','plan',plan_request={'method':'computational','question_id':q['item_id']}))['suggestions'][0]
    formal=expect(s['a'].post(f"/api/projects/{s['pid']}/conversations/{cid}/suggestions/{draft['suggestion_id']}/apply",json={}))['item']
    pending=expect(send(s,cid,'请修订当前计算研究方案','revision',plan_request={'method':'computational','supersedes_id':formal['item_id']}))['suggestions'][0]
    w=workspace(s);expect(s['a'].post(f"/api/projects/{s['pid']}/workspace/items",json={'expected_revision':w['revision'],'item':{'kind':'note','title':'笔记','content':'不自动成为背景。'}}))
    before=rows(s);count=len(s['calls']);w=change(s,time_budget='四周')
    assert all(i['needs_review'] for i in w['items'] if i['kind'] in ('question','plan'))
    assert all(not i['needs_review'] for i in w['items'] if i['kind']=='note')
    assert next(i for i in w['items'] if i['kind']=='plan')['plan_version']==1
    assert rows(s)==before and len(s['calls'])==count
    proposals=expect(s['a'].get(f"/api/projects/{s['pid']}/conversations/{cid}/suggestions"))['suggestions']
    assert next(p for p in proposals if p['suggestion_id']==pending['suggestion_id'])['applied_item_id'] is None


def test_project_account_and_moved_history_boundaries(sample):
    s=sample;change(s,resources='只有 CPU');brief(s,pid=s['target'],resources='目标项目专用 GPU')
    cid=create(s);expect(send(s,cid,'我只有 CPU，先讨论，不保存。','origin'))
    url=f"/api/projects/{s['pid']}/conversations/{cid}"
    pre=expect(s['a'].get(url+'/move-precheck',params={'target_project_id':s['target']}))
    expect(s['a'].post(url+'/move',json={'target_project_id':s['target'],'request_id':'move-background','expected_token':pre['token'],'acknowledge_history':True}))
    origin=s['pid'];s['pid']=s['target'];before=workspace(s)
    expect(send(s,cid,'只解释目标项目资源，不保存。','destination'))
    ctx=s['calls'][-1]['context'];assert ctx['brief']['resources']=='目标项目专用 GPU' and ctx['history']==[]
    assert ctx['conversation']['prior_project_history'][0]['origin_project_id']==origin
    assert workspace(s)==before
    for client,pid in ((s['b'],s['target']),(s['a'],s['foreign'])):
        assert client.get(f'/api/projects/{pid}/workspace').status_code==404
        assert client.put(f'/api/projects/{pid}/workspace/brief',json={'expected_revision':0,'brief':{}}).status_code==404
    with TestClient(s['api'].app) as anonymous:assert anonymous.get(f"/api/projects/{s['pid']}/workspace").status_code==401


def test_legacy_read_does_not_write_or_change_material_fingerprint(sample):
    from core.workspace.conversations import ConversationService
    from core.workspace.service import KEY
    s=sample;db=s['api']._CONFIG.paths.project_db(s['pid']);service=ConversationService(s['pid'],db)
    with closing(sqlite3.connect(db)) as conn:
        value=json.loads(conn.execute('SELECT value FROM kv_store WHERE key=?',(KEY,)).fetchone()[0]);value.pop('brief_revision',None)
        conn.execute('UPDATE kv_store SET value=? WHERE key=?',(json.dumps(value),KEY));conn.commit()
    raw=db.read_bytes();before=service.material_state()['fingerprint']
    assert workspace(s)['brief_revision']==0 and db.read_bytes()==raw
    # Default metadata does not invalidate stored material identities.
    assert service.material_state()['fingerprint']==before


def test_restart_and_cold_backup_preserve_background_and_isolation(tmp_path):
    from core.local_backup import backup, restore
    source=tmp_path/'source'
    with fixture(source) as s:
        w=change(s,goal='恢复验收课题',background='已学 Python',resources='只有 CPU',time_budget='')
        pid,target=s['pid'],s['target'];cid=create(s);expect(send(s,cid,'恢复前已保存的正文。','saved'))
        messages=rows(s);account_cookie=dict(s['a'].cookies);calls=len(s['calls'])
    with app_at(source) as (api,state),TestClient(api.app) as a:
        login(a,0);assert expect(a.get(f'/api/projects/{pid}/workspace'))['workspace']==w
        assert state['guidance_calls']==0
    backup(source,tmp_path/'backup','accounts',confirm_stopped=True);restore(tmp_path/'backup',tmp_path/'restored')
    with app_at(tmp_path/'restored') as (api,state),TestClient(api.app) as a,TestClient(api.app) as b,TestClient(api.app) as old:
        for name,value in account_cookie.items():old.cookies.set(name,value)
        assert old.get(f'/api/projects/{pid}/workspace').status_code==401
        login(a,0);login(b,1)
        assert expect(a.get(f'/api/projects/{pid}/workspace'))['workspace']==w
        assert b.get(f'/api/projects/{pid}/workspace').status_code==404
        assert expect(a.get(f'/api/projects/{target}/workspace'))['workspace']['brief']['resources']==''
        assert rows({'api':api,'pid':pid})==messages and state['guidance_calls']==0 and calls==1

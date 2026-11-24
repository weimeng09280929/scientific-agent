"""Audit P2s: cross-references and version-local content status, offline only."""
from copy import deepcopy
import json
import pytest

from core.knowledge import KnowledgeStore
from core.workspace.conversation_schema import ConversationSend
from core.workspace.schema import GuideDraft
from core.research_tasks.report_sources import (
    freeze_sources, reference_index, research_points, supported_limitations,
    delivery_reference_ids, format_prose,
)
from core.research_tasks.synthesis import synthesize_report, SynthesisValidationError, report_markdown
from core.research_tasks.limits import SearchLimits
from tests.test_research_delivery_closure import candidate, report_for, setup, start
from tests.test_project_navigation import navigation
from tests.test_conversation_home import home, tree
from tests.test_research_tasks import make_paper

A, B, C = 'cand-sameprefix-111111', 'cand-sameprefix-222222', 'cand-other-333333'


def cross_report():
    return {'mode':'model', 'representative':[{'paper_id':A,'point':f'与 paper:{B} 的条件不同；需核对 paper:{B}。'}],
            'methods_relations':'', 'limitations':[{'point':f'需与 paper:{B} 一起核查', 'paper_ids':[A]},
                                                  {'point':f'另一项 paper:{C} 的疑问', 'paper_ids':[C]}]}


def test_cross_reference_union_and_only_displayed_content_is_cited():
    r=freeze_sources(cross_report(),[candidate(p) for p in (A,B,C)],'model')
    assert [(s['paper_id'],s['number']) for s in reference_index(r)]==[(A,1),(B,2),(C,3)]
    assert research_points(r)==[(r['representative'][0]['point'],[A,B])]
    assert supported_limitations(r)[0]['paper_ids']==[A,B]
    assert delivery_reference_ids(r)==[A,B]  # second limitation is not shown
    body,refs=report_markdown({'report':r}).split('## 参考文献与阅读范围')
    assert '引用未解析' not in body and 'paper:' not in body
    assert '[1]' in body and '[2]' in body and all(p in refs for p in (A,B,C))
    assert format_prose('paper:'+B+'-extra',reference_index(r))=='[引用未解析，需核对]'


@pytest.mark.parametrize('field',['point','limitation','relation'])
def test_unknown_explicit_reference_rejected_in_each_content_field(field):
    r=report_for([A])
    if field=='point':r['representative'][0]['point']='与 paper:cand-unknown 不同'
    elif field=='limitation':r['limitations'][0]['point']='与 paper:cand-unknown 不同'
    else:r['methods_relations']='与 paper:cand-unknown 不同'
    with pytest.raises(SynthesisValidationError):
        synthesize_report('test',[],[candidate(A)],[],SearchLimits(),lambda *a:json.dumps(r))


def test_metadata_cross_reference_cannot_be_delivered_as_content_evidence():
    r=freeze_sources(cross_report(),[candidate(A),candidate(B,abstract='',has_abstract=False),candidate(C)],'model')
    assert not research_points(r) and not delivery_reference_ids(r)
    assert [p['paper_ids'] for p in supported_limitations(r)]==[[C]]
    text=report_markdown({'report':r})
    assert '此表述须回查原始资料' in text and '仅元信息' in text
    # A different valid point must not make the rejected cross-reference or
    # its limitation eligible to re-enter the actual reply citation set.
    r['representative'].append({'paper_id':C,'point':'另一条可按摘要核对的要点'})
    assert delivery_reference_ids(r)==[C]


def test_legacy_numbering_preserved_with_cross_references_appended_read_only():
    r=freeze_sources(cross_report(),[candidate(p) for p in (A,B,C)],'model')
    r['source_snapshot']['schema_version']=1;r['source_snapshot'].pop('reference_order')
    # Old renderer gave A=1,C=2. Newly recognized B must not shift C.
    before=deepcopy(r)
    assert [(p['paper_id'],p['number']) for p in reference_index(r)]==[(A,1),(C,2),(B,3)]
    report_markdown({'report':r});assert r==before


def install_cross_model(svc):
    original=svc.model_fn
    def model(step,payload):
        if step!='synthesize':return original(step,payload)
        data=json.loads(payload['prompt'].split('候选文献：',1)[1].split('\n来源执行情况',1)[0])
        a,b=[p['paper_id'] for p in data[:2]]
        return json.dumps({'representative':[{'paper_id':a,'point':f'与 paper:{b} 的条件不同。'}],
                           'methods_relations':'','limitations':[{'paper_ids':[a],'point':f'需核对 paper:{b} 的条件。'}]})
    svc.model_fn=model


def test_saved_message_history_export_cross_references_and_resume_stay_consistent(navigation,monkeypatch):
    n=navigation;chat,cid,svc,control,calls=setup(n,monkeypatch,partial=True)
    control.default=lambda name,q,seq:[make_paper(seq*10+i,q,doi=f'10.0000/{seq}-{i}',abstract='Provided abstract') for i in (1,2)]
    install_cross_model(svc)
    task=start(svc,cid);assert task['status']=='partial',task.get('error')
    reply=next(m for m in chat.messages(cid)['messages'] if m.get('research_task_id'))
    ids=reply['cited_paper_ids'];assert len(ids)==2
    assert [(p['paper_id'],p['number']) for p in reply['report_citations']]==[(ids[0],1),(ids[1],2)]
    assert '[2]' in reply['content'] and '引用未解析' not in reply['content']
    original=svc.report_document(task['task_id'],1)
    assert all(p in original for p in ids)
    seen=[]
    def guidance(area,text,context,purpose):
        seen.append(context['history']);return GuideDraft(reply='仅核对已有编号。'),'rules'
    chat.send(cid,ConversationSend(content='解释[1]与[2]',request_id='followup-cross'),'offline',guidance)
    mapping=next(m['report_reference_map'] for m in seen[0] if m.get('report_reference_map'))
    assert [p['paper_id'] for p in mapping['citations']]==ids
    paper=chat.shared.store.list_papers()[0];paper.title='LATER LIBRARY';KnowledgeStore(chat.db_path).save_paper(paper)
    from core.research_tasks import sources
    monkeypatch.setitem(sources.SOURCE_FUNCS,'arxiv',control('arxiv'))
    svc.retry(task['task_id'],resume=True)
    assert svc.get(task['task_id'])['current_version']==2
    assert svc.report_document(task['task_id'],1)==original and 'LATER LIBRARY' not in original
    before=tree(n.root)
    assert n.client.get(f'/api/projects/{chat.project_id}/research-tasks/{task["task_id"]}/export?version=1').text==original
    svc.get(task['task_id']);assert tree(n.root)==before


@pytest.mark.parametrize('kind,expected',[
    ('model','synthesized'),('rules','rules_catalog_only'),('metadata','metadata_catalog_only'),
    ('failed','synthesis_failed_catalog_only'),('partial-failed','synthesis_failed_catalog_only')])
def test_directory_preview_download_and_task_content_status_agree(navigation,monkeypatch,kind,expected):
    n=navigation;chat,cid,svc,control,calls=setup(n,monkeypatch,metadata=kind=='metadata',
        truncated='failed' in kind,partial=kind=='partial-failed')
    if kind=='rules':svc.model_fn=None
    task=start(svc,cid);assert task['status'] in ('completed','partial'),task.get('error')
    assert task['content_status']==expected
    before=tree(n.root);usage=(len(calls),len(control.calls))
    material=n.client.get(f'/api/projects/{chat.project_id}/workspace/materials?kind=reports').json()['entries']
    download=n.client.get(f'/api/projects/{chat.project_id}/exports').json()['entries']
    entry=next(e for e in material if e.get('task_id')==task['task_id'])
    other=next(e for e in download if e['key']==entry['key'])
    assert entry['content_status']==other['content_status']==expected
    assert entry['status_label']==other['status_label']==task['status_label']==task['report_versions'][0]['status_label']
    for suffix in ('','&catalog=true'):
        text=n.client.get(entry['url']+suffix).text
        assert entry['status_label'] in text
    assert task['status_label'] in task['report_markdown']
    assert (kind=='partial-failed')==('有来源失败' in entry['status_label'])
    assert tree(n.root)==before and usage==(len(calls),len(control.calls))


def test_successful_resume_does_not_relabel_old_failed_content(navigation,monkeypatch):
    n=navigation;chat,cid,svc,control,calls=setup(n,monkeypatch,partial=True,truncated=True)
    task=start(svc,cid);old=svc.report_document(task['task_id'],1)
    assert task['content_status']=='synthesis_failed_catalog_only'
    from core.research_tasks import sources
    monkeypatch.setitem(sources.SOURCE_FUNCS,'arxiv',control('arxiv'))
    # Replace the offline malformed response with a valid offline response.
    install_cross_model(svc)
    svc.retry(task['task_id'],resume=True)
    task=svc.get(task['task_id']);assert task['current_version']==2 and task['content_status']=='synthesized'
    before=tree(n.root)
    entries=n.client.get(f'/api/projects/{chat.project_id}/workspace/materials?kind=reports').json()['entries']
    entries={e['version']:e for e in entries if e.get('task_id')==task['task_id']}
    assert entries[1]['content_status']=='synthesis_failed_catalog_only'
    assert '模型内容整理未成功' in entries[1]['status_label']
    assert entries[2]['content_status']=='synthesized'
    assert svc.report_document(task['task_id'],1)==old
    assert entries[1]['status_label'] in old and entries[2]['status_label'] in svc.report_document(task['task_id'],2)
    assert tree(n.root)==before


def test_legacy_content_mode_is_version_owned_not_current_or_planning_mode(navigation,monkeypatch):
    n=navigation;chat,cid,svc,control,calls=setup(n,monkeypatch,truncated=True)
    task=start(svc,cid)
    with svc._connection(True) as conn:
        row=svc._row(conn,task['task_id']);versions=json.loads(row['report_versions'])
        # Old task mode sometimes described a model being configured, while
        # the actual saved report correctly said its output was rules-only.
        versions[0]['mode']='model'
        conn.execute('UPDATE sra_research_tasks SET mode=?,report_versions=? WHERE task_id=?',('model',json.dumps(versions),task['task_id']))
    before=tree(n.root)
    task=svc.get(task['task_id'])
    entries=n.client.get(f'/api/projects/{chat.project_id}/workspace/materials?kind=reports').json()['entries']
    entry=next(e for e in entries if e.get('task_id')==task['task_id'])
    assert task['content_status']==entry['content_status']=='synthesis_failed_catalog_only'
    assert entry['status_label']==task['report_versions'][0]['status_label']==task['status_label']
    assert entry['status_label'] in svc.report_document(task['task_id'],1)
    assert tree(n.root)==before
    # No version-local evidence: do not borrow a current task's failure note.
    with svc._connection(True) as conn:
        versions[0].pop('synthesis_note');versions[0]['report'].pop('mode');versions[0].pop('mode')
        conn.execute('UPDATE sra_research_tasks SET report_versions=? WHERE task_id=?',(json.dumps(versions),task['task_id']))
    before=tree(n.root)
    entry=next(e for e in n.client.get(f'/api/projects/{chat.project_id}/workspace/materials?kind=reports').json()['entries'] if e.get('task_id')==task['task_id'])
    assert entry['content_status']=='unknown' and '内容状态未记录' in svc.report_document(task['task_id'],1)
    assert tree(n.root)==before

"""Real SDK parsing on an offline transport; scientific schema stays strict."""
import json
from pathlib import Path
import pytest
from core.llm.base import LLMStructureError
from core.llm.providers.openai_provider import OpenAIProvider
from core.llm.registry import LLMRegistry
from core.llm.task_router import TaskRouter
from core.workspace.schema import GuideDraft
from tests.test_real_workflow_reliability import sdk_client,model_response
from tests.research_partner_fixture import sample,create,send,workspace
from tests.local_backup_fixture import expect

@pytest.mark.parametrize('case',['optional_price','missing_plan','action_url','root_price','too_many_options'])
def test_optional_isolation_and_strict_repair(tmp_path,case):
    payload={'reply':'离线解析样本','suggested_replies':[{'label':'讨论','content':'比较条件','price':'SECRET_SENTINEL'}]}
    if case=='missing_plan':payload['suggestions']=[{'kind':'plan','title':'草案','content':'摘要','plan':{}}]
    if case=='action_url':payload['actions']=[{'type':'open_settings','url':'https://not-allowed.invalid'}]
    if case=='root_price':payload['price']='SECRET_SENTINEL'
    if case=='too_many_options':payload['suggested_replies']*=5
    calls=[]
    def handler(request):
        calls.append(json.loads(request.content))
        return model_response(module,request,content=json.dumps(payload,ensure_ascii=False))
    client,module=sdk_client(handler)
    provider=OpenAIProvider('offline-key',base_url='https://offline.invalid/v1',max_retries=0,http_client=client)
    registry=LLMRegistry(TaskRouter.load(Path('config/tasks.yaml')),{'deepseek':provider})
    try:
        if case=='optional_price':
            result=registry.structured_output('design_method_formalize',GuideDraft,prompt='offline')
            assert result.reply=='离线解析样本' and result.suggested_replies==[] and result._omitted_reply_options==1
            assert len(calls)==1
        else:
            with pytest.raises(LLMStructureError) as error:registry.structured_output('design_method_formalize',GuideDraft,prompt='offline')
            assert len(calls)==2 and error.value.structure_issues
            repair=calls[1]['messages'][-1]['content']
            assert '一次结构修复' in repair and 'SECRET_SENTINEL' not in repair
            assert 'SECRET_SENTINEL' not in str(error.value)
    finally:client.close()


def test_price_option_does_not_fail_actual_website_or_repeat_calls(tmp_path):
    with sample(tmp_path) as s:
        cid=create(s);before=workspace(s)
        s['control']['result']={'reply':'模型正文原样保留','suggested_replies':[{'label':'比较','content':'讨论条件','price':'untrusted'}]}
        result=expect(send(s,cid,'看看有哪些待确认内容。'))
        assert result['messages'][-1]['content']=='模型正文原样保留'
        assert result['messages'][-1]['delivery_warnings'] and len(s['calls'])==1
        assert any(e['name']=='reply_option_schema_omitted' for e in result['call_audit']['entries'])
        assert workspace(s)==before
        expect(send(s,cid,'看看有哪些待确认内容。'))
        assert len(s['calls'])==1

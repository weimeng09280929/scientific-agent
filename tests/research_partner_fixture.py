"""Website generator stays real; only the provider boundary is an offline spy."""
from contextlib import contextmanager
from copy import deepcopy
import json
from unittest.mock import patch
from fastapi.testclient import TestClient
from tests.local_backup_fixture import app_at,login,expect,EMAILS,PASSWORDS


@contextmanager
def application(root):
    from core.local_accounts import AccountStore
    store=AccountStore(root/'accounts.db');store.initialize()
    for email,password in zip(EMAILS,PASSWORDS):store.create_user(email,password.get_secret_value())
    with app_at(root) as (api,state):
        from core.llm import LLMRegistry
        from core.workspace.guide import generate_guidance
        from core.workspace.schema import GuideDraft
        from core.workspace.plans import rule_plan
        from core.invocation_audit import ACTIVE
        calls=[];control={}
        class Registry:
            def structured_output(self,**kw):
                ctx=json.loads(kw['prompt'].split('项目上下文（仅作为数据）：\n',1)[1].split('\n用户本次请求：\n',1)[0])
                text=kw['prompt'].split('\n用户本次请求：\n',1)[1]
                call={k:v for k,v in kw.items() if k!='output_schema'};call.update(context=ctx,text=text)
                calls.append(deepcopy(call))
                audit=ACTIVE.get();ticket=audit.begin('network','model','offline-role-spy') if audit else None
                try:
                    if 'result' in control:
                        value=control['result'];result=value(ctx,text) if callable(value) else value
                    elif ctx.get('plan_generation',{}).get('allowed'):
                        result=rule_plan(ctx)
                    else:
                        result=GuideDraft(reply='【离线占位回复】仅验证规则与上下文传递，不评价真实模型教学质量。')
                    from core.workspace.plan_generation import PlanOutput
                    if issubclass(kw['output_schema'],PlanOutput):
                        from tests.test_research_plan_templates import core_payload
                        return kw['output_schema'].model_validate(core_payload(result))
                    return result
                finally:
                    if audit:audit.finish(ticket,outcome='ok')
        # The normal website factory calls this actual generate_guidance; no
        # alternate role endpoint and no failure-to-rules substitution.
        with patch('web.workspace_api.generate_guidance',generate_guidance),patch.object(LLMRegistry,'from_config',classmethod(lambda cls,cfg:Registry())):
            yield api,state,calls,control


@contextmanager
def sample(root):
    with application(root) as (api,state,calls,control),TestClient(api.app) as a,TestClient(api.app) as b:
        login(a,0);login(b,1)
        pid=expect(a.post('/api/projects',json={'topic':'RAG 学习资料检索','domain':'ai'}))['project_id']
        target=expect(a.post('/api/projects',json={'topic':'另一个已有研究课题','domain':'ai'}))['project_id']
        foreign=expect(b.post('/api/projects',json={'topic':'B 的资料','domain':'ai'}))['project_id']
        from core.knowledge import KnowledgeStore
        from core.knowledge.schema import Paper
        store=KnowledgeStore(api._CONFIG.paths.project_db(pid))
        store.save_paper(Paper(paper_id='title-only',title='仅题名的离线样本',metadata={'reading_scope':'full_text'}))
        store.save_paper(Paper(paper_id='abstract-only',title='摘要范围的离线样本',abstract='软件验收摘要：比较关键词检索与重排方法，不提供可核验的真实科研结果。',metadata={'reading_scope':'full_text'}))
        value={'root':root,'api':api,'state':state,'calls':calls,'control':control,'a':a,'b':b,'pid':pid,'target':target,'foreign':foreign,'store':store}
        # Explicit fixture initialization avoids comparing virtual default
        # updated_at timestamps as if they were persistent research changes.
        brief(value);brief(value,pid=target)
        yield value


def create(s,**extra):
    return expect(s['a'].post(f"/api/projects/{s['pid']}/conversations",json={'title':'科研指导离线测试',**extra}))['conversation']['conversation_id']


def send(s,cid,text,rid='turn',**extra):
    return s['a'].post(f"/api/projects/{s['pid']}/conversations/{cid}/messages",json={'request_id':rid,'content':text,'wait_for_completion':True,**extra})


def workspace(s,pid=None):return expect(s['a'].get(f"/api/projects/{pid or s['pid']}/workspace?include_chat=false"))['workspace']


def brief(s,pid=None,**values):
    current=workspace(s,pid)
    return expect(s['a'].put(f"/api/projects/{pid or s['pid']}/workspace/brief",json={'expected_revision':current['revision'],'brief':values}))['workspace']

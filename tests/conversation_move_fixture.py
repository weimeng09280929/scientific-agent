"""Disposable accounts and ordinary/related chats for real move acceptance."""
from contextlib import contextmanager
from unittest.mock import patch
from fastapi.testclient import TestClient
from tests.local_backup_fixture import app_at, login, expect, EMAILS, PASSWORDS


@contextmanager
def fixture(root):
    from core.local_accounts import AccountStore
    store=AccountStore(root/'accounts.db');store.initialize()
    for email,password in zip(EMAILS,PASSWORDS):store.create_user(email,password.get_secret_value())
    with app_at(root) as (api,state):
        import web.workspace_api
        original=web.workspace_api.generate_guidance
        observed=[]
        def generate(registry,area,domain,text,context,purpose):
            observed.append({'brief':context['brief'],'history':context['history'],'conversation':context['conversation'],'text':text})
            return original(registry,area,domain,text,context,purpose)
        with patch('web.workspace_api.generate_guidance',generate),TestClient(api.app) as a,TestClient(api.app) as b:
            login(a,0);login(b,1)
            start_body={'title':'同名项目','domain':'ai','conversation_title':'同名讨论','request_id':'move-original-start'}
            first=expect(a.post('/api/conversations/start',json=start_body));source,cid=first['project_id'],first['conversation_id']
            target=expect(a.post('/api/projects',json={'topic':'同名项目','domain':'ai'}))['project_id']
            third=expect(a.post('/api/projects',json={'topic':'另一个已有项目','domain':'ai'}))['project_id']
            foreign=expect(b.post('/api/projects',json={'topic':'B 私有项目','domain':'ai'}))['project_id']
            for pid,resources in ((source,'只有 CPU，没有 GPU'),(target,'已有一块 GPU，仅作临时软件验收')):
                w=expect(a.get(f'/api/projects/{pid}/workspace'))['workspace']
                expect(a.put(f'/api/projects/{pid}/workspace/brief',json={'expected_revision':w['revision'],'brief':{'goal':'软件验收临时研究目标','resources':resources}}))
            chat=f'/api/projects/{source}/conversations/{cid}'
            for index,text in enumerate(('只有 CPU，没有 GPU，请先解释怎样讨论研究范围。','接着解释怎样核对研究条件，不生成草案。')):
                expect(a.post(chat+'/messages',json={'request_id':'text-'+str(index),'content':text,'wait_for_completion':True}))
            yield {'root':root,'api':api,'state':state,'a':a,'b':b,'source':source,'target':target,'third':third,'foreign':foreign,'cid':cid,'start_body':start_body,'observed':observed}

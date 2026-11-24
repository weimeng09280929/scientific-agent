"""Temporary >20-paper sample; no real data or network."""
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from tests.research_partner_fixture import application
from tests.local_backup_fixture import login,expect

def main():
    root=Path(tempfile.mkdtemp(prefix='sra-paper-library-'))
    with application(root) as (api,state,calls,control):
        with TestClient(api.app) as a:
            login(a,0);pid=expect(a.post('/api/projects',json={'topic':'25篇文献验收','domain':'ai'}))['project_id']
            from core.knowledge import KnowledgeStore
            from core.knowledge.schema import Paper
            store=KnowledgeStore(api._CONFIG.paths.project_db(pid))
            for n in range(25):
                store.save_paper(Paper(paper_id=f'offline-{n}',title=f'离线资料{n:02}',abstract=f'软件验收摘录{n}，不是科研结论。',
                                      metadata={"text_origin":"author_abstract"} if n==0 else {}))
            assert len(expect(a.get(f'/api/projects/{pid}/papers'))['papers'])==25
        import uvicorn
        server=uvicorn.Server(uvicorn.Config(api.app,host='127.0.0.1',port=8099,log_level='warning'))
        @api.app.get('/__papers__/state')
        def status():return {'pid':pid,'root':str(root),'calls':len(calls),'state':state}
        @api.app.post('/__papers__/shutdown')
        def stop():server.should_exit=True;return {'stopping':True}
        try:server.run()
        finally:(root/'stopped.txt').write_text('stopped',encoding='utf-8')
if __name__=='__main__':main()

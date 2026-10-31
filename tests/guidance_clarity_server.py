"""Temporary local clarity evidence; no product routes or external calls added."""
import argparse
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from tests.research_partner_fixture import application
from tests.local_backup_fixture import login,expect
from core.workspace.schema import GuideDraft,ReplyOption

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args();root=a.root.resolve()
    if root.parent!=Path(tempfile.gettempdir()).resolve() or not root.name.startswith('sra-clarity-'):raise ValueError('Temporary root required')
    root.mkdir(exist_ok=False)
    with application(root) as (api,state,calls,control):
        with TestClient(api.app) as c:
            login(c,0);pid=expect(c.post('/api/projects',json={'topic':'跨领域科研理解验收','domain':'materials'}))['project_id']
            cid=expect(c.post(f'/api/projects/{pid}/conversations',json={'title':'术语与选项讨论'}))['conversation']['conversation_id']
            from core.knowledge import KnowledgeStore
            from core.knowledge.schema import Paper
            KnowledgeStore(api._CONFIG.paths.project_db(pid)).save_paper(Paper(paper_id='offline-materials',title='离线材料比较样本',abstract='用于界面与上下文验收，不提供真实研究结论。'))
        def answer(ctx,text):
            return GuideDraft(reply='【人工预期示例，非真实模型质量证据】比较时，另一个因素可能同时影响分组和结果，让我们误判原因，这叫混杂因素。例如比较两种材料的强度时，若加工温度也不同，就不能把差异全归于材料。',
                suggested_replies=[ReplyOption(label=label,content=content,explanation=why) for label,content,why in [
                    ('先理解概念','只解释混杂因素，再给一个材料研究例子。','适合首次接触；用具体例子理解含义。'),
                    ('比较条件','请比较哪些条件可能影响材料强度，先不保存。','列出可能影响比较的条件，得到待核对清单。'),
                    ('讨论评价方式','请讨论如何评价材料强度，未知条件先不确定。','比较可讨论的评价方式，不执行实验。')]],actions=[{'type':'open_settings'}])
        control['result']=answer
        import uvicorn
        server=uvicorn.Server(uvicorn.Config(api.app,host='127.0.0.1',port=8099,log_level='warning'))
        @api.app.get('/__clarity__/state')
        def view():return {'pid':pid,'cid':cid,'calls':calls,'state':state,'root':str(root)}
        @api.app.post('/__clarity__/shutdown')
        def stop():server.should_exit=True;return {'stopping':True}
        try:server.run()
        finally:(root/'stopped.txt').write_text('stopped',encoding='utf-8')
if __name__=='__main__':main()

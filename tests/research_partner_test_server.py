"""Disposable site with manually authored examples behind the real generator."""
import argparse
from pathlib import Path
import tempfile
from fastapi.testclient import TestClient
from tests.research_partner_fixture import application,create,brief
from tests.local_backup_fixture import login,expect
from core.workspace.schema import GuideDraft,SuggestedItem
from core.workspace.plans import rule_plan


def examples(ctx,text):
    """Human-authored fixtures; NOT a claim about a real model's reasoning."""
    tag='【离线回答示例，不代表真实模型质量】'
    if ctx.get('plan_generation',{}).get('allowed'):
        return rule_plan(ctx).model_copy(update={'reply':tag+'方法草案尚未采纳，条件与依据须继续核对。'})
    if '暂停' in text:return GuideDraft(reply=tag+'好的，先停在这里。已有记录不变，也不追加操作。')
    if '一句话' in text:return GuideDraft(reply=tag+'基线就是用来比较的已有方法。')
    if '基线' in text or '消融' in text:
        return GuideDraft(reply=tag+'\n\n基线就是用来比较的已有方法。比如你想讨论文档检索的改进，可以先把现有的关键词匹配方法作为参照，再考虑其他方法的差异。这里的例子帮助理解，并不是项目已经获得的结果。\n\n消融实验可以理解为：去掉一个部件，看它是否确实有作用。例如把“有重排”和“没有重排”作为待设计的比较，才能讨论重排部件是否值得保留。其他资料和比较条件也需要保持一致，不能把多个变化都归因于一个部件。\n\n这些概念可以用于讨论方法草案，但本轮只解释，不启动实验、不把建议保存成正式方案。')
    if '我现在有一块GPU' in text:
        return GuideDraft(reply=tag+'已保存的是“只有 CPU，没有 GPU”，而这次你说“我现在有一块GPU”。这是资源条件的变化，还是先讨论有 GPU 的假设？本次新说法尚未覆盖正式条件。',questions=['这是已经变化的资源，还是讨论中的假设？'])
    if '标题' in text:return GuideDraft(reply=tag+'只有题名，还不足以判断这篇论文的方法、结果或核心贡献。可以先核对摘要中作者实际提出的问题，再决定是否需要正文；这里没有读过该论文全文。',cited_paper_ids=['title-only'])
    if '摘要' in text:return GuideDraft(reply=tag+'这份离线摘要只描述了关键词检索与重排的比较思路。它没有提供完整实验与局限，不能据此声称已经核对全文，也不能把摘要未说的内容当成创新空白。',cited_paper_ids=['abstract-only'])
    if '创新' in text:return GuideDraft(reply=tag+'把两个模块结合是一个可讨论的设想，还不能宣布创新成立。需要与最接近的已有工作比较，核对它们是否已经做过类似组合，以及差异是否真的回答了有价值的问题。当前材料不足以给出确定结论。')
    if '候选研究问题' in text:
        return GuideDraft(reply=tag+'可以先讨论“在相同资料条件下，重排会怎样改变关键词匹配的结果”。这是待你选择的候选问题，不是已确认的新颖贡献。',cited_paper_ids=['abstract-only'],suggestions=[SuggestedItem(kind='question',title='同一资料条件下的检索比较',content='如何在相同资料条件下比较关键词匹配与重排方法？',paper_ids=['abstract-only'])])
    if ctx.get('brief',{}).get('resources'):
        resources=ctx['brief']['resources'];time=ctx['brief'].get('time_budget') or '期限待确认'
        return GuideDraft(reply=tag+f'可以从已经保存的方向继续。项目记录的条件是“{resources}”和“{time}”，无需重新填写入门问卷。先讨论一个范围较小的比较问题；其他未知条件继续保持待确认。本次建议没有修改正式材料。')
    return GuideDraft(reply=tag+'不必现在就确定完整课题。可以先从一个熟悉的应用，或一种想理解的方法开始；这能帮助决定后面需要核对哪些资料。不确定也可以先选一个小例子讨论。',questions=['你想先聊一个应用，还是先理解一种方法？也可以说不确定。'])


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--port',type=int,default=8098);args=p.parse_args();root=args.root.resolve()
    if args.port in (8001,8002,8077) or root.parent!=Path(tempfile.gettempdir()).resolve() or not root.name.startswith('sra-partner-'):
        raise ValueError('Requires a fresh disposable root and non-product loopback port')
    root.mkdir(exist_ok=False)
    with application(root) as (api,state,calls,control):
        control['result']=examples
        with TestClient(api.app) as a,TestClient(api.app) as b:
            login(a,0);login(b,1);sample={}
            for key,topic,mode in [('broad','人工智能','beginner'),('known','RAG 学习资料检索','direct'),('origin','原课题示例','direct'),('destination','目标课题示例','direct')]:
                pid=expect(a.post('/api/projects',json={'topic':topic,'domain':'ai'}))['project_id']
                view={'a':a,'pid':pid};brief(view,**({'goal':'比较学习资料检索方法','resources':'只有 CPU，没有 GPU','time_budget':'四周'} if key in ('known','origin') else {'resources':'已有一块 GPU（离线验收）'} if key=='destination' else {}))
                cid=create(view,guidance_mode=mode);sample[key]={'pid':pid,'cid':cid}
            from core.knowledge import KnowledgeStore
            from core.knowledge.schema import Paper
            store=KnowledgeStore(api._CONFIG.paths.project_db(sample['known']['pid']))
            store.save_paper(Paper(paper_id='title-only',title='仅有题名的离线资料',metadata={'reading_scope':'metadata_only'}))
            store.save_paper(Paper(paper_id='abstract-only',title='仅有摘要的离线资料',abstract='离线样例：比较关键词检索与重排，不提供真实科研结果。',metadata={'reading_scope':'abstract'}))
            source=sample['origin'];url=f"/api/projects/{source['pid']}/conversations/{source['cid']}"
            expect(a.post(url+'/messages',json={'request_id':'prior-source','content':'我只有CPU，没有GPU，先讨论资源。','wait_for_completion':True}))
            target=sample['destination'];pre=expect(a.get(url+'/move-precheck',params={'target_project_id':target['pid']}));assert pre['can_move']
            expect(a.post(url+'/move',json={'target_project_id':target['pid'],'request_id':'prepare-origin','expected_token':pre['token'],'acknowledge_history':True}))
            sample['moved']={'pid':target['pid'],'cid':source['cid']}
            sample['foreign']=expect(b.post('/api/projects',json={'topic':'B 私有资料','domain':'ai'}))['project_id']
        seed_calls=len(calls);calls.clear()
        import uvicorn
        server=uvicorn.Server(uvicorn.Config(api.app,host='127.0.0.1',port=args.port,log_level='warning',timeout_graceful_shutdown=5))
        @api.app.get('/__partner__/state')
        def fixture_state():return {'sample':sample,'calls':calls,'state':state,'seed_offline_calls':seed_calls,'root':str(root),'examples_only':True}
        @api.app.post('/__partner__/shutdown')
        def shutdown():server.should_exit=True;return {'stopping':True}
        print('Offline example instance on loopback port '+str(args.port),flush=True);server.run()


if __name__=='__main__':main()

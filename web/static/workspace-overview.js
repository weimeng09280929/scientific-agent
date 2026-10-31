/* Projections only: all records, versions and task states belong to the server. */
(() => {
    'use strict';
    const E=(tag,text,cls='')=>{const n=document.createElement(tag);n.textContent=text || '';n.className=cls;return n;};
    const btn=(text,fn)=>{const b=E('button',text,'btn btn-secondary btn-sm');b.type='button';b.onclick=fn;return b;};
    const labels={processing:'处理中',completed:'已完成',failed:'失败',interrupted:'已中断',running:'运行中',queued:'排队中',partial:'部分完成',cancelled:'已取消'};
    async function get(url){const r=await fetch((window.SRA_API_BASE||'')+url),d=await r.json();if(!r.ok)throw new Error(d.detail || `读取失败 ${r.status}`);return d;}
    function mount(host,kind,app){
        const pid=app.getRoute().projectId,root=E('section','','workspace-unified');host.append(root);
        const active=()=>root.isConnected && app.getRoute().projectId===pid;
        if(!pid){root.append(E('p','请先选择项目，或从新研究开始。查看此页不会创建项目。'));return;}
        let offset=0,busy=false,timer=null,signature='';
        const title={reports:'研究现状',plans:'研究方案',history:'历史材料',activities:'任务活动'}[kind];
        root.append(E('h2',title,'page-title'));
        const state=E('p','','muted'),list=E('div'),feedback=E('p','','ch-warning');
        const next=btn('加载更多',()=>load(true));next.hidden=true;
        root.append(state,btn('返回当前科研对话',()=>app.navigate({page:'dashboard'})),btn('刷新',()=>load()),feedback,list,next);
        async function preview(entry,body){
            body.replaceChildren(E('p','正在读取所选版本…'));
            try{const r=await fetch((window.SRA_API_BASE||'')+entry.url);if(!r.ok)throw new Error('该版本暂不可读取');const text=await r.text();if(!active())return;body.replaceChildren();await window.SRA_DOCUMENT.render(body,text);}
            catch(e){if(active())body.replaceChildren(E('p',e.message,'ch-warning'));}
        }
        function entryCard(entry){
            const card=E('article','','card');card.dataset.materialKey=entry.key;
            card.append(E('h3',entry.title,'card-title'),E('p',[entry.label,entry.version!=null?'版本 '+entry.version:'未标数字版本',entry.status_label,entry.created_at?new Date(entry.created_at).toLocaleString('zh-CN'):entry.saved_at?'保存于 '+new Date(entry.saved_at).toLocaleString('zh-CN'):''].filter(Boolean).join(' · '),'muted small'));
            const body=E('div'),details=E('details');details.append(E('summary','查看此版本'),body);details.ontoggle=()=>{if(details.open&&!body.childNodes.length)preview(entry,body);};card.append(details);
            if(entry.key.startsWith('plan:'))card.append(btn('审阅正式记录',()=>window.SRA_CONVERSATIONS.showPlan(pid,entry.key.slice(5))));
            if(entry.conversation_id)card.append(btn('返回来源对话',()=>app.navigate({projectId:pid,conversationId:entry.conversation_id,page:'dashboard'})));
            const download=E('a','下载此版本（MD）','btn btn-secondary btn-sm');download.href=(window.SRA_API_BASE||'')+entry.url;download.setAttribute('download','');card.append(download);return card;
        }
        async function load(append=false,poll=false){
            if(busy||!active())return;clearTimeout(timer);busy=true;if(!poll)feedback.textContent='正在读取已保存的材料与状态…';next.disabled=true;if(!append)offset=0;
            try{
                const cap=await get('/api/conversation-capabilities');if(!cap.workspace_read_model)throw new Error('当前后端尚未加载统一材料与任务视图，请更新后端后重试；不会自动重启或恢复任务。');
                const data=await get(`/api/projects/${encodeURIComponent(pid)}/${kind==='activities'?'activities':'workspace/materials?kind='+kind}${kind==='activities'?'?':'&'}limit=20&offset=${offset}`);if(!active())return;
                window.dispatchEvent(new CustomEvent('sra:workspace-loaded',{detail:{projectId:pid,workflow:data.workflow,counts:data.workflow.counts,material_counts:data.material_counts}}));
                state.textContent=(data.project_name || '当前项目')+' · 已确认进展：'+data.progress.label+'。'+data.progress.notice;
                const live=kind==='activities'&&data.activities.some(x=>['processing','running','queued'].includes(x.status));
                if(live)timer=setTimeout(()=>load(false,true),2000);
                const fresh=JSON.stringify(data);
                if(poll&&signature===fresh){offset=data.next_offset;feedback.textContent='';return;}
                signature=fresh;
                if(!append)list.replaceChildren();
                if(kind==='activities'){
                    for(const item of data.activities){
                        const card=E('article','','card');card.dataset.activityId=item.id;
                        card.append(E('h3',item.title,'card-title'),E('p',item.status_label||labels[item.status]||item.status),E('p',item.created_at?new Date(item.created_at).toLocaleString('zh-CN'):'' ,'muted small'));
                        if(item.error){const detail=E('details');detail.append(E('summary','查看失败原因'),E('p',item.error));card.append(detail);}
                        card.append(btn(['failed','interrupted'].includes(item.status)?'回到原对话核对并重试':'返回对应对话',()=>app.navigate({projectId:pid,conversationId:item.conversation_id,page:'dashboard'})));
                        if(item.result_url){const a=E('a','下载已保存报告','btn btn-secondary btn-sm');a.href=item.result_url;a.setAttribute('download','');card.append(a);}list.append(card);
                    }
                    const old=data.historical_execution;
                    if(!append && old && (old.node_id || old.restore_gate || old.status && old.status!=='created')){
                        const d=E('details');d.append(E('summary','旧流水线历史状态（与本轮对话任务分开）'),E('p',(old.status || '已有历史检查点')+'；只读查看不会重新启动任务。'),E('p',old.hint || '已有材料可查看和导出。'));
                        const cursor=E('details');cursor.append(E('summary','查看历史执行位置'),E('p',[old.stage,old.node_id].filter(Boolean).join(' · ')));d.append(cursor);
                        d.append(btn('查看历史产物',()=>mountHistory()));
                        if(old.action==='confirm')d.append(btn('查看待确认事项',()=>app.navigate({page:'human'})));
                        list.append(d);
                    }
                    if(!data.activities.length&&!append)list.append(E('p','尚无本项目对话请求或检索任务。已有材料仍可查看，不需要启动旧流水线。'));
                }else{
                    if(kind==='plans'&&!append){
                        const q=data.workflow.flow.question;list.append(E('p',q?'当前问题'+(q.needs_review?'（待复核）':'')+'：'+q.title:'尚未确认当前问题；可以从对话讨论或已有候选中明确采纳。'));
                    }
                    for(const entry of data.entries)list.append(entryCard(entry));
                    for(const draft of data.drafts || []){
                        const card=E('article','','card');card.dataset.suggestionId=draft.suggestion_id;card.append(E('h3',draft.title+' · 未采纳草案','card-title'),E('p',draft.basis_changed?'依据已变化或缺少快照，需复核，不能直接采纳。':'待用户审阅；尚未成为正式方案。'),btn('回原对话审阅草案',()=>window.SRA_CONVERSATIONS.showProposal(pid,draft.conversation_id,draft.suggestion_id)));list.append(card);
                    }
                    if(!data.entries.length&&!(data.drafts||[]).length&&!append)list.append(E('p',kind==='reports'?'尚无已保存的研究现状报告。入库论文与研究现状报告不是同一材料。':'尚无此类材料。不会为填满页面生成内容。','muted'));
                    if(kind==='plans'&&!append)list.append(btn('查看历史方法、实验记录与稿件',()=>mountHistory()));
                }
                const cursors=[data.next_offset,data.draft_next_offset].filter(n=>n!=null);offset=cursors.length?Math.min(...cursors):null;next.hidden=offset===null;feedback.textContent='';
            }catch(e){if(active())feedback.textContent=e.message;}finally{busy=false;next.disabled=false;}
        }
        function mountHistory(){root.remove();mount(host,'history',app);}
        load();
    }
    function mountOverview(host,app){
        const route=app.getRoute(),pid=route.projectId,cid=route.conversationId;
        const root=E('section','','project-overview'),heading=E('h1','正在读取项目…'),body=E('div'),feedback=E('div');
        root.setAttribute('aria-label','项目总览');feedback.setAttribute('role','status');
        const active=()=>root.isConnected&&app.getRoute().projectId===pid&&app.getRoute().page==='project-overview';
        root.append(E('p','项目总览','po-eyebrow'),heading,feedback,body);host.append(root);
        if(!pid){heading.textContent='尚未选择项目';feedback.append(E('p','请从侧栏选择已有项目。浏览总览不会建立项目或对话。'));return;}
        const base='/api/projects/'+encodeURIComponent(pid);
        let busy=false;
        const section=(title,key)=>{const n=E('section','','card po-section');n.dataset.section=key;n.append(E('h2',title,'card-title'));return n;};
        const go=page=>app.navigate({projectId:pid,page});
        const excerpt=text=>text.length>240?text.slice(0,240)+'…':text;
        async function load(){
            if(busy||!active())return;busy=true;root.dataset.loaded='false';body.replaceChildren();feedback.replaceChildren(E('p','正在读取已有材料…','muted'));
            try{
                const [data,plans,notes,recent,original]=await Promise.all([
                    get(base+'/workspace?include_chat=false'),get(base+'/workspace/materials?kind=plans&limit=3'),
                    get(base+'/notes'),get(base+'/conversations?limit=1'),cid?get(base+'/conversations/'+encodeURIComponent(cid)):Promise.resolve(null)
                ]);
                if(!active())return;
                if(data.workspace?.project_id!==pid||plans.project_id!==pid||original&&original.conversation?.project_id!==pid)throw new Error('项目与材料归属不一致，请重新选择项目。');
                const w=data.workspace,flow=data.workflow.flow,brief=w.brief||{},question=flow.question;
                heading.textContent=data.project.display_name||data.project.topic;
                const intro=section('项目简介','intro');
                if(brief.goal?.trim())intro.append(E('p','已保存的研究目标：'+excerpt(brief.goal)));
                else if(question)intro.append(E('p',(question.needs_review?'已保存的问题（待复核）：':'已确认的研究问题：')+question.title));
                else intro.append(E('p','项目简介尚未完善，可以在对话中逐步明确研究目标与条件。','muted'));
                body.append(intro);
                const background=section('项目背景','background');
                window.SRA_PROJECT_BACKGROUND.view(background,{projectId:pid,workspace:w,app,onSaved:()=>{if(active())return load();}});body.append(background);
                const status=section('当前研究状态','status');
                status.append(E('p',plans.progress.label,'po-state'));
                status.append(E('p',flow.planReady?'已明确采纳研究问题和方案；可以继续审阅、讨论修改。':question?(question.needs_review?'正式问题需要复核；已有草案不代表方案已经采纳。':'已明确采纳研究问题；方案草案仍须单独审阅和采纳。'):plans.drafts.length?'已有方案草案，尚未明确采纳当前研究问题与方案。':recent.conversations.length?'正在讨论研究目标与问题；对话中的候选内容须明确采纳。':'尚未开始讨论；浏览总览不代表研究阶段已经完成。'));
                status.append(E('p',flow.next?.text||data.next_step,'muted'),E('p',plans.progress.notice,'muted small'));body.append(status);
                const materials=section('已有资料与成果','materials'),grid=E('div','','po-materials');
                function card(title,description,label,action){const n=E('article','','po-material');n.append(E('h3',title),E('p',description),btn(label,action));grid.append(n);return n;}
                card('文献与资料',`已入库 ${data.papers.length} 篇。数量只表示已保存，不表示全部已阅读。`,'查看文献与资料',()=>go('papers'));
                const savedNotes=(notes.notes||[]).length+w.items.filter(i=>i.kind==='note'&&i.status==='active').length;
                card('已保存笔记',`已保存 ${savedNotes} 条笔记。聊天正文不会自动成为笔记。`,'查看笔记',()=>go('notes'));
                const q=card('研究问题',question?(question.needs_review?'正式记录 · 待复核：':'已明确采纳：')+question.title:'当前尚未明确采纳研究问题；候选内容可返回对话核对。','查看研究问题',()=>go('gaps'));
                if(question&&question.content)q.append(E('p',excerpt(question.content),'muted small'));
                const formal=card('正式方案',plans.entries.length?'以下是已保存的正式版本；与未采纳草案分开。':'尚无正式方案。','查看全部方案与版本',()=>go('experiments'));
                for(const item of plans.entries){const row=E('div','','po-record');row.append(E('p',item.title),E('small',[item.label||'正式记录',item.version!=null?'版本 '+item.version:'',item.status_label].filter(Boolean).join(' · ')),btn('审阅正式方案',()=>window.SRA_CONVERSATIONS.showPlan(pid,item.key.slice(5))));formal.append(row);}
                if(plans.next_offset!=null)formal.append(E('p','仅列出最近 3 条，全部版本见方案目录。','muted small'));
                const drafts=card('方案草案',plans.drafts.length?'未采纳草案；不会替代正式方案。':'当前没有未采纳方案草案。','查看草案目录',()=>go('experiments'));
                for(const item of plans.drafts){const row=E('div','','po-record');row.append(E('p',item.title+' · 未采纳草案'),E('small',item.basis_changed?'依据需复核，不能直接采纳。':'待用户审阅和明确采纳。'),btn('回原对话审阅草案',()=>window.SRA_CONVERSATIONS.showProposal(pid,item.conversation_id,item.suggestion_id)));drafts.append(row);}
                if(plans.draft_next_offset!=null)drafts.append(E('p','仅列出最近 3 条，其他草案见方案目录。','muted small'));
                card('研究现状报告',`已保存 ${data.material_counts.research_reports} 份报告，内容状态与阅读范围见原报告。`,'查看研究现状报告',()=>go('research-report'));
                materials.append(grid);body.append(materials);
                const next=section('继续研究','continue'),conversation=original?.conversation||recent.conversations[0];
                if(conversation){next.append(E('p','继续讨论：'+conversation.title),btn(cid?'返回刚才的对话':'继续最近对话',()=>app.navigate({projectId:pid,conversationId:conversation.conversation_id,page:'dashboard'})));}
                else next.append(E('p','尚无对话。可以在本项目开始一次新讨论；只有明确发送才保存新会话。','muted'));
                next.append(btn('在本项目新建对话',()=>app.navigate({projectId:pid,conversationId:null,page:'dashboard'})),btn('刷新项目总览',()=>load()));body.append(next);
                feedback.replaceChildren();root.dataset.loaded='true';
                window.dispatchEvent(new CustomEvent('sra:workspace-loaded',{detail:{projectId:pid,workflow:data.workflow,counts:data.counts,material_counts:data.material_counts}}));
            }catch(e){if(active()){heading.textContent='项目总览';feedback.replaceChildren(E('p','项目总览暂时无法读取','ch-warning'),E('p',e.message,'muted'),btn('重新读取项目总览',()=>load()));}}
            finally{busy=false;}
        }
        load();
    }
    window.SRA_WORKSPACE_VIEW={mount,mountOverview};
})();

/* Saved teaching packages use real project chat/material APIs, never a model. */
(() => {
    "use strict";
    const views=new WeakMap();
    const keep={get(k){try{return JSON.parse(localStorage.getItem(k));}catch(_){return null;}},set(k,v){localStorage.setItem(k,JSON.stringify(v));},remove(k){localStorage.removeItem(k);}};
    function attach(ctx){
        const {v,E,button,current,request,app,refreshMessages,refreshMaterials,notice,fail}=ctx;
        views.set(v,ctx);
        const open=button("案例体验 · 离线可用",()=>directory(ctx),"btn btn-secondary");open.dataset.teaching="directory";
        v.r.welcome.querySelector("small").replaceWith(open);
        const box=E("section","ch-teaching");box.hidden=true;box.setAttribute("aria-label","科研教学进度");v.r.teaching=box;
        v.root.querySelector(".ch-heading").after(box);
        ctx.act=async(option,retryOriginal=false)=>{
            if(ctx.busy || v.busy || v.remoteBusy || !current(v))return;
            const key=`sra.case.choice.${v.pid}/${v.cid}`;
            const saved=keep.get(key);
            if(saved && !retryOriginal && (saved.option_id!==option.id || saved.expected_version!==ctx.state.state_version)){
                notice(v,"上次选择的响应尚未核对。请先点击‘核对原选择’，不会另发新选择。");return;
            }
            const body=saved || {request_id:crypto.randomUUID(),expected_version:ctx.state.state_version,option_id:option.id};keep.set(key,body);ctx.busy=true;decorate(v);
            try{
                await request(ctx.base+"/choices",{method:"POST",body});keep.remove(key);if(!current(v))return;
                await refreshMessages();await refreshMaterials();
            }catch(e){
                // 409/422 are definitive rejection; a lost reply must retain the original ID.
                if([409,422,404].includes(e.status))keep.remove(key);
                fail(v,e);await refresh(ctx).catch(()=>{});
            }finally{ctx.busy=false;if(current(v))decorate(v);}
        };
        ctx.retryChoice=async()=>{const saved=keep.get(`sra.case.choice.${v.pid}/${v.cid}`);if(saved)await ctx.act({id:saved.option_id},true);};
    }
    async function directory(ctx){
        const {E,button,request,app}=ctx;
        const dialog=E("dialog","case-dialog"),body=E("div","case-catalog");
        dialog.append(E("h2","","用一个案例体验科研方案设计"),E("p","","预先整理的资料与讲解；无需模型密钥或实时检索。只形成教学方案，不执行实验。"),body,button("返回对话",()=>dialog.close()));document.body.append(dialog);dialog.addEventListener("close",()=>dialog.remove());dialog.showModal();
        try{
            const capabilities=await request("/api/conversation-capabilities");
            if(capabilities.teaching_experience!==1){body.textContent="当前后端尚未加载四阶段教学体验。请更新／重启后端后再开始新案例；已有项目和历史对话仍可查看。页面不会自动重启服务。";return;}
            const data=await request("/api/teaching-cases");
            for(const c of data.cases){
                const card=E("article","case-card");card.append(E("h3","",c.title || c.case_id));
                if(!c.available){card.append(E("p","",c.reason));body.append(card);continue;}
                card.append(E("p","",`${c.method==="computational"?"计算研究":"实验研究设计（不执行）"} · 资料 v${c.version} · 整理 ${c.organized_on}`),E("p","",c.learning_goals.join(" · ")));
                const status=E("p","case-status");let busy=false;
                const start=button("开始体验",async()=>{
                    if(busy)return;busy=true;start.disabled=true;
                    const key=`sra.case.start.${c.case_id}/${c.version}`,rid=keep.get(key) || crypto.randomUUID();keep.set(key,rid);
                    try{const result=await request("/api/teaching-cases/start",{method:"POST",body:{request_id:rid,case_id:c.case_id,version:c.version}});keep.remove(key);window.dispatchEvent(new CustomEvent("sra:projects-changed"));if(!dialog.isConnected || !ctx.current(ctx.v))return;dialog.close();app.navigate({projectId:result.project_id,conversationId:result.conversation_id,page:"dashboard"});}
                    catch(e){status.textContent="未确认体验创建成功："+e.message+"；再次点击会核对原请求，不重复创建。";}
                    finally{busy=false;start.disabled=false;}
                });card.append(start,status);body.append(card);
            }
        }catch(e){body.textContent="案例目录暂不可用："+e.message;}
    }
    async function refresh(ctx){
        const {v,current,request}=ctx;if(!current(v))return;
        if(!v.material?.teaching){ctx.state=null;v.teachingState=null;decorate(v);return;}
        const cid=v.cid || v.material.teaching.conversation_id;
        ctx.base=`/api/projects/${encodeURIComponent(v.pid)}/conversations/${encodeURIComponent(cid)}/teaching`;
        const state=await request(ctx.base,{signal:v.reads.signal});if(!current(v))return;ctx.state=state;decorate(v);
    }
    function decorate(v){
        const ctx=views.get(v);if(!ctx || !ctx.current(v))return;
        const box=v.r.teaching, teaching=v.material?.teaching;
        v.root.classList.toggle("is-teaching",!!teaching);
        if(!teaching){box.hidden=true;return;}
        document.getElementById('ch-project-new').textContent='当前教学体验选项';
        if(!v.cid){
            const {E,button}=ctx;v.r.form.hidden=true;v.r.summary.hidden=true;box.hidden=false;
            const transfer=button('带着这个思路开始自己的研究',()=>fork(ctx));transfer.disabled=!ctx.state;
            box.replaceChildren(E('p','','这是离线教学项目。请继续原案例，或带着参考资料开始自己的研究；不创建不可使用的空对话。'),button('继续当前案例',()=>ctx.app.navigate({projectId:v.pid,conversationId:teaching.conversation_id,page:'dashboard'})),transfer);
            if(!ctx.state)box.append(E('p','muted small','正在读取该案例的交接资料，尚未创建普通项目。'));
            return;
        }
        v.r.planTools.hidden=true;
        v.r.continueHint.hidden=true;
        v.r.progress.hidden=true;v.r.title.textContent="科研教学对话";v.r.belongsLabel.textContent="当前项目";
        v.r.import.textContent="查看教学文献";
        v.r.saveSettings.disabled=true;v.r.mode.disabled=true;v.r.stage.disabled=true;
        v.r.input.rows=2;
        v.r.input.placeholder="例如：为什么选这个问题？范围能不能再小一点？";
        v.r.send.textContent=v.busy?"保存中…":"发送";
        const state=ctx.state;if(!state)return;
        const {E,button}=ctx;box.hidden=false;box.dataset.stateVersion=state.state_version;box.dataset.busy=String(!!ctx.busy);
        v.teachingState={version:state.state_version,busy:!!ctx.busy,step:state.step,completed:state.completed};
        const signature=JSON.stringify(v.teachingState);
        if(box.dataset.signature===signature){ctx.repaint();return;}
        const expanded=box.querySelector('details')?.open || false;box.dataset.signature=signature;box.replaceChildren();
        const stage=state.stage_index ?? [0,1,2,2,3,3,3][state.step],titles=["明确条件","了解研究现状","确认研究问题","形成并审阅方案"];
        const revisiting=state.completed && state.step<6;
        const details=E("details","case-reading"),summary=E("summary");details.open=expanded;summary.append(E("strong","",`${revisiting?'返回审阅与修改':state.completed?'体验已完成':'第 '+(stage+1)+' 阶段'} · ${titles[stage]}`),E("span","case-label","预置教学 · 离线"));details.append(summary);
        details.append(E("p","",titles.map((t,i)=>`${i+1}. ${t}`).join(" → ")),E("p","case-label",state.notice+` 资料 v${state.version} · 整理 ${state.organized_on}`));
        for(const [name,text] of Object.entries(state.concepts))details.append(E("p","",name+"："+text));
        details.append(button("查看案例文献",()=>ctx.openMaterial("papers")),button("带着这个思路开始自己的研究",()=>fork(ctx)));
        box.append(details);
        ctx.repaint();
    }
    function message(v,m,card){
        const ctx=views.get(v),s=ctx?.state;if(!s || !ctx.current(v) || m.message_id!==v.messages.filter(m=>m.role==="assistant").at(-1)?.message_id)return;
        const {E,button}=ctx,task=E("section","case-task");task.setAttribute("aria-label","当前教学任务");
        const atCompletion=s.completed && s.step===6;
        task.append(E("p","case-why",atCompletion?"下一步：下载材料，和导师核对未确认事项；或开始自己的研究。":`为什么做：${s.stage_why || s.why}`));
        const group=E("div","ch-action-options case-options");group.setAttribute("aria-label","当前任务操作");
        const add=(label,fn,option)=>{const b=button(label,fn,"btn btn-secondary ch-action-card");if(option){b.dataset.caseOption=option.id;b.disabled=ctx.busy || v.busy || !option.enabled;b.title=option.reason || "读取预置内容，不调用模型";}group.append(b);};
        if(s.step===1)add("查看这三篇文献",()=>ctx.openMaterial("papers"));
        if(s.step===2){
            for(const o of s.options){const q=s.question_candidates?.find(q=>q.key===o.id);const choice=E("div","case-candidate");if(q){choice.append(E("strong","",q.title),E("p","",q.help || q.content));if(q.recommended)choice.append(E("small","","与你刚才的兴趣选择一致；可改选另一项。"));}
                const b=button(o.label,()=>ctx.act(o));b.dataset.caseOption=o.id;b.disabled=ctx.busy || v.busy || !o.enabled;choice.append(b);group.append(choice);}
        }else if(!atCompletion){
            const hasQuestionCard=s.step===3 && (m.suggestion_ids || []).includes(s.question_suggestion);
            for(const o of s.options){
                if(hasQuestionCard && o.id==="adopt_question")continue;
                if(s.step===6 && (o.id==="back" || o.id==="adopt_plan"))continue;
                if(o.kind==="discussion" || ["explain","wide"].includes(o.id)){const discussion=E("section","ch-reply-options case-discussion");discussion.append(E("small","","继续讨论 · 预置讲解"));const b=button(o.label,()=>ctx.act(o),"ch-reply-option");b.dataset.caseOption=o.id;b.disabled=ctx.busy || v.busy;discussion.append(b);task.append(discussion);}
                else add(o.label,()=>o.id==="adopt_plan" ? ctx.adopt(s.plan_suggestion) : ctx.act(o),o);
            }
        }else{
            add("查看已保存方案",()=>ctx.openMaterial("plan"));
            add("带着这个思路开始自己的研究",()=>fork(ctx));
            add("修改条件",()=>revise(v),{id:"scope_back",enabled:true});
        }
        if(keep.get(`sra.case.choice.${v.pid}/${v.cid}`))add("核对原选择",ctx.retryChoice);
        task.append(group);card.append(task);
    }
    async function adopt(v,s){
        const ctx=views.get(v);if(!ctx?.state)return;
        const kind=s.kind==="question" ? "question" : "plan",option=ctx.state.options.find(o=>o.id==="adopt_"+kind);
        if(ctx.state[kind+"_suggestion"]!==s.suggestion_id || !option?.enabled){ctx.notice(v,"这不是当前待确认的草案，或依据已变化。请核对当前任务；历史草案保留查看。");return;}
        await ctx.act(option);
    }
    async function revise(v){const ctx=views.get(v);if(!ctx?.state)return;const option=ctx.state.options.find(o=>o.id==="scope_back");if(!option){ctx.notice(v,"请先完成当前问题确认，再调整方案条件。");return;}ctx.closeMaterials();await ctx.act(option);}
    function fork(ctx){
        const {v,E,button,request,app}=ctx,dialog=E("dialog","case-dialog"),form=E("form","case-fork");
        form.append(E("h2","","开始自己的普通研究"),E("p","",ctx.state.transfer_preview),E("p","",ctx.state.sources.map(s=>s.title).join("；")));
        const preview=ctx.state.reference_preview;
        if(preview?.question)form.append(E("p","","将带入的参考问题（未确认）："+preview.question.content));
        if(preview?.plan){const details=E("details","case-transfer-preview");details.append(E("summary","","查看将带入的教学方案参考（不是正式方案）"),E("p","",preview.plan.title),E("p","",preview.plan.content));form.append(details);}
        else form.append(E("p","","当前没有方案草案可带入；不会补造方案。"));
        const inputs={};for(const [key,label] of [["title","自己的研究主题（必填）"],["resources","自己的资源条件（可空）"],["time_budget","自己的时间条件（可空）"]]){const wrap=E("label","",label),input=E("input");input.name=key;input.required=key==="title";input.maxLength=key==="title"?200:key==="resources"?2000:1000;wrap.append(input);form.append(wrap);inputs[key]=input;}
        const error=E("p","case-status"),submit=button("确认创建普通研究",()=>form.requestSubmit());form.append(error,submit,button("取消，保留教学体验",()=>dialog.close()));dialog.append(form);document.body.append(dialog);dialog.addEventListener("close",()=>dialog.remove());dialog.showModal();
        let busy=false;form.onsubmit=async e=>{e.preventDefault();if(busy)return;busy=true;submit.disabled=true;const key=`sra.case.fork.${v.pid}`,body=keep.get(key) || {request_id:crypto.randomUUID(),...Object.fromEntries(Object.entries(inputs).map(([k,i])=>[k,i.value]))};keep.set(key,body);
            try{const result=await request(ctx.base+"/fork",{method:"POST",body});keep.remove(key);window.dispatchEvent(new CustomEvent("sra:projects-changed"));if(!dialog.isConnected || !ctx.current(v))return;dialog.close();app.navigate({projectId:result.project_id,conversationId:result.conversation_id,page:"dashboard"});}
            catch(e){error.textContent=e.message+"；保留原创建请求，再次点击核对原请求。";}finally{busy=false;submit.disabled=false;}};
    }
    window.SRA_TEACHING={attach,decorate,message,adopt,revise,refresh(v){const ctx=views.get(v);return ctx?refresh(ctx):Promise.resolve();}};
})();

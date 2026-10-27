/* Main-app conversation controller. Server messages/materials are authoritative.
   localStorage holds only drafts, identities of unconfirmed requests and settings
   for NOT YET created conversations. No model retries run from reads or timers. */
(() => {
    "use strict";
    const BASE=window.SRA_API_BASE || "", $=id=>document.getElementById(id);
    const g={app:null,v:null,projects:[],projectsLoaded:false,cap:null,capPromise:null,listTicket:0,sideTicket:0,recentTicket:0,recent:[],recentLoaded:false,recentNext:null,projectNext:null,projectExpanded:false,files:new Map()};
    const uid=()=>crypto.randomUUID();
    const E=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n;};
    const button=(text,fn,cls="btn btn-secondary")=>{const b=E("button",cls,text);b.type="button";b.onclick=fn;return b;};
    const store={get(key,fallback=null){try{return JSON.parse(localStorage.getItem(key)) ?? fallback;}catch(_){return fallback;}},set(key,value){try{localStorage.setItem(key,JSON.stringify(value));}catch(_){}},remove(key){try{localStorage.removeItem(key);}catch(_){}}};
    const scope=(pid,cid)=>`${pid || "new"}/${cid || "draft"}`;
    const draftKey=v=>"sra.ch.draft."+scope(v.pid,v.cid), pendingKey=v=>"sra.ch.pending."+scope(v.pid,v.cid);
    const current=v=>!!v && g.v===v && v.root.isConnected;
    const path=(v,suffix="")=>`/api/projects/${encodeURIComponent(v.pid)}/conversations/${encodeURIComponent(v.cid)}${suffix}`;
    const textError=e=>e.status===404 ? (String(e.message).includes("数据库") ? "项目数据不可用：数据库缺失。未创建替代项目或空库。" : String(e.message).includes("对话") ? "会话不存在或不属于当前项目，请重新选择。" : "项目或请求不存在，请核对归属；不会自动新建替代项目。") : e.message;
    async function request(url,{method="GET",body,signal,timeout=30000}={}) {
        const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),timeout);
        const stop=()=>controller.abort();signal?.addEventListener("abort",stop,{once:true});
        if(signal?.aborted)controller.abort();
        try {
            const r=await fetch(BASE+url,{method,signal:controller.signal,headers:body===undefined ? {} : {"Content-Type":"application/json"},body:body===undefined ? undefined : JSON.stringify(body)});
            const data=await r.json().catch(()=>({}));
            if(!r.ok)throw Object.assign(new Error(typeof data.detail==="string" ? data.detail : `请求失败（${r.status}）`),{status:r.status,data});
            return data;
        } finally {clearTimeout(timer);signal?.removeEventListener("abort",stop);}
    }
    function runtimeBlocked(v){return !v.material?.teaching && v.runtime?.mode==='model' && v.runtime.status==='needs_configuration' && !!v.runtime.checks?.length && v.runtime.checks.every(c=>['missing_route','invalid_route','missing_credentials','unsupported_provider'].includes(c.status));}
    function paintRuntime(v){
        if(!current(v))return;
        const box=v.r.runtime;box.hidden=!!v.material?.teaching;if(box.hidden)return;
        const state=v.runtime,signature=JSON.stringify([state,v.runtimeChecking,v.runtimeError]);if(v.runtimeSignature===signature)return;v.runtimeSignature=signature;
        const focused=box.contains(document.activeElement),open=box.querySelector('details')?.open;box.replaceChildren();
        const details=E('details');details.open=open ?? runtimeBlocked(v);
        details.append(E('summary','',state?.title || (v.runtimeChecking?'正在核对运行配置…':'运行配置暂不可读取')));
        details.append(E('p','',v.runtimeError || (state?.status==='needs_configuration'&&!runtimeBlocked(v)?'部分功能配置缺失，已配置的功能仍可尝试。此检查未发送模型请求；实际可用性由请求结果确认。':state?.message) || '配置核对只读取当前设置，不调用模型。'));
        if(state?.checks?.length){const checks=E('ul');for(const check of state.checks)checks.append(E('li',['rules_mode','configured_unverified'].includes(check.status)?'':'ch-warning',check.label+'：'+check.message));details.append(checks);}
        for(const action of state?.actions || [])if(action.kind!=='try_teaching')details.append(E('p','muted small',action.detail));
        const actions=E('div','ch-runtime-actions'),refresh=button(v.runtimeChecking?'核对中…':'重新读取配置',()=>loadRuntime(v),'ch-text-button');refresh.disabled=v.runtimeChecking;actions.append(refresh);
        if(v.root.querySelector('[data-teaching="directory"]'))actions.append(button('体验离线案例',()=>v.root.querySelector('[data-teaching="directory"]').click(),'ch-text-button'));
        details.append(actions);box.append(details);box.classList.toggle('ch-runtime-attention',state?.status==='needs_configuration');if(focused)details.querySelector('summary').focus({preventScroll:true});
    }
    async function loadRuntime(v){
        if(!current(v))return;
        if(v.runtimeRead)return v.runtimeRead;
        v.runtimeChecking=true;v.runtimeError=null;paintRuntime(v);
        v.runtimeRead=(async()=>{
            try{const data=await request('/api/runtime-status',{signal:v.reads.signal,timeout:7000});if(!current(v))return;
                if(data.schema_version!=='runtime-status.v1')throw new Error('运行配置接口版本不匹配，请更新后端后重新核对。');
                v.runtime=data;
            }catch(e){if(current(v)&&e.name!=='AbortError')v.runtimeError='暂时无法核对模型配置；这不代表模型可用。可以重读配置，或先体验离线案例。';}
            finally{v.runtimeChecking=false;v.runtimeRead=null;if(current(v)){paintRuntime(v);paintHeader(v);}}
        })();return v.runtimeRead;
    }
    async function allowGeneration(v){
        if(!v.runtime)await loadRuntime(v);
        if(!current(v))return false;
        if(runtimeBlocked(v)){paintRuntime(v);v.r.runtime.querySelector('details').open=true;notice(v,'当前模型配置需要补齐，尚未提交本次生成请求；项目资料和草稿已保留。可以先体验离线案例。');return false;}
        return true;
    }
    async function capabilities() {
        if(g.cap)return g.cap;
        if(!g.capPromise)g.capPromise=(async()=>{
            try {
                const c=await request("/api/conversation-capabilities");
                if(c.version!==1 || !c.project_conversations || !c.durable_start || !c.message_tail)throw new Error("后端会话能力版本不匹配，请更新后端后重试。");
                $('ch-conversation-search').disabled=!c.conversation_search;$('ch-conversation-search').title=c.conversation_search?'搜索已保存的对话标题和项目':'当前后端尚未加载对话搜索';return g.cap=c;
            }catch(e){
                if(e.status===404){
                    // A project/conversation 404 NEVER takes this path. Confirm
                    // route absence from the running backend's own API schema.
                    try {
                        const schema=await request("/openapi.json");
                        if(schema.paths && !schema.paths["/api/conversation-capabilities"])
                            throw Object.assign(new Error("当前后端尚未加载多会话接口。请更新或重启后端后再使用；不会退回旧项目级聊天。"),{missingCapability:true});
                    }catch(check){if(check.missingCapability)throw check;}
                    throw new Error("暂时无法核对后端会话能力，请检查服务或代理连接；尚不能判断需要重启。");
                }
                throw e;
            }
        })().finally(()=>{g.capPromise=null;});
        return g.capPromise;
    }
    function fail(v,e){if(!current(v))return;v.error=textError(e);v.r.error.hidden=false;v.r.error.textContent=v.error;}
    function notice(v,text){if(!current(v))return;v.r.error.hidden=!text;v.r.error.textContent=text;}
    function saveDraft(v){store.set(draftKey(v),{text:v.r.input.value,settings:readSettings(v),entryId:v.entryId ?? null,domain:v.r.domain.value,domainConfirmed:v.domainConfirmed===true,selectedItemId:v.selectedItemId || null,paperIds:v.paperIds || [],planRequest:v.planRequest || null,planMethod:v.r.planMethod?.value || ""});}
    function compactName(text,fallback){
        const topic=text.match(/(?:联邦学习|大模型|机器学习|固态电解质|硫化物|量子|机器人|图神经网络)[^，,。！？\n；;]{0,12}/)?.[0];
        const cleaned=(topic || text.replace(/^.*?(?:感兴趣的方向是|研究主题是|研究方向是)[：:]?/,'')) .replace(/\s+/g," ").replace(/^(这是我第一次做研究[，,]?|我目前的研究问题是[：:]?|我想(?:研究|了解|做)|请(?:你|帮我)?(?:检索|搜索|分析|讨论|研究)?|帮我(?:检索|查找|分析))\s*/g,"").split(/[。！？\n]/)[0].split(/[，,；;]|(?:并且|并整理|并分析|想请你|请帮我)/)[0].trim();
        if(/第一次|没(?:有)?(?:方向|经验)|带我|新手|你好|不确定/.test(cleaned))return fallback;
        return cleaned.length>=3 && [...cleaned].length<=24 ? cleaned : fallback+" · "+new Date().toLocaleDateString("zh-CN",{month:"numeric",day:"numeric"});
    }
    function discussionName(text,stage){text=text.replace(/(?:不|不要)(?:直接|自动)?生成方案/g,'');return /方案|可行/.test(text)?'方案设计与审阅':/问题|选题/.test(text)?'细化研究问题':/文献|论文|现状/.test(text)?'梳理文献与现状':({orientation:'明确兴趣与条件',literature:'了解研究现状',question:'讨论研究问题',design:'方案设计与审阅'})[stage] || '研究讨论';}
    function navigationContext(){
        const route=g.app.getRoute(),name=projectName(route.projectId),label=$("ch-current-project");
        label.textContent=route.projectId ? name : "首次发送后建立项目";label.title=route.projectId ? name : "尚未选择项目，不会自动创建";label.disabled=!route.projectId;
        $('ch-project-new').disabled=!route.projectId;
        $('ch-project-new').hidden=!route.projectId;
        $('ch-project-new').textContent='在本项目新建对话';
        document.querySelectorAll('.ch-current-materials .nav-item').forEach(n=>{n.setAttribute('aria-disabled',String(!route.projectId));n.hidden=!route.projectId;n.tabIndex=route.projectId?0:-1;});
        document.querySelector('.ch-current-materials .nav-more').hidden=!route.projectId;
        const top=$("current-project-id");top.textContent=route.projectId ? name : "未选择";top.title=route.projectId ? name : "尚未建立研究项目";
    }
    const popupSelector='.ch-row-menu,.ch-title-menu,.ch-settings,.ch-plan-tools,.ch-stage-nav';
    function closePopups(except=null,{focus=false}={}){
        document.querySelectorAll(popupSelector).forEach(n=>{if(n.open && n!==except){const hadFocus=n.contains(document.activeElement);n.open=false;if(focus&&hadFocus)n.querySelector('summary')?.focus({preventScroll:true});}});
        const v=g.v;if(v && !v.r.downloadPanel.hidden && except!==v.r.downloadPanel){v.r.downloadPanel.hidden=true;v.r.downloadToggle.setAttribute('aria-expanded','false');if(focus)v.r.downloadToggle.focus({preventScroll:true});}
    }
    function rowMenu(label,fn,details=''){const d=E('details','ch-row-menu'),s=E('summary','','⋯'),popup=E('div','ch-row-popup');s.setAttribute('aria-label',label);popup.append(E('small','',label.replace(/^(项目|对话)操作：/,'')));if(details)popup.append(E('small','',details));popup.append(button('重命名',()=>{d.open=false;s.focus({preventScroll:true});fn();},'ch-side-rename'));d.append(s,popup);return d;}
    function readSettings(v){return {guidance_mode:v.r.mode.value,entry_stage:v.r.stage.value,discussion_goal:v.r.goal.value,...(v.discussionStep ? {discussion_step:v.discussionStep} : {})};}
    function setSettings(v,s){v.r.mode.value=s.guidance_mode || "direct";v.r.stage.value=s.entry_stage || "orientation";v.r.goal.value=s.discussion_goal || "";v.discussionStep=s.discussion_step || null;}

    const ENTRY_PATHS=[
        {title:'第一次做研究',outcome:'从兴趣中找到两三个可继续了解的切入点。',need:'说一个感兴趣的现象或应用；不知道专业术语也可以。',mode:'beginner',stage:'orientation',step:'conditions',goal:'找到适合继续了解的研究切入点',prompt:'这是我第一次做研究，我感兴趣的现象或方向是：\n请先帮我缩小范围，用容易理解的方式讨论可研究的切入点。'},
        {title:'已经有研究方向',outcome:'比较可研究的切入点，明确还需要哪些资料。',need:'说出方向和这次想解决的困难；已有条件可以直接补充。',mode:'direct',stage:'literature',step:'exploration',goal:'比较研究切入点并明确需要的资料',prompt:'我想围绕这个方向找研究切入点：\n请结合已有思路和需要补充的资料，和我一起收窄问题。'},
        {title:'已经有研究问题',outcome:'先确认一句具体问题，再围绕它完善方案。',need:'贴出已有问题或方案想法；不需要重新从兴趣开始。',mode:'direct',stage:'question',step:'question',goal:'明确已有研究问题并讨论验证思路',prompt:'我目前的研究问题是：\n请先帮我核对问题是否具体、可以怎样验证，并整理成待我确认的问题；确认后再讨论方案。'},
        {title:'带着论文来阅读',outcome:'读懂论文在解决什么、怎么做，以及对你的研究有什么帮助。',need:'选择 PDF、TXT、MD，或直接粘贴希望讨论的段落。',mode:'direct',stage:'literature',step:'reading',goal:'读懂已有论文并整理阅读重点',prompt:'我手上有一篇论文，希望先读懂研究问题、方法与局限，再整理阅读重点。\n我最想弄清楚的是：'},
    ];
    function paintEntry(v){
        const entry=ENTRY_PATHS[v.entryId];v.r.entryGuide.hidden=!entry;
        v.root.querySelectorAll('[data-entry]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.entry)===v.entryId)));
        if(!entry)return;
        v.r.entryTitle.textContent=entry.title;v.r.entryOutcome.textContent='这次可以得到：'+entry.outcome;v.r.entryNeed.textContent=entry.need;
        v.r.entryUpload.hidden=v.entryId!==3;
    }
    async function selectEntry(v,id){
        const entry=ENTRY_PATHS[id];if(!entry||!current(v)||v.entryChanging||v.busy||v.remoteBusy)return;
        if(v.entryId===id){v.r.input.focus();return;}
        v.entryChanging=true;
        try{
            const previous=ENTRY_PATHS[v.entryId],scaffold=previous&&v.r.input.value.trim()===previous.prompt;
            if((v.r.input.value.trim()&&!scaffold)||v.planRequest){if(!await preparePlanInput(v,entry.prompt+'\n\n本次先按新起点讨论，暂不检索或搜索。',null,true))return;}
            else{v.r.input.value=entry.prompt;v.r.input.dispatchEvent(new Event('input'));}
            if(!current(v))return;
            v.entryId=id;v.r.mode.value=entry.mode;v.r.stage.value=entry.stage;v.discussionStep=entry.step;v.r.goal.value=entry.goal;
            saveDraft(v);paintEntry(v);v.r.input.focus();
        }finally{v.entryChanging=false;}
    }
    const STEP_LABELS={conditions:'明确研究方向',exploration:'比较研究切入点',reading:'阅读与准备资料',synthesis:'梳理研究现状',question:'确认研究问题',design:'完善研究方案'};
    function journeyNext(v,selected){
        if(v.researchRoute?.step==='paused')return {title:'当前讨论已暂停',outcome:'已有内容会保留；准备继续时，可以直接输入新的要求。',label:'回到输入框',action:'focus'};
        const flow=v.material?.workflow?.flow,proposals=(v.suggestions||[]).filter(s=>!s.applied_item_id&&s.basis_changed===false&&s.material_fingerprint);
        const pendingPlan=selected.id==='design'&&!flow?.question?.needs_review&&flow?.question?.item_id&&proposals.find(s=>s.kind==='plan'&&s.plan?.question_id===flow.question.item_id);
        if(pendingPlan)return {title:'方案草案已准备好',outcome:'先审阅这份草案，再决定是否替换或补充已保存的方案。',label:'审阅方案草案',action:'proposal',id:pendingPlan.suggestion_id};
        if(selected.id==='design'&&flow?.plan){return {title:'继续完善你的方案',outcome:flow.plan.needs_review?'资料有变化，先核对后再修改或导出。':'已有正式方案，可以继续审阅、修改或带走。',label:'打开当前方案',action:'plan',id:flow.plan.item_id};}
        if(['question','design'].includes(selected.id)){
            const proposal=proposals.find(s=>s.kind===(selected.id==='design'?'plan':'question')) || proposals.find(s=>s.kind==='question');
            if(proposal)return {title:proposal.kind==='plan'?'方案草案已准备好':'有一个问题等待你确认',outcome:'打开后核对内容，再决定是否采用。',label:proposal.kind==='plan'?'审阅方案草案':'查看待确认问题',action:'proposal',id:proposal.suggestion_id};
        }
        if(['question','design'].includes(selected.id)&&flow?.question?.needs_review)return {title:'先核对研究问题的依据',outcome:'已有问题或来源发生变化，核对后再继续方案。',label:'核对当前问题',action:'overview'};
        if(selected.id==='question'&&flow?.question)return {title:'研究问题已确认',outcome:'可以直接围绕当前问题讨论方法、对照与可行性。',label:'围绕这个问题设计方案',action:'design'};
        if(selected.id==='design'&&selected.blocked_reason){return {title:'先明确这份方案要回答的问题',outcome:selected.blocked_reason,label:'准备问题确认',action:'question'};}
        if((selected.id==='reading'&&selected.action==='search')||(['synthesis','question'].includes(selected.id)&&selected.blocked_reason)){
            return {title:'先准备一份可以阅读的资料',outcome:'带来已有论文，或先明确要查找的内容，再继续讨论。',label:'添加已有论文',action:'upload',secondary:selected.action==='search'?'查找相关论文':'讨论检索需求'};
        }
        const outcomes={conditions:'把兴趣和已知条件说清楚，未知的部分可以留待后续补充。',exploration:'比较少量切入点，先看哪一个值得继续了解。',reading:'围绕已有资料解释问题、方法与局限，整理阅读重点。',synthesis:'把已有工作与待核实的差异整理清楚。',question:flow?.question?'已有正式问题，可以继续细化范围或准备方案。':'把已有想法收窄成一句待你确认的问题。',design:'围绕已确认问题，准备方法、对照与可行性讨论。'};
        return {title:STEP_LABELS[selected.id],outcome:outcomes[selected.id],label:selected.action_label,action:'step'};
    }
    async function runJourneyNext(v,next,selected){
        if(!current(v)||v.busy||v.remoteBusy||v.routeBusy)return;
        if(next.action==='plan')return openMaterial(v,'record',next.id);
        if(next.action==='proposal')return openMaterial(v,'proposal',next.id);
        if(next.action==='upload')return v.r.import.click();
        if(next.action==='focus')return v.r.input.focus();
        if(next.action==='overview')return openMaterial(v,'overview');
        if(next.action==='design'){await chooseResearchStep(v,'design');if(current(v)){const step=v.researchRoute?.steps.find(s=>s.id==='design');if(step&&!step.blocked_reason)return beginResearchStep(v,step);}return;}
        if(next.action==='question'){
            if(await preparePlanInput(v,'请把我已经提供的研究问题整理成一句具体、可讨论验证方法的问题，先给待我确认的候选；不要自动采用或生成方案。',null,true)&&current(v))await chooseResearchStep(v,'question');
            return;
        }
        return beginResearchStep(v,selected);
    }

    function paintResearchRoute(v){
        if(!current(v))return;
        const box=v.r.researchRoute;box.hidden=!v.cid || !v.researchRoute || !!v.material?.teaching;
        if(box.hidden)return;
        const route=v.researchRoute,selected=route.steps.find(s=>s.id===route.discussion_step);
        const signature=JSON.stringify([route,v.material?.workflow?.flow,(v.suggestions||[]).map(s=>[s.suggestion_id,s.applied_item_id,s.basis_changed]),v.busy,v.remoteBusy,v.routeBusy]);if(v.routeSignature===signature)return;v.routeSignature=signature;
        const open=box.querySelector('details')?.open || false;box.replaceChildren();
        if(selected){const next=journeyNext(v,selected),row=E('div','ch-journey-next'),copy=E('div'),actions=E('div','ch-journey-actions');
            copy.append(E('strong','',next.title),E('p','',next.outcome));
            const action=button(next.label,()=>runJourneyNext(v,next,selected),'btn btn-secondary');action.dataset.ref='beginResearchStep';action.disabled=v.busy||v.remoteBusy||v.routeBusy;actions.append(action);
            if(next.secondary){const extra=button(next.secondary,()=>selected.action==='search'?beginResearchStep(v,selected):preparePlanInput(v,'请根据我们正在讨论的方向，先明确需要查找哪些论文和资料；这次只讨论检索需求。',null,true),'ch-text-button');extra.disabled=action.disabled;actions.append(extra);}
            row.append(copy,actions);box.append(row);
        }
        const details=E('details','ch-route-details');details.open=open;
        details.append(E('summary','','更换当前任务'));
        const choices=E('div','ch-route-choices');choices.setAttribute('aria-label','选择本次科研讨论步骤');
        for(const s of route.steps){const b=button(STEP_LABELS[s.id]||s.label,()=>chooseResearchStep(v,s.id),'ch-text-button');b.dataset.researchStep=s.id;b.setAttribute('aria-pressed',String(s.id===route.discussion_step));b.disabled=v.busy||v.remoteBusy||v.routeBusy;choices.append(b);}
        details.append(choices,E('p','muted small',route.notice));box.append(details);
        if(route.next_step){const next=button('下一环节：'+(STEP_LABELS[route.next_step.id]||route.next_step.label),()=>chooseResearchStep(v,route.next_step.id),'ch-text-button ch-route-next');next.title=route.next_step.blocked_reason || '只切换任务，不自动执行或确认材料';next.disabled=!!route.next_step.blocked_reason||v.busy||v.remoteBusy||v.routeBusy;details.append(next);}
    }
    async function chooseResearchStep(v,id){
        if(!current(v)||v.busy||v.remoteBusy||v.routeBusy)return;
        v.routeBusy=true;paintResearchRoute(v);
        try{const data=await request(path(v),{method:'PATCH',body:{discussion_step:id}});if(!current(v))return;v.meta=data.conversation;v.discussionStep=id;saveDraft(v);await loadResearchRoute(v);const choices=v.r.researchRoute.querySelector('.ch-route-details');if(choices)choices.open=false;notice(v,'已切换讨论环节，尚未检索、生成或确认任何研究材料。');}
        catch(e){if(current(v))fail(v,e);}finally{v.routeBusy=false;paintResearchRoute(v);}
    }
    async function loadResearchRoute(v){
        if(!v.cid||!g.cap?.research_discussion_route||v.material?.teaching)return;
        const ticket=v.routeRead=(v.routeRead||0)+1;
        const route=await request(path(v,'/research-route'),{signal:v.reads.signal});
        if(!current(v)||ticket!==v.routeRead)return;v.researchRoute=route;paintResearchRoute(v);
    }
    async function beginResearchStep(v,step){
        if(!current(v)||v.busy||v.remoteBusy||v.routeBusy||step.blocked_reason)return;
        if(step.action==='search'){
            // Explicit confirmation uses the existing bounded search API. No fake
            // model card IDs and no retrieval when merely choosing a step.
            await startFromCard(v,{content:'按确认的范围检索代表性论文并保存到项目文献库。'}, {topic:v.researchRoute.search_topic});return;
        }
        if(step.action==='plan'){v.r.planTools.open=true;v.r.planQuestion.focus();notice(v,'核对问题与研究方法后准备方案请求；发送才生成草案，采纳仍需你明确操作。');return;}
        await chooseReply(v,null,{content:step.content});
    }
    function projectName(pid){const p=g.projects.find(x=>x.project_id===pid) || (g.currentProject?.project_id===pid ? g.currentProject : null), shared=g.v?.pid===pid ? g.v.material?.project : null;return p?.display_name || shared?.display_name || p?.topic || shared?.topic || pid || "开始新研究";}
    function paintHeader(v){
        if(!current(v))return;
        v.root.classList.toggle('has-project',!!v.pid);
        v.root.classList.toggle('is-new-research',!v.pid && !v.messages.length);
        v.r.project.textContent=projectName(v.pid);v.r.project.title=projectName(v.pid);v.r.title.textContent=v.meta?.title || (v.pid ? "项目工作区" : "新对话");v.r.title.title=v.r.title.textContent;
        const flow=v.material?.workflow;
        const discussion=({orientation:'明确条件',literature:'了解研究现状',question:'确认研究问题',design:'形成并审阅方案'})[v.meta?.entry_stage || v.r.stage.value] || '明确条件';
        v.r.progress.textContent=flow?.product_boundary?.restricted ? '历史材料 · 仅查看与讨论' : (v.meta?.discussion_goal || flow?.flow?.next?.text || '本次讨论：'+discussion);
        v.r.progress.title='查看研究概况、阶段与待确认事项';
        v.r.renameProject.hidden=!v.pid;v.r.rename.hidden=!v.cid;
        v.r.identity.hidden=!v.pid;
        v.r.panelToggle.hidden=!v.pid;v.r.downloadToggle.hidden=!v.pid;
        v.r.project.onclick=()=>{if(v.pid)g.app.navigate({projectId:v.pid,conversationId:null,page:'dashboard'});};
        v.r.fullName.textContent='项目：'+projectName(v.pid)+'；对话：'+v.r.title.textContent;
        v.r.downloadToggle.disabled=!v.pid;
        v.r.downloadToggle.classList.toggle('ch-muted-download',!v.material?.workspace?.items?.length && !v.material?.workflow?.materials?.length);
        v.r.summary.hidden=!v.pid || !!v.cid;
        const branch=flow?.flow, boundary=v.material?.product_boundary || flow?.product_boundary;
        const contextHint=boundary?.restricted ? "该历史项目的后续执行受限，已有材料仍可查看、导出；本轮只讨论问题与研究方案。" : branch?.next?.action==="revise" || boundary?.reason==="review_required" ? "问题、方案或来源需要复核，先核对变化，不直接认定方案就绪。" : branch?.planReady ? "方案已可审阅；可以讨论修改或导出，不代表结论得到验证。" : !v.material?.papers?.length ? "尚无可用论文材料，先讨论兴趣或需补充的证据；尚未完成文献检索。" : flow?.continuation?.hint || "可以结合本项目已有材料继续讨论。";
        if(v.pid && !v.cid){v.r.welcome.hidden=true;v.r.summaryText.textContent=branch?.question ? (branch.question.needs_review?'待复核问题：':'已确认问题：')+branch.question.title : '选择一段已有对话继续，或在下方开始新讨论。首次发送才保存新会话。';}
        v.r.continueHint.hidden=!v.cid || !v.messages.length || (!!v.researchRoute&&!v.material?.teaching);
        v.r.continueHint.textContent=contextHint;
        const stages=['明确条件','了解研究现状','确认研究问题','形成并审阅方案'],currentStage=({orientation:0,literature:1,question:2,design:3})[flow?.product_stage] ?? 0;
        [...v.r.stageList.children].forEach((n,i)=>{n.textContent=stages[i];if(i===currentStage)n.setAttribute('aria-current','step');else n.removeAttribute('aria-current');});
        v.r.summaryHint.textContent=branch?.next?.text || contextHint;v.r.confirmedQuestion.textContent='当前还没有正式问题，可在对话中讨论后明确采纳。';v.r.confirmedQuestion.hidden=!!branch?.question;
        v.r.confirmedBrief.textContent=Object.entries(v.material?.workspace?.brief || {}).filter(([k,val])=>typeof val==='string'&&val.trim()).map(([k,val])=>(({goal:'目标',resources:'资源',time_budget:'时间',constraints:'约束'})[k] || k)+ '：'+val).join('；') || '尚无已保存的研究条件';
        v.r.confirmedBrief.hidden=!Object.values(v.material?.workspace?.brief || {}).some(val=>typeof val==='string'&&val.trim());
        v.r.researchSummary.hidden=!v.pid || !!v.materialTab&&v.materialTab!=='overview';v.r.composeScope.textContent=v.pid ? '归属：'+projectName(v.pid) : '独立新研究';v.r.composeScope.title=v.r.composeScope.textContent;
        navigationContext();
        v.r.belongsLabel.textContent=v.cid ? "切换研究项目（草稿独立保存）" : "首次发送后归入";
        const chosen=v.pid || "";
        v.r.belongs.replaceChildren(new Option("＋ 开始新研究",""));
        for(const p of g.projects)v.r.belongs.add(new Option(p.display_name || p.topic || p.project_id,p.project_id));
        v.r.belongs.add(new Option("选择其他已有项目…","__more__"));
        if(chosen && !g.projects.some(p=>p.project_id===chosen))v.r.belongs.add(new Option(projectName(chosen),chosen));
        v.r.belongs.value=chosen;
        v.r.domainWrap.hidden=!!v.pid;
        v.r.import.disabled=!v.writable || v.busy;
        v.r.send.disabled=!v.writable || v.busy || v.remoteBusy || v.deliveryBusy;
        v.r.send.textContent=v.busy ? "提交中…" : v.remoteBusy ? "当前会话处理中" : v.pending?.awaiting_upload ? '继续上传并发送' : v.startPending ? "核对并继续首次发送" : "发送";
        paintFile(v);
        paintResearchRoute(v);
        paintRuntime(v);
        const ref=v.material?.teaching_reference;
        v.r.handoff.hidden=!ref || v.messages.some(m=>!m.optimistic);
        if(ref&&!v.r.handoff.hidden){
            const conditions=v.material.workspace.brief;
            v.r.handoff.replaceChildren(E('h3','','已从案例带入参考资料'),E('p','',`公开资料：${ref.source_titles?.join('；') || '见文献与资料中的来源清单'}。`),E('p','',`你明确提供的条件：${[conditions.resources,conditions.time_budget].filter(Boolean).join('；') || '尚未提供，待你确认'}。教学条件不会作为你的事实。`),E('p','','参考问题与方案仍是教学参考；本项目尚未确认正式问题或方案。可以先核对自己的条件，或结合资料讨论候选问题。'),button('查看带入的资料',()=>openMaterial(v,'papers')),button('先核对我的条件',()=>{if(!v.r.input.value.trim()){v.r.input.value='请先和我核对自己的研究条件；没有提供的信息保持待确认，不采用教学设定。';saveDraft(v);}v.r.input.focus();}));
        }
        window.SRA_TEACHING?.decorate(v);
    }
    async function catalogCapability(){
        const cap=await capabilities();
        if(!cap.catalog_navigation || !cap.project_exports)throw new Error("当前后端尚未加载项目导航与产物目录接口，请更新／重启后端。当前主服务不会被页面自动重启。");
    }
    const timeLabel=value=>value ? new Date(value).toLocaleString("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}) : "时间未记录";
    async function projects({append=false}={}){
        const ticket=++g.listTicket;
        if(!g.projectsLoaded)$("ch-projects").replaceChildren(E("p","ch-side-caption","正在读取项目…"));
        const pid=g.app.getRoute().projectId, offset=append ? g.projectNext : 0;
        if(append && offset===null)return;
        try {await catalogCapability();const data=await request(`/api/projects?limit=${!append && g.projectExpanded ? 10 : 5}&offset=${offset || 0}&q=${encodeURIComponent($("ch-project-search").value.trim())}&include_project_id=${encodeURIComponent(pid || "")}`);
            if(ticket!==g.listTicket)return;
            g.projectsLoaded=true;g.projectNext=data.next_offset;g.currentProject=data.current_project;
            const rows=append ? [...g.projects,...data.projects] : data.projects;
            g.projects=[...new Map(rows.map(p=>[p.project_id,p])).values()];
            paintProjects();navigationContext();if(g.v)paintHeader(g.v);
        }catch(e){if(ticket===g.listTicket)$("ch-projects").replaceChildren(E("p","ch-side-error",e.message),button("重试读取",()=>projects(),"ch-side-link"));}
    }
    function paintProjects(){
        const target=$("ch-projects");if(!target)return;
        if(!g.projectsLoaded){target.replaceChildren(E("p","ch-side-caption","正在读取项目…"));return;}
        const q=$("ch-project-search").value.trim(), pid=g.app.getRoute().projectId;
        target.replaceChildren();
        let rows=g.projects.slice(0,g.projectExpanded ? g.projects.length : 5);
        if(!q && g.currentProject?.project_id===pid && !rows.some(p=>p.project_id===pid))rows=[...(g.projectExpanded?rows:rows.slice(0,4)),g.currentProject];
        for(const p of rows){
            const b=button(p.display_name || p.topic || p.project_id,()=>g.app.navigate({projectId:p.project_id,conversationId:null,page:"dashboard"}),"ch-project-link");
            b.title=`${p.display_name || p.topic} · 最近活动 ${timeLabel(p.activity_at)}`;b.dataset.projectId=p.project_id;b.classList.toggle("selected",p.project_id===pid);b.setAttribute("aria-current",p.project_id===pid ? "true" : "false");
            const sameName=rows.filter(x=>(x.display_name||x.topic)===(p.display_name||p.topic)).length>1;
            if(sameName)b.append(E('small','ch-project-disambiguator',timeLabel(p.created_at || p.activity_at)+' · '+p.project_id.slice(-6)));
            if(p.data_status!=='available')b.append(E('small','ch-project-disambiguator','资料暂不可用'));
            const row=E('div','ch-project-row');row.append(b,rowMenu('项目操作：'+(p.display_name||p.topic),()=>renameProject(p.project_id,p.display_name||p.topic),'最近活动：'+timeLabel(p.activity_at)+'；项目编号：'+p.project_id));target.append(row);
        }
        if(!rows.length)target.append(E("p","ch-side-empty",q ? "没有匹配项目" : "尚无项目，首次发送后创建"));
        const expand=$("ch-project-expand");expand.hidden=!g.projectExpanded && g.projectNext===null;expand.textContent=g.projectExpanded ? "收起项目" : "展开更多";expand.setAttribute("aria-expanded",String(g.projectExpanded));
        if(g.projectExpanded && g.projectNext!==null)target.append(button("加载更多项目",()=>projects({append:true}),"ch-side-link"));
    }
    async function recentConversations(offset=0){
        const ticket=++g.recentTicket,target=$("ch-conversations");
        if(!g.recentLoaded)target.replaceChildren(E("p","ch-side-caption","正在读取最近对话…"));
        const selected=g.app.getRoute();
        const query=$('ch-conversation-search').value.trim();
        const limit=offset?5:Math.min(50,g.recentWindow || 5);
        try{await catalogCapability();const data=await request(`/api/conversations/recent?limit=${limit}&offset=${offset}&q=${encodeURIComponent(query)}&include_project_id=${encodeURIComponent(selected.projectId || "")}&include_conversation_id=${encodeURIComponent(selected.conversationId || "")}`);if(ticket!==g.recentTicket)return;
            g.recentLoaded=true;g.recent=offset ? [...g.recent,...data.conversations] : data.conversations;g.recentNext=data.next_offset;target.replaceChildren();
            g.recentWindow=Math.min(50,Math.max(5,g.recent.length));
            const route=g.app.getRoute();
            let displayed=[...new Map(g.recent.map(c=>[`${c.project_id}/${c.conversation_id}`,c])).values()];
            if(!query && !offset && data.current_conversation && !displayed.some(c=>c.project_id===route.projectId && c.conversation_id===route.conversationId))displayed=[...displayed.slice(0,limit-1),data.current_conversation];
            for(const c of displayed){
                const row=E("div","ch-conversation-row");row.dataset.conversationId=c.conversation_id;row.dataset.projectId=c.project_id;
                const open=button(c.title,()=>g.app.navigate({projectId:c.project_id,conversationId:c.conversation_id,page:"dashboard"}),"ch-conversation-link");open.classList.toggle("selected",c.project_id===route.projectId && c.conversation_id===route.conversationId);open.title=`${c.title} · ${c.project_name} · ${timeLabel(c.updated_at)}`;
                row.append(open,E("small","ch-project-context",c.project_name),rowMenu('对话操作：'+c.title,()=>renameConversation(c.project_id,c.conversation_id,c.title),'所属项目：'+c.project_name+'；最近更新：'+timeLabel(c.updated_at)+'；项目标识：'+c.project_id.slice(-6)));target.append(row);
            }
            if(!g.recent.length)target.append(E("p","ch-side-empty",query?'没有匹配的对话':'尚无对话，首次发送后保存。'));
            if(data.next_offset!==null)target.append(button("查看更多对话",()=>recentConversations(data.next_offset),"ch-side-link ch-more-conversations"));
            $('ch-conversation-collapse').hidden=g.recent.length<=5;
            if(data.unavailable_projects?.length)target.append(E("small","ch-side-caption",`${data.unavailable_projects.length} 个项目数据暂不可读，可到项目管理核对；未自动修复。`));
        }catch(e){if(ticket===g.recentTicket)target.replaceChildren(E("p","ch-side-error",e.message),button("重试读取",()=>recentConversations(),"ch-side-link"));}
    }
    async function conversations(pid,offset=0,ticket=null){
        g.sidePid=pid;
        if(ticket===null)ticket=++g.sideTicket;
        const view=g.v,target=view?.r.projectConversations;
        if(!pid || !target || !current(view) || view.cid || view.pid!==pid)return;
        if(!offset)target.replaceChildren(E("p","muted","项目内对话"));
        try{
            await capabilities();
            const data=await request(`/api/projects/${encodeURIComponent(pid)}/conversations?limit=8&offset=${offset}`);
            if(ticket!==g.sideTicket || !current(view) || view.pid!==pid)return;
            target.querySelector(".ch-more-conversations")?.remove();
            for(const c of data.conversations){
                if([...target.querySelectorAll('.ch-conversation-row')].some(row=>row.dataset.conversationId===c.conversation_id))continue;
                const row=E("div","ch-conversation-row");
                row.dataset.conversationId=c.conversation_id;
                const open=button(c.title,()=>g.app.navigate({projectId:pid,conversationId:c.conversation_id,page:"dashboard"}),"ch-conversation-link");
                open.title=c.title+' · '+timeLabel(c.updated_at);open.classList.toggle("selected",c.conversation_id===g.app.getRoute().conversationId);
                const time=E("small","",new Date(c.updated_at).toLocaleString("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}));
                const rename=button("重命名",()=>renameConversation(pid,c.conversation_id,c.title),"ch-side-rename");rename.setAttribute("aria-label",`重命名对话：${c.title}`);
                row.append(open,time,rename);target.append(row);
            }
            if(!data.conversations.length && !offset)target.append(E("p","muted","还没有对话。点击开始讨论，首次发送后才保存。"));
            if(data.next_offset!==null)target.append(button("更多对话",()=>conversations(pid,data.next_offset,ticket),"ch-side-link ch-more-conversations"));
        }catch(e){if(ticket===g.sideTicket && current(view))target.append(E("p","ch-warning",textError(e)),button("重试读取",()=>conversations(pid)));}
    }
    async function renameConversation(pid,cid,title){
        const value=window.prompt("重命名对话（不改变研究问题或进度）",title);if(!value?.trim())return;
        try{const data=await request(`/api/projects/${encodeURIComponent(pid)}/conversations/${encodeURIComponent(cid)}`,{method:"PATCH",body:{title:value.trim()}});if(g.v?.cid===cid && g.v.pid===pid){g.v.meta=data.conversation;paintHeader(g.v);}conversations(g.app.getRoute().projectId);recentConversations();}
        catch(e){if(g.v)fail(g.v,e);}
    }
    async function renameProject(pid,title){const name=window.prompt('修改项目显示名称（不改变研究主题与主线）',title);if(!name?.trim())return;try{await request(`/api/projects/${encodeURIComponent(pid)}/identity`,{method:'PATCH',body:{display_name:name.trim()}});await projects();recentConversations();}catch(e){if(g.v)fail(g.v,e);}}
    function rich(text,cls="ch-rich"){
        const node=E("div",cls);window.SRA_DOCUMENT.render(node,text || "").catch(()=>{node.textContent=text || "";});return node;
    }
    function merge(v,rows){
        const map=new Map(v.messages.map(m=>[m.message_id,m]));
        for(const m of rows){if(m.optimistic && [...map.values()].some(old=>!old.optimistic && old.role==="user" && old.request_id===m.request_id))continue;if(m.request_id){for(const [id,old] of map)if(old.optimistic && old.request_id===m.request_id)map.delete(id);}map.set(m.message_id,{...map.get(m.message_id),...m});}
        v.messages=[...map.values()].sort((a,b)=>(a.seq ?? Number.MAX_SAFE_INTEGER)-(b.seq ?? Number.MAX_SAFE_INTEGER));
    }
    function paintMessages(v,{initial=false,prepend=false,force=false}={}){
        if(!current(v))return;
        const box=v.r.messages, top=box.scrollTop, height=box.scrollHeight, near=height-top-box.clientHeight<85;
        const signature=JSON.stringify([v.messages,v.requests,v.material?.papers,v.suggestions,v.busy,v.remoteBusy,v.actionBusy,v.optionSending,v.teachingState,Object.values(v.tasks).map(t=>[t.request_id,t.current_version,!!t.report])]);
        if(v.messageSignature===signature && !force && !initial && !prepend)return;
        v.messageSignature=signature;
        v.taskDisplay=v.taskDisplay || new Map();box.querySelectorAll('.ch-task-card').forEach(n=>v.taskDisplay.set(n.dataset.taskId,{open:n.open,details:n.querySelector('.ch-task-diagnostics')?.open,source:n.querySelector('.ch-task-detail')?.open}));
        box.replaceChildren();
        v.r.welcome.hidden=v.messages.length>0 || !!v.pid;v.root.classList.toggle("has-messages",v.messages.length>0);
        v.r.earlier.hidden=v.before===null;v.r.earlier.disabled=v.loadingEarlier;
        for(const m of v.messages){
            const card=E("article",`ch-message ch-message-${m.role}`);card.dataset.messageId=m.message_id;
            if(m.request_id)card.dataset.requestId=m.request_id;
            if(m.seq)card.dataset.seq=m.seq;
            const heading=E("div","ch-message-heading");
            if(m.role==="assistant") {const av=E("span","rg-avatar rg-avatar-message");const img=E("img");img.src="/static/assets/research-guide.png";img.alt="";av.append(img);heading.append(av);}
            heading.append(E("strong","",m.role==="user" ? "你" : "科研指导"),E("small","",m.content_source==="teaching_case" ? "案例讲解" : m.mode==="rules" ? "规则指导 · 未调用模型" : m.role==="assistant" ? "基于当前项目材料" : ""));
            const containsPlan=(m.suggestion_ids||[]).some(sid=>v.suggestions.some(s=>s.suggestion_id===sid&&s.plan));
            const taskDelivery=m.role==='assistant'&&m.request_id&&Object.values(v.tasks).some(t=>t.request_id===m.request_id&&t.report);
            if(taskDelivery){card.append(heading,E('p','','检索报告已保存，摘要、阅读范围和下一步见上方检索结果。'));const original=E('details','ch-original-response');original.append(E('summary','','查看完整交付说明'),rich(visibleContent(v,m.content)));card.append(original);}
            else if(containsPlan && (v.material?.teaching || m.content.length>600)){card.append(heading,E('p','','方案草案已整理。请打开完整方案核对方法、依据与条件，再决定是否采纳。'));const original=E('details','ch-original-response');original.append(E('summary','','查看原回复与完整说明'),rich(visibleContent(v,m.content)));card.append(original);}else card.append(heading,rich(visibleContent(v,m.content)));
            if(m.delivery_warnings?.length){const warning=E('details','ch-delivery-warning');warning.append(E('summary','','回答已保存，部分操作未提供'));for(const note of m.delivery_warnings)warning.append(E('p','',note));card.append(warning);}
            for(const sid of m.suggestion_ids || []){const s=v.suggestions.find(s=>s.suggestion_id===sid);if(!s)continue;
                if(s.kind==="plan" && s.plan)card.append(planPreview(v,s));
                if(s.kind==="question"){const preview=E("div","ch-proposal-preview");preview.append(E("strong","",s.applied_item_id ? "问题已确认" : "候选研究问题"));const b=button(s.applied_item_id ? "查看已确认问题" : "采用这个问题",()=>s.applied_item_id ? openMaterial(v,"question") : adopt(v,sid));if(v.material?.teaching && !s.applied_item_id)b.dataset.caseOption="adopt_question";b.disabled=!s.applied_item_id && (s.basis_changed!==false || !s.material_fingerprint);preview.append(b);card.append(preview);}
            }
            if(m.role==="assistant" && m.questions?.length){const questions=E("section","ch-guide-questions");questions.setAttribute("aria-label","需要你确认的问题");questions.append(E("small","","需要你确认"));for(const q of m.questions)questions.append(E("p","",q));card.append(questions);}
            const references=(v.material?.teaching||taskDelivery) && m.cited_paper_ids?.length ? E("details","case-citations") : card;
            if(references!==card){references.append(E("summary","",taskDelivery?`本轮保存的资料 · ${m.cited_paper_ids.length} 篇（保存不代表纳入综合或已阅读）`:`引用资料 · ${m.cited_paper_ids.length} 篇（已准备好）`));card.append(references);}
            for(const id of m.cited_paper_ids || [])references.append(button(`引用：${v.material?.papers?.find(p=>p.paper_id===id)?.title || '查看来源'}`,()=>openMaterial(v,"papers",id),"ch-citation"));
            if(m.role==="assistant"){
                const replies=m.suggested_replies || [], actions=m.actions || [];
                if(replies.length){const group=E("section","ch-reply-options");group.setAttribute("aria-label","继续讨论（点击发送）");group.append(E("small","","继续讨论 · 点击发送对应内容"));
                    for(const option of replies){const b=button(option.label,()=>chooseReply(v,m,option),"ch-reply-option");b.append(E("span","",option.content));b.title=option.content;b.disabled=v.busy || v.remoteBusy || v.optionSending || !v.writable;group.append(b);}card.append(group);}
                if(actions.length){const group=E("section","ch-action-options");group.setAttribute("aria-label","明确操作（不发送聊天）");group.append(E("small","","明确操作 · 不发送聊天、不启动研究"));
                    for(const action of actions){const proposal=v.suggestions.find(s=>s.suggestion_id===action.suggestion_id), stale=proposal && (proposal.basis_changed!==false || !proposal.material_fingerprint || !!proposal.applied_item_id);
                        const isSearch=action.type==="start_search", capMissing=isSearch && !g.cap?.research_search;
                        const b=button(action.label + (isSearch && action.topic ? `：${action.topic}` : ""),capMissing ? ()=>notice(v,"当前后端尚未接入检索能力。") : ()=>runAction(v,m,action),"btn btn-secondary ch-action-card");b.dataset.actionType=action.type;b.disabled=action.enabled===false || stale || v.actionBusy || capMissing;b.title=capMissing ? "后端未接入检索能力" : stale ? "草案已采纳或依据待复核，请查看研究材料" : action.disabled_reason || "由服务端核对后执行";group.append(b);if(action.disabled_reason)group.append(E("small","ch-warning",action.disabled_reason));}card.append(group);}
            }
            if(m.role==="user" && m.request_id){
                const req=v.requests[m.request_id], status=req?.status || m.request_status;
                if(status && status!=="completed"){
                    const labels={creating:"正在建立项目与会话，尚未提交模型",processing:"处理中，可切换会话或离开页面",failed:"生成失败",interrupted:"原处理已中断",unknown:"响应未确认，请核对原请求",not_submitted:"尚未提交成功"};
                    const statusNode=E("div","ch-request-status",labels[status] || status);statusNode.setAttribute("role","status");
                    const audit=req?.callAudit?.entries || [],phase=audit.filter(e=>e.event==="phase" && e.outcome==="processing").at(-1);
                    if(status==="processing" && phase)statusNode.append(E("p","",({generation:phase.kind==='plan' ? "正在等待方案草案返回；可离开页面，之后查看原请求" : "正在等待科研指导回复；可离开页面，之后查看原请求",validation:"已收到回复，正在核对结构与依据",saving:"正在保存已校验的回复与草案；不会自动采纳"})[phase.name] || (phase.name.startsWith('condition_repair:') ? "正在重新核对方案条件（最多一次修复）" : "正在处理已保存请求")));
                    if(audit.length){const detail=E("details");detail.append(E("summary","","调用与耗时记录"));for(const e of audit.filter(e=>e.event==="network" || e.event==="phase"))detail.append(E("p","small",`${e.event==="network"?'实际请求':'处理步骤'} · ${e.name} · ${e.outcome} · ${e.elapsed_ms==null?'尚未结束':(e.elapsed_ms/1000).toFixed(2)+'秒'}${e.event==='network'?' · token '+(e.usage?.total_tokens ?? '未知')+' · 费用 '+(e.cost ?? '未知'):''}`));statusNode.append(detail);}
                    if(req?.error || m.error)statusNode.append(E("p","",req?.error || m.error));
                    if(g.cap?.call_audit && !audit.length && ["failed","interrupted","processing"].includes(status))statusNode.append(button("核对状态与调用记录",()=>checkRequest(v,m.request_id)));
                    if(["failed","interrupted"].includes(status))statusNode.append(button("重试此消息",()=>retry(v,m.request_id)));
                    if(["unknown","not_submitted"].includes(status)){
                        statusNode.append(button("查询原请求",()=>checkRequest(v,m.request_id)));
                        if(v.pending?.body.request_id===m.request_id)statusNode.append(button("用原请求重新提交",()=>resend(v)));
                    }
                    card.append(statusNode);
                }
            }
            window.SRA_TEACHING?.message(v,m,card);
            box.append(card);
        }
        paintTasks(v);
        if(initial || force || (near && !prepend))box.scrollTop=box.scrollHeight;
        else if(prepend)box.scrollTop=top+(box.scrollHeight-height);
        else {box.scrollTop=top;v.r.latest.hidden=false;}
    }
    async function pageMessages(v,{earlier=false,replace=false}={}){
        if(!v.cid)return;
        const ticket=v.pageTicket=(v.pageTicket || 0)+1;
        const suffix=earlier ? `?limit=30&before=${v.before}` : "?limit=30&tail=true";
        if(earlier){v.loadingEarlier=true;v.r.earlier.disabled=true;}
        try{const data=await request(path(v,"/messages")+suffix,{signal:v.reads.signal});if(!current(v) || ticket!==v.pageTicket)return;
            if(replace){const end=Math.max(0,...data.messages.map(m=>m.seq || 0));v.messages=v.messages.filter(m=>m.optimistic || m.seq>end);}
            merge(v,data.messages);v.before=data.next_before;paintMessages(v,{initial:!earlier,prepend:earlier});
            for(const m of data.messages)if(m.request_id && ["processing","failed","interrupted"].includes(m.request_status))v.requests[m.request_id]={...v.requests[m.request_id],status:m.request_status,error:m.error};
            paintMessages(v);schedule(v);
        }catch(e){if(e.name!=="AbortError")fail(v,e);}finally{v.loadingEarlier=false;if(current(v))v.r.earlier.disabled=false;}
    }
    async function newMessages(v){
        let last=Math.max(0,...v.messages.map(m=>m.seq || 0));
        for(let i=0;i<10;i++){
            const data=await request(path(v,"/messages")+`?limit=100&after=${last}`,{signal:v.reads.signal});if(!current(v))return;
            merge(v,data.messages);if(data.next_after===null)break;last=data.next_after;
        }
        paintMessages(v);
    }
    async function refreshMaterials(v,{append=false}={}){
        if(!v.pid)return;
        const data=await request(`/api/projects/${encodeURIComponent(v.pid)}/workspace?include_chat=false`,{signal:v.reads.signal});
        if(!current(v))return;v.material=data;
        window.dispatchEvent(new CustomEvent("sra:workspace-loaded",{detail:{projectId:v.pid,counts:data.counts,paper_count:data.papers?.length,material_counts:data.material_counts,workflow:data.workflow}}));
        if(v.cid){const suggestions=await request(path(v,"/suggestions")+`?limit=30&recent=true&offset=${append ? v.suggestionOffset : 0}`,{signal:v.reads.signal});if(!current(v))return;v.suggestions=append ? [...v.suggestions,...suggestions.suggestions] : suggestions.suggestions;v.suggestionOffset=suggestions.next_offset;}
        const chosen=v.r.planQuestion.value || v.planRequest?.question_id;
        v.r.planQuestion.replaceChildren(new Option("选择本次方案对应的问题（不改变主线）",""));
        for(const q of data.workflow?.flow?.questions || [])v.r.planQuestion.add(new Option(q.title+(q.needs_review ? "（需复核）" : ""),q.item_id));
        v.r.planQuestion.value=chosen || data.workflow?.flow?.question?.item_id || "";
        const eligibility=()=>{const q=(data.workflow?.flow?.questions||[]).find(q=>q.item_id===v.r.planQuestion.value);v.r.preparePlan.disabled=!q||q.needs_review;v.r.planEligibility.textContent=!q?'先确认并选择研究问题，再准备方案请求。':q.needs_review?'这个问题的依据待复核，请先核对材料。':'将为所选问题准备可编辑请求；发送后生成草案，仍需明确采纳。';};eligibility();v.r.planQuestion.onchange=()=>{if(v.planRequest)v.planRequest.question_id=v.r.planQuestion.value || null;eligibility();saveDraft(v);};
        v.r.planTools.hidden=!g.cap?.research_plan_templates || !v.pid || data.research_scope?.supported===false;
        paintHeader(v);paintMaterials(v);
        if(v.cid){const ids=v.messages.filter(m=>m.actions?.length).slice(-100).map(m=>m.message_id);if(ids.length){const result=await request(path(v,"/guidance-actions")+`?message_ids=${encodeURIComponent(ids.join(','))}`,{signal:v.reads.signal});if(!current(v))return;for(const m of v.messages)if(result.actions[m.message_id])m.actions=result.actions[m.message_id];}}
        paintMessages(v);
        await window.SRA_TEACHING?.refresh(v);
        await loadResearchRoute(v);
    }
    async function downloads(v,{append=false}={}){
        if(!current(v) || !v.pid)return;
        const ticket=v.downloadTicket=(v.downloadTicket || 0)+1,box=v.r.downloadBody;
        if(!append){box.replaceChildren(E("p","muted","正在核对当前项目的真实产物…"));v.exports=[];}
        try{await catalogCapability();const data=await request(`/api/projects/${encodeURIComponent(v.pid)}/exports?limit=30&offset=${append ? v.exportNext : 0}${v.cid ? '&conversation_id='+encodeURIComponent(v.cid) : ''}`,{signal:v.reads.signal});if(!current(v) || ticket!==v.downloadTicket)return;
            v.exports=append ? [...v.exports,...data.entries] : data.entries;v.exportNext=data.next_offset;box.replaceChildren(E("p","muted small",data.scope));
            if(!v.exports.length)box.append(E("p","ch-export-empty","当前项目还没有可下载产物。讨论、浏览或展开菜单不会生成文件。"));
            for(const [key,title] of [["current","当前方案与材料"],["other","其他已有产物与版本"],["history","历史产物（非本轮新增成果）"]]){
                const rows=v.exports.filter(e=>e.group===key);if(!rows.length)continue;
                const group=E(key==="current" ? "section" : "details","ch-export-group");group.append(E(key==="current" ? "h3" : "summary","",title));
                for(const entry of rows){const row=E("div","ch-export-row");row.dataset.exportKey=entry.key;
                    const rawVersion=entry.version || entry.label || "",recordId=entry.key.startsWith("plan:") ? entry.key.slice(5) : "";
                    const version=recordId && rawVersion.endsWith(" · "+recordId.slice(0,8)) ? rawVersion.slice(0,-11) : rawVersion;
                    row.append(E("strong","",`${entry.title} · ${version}`),E("small","",`${entry.status_label}${entry.recommended ? " · 当前有效版本" : ""}`));
                    if(recordId){const details=E("details","ch-export-detail");details.append(E("summary","","记录详情"),E("small","",`记录编号：${recordId}`));row.append(details);}
                    const controls=E("div","ch-export-actions"),format=E("select");format.setAttribute("aria-label",`导出格式：${entry.title}`);for(const fmt of entry.formats)format.add(new Option(fmt.toUpperCase(),fmt));
                    const link=E("a","btn btn-secondary btn-sm","下载");link.setAttribute("download","");const update=()=>{link.href=BASE+entry.url+`${entry.url.includes('?') ? '&' : '?'}format=${format.value}`;};update();format.onchange=update;
                    link.onclick=e=>{if(!current(v) || v.pid!==g.app.getRoute().projectId){e.preventDefault();return;}if(format.value!=="md" && format.value!=="json")notice(v,"Word/PDF 沿用原转换；复杂公式建议在方案页面预览并打印，不能保证所有公式格式。 ");};controls.append(format,link);row.append(controls);group.append(row);
                }box.append(group);
            }
            if(data.next_offset!==null)box.append(button("加载更多产物版本",()=>downloads(v,{append:true})));
            box.append(E("p","ch-export-notice",data.format_notice),button("打开方案与公式预览／打印",()=>g.app.navigate({page:"experiments"})));
        }catch(e){if(current(v) && e.name!=="AbortError")box.replaceChildren(E("p","ch-warning",textError(e)),button("重试读取产物",()=>downloads(v)));}
    }
    function placeDownloads(v){
        if(!current(v))return;
        if(window.innerWidth<=700){const top=v.root.querySelector('.ch-heading').getBoundingClientRect().bottom+4;v.r.downloadPanel.style.top=top+"px";v.r.downloadPanel.style.maxHeight=Math.max(90,v.r.form.getBoundingClientRect().top-top-8)+"px";}
        else {v.r.downloadPanel.style.top="";v.r.downloadPanel.style.maxHeight="";}
    }
    function selectMaterialTab(v,id,{reset=true}={}){
        if(!current(v))return;
        v.materialTab=id;
        for(const tab of v.r.materialTabs.children){const active=tab.dataset.tab===id;tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;}
        v.r.materialScroll.setAttribute('aria-labelledby','ch-tab-'+id);
        v.r.panel.querySelectorAll('[data-material-group]').forEach(n=>n.hidden=n.dataset.materialGroup!==id);
        if(reset)v.r.materialScroll.scrollTop=0;
    }
    function paintMaterials(v){
        if(!current(v))return;
        const signature=JSON.stringify([v.material?.workspace?.revision,v.material?.workspace?.brief,v.material?.workflow,v.material?.papers,v.suggestions,v.adopting,v.suggestionOffset]);
        if(v.materialSignature===signature)return;v.materialSignature=signature;
        const box=v.r.materialBody;
        const key=n=>n.dataset.planId?'plan:'+n.dataset.planId:n.dataset.suggestionId?'proposal:'+n.dataset.suggestionId:n.dataset.paperId?'paper:'+n.dataset.paperId:n.dataset.materialKind?'section:'+n.dataset.materialKind:(n.closest('[data-plan-id]')?.dataset.planId || '')+':'+n.querySelector('summary')?.textContent;
        const expanded=new Set([...box.querySelectorAll('details[open]')].map(key));
        // Adoption changes a draft's identity to its saved record. Keep the
        // reader open on that exact record, not on another candidate/version.
        for(const s of v.suggestions)if(s.applied_item_id&&expanded.has('plan:'+s.suggestion_id))expanded.add('plan:'+s.applied_item_id);
        const top=v.r.materialScroll.scrollTop;
        box.replaceChildren();
        if(!v.material){box.append(E("p","muted","首次发送后，这里会显示当前项目共享的研究材料。"));return;}
        const flow=v.material.workflow, fields=[["已确认研究问题",flow?.flow?.question],["当前正式方案",flow?.flow?.plan]];
        const group=(n,id)=>{n.dataset.materialGroup=id;box.append(n);return n;};
        for(const [label,item] of fields){if(!item)continue;const block=E("section","ch-material-block");block.dataset.materialKind=item.kind==='plan'?'plan':'question';block.append(E("h3","",label),E('strong','',item.title));if(item.kind==='plan')block.append(planReview(v,item,false));else block.append(rich(visibleContent(v,item.content)));if(item.needs_review)block.append(E("p","ch-warning","依据已变化，请先复核。"));group(block,item.kind==='plan'?'plans':'overview');}
        const papers=E("details","ch-material-block");papers.append(E("summary","",`论文与引用来源 · ${v.material.papers.length}`),E("p","muted","可选择至多 3 篇作为本次讨论参考；论文标题保留原文。"));
        papers.dataset.materialKind="papers";
        for(const p of v.material.papers){const d=E("details","ch-paper-card");d.dataset.paperId=p.paper_id;d.append(E("summary","",p.title),E("p","muted small",`${(p.authors || []).join('、')} · ${p.year || '年份待核对'}`),E("p","",`阅读范围：${p.reading_scope || p.scope}`),E("p","",p.abstract || "尚无摘要，仅可核对元信息。"));if(p.relevance)d.append(E("p","",`与当前讨论的关系：${p.relevance}`));if(/^https?:\/\//i.test(p.url || '')){const a=E("a","","原始来源（外部链接，需要网络）");a.href=p.url;a.target="_blank";a.rel="noopener noreferrer";d.append(a);}
            const label=E("label","ch-paper","作为本次讨论参考");const input=E("input");input.type="checkbox";input.checked=v.paperIds.includes(p.paper_id);input.onchange=()=>{if(input.checked && v.paperIds.length>=3){input.checked=false;notice(v,"最多选择三篇参考论文。");return;}v.paperIds=input.checked ? [...v.paperIds,p.paper_id] : v.paperIds.filter(id=>id!==p.paper_id);};label.prepend(input);d.append(label);papers.append(d);}papers.open=true;
        if(v.material.papers.length)group(papers,'papers');
        const reports=E('section','ch-material-block');reports.dataset.materialKind='reports';reports.append(E('h3','','研究现状报告'));group(reports,'papers');
        if(v.material.material_counts?.research_reports){reports.append(button('查看已保存报告',()=>loadMaterialReports(v),'ch-text-button'));}else reports.append(E('p','muted','尚无已保存报告。'));
        if(!v.material.papers.length && !v.material.material_counts?.research_reports){reports.replaceChildren(E('p','muted','还没有文献或报告，可以添加已有资料，或在对话中讨论检索需求。'),button('添加或查看资料',()=>{panel(v,false);v.r.import.click();}));}
        const sources=E("details","ch-material-block");sources.append(E("summary","",`已有材料 · ${flow?.materials?.length || 0}`));
        for(const source of flow?.materials || []){const d=E("details");d.append(E("summary","",source.title),rich(source.content || "可在研究方案或历史材料页面查看正文。"));sources.append(d);}if(flow?.materials?.length)group(sources,'papers');
        const proposals=v.suggestions.filter(s=>!s.applied_item_id);
        if(!flow?.flow?.plan && !proposals.some(s=>s.kind==='plan'))group(E('section','ch-material-empty','尚无正式方案或待审阅草案。先确认研究问题，再在对话中提出方案需求。'),'plans').append(button('回到对话',()=>{panel(v,false);v.r.input.focus();}));
        for(const s of proposals){const d=E("details","ch-proposal");d.dataset.suggestionId=s.suggestion_id;d.append(E("summary","",(s.kind==='plan'?'未采纳方案草案：':'待采纳草案：')+s.title));
            if(s.plan)d.append(planReview(v,s,true));else d.append(rich(visibleContent(v,s.content)));
            const stale=s.basis_changed!==false || !s.material_fingerprint, closed=["result","finding","outline"].includes(s.kind);
            if(!s.plan){const b=button(closed ? "历史建议，仅查看" : stale ? "依据待复核，请重新讨论" : s.kind==="question" ? "采用这个问题" : "采纳到研究记录",()=>adopt(v,s.suggestion_id));b.disabled=closed || stale || v.adopting;d.append(b);}group(d,s.kind==='plan'?'plans':'overview');
        }
        if(v.suggestionOffset!==null)box.append(button("更多草案",()=>refreshMaterials(v,{append:true}).catch(e=>fail(v,e))));
        const history=(v.material.workspace?.items||[]).filter(i=>i.kind==='plan'&&i.item_id!==flow?.flow?.plan?.item_id);
        if(history.length){const versions=E('details','ch-material-block');versions.dataset.materialKind='history';versions.append(E('summary','','历史／其他方案版本 · '+history.length));for(const item of history){const d=E('section','ch-historical-plan');d.dataset.recordId=item.item_id;d.append(E('h3','',item.title),planReview(v,item,false));versions.append(d);}group(versions,'plans');}
        const extra=E("details","ch-material-block ch-material-shortcuts");extra.dataset.materialKind="shortcuts";extra.append(E("summary","","其他材料与详细页面"));
        const links=E("div","ch-material-links");
        for(const [label,page] of [["文献导入与浏览","papers"],["方案审阅与导出","experiments"],["历史结果与稿件","writing"],["研究现状","research-report"],["研究问题","gaps"],["待确认事项","human"]])links.append(button(label,()=>g.app.navigate({page})));
        extra.append(links);group(extra,'overview');
        box.querySelectorAll('details').forEach(n=>{n.open=expanded.has(key(n));});
        papers.open=true;selectMaterialTab(v,v.materialTab || 'overview',{reset:false});v.r.materialScroll.scrollTop=top;
        if(v.reportsVisible)loadMaterialReports(v).catch(e=>fail(v,e));
    }
    async function loadMaterialReports(v,{id=null,offset=0}={}){
        const pid=v.pid,ticket=v.reportTicket=(v.reportTicket||0)+1,box=v.r.materialBody.querySelector('[data-material-kind="reports"]');if(!box)return;
        v.reportsVisible=true;if(!offset)box.replaceChildren(E('h3','','研究现状报告'),E('p','muted','正在读取已保存报告…'));
        try{const data=await request(`/api/projects/${encodeURIComponent(pid)}/workspace/materials?kind=reports&limit=20&offset=${offset}`,{signal:v.reads.signal});if(!current(v)||v.pid!==pid||ticket!==v.reportTicket)return;
            // A message may refer to a report older than the first catalog page.
            // Read that exact task through its project/conversation-scoped API;
            // do not fetch all pages or infer a report from model text.
            if(id&&!data.entries.some(e=>e.task_id===id)){
                const task=await request(path(v,'/research-tasks/'+encodeURIComponent(id)),{signal:v.reads.signal});if(!current(v)||v.pid!==pid||ticket!==v.reportTicket)return;
                if(!task.report)throw new Error('该任务尚无已保存报告，可返回原任务查看状态。');
                const version=task.report_versions?.at(-1)?.version;
                data.entries.unshift({task_id:id,title:task.topic,status_label:task.status_label,version,url:`/api/projects/${encodeURIComponent(pid)}/research-tasks/${encodeURIComponent(id)}/export`+(version?'?version='+version:'')});
            }
            if(!offset)box.replaceChildren(E('h3','','研究现状报告'));box.querySelector('.ch-more-reports')?.remove();
            for(const entry of data.entries){
                const row=E('details','ch-report-card');row.dataset.reportId=entry.task_id||entry.key;
                row.append(E('summary','',entry.title+' · '+(entry.label||'已保存报告')+' · '+(entry.version?'v'+entry.version+' · ':'')+(entry.status_label||'')));
                const content=E('div');row.append(content);
                row.addEventListener('toggle',async()=>{
                    if(!row.open||row.dataset.loaded)return;
                    row.dataset.loaded='loading';content.textContent='正在读取此版本…';
                    try{
                        const r=await fetch(BASE+entry.url,{signal:v.reads.signal});if(!r.ok)throw new Error('报告读取失败，请关闭后展开重试');
                        const text=await r.text();if(!current(v)||!row.isConnected)return;
                        content.replaceChildren(rich(text));const a=E('a','ch-text-button','下载此版本（MD）');a.href=BASE+entry.url;a.setAttribute('download','');content.append(a);row.dataset.loaded='yes';
                    }catch(e){row.dataset.loaded='';content.textContent=e.name==='AbortError'?'':e.message;}
                });box.append(row);
            }
            if(id){const target=box.querySelector(`[data-report-id="${CSS.escape(id)}"]`);if(target){target.open=true;target.tabIndex=-1;target.scrollIntoView({block:'start'});target.focus({preventScroll:true});}}
            if(!data.entries.length&&!offset)box.append(E('p','muted','尚无已保存报告。'));
            if(data.next_offset!==null)box.append(button('更多报告',()=>loadMaterialReports(v,{offset:data.next_offset}),'ch-text-button ch-more-reports'));
        }catch(e){if(current(v)&&e.name!=='AbortError')box.replaceChildren(E('p','ch-warning',textError(e)),button('重试读取报告',()=>loadMaterialReports(v,{id})));}
    }
    async function adopt(v,sid){
        if(v.adopting)return;v.adopting=true;
        try{
            // Revision alone cannot catch excerpt/source changes. Re-read the
            // server dependency verdict before every user-authorized adoption.
            await refreshMaterials(v);if(!current(v))return;
            const s=v.suggestions.find(x=>x.suggestion_id===sid);
            if(!s || s.basis_changed!==false || !s.material_fingerprint){notice(v,"草案依据已变化，请重新讨论或核对；原草案已保留。");return;}
            if(v.material?.teaching){await window.SRA_TEACHING.adopt(v,s);return;}
            const result=await request(path(v,`/suggestions/${encodeURIComponent(sid)}/apply`),{method:"POST",body:{expected_revision:v.material.workspace.revision}});
            if(!current(v))return;v.accepted.add(result.item.item_id);await refreshMaterials(v);notice(v,"已采纳到研究记录，可继续审阅；不表示结论得到验证。");
            window.dispatchEvent(new CustomEvent("sra:records-changed",{detail:{projectId:v.pid}}));
        }catch(e){fail(v,e);if(e.status===409)await refreshMaterials(v).catch(()=>{});}
        finally{v.adopting=false;paintMaterials(v);}
    }
    function planQuality(v,item,draft,review){
        const box=E('section','ch-plan-quality');box.setAttribute('aria-label','方案交付检查');
        const heading=E('div','ch-quality-heading'),status=E('p','muted small'),body=E('div');status.setAttribute('role','status');
        const refresh=button('重新核对',()=>load(true),'ch-text-button');heading.append(E('h4','','交付检查'),refresh);box.append(heading,status,body);
        let loading=false,loaded=false;
        const discuss=text=>preparePlanInput(v,`请和我核对方案“${item.title}”：${text}。先讨论并保留未确认信息，不自动采纳或重写方案。`,null,true);
        const load=async(force=false)=>{
            if(loading || loaded&&!force || !current(v) || !box.isConnected)return;
            loading=true;refresh.disabled=true;status.textContent='正在核对当前资料、引用和研究条件…';
            const params=new URLSearchParams(draft ? {conversation_id:v.cid,suggestion_id:item.suggestion_id} : {item_id:item.item_id});
            try{
                const report=await request(`/api/projects/${encodeURIComponent(v.pid)}/workspace/plan-quality?${params}`,{signal:v.reads.signal});
                if(!current(v) || !box.isConnected)return;
                loaded=true;box.dataset.qualityStatus=report.status;
                body.replaceChildren(E('strong','',report.label));status.textContent='本次核对已完成；检查不改写方案。';
                const attention=report.checks.filter(c=>c.status==='attention'),passed=report.checks.filter(c=>c.status!=='attention');
                if(attention.length){const list=E('ul','ch-quality-actions');for(const check of attention){const row=E('li');row.dataset.check=check.code;row.append(E('strong','',check.label),E('p','',check.detail));if(!v.material?.teaching)row.append(button('讨论这一项',()=>discuss(check.label+'：'+check.detail),'ch-text-button'));list.append(row);}body.append(E('p','ch-warning',`优先核对 ${attention.length} 项`),list);}
                if(passed.length){const done=E('details','ch-quality-passed');done.append(E('summary','',`已完成的结构与依据检查 · ${passed.length} 项`));const list=E('ul');for(const check of passed){const row=E('li','',`${check.label}：${check.detail}`);row.dataset.check=check.code;list.append(row);}done.append(list);body.append(done);}
                if(report.condition_details?.length){const conditions=E('details','ch-quality-conditions');conditions.append(E('summary','','逐项核对研究条件'));const list=E('dl');for(const condition of report.condition_details){const value=E('dd');value.append(E('span','',condition.value || '尚未提供'),E('small',condition.basis==='user'?'muted':'ch-warning',condition.basis==='user'?'用户提供，仍需核对适用性':'尚未由你确认'));list.append(E('dt','',condition.label),value);}conditions.append(list);body.append(conditions);}
                else if(report.pending_conditions?.length)body.append(E('p','ch-warning','待你确认的条件：'+report.pending_conditions.join('、')));
                if(report.unconfirmed?.length){const pending=E('details','ch-quality-pending');pending.append(E('summary','',`方案中的待讨论事项 · ${report.unconfirmed.length} 项`));const list=E('ul');for(const text of report.unconfirmed)list.append(E('li','',text));pending.append(list);body.append(pending);}
                if(report.evidence.length){const sources=E('details');sources.append(E('summary','','逐条核对引用片段'));
                    for(const entry of report.evidence){const row=E('section','ch-quality-citation');row.append(E('p','',entry.statement));if(entry.paper_id)row.append(button(entry.title,()=>openMaterial(v,'papers',entry.paper_id),'ch-text-button'));else row.append(E('strong','',entry.title));row.append(E('p',entry.matched?'muted small':'ch-warning',entry.location),E('blockquote','',entry.quote || '尚未提供引用片段'));if(!v.material?.teaching)row.append(button('讨论这条依据',()=>discuss(`陈述“${entry.statement}”引用“${entry.title}”的片段“${entry.quote || '未提供'}”；请核对这条引用是否支持陈述，以及需要补充的材料`),'ch-text-button'));sources.append(row);}body.append(sources);}
                if(report.material_review?.length){const materials=E('details','ch-quality-materials');materials.append(E('summary','','材料数值与比较条件 · 审阅线索'),E('p','muted small',report.material_notice || '原文线索只供人工审阅，不表示数据已经可比或得到验证。'));for(const entry of report.material_review){const row=E('section','ch-quality-citation');row.append(E('p','',entry.statement));if(entry.source_matched){const list=E('dl');for(const [label,values] of [['数值与单位',entry.quantities],['温度',entry.temperatures],['样品与测试条件',entry.condition_mentions]])list.append(E('dt','',label),E('dd','',values?.join('；') || '当前片段未识别到，请核对原文'));list.append(E('dt','','数据类型线索'),E('dd','',({experimental:'出现实验相关用语',computational:'出现计算相关用语',mixed:'同时出现实验和计算用语',unspecified:'当前片段未能区分'})[entry.evidence_type] || '需人工区分'));row.append(list);}for(const text of entry.review_points || [])row.append(E('p','ch-warning',text));if(!v.material?.teaching)row.append(button('讨论比较条件',()=>discuss(`针对“${entry.statement}”，请核对单位、测试温度、样品与测量条件、实验或计算数据类型；缺失信息保持待确认`),'ch-text-button'));materials.append(row);}body.append(materials);}
                const questions=E('details');questions.append(E('summary','','研究者审阅要点'));for(const text of report.review_questions)questions.append(E('p','',text));body.append(questions,E('p','muted small',report.notice));
                if(!v.material?.teaching && attention.length)body.append(button('讨论待补充项',()=>discuss(attention.map(c=>c.detail.replace(/[。；]+$/,'')).join('；'))));
            }catch(e){if(current(v) && box.isConnected && e.name!=='AbortError')status.textContent=loaded?'本次核对失败，下方保留上次检查，请重新核对后再判断。':'交付检查暂不可用，请重新核对；原方案仍保留。';}
            finally{loading=false;refresh.disabled=false;}
        };
        review.addEventListener('toggle',()=>{if(review.open)load(true);});
        return box;
    }
    function planReview(v,item,draft){
        if(draft && item.applied_item_id){const saved=v.material?.workspace.items.find(i=>i.item_id===item.applied_item_id);if(saved)return planReview(v,saved,false);}
        const names={computational:"计算研究",theoretical:"理论研究",experimental:"实验研究设计"},box=E("details","ch-plan-review");box.dataset.planId=item.item_id || item.suggestion_id;
        const stale=draft ? item.basis_changed!==false || !item.material_fingerprint : item.needs_review;
        box.append(E("summary","",`${draft ? item.applied_item_id ? "已采纳草案" : "未采纳讨论稿" : "已保存方案"} · ${names[item.plan?.method] || "历史未标类型"} · ${item.plan_version!=null ? 'v'+item.plan_version : draft ? '尚无正式版本' : '历史未标版'}${stale ? ' · 待复核' : ''}`));
        const q=v.material?.workspace.items.find(i=>i.item_id===item.plan?.question_id || i.kind==="question" && item.related_ids?.includes(i.item_id)),old=v.material?.workspace.items.find(i=>i.item_id===item.supersedes_id);
        box.append(E("p","muted small",`关联问题：${q?.title || '需核对'}${old ? '；修订自：'+old.title+(old.plan_version!=null ? '（v'+old.plan_version+'）' : '') : ''}`));
        if(old?.plan && item.plan){const changes=E('div','ch-plan-change');changes.append(E('strong','','本次修订变化'));
            let count=0;for(const [key,label] of [['objective','研究目标'],['conditions','资料条件'],['method','技术路线'],['validation','验证设计'],['feasibility','资源与可行性']]){const a=old.plan.sections.find(s=>s.key===key)?.statements.map(s=>s.text).join('；'),b=item.plan.sections.find(s=>s.key===key)?.statements.map(s=>s.text).join('；');if(a!==b){changes.append(E('p','',label+'：'+(a||'旧版未提供').slice(0,110)+' → '+(b||'新版未提供').slice(0,140)));count++;}}
            for(const key of ['resources','time_budget','budget','data_access'])if(JSON.stringify(old.plan.conditions[key])!==JSON.stringify(item.plan.conditions[key])){changes.append(E('p','',({resources:'资源',time_budget:'时间',budget:'预算',data_access:'数据权限'})[key]+'条件：'+old.plan.conditions[key].value+' → '+item.plan.conditions[key].value));count++;}
            if(!count)changes.append(E('p','','上述目标、条件与验证设计未检测到文字变化；请结合完整正文审阅，不能仅凭版本号认定有实质改进。'));box.append(changes);}else if(old){const changes=E('div','ch-plan-change');changes.append(E('strong','','本次修订变化'),E('p','','旧版缺少结构化方案，无法可靠自动比较具体变化；请对照旧版正文审阅，不仅依据版本号。'));box.append(changes);}
        // Presentation only: keep saved bodies/exports immutable. Collapse only
        // exact renderer boilerplate, never specific evidence gaps or conditions.
        let body=item.content || "";
        if(item.plan){
            body=body.replace(/^\*\*方案类型：[^\n]+ · 模板 [^\n]+\*\*\s*$/m,"")
                .replace(/^> 研究方案建议，尚未执行。结构完整不等于科学有效；采纳状态见记录标记。\s*$/m,"");
            if(v.material?.teaching)body=body.replace(/^教学案例，使用预先整理的资料；不是实时检索或实验结果。\s*$/m,"");
            const pending="## 待讨论事项\n\n"+item.plan.unconfirmed.map(s=>"- "+s).join("\n\n");
            if(item.plan.unconfirmed.length)body=body.replace(pending,"");
        }
        box.append(E("p","muted small",v.material?.teaching ? "教学方案 · 预置资料，未执行。结构与依据检查仅供讨论。" : "结构与依据检查仅供讨论；方案尚未执行，结论未验证。"),planQuality(v,item,draft,box),rich(visibleContent(v,body)));
        if(item.plan?.unconfirmed.length)box.append(E("p","ch-warning","待确认／待讨论："+item.plan.unconfirmed.join("；")));
        const metadata=E("details","ch-plan-metadata");metadata.append(E("summary","","版本与技术详情"),E("p","muted small",`模板：${item.plan?.template_version || '旧结构'}；记录：${item.item_id || item.suggestion_id}；问题记录：${q?.item_id || '需核对'}；修订记录：${item.supersedes_id || '无'}`));box.append(metadata);
        const controls=E("div","ch-plan-controls");
        if(v.material?.teaching){
            controls.append(button("修改条件",()=>window.SRA_TEACHING.revise(v)),button("讨论某一部分",()=>preparePlanInput(v,"这个方案需要什么条件？",null,true)));
            if(draft && !item.applied_item_id){const b=button(stale ? "依据待复核，不能直接采纳" : "保存当前方案",()=>adopt(v,item.suggestion_id));b.dataset.caseOption="adopt_plan";b.disabled=stale || v.adopting;controls.append(b);}
        }
        else {
        controls.append(button("讨论某一部分",()=>preparePlanInput(v,`请讨论${item.title}的某一部分（请填写章节）：`,null,true)));
        if(g.cap?.research_search)controls.append(button("准备检索依据",()=>prepareSearchInput(v,`请帮我检索${v.material?.workflow?.flow?.question?.title || projectName(v.pid)}的代表性论文。`)));
        if(!draft && v.material?.workflow?.flow?.plan?.item_id!==item.item_id && !stale)controls.append(button("设为当前方案",async()=>{try{await request(`/api/projects/${encodeURIComponent(v.pid)}/workspace/focus`,{method:"PUT",body:{expected_revision:v.material.workspace.revision,focus:{question:item.plan?.question_id || item.related_ids[0],plan:item.item_id}}});await refreshMaterials(v);}catch(e){fail(v,e);}}));
        controls.append(button('修改条件',()=>preparePlanInput(v,'请讨论并修订这份方案的条件，变更要求：',{method:item.plan?.method || null,question_id:item.plan?.question_id || null,supersedes_id:draft ? item.supersedes_id || null : item.item_id})));
        if(!draft)controls.append(button("请求修订方案",()=>preparePlanInput(v,`请修订方案“${item.title}”，修改要求：`,{method:item.plan?.method || null,question_id:item.plan?.question_id || item.related_ids?.find(id=>v.material.workspace.items.some(i=>i.item_id===id && i.kind==="question")),supersedes_id:item.item_id})));
        if(draft && !item.applied_item_id){const b=button(stale ? "依据待复核，不能直接采纳" : "采纳方案",()=>adopt(v,item.suggestion_id));b.disabled=stale || v.adopting;controls.append(b);}
        if(draft && !item.applied_item_id)controls.append(button("编辑草案修订要求",()=>preparePlanInput(v,`请重新起草${item.title}，修改要求：`,{method:item.plan?.method || null,question_id:item.plan?.question_id || null,supersedes_id:item.supersedes_id || null})));
        }
        const link=E("a","btn btn-secondary","下载此版本（MD）");link.href=BASE+`/api/projects/${encodeURIComponent(v.pid)}`+(draft ? `/conversations/${encodeURIComponent(v.cid)}/suggestions/${encodeURIComponent(item.suggestion_id)}/export` : `/exports/items/${encodeURIComponent(item.item_id)}`);link.setAttribute("download","");controls.append(link);box.append(controls);return box;
    }
    function planPreview(v,s){const p=E('section','ch-proposal-preview');p.dataset.planPreviewId=s.suggestion_id;p.append(E('strong','',s.applied_item_id?'方案已采纳':'方案草案 · 待审阅'));
        const list=E('ul','ch-plan-preview-list');for(const [key,label] of [['objective','要研究什么'],['method','准备怎么研究'],['validation','如何判断'],['conditions','需要的条件']]){const text=s.plan.sections.find(sec=>sec.key===key)?.statements.map(st=>st.text).join('；') || '尚待确认';const n=E('li','',label+'：'+text.slice(0,110)+(text.length>110?'…':''));n.title=text;list.append(n);}list.append(E('li','','待确认：'+s.plan.unconfirmed.slice(0,2).join('；')));p.append(list);
        if(!s.applied_item_id && (s.basis_changed!==false || !s.material_fingerprint))p.append(E('p','ch-warning','依据待复核；可查看原草案，不能直接采纳。'));
        p.append(button('打开方案',()=>openMaterial(v,'proposal',s.suggestion_id)));return p;}
    function prepareSearchInput(v,text){
        if(!current(v))return false;
        if(v.r.input.value.trim()){notice(v,'输入框已有未发送草稿，请先整理现有内容，再准备检索依据；原草稿已保留。');if(!v.r.panel.hidden)panel(v,false,{restore:false});v.r.input.focus();return false;}
        return preparePlanInput(v,text,null);
    }
    async function preparePlanInput(v,text,request,discussOnly=false){
        if(!current(v) || v.preparingPlan)return false;
        if(request && v.r.input.value.includes('本次不生成或修订方案。')){notice(v,'现有草稿明确要求本次不生成或修订方案。请先修改这段约束，再准备方案请求；原草稿已保留。');if(!v.r.panel.hidden)panel(v,false,{restore:false});v.r.input.focus();return false;}
        const discussion=!request&&v.planRequest,prepared=discussOnly ? text+'\n\n本次不生成或修订方案。' : text;
        if(v.r.input.value.trim() || discussion){
            v.preparingPlan=true;let use=false;
            try{use=await new Promise(resolve=>{
                const dialog=E('dialog','ch-reply-confirm');dialog.setAttribute('aria-label','保留草稿并追加要求');
                dialog.append(E('h3','','保留现有草稿，追加这段要求？'),E('p','',prepared));
                if(discussion)dialog.append(E('p','',discussOnly?'这次将切换为普通讨论，清除附加的方案生成请求；原文字保留。':'这次将清除附加的方案生成请求；发送后才执行当前要求。'));
                const done=value=>{v.reads.signal.removeEventListener('abort',abort);if(dialog.open)dialog.close();dialog.remove();resolve(value);},abort=()=>done(false);
                const keep=button('保留草稿，暂不追加',()=>done(false));dialog.append(keep,button('保留并追加',()=>done(true)));dialog.addEventListener('cancel',e=>{e.preventDefault();done(false);},{once:true});
                v.reads.signal.addEventListener('abort',abort,{once:true});v.root.append(dialog);dialog.showModal();keep.focus();
            });}finally{v.preparingPlan=false;}
            if(!use || !current(v))return false;
        }
        const next=v.r.input.value.trim() ? v.r.input.value+"\n\n"+prepared : prepared;
        if(next.length>v.r.input.maxLength){notice(v,'追加后的内容超过输入长度上限，请先整理现有草稿再准备要求；原草稿已保留。');return false;}
        v.r.input.value=next;
        v.planRequest=request;if(request){v.r.planMethod.value=request.method || "";v.r.planQuestion.value=request.question_id || "";}v.r.planPending.textContent=request ? "下一次发送将请求方案草案；尚未调用模型，可取消或继续编辑。" : "";v.r.planTools.open=false;saveDraft(v);if(!v.r.panel.hidden)panel(v,false,{restore:false});v.r.input.focus();
        v.r.input.dispatchEvent(new Event('input'));return true;
    }
    async function chooseReply(v,message,option){
        if(!current(v) || v.busy || v.remoteBusy || v.optionSending)return;
        if(v.planRequest && v.r.input.value.trim()!==option.content){notice(v,'当前仍有待发送的方案请求，请先完成或取消原方案请求，再发送其他推荐内容；现有草稿已保留。');v.r.planTools.open=true;v.r.input.focus();return;}
        v.optionSending=true;paintMessages(v);
        try{
            if(v.r.input.value.trim() && v.r.input.value.trim()!==option.content){
                const dialog=E("dialog","ch-reply-confirm");dialog.setAttribute("aria-label","保留未发送草稿");dialog.append(E("h3","","输入框中已有未发送草稿"),E("p","","推荐内容："+option.content),E("p","","草稿将保留，不会被推荐内容覆盖。"));
                const use=await new Promise(resolve=>{const done=value=>{dialog.close();dialog.remove();resolve(value);};dialog.append(button("保留草稿，暂不发送",()=>done(false)),button("保留草稿并发送推荐",()=>done(true)));dialog.addEventListener("cancel",e=>{e.preventDefault();done(false);},{once:true});v.root.append(dialog);dialog.showModal();dialog.querySelector('button').focus();});
                if(!use || !current(v))return;
            }
            if(current(v))await firstSend(v,option.content);
        }finally{v.optionSending=false;if(current(v)){paintMessages(v);v.r.input.focus();}}
    }
    async function runAction(v,message,action){
        if(!current(v) || v.actionBusy)return;v.actionBusy=true;paintMessages(v);
        try{const result=await request(path(v,`/messages/${encodeURIComponent(message.message_id)}/actions/${encodeURIComponent(action.action_id)}`),{method:"POST",body:{expected_revision:v.material?.workspace.revision}});if(!current(v))return;
            if(result.type==="start_search"){await startFromCard(v,message,action);}
            else if(result.type==="open_settings"){v.root.querySelector('.ch-settings').open=true;v.r.mode.focus();}
            else if(result.type==="adopt_suggestion"){v.accepted.add(result.item.item_id);await refreshMaterials(v);notice(v,"已采纳到研究记录，不表示结论得到验证，也未启动研究。");window.dispatchEvent(new CustomEvent("sra:records-changed",{detail:{projectId:v.pid}}));}
            else if(result.type==="view_references" && result.page==="papers")g.app.navigate({page:"papers",paperId:result.paper_ids?.[0] || null});
            else if(result.type==="open_plan" && result.page==="experiments")g.app.navigate({page:"experiments"});
        }catch(e){if(current(v)){fail(v,e);if(e.status===409)await refreshMaterials(v).catch(()=>{});}}
        finally{v.actionBusy=false;if(current(v))paintMessages(v);}
    }
    async function settings(v,quiet=false){
        if(!v.cid)return;
        const values=readSettings(v);
        if(Object.keys(values).every(k=>values[k]===v.meta?.[k]))return;
        const data=await request(path(v),{method:"PATCH",body:values});
        v.meta=data.conversation;if(current(v)){paintHeader(v);if(!quiet)notice(v,"讨论设置已保存，不改变项目研究进度。");}
    }
    function pendingSave(v){if(v.pending)store.set(pendingKey(v),v.pending);}
    function clearPending(v,rid){if(v.pending?.body.request_id===rid){v.pending=null;store.remove(pendingKey(v));}}
    function applyResponse(v,result){
        v.requests[result.request_id]={status:result.status,error:result.error,callAudit:result.call_audit};merge(v,result.messages || []);
        if(result.status==="completed" || result.status==="failed" || result.status==="interrupted"){
            clearPending(v,result.request_id);
            if(v.waitingNotice===result.request_id){notice(v,"");v.waitingNotice=null;}
        }
        v.remoteBusy=Object.values(v.requests).some(r=>r.status==="processing");paintMessages(v);paintHeader(v);
    }
    function schedule(v){
        if(!current(v))return;clearTimeout(v.timer);
        v.timer=setTimeout(async()=>{
            if(!current(v))return;
            for(const [rid,r] of Object.entries(v.requests))if(["processing","unknown"].includes(r.status))await checkRequest(v,rid,true);
            if(current(v) && !v.r.panel.hidden)await refreshMaterials(v).catch(e=>{if(e.name!=="AbortError")fail(v,e);});
            schedule(v);
        },2500);
    }
    async function checkRequest(v,rid,quiet=false){
        if(!v.cid)return;
        try{const data=await request(path(v,`/requests/${encodeURIComponent(rid)}`),{signal:v.reads.signal});if(!current(v))return;
            v.deliveryBusy=data.status==='completed';applyResponse(v,data);
            try{if(data.status==="completed"){await newMessages(v);await refreshMaterials(v);}}
            finally{v.deliveryBusy=false;if(current(v))paintHeader(v);}}
        catch(e){if(!current(v) || e.name==="AbortError")return;
            if(e.status===404){
                if(!String(e.message).includes("消息请求")){v.writable=false;v.requests[rid]={status:"unavailable",error:textError(e)};fail(v,e);}
                else v.requests[rid]={status:"not_submitted",error:"尚未查到原请求。请核对网络后主动用原请求提交，不会自动重发。"};
                v.remoteBusy=Object.values(v.requests).some(r=>r.status==="processing");paintMessages(v);paintHeader(v);
            }
            else if(!quiet)fail(v,e);
        }
    }
    async function postMessage(v,body,retry=false){
        v.busy=true;v.requests[body.request_id]={status:"processing"};v.remoteBusy=true;paintHeader(v);paintMessages(v);schedule(v);
        try{
            const url=retry ? path(v,`/requests/${encodeURIComponent(body.request_id)}/retry`) : path(v,"/messages");
            // Do NOT attach navigation's read AbortSignal to a generation POST.
            const delivery=g.cap?.background_plan ? {wait_for_completion:false} : {};
            const result=await request(url,{method:"POST",body:retry ? delivery : {...body,...delivery}});
            if(current(v)){applyResponse(v,result);if(result.status==="completed"){await newMessages(v);await refreshMaterials(v);}}
            if(result.status==="search_started"){
                // 检索任务已接受：用户消息由任务服务持久化，轮询由任务卡片接管。
                v.requests[body.request_id]={status:"completed"};
                clearPending(v,body.request_id);
                if(current(v) && result.task)registerTask(v,result.task);
            }
            if(result.status==="completed" || result.status==="failed")clearPending(v,body.request_id);
        }catch(e){
            if(e.data?.request_id){if(current(v))applyResponse(v,e.data);if(["failed","interrupted","completed"].includes(e.data.status))clearPending(v,body.request_id);}
            else if(current(v)){
                v.requests[body.request_id]={status:"unknown",error:e.status===409 ? e.message : "响应未确认，正在查询原请求；切换页面不会取消服务端生成。"};
                await checkRequest(v,body.request_id,true);
            }
            if(current(v)){
                const status=v.requests[body.request_id]?.status;
                if(!e.data?.request_id && status==="completed")notice(v,"已按原请求核对：回复已保存，未重复提交。");
                else if(!e.data?.request_id && status==="processing"){v.waitingNotice=body.request_id;notice(v,"响应暂未返回，原请求仍在处理中；可切换会话，不会自动重发。");}
                else fail(v,e.name==="AbortError" ? new Error("等待响应超时。已查询原 request_id，不会自动换编号重发。") : e);
                if(e.status===409)await refreshMaterials(v).catch(()=>{});
            }
        }finally{v.busy=false;if(current(v)){paintHeader(v);paintMessages(v);conversations(v.pid);recentConversations();projects();schedule(v);}}
    }
    function fileKey(v){return 'sra.ch.file.'+scope(v.pid,v.cid);}
    function attachment(v,{requireFile=false}={}){
        const memory=g.files.get(scope(v.pid,v.cid));let state=store.get(fileKey(v));
        // The actual selected File owns an immutable selection identity. Repair a
        // missing local manifest from that same in-memory selection, never treat
        // it as "no attachment" and silently deliver a text-only request.
        if(!state && memory){state={...memory.state};store.set(fileKey(v),state);}
        if(!state && !memory)return null;
        if(memory && (!state.selection_id || state.selection_id!==memory.selection_id || state.name!==memory.file.name))throw new Error('附件选择状态不一致，尚未上传或发送。请重新选择文件，或明确移除附件；文字已保留。');
        if(requireFile && !memory)throw new Error('请重新选择待上传文件，再继续原项目；不会重新创建项目。');
        return {state:{...state},memory};
    }
    function paintFile(v){
        if(!current(v)||!v.r.filePending)return;
        v.r.filePending.replaceChildren();let selected;
        try{selected=attachment(v);}catch(e){v.r.filePending.append(E('span','ch-warning',e.message));}
        if(!selected && !g.files.has(scope(v.pid,v.cid)) && !store.get(fileKey(v)))return;
        if(selected)v.r.filePending.append(E('span','small',selected.memory?'待提交资料：'+selected.state.name:'上次选择的文件 '+selected.state.name+' 无法在刷新后恢复，请重新选择同一文件；文本草稿已保留。'));
        v.r.filePending.append(button('移除待上传文件',()=>{g.files.delete(scope(v.pid,v.cid));store.remove(fileKey(v));paintFile(v);},'ch-text-button'));
    }
    function chooseFile(v){
        if(!current(v))return;
        const input=E('input');input.type='file';input.accept='.pdf,.txt,.md';input.setAttribute('aria-label','选择待上传论文');
        input.hidden=true;v.root.append(input);input.addEventListener('cancel',()=>input.remove(),{once:true});
        input.onchange=()=>{
            input.remove();
            if(!input.files.length||!current(v))return;
            const f=input.files[0],old=store.get(fileKey(v));
            // Every selection is new, even the same File, filename or contents.
            // An existing upload identity is only a retry candidate: its hash is
            // still verified before status lookup/upload, never by name alone.
            const state={name:f.name,selection_id:uid(),...(old?.name===f.name && old.hash && old.request_id ? {hash:old.hash,request_id:old.request_id} : {})};
            g.files.set(scope(v.pid,v.cid),{selection_id:state.selection_id,file:f,state:{...state}});
            store.set(fileKey(v),state);paintFile(v);
        };input.click();
    }
    async function uploadSelected(v,snapshot=attachment(v,{requireFile:true})){
        if(!snapshot)return;
        const saved={...snapshot.state},memory=snapshot.memory,f=memory?.file;
        if(!f)throw new Error('请重新选择待上传文件，再继续原项目；不会重新创建项目。');
        const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await f.arrayBuffer())),b=>b.toString(16).padStart(2,'0')).join('');
        if(saved.hash && saved.hash!==hash)throw new Error('文件内容与上次上传不同。请先核对原上传，或明确移除后另行导入。');
        const previous=saved.request_id;
        saved.hash=hash;saved.request_id=saved.request_id||'upload-'+uid();memory.state={...saved};
        if(store.get(fileKey(v))?.selection_id===saved.selection_id)store.set(fileKey(v),saved);
        const clearUploaded=()=>{if(store.get(fileKey(v))?.selection_id===saved.selection_id)store.remove(fileKey(v));if(g.files.get(scope(v.pid,v.cid))?.selection_id===saved.selection_id)g.files.delete(scope(v.pid,v.cid));paintFile(v);};
        if(previous){
            let state;
            try{state=await request(`/api/projects/${encodeURIComponent(v.pid)}/upload-requests/${encodeURIComponent(previous)}`);}catch(e){if(e.status!==404)throw e;}
            if(state?.status==='completed'){if(state.paper_id&&!v.paperIds.includes(state.paper_id))v.paperIds.push(state.paper_id);clearUploaded();notice(v,'资料已保存，可在研究材料核对实际阅读范围。');window.dispatchEvent(new CustomEvent('sra:papers-changed',{detail:{projectId:v.pid}}));return;}
            if(state?.status==='processing')throw new Error('原上传仍在处理，请稍后核对并继续；未重新上传或解析。');
        }
        const fd=new FormData();fd.append('file',f);fd.append('request_id',saved.request_id);
        notice(v,'正在上传与解析所选资料；失败会保留归属和原请求编号，不会重建项目。');
        const response=await fetch(BASE+`/api/projects/${encodeURIComponent(v.pid)}/upload-paper`,{method:'POST',body:fd});const result=await response.json();
        if(!response.ok)throw new Error(result.detail || '上传未成功，文件与文字已保留，可在原项目重试。');
        if(response.status===202)throw new Error('原上传仍在处理中，请稍后核对并继续，不要另建项目。');
        if(result.paper_id&&!v.paperIds.includes(result.paper_id))v.paperIds.push(result.paper_id);
        clearUploaded();
        window.dispatchEvent(new CustomEvent('sra:papers-changed',{detail:{projectId:v.pid}}));
        if(result.parse_error)notice(v,'资料已保存，但正文未成功解析；后续仅按实际可读范围讨论，可到文献与资料核对。');
        else notice(v,'资料已保存，可在研究材料核对实际阅读范围。');
    }
    function confirmDomain(v){
        return new Promise(resolve=>{
            const dialog=E('dialog','case-dialog'),select=E('select');select.setAttribute('aria-label','确认研究领域');select.append(new Option('请选择最贴近的领域',''));
            for(const d of g.cap?.domains||[])select.add(new Option(d.label,d.value));
            // A local suggestion is not a persisted user fact until confirmed.
            const text=v.r.input.value;const guess=/联邦学习|大模型|人工智能|机器学习/.test(text)?'ai':/硫化物|材料|电解质/.test(text)?'materials':'';
            if([...select.options].some(o=>o.value===guess))select.value=guess;
            const finish=ok=>{dialog.close();dialog.remove();resolve(ok);};
            dialog.append(E('h3','','确认一个研究领域'),E('p','','只用于选择合适的研究支持，不改变你提供的条件；其他讨论设置可稍后调整。'),select,button('确认并继续提交',()=>{if(!select.value){select.focus();return;}v.r.domain.value=select.value;v.domainConfirmed=true;saveDraft(v);finish(true);}),button('返回编辑，不创建项目',()=>finish(false)));
            dialog.addEventListener('cancel',e=>{e.preventDefault();finish(false);},{once:true});v.root.append(dialog);dialog.showModal();select.focus();
        });
    }
    async function firstSend(v,text){
        if(v.busy || v.remoteBusy || !v.writable)return;
        if(v.planRequest){const q=(v.material?.workflow?.flow?.questions || []).find(q=>q.item_id===v.r.planQuestion.value);if(!q || q.needs_review){notice(v,'请先明确选择一个可用且无需复核的研究问题；原草稿和方案请求已保留，尚未发送。');if(!v.r.panel.hidden)panel(v,false,{restore:false});v.r.planTools.open=true;v.r.planQuestion.focus();return;}v.planRequest.question_id=q.item_id;saveDraft(v);}
        if(!await allowGeneration(v) || v.busy || v.remoteBusy)return;
        try{attachment(v,{requireFile:true});}catch(e){fail(v,e);paintFile(v);return;}
        if(v.material?.teaching&&!v.cid){notice(v,'请继续原案例，或明确开始自己的研究；这里不会创建不可使用的对话。');return;}
        if(!v.pid && !v.startPending && (!v.r.domain.value || !v.domainConfirmed)){if(v.domainDialog)return;v.domainDialog=true;const ok=await confirmDomain(v);v.domainDialog=false;if(!ok||!current(v))return;}
        v.busy=true;notice(v,"");paintHeader(v);
        try{
            let start=store.get("sra.ch.start."+scope(v.pid,null));
            if(!v.cid){
                if(!start){start={request_id:"create-"+uid(),project_id:v.pid,title:compactName(text,'待明确主题的研究'),domain:v.r.domain.value,conversation_title:discussionName(text,v.r.stage.value),...readSettings(v),message:{request_id:"message-"+uid(),content:text,selected_item_id:v.selectedItemId || null,selected_paper_ids:v.paperIds.slice(),...(v.planRequest ? {plan_request:v.planRequest} : {})}};store.set("sra.ch.start."+scope(v.pid,null),start);}
                v.requests[start.message.request_id]={status:"creating"};
                merge(v,[{message_id:"pending:"+start.message.request_id,request_id:start.message.request_id,role:"user",content:start.message.content,optimistic:true}]);
                paintMessages(v,{force:true});
                const oldKey="sra.ch.start."+scope(v.pid,null), oldDraft=draftKey(v),oldScope=scope(v.pid,v.cid),oldFile=fileKey(v),fileSnapshot=attachment(v,{requireFile:true});
                const result=await request("/api/conversations/start",{method:"POST",body:Object.fromEntries(Object.entries(start).filter(([k])=>k!=="message"))});
                v.pid=result.project_id;v.cid=result.conversation_id;v.meta=result.conversation;v.startPending=false;
                if(fileSnapshot){
                    const id=fileSnapshot.state.selection_id,destination=store.get(fileKey(v));
                    if(destination && destination.selection_id!==id)throw new Error('该项目已有另一份待上传附件，未覆盖。请回到对应项目核对，原文件与文字仍保留。');
                    store.set(fileKey(v),fileSnapshot.state);g.files.set(scope(v.pid,v.cid),fileSnapshot.memory);
                    if(store.get(oldFile)?.selection_id===id)store.remove(oldFile);
                    if(g.files.get(oldScope)?.selection_id===id)g.files.delete(oldScope);
                }
                v.pending={body:start.message,awaiting_upload:!!fileSnapshot};v.planRequest=null;v.r.planPending.textContent="";pendingSave(v);if(store.get(oldKey)?.request_id===start.request_id)store.remove(oldKey);
                if(current(v)){g.app.adopt(v.pid,v.cid);if(store.get(oldDraft)?.text?.trim()===start.message.content)store.remove(oldDraft);saveDraft(v);await projects();conversations(v.pid);}
                await uploadSelected(v,fileSnapshot);v.pending.awaiting_upload=false;v.pending.body.selected_paper_ids=v.paperIds.slice();pendingSave(v);
                const data=await request(`/api/projects/${encodeURIComponent(v.pid)}/workspace?include_chat=false`);v.material=data;
                v.pending.body.expected_revision=data.workspace.revision;pendingSave(v);
                merge(v,[{message_id:"pending:"+start.message.request_id,request_id:start.message.request_id,role:"user",content:start.message.content,optimistic:true,request_status:"processing"}]);
                if(current(v)){if(v.r.input.value.trim()===start.message.content)v.r.input.value='';saveDraft(v);setSettings(v,v.meta);paintHeader(v);paintMessages(v,{force:true});}
                await postMessage(v,v.pending.body);
            }else{
                if(v.pending?.awaiting_upload){await uploadSelected(v);await refreshMaterials(v);v.pending.awaiting_upload=false;v.pending.body.selected_paper_ids=v.paperIds.slice();v.pending.body.expected_revision=v.material.workspace.revision;pendingSave(v);if(current(v)&&v.r.input.value.trim()===v.pending.body.content){v.r.input.value='';saveDraft(v);}await postMessage(v,v.pending.body);return;}
                await settings(v,true);
                await uploadSelected(v);await refreshMaterials(v);
                const body={request_id:"message-"+uid(),content:text,selected_item_id:v.selectedItemId || null,selected_paper_ids:v.paperIds.slice(),expected_revision:v.material.workspace.revision,...(v.planRequest ? {plan_request:v.planRequest} : {})};
                v.planRequest=null;v.r.planPending.textContent="";
                v.pending={body};pendingSave(v);
                merge(v,[{message_id:"pending:"+body.request_id,request_id:body.request_id,role:"user",content:text,optimistic:true,request_status:"processing"}]);
                if(current(v)){if(v.r.input.value.trim()===text)v.r.input.value="";saveDraft(v);paintMessages(v,{force:true});}
                await postMessage(v,body);
            }
        }catch(e){
            v.startPending=!v.cid && !!store.get("sra.ch.start."+scope(v.pid,null));
            // A failed create is not a running generation. Keep its stable create
            // identity and original input for an explicit retry, never a fake wait.
            for(const r of Object.values(v.requests))if(r.status==='creating'){r.status='not_submitted';r.error='项目或对话尚未建立，输入已保留；请核对后重试。';}
            if(v.pending?.awaiting_upload){v.requests[v.pending.body.request_id]={status:'not_submitted',error:'项目与对话已建立，资料尚未确认上传成功；文字保留，继续时核对原上传。'};pendingSave(v);}
            if(current(v)){paintMessages(v);fail(v,e);}
        }
        finally{v.busy=false;paintHeader(v);}
    }
    async function retry(v,rid){if(v.busy || v.remoteBusy)return;if(!await allowGeneration(v) || v.busy || v.remoteBusy)return;await postMessage(v,{request_id:rid},true);}
    async function resend(v){if(v.busy || v.remoteBusy || !v.pending)return;if(!await allowGeneration(v) || v.busy || v.remoteBusy || !v.pending)return;if(v.pending.awaiting_upload){await firstSend(v,v.pending.body.content);return;}await postMessage(v,v.pending.body);}
    function panel(v,open,{refresh=true,restore=true}={}){
        if(!current(v))return;
        const wasOpen=!v.r.panel.hidden,center=v.root.querySelector('.ch-center');
        if(open){closePopups();if(!wasOpen){v.panelOrigin=document.activeElement;v.panelChatTop=v.r.messages.scrollTop;}
            v.r.panel.hidden=false;v.r.panelToggle.setAttribute('aria-expanded','true');center.inert=innerWidth<=900;
            v.r.panel.setAttribute('role',innerWidth<=900?'dialog':'complementary');if(innerWidth<=900)v.r.panel.setAttribute('aria-modal','true');
            selectMaterialTab(v,v.materialTab||'overview',{reset:false});if(refresh)refreshMaterials(v).catch(e=>fail(v,e));if(!wasOpen)v.r.panelClose.focus({preventScroll:true});
        }else{const overlay=innerWidth<=900 || v.root.querySelector('.ch-shell').classList.contains('reading-wide');v.r.panel.hidden=true;v.r.panelToggle.setAttribute('aria-expanded','false');center.inert=false;v.root.querySelector('.ch-shell').classList.remove('reading-wide');v.r.panelWide.textContent='扩大阅读';
            if(restore){if(overlay)v.r.messages.scrollTop=v.panelChatTop??v.r.messages.scrollTop;const target=v.panelOrigin?.isConnected&&!v.panelOrigin.closest('[hidden]')?v.panelOrigin:v.r.panelToggle;target.focus({preventScroll:true});}
        }
    }
    function visibleContent(v,content){for(const p of v.material?.papers || [])content=content.split(p.paper_id).join(`${p.authors?.[0] || p.title}（${p.year || '年份待核对'}）`);for(const item of v.material?.workspace?.items || [])content=content.split(item.item_id).join(item.title);return content.replace(/参考论文 ID：/g,"参考资料：");}
    async function openMaterial(v,kind,id){
        if(!current(v))return;
        try{
        if(kind==='proposal'||kind==='plan')v.r.materialBody.querySelectorAll('.ch-plan-review').forEach(n=>n.open=false);
        panel(v,true,{refresh:false});v.r.panel.setAttribute('aria-busy','true');await refreshMaterials(v);if(!current(v))return;
        const savedId=kind==="proposal" ? v.suggestions.find(s=>s.suggestion_id===id)?.applied_item_id : null;
        const suggestion=kind==='proposal'?v.suggestions.find(s=>s.suggestion_id===id):null;
        selectMaterialTab(v,['plan','record'].includes(kind)||kind==='proposal'&&suggestion?.kind==='plan'?'plans':['papers','report'].includes(kind)?'papers':'overview');
        if(kind==='report'){await loadMaterialReports(v,{id});return;}
        if(kind==='record'){
            const item=v.material.workspace.items.find(i=>i.item_id===id&&i.kind==='plan');if(!item)throw new Error('所选方案不存在或不属于当前项目');
            let target=v.r.materialBody.querySelector(`[data-plan-id="${CSS.escape(id)}"]`);if(target){target.closest('[data-material-kind="history"]')?.setAttribute('open','');target.open=true;target.scrollIntoView({block:'start'});}return;
        }
        if(savedId && v.material?.workflow?.flow?.plan?.item_id===savedId)kind="plan";
        const selector=kind==="proposal" ? `[data-suggestion-id="${CSS.escape(id)}"]` : `[data-material-kind="${kind}"]`;
        let target=v.r.materialBody.querySelector(selector);
        if(!target && savedId){target=v.r.materialBody.querySelector(`[data-plan-id="${CSS.escape(savedId)}"]`);target?.closest('[data-material-kind="history"]')?.setAttribute('open','');}
        if(target){if(target.tagName==="DETAILS")target.open=true;target.querySelectorAll('.ch-plan-review').forEach(d=>d.open=true);if(kind==="papers"){const paper=id ? target.querySelector(`[data-paper-id="${CSS.escape(id)}"]`) : target.querySelector('.ch-paper-card');if(paper){paper.open=true;target=paper;}}target.scrollIntoView({block:"start"});target.tabIndex=-1;target.focus({preventScroll:true});}
        }catch(e){if(e.name!=="AbortError")fail(v,e);}finally{if(current(v))v.r.panel.removeAttribute('aria-busy');}
    }
    /* ---- 第五步：对话驱动的检索任务卡片 ---- */
    function taskPapers(v,t){
        const excluded=new Set((t.excluded||[]).map(p=>p.paper_id)),representative=(t.report?.representative||[]).map(p=>p.paper_id);
        const ids=representative.length?representative:(t.saved_paper_ids||[]);
        return [...new Set(ids)].filter(id=>!excluded.has(id)).map(id=>v.material?.papers?.find(p=>p.paper_id===id)).filter(Boolean);
    }
    async function prepareTaskDiscussion(v,t,step){
        if(!current(v)||v.busy||v.remoteBusy||v.routeBusy)return;
        const papers=taskPapers(v,t).slice(0,3);
        if(!papers.length){notice(v,'本轮尚无可关联的资料，请先打开报告核对或补充论文。');return;}
        const prompt=(step==='question'?'请结合这批资料和我已有的想法，先讨论可以回答的具体研究问题，给待我确认的候选，不自动采用。':'请带我阅读这批资料，先解释研究对象、方法与局限，并说明对当前方向有什么帮助。')
            +'\n本次参考：'+papers.map(p=>'《'+p.title+'》').join('、')+'。\n依据是检索时保存的摘要或元信息，请核对当前材料；不将其当作全文或已验证结论。本次不检索或搜索。';
        if(!await preparePlanInput(v,prompt,null,true)||!current(v))return;
        v.paperIds=papers.map(p=>p.paper_id);saveDraft(v);await chooseResearchStep(v,step);
    }
    function taskResult(v,t){
        if(!['completed','partial','failed','interrupted','cancelled'].includes(t.status))return null;
        const report=t.report,box=E('section','ch-task-result'),papers=taskPapers(v,t),included=report?.coverage?.included_count ?? t.included_count;
        box.append(E('strong','',report?'本轮检索摘要'+(t.current_version?' · v'+t.current_version:''):['completed','partial'].includes(t.status)?'已保存报告的摘要':'本轮整理尚未完成'));
        const names=new Map((v.material?.papers||[]).map(p=>[p.paper_id,'《'+p.title+'》']));
        (report?.representative||[]).forEach((p,i)=>{if(!names.has(p.paper_id))names.set(p.paper_id,'资料 '+(i+1));});
        const readable=text=>{let value=String(text||'');for(const [id,name] of names)value=value.replaceAll(id,name);return value;};
        const excerpt=(parent,text)=>{const value=readable(text);parent.append(E('p','ch-task-result-summary',value.length>500?value.slice(0,500)+'…':value));if(value.length>500){const full=E('details');full.append(E('summary','','展开完整摘要'),E('p','ch-task-result-summary',value));parent.append(full);}};
        if(report){
            if(t.status==='partial')box.append(E('p','ch-warning small','本轮只有部分结果，来源失败或解析不完整；已有可读资料可以继续使用。'));
            if(!['completed','partial'].includes(t.status))box.append(E('p','ch-warning small','以下是之前保存的报告；本次尝试尚未完成，不代表本次已取得新结果。'));
            if(t.basis_changed)box.append(E('p','ch-warning small','整理期间项目资料发生变化，请先核对报告和当前材料。'));
            if(included===0)excerpt(box,(t.candidates_count||0)>0?'本轮返回了资料，但筛选后没有纳入整理。可以查看排除理由，或调整检索范围；这不代表领域没有相关研究。':t.status==='partial'?'本轮没有取得可纳入的资料，且部分来源未成功。请核对来源情况后再决定是否补跑。':'本次查询没有取得可纳入的资料，不代表领域无人研究。可以换一个范围或带入已有论文。');
            else{
                if(report.mode==='rules')box.append(E('p','ch-task-result-summary','已整理可供阅读的资料；本报告采用规则汇总，没有完成方法或结论分析。'));
                if(report.methods_relations)excerpt(box,report.methods_relations);
                const list=E('ul');for(const entry of (report.representative||[]).slice(0,3)){
                    const row=E('li'),paper=papers.find(p=>p.paper_id===entry.paper_id);
                    row.append(paper?button(paper.title,()=>openMaterial(v,'papers',paper.paper_id),'ch-text-button'):E('strong','',names.get(entry.paper_id)));
                    if(entry.point)excerpt(row,entry.point);list.append(row);
                }if(list.children.length)box.append(list);
                if(!report.methods_relations&&!list.children.length)box.append(E('p','ch-task-result-summary','报告已保存，尚无可展示的内容摘要；可打开完整报告核对。'));
            }
            const coverage=report.coverage;
            box.append(E('p','ch-task-result-scope',(coverage?`纳入 ${coverage.included_count} 条；摘要 ${coverage.abstract_count} 条，仅元信息 ${coverage.metadata_only_count} 条。`:'')+(report.evidence_note || '这是检索时保存的资料快照，未核对全文。')));
            if(report.limitations?.length){const limits=E('details');limits.append(E('summary','','这批资料的局限'));for(const item of report.limitations)excerpt(limits,item.point);box.append(limits);}
            if([t.planning_note,t.ranking_note,t.synthesis_note].some(Boolean))box.append(E('p','ch-warning small','查询、筛选或整理中有环节使用规则回退，具体说明见检索审计。'));
        }else box.append(E('p','ch-task-result-summary',!Object.hasOwn(t,'report')?(t._reportError || '正在读取已保存报告的摘要…'):(t.saved_paper_ids?.length?'已保存的资料仍在项目中，可以先阅读；':'')+'尚无成功保存的内容报告，请核对任务状态后再决定是否重试。'));
        const next=E('div','ch-task-result-next'),locked=v.busy||v.remoteBusy||v.routeBusy;
        if(t._reportError)next.append(button('重新读取摘要',()=>pollTask(v,t.task_id),'ch-text-button'));
        if(papers.length&&included!==0&&!t.basis_changed){
            const read=button('阅读这批资料',()=>prepareTaskDiscussion(v,t,'reading'),'btn btn-secondary');read.disabled=locked;next.append(read);
            const question=button('讨论研究问题',()=>prepareTaskDiscussion(v,t,'question'),'ch-text-button');question.disabled=locked;next.append(question);
            box.append(E('p','ch-task-result-scope','下一步只准备可编辑讨论，发送后才处理；最多关联三篇资料，问题与方案仍需你确认。'));
        }else if(report){next.append(button('核对完整报告',()=>openMaterial(v,'report',t.task_id),'ch-text-button'));}
        if(report&&included===0){const adjust=button('调整检索范围',()=>startFromCard(v,{content:'按调整后的范围检索相关资料。'},{topic:t.topic}),'ch-text-button');adjust.disabled=locked;next.append(adjust);}
        box.append(next);return box;
    }
    function registerTask(v,view){
        if(!view || !view.task_id)return;
        v.tasks[view.task_id]={...v.tasks[view.task_id],...view};
        paintTasks(v);
        pollTask(v,view.task_id);
    }
    async function pollTask(v,taskId){
        if(!current(v) || !v.tasks[taskId])return;
        let state=v.tasks[taskId];
        if(["completed","partial","failed","interrupted","cancelled"].includes(state.status)){
            if(!Object.hasOwn(state,'report')){
                if(state._readingReport)return;
                state._readingReport=true;
                try{const data=await request(path(v,`/research-tasks/${encodeURIComponent(taskId)}`),{signal:v.reads.signal});if(!current(v))return;state=v.tasks[taskId]={...state,...data,report:data.report ?? null,_reportError:null};}
                catch(e){if(!current(v)||e.name==='AbortError')return;state._reportError='摘要暂时无法读取，已有报告和资料保留；可重新读取，不会重跑检索。';paintTasks(v);return;}
                finally{state._readingReport=false;}
            }
            if(!state._settled){state._settled=true;paintTasks(v);await newMessages(v).catch(()=>{});await refreshMaterials(v).catch(()=>{});}
            return;
        }
        try{
            const data=await request(path(v,`/research-tasks/${encodeURIComponent(taskId)}`),{signal:v.reads.signal});
            if(!current(v))return;
            v.tasks[taskId]={...state,...data};
            paintTasks(v);
            v.taskTimer=setTimeout(()=>pollTask(v,taskId),1500);
        }catch(e){if(!current(v) || e.name==="AbortError")return;v.tasks[taskId]={...state,status:"unknown",error:e.message};paintTasks(v);}
    }
    function paintTasks(v){
        if(!current(v) || !v.r.taskBox)return;
        const box=v.r.taskBox, top=v.r.messages.scrollTop, near=v.r.messages.scrollHeight-top-v.r.messages.clientHeight<85;
        const previous=v.taskDisplay || new Map();v.r.messages.querySelectorAll('.ch-task-card').forEach(n=>previous.set(n.dataset.taskId,{open:n.open,details:n.querySelector('.ch-task-diagnostics')?.open,source:n.querySelector('.ch-task-detail')?.open}));v.taskDisplay=previous;
        v.r.messages.querySelectorAll('.ch-task-card').forEach(n=>n.remove());
        box.replaceChildren();
        const tasks=Object.values(v.tasks).sort((a,b)=>(a.created_at||"").localeCompare(b.created_at||""));
        const failLabel={proxy_error:"代理连接失败",timeout:"来源超时",rate_limited:"限流，请等待后重试",authentication:"认证失败",access_denied:"来源拒绝访问",parse_error:"来源内容无法解析",budget_exhausted:"本次调用上限已达到",auth_or_blocked:"认证或拦截",network:"网络不可达",unknown:"未知错误"};
        for(const t of tasks){
            const card=E("details","ch-task-card");card.dataset.taskId=t.task_id;
            card.open=previous.get(t.task_id)?.open ?? (["running","queued"].includes(t.status)||t===tasks.at(-1));
            const failed=t.failed_sources?.length ? ` · 失败来源：${t.failed_sources.join("、")}` : "";
            card.append(E("summary","",`文献检索：${t.topic} · ${t.status_label}${failed}`));
            const body=E("details","ch-task-body ch-task-diagnostics");body.open=previous.get(t.task_id)?.details || false;body.append(E('summary','','范围、来源与调用详情'));
            if(t.degraded_sources?.length)body.append(E("p","ch-warning small","部分资料无法解析，已保留可读结果："+t.degraded_sources.join("、")));
            if(t.error)body.append(E("p","ch-warning",t.error));
            // 降级必须可见：规划/筛选/整理任何环节未由模型完成时明确标注。
            for(const note of [t.planning_note,t.ranking_note,t.synthesis_note].filter(Boolean))
                body.append(E("p","ch-warning small","降级说明："+note));
            body.append(E("p","muted small",`查询（${t.queries?.length || 0}/${t.limits?.max_queries || 3}）：${(t.queries||[]).map(q=>q.query).join("；") || "规划中…"}`));
            const included=t.included_count ?? t.candidates_count ?? 0, excluded=t.excluded_count ?? 0;
            body.append(E("p","muted small",`当前步骤：${({planning:"规划查询",searching:"检索来源",ranking:"去重与筛选",saving:"保存材料",synthesizing:"整理研究现状",delivering:"交付结果",done:"已结束"})[t.stage] || t.stage} · 候选 ${t.candidates_count ?? 0} 条（纳入整理 ${included}、标记不相关 ${excluded}）· 已入库 ${t.saved_papers_count ?? 0} 条。候选为去重合并后的任务级总数（上限 ${t.limits?.max_candidates || 20}），非每条查询各自的上限`));
            for(const ex of (t.excluded||[]).slice(0,3))
                body.append(E("p","ch-warning small","未纳入整理："+ex.title+"（"+ex.relevance_reason+"）"));
            const calls=(t.source_calls||[]);
            if(t.report?.coverage)body.append(E("p","muted small",t.report.summary));
            if(calls.length){const detail=E("details","ch-task-detail");detail.append(E("summary","","来源执行明细（已折叠）"));
                for(const c of calls){const line=E("p","small",`${c.source} · ${c.query} · ${({ok:"成功",zero:"成功但无结果",error:"调用失败"})[c.status] || c.status}${c.error_class ? "（"+(failLabel[c.error_class]||c.error_class)+"）" : ""}${c.error ? "（"+c.error.slice(0,80)+"）" : ""}`);if(c.status==="error")line.className="small ch-warning";detail.append(line);}
                body.append(detail);}
            if(t.report?.gaps?.length){for(const gap of t.report.gaps.slice(0,3))body.append(E("p","ch-warning small","缺口："+gap));}
            const versions=(t.report_versions||[]);
            if(versions.length)body.append(E("p","muted small",`报告版本：${versions.map(x=>"v"+x.version+(x.resume?"（补跑）":"")).join("、")}（当前 v${t.current_version||versions[versions.length-1].version}）`));
            const controls=E("div","ch-task-controls");
            controls.append(button('查看检索审计',()=>{card.open=true;body.open=true;body.querySelector('.ch-task-detail')?.setAttribute('open','');},'ch-text-button'));
            if(["queued","running"].includes(t.status))controls.append(button("取消检索",()=>cancelTask(v,t.task_id),"btn btn-secondary btn-sm"));
            if(t.status==="partial")controls.append(button("补跑失败来源",()=>retryTask(v,t.task_id,false,true),"btn btn-secondary btn-sm"));
            if(["failed","interrupted","cancelled"].includes(t.status))controls.append(button("重试",()=>retryTask(v,t.task_id),"btn btn-secondary btn-sm"));
            if(t.saved_paper_ids?.length)controls.append(button("查看已入库文献",()=>openMaterial(v,'papers',t.saved_paper_ids[0]),"ch-text-button"));
            if(["completed","partial"].includes(t.status))controls.append(button('打开整理报告',()=>openMaterial(v,'report',t.task_id),'ch-text-button'));
            if(["completed","partial"].includes(t.status))controls.append(button("下载整理报告",()=>{window.open(BASE+`/api/projects/${encodeURIComponent(v.pid)}/research-tasks/${encodeURIComponent(t.task_id)}/export`,"_blank");},"ch-text-button"));
            for(const ver of versions.slice(0,-1))controls.append(button(`下载 v${ver.version}`,()=>{window.open(BASE+`/api/projects/${encodeURIComponent(v.pid)}/research-tasks/${encodeURIComponent(t.task_id)}/export?version=${ver.version}`,"_blank");},"ch-text-button"));
            body.append(E("p","muted small","候选、入库和纳入综合含义不同；正式问题和方案仍需你确认。"));
            const compact=E('p','ch-task-compact',(['completed','partial'].includes(t.status)?`已保存 ${t.saved_papers_count ?? 0} 篇资料；纳入 ${t.included_count ?? 0}，筛选排除 ${t.excluded_count ?? 0}。`:t.status==='failed'?'整理未完成；已保存资料保留，可查看原因后重试。':({planning:'正在规划查询',searching:'正在请求文献来源',ranking:'正在筛选候选',saving:'正在保存资料',synthesizing:'正在整理研究现状',delivering:'正在保存报告'})[t.stage] || t.status_label));
            card.append(compact);const result=taskResult(v,t);if(result)card.append(result);card.append(controls,body);
            const user=v.r.messages.querySelector(`[data-request-id="${CSS.escape(t.request_id || '')}"]`);
            // Completed tasks must remain discoverable even when their original
            // user message is outside the currently loaded history page.
            if(user)user.after(card);else box.append(card);
            if(previous.get(t.task_id)?.source)card.querySelectorAll('.ch-task-detail').forEach(n=>n.open=true);
        }
        box.hidden=!box.children.length;if(box.children.length)v.r.messages.append(box);
        v.r.messages.scrollTop=near ? v.r.messages.scrollHeight : top;
    }
    async function cancelTask(v,taskId){
        try{const data=await request(path(v,`/research-tasks/${encodeURIComponent(taskId)}/cancel`),{method:"POST"});if(!current(v))return;v.tasks[taskId]={...v.tasks[taskId],...data};paintTasks(v);notice(v,data.cancel_note || "已请求取消。");}
        catch(e){if(current(v))fail(v,e);}
    }
    async function retryTask(v,taskId,reSearch=false,resume=false){
        try{const data=await request(path(v,`/research-tasks/${encodeURIComponent(taskId)}/retry`),{method:"POST",body:{re_search:reSearch,resume:resume}});if(!current(v))return;v.tasks[taskId]={...v.tasks[taskId],...data,_settled:false};paintTasks(v);pollTask(v,taskId);}
        catch(e){if(current(v))fail(v,e);}
    }
    async function startFromCard(v,message,action){
        if(!g.cap?.research_search){notice(v,"当前后端尚未接入检索能力，卡片不可用。");return;}
        const dialog=E("dialog","ch-search-scope");dialog.setAttribute("aria-label","确认检索范围");
        const topic=E("input");topic.value=action.topic || "";topic.maxLength=200;topic.setAttribute("aria-label","检索主题");
        const yf=E("input");yf.type="number";yf.value=action.year_from || "";yf.placeholder="如 2020";yf.setAttribute("aria-label","起始年份");
        const yt=E("input");yt.type="number";yt.value=action.year_to || "";yt.placeholder="如 2026";yt.setAttribute("aria-label","结束年份");
        const build=()=>{dialog.replaceChildren(
            E("h3","","开始文献检索"),
            E("p","muted small","将执行有界检索：最多 3 条查询、每条至多 2 个来源、去重后至多 20 条候选；完成后在原会话交付整理结果。不启动实验或写作。"),
            E("label","","检索主题"),topic,
            E("div","ch-scope-years"),
            E("p","muted small","未限定的内容将保持未限定；不会替你添加额外约束。"),
            E("div","ch-task-controls"),
            button("开始检索",async()=>{
                const t=topic.value.trim();
                if(!t){notice(v,"请填写检索主题。");return;}
                dialog.close();dialog.remove();
                await submitTask(v,{content:message.content || t,topic:t,year_from:yf.value ? Number(yf.value) : null,year_to:yt.value ? Number(yt.value) : null,card_message_id:message.message_id,card_action_id:action.action_id,request_id:"task-"+uid()});
            },"btn"),
            button("取消",()=>{dialog.close();dialog.remove();},"btn btn-secondary"));
            dialog.querySelector(".ch-scope-years").append(E("label","","起始年份（可选）"),yf,E("label","","结束年份（可选）"),yt);
        };
        build();
        dialog.addEventListener("cancel",e=>{e.preventDefault();dialog.close();dialog.remove();},{once:true});
        v.root.append(dialog);dialog.showModal();topic.focus();
    }
    async function submitTask(v,body){
        try{
            const result=await request(path(v,"/research-tasks"),{method:"POST",body});
            if(!current(v))return;
            registerTask(v,result.task);
            await refreshMaterials(v).catch(()=>{});
        }catch(e){if(current(v))fail(v,e);}
    }
    function leave(){closePopups();if(g.v){clearTimeout(g.v.timer);clearTimeout(g.v.taskTimer);g.v.reads.abort();g.v=null;}}
    function mount(host,{projectId,conversationId}){
        const root=E("section","conversation-home");root.setAttribute("aria-label","中央科研对话");
        root.innerHTML=`<div class="ch-shell"><div class="ch-center">
          <header class="ch-heading"><div><div class="ch-project-line"><button type="button" class="ch-text-button" data-ref="project" aria-label="查看所属项目">开始新研究</button></div><h1 data-ref="title" tabindex="0">新对话</h1><button type="button" data-ref="progress" class="ch-stage ch-text-button">明确条件</button></div><div class="ch-heading-actions"><button class="btn btn-secondary" type="button" data-ref="panelToggle" aria-expanded="false">研究材料</button><details class="ch-title-menu"><summary aria-label="项目与对话操作">⋯</summary><div><button type="button" data-ref="renameProject" class="ch-text-button" hidden>修改项目名</button><button class="ch-text-button" type="button" data-ref="rename" hidden>重命名对话</button><button class="ch-text-button" type="button" data-ref="identity">查看与复制项目编号</button><button type="button" data-ref="relationship" class="ch-text-button">项目与对话有什么不同？</button><small data-ref="fullName" hidden></small></div></details></div></header>
          <div data-ref="welcome" class="ch-welcome"><span class="ch-eyebrow">SCIENTIFIC RESEARCH · HUMAN + AGENT</span><h2>从你现在的研究进度开始</h2><p>一起找方向、读论文、明确问题，再形成一份可以审阅和带走的研究方案。<br>选一个起点，或直接说说你现在想解决什么。</p><div class="ch-openers" aria-label="选择研究起点"><button type="button" data-entry="0" aria-pressed="false">第一次做研究，带我入门<span>从一个兴趣，找到可以继续了解的切入点</span></button><button type="button" data-entry="1" aria-pressed="false">我有方向，想找研究切入点<span>比较思路，明确需要补充哪些资料</span></button><button type="button" data-entry="3" aria-pressed="false">我有论文，想先读懂<span>围绕论文解释问题、方法与局限</span></button><button type="button" data-entry="2" aria-pressed="false">我有问题，想完善研究方案<span>先确认具体问题，再讨论怎样验证</span></button></div><section class="ch-entry-guide" data-ref="entryGuide" aria-live="polite" hidden><strong data-ref="entryTitle"></strong><p data-ref="entryOutcome"></p><p data-ref="entryNeed"></p><button type="button" class="ch-text-button" data-ref="entryUpload" hidden>选择论文</button></section><small>还不确定怎么开始？可以先体验一个完整案例。</small></div>
          <div class="ch-history-tools"><button type="button" class="ch-text-button" data-ref="earlier" hidden>加载更早消息</button><button type="button" class="ch-text-button" data-ref="latest" hidden>查看最新消息 ↓</button><span data-ref="loading" role="status">正在核对会话能力…</span></div>
          <div class="ch-messages" data-ref="messages" role="log" aria-label="当前会话消息" aria-live="polite" tabindex="0"></div>
          <form data-ref="form" class="ch-compose"><div class="ch-error" data-ref="error" role="status" hidden></div><label class="rg-sr-only" for="ch-input">向科研指导提问</label><textarea id="ch-input" data-ref="input" rows="2" maxlength="6000" placeholder="说说你的研究兴趣、已有想法，或当前遇到的问题…"></textarea><div class="ch-compose-footer"><div class="ch-compose-tools"><button type="button" class="ch-text-button" data-ref="import" title="导入／查看论文">＋ 资料</button><details class="ch-settings"><summary>讨论设置</summary><div class="ch-settings-grid"><label><span data-ref="belongsLabel">首次发送后归入</span><select data-ref="belongs" aria-label="对话归属"><option value="">＋ 开始新研究</option></select></label><label data-ref="domainWrap">研究领域<select data-ref="domain" aria-label="研究领域"><option value="">尚未确定 · 请确认</option></select></label><label>指导方式<select data-ref="mode" aria-label="指导方式"><option value="beginner">带我入门</option><option value="direct">直接处理问题</option><option value="case" disabled>教学设置（案例内固定）</option></select></label><label>进入阶段<select data-ref="stage" aria-label="进入阶段"><option value="orientation">明确条件</option><option value="literature">了解研究现状</option><option value="question">确认研究问题</option><option value="design">形成并审阅方案</option></select></label><label class="ch-goal">讨论目标<input data-ref="goal" aria-label="讨论目标" maxlength="2000" placeholder="例如：明确研究问题的范围" /></label><p class="muted small">设置不改变正式研究主线。新研究的领域需由你确认。</p><button type="button" class="btn btn-secondary" data-ref="saveSettings">保存讨论设置</button></div></details></div><span data-ref="composeScope" class="ch-compose-scope"></span><button type="submit" class="btn" data-ref="send" disabled>发送</button></div></form>
        </div><aside data-ref="panel" class="ch-material-panel" aria-label="当前项目研究材料" hidden><div class="ch-panel-header"><h2>研究材料</h2><button type="button" data-ref="panelWide" class="btn btn-secondary">扩大阅读</button><button type="button" data-ref="panelClose" class="btn btn-secondary">返回对话</button><div class="ch-material-tabs" data-ref="materialTabs" role="tablist" aria-label="研究材料分类"></div></div><div class="ch-material-scroll" data-ref="materialScroll" tabindex="0"><div data-ref="researchSummary" class="ch-overview" data-material-group="overview"><h3>研究概况</h3><p data-ref="confirmedQuestion"></p><p data-ref="confirmedBrief"></p><p data-ref="summaryHint"></p><button type="button" class="ch-text-button" data-ref="pendingItems">查看待确认事项</button><details class="ch-overview-stages"><summary>四阶段与正式材料状态</summary><ol data-ref="stageList"><li></li><li></li><li></li><li></li></ol><p>当前阶段来自已保存材料；讨论入口不代表前面步骤已完成。流程止于研究方案，不执行实验。</p></details></div><div data-ref="materialBody"></div><button type="button" class="ch-text-button" data-ref="refresh">刷新材料与草案</button></div></aside></div>`;
        const actions=root.querySelector('.ch-heading-actions');
        root.querySelector('.ch-welcome p').after(E('p','ch-first-send-note','选好起点后，输入内容并发送即可开始；也可以先选论文。'));
        const runtime=E('section','ch-runtime');runtime.dataset.ref='runtime';runtime.setAttribute('aria-label','运行配置与离线体验');runtime.setAttribute('role','status');root.querySelector('.ch-compose').prepend(runtime);
        const planTools=E("details","ch-plan-tools");planTools.dataset.ref="planTools";planTools.hidden=true;
        planTools.innerHTML='<summary>方案生成与审阅</summary><label>研究问题<select data-ref="planQuestion" aria-label="方案关联问题"></select></label><label>研究方法<select data-ref="planMethod" aria-label="方案研究方法"><option value="">尚未确定（先澄清）</option><option value="computational">计算研究</option><option value="theoretical">理论研究</option><option value="experimental">实验研究设计（仅规划）</option></select></label><p class="muted small">领域不决定方法。此处只准备可编辑请求，发送后才生成草案，不执行实验。</p><button type="button" class="btn btn-secondary" data-ref="preparePlan">准备生成方案请求</button><button type="button" class="ch-text-button" data-ref="cancelPlan">取消方案请求</button><p role="status" data-ref="planPending"></p>';
        root.querySelector('.ch-settings').after(planTools);
        const planPending=planTools.querySelector('[data-ref="planPending"]');planPending.className='ch-plan-pending';planTools.after(planPending);
        const planEligibility=E('p','muted small');planEligibility.dataset.ref='planEligibility';planTools.querySelector('[data-ref="preparePlan"]').before(planEligibility);
        planTools.addEventListener('toggle',()=>{if(planTools.open)root.querySelector('.ch-settings').open=false;});
        root.querySelector('.ch-settings').addEventListener('toggle',e=>{if(e.target.open)planTools.open=false;});
        const continueHint=E("p","ch-continue-hint");continueHint.dataset.ref="continueHint";continueHint.hidden=true;root.querySelector('.ch-history-tools').before(continueHint);
        const researchRoute=E('section','ch-research-route');researchRoute.dataset.ref='researchRoute';researchRoute.hidden=true;researchRoute.setAttribute('aria-label','科研讨论流程');root.querySelector('.ch-history-tools').before(researchRoute);
        const intro=E('div','ch-relationship');intro.dataset.ref='relationshipHint';intro.hidden=store.get('sra.ch.relationshipSeen',false);intro.append(E('span','','项目保存共同材料，对话保留一次讨论。'),button('知道了',()=>{intro.hidden=true;store.set('sra.ch.relationshipSeen',true);},'ch-text-button'));root.querySelector('.ch-history-tools').before(intro);
        const downloadToggle=button("下载当前产物",null);downloadToggle.dataset.ref="downloadToggle";downloadToggle.setAttribute("aria-expanded","false");actions.insertBefore(downloadToggle,actions.querySelector('.ch-title-menu'));
        const downloadPanel=E("section","ch-download-panel");downloadPanel.dataset.ref="downloadPanel";downloadPanel.hidden=true;downloadPanel.setAttribute("aria-label","下载当前项目产物");
        const closeDownloads=button("收起下载",()=>{downloadPanel.hidden=true;downloadToggle.setAttribute("aria-expanded","false");downloadToggle.focus();},"ch-text-button");
        const downloadBody=E("div");downloadBody.dataset.ref="downloadBody";downloadPanel.append(closeDownloads,button("刷新产物目录",()=>{if(g.v?.root===root)downloads(g.v);},"ch-text-button"),downloadBody);
        root.querySelector('.ch-heading').after(downloadPanel);
        const summary=E("section","ch-project-summary");summary.dataset.ref="summary";summary.hidden=true;
        const summaryText=E("p","muted");summaryText.dataset.ref="summaryText";
        const projectConversations=E("div","ch-project-conversations");projectConversations.dataset.ref="projectConversations";
        summary.append(E("h2","","继续这个项目"),summaryText,button("在本项目新建对话",()=>{root.querySelector('[data-ref="input"]').focus();}),button("查看文献与资料",()=>openMaterial(g.v,'papers')),projectConversations);
        root.querySelector('.ch-welcome').before(summary);
        const handoff=E('section','ch-project-summary');handoff.dataset.ref='handoff';handoff.hidden=true;root.querySelector('.ch-history-tools').before(handoff);
        const filePending=E('div','ch-file-pending');filePending.dataset.ref='filePending';filePending.setAttribute('role','status');root.querySelector('.ch-compose-footer').before(filePending);
        host.append(root);
        const r=Object.fromEntries([...root.querySelectorAll("[data-ref]")].map(n=>[n.dataset.ref,n]));
        const v=g.v={root,r,pid:projectId,cid:conversationId,meta:null,material:null,messages:[],requests:{},paperIds:[],suggestions:[],suggestionOffset:null,before:null,reads:new AbortController(),busy:false,remoteBusy:false,writable:false,accepted:new Set(),tasks:{},taskTimer:null};
        window.SRA_TEACHING?.attach({v,E,button,current,request,app:g.app,refreshMessages:()=>pageMessages(v,{replace:true}),refreshMaterials:()=>refreshMaterials(v),repaint:()=>paintMessages(v),openMaterial:(kind,id)=>openMaterial(v,kind,id),adopt:sid=>adopt(v,sid),closeMaterials:()=>panel(v,false),notice,fail});
        const taskBox=E("div","ch-task-box");taskBox.dataset.ref="taskBox";taskBox.hidden=true;taskBox.setAttribute("role","status");taskBox.setAttribute("aria-label","文献检索任务");r.messages.append(taskBox);r.taskBox=taskBox;
        const draft=store.get(draftKey(v),{});v.entryId=Number.isInteger(draft.entryId)&&ENTRY_PATHS[draft.entryId]?draft.entryId:null;v.domainConfirmed=draft.domainConfirmed===true;v.selectedItemId=draft.selectedItemId || null;v.paperIds=Array.isArray(draft.paperIds)?draft.paperIds.filter(id=>typeof id==='string').slice(0,3):[];v.planRequest=draft.planRequest || null;r.planMethod.value=draft.planMethod || "";r.input.value=draft.text || "";setSettings(v,draft.settings || {});v.startPending=!v.cid && !!store.get("sra.ch.start."+scope(v.pid,null));paintEntry(v);
        r.preparePlan.onclick=()=>preparePlanInput(v,"请为所选研究问题生成一份研究方案，具体要求：",{method:r.planMethod.value || null,question_id:r.planQuestion.value || null,supersedes_id:v.planRequest?.supersedes_id || null});
        if(v.planRequest)r.planPending.textContent="已恢复待发送的方案请求；尚未调用模型，可继续编辑。";
        r.planMethod.onchange=()=>{if(v.planRequest)v.planRequest.method=r.planMethod.value || null;saveDraft(v);};r.cancelPlan.onclick=()=>{v.planRequest=null;r.planPending.textContent="已清除附加的类型和修订信息，草稿保留；如果文字仍要求生成方案，请先修改文字再发送。";saveDraft(v);};
        r.form.onsubmit=e=>{e.preventDefault();const text=r.input.value.trim();if(v.startPending || v.pending?.awaiting_upload || text || store.get(fileKey(v)) || g.files.has(scope(v.pid,v.cid)))firstSend(v,text || '请根据我提供的论文资料，先帮助我理解研究内容与可讨论的问题；不自动确认问题或生成方案。');};
        const fitInput=()=>{r.input.style.height='auto';r.input.style.height=Math.min(r.input.scrollHeight,window.innerWidth<=700?112:170)+'px';};
        r.input.oninput=()=>{fitInput();saveDraft(v);};fitInput();
        let composing=false;r.input.addEventListener("compositionstart",()=>{composing=true;});r.input.addEventListener("compositionend",()=>{composing=false;});
        r.input.onkeydown=e=>{if((e.ctrlKey || e.metaKey) && e.key==="Enter" && !e.isComposing && !composing && e.keyCode!==229){e.preventDefault();r.form.requestSubmit();}};
        root.querySelectorAll('[data-entry]').forEach(b=>b.onclick=()=>selectEntry(v,Number(b.dataset.entry)));
        r.entryUpload.onclick=()=>chooseFile(v);
        for(const input of [r.mode,r.stage,r.goal])input.addEventListener('input',()=>{v.entryId=null;if(input===r.stage)v.discussionStep=({orientation:'conditions',literature:'reading',question:'question',design:'design'})[r.stage.value];paintEntry(v);saveDraft(v);});
        r.domain.onchange=()=>{v.domainConfirmed=!!r.domain.value;saveDraft(v);paintHeader(v);};
        r.belongs.onchange=()=>{if(r.belongs.value==="__more__"){r.belongs.value=v.pid || "";g.app.navigate({page:"create"});}else g.app.navigate({projectId:r.belongs.value || null,conversationId:null,page:"dashboard"});};
        r.saveSettings.onclick=()=>v.cid ? settings(v).catch(e=>fail(v,e)) : notice(v,"设置已留在当前草稿，首次发送时才创建会话并保存。");
        r.import.setAttribute('aria-label','导入／查看论文');r.import.onclick=()=>{if(!v.pid){chooseFile(v);return;}if(v.material?.teaching){openMaterial(v,'papers');return;}const d=E('dialog','case-dialog');d.append(E('h3','','当前项目资料'),button('添加待上传论文',()=>{d.close();d.remove();chooseFile(v);}),button('查看已有资料',()=>{d.close();d.remove();openMaterial(v,'papers');}),button('取消',()=>{d.close();d.remove();}));v.root.append(d);d.showModal();};r.rename.onclick=()=>renameConversation(v.pid,v.cid,v.meta.title);
        r.renameProject.onclick=()=>renameProject(v.pid,projectName(v.pid));r.pendingItems.onclick=()=>g.app.navigate({page:'human'});
        r.relationship.onclick=()=>{r.relationshipHint.hidden=false;};
        for(const [id,label] of [['overview','研究概况'],['papers','文献与报告'],['plans','研究方案']]){const tab=button(label,()=>selectMaterialTab(v,id));tab.id='ch-tab-'+id;tab.dataset.tab=id;tab.setAttribute('role','tab');tab.setAttribute('aria-controls','ch-material-content');r.materialTabs.append(tab);}
        r.materialScroll.id='ch-material-content';r.materialScroll.setAttribute('role','tabpanel');
        r.materialTabs.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;const tabs=[...r.materialTabs.children],i=tabs.indexOf(document.activeElement);if(i<0)return;e.preventDefault();const n=e.key==='Home'?0:e.key==='End'?tabs.length-1:(i+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;selectMaterialTab(v,tabs[n].dataset.tab);tabs[n].focus();});
        r.identity.onclick=async()=>{if(v.pid)try{await navigator.clipboard.writeText(v.pid);notice(v,'项目编号：'+v.pid+'（已复制）');}catch(_){notice(v,'项目编号：'+v.pid);}};
        r.panelToggle.onclick=()=>panel(v,r.panel.hidden);r.progress.onclick=()=>{panel(v,true);selectMaterialTab(v,'overview');};r.panelClose.onclick=()=>panel(v,false);r.refresh.onclick=()=>refreshMaterials(v).catch(e=>fail(v,e));
        r.panelWide.onclick=()=>{const wide=root.querySelector('.ch-shell').classList.toggle('reading-wide');r.panelWide.textContent=wide?'恢复侧栏':'扩大阅读';};
        r.downloadToggle.onclick=()=>{const open=r.downloadPanel.hidden;closePopups();if(!r.panel.hidden)panel(v,false,{restore:false});r.downloadPanel.hidden=!open;r.downloadToggle.setAttribute("aria-expanded",String(open));if(open){placeDownloads(v);downloads(v);}};
        window.addEventListener("resize",()=>{placeDownloads(v);if(!r.panel.hidden){root.querySelector('.ch-center').inert=innerWidth<=900;r.panel.setAttribute('role',innerWidth<=900?'dialog':'complementary');if(innerWidth<=900)r.panel.setAttribute('aria-modal','true');else r.panel.removeAttribute('aria-modal');}},{signal:v.reads.signal});
        r.earlier.onclick=()=>pageMessages(v,{earlier:true});r.latest.onclick=async()=>{if(v.cid)await pageMessages(v,{replace:true}).catch(e=>fail(v,e));if(current(v)){r.messages.scrollTop=r.messages.scrollHeight;r.latest.hidden=true;}};
        root.addEventListener("keydown",e=>{if(e.key==='Tab'&&!r.panel.hidden&&innerWidth<=900){const nodes=[...r.panel.querySelectorAll('button:not(:disabled):not([tabindex="-1"]),a[href],input,summary,[tabindex="0"]')].filter(n=>n.getClientRects().length);const first=nodes[0],last=nodes.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}});
        r.messages.addEventListener("scroll",()=>{r.latest.hidden=r.messages.scrollHeight-r.messages.scrollTop-r.messages.clientHeight<60;});
        paintHeader(v);paintMessages(v);paintMaterials(v);paintProjects();conversations(v.pid);recentConversations();
        loadRuntime(v);
        (async()=>{
            try{const cap=await capabilities();if(!current(v))return;
                for(const d of cap.domains)r.domain.add(new Option(d.label,d.value));r.domain.value=v.domainConfirmed ? draft.domain || '' : '';
                if(v.pid)await refreshMaterials(v);if(!current(v))return;
                if(v.cid){const meta=await request(path(v),{signal:v.reads.signal});if(!current(v))return;v.meta=meta.conversation;setSettings(v,v.meta);await pageMessages(v);}
                if(!current(v))return;v.writable=v.material?.research_scope?.supported!==false;r.loading.textContent="";
                v.pending=store.get(pendingKey(v));
                if(v.pending && v.cid){const body=v.pending.body;merge(v,[{message_id:"pending:"+body.request_id,request_id:body.request_id,role:"user",content:body.content,optimistic:true}]);v.requests[body.request_id]={status:v.pending.awaiting_upload?'not_submitted':'unknown',error:v.pending.awaiting_upload?'已保留项目、文字和原上传编号，请重新选择文件后继续。':null};if(!v.pending.awaiting_upload)await checkRequest(v,body.request_id,true);}
                // 恢复本会话未结束的检索任务（刷新/重进后继续显示真实状态，不重启任务）。
                if(v.cid && g.cap?.research_search && !v.material?.teaching){try{const list=await request(path(v,"/research-tasks")+"?limit=10",{signal:v.reads.signal});if(current(v)){for(const t of list.tasks)v.tasks[t.task_id]={...v.tasks[t.task_id],...t};paintTasks(v);for(const t of list.tasks)pollTask(v,t.task_id);}}catch(e){if(e.name!=="AbortError" && current(v))notice(v,"检索任务状态暂时无法读取："+e.message);}}
                paintHeader(v);paintMessages(v,{initial:true});schedule(v);
                if(g.pendingRead?.pid===v.pid){const target=g.pendingRead;g.pendingRead=null;await openMaterial(v,target.suggestionId?'proposal':'record',target.suggestionId || target.itemId);}
            }catch(e){if(e.name!=="AbortError"){r.loading.textContent="读取失败";fail(v,e);r.error.append(button("重新核对接口与材料",()=>g.app.navigate({...g.app.getRoute(),replace:true})));}}
        })();
    }
    function init(app){g.app=app;
        document.querySelectorAll("#nav .nav-item").forEach(n=>{n.tabIndex=0;n.setAttribute("role","button");n.onkeydown=e=>{if(e.key==="Enter" || e.key===" "){e.preventDefault();n.click();}};});
        $("ch-new").onclick=()=>app.navigate({projectId:null,conversationId:null,page:"dashboard"});
        $('ch-project-new').onclick=()=>{const route=app.getRoute();if(!route.projectId)return;app.navigate({projectId:route.projectId,conversationId:null,page:'dashboard'});};
        $('ch-current-project').onclick=()=>app.navigate({projectId:app.getRoute().projectId,conversationId:null,page:'dashboard'});
        $('ch-sidebar-help').onclick=()=>$('topbar-help').click();
        document.querySelectorAll('.ch-current-materials .nav-item').forEach(n=>n.addEventListener('keydown',e=>{if((e.key==='Enter'||e.key===' ')&&n.getAttribute('aria-disabled')!=='true'){e.preventDefault();n.click();}}));
        $('ch-conversation-collapse').onclick=()=>{g.recentWindow=5;recentConversations();};
        const toggleSide=()=>{if(innerWidth<=900){const open=document.body.classList.toggle('ch-nav-open');$('ch-menu').setAttribute('aria-expanded',String(open));}else {const collapsed=document.body.classList.toggle('ui-sidebar-collapsed');store.set('sra.ch.sidebarCollapsed',collapsed);$('ch-menu').setAttribute('aria-expanded',String(!collapsed));}};
        document.body.classList.toggle('ui-sidebar-collapsed',store.get('sra.ch.sidebarCollapsed',false));
        $('ch-sidebar-collapse').onclick=toggleSide;
        let searchTimer;$("ch-project-search").oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>projects(),180);};
        let conversationSearchTimer;$('ch-conversation-search').oninput=()=>{clearTimeout(conversationSearchTimer);g.recentWindow=5;conversationSearchTimer=setTimeout(()=>recentConversations(),180);};
        g.projectExpanded=store.get("sra.ch.projectsExpanded",false);
        $("ch-project-expand").onclick=()=>{g.projectExpanded=!g.projectExpanded;store.set("sra.ch.projectsExpanded",g.projectExpanded);projects();};
        $("ch-project-manage").onclick=()=>app.navigate({page:"create"});
        $("ch-menu").onclick=toggleSide;
        for(const [id,body] of [['ch-recent-group','ch-recent-body'],['ch-project-group','ch-project-body']]){const control=$(id),region=$(body);region.hidden=store.get('sra.ch.collapsed.'+id,false);control.setAttribute('aria-expanded',String(!region.hidden));control.onclick=()=>{region.hidden=!region.hidden;control.setAttribute('aria-expanded',String(!region.hidden));store.set('sra.ch.collapsed.'+id,region.hidden);};}
        document.addEventListener('click',e=>{const summary=e.target.closest('summary'),popup=summary?.parentElement?.matches(popupSelector)?summary.parentElement:null;if(popup){closePopups(popup);if(g.v&&!g.v.r.panel.hidden&&innerWidth<=900)panel(g.v,false,{restore:false});return;}document.querySelectorAll(popupSelector).forEach(n=>{if(n.open && (!n.contains(e.target)||e.target.closest('button,a')))n.open=false;});const v=g.v;if(v&&!v.r.downloadPanel.hidden&&!v.r.downloadPanel.contains(e.target)&&!v.r.downloadToggle.contains(e.target)){v.r.downloadPanel.hidden=true;v.r.downloadToggle.setAttribute('aria-expanded','false');}},true);
        document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!document.querySelector('dialog[open]')){const opened=!!document.querySelector(popupSelector.split(',').map(s=>s+'[open]').join(',')) || g.v&&!g.v.r.downloadPanel.hidden;if(opened)closePopups(null,{focus:true});else if(g.v&&!g.v.r.panel.hidden)panel(g.v,false);document.body.classList.remove('ch-nav-open');$('ch-menu').setAttribute('aria-expanded',String(innerWidth>900&&!document.body.classList.contains('ui-sidebar-collapsed')));}});
        window.addEventListener("sra:projects-changed",()=>{projects();recentConversations();});
        window.addEventListener("sra:context",()=>{navigationContext();projects();recentConversations();if(g.app.getRoute().projectId!==g.sidePid)conversations(g.app.getRoute().projectId);});
        window.addEventListener("focus",()=>{const v=g.v;if(v?.pid)refreshMaterials(v).catch(e=>{if(e.name!=="AbortError")fail(v,e);});});
        window.addEventListener("sra:discuss",e=>{const route=app.getRoute();if(e.detail.projectId!==route.projectId)return;const key="sra.ch.draft."+scope(route.projectId,route.conversationId),draft=store.get(key,{});if(!draft.text?.trim())draft.text=e.detail.message || "";draft.selectedItemId=e.detail.itemId || null;store.set(key,draft);app.navigate({page:"dashboard"});});
        projects();
    }
    function showPlan(pid,itemId){g.pendingRead={pid,itemId};g.app.navigate({projectId:pid,conversationId:g.app.getRoute().projectId===pid?g.app.getRoute().conversationId:null,page:'dashboard'});}
    function showProposal(pid,cid,suggestionId){g.pendingRead={pid,suggestionId};g.app.navigate({projectId:pid,conversationId:cid,page:'dashboard'});}
    window.SRA_CONVERSATIONS={init,mount,leave,capabilities,showPlan,showProposal};
})();

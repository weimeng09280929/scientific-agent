/* Workspace.brief is the only confirmed background. Tab-local drafts are never
   model context. Writes use the existing compare-and-save API, without tools. */
(() => {
    'use strict';
    const fields=[['goal','研究目标',2000],['background','已有基础',2000],['resources','资源条件',2000],['time_budget','时间安排',1000]];
    const E=(tag,text='',cls='')=>{const n=document.createElement(tag);n.textContent=text;n.className=cls;return n;};
    const button=(text,fn)=>{const n=E('button',text,'btn btn-secondary');n.type='button';n.onclick=fn;return n;};
    const equal=(a,b)=>fields.every(([k])=>a?.[k]===b?.[k]);
    const clone=b=>Object.fromEntries(fields.map(([k])=>[k,b[k]]));
    const clean=v=>v.trim()?v:'';
    const shown=v=>v.trim()?v:'尚未明确';
    const wireEqual=(a,b)=>!!a&&!!b&&a.expected_revision===b.expected_revision&&equal(a.brief,b.brief);
    let currentEditor=null;
    async function request(pid,method='GET',body,signal){
        const response=await fetch((window.SRA_API_BASE||'')+`/api/projects/${encodeURIComponent(pid)}/workspace`+(method==='GET'?'?include_chat=false':'/brief'),{method,signal,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined,cache:'no-store'});
        const data=await response.json().catch(()=>({}));
        if(!response.ok)throw Object.assign(new Error(typeof data.detail==='string'?data.detail:`项目背景请求失败（${response.status}）`),{status:response.status});
        const w=data.workspace;
        if(w?.project_id!==pid||!Number.isInteger(w.revision)||fields.some(([k])=>typeof w.brief?.[k]!=='string'))throw new Error('项目背景的归属或内容未能核对，未显示为空背景。');
        return data;
    }
    function list(brief){const n=E('dl','','project-background-fields');for(const [k,label] of fields){const value=E('dd',shown(brief[k]));value.dataset.backgroundField=k;n.append(E('dt',label),value);}return n;}
    function view(host,{projectId,workspace,app,onSaved}){
        const root=E('section','','project-background-view');root.dataset.projectId=projectId;
        root.append(E('p','保存后，同一项目后续对话可以参考这些信息。问题与正式方案仍以原记录为准；聊天和未采纳草案不会自动成为项目背景。','muted small'));
        if(workspace?.project_id===projectId&&fields.every(([k])=>typeof workspace.brief?.[k]==='string'))root.append(list(workspace.brief));
        else root.append(E('p','项目背景尚未成功读取，请重新读取后查看；未用空内容代替。','ch-warning'));
        root.append(button('编辑项目背景',()=>open({projectId,app,onSaved})));host.append(root);return root;
    }
    function open({projectId:pid,app,onSaved}){
        if(!pid||app.getRoute().projectId!==pid)return;
        currentEditor?.close(true);
        const key='sra.project-background.'+pid,dialog=E('dialog','','case-dialog project-background-editor'),title=E('h2','编辑项目背景'),status=E('p','','ch-warning'),form=E('form'),inputs={},diff=E('section','','project-background-diff'),conflict=E('section','','project-background-conflict'),result=E('div');
        let cached=null,cacheError=false;
        try{
            cached=JSON.parse(sessionStorage.getItem(key));
            const complete=b=>b&&fields.every(([k])=>typeof b[k]==='string');
            if(cached&&(cached.project_id!==pid||!Number.isInteger(cached.base?.revision)||cached.base.revision<0||!complete(cached.base.brief)||!complete(cached.draft)||cached.pending&&(!complete(cached.pending.brief)||cached.pending.expected_revision!==cached.base.revision)))throw new Error('Invalid local background recovery data');
        }catch(_){cached=null;cacheError=true;}
        let base=cached?.project_id===pid?cached.base:null,draft=base&&cached.draft?cached.draft:null,pending=base?cached.pending:null,latest=null,data=null,phase='loading',busy=false,closed=false,ticket=0,read=null,preview=null,verifiedOriginal=false;
        const editor={close};currentEditor=editor;
        const live=()=>!closed&&dialog.isConnected&&currentEditor===editor&&app.getRoute().projectId===pid;
        const snapshot=w=>({revision:w.revision,brief:clone(w.brief)});
        const values=()=>Object.fromEntries(fields.map(([k])=>[k,clean(inputs[k].value)]));
        const dirty=()=>base?fields.filter(([k])=>draft[k]!==base.brief[k]).map(([k])=>k):[];
        function persist(){sessionStorage.setItem(key,JSON.stringify({project_id:pid,base,draft,pending}));}
        function close(keep=false){
            if(closed)return;closed=true;ticket++;read?.abort();
            if(!pending&&!keep){try{sessionStorage.removeItem(key);}catch(_){}}
            dialog.close();dialog.remove();if(currentEditor===editor)currentEditor=null;
        }
        const cancel=button('取消编辑',()=>close(!!pending||['conflict','error'].includes(phase)));
        const loadButton=button('重新读取项目背景',()=>load());
        const back=button('返回修改',()=>{phase='edit';preview=null;render();inputs.goal.focus();});
        const check=button('核对保存结果',()=>load());
        const acknowledge=button('我已核对，保留我的修改',()=>{
            if(phase!=='conflict'||!latest||pending)return;
            const edited=dirty(),mine=clone(draft);base=snapshot(latest.workspace);draft=clone(base.brief);
            for(const k of edited)draft[k]=mine[k];
            for(const [k] of fields)inputs[k].value=draft[k];
            latest=null;phase='edit';preview=null;status.textContent='已保留你修改的字段，未修改字段使用最新内容。请再次预览，再明确保存。';
            try{persist();}catch(_){status.textContent+=' 本标签页无法保留输入，请先复制；未提交保存。';}render();inputs.goal.focus();
        });
        const previewButton=button('预览本次修改',()=>{
            if(phase!=='edit'||busy||!base)return;draft=values();preview={expected_revision:base.revision,brief:clone(draft)};
            if(!dirty().length){status.textContent='没有修改，不需要保存。';return;}
            try{persist();}catch(_){status.textContent='本标签页无法保留恢复信息，请先复制输入；本次未提交。';return;}
            phase='review';status.textContent='请核对以下变化。保存背景后，现有相关问题和正式方案会按原规则进入待复核；不会自动判定旧方案仍适用。';render();diff.focus();
        });
        const save=button('确认保存项目背景',()=>{
            if(phase!=='review'||busy||!preview)return;
            if(!equal(values(),preview.brief)){phase='edit';preview=null;status.textContent='输入发生变化，请重新预览。';render();return;}
            submit(preview);
        });
        const retry=button('用原版本重新提交本次修改',async()=>{
            if(!pending||!verifiedOriginal||busy)return;
            // Read once more before this explicitly requested, identical CAS.
            const original=pending;await load();if(live()&&pending&&wireEqual(pending,original)&&verifiedOriginal)submit(original);
        });
        const again=button('继续编辑',()=>{phase='edit';result.replaceChildren();status.textContent='';render();inputs.goal.focus();});
        for(const [k,label,max] of fields){const wrap=E('label'),input=E('textarea');input.name=k;input.maxLength=max;input.rows=3;input.setAttribute('aria-label',label);inputs[k]=input;wrap.append(E('span',label),input);form.append(wrap);
            input.addEventListener('input',()=>{if(phase!=='edit')return;draft=values();preview=null;try{persist();status.textContent='编辑尚未保存，只在本标签页保留输入。';}catch(_){status.textContent='本标签页无法保留草稿，刷新可能丢失输入；请先复制。';}render();});}
        form.onsubmit=e=>{e.preventDefault();previewButton.click();};diff.tabIndex=-1;
        function render(){
            form.hidden=!draft||phase==='saved';diff.hidden=phase!=='review';conflict.hidden=phase!=='conflict';
            for(const [k] of fields)inputs[k].readOnly=phase!=='edit'||busy;
            previewButton.hidden=phase!=='edit';previewButton.disabled=busy||!base||!draft||!dirty().length||cacheError;
            save.hidden=phase!=='review';save.disabled=busy;back.hidden=phase!=='review';back.disabled=busy;
            check.hidden=phase!=='unknown';check.disabled=busy;retry.hidden=phase!=='unknown'||!verifiedOriginal;retry.disabled=busy;
            loadButton.hidden=phase!=='error';loadButton.disabled=busy;acknowledge.hidden=phase!=='conflict';acknowledge.disabled=busy||!latest||!!pending;
            again.hidden=phase!=='saved';cancel.textContent=phase==='saved'?'完成':pending||busy?'关闭窗口，稍后核对':phase==='conflict'||phase==='error'?'关闭并保留输入':'取消编辑';
            if(phase==='review'){
                diff.replaceChildren(E('h3','本次拟保存的修改'));
                for(const [k,label] of fields)if(draft[k]!==base.brief[k]){const n=E('article');n.append(E('h4',label),E('p','原内容：'+shown(base.brief[k])),E('p','拟保存：'+(draft[k].trim()?draft[k]:'清空（尚未明确）')));diff.append(n);}
            }
            if(phase==='conflict'&&latest){
                conflict.replaceChildren(E('h3','最新已保存内容（请核对）'),E('p',`原版本 ${base.revision}，当前版本 ${latest.workspace.revision}。你的输入仍保留在上方；没有静默覆盖。`),list(latest.workspace.brief));
            }
        }
        function conflictWith(fresh){
            latest=fresh;pending=null;verifiedOriginal=false;phase='conflict';preview=null;
            status.textContent='项目已有更新。请对照最新背景核对，再选择保留本次修改；不会直接采用新版本提交。';persist();render();conflict.focus();
        }
        function saved(fresh,notice){
            data=fresh;base=snapshot(fresh.workspace);draft=clone(base.brief);pending=null;preview=null;verifiedOriginal=false;phase='saved';try{sessionStorage.removeItem(key);}catch(_){}
            for(const [k] of fields)inputs[k].value=draft[k];
            const items=fresh.workspace.items||[],questions=items.filter(i=>i.kind==='question'&&i.status==='active'&&i.needs_review).length,plans=items.filter(i=>i.kind==='plan'&&i.status==='active'&&i.needs_review).length;
            status.textContent=notice;result.replaceChildren(list(draft),E('p',`实际状态：${questions} 个有效研究问题、${plans} 份正式方案待复核。请在原记录中审阅，不自动认定旧方案仍适用。`));render();
            // UI refresh failures cannot turn an already confirmed save into an
            // unknown write and must never trigger another PUT.
            try{Promise.resolve(onSaved?.(fresh)).catch(()=>{if(live())status.textContent+=' 页面刷新未完成，可手动刷新查看。';});}catch(_){status.textContent+=' 页面刷新未完成，可手动刷新查看。';}
        }
        function inspect(fresh){
            data=fresh;title.textContent='编辑项目背景 · '+(fresh.project?.display_name||fresh.project?.topic||pid)+' · '+pid.slice(-6);
            if(pending){
                if(fresh.workspace.revision>pending.expected_revision&&equal(fresh.workspace.brief,pending.brief)){
                    saved(fresh,'已读取核对：当前已保存内容与本次修改一致。不会重复保存；这不单独证明是哪一次提交写入。');return;
                }
                if(fresh.workspace.revision>pending.expected_revision){conflictWith(fresh);return;}
                phase='unknown';verifiedOriginal=fresh.workspace.revision===pending.expected_revision&&equal(fresh.workspace.brief,base.brief);
                status.textContent='保存结果尚未确认，原提交仍可能完成。请保留输入并核对结果；不会自动重发。'+(verifiedOriginal?' 如需重交，只能明确提交相同内容和原版本，版本校验保证最多保存一次。':' 当前内容或版本与原请求不一致，不能盲目重交。');render();return;
            }
            if(!base){base=snapshot(fresh.workspace);draft=clone(base.brief);}
            else if(base.revision!==fresh.workspace.revision||!equal(base.brief,fresh.workspace.brief)){conflictWith(fresh);return;}
            for(const [k] of fields)inputs[k].value=draft[k];phase='edit';status.textContent=cached?'已恢复本标签页未保存的输入；保存前仍须预览并确认。':'请按实际情况编辑；空字段保持尚未明确。';render();
        }
        async function load(){
            if(busy||!live())return;busy=true;const n=++ticket;read?.abort();read=new AbortController();status.textContent='正在读取当前项目背景…';render();
            try{const fresh=await request(pid,'GET',undefined,read.signal);if(live()&&n===ticket)inspect(fresh);}
            catch(e){if(live()&&n===ticket&&e.name!=='AbortError'){phase=pending?'unknown':'error';verifiedOriginal=false;status.textContent='项目背景暂时无法核对：'+e.message+' 输入保留，未用空数据代替。';}}
            finally{if(live()&&n===ticket){busy=false;render();}}
        }
        async function submit(wire){
            if(busy||!live())return;
            pending={expected_revision:wire.expected_revision,brief:clone(wire.brief)};verifiedOriginal=false;
            try{persist();}catch(_){pending=null;status.textContent='无法保留恢复信息，本次未提交，请先复制输入。';return;}
            busy=true;phase='unknown';status.textContent='正在保存。关闭窗口不会撤销已经提交的保存；结果未确认时请先核对。';render();
            try{
                const fresh=await request(pid,'PUT',pending);
                if(!live())return;
                if(fresh.workspace.revision>pending.expected_revision&&equal(fresh.workspace.brief,pending.brief))saved(fresh,'项目背景已保存。后续请求可参考当前背景；旧聊天和已提交请求不被改写或重新生成。');
                else{phase='unknown';status.textContent='返回内容未能与本次保存对应，请先核对结果。';}
            }catch(e){
                if(!live())return;
                phase='unknown';status.textContent='保存结果尚未确认，请先核对。原输入、内容和版本已保留，不会自动重复保存。';
                // The authoritative read also distinguishes a concurrent write
                // from a lost successful response; HTTP status alone does not.
                if(e.status===409){busy=false;await load();}
            }finally{if(live()){busy=false;render();}}
        }
        dialog.setAttribute('aria-label','编辑项目背景');status.setAttribute('role','status');conflict.tabIndex=-1;
        dialog.append(title,E('p','保存的是本项目后续对话可参考的背景。请核对修改；更改会按原规则使相关正式材料待复核。不会调用模型、检索或自动采纳。'),status,form,diff,conflict,result);
        const controls=E('div','','project-background-controls');controls.append(previewButton,back,save,loadButton,check,retry,acknowledge,again,cancel);dialog.append(controls);
        if(draft)for(const [k] of fields)inputs[k].value=draft[k];
        dialog.addEventListener('cancel',e=>{e.preventDefault();cancel.click();});document.body.append(dialog);dialog.showModal();render();cancel.focus();
        if(cacheError){phase='error';status.textContent='本标签页的恢复信息无法读取，未提交任何保存；请先核对浏览器草稿。';render();}
        else load();
    }
    window.addEventListener('sra:context',()=>{if(currentEditor)currentEditor.close(true);});
    window.SRA_PROJECT_BACKGROUND={view,open};
})();

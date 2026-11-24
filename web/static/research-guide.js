/* 原系统右侧指导：只管理自己的 DOM，以事件跟随原导航，复用项目和灵感笔记。 */
(() => {
    "use strict";
    // Never silently route a selected conversation into legacy default chat.
    if (window.SRA_CONVERSATIONS) return;
    const $ = id => document.getElementById(id);
    const BASE = window.SRA_API_BASE || "";
    const PAGES = Object.fromEntries(Object.entries(window.SRA_NAVIGATION.pages).map(([id,p])=>[id,[p.title,p.area]]));
    const KINDS = {note:"笔记", question:"研究问题", plan:"研究方案", result:"实验结果", finding:"研究判断", outline:"写作骨架"};
    const s = {pid:null, page:"dashboard", data:null, loading:0, busy:false, papers:[], selected:null, tab:"chat", inputVersion:0};
    const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text) n.textContent = text; return n; };
    const area = () => (PAGES[s.page] || PAGES.dashboard)[1];
    const key = pid => `sra.guide.draft.${pid || "none"}`;
    const guideImage = "/static/assets/research-guide.png";
    function error(text="") { $("rg-error").textContent=text; $("rg-error").hidden=!text; }
    function messageAvatar() {
        const avatar=el("span","rg-avatar rg-avatar-message"); avatar.setAttribute("aria-hidden","true");
        avatar.append(el("span","rg-avatar-mark","研"));
        const img=el("img"); img.src=guideImage; img.alt=""; img.decoding="async"; avatar.append(img);
        return avatar;
    }
    async function request(pid, suffix="", body) {
        const r = await fetch(`${BASE}/api/projects/${encodeURIComponent(pid)}/workspace${suffix}`, body === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
        const d = await r.json();
        if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : `请求失败（${r.status}）`);
        return d;
    }
    function setOpen(open, remember=true) {
        $("research-layout").classList.toggle("guide-collapsed", !open);
        $("research-guide-toggle").setAttribute("aria-expanded", String(open));
        if (remember) localStorage.setItem("sra.guide.open", String(open));
    }
    function setTab(tab) {
        s.tab=tab; $("rg-chat").hidden=tab!=="chat"; $("rg-records").hidden=tab!=="records";
        $("rg-chat-tab").setAttribute("aria-selected",String(tab==="chat"));
        $("rg-records-tab").setAttribute("aria-selected",String(tab==="records"));
    }
    function richText(text) {
        const n=el("div","rg-message-text");
        // Shared renderer sanitizes Markdown before rendering local SVG equations.
        window.SRA_DOCUMENT.render(n,text);
        return n;
    }
    function renderContext() {
        const unsupported=s.data?.research_scope?.supported===false;
        const readOnlyWriting=area()==="writing";
        const selected=s.data?.workspace.items.find(item=>item.item_id===s.selected);
        $("rg-focus").hidden=!selected;
        $("rg-focus").textContent=selected ? `讨论参考：${selected.title}（研究记录，非已验证结论）` : "";
        $("rg-page").textContent=(PAGES[s.page] || PAGES.dashboard)[0];
        $("rg-mode").textContent=unsupported ? "历史领域 · 可查看记录" : s.busy ? "正在思考" : s.data ? s.data.guide_mode==="model" ? "模型已接入" : "规则指导" : s.pid ? "读取材料中" : "未选择项目";
        const starters=$("rg-starters"); starters.replaceChildren();
        const prompts=s.data?.starters[area()] || ["如何把我的研究兴趣收敛成具体问题？"];
        prompts.slice(0,2).forEach(text => {
            const b=el("button","",text); b.type="button"; b.onclick=()=>{ $("rg-input").value=text; s.inputVersion++; sessionStorage.setItem(key(s.pid),text); $("rg-input").focus(); }; starters.append(b);
        });
        $("rg-send").disabled=!s.data || s.busy || unsupported || readOnlyWriting; $("rg-send").textContent=s.busy ? "思考中…" : readOnlyWriting ? "历史写作（仅查看）" : "发送";
        $("rg-send").title=readOnlyWriting ? "当前产品止于研究方案，不生成新提纲或稿件；可回到方案页面讨论。" : unsupported ? "该历史领域暂不提供新指导；已有对话、笔记与研究材料保留。" : "";
    }
    function renderMaterials() {
        const box=$("rg-materials"); box.replaceChildren();
        $("rg-material-count").textContent=s.data ? `· ${s.data.papers.length} 篇论文` : "";
        box.append(el("p","", s.data?.research_scope?.supported===false ? s.data.research_scope.notice + " 当前项目仅保留历史材料浏览与记录。" : "默认参考近期论文摘要、已有研究材料与最近灵感笔记；摘要不等于全文。可指定至多三篇论文。"));
        (s.data?.papers || []).forEach(p => {
            const label=el("label"), input=el("input"); input.type="checkbox"; input.checked=s.papers.includes(p.paper_id);
            input.onchange=()=>{ if (input.checked && s.papers.length>=3) {input.checked=false; error("每次至多选择三篇论文"); return;} s.papers=input.checked ? [...s.papers,p.paper_id] : s.papers.filter(id=>id!==p.paper_id); };
            label.append(input,document.createTextNode(p.title)); box.append(label);
        });
    }
    function renderRecords() {
        const box=$("rg-records"); box.replaceChildren(el("p","","文献与灵感笔记保留原用法；研究问题、方案与实验、成果写作分别展示对应记录。论断与证据已并入研究问题。这里可以回看讨论采纳的内容。"));
        if (!s.data) return;
        const records=s.data.workspace.items;
        if (!records.length) box.append(el("p","","暂无讨论采纳的记录。你也可以把指导答复保存到原来的灵感笔记。"));
        records.slice().reverse().forEach(item=>{
            const d=el("details","rg-record"), summary=el("summary","",`${KINDS[item.kind]} · ${item.title}`);
            d.append(summary,el("p","",item.content),el("small","",item.status==="superseded" ? "历史版本" : item.needs_review ? "依据变化，待复核" : "已保存；不代表科学结论已验证"));
            if (item.status==="active") {
                const b=el("button","rg-small-btn",s.selected===item.item_id ? "已选作对话参考" : "围绕这条讨论"); b.onclick=()=>{s.selected=item.item_id; setTab("chat"); error(`已选：${item.title}`);}; d.append(b);
            }
            box.append(d);
        });
        const a=el("a","rg-small-btn","下载研究记录"); a.href=`${BASE}/api/projects/${encodeURIComponent(s.pid)}/workspace/export?format=md`; box.append(a);
    }
    async function saveNote(text, button) {
        const pid=s.pid; button.disabled=true;
        try {
            const r=await fetch(`${BASE}/api/projects/${encodeURIComponent(pid)}/notes`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({text})});
            if (!r.ok) throw new Error("灵感笔记保存失败，请重试");
            button.textContent="已存入灵感笔记";
            window.dispatchEvent(new CustomEvent("sra:note-saved",{detail:{projectId:pid}}));
        } catch(e) {error(e.message); button.disabled=false;}
    }
    function renderMessages(follow=false) {
        const box=$("rg-messages"), top=box.scrollTop; box.replaceChildren();
        const messages=s.data?.workspace.messages || [];
        if (!messages.length) {
            const empty=el("div","rg-empty"); empty.append(el("strong","","在原来的研究流程中，随时讨论"),document.createTextNode(s.pid ? "可以问方向是否可行、论文如何理解，或下一步如何安排。对话不会替你启动任务、确认人工节点或修改研究结论。" : "先在原来的总览中选择项目，或新建项目。左侧导航和灵感笔记的用法保持不变。")); box.append(empty);
        }
        messages.forEach(m=>{
            const card=el("article",`rg-message ${m.role}`);
            const heading=el("div","rg-message-heading");
            if(m.role==="assistant") heading.append(messageAvatar());
            heading.append(el("span","rg-message-label",`${m.role==="user" ? "你" : "科研指导"} · ${m.area==="literature" ? "文献阅读" : m.area==="question" ? "问题讨论" : m.area==="study" ? "方案与实验" : m.area==="writing" ? "成果写作" : "项目总览"}`));
            card.append(heading,richText(m.content));
            m.questions.forEach(q=>card.append(el("p","rg-message-question",q)));
            m.cited_paper_ids.forEach(id=>card.append(el("p","rg-message-source",`参考：${s.data.papers.find(p=>p.paper_id===id)?.title || id}`)));
            m.suggestion_ids.forEach(id=>{
                const item=s.data.workspace.suggestions.find(x=>x.suggestion_id===id); if(!item) return;
                const d=el("details","rg-suggestion"); d.append(el("summary","",`建议草案：${item.title}`),el("p","",item.content));
                const stale=item.based_on_revision!==s.data.workspace.revision;
                const closed=["result","finding","outline"].includes(item.kind);
                const b=el("button","rg-small-btn",closed ? "历史建议（仅查看）" : item.applied_item_id ? "已采纳" : stale ? "材料已更新，请重新讨论" : "采纳到研究记录"); b.disabled=closed || !!item.applied_item_id || stale || s.busy;
                b.onclick=async()=>{const pid=s.pid; b.disabled=true; try{const data=await request(pid,`/suggestions/${id}/apply`,{expected_revision:s.data.workspace.revision}); if(pid===s.pid){s.data=data; renderMessages(); renderRecords(); window.dispatchEvent(new CustomEvent("sra:records-changed",{detail:{projectId:pid}}));}}catch(e){error(e.message);b.disabled=false;}}; d.append(b); card.append(d);
            });
            if(m.role==="assistant") {const b=el("button","rg-small-btn","存入灵感笔记"); b.onclick=()=>saveNote(m.content,b); card.append(b);}
            box.append(card);
        });
        if(s.busy) box.append(el("p","rg-message-label","正在结合当前项目材料思考，你可以继续浏览其他页面。"));
        box.scrollTop=follow ? box.scrollHeight : top;
    }
    async function load() {
        const pid=s.pid, ticket=++s.loading; error();
        if(!pid) {s.data=null; renderContext();renderMessages();renderMaterials();renderRecords();return;}
        try {const data=await request(pid); if(pid!==s.pid || ticket!==s.loading) return; s.data=data; renderContext();renderMessages();renderMaterials();renderRecords();window.dispatchEvent(new CustomEvent("sra:workspace-loaded",{detail:{projectId:pid,counts:data.counts}}));}
        catch(e){if(pid===s.pid && ticket===s.loading) error(e.message);}
    }
    window.addEventListener("sra:context",event=>{
        const {projectId,page}=event.detail; const changed=projectId!==s.pid; s.page=page;
        if(changed) {s.pid=projectId; s.data=null; s.papers=[]; s.selected=null; $("rg-input").value=sessionStorage.getItem(key(s.pid)) || ""; s.inputVersion++; renderMessages();renderMaterials();renderRecords();load();}
        renderContext();
    });
    window.addEventListener("sra:records-changed",event=>{if(event.detail.projectId===s.pid) load();});
    window.addEventListener("sra:research-focus",event=>{
        if(event.detail.projectId!==s.pid) return;
        s.selected=event.detail.itemId;renderContext();
    });
    window.addEventListener("sra:discuss",event=>{
        if(event.detail.projectId!==s.pid) return;
        setOpen(true); setTab("chat");
        s.selected=event.detail.itemId || null;
        // 不覆盖尚未发送的提问。
        if(!$("rg-input").value.trim()) {$("rg-input").value=event.detail.message || ""; s.inputVersion++; sessionStorage.setItem(key(s.pid),$("rg-input").value);}
        $("rg-input").focus();
    });
    $("rg-form").addEventListener("submit",async event=>{
        event.preventDefault(); const text=$("rg-input").value.trim(); if(!text || !s.pid || !s.data || s.busy || area()==="writing" || s.data.research_scope?.supported===false) return;
        const pid=s.pid, chosenArea=area(), papers=s.papers.slice(), selected=s.selected, inputVersion=s.inputVersion;
        s.busy=true; error();renderContext();renderMessages(true);
        try {
            // 每次发送前取最新版本；不以定时轮询重绘对话或业务页面。
            const latest=await request(pid);
            const data=await request(pid,"/guide",{expected_revision:latest.workspace.revision,area:chosenArea,message:text,selected_item_id:selected,selected_paper_ids:papers,reading_purpose:"orientation"});
            if(pid===s.pid){s.data=data; if(inputVersion===s.inputVersion && $("rg-input").value.trim()===text){$("rg-input").value="";sessionStorage.removeItem(key(pid));}}
        }catch(e){if(pid===s.pid)error(e.message);}
        finally{s.busy=false;renderContext();renderMessages(pid===s.pid);renderRecords();}
    });
    $("rg-input").addEventListener("input",event=>{s.inputVersion++;sessionStorage.setItem(key(s.pid),event.target.value);});
    $("rg-input").addEventListener("keydown",event=>{if((event.ctrlKey||event.metaKey)&&event.key==="Enter"){event.preventDefault();$("rg-form").requestSubmit();}});
    $("research-guide-toggle").onclick=()=>setOpen($("research-layout").classList.contains("guide-collapsed"));
    $("rg-close").onclick=()=>setOpen(false);
    $("rg-chat-tab").onclick=()=>setTab("chat"); $("rg-records-tab").onclick=()=>setTab("records");
    $("rg-latest").onclick=()=>{$("rg-messages").scrollTop=$("rg-messages").scrollHeight;};
    $("rg-refresh").onclick=load;
    const preference=localStorage.getItem("sra.guide.open");
    setOpen(preference===null ? window.innerWidth>1280 : preference==="true",false);
    renderContext();renderMessages();renderRecords();
})();

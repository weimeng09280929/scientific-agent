/* Server-paged project management. Selection is an explicit frozen ID set. */
(() => {
    "use strict";
    const E=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n;};
    const button=(text,fn)=>{const b=E("button","btn btn-secondary btn-sm",text);b.type="button";b.onclick=fn;return b;};
    async function request(path,body){const r=await fetch((window.SRA_API_BASE || "")+path,body ? {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)} : {});const data=await r.json();if(!r.ok)throw new Error(data.detail || `请求失败 ${r.status}`);return data;}
    function mount(host,app){
        const root=E("section","card project-library");root.setAttribute("aria-label","已有项目");host.append(root);
        const title=E("h3","card-title","管理全部项目"),search=E("input","input project-search");search.type="search";search.placeholder="搜索名称、原主题或项目编号";search.setAttribute("aria-label","搜索已有项目");
        const status=E("p","muted small");status.setAttribute("role","status");const list=E("div","project-library-list");
        const selected=new Map();let rows=[],offset=0,nextOffset=null,total=0,matched=0,ticket=0,busy=false,supported=false;
        const selection=E("details","project-selection-summary"),selectionTitle=E("summary"),selectionNames=E("div");selection.append(selectionTitle,selectionNames);
        const selectPage=E("input");selectPage.type="checkbox";selectPage.setAttribute("aria-label","全选当前页");const pageLabel=E("label");pageLabel.append(selectPage," 全选当前页");
        const all=button("选择全部搜索结果",selectSearch),clear=button("取消全部选择",()=>{selected.clear();paint();});
        const archive=button("归档所选",archiveSelected);archive.id="delete-selected-projects";archive.classList.add("btn-danger");
        const previous=button("上一页",()=>load(Math.max(0,offset-10))),next=button("下一页",()=>load(nextOffset)),pager=E("span","muted small");
        const feedback=E("p","ch-warning");feedback.setAttribute("role","status");
        const recovery=E("p","muted small","归档保留文件于本机 .project-trash/<批次编号>/。目前没有网页撤销：停止服务后，核对 manifest.json，将所需项目目录原样移回 projects/（同名目录存在时不要覆盖），再重启服务。请由熟悉文件操作的人协助恢复。");
        const tools=E("div","project-selection-bar");tools.append(pageLabel,all,clear,archive);
        const heading=E("div","project-library-heading");heading.append(title,search);const footer=E("div","project-library-footer");footer.append(previous,pager,next);
        root.append(heading,status,tools,selection,feedback,list,footer,recovery);
        const canSelect=p=>supported && p.data_status==="available" && !["running","pending_human"].includes(p.status);
        function paint(){
            if(!root.isConnected)return;
            status.textContent=`共 ${total} 个项目 · 匹配 ${matched} 个 · 已选 ${selected.size} 个（含其他页，筛选不扩大选择）`;
            selectionTitle.textContent=`查看所选 ${selected.size} 个项目${selected.has(app.getRoute().projectId) ? " · 包含当前项目" : ""}`;selection.hidden=!selected.size;selectionNames.replaceChildren();
            for(const p of selected.values()){const row=E("div","project-selected-row",`${p.display_name || p.topic}（${p.project_id}）${p.project_id===app.getRoute().projectId ? " [当前项目]" : ""}`);const remove=button("取消选择",()=>{selected.delete(p.project_id);paint();});remove.disabled=busy;row.append(remove);selectionNames.append(row);}
            const candidates=rows.filter(canSelect);selectPage.checked=!!candidates.length && candidates.every(p=>selected.has(p.project_id));selectPage.indeterminate=!selectPage.checked && candidates.some(p=>selected.has(p.project_id));selectPage.disabled=busy || !candidates.length;
            archive.disabled=busy || !supported || !selected.size;archive.textContent=`归档所选${selected.size ? `（${selected.size}）` : ""}`;clear.disabled=busy || !selected.size;all.disabled=busy || !supported || !matched;search.disabled=busy;previous.disabled=busy || offset===0;next.disabled=busy || nextOffset===null;
            pager.textContent=`第 ${Math.floor(offset/10)+1} / ${Math.max(1,Math.ceil(matched/10))} 页`;
            list.replaceChildren();if(!rows.length)list.append(E("p","muted project-list-empty",matched ? "此页已无项目，请返回上一页。" : "没有匹配项目。浏览不会创建空项目。"));
            for(const p of rows){const row=E("div",`project-library-row${p.project_id===app.getRoute().projectId ? " current" : ""}`);row.dataset.projectId=p.project_id;const check=E("input");check.type="checkbox";check.setAttribute("aria-label",`选择项目 ${p.project_id}`);check.checked=selected.has(p.project_id);check.disabled=busy || !canSelect(p);check.onchange=()=>{if(check.checked)selected.set(p.project_id,p);else selected.delete(p.project_id);paint();};
                const info=E("div","project-row-info"),open=button(p.display_name || p.topic || "未命名研究",()=>app.navigate({page:"project-overview",projectId:p.project_id,conversationId:app.getRoute().projectId===p.project_id?app.getRoute().conversationId:null}));open.className="project-title-link";open.disabled=busy;
                const time=p.activity_at ? new Date(p.activity_at).toLocaleString("zh-CN") : "无可靠时间记录";info.append(open,E("div","muted small project-row-meta",`${p.project_id} · 最近活动 ${time}`));
                const action=E("div","project-row-actions");action.append(E("span","muted small",p.data_status!=="available" ? "数据不可用，未修复" : p.research_progress?.label || "研究进展待核对"));
                if(['running','pending_human'].includes(p.status))action.append(E('small','muted',p.status==='running'?'旧后台运行中，不能归档':'待人工确认，不能归档'));
                if(p.project_id===app.getRoute().projectId)action.append(E("span","badge badge-success","当前"));action.append(button("任务活动",()=>app.navigate({page:"progress",projectId:p.project_id,conversationId:null})));row.append(check,info,action);list.append(row);
            }
        }
        async function load(start=0){const own=++ticket;offset=start || 0;feedback.textContent="";status.textContent="正在读取项目…";
            try{const cap=await request("/api/conversation-capabilities");if(!cap.catalog_navigation)throw new Error("当前后端尚未加载项目分页与活动排序，请更新／重启后端；不会自动修改数据。");supported=true;const data=await request(`/api/projects?limit=10&offset=${offset}&q=${encodeURIComponent(search.value.trim())}`);if(own!==ticket || !root.isConnected)return;rows=data.projects;nextOffset=data.next_offset;total=data.total;matched=data.matched_total;paint();}
            catch(e){if(own===ticket && root.isConnected){feedback.textContent=e.message;list.replaceChildren(button("重试读取",()=>load(offset)));}}
        }
        async function selectSearch(){if(busy)return;const q=search.value.trim();if(matched>200){feedback.textContent="每批上限 200 个，请缩小搜索范围后选择；未增加任何选择。";return;}busy=true;paint();
            try{let cursor=0;const snapshot=[];do{const data=await request(`/api/projects?limit=100&offset=${cursor}&q=${encodeURIComponent(q)}`);snapshot.push(...data.projects);cursor=data.next_offset;if(snapshot.length>200)throw new Error("搜索结果已超过 200 个，未扩大选择，请重新核对。");}while(cursor!==null);
                if(!root.isConnected)return;const eligible=snapshot.filter(canSelect);if(selected.size+eligible.filter(p=>!selected.has(p.project_id)).length>200)throw new Error("加上跨页选择将超过 200 个，请先取消部分选择。");for(const p of eligible)selected.set(p.project_id,p);feedback.textContent=`已明确选择当前搜索的 ${eligible.length} 个可归档项目；运行中、待确认或数据不可用的项目未选中。之后新匹配的项目不会自动加入。`;
            }catch(e){feedback.textContent=e.message;}finally{busy=false;paint();}}
        async function archiveSelected(){if(busy || !selected.size)return;const snapshot=[...selected.values()],ids=snapshot.map(p=>p.project_id);if(ids.length>200){feedback.textContent="每批最多归档 200 个项目。";return;}
            if(!window.confirm(`确认将明确选中的 ${ids.length} 个项目移入可恢复归档？\n\n${snapshot.map(p=>`${p.display_name || p.topic}（${p.project_id}）`).join('\n')}\n\n${ids.includes(app.getRoute().projectId) ? '包含当前项目；归档后将退出该项目。\n' : ''}文件移入 .project-trash/<批次编号>/；目前仅支持手工恢复，没有网页撤销。运行中或待人工确认项目不能归档。`))return;
            busy=true;paint();try{const result=await request("/api/projects/batch-delete",{project_ids:ids});for(const id of result.deleted)selected.delete(id);window.dispatchEvent(new CustomEvent("sra:projects-changed"));if(result.deleted.includes(app.getRoute().projectId)){app.navigate({page:"dashboard",projectId:null,conversationId:null,replace:true});return;}await load(offset);feedback.textContent=`已归档 ${result.deleted.length} 个项目，批次 ${result.archive_id}；文件保留，可手工恢复。`;}
            catch(e){feedback.textContent=`归档未完成：${e.message}。原选择保留，请核对后再操作。`;}
            finally{busy=false;paint();}}
        selectPage.onchange=()=>{for(const p of rows.filter(canSelect))if(selectPage.checked)selected.set(p.project_id,p);else selected.delete(p.project_id);paint();};
        let debounce;search.oninput=()=>{clearTimeout(debounce);debounce=setTimeout(()=>load(0),200);};load();
    }
    window.SRA_PROJECT_LIBRARY={mount};
})();

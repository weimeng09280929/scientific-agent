/* Research workspace: explicit user actions, persistent records, contextual guidance. */
(() => {
    "use strict";
    const AREAS = {
        overview: ["项目总览", "明确目标和约束，看看现在最值得推进哪一步。"],
        literature: ["文献阅读", "围绕当前问题读论文，记录依据、局限和自己的想法。"],
        question: ["研究问题", "把方向收敛成一个能检验的问题，并说明与已有工作的关系。"],
        study: ["方案与实验", "从最小试跑开始；实际结果、运行状态与研究判断分别记录。"],
        writing: ["成果写作", "逐步组织写作骨架，有证据的结论和待补内容都明确保留。"],
    };
    const KINDS = { note: "阅读与灵感笔记", question: "研究问题", plan: "研究方案", result: "实验结果", finding: "研究判断", outline: "写作骨架" };
    const AREA_KINDS = { overview: ["note"], literature: ["note"], question: ["question", "note"], study: ["plan", "result", "finding"], writing: ["outline", "note"] };
    const RUN_STATUS = { completed: "已运行完成", failed: "运行失败", inconclusive: "尚无法定论" };
    const ASSESSMENT = { supports: "支持设想", refutes: "反驳设想", inconclusive: "无法定论" };
    const state = {
        projectId: localStorage.getItem("research-workspace.project") || "",
        area: localStorage.getItem("research-workspace.area") || "overview", data: null,
        selectedItem: null, selectedPapers: [], editing: null, busy: false,
    };
    if (!AREAS[state.area]) state.area = "overview";
    const $ = id => document.getElementById(id);
    function el(tag, attrs = {}, children = []) {
        const node = document.createElement(tag);
        Object.entries(attrs).forEach(([key, value]) => {
            if (key === "class") node.className = value;
            else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
            else if (key in node) node[key] = value;
            else node.setAttribute(key, value);
        });
        (Array.isArray(children) ? children : [children]).forEach(child => {
            if (child != null) node.append(child instanceof Node ? child : String(child));
        });
        return node;
    }
    function notify(message, error = false) {
        const box = $("notification");
        box.textContent = message; box.className = error ? "error" : ""; box.hidden = false;
        clearTimeout(notify.timer); notify.timer = setTimeout(() => { box.hidden = true; }, error ? 11000 : 4200);
    }
    async function api(path, body, method = "POST") {
        const response = await fetch(path, body === undefined ? {} : {
            method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        });
        let data;
        try { data = await response.json(); } catch { throw new Error(`服务器返回异常（${response.status}）`); }
        if (!response.ok) {
            const message = typeof data.detail === "string" ? data.detail : (data.detail || []).map(d => d.msg).join("；");
            throw new Error(message || `请求失败（${response.status}）`);
        }
        return data;
    }
    const endpoint = suffix => `/api/projects/${encodeURIComponent(state.projectId)}/workspace${suffix}`;
    function workspace() { return state.data.workspace; }
    function activeItems() { return workspace().items.filter(i => i.status === "active"); }
    function chatKey() { return `research-workspace.chat.${state.projectId}`; }
    function formKey() { return `research-workspace.form.${state.projectId}.${state.area}.${state.editing || "new"}`; }
    function getDraft(key) { try { return JSON.parse(sessionStorage.getItem(key)) || {}; } catch { return {}; } }
    function saveDraft(key, data) { sessionStorage.setItem(key, JSON.stringify(data)); }

    function renderNav() {
        $("workspace-nav").replaceChildren(...Object.entries(AREAS).map(([key, [label]], index) => el("button", {
            type: "button", class: `nav-button ${state.area === key ? "active" : ""}`,
            onclick: () => { state.area = key; state.editing = null; localStorage.setItem("research-workspace.area", key); renderNav(); renderContent(); renderGuide(); },
        }, [el("span", { class: "nav-number" }, `0${index + 1}`), label])));
    }
    function renderAll() {
        if (state.data && !activeItems().some(i => i.item_id === state.selectedItem)) state.selectedItem = null;
        renderNav(); renderContent(); renderGuide();
        $("exports").hidden = !state.data;
        if (state.data) {
            $("exports").replaceChildren(
                el("a", { href: endpoint("/export?format=md") }, "下载研究记录 ↓"),
                el("a", { href: endpoint("/export?format=json") }, "导出完整记录 JSON ↓"),
            );
            $("capabilities").replaceChildren(...Object.entries(state.data.capabilities).map(([, value]) => el("p", {}, value)));
        }
    }
    async function loadProject() {
        const projectId = state.projectId;
        state.selectedItem = null; state.selectedPapers = []; state.editing = null;
        state.data = null; renderAll();
        $("guide-input").value = sessionStorage.getItem(chatKey()) || "";
        if (!projectId) return;
        try {
            const data = await api(endpoint(""));
            if (state.projectId !== projectId) return;
            state.data = data; renderAll();
        } catch (error) { notify(error.message, true); }
    }
    async function loadProjects(selectedId) {
        const data = await api("/api/projects");
        const select = $("project-select");
        select.replaceChildren(el("option", { value: "" }, "选择或新建项目"), ...data.projects.map(p => el("option", { value: p.project_id }, p.topic || p.project_id)));
        state.projectId = data.projects.some(p => p.project_id === (selectedId || state.projectId)) ? (selectedId || state.projectId) : "";
        select.value = state.projectId;
        await loadProject();
    }
    function renderContent() {
        const main = $("workspace-content");
        main.replaceChildren(el("span", { class: "eyebrow" }, "RESEARCH WORKSPACE"), el("h1", {}, AREAS[state.area][0]), el("p", { class: "page-intro" }, AREAS[state.area][1]));
        if (!state.data) {
            main.append(el("section", { class: "card accent" }, [el("h2", {}, "从你自己的问题开始"),
                el("p", {}, "选择一个已有项目，或新建项目记录研究兴趣。工作台会沿用该项目的论文库，并保存你的讨论与研究记录。"),
                el("button", { type: "button", class: "primary", onclick: () => $("create-dialog").showModal() }, "新建研究项目")]));
            return;
        }
        if (state.area === "overview") renderOverview(main);
        else if (state.area === "literature") renderPapers(main);
        else {
            if (state.area === "question") main.append(el("div", { class: "card accent" }, "先区分研究问题和预期结果。与已有工作有什么区别、目前还不能确定什么，都写进问题卡；不以模型分数代替文献核对。"));
            if (state.area === "study") main.append(el("div", { class: "card accent" }, "确认问题 → 形成方案 → 审阅、修改与导出。本轮止于研究方案，历史结果与判断仅供查看。"));
            if (state.area === "writing") main.append(el("div", { class: "card accent" }, "当前不进入论文写作；已有稿件、骨架和版本保留，可查看与导出，不生成或采纳新稿。"));
            renderRecords(main, AREA_KINDS[state.area]); main.append(recordEditor());
        }
    }
    function renderOverview(main) {
        main.append(el("div", { class: "card accent" }, [el("span", { class: "eyebrow" }, "建议的下一步"), el("h3", {}, state.data.next_step),
            el("p", { class: "small muted" }, `当前项目：${state.data.project.topic} · 记录版本 ${workspace().revision}`)]));
        main.append(el("div", { class: "counts" }, Object.entries(state.data.counts).map(([kind, count]) => el("div", { class: "count" }, [el("strong", {}, count), KINDS[kind]]))));
        if (state.data.needs_review_count) main.append(el("p", { class: "tag warning" }, `${state.data.needs_review_count} 条记录的依据发生变化，请修订后重新核对。`));
        const brief = workspace().brief, draftKey = `research-workspace.brief.${state.projectId}`, saved = getDraft(draftKey);
        const form = el("form", { class: "card" }, [el("h2", {}, "你的目标和条件"), el("p", { class: "small muted" }, "不确定的内容可以留空。这些条件会用于右侧指导；修改后，已有方案会提醒复核。")]);
        const inputs = {};
        [["goal", "希望得到什么成果", "例如：完成学期项目，形成一份可执行的研究方案"], ["background", "已经掌握的基础", "例如：Python、PyTorch，读过哪些论文"], ["resources", "可以使用的资源", "设备、数据、代码、协作条件"], ["time_budget", "可投入的时间", "例如：每周 8 小时，持续两个月"]].forEach(([key, label, placeholder]) => {
            inputs[key] = el("textarea", { rows: 2, value: saved[key] ?? brief[key], placeholder, maxLength: key === "time_budget" ? 1000 : 2000 });
            form.append(el("label", {}, [label, inputs[key]]));
        });
        form.addEventListener("input", () => saveDraft(draftKey, Object.fromEntries(Object.entries(inputs).map(([k, v]) => [k, v.value]))));
        const submit = el("button", { type: "submit", class: "primary" }, "保存项目条件");
        form.append(submit);
        form.addEventListener("submit", async event => {
            event.preventDefault(); submit.disabled = true;
            const pid = state.projectId;
            try {
                const data = await api(endpoint("/brief"), { expected_revision: workspace().revision, brief: Object.fromEntries(Object.entries(inputs).map(([k, v]) => [k, v.value.trim()])) }, "PUT");
                sessionStorage.removeItem(draftKey);
                if (pid === state.projectId) { state.data = data; renderAll(); }
                notify("项目条件已保存");
            } catch (error) { notify(error.message, true); } finally { submit.disabled = false; }
        });
        main.append(form);
        main.append(el("div", { class: "button-row" }, [el("button", { type: "button", onclick: () => refresh() }, "刷新项目记录"), el("span", { class: "small muted" }, "页面不会定时重绘或自动跳回顶部。") ]));
        renderRecords(main, ["note"]); main.append(recordEditor());
    }
    async function refresh() {
        const pid = state.projectId;
        try { const data = await api(endpoint("")); if (pid === state.projectId) { state.data = data; renderAll(); } }
        catch (error) { notify(error.message, true); }
    }
    function renderPapers(main) {
        const box = el("section", { class: "card" }, [el("h2", {}, `项目文献 · ${state.data.papers.length} 篇`),
            el("p", { class: "small muted" }, "这里读取现有文献库，不重复调用检索。选择至多 3 篇放入右侧上下文；有摘要不代表已完成全文精读。")]);
        box.append(el("div", { class: "button-row" }, [el("a", { class: "button-link", href: "/", target: "_blank", rel: "noopener" }, "打开现有检索与导入 ↗"), el("button", { type: "button", onclick: () => refresh() }, "刷新文献库")]));
        const upload = el("form"), file = el("input", { type: "file", accept: ".pdf,.txt,.md", required: true }), uploadButton = el("button", { type: "submit" }, "导入本地论文");
        upload.append(el("label", {}, ["也可以直接上传 PDF、TXT 或 Markdown", file]), uploadButton);
        const hint=el("p",{class:"small muted"});upload.append(hint);window.SRA_RESOURCES?.hint(hint);
        upload.addEventListener("submit", async event => {
            event.preventDefault(); const pid = state.projectId; const body = new FormData(); body.append("file", file.files[0]); uploadButton.disabled = true; uploadButton.textContent = "正在解析导入…";
            try {
                await window.SRA_RESOURCES?.checkFile(file.files[0]);
                const response = await fetch(`/api/projects/${encodeURIComponent(pid)}/upload-paper`, { method: "POST", body });
                const result = await response.json(); if (!response.ok) throw new Error(result.detail || "导入失败");
                if (pid === state.projectId) await refresh(); notify(`已导入；可用文本片段 ${result.chunks || 0} 条，请核对解析范围`);
            } catch (error) { notify(error.message, true); } finally { uploadButton.disabled = false; uploadButton.textContent = "导入本地论文"; }
        });
        box.append(upload);
        if (!state.data.papers.length) box.append(el("p", { class: "muted" }, "还没有文献。请导入已有论文，或在现有功能页面进行真实检索。"));
        state.data.papers.forEach(paper => {
            const details = el("details", { class: "paper" }, [el("summary", {}, paper.title), el("p", {}, paper.abstract || "暂无摘要，请补充原文后再分析。"), el("p", {}, paper.scope)]);
            const select = el("button", { type: "button", onclick: () => {
                if (state.selectedPapers.includes(paper.paper_id)) state.selectedPapers = state.selectedPapers.filter(id => id !== paper.paper_id);
                else if (state.selectedPapers.length < 3) state.selectedPapers.push(paper.paper_id);
                else { notify("每次最多选择 3 篇论文，以便围绕具体材料讨论", true); return; }
                select.textContent = state.selectedPapers.includes(paper.paper_id) ? "移出对话上下文" : "围绕这篇讨论"; renderGuide();
            } }, state.selectedPapers.includes(paper.paper_id) ? "移出对话上下文" : "围绕这篇讨论");
            const actions = el("div", { class: "paper-actions" }, [select, el("a", { href: `/api/projects/${encodeURIComponent(state.projectId)}/papers/${encodeURIComponent(paper.paper_id)}/pdf`, target: "_blank", rel: "noopener" }, "获取 PDF")]);
            if (paper.url && /^https?:\/\//i.test(paper.url)) actions.append(el("a", { href: paper.url, target: "_blank", rel: "noopener noreferrer" }, "原文链接 ↗"));
            details.append(actions); box.append(details);
        });
        main.append(box); renderRecords(main, ["note"]); main.append(recordEditor());
    }
    function renderRecords(main, kinds) {
        const records = activeItems().filter(i => kinds.includes(i.kind)).slice().reverse();
        records.forEach(item => {
            const card = el("article", { class: "card" });
            const body=el("div",{class:"record-content"}); window.SRA_DOCUMENT.render(body,item.content);
            card.append(el("div", { class: "card-heading" }, [el("span", { class: "tag" }, KINDS[item.kind]), item.needs_review ? el("span", { class: "tag warning" }, "依据变化 · 需要复核") : null]), el("h3", {}, item.title), body);
            if (item.result_status) card.append(el("p", { class: "small" }, `用户提交的状态：${RUN_STATUS[item.result_status]}`), el("pre", { class: "metrics" }, JSON.stringify(item.metrics, null, 2)));
            if (item.assessment) card.append(el("p", { class: "small" }, `用户判断：${ASSESSMENT[item.assessment]}；未经系统独立验证。`));
            const sourceNames = item.paper_ids.map(id => state.data.papers.find(p => p.paper_id === id)?.title || id);
            if (sourceNames.length) card.append(el("p", { class: "record-meta" }, `关联论文：${sourceNames.join("；")}`));
            if (item.related_ids.length) card.append(el("p", { class: "record-meta" }, `关联记录：${item.related_ids.map(id => workspace().items.find(i => i.item_id === id)?.title || id).join("；")}`));
            card.append(el("div", { class: "button-row record-actions" }, [el("button", { type: "button", onclick: () => { state.selectedItem = item.item_id; renderGuide(); $("guide-input").focus(); } }, "围绕这条讨论"),
                ["note","question","plan"].includes(item.kind) ? el("button", { type: "button", onclick: () => { state.editing = item.item_id; renderContent(); } }, "修订并保留旧版") : null, el("small", {}, new Date(item.created_at).toLocaleString())]));
            main.append(card);
        });
        const history = workspace().items.filter(i => i.status === "superseded" && kinds.includes(i.kind));
        if (history.length) main.append(el("details", { class: "card small muted" }, [el("summary", {}, `查看历史版本 · ${history.length} 条`), ...history.map(i => el("div", { class: "record-content" }, `${i.title}\n${i.content}`))]));
    }
    function recordEditor() {
        const editing = activeItems().find(i => i.item_id === state.editing), key = formKey(), saved = getDraft(key);
        const initial = { ...editing, ...saved }, choices = AREA_KINDS[state.area].filter(k => ["note","question","plan"].includes(k));
        if (state.area === "writing" || !choices.length || editing && !choices.includes(editing.kind)) return el("p", {class:"small muted"}, "历史材料仅供查看与导出。");
        const form = el("form", { class: "card editor" }, [el("h2", {}, editing ? "修订研究记录" : "记录当前成果")]);
        const kind = el("select", {}, choices.map(k => el("option", { value: k }, KINDS[k])));
        kind.value = choices.includes(initial.kind) ? initial.kind : choices[0]; kind.disabled = !!editing;
        const title = el("input", { required: true, maxLength: 200, value: initial.title || "", placeholder: "用一句话概括这条记录" });
        const content = el("textarea", { required: true, rows: 6, maxLength: 16000, value: initial.content || "", placeholder: "研究对象、已有依据、待检验设想、具体步骤或仍不确定的地方…" });
        const papers = el("select", { multiple: true, size: Math.min(4, Math.max(2, state.data.papers.length)) }, state.data.papers.map(p => el("option", { value: p.paper_id, selected: (initial.paper_ids || []).includes(p.paper_id) }, p.title)));
        const refs = activeItems().filter(i => !i.needs_review && i.item_id !== state.editing);
        const related = el("select", { multiple: true, size: Math.min(5, Math.max(2, refs.length)) }, refs.map(i => el("option", { value: i.item_id, selected: (initial.related_ids || []).includes(i.item_id) }, `${KINDS[i.kind]} · ${i.title}`)));
        const status = el("select", {}, Object.entries(RUN_STATUS).map(([value, label]) => el("option", { value }, label))); status.value = initial.result_status || "inconclusive";
        const metrics = el("textarea", { rows: 3, placeholder: '实际指标 JSON，例如 {"accuracy": 0.82}，请填入你的运行结果', value: initial.metricsText ?? (initial.metrics && Object.keys(initial.metrics).length ? JSON.stringify(initial.metrics, null, 2) : "") });
        const assessment = el("select", {}, Object.entries(ASSESSMENT).map(([value, label]) => el("option", { value }, label))); assessment.value = initial.assessment || "inconclusive";
        const resultFields = el("div", {}, [el("label", {}, ["实际运行状态", status]), el("label", {}, ["实际指标（JSON 对象）", metrics])]);
        const findingFields = el("label", {}, ["你对这些结果的判断", assessment]);
        function toggle() { resultFields.hidden = kind.value !== "result"; findingFields.hidden = kind.value !== "finding"; }
        toggle(); kind.addEventListener("change", toggle);
        form.append(el("label", {}, ["记录类型", kind]), el("label", {}, ["标题", title]), el("label", {}, ["内容", content]),
            el("label", {}, ["关联论文（可选）", papers]), el("label", {}, ["关联研究记录", related]), el("p", { class: "field-hint" }, "按住 Ctrl / ⌘ 可多选。方案关联问题，结果关联方案，研究判断关联实际结果。"), resultFields, findingFields);
        function values() { return { kind: kind.value, title: title.value, content: content.value, paper_ids: [...papers.selectedOptions].map(o => o.value), related_ids: [...related.selectedOptions].map(o => o.value), metricsText: metrics.value, result_status: status.value, assessment: assessment.value }; }
        form.addEventListener("input", () => saveDraft(key, values())); form.addEventListener("change", () => saveDraft(key, values()));
        const submit = el("button", { class: "primary", type: "submit" }, editing ? "保存新版本" : "保存记录");
        const actions = el("div", { class: "button-row" }, [submit]);
        if (editing) actions.append(el("button", { type: "button", onclick: () => { state.editing = null; renderContent(); } }, "取消修订"));
        form.append(actions);
        form.addEventListener("submit", async event => {
            event.preventDefault(); const pid = state.projectId;
            try {
                const raw = values(); const item = { kind: raw.kind, title: raw.title.trim(), content: raw.content.trim(), paper_ids: raw.paper_ids, related_ids: raw.related_ids };
                if (editing) item.supersedes_id = editing.item_id;
                if (raw.kind === "result") { item.result_status = raw.result_status; item.metrics = raw.metricsText.trim() ? JSON.parse(raw.metricsText) : {}; }
                if (raw.kind === "finding") item.assessment = raw.assessment;
                submit.disabled = true;
                const data = await api(endpoint("/items"), { expected_revision: workspace().revision, item });
                sessionStorage.removeItem(key);
                if (pid === state.projectId) { state.data = data; state.editing = null; renderAll(); }
                notify("研究记录已保存；相关判断由你确认，不会自动标为已验证");
            } catch (error) { notify(error instanceof SyntaxError ? "指标不是有效的 JSON，请检查括号和引号；输入已保留" : error.message, true); }
            finally { submit.disabled = false; }
        });
        return form;
    }
    function renderGuide(follow = false) {
        const thread = $("guide-messages"), previousScroll = thread.scrollTop;
        thread.replaceChildren(); $("guide-context").replaceChildren(); $("guide-starters").replaceChildren();
        $("guide-send").disabled = !state.data || state.busy || state.area === "writing";
        $("guide-send").textContent = state.busy ? "正在思考…" : "发送";
        $("guide-input").disabled = !state.data;
        $("reading-purpose-label").hidden = state.area !== "literature" || !state.data;
        if (!state.data) { $("guide-mode").textContent = "等待选择项目"; thread.append(el("p", { class: "muted small" }, "选择项目后，我们可以结合你的目标、论文和实验记录讨论。")); return; }
        $("guide-mode").textContent = state.data.guide_mode === "model" ? "模型指导" : "规则指导";
        const contextBox = $("guide-context"); contextBox.append(el("span", { class: "tag" }, `当前：${AREAS[state.area][0]}`));
        const selected = activeItems().find(i => i.item_id === state.selectedItem);
        if (selected) contextBox.append(el("button", { type: "button", onclick: () => { state.selectedItem = null; renderGuide(); } }, `${selected.title.slice(0, 22)} ×`));
        state.selectedPapers.forEach(id => contextBox.append(el("button", { type: "button", onclick: () => { state.selectedPapers = state.selectedPapers.filter(x => x !== id); renderGuide(); } }, `${(state.data.papers.find(p => p.paper_id === id)?.title || id).slice(0, 22)} ×`)));
        (state.data.starters[state.area] || []).forEach(text => $("guide-starters").append(el("button", { type: "button", onclick: () => { $("guide-input").value = text; sessionStorage.setItem(chatKey(), text); $("guide-input").focus(); } }, text)));
        if (!workspace().messages.length) thread.append(el("div", { class: "message" }, "你可以用对话补充条件、理解论文、比较问题、检查方案或组织写作。点击中间的“围绕这条讨论”后，我会将它纳入上下文。"));
        workspace().messages.forEach(message => {
            const body=el("div");window.SRA_DOCUMENT.render(body,message.content);
            const box = el("div", { class: `message ${message.role}` }, [el("span", { class: "message-label" }, `${message.role === "user" ? "你" : message.mode === "rules" ? "科研指导 · 规则模式" : "科研指导"} · ${AREAS[message.area][0]}`), body]);
            message.cited_paper_ids.forEach(id => box.append(el("span", { class: "source" }, `来源：${state.data.papers.find(p => p.paper_id === id)?.title || id}`)));
            message.questions.forEach(text => box.append(el("p", { class: "small" }, text)));
            message.suggestion_ids.forEach(id => {
                const suggestion = workspace().suggestions.find(s => s.suggestion_id === id); if (!suggestion) return;
                const stale = suggestion.based_on_revision !== workspace().revision;
                const detail = el("details", { class: "suggestion" }, [el("summary", {}, `建议草案：${suggestion.title}`), el("p", {}, suggestion.content)]);
                detail.append(el("button", { type: "button", disabled: ["result","finding","outline"].includes(suggestion.kind) || !!suggestion.applied_item_id || stale || state.busy, onclick: async event => {
                    event.target.disabled = true; const pid = state.projectId;
                    try { const data = await api(endpoint(`/suggestions/${id}/apply`), { expected_revision: workspace().revision }); if (pid === state.projectId) { state.data = data; renderAll(); } notify("已采纳为研究记录，可继续修改"); }
                    catch (error) { notify(error.message, true); renderGuide(); }
                } }, suggestion.applied_item_id ? "已采纳" : stale ? "项目已有更新，请重新讨论后采纳" : `采纳为${KINDS[suggestion.kind]}`));
                box.append(detail);
            });
            thread.append(box);
        });
        if (state.busy) thread.append(el("p", { class: "small muted" }, "正在结合当前材料生成指导，其他工作区仍可浏览。"));
        thread.scrollTop = follow ? thread.scrollHeight : previousScroll;
    }
    $("guide-form").addEventListener("submit", async event => {
        event.preventDefault(); if (!state.data || state.busy || state.area === "writing") return;
        const input = $("guide-input"), text = input.value.trim(); if (!text) return;
        const pid = state.projectId, area = state.area;
        const request = { expected_revision: workspace().revision, area, message: text, selected_item_id: state.selectedItem, selected_paper_ids: state.selectedPapers.slice(), reading_purpose: $("reading-purpose").value };
        state.busy = true; renderGuide(true);
        try {
            const data = await api(endpoint("/guide"), request);
            if (pid === state.projectId) { state.data = data; if (input.value.trim() === text) { input.value = ""; sessionStorage.removeItem(chatKey()); } }
        } catch (error) { notify(error.message, true); }
        finally { state.busy = false; renderGuide(pid === state.projectId); }
    });
    $("guide-latest").addEventListener("click", () => { const thread = $("guide-messages"); thread.scrollTop = thread.scrollHeight; });
    $("guide-input").addEventListener("input", event => sessionStorage.setItem(chatKey(), event.target.value));
    $("guide-input").addEventListener("keydown", event => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); $("guide-form").requestSubmit(); } });
    $("project-select").addEventListener("change", async event => { state.projectId = event.target.value; localStorage.setItem("research-workspace.project", state.projectId); await loadProject(); });
    $("new-project").addEventListener("click", () => $("create-dialog").showModal());
    $("close-dialog").addEventListener("click", () => $("create-dialog").close());
    $("create-form").addEventListener("submit", async event => {
        event.preventDefault(); const form = event.target, button = form.querySelector('[type="submit"]'); button.disabled = true;
        try {
            const data = await api("/api/projects", { topic: form.elements.topic.value.trim(), domain: form.elements.domain.value.trim() });
            localStorage.setItem("research-workspace.project", data.project_id); $("create-dialog").close(); form.reset(); await loadProjects(data.project_id); notify("项目已创建，先保存目标与条件即可开始讨论");
        } catch (error) { notify(error.message, true); } finally { button.disabled = false; }
    });
    loadProjects().catch(error => { renderAll(); notify(error.message, true); });
})();

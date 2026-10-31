/* 科研 Agent 系统前端逻辑 —— 单页应用，原生 JS 实现 */
(function () {
    "use strict";

    // 全局状态
    // API base URL：部署在静态托管（HF Spaces）时由 index.html 注入 window.SRA_API_BASE
    // 指向独立后端（如 Render）；同源部署/本地时不注入则保持相对路径
    const SRA_API_BASE = (typeof window !== "undefined" && window.SRA_API_BASE) || "";
    const NAVIGATION = window.SRA_NAVIGATION;
    const state = {
        currentProjectId: null,
        currentConversationId: null,
        currentPage: "dashboard",
        questionTab: "questions",
        methodTab: "reading",
        workspaceCounts: null,
        workspaceCountsProjectId: null,
        badgesProjectId: null,
        notesCount: null,
        reportGapsCount: null,
        reportCountLoadingPid: null,
        recordEditorIntent: null,
        pendingRecordId: null,
        publishedResearchFocus: null,
        statusCache: null,
        workflowCache: null,
        pollTimer: null,
        pollViewFingerprint: null, // 轮询内容不变时不重建页面，避免滚动条回位
        lastPollAt: 0,
        runMode: "",            // 运行模式（由 /status 的 run_mode 同步）
        resumingProjectId: null,
        humanFingerprint: null, // 最近一次 pending_human 的指纹（用于跳过无变化的整页重建）
        humanDraft: "",         // 人工输入草稿（轮询重建时恢复）
        pendingPaperId: null,   // 待定位论文（证据溯源跳转：gaps/claims → papers 定位高亮）
        papersCache: null,      // 论文列表缓存（供前端筛选重绘）
        searchPrefs: null,      // 最近一次提交的检索偏好（年份/期刊）
        searchPrefsForm: null,  // 检索范围配置表单引用（提交时收集）
        pendingExperimentId: null, // 待定位实验（Claim 证据 → experiments 定位高亮）
        papersView: "all",      // 论文浏览页视图：all / unlinked（未入库候选）
        // 论文浏览筛选状态
        papersFilter: {
            q: "",          // 关键词（标题/作者/摘要/ID）
            yearMin: "",
            yearMax: "",
            venue: "",      // 期刊/venue 关键词
            sort: "newest", // newest / oldest / relevance / title / if_desc / if_asc
            casZone: "",    // 中科院分区筛选："" / "1" / "2" / "3" / "4"
            ifMin: "",      // 最低影响因子
            pdfOnly: false, // 仅显示可下载 PDF 的论文
        },
        dashboardCache: null,   // /dashboard 聚合数据
        researchReportCache: null,   // /research-report 调研报告
        domain: "",             // 当前项目的研究领域（新建时选择，全程注入各阶段 prompt）
        pendingPaperId: null,        // 证据跳转目标：点击证据中的 paper_id 后置位，renderPapers 自动展开滚动
    };

    const STAGES = ["research", "ideation", "design", "experiment", "writing"];
    const STAGE_LABELS = {
        topic_discovery: "选题探索",
        research: "调研",
        ideation: "思路探讨",
        design: "方案制定",
        experiment: "实验规划",
        writing: "论文写作",
    };

    // ===== 工具函数 =====

    function el(tag, attrs, children) {
        const node = document.createElement(tag);
        if (attrs) {
            for (const k in attrs) {
                if (k === "class") node.className = attrs[k];
                else if (k === "text") node.textContent = attrs[k];
                else if (k === "html") node.innerHTML = attrs[k];
                else if (k.startsWith("data-")) node.setAttribute(k, attrs[k]);
                else if (k === "onclick") node.addEventListener("click", attrs[k]);
                else if (k in node) node[k] = attrs[k];
                else node.setAttribute(k, attrs[k]);
            }
        }
        if (children) {
            if (!Array.isArray(children)) children = [children];
            children.forEach(c => {
                if (c == null) return;
                node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
            });
        }
        return node;
    }

    function clear(node) {
        while (node.firstChild) node.removeChild(node.firstChild);
    }

    function truncateText(s, max) {
        if (s == null) return "";
        s = String(s);
        return s.length > max ? s.slice(0, max - 1) + "…" : s;
    }

    function escapeHtml(s) {
        if (s == null) return "";
        return String(s)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    // ===== 骨架屏 =====
    function renderSkeleton(type) {
        const wrap = el("div", { class: "skeleton-wrap" });
        if (type === "card") {
            wrap.appendChild(el("div", { class: "skeleton skeleton-line lg" }));
            wrap.appendChild(el("div", { class: "skeleton skeleton-line" }));
            wrap.appendChild(el("div", { class: "skeleton skeleton-line sm" }));
        } else if (type === "list") {
            for (let i = 0; i < 4; i++) {
                wrap.appendChild(el("div", { class: "skeleton skeleton-block" }));
            }
        } else if (type === "cards") {
            for (let i = 0; i < 3; i++) {
                wrap.appendChild(el("div", { class: "skeleton skeleton-card" }));
            }
        } else {
            wrap.appendChild(el("div", { class: "skeleton skeleton-line lg" }));
            wrap.appendChild(el("div", { class: "skeleton skeleton-line" }));
        }
        return wrap;
    }

    // ===== 空状态 =====
    function renderEmptyState(opts) {
        return el("div", { class: "empty-state" }, [
            el("div", { class: "empty-state-icon", html: opts.icon || "📭" }),
            el("div", { class: "empty-state-title", text: opts.title || "暂无数据" }),
            el("div", { class: "empty-state-desc", text: opts.desc || "" }),
            opts.actions ? el("div", { class: "empty-state-actions" }, opts.actions) : null,
        ].filter(Boolean));
    }

    // ===== 错误状态 =====
    function renderErrorState(opts) {
        return el("div", { class: "error-state" }, [
            el("div", { class: "error-state-icon", text: "⚠" }),
            el("div", { class: "error-state-title", text: opts.title || "出错了" }),
            el("div", { class: "error-state-desc", text: opts.desc || opts.message || "" }),
            opts.actions ? el("div", { class: "error-state-actions" }, opts.actions) : null,
        ].filter(Boolean));
    }

    // ===== 全局 Loading 遮罩 =====
    function showLoadingOverlay(text) {
        const overlay = document.getElementById("loading-overlay");
        const txt = document.getElementById("loading-overlay-text");
        if (overlay) {
            if (text && txt) txt.textContent = text;
            overlay.style.display = "flex";
        }
    }

    function hideLoadingOverlay() {
        const overlay = document.getElementById("loading-overlay");
        if (overlay) overlay.style.display = "none";
    }

    // ===== 进度指示器 =====
    function renderProgressIndicator(text) {
        return el("span", { class: "progress-indicator", "data-tooltip": "系统正在后台运行" }, [
            el("span", { class: "progress-dot" }),
            el("span", { text: text || "运行中" }),
        ]);
    }

    function formatTime(iso) {
        if (!iso) return "—";
        try {
            // 后端存的是 UTC 时间（无时区后缀），前端显示为本地时区
            const d = new Date(iso + "Z"); // 加 Z 标记为 UTC
            if (isNaN(d.getTime())) return iso;
            const pad = n => String(n).padStart(2, "0");
            return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
        } catch (e) {
            return iso;
        }
    }

    function showToast(msg, kind) {
        const t = document.getElementById("toast");
        t.textContent = msg;
        t.className = "toast show" + (kind ? " " + kind : "");
        clearTimeout(t._timer);
        t._timer = setTimeout(() => {
            t.className = "toast" + (kind ? " " + kind : "");
        }, 2400);
    }

    function downloadFile(artifactType, filename, format) {
        if (!state.currentProjectId) return;
        const fmt = format || "md";
        const url = `/api/projects/${state.currentProjectId}/download/${artifactType}?format=${fmt}`;
        fetch(url)
            .then(resp => {
                if (!resp.ok) throw new Error("下载失败");
                return resp.blob();
            })
            .then(blob => {
                const a = document.createElement("a");
                a.href = URL.createObjectURL(blob);
                // 根据格式调整扩展名
                const dotIdx = filename.lastIndexOf(".");
                const baseName = dotIdx > 0 ? filename.substring(0, dotIdx) : filename;
                const ext = fmt === "docx" ? ".docx" : fmt === "pdf" ? ".pdf" : (dotIdx > 0 ? filename.substring(dotIdx) : ".md");
                a.download = baseName + ext;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(a.href);
                showToast("已下载 " + a.download, "success");
            })
            .catch(e => showToast("下载失败: " + e.message, "error"));
    }

    function renderDownloadBar(items) {
        const bar = el("div", { class: "download-bar" }, [
            el("span", { class: "download-bar-label" }, "下载产出："),
        ]);

        // 格式选择器
        const fmtSelect = el("select", { class: "download-format-select" });
        [
            { v: "md", t: "Markdown (.md)" },
            { v: "docx", t: "Word (.docx)" },
            { v: "pdf", t: "PDF (.pdf)" },
        ].forEach(o => {
            const opt = el("option", { value: o.v, text: o.t });
            fmtSelect.appendChild(opt);
        });
        bar.appendChild(fmtSelect);

        items.forEach(it => {
            bar.appendChild(el("button", {
                class: "btn btn-secondary btn-sm",
                text: it.label,
                onclick: () => {
                    const fmt = fmtSelect.value;
                    const ext = fmt === "docx" ? ".docx" : fmt === "pdf" ? ".pdf" : ".md";
                    downloadFile(it.type, it.filename, fmt);
                },
            }));
        });
        return bar;
    }

    // Legacy callers may still request a refresh; downloads now live in the
    // conversation header and are populated by the server's actual inventory.
    function renderSidebarDownload() {
        // Deliberate no-op, no duplicate/sidebar download menu.
    }

    async function api(method, path, body) {
        const opts = { method, headers: {} };
        if (body !== undefined) {
            opts.headers["Content-Type"] = "application/json";
            opts.body = JSON.stringify(body);
        }
        // 静态托管场景：为相对路径补上远程后端地址；绝对路径/已带 base 的不重复拼
        const url = path.startsWith("http") ? path : SRA_API_BASE + path;
        const resp = await fetch(url, opts);
        if (resp.status === 204) return null;
        let data = null;
        try { data = await resp.json(); } catch (e) { data = null; }
        if (!resp.ok) {
            const msg = (data && data.detail) || resp.statusText || `HTTP ${resp.status}`;
            throw new Error(msg);
        }
        return data;
    }

    // 节点 ID → 中文描述（进度页「正在执行 / 下一步」展示）
    const NODE_LABELS = {
        // research 阶段
        topic_refine: "主题解析与检索策略制定",
        subquery_decompose: "研究问题分解（拆分子查询）",
        cp_before_confirm: "检查点：准备人工确认",
        topic_confirm: "研究主题确认（等待确认）",
        paper_fetch: "文献抓取（Sciverse/arxiv 多源检索）",
        paper_filter: "论文相关性筛选与打分",
        paper_ingest: "论文入库与 URL 补全",
        material_extraction: "领域知识抽取（研究对象-属性指标-方法条件三元组）",
        cross_validate: "多源信息交叉验证",
        research_gap: "提取文献中的待核实问题",
        // ideation 阶段
        brainstorm: "候选思路生成（与用户探讨）",
        idea_discuss: "思路讨论（等待人工输入）",
        cp_before_validate: "检查点：思路验证前",
        idea_validate: "思路可行性/新颖性验证",
        claim_draft: "研究论断起草",
        // design 阶段
        atom_decompose: "方法原子概念分解",
        method_formalize: "方法形式化（公式/伪代码）",
        cp_before_review: "检查点：方案审查前",
        method_review: "方案人工审查（等待确认）",
        claim_evidence_link: "论断与证据关联",
        method_artifact: "方法文档产出",
        // experiment 阶段
        experiment_config: "实验配置生成",
        code_generate: "实验代码生成（已停用：实验只交付任务不交付代码）",
        code_review: "代码审查（已停用：实验只交付任务不交付代码）",
        cp_before_run: "检查点：实验运行前",
        anomaly_check: "实验异常检测",
        claim_verify: "核查论断与实验的关联",
        experiment_outcome_assess: "实验成败评估",
        // writing 阶段
        style_learn: "写作风格学习",
        outline: "论文大纲生成",
        section_draft: "论文章节撰写",
        review: "论文审稿",
        // 关系发现子流程（可选）
        hypothesis_seed: "关系假设生成",
        search_space: "搜索空间定义",
        llm_guided_search: "LLM 引导搜索",
        discovery_validate: "发现验证（文献交叉+新颖性）",
        discovery_report: "发现报告生成",
        // topic_discovery 阶段
        trend_fetch: "趋势数据获取",
        trend_analysis: "趋势分析（新兴/稳定/饱和）",
        topic_recommend: "推荐研究主题生成",
        topic_select: "主题选择（等待用户选择）",
    };

    function nodeLabel(nodeId) {
        return NODE_LABELS[nodeId] || (nodeId || "未知节点");
    }

    function statusBadge(status) {
        const map = {
            success: "badge-success",
            completed: "badge-success",
            verified: "badge-success",
            done: "badge-success",
            running: "badge-info",
            in_progress: "badge-info",
            pending_human: "badge-warning",
            pending_review: "badge-warning",
            draft: "badge-neutral",
            planned: "badge-neutral",
            not_started: "badge-neutral",
            evidence_linked: "badge-info",
            failed: "badge-danger",
            aborted: "badge-danger",
            refuted: "badge-danger",
            blocked: "badge-danger",
            anomaly_detected: "badge-warning",
            superseded: "badge-neutral",
            experiment_failed: "badge-warning",
            experiment_pending: "badge-info",
            stopped: "badge-neutral",
            created: "badge-neutral",
            rejected: "badge-danger",
            validated: "badge-info",
            adopted: "badge-success",
        };
        const cls = map[status] || "badge-neutral";
        return `<span class="badge ${cls}">${escapeHtml(status)}</span>`;
    }

    function statusBanner(status, summary, error, recommendation, advice) {
        let cls = "info";
        if (["completed", "success"].includes(status)) cls = "success";
        else if (["pending_human", "pending_review", "experiment_failed", "experiment_pending", "anomaly_detected"].includes(status)) cls = "warning";
        else if (["failed", "aborted", "blocked"].includes(status)) cls = "danger";

        const parts = [];
        parts.push(`<span class="status-dot"></span><strong>${escapeHtml(status)}</strong>`);
        if (summary) parts.push(`<span>${escapeHtml(summary)}</span>`);
        if (error) parts.push(`<span class="mono small">错误：${escapeHtml(error)}</span>`);
        if (recommendation) parts.push(`<span class="small">建议：${escapeHtml(recommendation)}</span>`);
        let adviceHtml = "";
        if (Array.isArray(advice) && advice.length) {
            const rows = advice.map(a => {
                const target = a.target ? `<span class="badge badge-info">${escapeHtml(a.target)}</span>` : "";
                return `<div class="advice-row">
                    ${target}
                    <strong>${escapeHtml(a.action || "")}</strong>
                    <div class="small muted">依据：${escapeHtml(a.reason || "")}</div>
                    <div class="small muted">预期：${escapeHtml(a.expected || "")}</div>
                </div>`;
            }).join("");
            adviceHtml = `<div class="advice-block mt-8"><div class="small advice-title">方法改进建议：</div>${rows}</div>`;
        }
        return `<div class="status-banner ${cls}">${parts.join(" · ")}</div>${adviceHtml}`;
    }

    // ===== 导航 =====

    function setActivePage(page, options = {}) {
        if (!NAVIGATION.pages[page]) page = "dashboard";
        state.currentPage = page;
        const url=new URL(window.location.href);
        url.searchParams.set("page",page);
        if(state.currentProjectId) url.searchParams.set("project",state.currentProjectId);
        else {url.searchParams.delete("project");url.searchParams.delete("paper");url.searchParams.delete("exp");}
        if(state.currentConversationId && state.currentProjectId) url.searchParams.set("conversation",state.currentConversationId);
        else url.searchParams.delete("conversation");
        if(url.href!==location.href) window.history[options.replace ? "replaceState" : "pushState"](null,"",url);
        document.body.dataset.page=page;
        state.pollViewFingerprint = null;
        document.querySelectorAll(".nav-item").forEach(n => {
            const active = n.getAttribute("data-page") === (NAVIGATION.pages[page].owner || page);
            n.classList.toggle("active", active);
            if(active && n.closest("details")) n.closest("details").open=true;
            if (active) n.setAttribute("aria-current", "page"); else n.removeAttribute("aria-current");
        });
        const title = NAVIGATION.pages[page].title;
        const topbarTitle = document.getElementById("topbar-title");
        if (topbarTitle) topbarTitle.textContent = title;
        renderPage();
    }

    // 证据溯源全局跳转：Claim 证据/冲突 → 论文页定位 / 实验页
    window.__sraGoPaper = function (paperId) {
        state.pendingPaperId = paperId;
        setActivePage("papers");
        return false;
    };
    window.__sraGoExperiment = function (expId) {
        state.pendingExperimentId = expId;
        setActivePage("experiments");
        return false;
    };

    // ===== 顶部状态徽章更新 =====
    function updateTopbarStatus(status) {
        if(window.SRA_CONVERSATIONS){
            const workflow=state.workflowCache?.projectId===state.currentProjectId ? state.workflowCache : state.statusCache?.workflow;
            const badge=document.getElementById('topbar-status'),label=document.getElementById('topbar-status-text');
            if(!state.currentProjectId){badge.style.display='none';return;}
            const flow=workflow?.flow;
            const progress=flow?.next?.action==='revise'?'材料待复核':flow?.planReady?'方案可审阅':flow?.question?'问题已确认':'问题待确认';
            badge.style.display='';label.textContent=progress+(status==='running'?' · 旧后台运行中':status==='pending_human'?' · 待人工确认':'');badge.title='研究进展来自正式材料；任务状态请看对应消息或任务活动。不代表科学结论已验证。';return;
        }
        const node = document.getElementById("topbar-status");
        const txt = document.getElementById("topbar-status-text");
        if (!node || !txt) return;
        if (!status || status === "idle") {
            node.style.display = "none";
            return;
        }
        node.style.display = "flex";
        node.className = "topbar-status";
        node.title = "后台任务状态，不表示科研结论已经验证或项目全部完成";
        let cls = "", text = "";
        if (status === "running" || status === "in_progress") { cls = "running"; text = "运行中"; }
        else if (status === "completed") { cls = "completed"; text = "任务已结束"; }
        else if (status === "failed" || status === "blocked") { cls = "failed"; text = "失败"; }
        else if (status === "pending_human" || status === "pending_review") { cls = "pending"; text = "等待人工"; }
        else if (status === "experiment_failed") { cls = "failed"; text = "实验失败"; }
        else if (status === "experiment_pending") { cls = "pending"; text = "历史实验（仅查看）"; }
        else if (status === "created") { text = "已创建"; }
        else if (status === "stopped") { text = "任务已停止"; }
        else if (status === "aborted") { text = "任务已取消"; }
        else { cls = ""; text = status; }
        if (cls) node.classList.add(cls);
        txt.textContent = text;
    }

    // ===== 快捷键 =====
    function setupKeyboardShortcuts() {
        document.addEventListener("keydown", (e) => {
            const tag = (e.target.tagName || "").toLowerCase();
            const editing = ["input", "textarea", "select"].includes(tag) || e.target.isContentEditable;
            // 帮助面板：? 或 F1
            if ((!editing && e.key === "?") || e.key === "F1") {
                e.preventDefault();
                toggleHelpPanel();
                return;
            }
            // Esc：关闭帮助面板
            if (e.key === "Escape") {
                closeHelpPanel();
                return;
            }
            // 数字键 1-8：导航（仅在非输入元素聚焦时）
            if (editing) return;
            if (e.ctrlKey || e.metaKey || e.altKey) return;
            const map = NAVIGATION.shortcuts;
            const target = map[e.key];
            if (target) {
                e.preventDefault();
                setActivePage(target);
            }
        });
    }

    function renderNavigationHelp() {
        const help = document.getElementById("help-navigation");
        if (help) Object.entries(NAVIGATION.shortcuts).forEach(([key, page]) => {
            help.appendChild(el("div", {class:"help-row"}, [el("kbd", {}, key), " - ", el("span", {}, NAVIGATION.pages[page].title)]));
        });
    }

    function toggleHelpPanel() {
        const panel = document.getElementById("help-panel");
        if (panel) panel.style.display = panel.style.display === "none" ? "flex" : "none";
    }

    function closeHelpPanel() {
        const panel = document.getElementById("help-panel");
        if (panel) panel.style.display = "none";
    }

    function updateProjectIdDisplay() {
        // 切换项目先清空旧统计，避免新请求返回前显示上一项目的数字。
        if (state.badgesProjectId !== state.currentProjectId) {
            state.badgesProjectId = state.currentProjectId;
            state.notesCount = null;
            state.reportGapsCount = null;
            state.recordEditorIntent = null;
            state.pendingRecordId = null;
            state.publishedResearchFocus = null;
            state.workspaceCounts = null;
            state.workspaceCountsProjectId = null;
            state.materialCounts = null;
            if (state.statusCache?.project_id !== state.currentProjectId) state.statusCache = null;
            document.querySelectorAll(".nav-badge").forEach(node => {
                node.style.display = "none"; node.textContent = ""; node.removeAttribute("title");
            });
        }
        document.getElementById("current-project-id").textContent =
            state.currentProjectId || "未创建";
        publishGuideContext();
    }

    function publishGuideContext() {
        window.dispatchEvent(new CustomEvent("sra:context", {
            detail: { projectId: state.currentProjectId, page: state.currentPage },
        }));
    }

    window.addEventListener("sra:note-saved", event => {
        if (event.detail.projectId !== state.currentProjectId) return;
        renderSidebarNotes();
        if (state.currentPage === "notes") renderPage(true);
    });

    // ===== 轮询 =====

    function startPolling() {
        stopPolling();
        state.pollTimer = setInterval(pollStatus, 2000);
        // 立即跑一次
        pollStatus();
    }

    function stopPolling() {
        if (state.pollTimer) {
            clearInterval(state.pollTimer);
            state.pollTimer = null;
        }
    }

    let statusReadTicket = 0;
    async function pollStatus() {
        if (!state.currentProjectId) return;
        const pid = state.currentProjectId, ticket = ++statusReadTicket;
        try {
            const data = await api("GET", `/api/projects/${pid}/status`);
            if (pid !== state.currentProjectId || ticket !== statusReadTicket) return;
            const prevPending = state.statusCache?.pending_human;
            state.statusCache = data;
            if(data.workflow) state.workflowCache={projectId:pid,...data.workflow};
            state.runMode = data.run_mode || "";
            state.domain = data.domain || "";
            if (data.notes_count != null) state.notesCount = data.notes_count;
            if (data.counts?.report_gaps != null) state.reportGapsCount = data.counts.report_gaps;
            else refreshReportBadgeCount(pid); // 兼容尚未重启升级的服务，不以全部缺口数冒充报告问题数。
            updateBadges(data);
            updateTopbarStatus(data.status);
            window.dispatchEvent(new CustomEvent("sra:project-status",{detail:{projectId:pid}}));
            const curPending = data.pending_human;
            if (!prevPending && curPending) {
                showToast("有任务需要确认，请前往「待确认事项」处理", "warning");
            }
            // 约束编辑期间仍接收后台状态，但不重建页面覆盖未保存的草稿。
            if(document.querySelector('.project-brief-form[data-dirty="true"]')) {renderFlowNav();return;}
            if (state.currentPage === "progress" || state.currentPage === "topic-discovery") {
                const fingerprint = JSON.stringify({
                    status: data.status, summary: data.summary, error: data.error,
                    current_node: data.current_node, current_stage: data.current_stage,
                    pending_human: data.pending_human, stage_statuses: data.stage_statuses,
                    counts: data.counts, node_history: data.node_history,
                    topic_discovery: data.topic_discovery,
                    node_progress: data.node_progress, workflow: data.workflow,
                });
                if (fingerprint !== state.pollViewFingerprint) {
                    state.pollViewFingerprint = fingerprint;
                    renderPage(true);
                }
            } else if (state.currentPage === "human") {
                const fp = JSON.stringify(curPending);
                if (fp !== state.humanFingerprint) {
                    state.humanFingerprint = fp;
                    state.humanDraft = "";
                    renderPage(true);
                }
            } else {
                renderFlowNav();
            }
        } catch (e) {
            // 静默
        }
    }

    async function refreshReportBadgeCount(pid) {
        if (state.reportCountLoadingPid === pid) return;
        state.reportCountLoadingPid = pid;
        try {
            const data = await api("GET", `/api/projects/${pid}/research-report`);
            if (pid !== state.currentProjectId || data.error) return;
            state.reportGapsCount = (data.report?.gaps || []).length;
            updateBadges(state.statusCache || {});
        } catch (_) { /* 暂时失败时保留已知统计，不把未知当作零。 */ }
        finally { if (state.reportCountLoadingPid === pid) state.reportCountLoadingPid = null; }
    }

    function updateBadges(data) {
        if (data.project_id && data.project_id !== state.currentProjectId) return;
        const counts = data.counts || {};
        const cached=state.workflowCache?.projectId===state.currentProjectId ? state.workflowCache : null;
        const workflow=cached && cached.revision>=(data.workflow?.revision ?? -1) ? cached : data.workflow || cached;
        const records = workflow?.counts || (state.workspaceCountsProjectId === state.currentProjectId ? state.workspaceCounts : null);
        const count = value => Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0;
        setBadge("badge-papers", counts.papers);
        setBadge("badge-gaps", records ? records.question : null);
        const materialCounts=data.material_counts || state.materialCounts;
        setBadge("badge-report-gaps", materialCounts?.research_reports ?? null);
        setBadge("badge-claims", counts.claims);
        const sourceTasks=workflow?.materials?.filter(source=>source.kind==="experiment_plan") || [];
        const linkedTasks=sourceTasks.filter(source=>source.linked_item_id && !source.linked_needs_review).length;
        setBadge("badge-experiments", workflow ? sourceTasks.length+Math.max(0,count(records?.plan)-linkedTasks) : records ? count(records.plan) + count(records.result) + count(counts.experiments) : counts.experiments);
        setBadge("badge-writing", records ? count(records.outline) + count(counts.writing_artifacts) : counts.writing_artifacts);
        const writingBadge = document.getElementById("badge-writing");
        if(writingBadge) writingBadge.title = `写作提纲：${records?.outline || 0}；已保存稿件与修改意见：${counts.writing_artifacts ?? "待读取"}（不表示论文已审核完成）`;
        const questionsBadge = document.getElementById("badge-gaps");
        if (questionsBadge) questionsBadge.title = `已保存且无需复核的问题：${records?.question || 0}；文献待核实线索：${counts.gaps || 0}；原论断：${counts.claims || 0}`;
        const experimentsBadge = document.getElementById("badge-experiments");
        if (experimentsBadge) experimentsBadge.title = workflow ? `方案 / 任务来源数（按关联去重，不累计结果）：已确认方案 ${records?.plan || 0}；任务来源 ${sourceTasks.length}；实际结果记录 ${records?.result || 0}。不表示实验成功。` : `方案：${records?.plan || 0}；实际结果记录：${records?.result || 0}；原实验记录：${counts.experiments || 0}（计数不表示实验成功）`;
        setBadge("badge-notes", state.notesCount ?? data.notes_count ?? null);
        const reportBadge = document.getElementById("badge-report-gaps");
        if (reportBadge) reportBadge.title = "已保存的研究现状报告份数（不累计版本，不是候选问题数）";
        const notesBadge = document.getElementById("badge-notes");
        if (notesBadge) notesBadge.title = "当前项目保存的灵感笔记总数";
        setBadge("badge-human", data.pending_human ? "!" : null);
    }

    function setBadge(id, value) {
        const node = document.getElementById(id);
        if (!node) return;
        // 常亮：数字存在即显示（含 0），null/undefined 才隐藏
        if (value == null) {
            node.style.display = "none";
        } else {
            node.style.display = "inline-block";
            node.textContent = value;
        }
    }

    window.addEventListener("sra:workspace-loaded", event => {
        if (event.detail.projectId !== state.currentProjectId) return;
        if (Number.isInteger(event.detail.paper_count)) {
            ++statusReadTicket; // Discard a status read started before this material snapshot.
            // Workspace counts describe research records, NOT the paper library.
            state.statusCache = {...state.statusCache, project_id:state.currentProjectId,
                counts:{...state.statusCache?.counts, papers:event.detail.paper_count}};
        }
        state.workspaceCountsProjectId = event.detail.projectId;
        state.workspaceCounts = event.detail.counts;
        if(event.detail.material_counts)state.materialCounts=event.detail.material_counts;
        if(event.detail.workflow)state.workflowCache={projectId:event.detail.projectId,...event.detail.workflow};
        updateBadges(state.statusCache || {});
        updateTopbarStatus(state.statusCache?.status);
        renderFlowNav();
    });

    window.addEventListener("sra:papers-changed", event => {
        if (event.detail.projectId === state.currentProjectId) pollStatus();
    });

    // ===== 页面渲染入口 =====

    // 流程导航条：按项目真实运行顺序（人工确认节点用 👤 标记）
    // 与领域无关：同一套流程可跑材料、生物、医学、AI 等任意研究方向
    // 自动节点合并为阶段产出：论文浏览=抓取+筛选+入库+知识抽取，研究缺口=交叉验证+缺口识别
    const FLOW_STEPS = [
        { label: "选题探索", page: "topic-discovery", human: false, nodes: ["trend_fetch", "trend_analysis", "topic_recommend"], stage: "topic_discovery" },
        { label: "选择主题", page: "human", human: true, nodes: ["topic_select"], stage: "topic_discovery" },
        { label: "确认检索方向", page: "human", human: true, nodes: ["topic_confirm"], stage: "research" },
        { label: "文献库", page: "papers", human: false, nodes: ["paper_fetch", "paper_filter", "paper_ingest", "material_extraction"], stage: "research" },
        { label: "研究现状", page: "research-report", human: false, nodes: ["cross_validate", "research_gap"], stage: "research" },
        { label: "思路探讨", page: "human", human: true, nodes: ["brainstorm", "idea_discuss"], stage: "ideation" },
        { label: "方法确认", page: "human", human: true, nodes: ["atom_decompose", "method_formalize", "method_review"], stage: "design" },
        { label: "方案与实验", page: "experiments", human: false, nodes: ["experiment_config", "experiment_review", "experiment_run", "anomaly_check", "claim_verify", "experiment_outcome_assess", "outcome_assess"], stage: "experiment" },
        { label: "终稿确认", page: "human", human: true, nodes: ["provenance_check", "style_learn", "outline", "section_draft", "review", "revise"], stage: "writing" },
    ];
    // 页面 → 步骤索引（用于手动跳页时定位高亮）
    const FLOW_STEP_OF_PAGE = {
        dashboard: 0,
        "topic-discovery": 0,
        create: 0,
        progress: 0,
        "research-report": 4,
        papers: 3,
        gaps: 4,
        claims: 7,
        experiments: 7,
        writing: 8,
        notes: 8,
        human: 1,
    };

    // 计算当前活动步骤索引（后端 current_node 命中优先；否则取第一个未完成阶段的首步）
    function currentActiveStepIndex() {
        return NAVIGATION.executionProgress(state.statusCache, FLOW_STEPS).stepIndex;
    }

    // 根据后端状态判断步骤：skipped（跳过）/ done / active / pending
    function flowStepState(i) {
        return NAVIGATION.executionProgress(state.statusCache, FLOW_STEPS).states[i];
    }

    function renderFlowNav() {
        // 独立容器渲染（灵感笔记页隐藏）
        const host = document.getElementById("flow-nav-host");
        if (!host) return null;
        if (state.currentPage === "notes" || state.currentPage === "dashboard") {
            host.style.display = "none";
            return null;
        }
        host.style.display = "";
        const workflow=state.workflowCache?.projectId===state.currentProjectId ? state.workflowCache : state.statusCache?.workflow;
        const productStages=[['orientation','明确条件','dashboard'],['literature','了解研究现状','research-report'],['question','确认研究问题','gaps'],['design','形成并审阅方案','experiments']];
        const signature=JSON.stringify([state.currentProjectId,state.currentPage,workflow?.product_stage,workflow?.stages,workflow?.continuation]);
        if(host.dataset.uiFlowSignature===signature&&host.firstChild)return host.firstChild;
        const expanded=!!host.querySelector('details[open]');clear(host);host.dataset.uiFlowSignature=signature;
        const nav=el('details',{class:'ui-flow-nav'});nav.open=expanded;
        const current=productStages.find(s=>s[0]===workflow?.product_stage);
        nav.appendChild(el('summary',{},`研究阶段 · ${current?.[1] || '状态待读取'}${workflow?.flow?.planReady?' · 方案可审阅':''}`));
        const list=el('ol',{'aria-label':'四阶段研究路径'});
        for(const [id,label,page] of productStages){
            const saved=workflow?.stages?.find(s=>s.id===id);
            const status=id==='orientation'?'讨论目标与条件，不代表已确认':({available:'有材料，待核对',confirmed:'已确认',review:'待复核',pending:'待推进'})[saved?.state] || '状态待读取';
            list.appendChild(el('li',{},el('button',{type:'button','data-stage':id,'aria-current':id===workflow?.product_stage?'step':'false',title:saved?.detail || status,onclick:()=>setActivePage(page)},[label,el('small',{},status)])));
        }
        nav.appendChild(list);host.appendChild(nav);return nav;

    }

    function renderPage(preserveScroll = false) {
        window.SRA_CONVERSATIONS?.leave();
        if(["dashboard","project-overview"].includes(state.currentPage)) stopPolling();
        else if(state.currentProjectId && !state.pollTimer) startPolling();
        publishGuideContext();
        const host = document.getElementById("content");
        const previousScrollTop = preserveScroll ? host.scrollTop : 0;
        const previousWindowScrollY = preserveScroll ? window.scrollY : 0;
        // 每次导航使用独立视图，晚返回的旧页面请求只写入已脱离文档的节点，
        // 不再把项目列表或论文内容混进用户刚切换到的页面。
        const content = el("div", { class: "page-view" });
        host.replaceChildren(content);

        renderFlowNav();

        // 保留旧笔记计数同步；统一入口位于上方「笔记」，不再展示重复编辑器。
        if(!["dashboard","project-overview"].includes(state.currentPage)) {renderSidebarNotes();renderSidebarDownload();}

        if (state.currentPage !== "create" && state.currentPage !== "dashboard"
            && state.currentPage !== "reading-method" && state.currentPage !== "writing-method"
            && state.currentPage !== "methods"
            && !state.currentProjectId) {
            content.appendChild(renderNoProject());
            return;
        }

        switch (state.currentPage) {
            case "dashboard": renderDashboard(content); break;
            case "project-overview": window.SRA_WORKSPACE_VIEW.mountOverview(content,window.__sraApp); break;
            case "create": renderCreate(content); break;
            case "topic-discovery": renderTopicDiscovery(content); break;
            case "progress": renderProgress(content); break;
            case "research-report": renderResearchReport(content); break;
            case "papers": renderPapers(content); break;
            case "gaps": renderQuestions(content); break;
            case "claims": renderQuestions(content, "evidence"); break;
            case "experiments": renderExperiments(content); break;
            case "writing": renderWriting(content); break;
            case "notes": renderNotes(content); break;
            case "human": renderHuman(content); break;
            case "methods": renderMethods(content); break;
            case "reading-method": renderMethods(content, "reading"); break;
            case "writing-method": renderMethods(content, "writing"); break;
        }
        if (preserveScroll) {
            host.scrollTop = previousScrollTop;
            window.scrollTo(window.scrollX, previousWindowScrollY);
        }
    }

    function renderNoProject() {
        return el("div", { class: "card" }, [
            el("div", { class: "card-title" }, "尚未创建或选择项目"),
            el("p", { class: "muted" }, "请先在「新建与管理」页面选择已有项目，或创建一个科研项目。"),
            el("div", { class: "btn-row mt-16" }, [
                el("button", { class: "btn", onclick: () => setActivePage("create") }, "去创建项目"),
            ]),
        ]);
    }

    // ===== 0. Dashboard 总览页 =====

    async function renderDashboard(content) {
        if(window.SRA_CONVERSATIONS) {
            window.SRA_CONVERSATIONS.mount(content,{projectId:state.currentProjectId,conversationId:state.currentConversationId});
            return;
        }
        if (!state.currentProjectId) {
            // 无项目时显示系统介绍
            content.appendChild(el("h2", {class:"page-title"}, "项目总览"));
            content.appendChild(renderDashboardIntro());
            return;
        }

        // 骨架屏占位
        const pid=state.currentProjectId;
        content.appendChild(renderSkeleton("cards"));
        try {
            const data = await api("GET", `/api/projects/${state.currentProjectId}/dashboard`);
            if(pid!==state.currentProjectId || state.currentPage!=="dashboard") return;
            state.dashboardCache = data;
            clear(content);
            content.appendChild(el("div", {class:"page-header"}, [el("h2", {class:"page-title"}, "项目总览"), el("p", {class:"page-desc"}, "集中查看当前项目的任务状态与已有材料，点击研究路径进入对应页面。") ]));
            content.appendChild(renderQuickStartGuide());
            content.appendChild(renderContinuationCard(data));
            const workspaceData=api("GET",`/api/projects/${state.currentProjectId}/workspace?include_chat=false`);
            const workGrid=el("div",{class:"overview-work-grid"});
            content.appendChild(workGrid);
            renderProjectManagement(workGrid,workspaceData);
            renderResearchFlow(workGrid,workspaceData);
            let materialLinks=renderOverviewLinks(data);
            content.appendChild(materialLinks);
            workspaceData.then(workspace=>{
                if(pid!==state.currentProjectId || !materialLinks.isConnected) return;
                const updated=renderOverviewLinks(data,workspace.counts);
                materialLinks.replaceWith(updated);materialLinks=updated;
            }).catch(()=>{}); // 项目管理区域会显示详细读取错误，不把未知记录数写成零。
            const more=el("details",{class:"card overview-more"},el("summary",{},"更多操作、数据来源与文件下载"));
            renderDashboardDataSources(more);
            more.appendChild(renderDashboardActions(data));
            content.appendChild(more);
        } catch (e) {
            clear(content);
            content.appendChild(renderErrorState({
                title: "项目总览加载失败",
                desc: e.message || "请检查网络或项目状态",
                actions: [
                    el("button", {
                        class: "btn",
                        onclick: () => setActivePage("dashboard"),
                    }, "重试"),
                ],
            }));
        }
    }

    function renderDashboardIntro() {
        return renderQuickStartGuide();
    }

    function renderQuickStartGuide() {
        const instructions=el("details",{class:"quick-start-details"},el("summary",{},"查看使用说明"));
        instructions.append(
            el("ol", {class:"quick-start-steps"}, [
                el("li", {}, "找方向、读论文：创建项目或探索选题，在文献库与研究现状核对来源。"),
                el("li", {}, "明确问题、形成方案：确认研究问题、数据、设备和时间，拟定对照、指标与验证思路。"),
                el("li", {}, "审阅、修改与导出：核对问题与方案的关联及来源，本轮到研究方案为止。"),
                el("li", {}, "历史材料仍可查看和导出；暂不开放实验执行、结果回填与后续写作。"),
            ]),
            el("p",{class:"muted small"},"中断后，在「新建与管理」选择项目或粘贴编号，点击“打开并继续”。任务结束不等于创新成立。"),
        );
        return el("section", {class:"card quick-start-guide", "aria-label":"科研工作台使用说明"}, [
            el("div",{class:"quick-start-heading"},[
                el("h3", {class:"card-title"}, "科研工作台 · 从兴趣到有依据的研究方案"),
                el("button", {class:"btn btn-secondary btn-sm", onclick:()=>setActivePage("create")}, "新建 / 继续项目"),
            ]),
            el("p", {class:"muted small"}, "明确目标与条件 → 了解研究现状 → 确认研究问题 → 形成并审阅方案。在科研对话主页讨论，关键选择由你确认；方案不代表结论已验证。"),
            instructions,
        ]);
    }

    function renderOverviewLinks(data,records=null) {
        const counts=data.counts || {}, report=data.research_report_summary || {};
        const links=[
            ["文献库",`${counts.papers || 0} 篇论文`,"papers"],
            ["研究现状",`${report.gaps_count || 0} 个候选问题`,"research-report"],
            ["研究问题",records ? `${records.question || 0} 个问题 · ${counts.ideas || 0} 条思路` : `${counts.ideas || 0} 条思路`,"gaps"],
            ["方案与实验",records ? `${records.plan || 0} 份方案 · ${records.result || 0} 条结果` : `${counts.experiments || 0} 条实验任务`,"experiments"],
            ["成果写作",records ? `${records.outline || 0} 份提纲 · ${counts.writing_artifacts || 0} 份稿件 / 审阅` : `${counts.writing_artifacts || 0} 份稿件 / 审阅记录`,"writing"],
        ];
        return el("nav",{class:"overview-shortcuts","aria-label":"项目材料快捷入口"},links.map(([title,detail,page])=>
            el("button",{class:"overview-shortcut",onclick:()=>setActivePage(page)},[
                el("strong",{},title),el("span",{class:"muted small"},detail),el("span",{class:"shortcut-arrow","aria-hidden":"true"},"↗"),
            ])));
    }

    function renderContinuationCard(data) {
        const next=data.continuation;
        const box=el("section",{class:"card continuation-card","aria-label":"当前任务与下一步"},[
            el("h3",{class:"card-title"},"当前任务与下一步"),
            el("p",{}, next?.hint || data.summary || "可以查看已有材料，或继续上次任务。"),
            el("p",{class:"muted small"},`后台研究主题：${data.topic || "待确认"}；记录主线的选择不会自动替换该主题。`),
            el("button",{class:"btn btn-success",onclick:()=>startPipeline()},next?.label || "继续当前任务"),
        ]);
        return box;
    }

    function renderResearchChain(data) {
        const counts = data.counts || {};
        const stages = data.stage_statuses || {};
        const currentStage = data.current_stage || "";

        // 研究链路各节点
        const chainSteps = [
            {
                id: "research",
                icon: "01",
                name: "研究现状",
                desc: "检索·筛选·知识抽取",
                page: "research-report",
                outputs: [
                    { label: "论文", value: counts.papers || 0 },
                    { label: "待核实问题", value: (data.research_report_summary?.gaps_count) || 0 },
                ],
                status: stages.research || "",
            },
            {
                id: "ideation",
                icon: "02",
                name: "研究问题",
                desc: "聚焦问题·讨论候选思路",
                page: "gaps",
                outputs: [
                    { label: "思路", value: counts.ideas || 0 },
                ],
                status: stages.ideation || "",
            },
            {
                id: "design",
                icon: "03",
                name: "方法设计",
                desc: "方案·方法文档·验证条件",
                page: "experiments",
                outputs: [
                    { label: "论断", value: counts.claims || 0 },
                    { label: "公式", value: (data.method_alignment_summary?.total_formulas) || 0 },
                ],
                status: stages.design || "",
            },
            {
                id: "experiment",
                icon: "04",
                name: "实验规划",
                desc: "任务写清·用户照做·回填结果",
                page: "experiments",
                outputs: [
                    { label: "实验", value: counts.experiments || 0 },
                ],
                status: stages.experiment || "",
            },
            {
                id: "writing",
                icon: "05",
                name: "成果写作",
                desc: "大纲·初稿·审稿·修订",
                page: "writing",
                outputs: [
                    { label: "产出", value: (data.writing_summary?.artifact_count) || 0 },
                ],
                status: stages.writing || "",
            },
        ];

        const card = el("div", { class: "card" }, [
            el("div", { class: "card-title" }, "研究路径 · 文献 → 问题 → 方案 → 实验 → 写作"),
            el("p", { class: "muted small mb-12" },
                "点击查看对应材料；需要补证据时可以回到前一步。下方状态反映原有后台任务，不代表研究结论已经成立。"),
        ]);

        const chainEl = el("div", { class: "research-chain" });
        chainSteps.forEach((step, idx) => {
            const isCurrent = currentStage === step.id;
            const isDone = step.status === "completed" || step.status === "success";
            const isRunning = step.status === "running" || (isCurrent && data.status === "running");
            const isFailed = step.status === "failed";

            const statusIcon = isDone ? "✓" : isRunning ? "●" : isFailed ? "✗" : "○";
            const statusClass = isDone ? "done" : isRunning ? "running" : isFailed ? "failed" : "pending";

            const stepEl = el("div", {
                class: `chain-step ${statusClass} ${isCurrent ? "current" : ""}`,
                onclick: () => setActivePage(step.page),
            });

            stepEl.appendChild(el("div", { class: "chain-step-header" }, [
                el("div", { class: `chain-step-icon ${statusClass}`, text: step.icon }),
                el("div", { class: "chain-step-status", text: statusIcon }),
            ]));

            stepEl.appendChild(el("div", { class: "chain-step-name", text: step.name }));
            stepEl.appendChild(el("div", { class: "chain-step-desc small muted", text: step.desc }));

            // 产出计数
            const outputsEl = el("div", { class: "chain-step-outputs" });
            step.outputs.forEach(o => {
                outputsEl.appendChild(el("div", { class: "chain-output-item" }, [
                    el("span", { class: "chain-output-value", text: String(o.value) }),
                    el("span", { class: "chain-output-label", text: o.label }),
                ]));
            });
            stepEl.appendChild(outputsEl);

            // 状态标签
            stepEl.appendChild(el("div", { class: `chain-step-badge badge-${statusClass}` },
                isDone ? "已完成" : isRunning ? "进行中" : isFailed ? "失败" : "未开始"));

            chainEl.appendChild(stepEl);

            // 箭头连接（除最后一个）
            if (idx < chainSteps.length - 1) {
                chainEl.appendChild(el("div", { class: `chain-arrow ${statusClass}` }, "→"));
            }
        });

        card.appendChild(chainEl);
        return card;
    }

    function renderDashboardCounts(counts) {
        const wrap = el("div", { class: "counts-grid" });
        const items = [
            { label: "论文", value: counts.papers, extra: "research 阶段产出", highlight: true },
            { label: "文献疑问", value: counts.gaps, extra: "文献中的待核实线索", highlight: true },
            { label: "思路", value: counts.ideas, extra: "ideation 阶段产出" },
            { label: "论断", value: counts.claims, extra: "原流程保存的论断" },
            { label: "实验", value: counts.experiments, extra: "experiment 阶段产出" },
        ];
        items.forEach(it => {
            wrap.appendChild(el("div", { class: `count-card ${it.highlight ? "highlight" : ""}` }, [
                el("div", { class: "count-label", text: it.label }),
                el("div", { class: "count-value", text: String(it.value != null ? it.value : 0) }),
                el("div", { class: "count-extra", text: it.extra }),
            ]));
        });
        return wrap;
    }

    function renderDashboardSummaries(data) {
        const wrap = el("div", { class: "summary-grid" });

        // 调研报告摘要
        const rr = data.research_report_summary || {};
        wrap.appendChild(el("div", { class: "card summary-card" }, [
            el("div", { class: "card-title" }, "研究现状"),
            el("div", { class: "summary-stats" }, [
                el("div", { class: "summary-stat" }, [
                    el("div", { class: "summary-stat-value", text: String(rr.gaps_count || 0) }),
                    el("div", { class: "summary-stat-label" }, "候选问题"),
                ]),
                el("div", { class: "summary-stat" }, [
                    el("div", { class: "summary-stat-value", text: String(rr.consensus_count || 0) }),
                    el("div", { class: "summary-stat-label" }, "共识"),
                ]),
                el("div", { class: "summary-stat" }, [
                    el("div", { class: "summary-stat-value", text: String(rr.conflicts_count || 0) }),
                    el("div", { class: "summary-stat-label" }, "冲突"),
                ]),
            ]),
            el("div", { class: "mt-8" },
                `整体置信度：${(rr.overall_confidence || 0).toFixed(2)}`),
            el("div", { class: "btn-row mt-8" }, [
                el("button", {
                    class: "btn btn-secondary btn-sm",
                    onclick: () => setActivePage("research-report"),
                }, "查看研究现状 →"),
            ]),
        ]));

        // 研究缺口摘要
        const counts = data.counts || {};
        wrap.appendChild(el("div", { class: "card summary-card" }, [
            el("div", { class: "card-title" }, "研究问题"),
            el("div", { class: "summary-stats" }, [
                el("div", { class: "summary-stat" }, [
                    el("div", { class: "summary-stat-value", text: String(rr.gaps_count || counts.gaps || 0) }),
                    el("div", { class: "summary-stat-label" }, "文献疑问"),
                ]),
                el("div", { class: "summary-stat" }, [
                    el("div", { class: "summary-stat-value", text: String(rr.conflicts_count || 0) }),
                    el("div", { class: "summary-stat-label" }, "冲突"),
                ]),
                el("div", { class: "summary-stat" }, [
                    el("div", { class: "summary-stat-value", text: String(rr.consensus_count || 0) }),
                    el("div", { class: "summary-stat-label" }, "共识"),
                ]),
            ]),
            el("div", { class: "btn-row mt-8" }, [
                el("button", {
                    class: "btn btn-secondary btn-sm",
                    onclick: () => setActivePage("gaps"),
                }, "进入研究问题 →"),
            ]),
        ]));

        // Claim 与实验摘要
        wrap.appendChild(el("div", { class: "card summary-card" }, [
            el("div", { class: "card-title" }, "论断与实验关联"),
            el("div", { class: "summary-stats" }, [
                el("div", { class: "summary-stat" }, [
                    el("div", { class: "summary-stat-value", text: String(counts.claims || 0) }),
                    el("div", { class: "summary-stat-label" }, "论断"),
                ]),
                el("div", { class: "summary-stat" }, [
                    el("div", { class: "summary-stat-value", text: String(counts.experiments || 0) }),
                    el("div", { class: "summary-stat-label" }, "实验"),
                ]),
                el("div", { class: "summary-stat" }, [
                    el("div", { class: "summary-stat-value", text: String(counts.papers || 0) }),
                    el("div", { class: "summary-stat-label" }, "论文"),
                ]),
            ]),
            el("div", { class: "btn-row mt-8" }, [
                el("button", {
                    class: "btn btn-secondary btn-sm",
                    onclick: () => setActivePage("claims"),
                }, "查看论断与证据 →"),
            ]),
        ]));

        return wrap;
    }

    // ===== 数据源合规性卡片 =====
    function renderDashboardDataSources(container) {
        const card = el("div", { class: "card" }, [
            el("div", { class: "card-title" }, [
                "数据源合规性",
            ]),
            el("div", { class: "card-sub muted" }, [
                "系统使用的外部数据源 / API 与许可证，集中登记见 ",
                el("a", {
                    href: "/api/data-sources",
                    target: "_blank",
                    class: "link",
                }, "/api/data-sources"),
                " 与源码 ",
                el("code", {}, "core/tools/data_provenance.py"),
                "。",
            ]),
            el("div", { id: "data-sources-summary" }, [el("div", { class: "muted small" }, "加载中...")]),
            el("div", { id: "data-sources-list" }, []),
        ]);
        container.appendChild(card);
        // 异步拉取 + 渲染（失败不阻塞 dashboard）
        fetch(SRA_API_BASE + "/api/data-sources")
            .then(r => r.ok ? r.json() : Promise.reject(new Error("HTTP " + r.status)))
            .then(payload => {
                const list = (payload.sources || []);
                const sum = payload.summary || {};
                // 顶部统计
                const summary = document.getElementById("data-sources-summary");
                if (summary) {
                    clear(summary);
                    summary.appendChild(el("div", { class: "summary-stats" }, [
                        el("div", { class: "summary-stat" }, [
                            el("div", { class: "summary-stat-value", text: String(sum.total || 0) }),
                            el("div", { class: "summary-stat-label" }, "数据源"),
                        ]),
                        el("div", { class: "summary-stat" }, [
                            el("div", { class: "summary-stat-value", text: String(sum.required_count || 0) }),
                            el("div", { class: "summary-stat-label" }, "必需"),
                        ]),
                        el("div", { class: "summary-stat" }, [
                            el("div", { class: "summary-stat-value", text: String(sum.token_required_count || 0) }),
                            el("div", { class: "summary-stat-label" }, "需 Token"),
                        ]),
                    ]));
                }
                // 列表
                const listEl = document.getElementById("data-sources-list");
                if (!listEl) return;
                clear(listEl);
                const table = el("table", { class: "data-sources-table" }, [
                    el("thead", {}, el("tr", {}, [
                        el("th", {}, "名称"),
                        el("th", {}, "类别"),
                        el("th", {}, "许可证"),
                        el("th", {}, "接入"),
                        el("th", {}, "必选"),
                    ])),
                ]);
                const tbody = el("tbody");
                list.forEach(src => {
                    tbody.appendChild(el("tr", {}, [
                        el("td", {}, [
                            el("strong", {}, src.name),
                            el("div", { class: "small muted" }, src.usage || ""),
                        ]),
                        el("td", {}, src.category || ""),
                        el("td", {}, src.license || ""),
                        el("td", {}, src.access || ""),
                        el("td", {}, src.required ? "必选" : "可选"),
                    ]));
                });
                table.appendChild(tbody);
                listEl.appendChild(table);
            })
            .catch(e => {
                const summary = document.getElementById("data-sources-summary");
                if (summary) {
                    clear(summary);
                    summary.appendChild(el("div", { class: "error small" },
                        "数据源列表加载失败：" + (e.message || e)));
                }
            });
    }

    function renderDashboardActions(data) {
        const card = el("div", { class: "card" }, [
            el("div", { class: "card-title" }, "快速操作"),
            el("div", { class: "btn-row" }, [
                el("button", {
                    class: "btn btn-success",
                    onclick: () => startPipeline(),
                }, data.continuation?.label || (data.status === "created" ? "启动研究任务" : "继续当前任务")),
                el("button", {
                    class: "btn btn-secondary",
                    onclick: () => setActivePage("progress"),
                }, "查看节点历史"),
                el("button", {
                    class: "btn btn-secondary",
                    onclick: () => setActivePage("research-report"),
                }, "研究现状"),
            ]),
        ]);
        // 下载区
        card.appendChild(el("div", { class: "download-divider" }));
        card.appendChild(renderDownloadBar([
            { type: "full-report", label: "全流程报告", filename: "full_report.md" },
            { type: "research-report", label: "研究现状汇总", filename: "research_report.md" },
            { type: "ideas-summary", label: "思路汇总", filename: "ideas_summary.md" },
            { type: "method-doc", label: "方法文档", filename: "method_doc.md" },
            { type: "experiment-tasks", label: "实验任务书", filename: "experiment_tasks.md" },
            { type: "experiment-results", label: "实验结果", filename: "experiment_results.md" },
            { type: "claims-summary", label: "论断与证据汇总", filename: "claims_summary.md" },
            { type: "discovery-report", label: "发现报告", filename: "discovery_report.md" },
            { type: "paper-draft", label: "论文稿", filename: "paper_draft.md" },
        ]));
        return card;
    }

    // 方法文档卡片：查看 / 重新生成（修复历史空文档 + LLM 空返回兜底）
    function buildMethodDocCard() {
        const pid = state.currentProjectId;
        const card = el("div", { class: "card" });
        card.appendChild(el("div", { class: "card-title" }, "🔬 方法文档"));
        card.appendChild(el("p", { class: "muted small" },
            "保留原流程的方法设计文档：问题定义、方法概述、必要公式与实验建议。" +
            "若旧文档为空，可手动重新生成并保存新版本；这会调用模型，生成方案不表示已经完成实验。"));
        const pre = el("div", {
            class: "research-document",
            style: "display:none; max-height:620px; overflow:auto;",
        });
        const msg = el("div", { class: "small muted mt-8" });
        const row = el("div", { class: "btn-row mt-8" });
        const viewBtn = el("button", { class: "btn btn-secondary" }, "查看方法文档");
        const regenBtn = el("button", { class: "btn" }, "重新生成方法文档");
        viewBtn.addEventListener("click", async () => {
            if (pre.textContent && pre.style.display !== "none") {
                pre.style.display = "none";
                return;
            }
            viewBtn.disabled = true;
            try {
                const resp = await fetch(SRA_API_BASE +
                    `/api/projects/${pid}/download/method-doc`);
                if (!resp.ok) throw new Error("方法文档暂时无法读取");
                await window.SRA_DOCUMENT.render(pre, await resp.text());
                pre.style.display = "block";
            } catch (e) {
                showToast(`读取失败：${e.message}`, "error");
            } finally {
                viewBtn.disabled = false;
            }
        });
        regenBtn.addEventListener("click", async () => {
            regenBtn.disabled = true;
            msg.textContent = "正在提交生成任务；完成时间取决于模型服务，可浏览其他页面后回来查询。";
            try {
                await api("POST",
                    `/api/projects/${pid}/regenerate-method-doc`, {});
                // 轮询后台任务状态
                const poll = setInterval(async () => {
                    if (!card.isConnected || state.currentProjectId !== pid) { clearInterval(poll); return; }
                    try {
                        const st = await api("GET",
                            `/api/projects/${pid}/method-doc-status`);
                        if (st.status === "running") return; // 继续等
                        clearInterval(poll);
                        regenBtn.disabled = false;
                        if (st.status === "done") {
                            await window.SRA_DOCUMENT.render(pre, st.content || "");
                            pre.style.display = "block";
                            msg.textContent = st.message || "已重新生成";
                            showToast(st.message || "方法文档已重新生成", "success");
                        } else {
                            msg.textContent = `重新生成失败：${st.error || "未知错误"}`;
                            showToast(`重新生成失败：${st.error || "未知错误"}`, "error");
                        }
                    } catch (e) { /* 轮询失败继续等 */ }
                }, 5000);
            } catch (e) {
                regenBtn.disabled = false;
                msg.textContent = `启动失败：${e.message}`;
                showToast(`启动失败：${e.message}`, "error");
            }
        });
        row.appendChild(viewBtn);
        row.appendChild(regenBtn);
        const sourceBtn = el("button", { class: "btn btn-secondary" }, "查看原文");
        sourceBtn.onclick = () => { sourceBtn.textContent = window.SRA_DOCUMENT.toggleSource(pre) ? "返回公式预览" : "查看原文"; };
        const htmlBtn = el("button", { class: "btn btn-secondary" }, "下载公式版 HTML");
        const printBtn = el("button", { class: "btn btn-secondary" }, "公式版打印 / PDF");
        htmlBtn.onclick = () => window.SRA_DOCUMENT.download(pre,"方法文档").catch(e=>showToast(e.message,"error"));
        printBtn.onclick = () => window.SRA_DOCUMENT.print(pre,"方法文档").catch(e=>showToast(e.message,"error"));
        row.append(sourceBtn,htmlBtn,printBtn);
        card.appendChild(el("p", { class: "muted small" }, "查看后可下载离线公式版 HTML，或通过打印保存公式版 PDF。Markdown 原文保留；原有 Word/PDF 下载仍是简化文本版。"));
        card.appendChild(row);
        card.appendChild(pre);
        card.appendChild(msg);
        return card;
    }

    // ===== 1. 项目创建页 =====

    // 理工科领域与研究方法分开；与服务端公开范围保持一致。
    const RESEARCH_DOMAINS = [
        { value: "computer_science", label: "软件工程与计算机系统", hint: "软件测试、程序分析、数据库、系统可靠性与性能评估" },
        { value: "ai", label: "AI 与机器学习", hint: "模型与算法、RAG、科研 Agent、公开数据上的复现与评测" },
        { value: "security", label: "计算机安全与隐私", hint: "威胁模型、防护效果与代价；仅限公开数据、模拟环境或明确授权的测试" },
        { value: "engineering", label: "工程与技术", hint: "计算、理论或实验设计；具体方法需确认" },
        { value: "materials", label: "材料科学", hint: "材料问题与证据；仅规划，不提供湿实验或操作指导" },
        { value: "physics", label: "物理学", hint: "理论、计算或实验设计不能仅按学科决定" },
        { value: "chemistry", label: "化学", hint: "研究问题、理论或计算与测量规划；不提供危险操作" },
        { value: "energy", label: "能源", hint: "能源系统、模型与方案可行性" },
        { value: "mathematics", label: "数学", hint: "定义、假设、证明路线和反例" },
    ];
    const RESEARCH_SCOPE_NOTICE = "当前开放九个理工领域，支持计算研究、理论研究与实验研究设计（仅规划）。流程止于研究方案，不执行实验或生成结果，不保证创新成立。暂不支持医学、临床、人体、药物、湿实验及具体危险操作指导。历史项目与材料保留。";

    function domainLabel(value) {
        const hit = RESEARCH_DOMAINS.find(d => d.value === value);
        const legacy = {materials:"材料科学",chemistry:"化学与化工",biology:"生命科学",medicine:"医学与药学",physics:"物理学",energy:"能源与环境"};
        return hit ? hit.label : value ? `${legacy[value] || value}（历史领域）` : "未指定（历史项目）";
    }

    function renderCreate(content) {
        if(window.SRA_CONVERSATIONS) {
            content.append(el("h2",{class:"page-title"},"我的项目"),el("p",{class:"muted"},"选择已有项目继续对话，或管理项目的可恢复归档。"),el("button",{class:"btn",onclick:()=>window.__sraApp.navigate({projectId:null,conversationId:null,page:"dashboard"})},"开始新研究"));
            renderProjectList(content);
            return;
        }
        content.appendChild(el("div",{class:"page-header"},[
            el("h2", { class: "page-title" }, "新建与管理"),
            el("p", {class:"page-desc"},"开始一项研究，或回到已有项目。创建项目不会自动执行实验。"),
        ]));
        const setup=el("div",{class:"project-setup-grid"});
        const card = el("section", { class: "card project-create-card" }, [
            el("h3", {class:"card-title"},"开始新研究"),
        ]);
        const resumeCard=el("section",{class:"card project-resume-card"},[
            el("h3",{class:"card-title"},"接着上次的工作"),
            el("p",{class:"muted small"},"项目进度会保存在本机。直接在下方选择项目，或粘贴编号继续；无需重新创建。"),
        ]);

        // 历史领域不自动加入选项，也不改写旧项目。
        const domainSelect = el("select", { class: "input", id: "domain-select" });
        RESEARCH_DOMAINS.forEach(d => {
            domainSelect.appendChild(el("option", { value: d.value, text: d.label }));
        });
        domainSelect.value = RESEARCH_DOMAINS.some(d => d.value === state.domain) ? state.domain : "computer_science";

        const domainHint = el("p", { class: "muted small mb-0", id: "domain-hint" },
            (RESEARCH_DOMAINS.find(d => d.value === domainSelect.value) || {}).hint || "");

        domainSelect.addEventListener("change", () => {
            const cur = RESEARCH_DOMAINS.find(d => d.value === domainSelect.value);
            domainHint.textContent = cur ? cur.hint : "";
        });

        card.appendChild(el("div", { class: "field" }, [
            el("label", { class: "field-label", for: "domain-select" }, "研究领域"),
            domainSelect,
            domainHint,
        ]));

        const collectDomain = () => domainSelect.value;

        const topicField = el("div", { class: "field" }, [
            el("label", { class: "field-label", for: "topic-input" }, "研究主题"),
            el("textarea", {
                class: "textarea",
                id: "topic-input",
                placeholder: "例如：联邦学习投毒防御、RAG 引用准确性、软件缺陷检测或系统性能评估",
            }),
        ]);
        card.appendChild(topicField);

        const btnRow = el("div", { class: "btn-row" }, [
            el("button", { class: "btn", id: "create-btn" }, "创建项目"),
            el("button", { class: "btn btn-outline", id: "topic-discovery-create-btn" }, "探索选题"),
        ]);
        card.appendChild(btnRow);

        // 继续已有项目（配合服务端启动恢复机制：重启后旧项目自动恢复）
        const resumeRow = el("div", { class: "field mt-16" }, [
            el("label", { class: "field-label", for: "resume-project-input" }, "继续已有项目"),
            el("div", { class: "btn-row" }, [
                el("input", {
                    class: "input",
                    id: "resume-project-input",
                    placeholder: "粘贴项目编号，如 proj_…",
                    style: "flex:1;min-width:0;",
                }),
                el("button", { class: "btn btn-outline", id: "resume-project-btn" }, "打开并继续"),
            ]),
        ]);
        resumeCard.appendChild(resumeRow);

        // 会话重置：刷新项目 → 回到无项目状态（不清磁盘数据，仅清前端记忆）
        const sessionRow = el("details", { class: "session-options" }, [
            el("summary", {}, "使用范围与会话设置"),
            el("p", { class: "muted small", id: "research-scope-notice" }, RESEARCH_SCOPE_NOTICE),
            el("p", { class: "muted small mb-8" },
                "刷新项目 = 清除页面上的项目记忆（localStorage），回到未创建项目的初始状态。磁盘上的项目数据保留，仍可用上方 Project ID 继续访问。"),
            el("div", { class: "btn-row" }, [
                el("button", { class: "btn btn-outline btn-sm", id: "reset-session-btn" }, "退出当前项目"),
            ]),
        ]);
        resumeCard.appendChild(sessionRow);

        const resultArea = el("div", { id: "create-result" });
        resumeCard.appendChild(resultArea);
        setup.append(card,resumeCard);
        content.appendChild(setup);

        // 绑定「继续已有项目」
        document.getElementById("resume-project-btn").addEventListener("click", async () => {
            const pid = document.getElementById("resume-project-input").value.trim();
            if (!pid) {
                showToast("请输入项目 ID", "error");
                return;
            }
            const btn = document.getElementById("resume-project-btn");
            btn.disabled = true;
            btn.textContent = "恢复中…";
            try {
                const st = await api("GET", `/api/projects/${pid}/status`);
                activateProject(st);
                await startPipeline();
            } catch (e) {
                showToast("项目打开失败：" + e.message, "error");
            } finally {
                btn.disabled = false;
                btn.textContent = "打开并继续";
            }
        });

        // 绑定「刷新项目」：清除前端项目记忆，回到无项目状态（磁盘数据保留）
        document.getElementById("reset-session-btn").addEventListener("click", async () => {
            const pid = state.currentProjectId;
            if (!pid && !getProjectFromStorage()) {
                showToast("当前已是无项目状态", "info");
                return;
            }
            if (!confirm("确认刷新项目？\n\n将清除页面上的项目记忆并回到未创建项目状态。磁盘上的项目数据会保留，仍可通过 Project ID 继续访问。")) {
                return;
            }
            const btn = document.getElementById("reset-session-btn");
            btn.disabled = true;
            btn.textContent = "刷新中…";
            try {
                // 清空前端状态 + localStorage + 停止轮询
                state.currentProjectId = null;
                state.statusCache = null;
                state.statusCachePrevious = null;
                state.dashboardCache = null;
                state.researchReportCache = null;
                state.papersCache = null;
                state.papersFilter = { q: "", yearMin: "", yearMax: "", venue: "", sort: "newest", casZone: "", ifMin: "", pdfOnly: false };
                state.searchPrefs = null;
                state.searchPrefsForm = null;
                state.runMode = "";
                clearProjectFromStorage();
                stopPolling();
                updateProjectIdDisplay();
                renderSidebarNotes();
                renderSidebarDownload();
                setActivePage("create");
                showToast("已刷新，回到无项目状态", "success");
            } catch (e) {
                showToast("刷新失败：" + e.message, "error");
            } finally {
                btn.disabled = false;
                btn.textContent = "退出当前项目";
            }
        });

        // 绑定按钮
        document.getElementById("create-btn").addEventListener("click", async () => {
            const topic = document.getElementById("topic-input").value.trim();
            if (!topic) {
                showToast("请输入研究主题", "error");
                return;
            }
            const btn = document.getElementById("create-btn");
            btn.disabled = true;
            btn.textContent = "创建中…";
            showLoadingOverlay("正在创建项目…");
            try {
                const data = await api("POST", "/api/projects", { topic, domain: collectDomain() });
                activateProject(data);
                startPolling();
                renderSidebarNotes();
                renderSidebarDownload();
                renderCreateResult(resultArea, data);
                showToast("项目已创建", "success");
            } catch (e) {
                showToast("创建失败：" + e.message, "error");
            } finally {
                btn.disabled = false;
                btn.textContent = "创建项目";
                hideLoadingOverlay();
            }
        });

        // 方向推荐按钮：创建项目后启动方向推荐
        document.getElementById("topic-discovery-create-btn").addEventListener("click", async () => {
            const topic = document.getElementById("topic-input").value.trim();
            if (!topic) {
                showToast("请输入研究兴趣", "error");
                return;
            }
            const btn = document.getElementById("topic-discovery-create-btn");
            btn.disabled = true;
            btn.textContent = "创建中…";
            try {
                // 创建项目（topic 字段暂存研究兴趣）
                const data = await api("POST", "/api/projects", { topic, domain: collectDomain() });
                activateProject(data);
                startPolling();
                renderSidebarNotes();
                // 启动方向推荐
                await api("POST", `/api/projects/${data.project_id}/run-topic-discovery`, { interest: topic, domain: state.domain });
                state.runMode = "topic_discovery";
                setActivePage("topic-discovery");
                showToast("选题探索已启动", "success");
            } catch (e) {
                showToast("启动失败：" + e.message, "error");
            } finally {
                btn.disabled = false;
                btn.textContent = "探索选题";
            }
        });

        // 若已有项目，显示当前项目并允许切换
        if (state.currentProjectId) {
            renderCreateResult(resultArea, { project_id: state.currentProjectId, topic: state.statusCache?.topic || "" });
        }
        // 项目列表（刷新后可恢复/切换）
        renderProjectList(content);
    }

    async function renderProjectList(content) {
        if(window.SRA_CONVERSATIONS && window.SRA_PROJECT_LIBRARY){window.SRA_PROJECT_LIBRARY.mount(content,window.__sraApp);return;}
        const card=el("section",{class:"card project-library","aria-label":"已有项目"},el("p",{class:"muted small"},"正在读取项目…"));
        content.appendChild(card);
        try {
            const data = await api("GET", "/api/projects");
            // 静态资源可先于服务升级；不可向旧的物理删除接口降级。
            let batchSupported=false;
            try {const schema=await api("GET","/openapi.json");batchSupported=!!schema.paths?.["/api/projects/batch-delete"]?.post;} catch(_) {}
            if(!card.isConnected || state.currentPage!=="create") return;
            let projects=data.projects || [], page=1, deleting=false;
            const selected=new Set(), pageSize=8;
            const heading=el("h3",{class:"card-title"},"已有项目");
            const search=el("input",{class:"input project-search",type:"search",placeholder:"搜索主题或项目编号","aria-label":"搜索已有项目"});
            const deleteButton=el("button",{class:"btn btn-danger btn-outline btn-sm",id:"delete-selected-projects",disabled:true},"删除所选");
            const count=el("span",{class:"muted small",role:"status"});
            const selectAll=el("input",{type:"checkbox","aria-label":"全选当前页"});
            const list=el("div",{class:"project-library-list"});
            const previous=el("button",{class:"btn btn-secondary btn-sm",onclick:()=>{page--;paint();}},"上一页");
            const next=el("button",{class:"btn btn-secondary btn-sm",onclick:()=>{page++;paint();}},"下一页");
            const pageLabel=el("span",{class:"muted small"});
            const clearSelection=el("button",{class:"btn btn-secondary btn-sm",onclick:()=>{selected.clear();paint();}},"清空选择");
            const deletable=p=>batchSupported && !['running','pending_human'].includes(p.status);
            const filtered=()=>projects.filter(p=>`${p.topic || ''} ${p.project_id}`.toLowerCase().includes(search.value.trim().toLowerCase()));
            const visible=()=>filtered().slice((page-1)*pageSize,page*pageSize);
            function paint() {
                const matches=filtered(), totalPages=Math.max(1,Math.ceil(matches.length/pageSize));
                page=Math.max(1,Math.min(page,totalPages));
                const candidates=visible().filter(deletable);
                selectAll.checked=!!candidates.length && candidates.every(p=>selected.has(p.project_id));
                selectAll.indeterminate=!selectAll.checked && candidates.some(p=>selected.has(p.project_id));
                selectAll.disabled=deleting || !candidates.length;
                count.textContent=`共 ${projects.length} 个 · 匹配 ${matches.length} 个 · 已选 ${selected.size} 个（含其他页）`;
                deleteButton.disabled=deleting || !batchSupported || !selected.size;
                deleteButton.textContent=!batchSupported ? "重启服务后可批量删除" : deleting ? "正在移入归档…" : `删除所选${selected.size ? `（${selected.size}）` : ''}`;
                clearSelection.disabled=deleting || !selected.size;
                search.disabled=deleting;
                previous.disabled=deleting || page===1;next.disabled=deleting || page===totalPages;
                pageLabel.textContent=`${page} / ${totalPages} 页`;
                list.replaceChildren();
                if(!matches.length) list.appendChild(el("p",{class:"muted project-list-empty"},projects.length ? "没有匹配的项目，试试其他关键词。" : "还没有项目，从上方创建你的第一项研究。"));
                for(const p of visible()) {
                    const checkbox=el("input",{type:"checkbox","aria-label":`选择项目 ${p.project_id}`,disabled:deleting || !deletable(p)});
                    checkbox.checked=selected.has(p.project_id);
                    checkbox.addEventListener("change",()=>{if(checkbox.checked) selected.add(p.project_id);else selected.delete(p.project_id);paint();});
                    const open=async resume=>{
                        if(window.SRA_CONVERSATIONS) {window.__sraApp.navigate({projectId:p.project_id,conversationId:null,page:resume ? "progress" : "dashboard"});return;}
                        try {activateProject(await api("GET",`/api/projects/${p.project_id}/status`));if(resume) await startPipeline();else setActivePage("dashboard");}
                        catch(e){showToast("打开失败："+e.message,"error");}
                    };
                    const row=el("div",{class:`project-library-row${p.project_id===state.currentProjectId ? ' current' : ''}`,"data-project-id":p.project_id},[
                        checkbox,
                        el("div",{class:"project-row-info"},[
                            el("button",{class:"project-title-link",disabled:deleting,onclick:()=>open(false)},p.display_name || p.topic || "未命名研究"),
                            el("div",{class:"muted small project-row-meta"},`${p.project_id} · ${formatTime(p.created_at)}`),
                        ]),
                        el("div",{class:"project-row-actions"},[
                            el("span",{class:"muted small"},({created:"未开始",running:"运行中",pending_human:"待确认",completed:"已结束",stopped:"已暂停",failed:"需重试",cancelled:"已取消"})[p.status] || p.status),
                            p.project_id===state.currentProjectId ? el("span",{class:"badge badge-success"},"当前") : null,
                            el("button",{class:"btn btn-secondary btn-sm",disabled:deleting,onclick:()=>open(true)},"任务活动"),
                        ]),
                    ]);
                    list.appendChild(row);
                }
            }
            search.addEventListener("input",()=>{page=1;paint();});
            selectAll.addEventListener("change",()=>{for(const p of visible().filter(deletable)) {if(selectAll.checked) selected.add(p.project_id);else selected.delete(p.project_id);}paint();});
            deleteButton.addEventListener("click",async()=>{
                const ids=[...selected];
                if(!ids.length || deleting) return;
                if(ids.length>200) {showToast("每批最多删除 200 个项目，请减少选择。","warning");return;}
                const names=projects.filter(p=>selected.has(p.project_id)).map(p=>`${p.topic || '未命名研究'} (${p.project_id})`);
                if(!confirm(`确认删除选中的 ${ids.length} 个项目？\n${names.slice(0,8).join('\n')}${names.length>8 ? '\n…' : ''}\n\n将从列表移除并移入本机可恢复归档，不会直接抹除文件。运行中或待确认的项目不能删除。`)) return;
                deleting=true;paint();
                try {
                    const response=await api("POST","/api/projects/batch-delete",{project_ids:ids});
                    projects=projects.filter(p=>!ids.includes(p.project_id));selected.clear();
                    window.dispatchEvent(new CustomEvent("sra:projects-changed"));
                    if(ids.includes(state.currentProjectId)) {
                        state.currentProjectId=null;state.currentConversationId=null;state.statusCache=null;state.statusCachePrevious=null;
                        state.dashboardCache=null;state.researchReportCache=null;state.papersCache=null;
                        state.runMode="";state.searchPrefs=null;state.searchPrefsForm=null;
                        clearProjectFromStorage();stopPolling();updateProjectIdDisplay();renderSidebarNotes();renderSidebarDownload();
                        const result=document.getElementById("create-result");if(result) clear(result);
                        const url=new URL(location.href);url.searchParams.delete("project");url.searchParams.delete("conversation");history.replaceState(null,"",url);
                    }
                    showToast(response.message || "所选项目已移入可恢复归档。","success");
                } catch(e) {showToast("删除失败："+e.message,"error");}
                finally {deleting=false;paint();}
            });
            card.replaceChildren(
                el("div",{class:"project-library-heading"},[heading,search]),
                el("div",{class:"project-selection-bar"},[el("label",{},[selectAll,"全选当前页"]),count,clearSelection,deleteButton]),
                list,
                el("div",{class:"project-library-footer"},[el("span",{class:"muted small"},batchSupported ? "仅查看不会启动任务；删除需勾选并确认。" : "当前后台尚未加载批量归档接口，请重启服务；查看与继续项目不受影响。"),el("div",{class:"btn-row"},[previous,pageLabel,next])]),
            );
            paint();
        } catch (e) {
            if(card.isConnected) card.replaceChildren(el("p",{class:"muted"},`项目列表读取失败：${e.message}`),el("button",{class:"btn btn-secondary btn-sm",onclick:()=>{card.remove();renderProjectList(content);}},"重试"));
        }
    }

    function renderUploadCard(papersOnly = false) {
        const card = el("div", { class: "card" }, [
            el("div", { class: "card-title" }, papersOnly ? "导入已有文献" : "上传文献 / 主题文件"),
            el("p", { class: "muted small mb-12" },
                papersOnly ? "支持 PDF、UTF-8 TXT/MD；只依据实际提取的片段讨论，解析失败不会冒充已阅读。" : "支持上传 PDF/TXT/MD 格式的文献（入库为 Paper 实体），或上传主题描述文件覆盖当前研究主题。"),
        ]);

        const resourceHint = el("p", {class:"muted small",role:"status"});
        card.appendChild(resourceHint);window.SRA_RESOURCES?.hint(resourceHint);
        // 上传文献
        const paperSection = el("div", { class: "upload-section" }, [
            el("div", { class: "field-label" }, "上传文献"),
        ]);
        const paperFile = el("input", { type: "file", accept: ".pdf,.txt,.md", class: "upload-input" });
        const paperTitle = el("input", { type: "text", class: "input", placeholder: "文献标题（可选，默认用文件名）" });
        const paperBtn = el("button", { class: "btn btn-secondary btn-sm", text: "上传文献" });
        paperSection.appendChild(paperFile);
        paperSection.appendChild(paperTitle);
        paperSection.appendChild(paperBtn);

        const uploadProjectId=state.currentProjectId, uploadKey="sra.upload.pending."+uploadProjectId;
        const uploadStatus=el("p",{class:"muted small",role:"status"});
        const checkUpload=el("button",{class:"btn btn-secondary btn-sm",text:"核对上次上传",type:"button"});
        const pendingUpload=()=>{try{return JSON.parse(localStorage.getItem(uploadKey));}catch(_){return null;}};
        const rememberUpload=value=>{try{if(value)localStorage.setItem(uploadKey,JSON.stringify(value));else localStorage.removeItem(uploadKey);}catch(_){}};
        checkUpload.hidden=!pendingUpload();
        if(!checkUpload.hidden)uploadStatus.textContent="上次上传尚未核对；先查询状态，不会自动重新解析。";
        checkUpload.onclick=async()=>{
            const pending=pendingUpload();if(!pending)return;
            checkUpload.disabled=true;
            try{
                const response=await fetch(`/api/projects/${uploadProjectId}/upload-requests/${encodeURIComponent(pending.request_id)}`);
                const result=await response.json();if(!response.ok)throw new Error(result.detail || "无法核对上传状态");
                if(result.status==="completed"){
                    rememberUpload(null);checkUpload.hidden=true;
                    window.dispatchEvent(new CustomEvent('sra:papers-changed',{detail:{projectId:uploadProjectId}}));
                    uploadStatus.textContent=result.parse_error || "已确认资料保存成功，没有重复创建或解析。";
                    if(state.currentProjectId===uploadProjectId && card.isConnected)renderPage();
                }else uploadStatus.textContent=result.status==="processing"?"原上传仍在处理，可稍后核对；不会重复解析。":(result.error || "原处理已中断，请重新选择同一文件和标题，点击上传明确重试。");
            }catch(e){uploadStatus.textContent=e.message+"。尚未确认成功，请保留原文件和标题，不要反复开始新上传。";}
            finally{checkUpload.disabled=false;}
        };
        paperSection.appendChild(uploadStatus);paperSection.appendChild(checkUpload);

        paperBtn.addEventListener("click", async () => {
            if (!paperFile.files.length) {
                showToast("请选择文件", "error");
                return;
            }
            const fd = new FormData();
            fd.append("file", paperFile.files[0]);
            if (paperTitle.value.trim()) {
                fd.append("title", paperTitle.value.trim());
            }
            paperBtn.disabled = true;
            paperBtn.textContent = "上传中…";
            try {
                await window.SRA_RESOURCES?.checkFile(paperFile.files[0]);
                const capabilities=await window.SRA_CONVERSATIONS?.capabilities();
                if(!capabilities?.upload_request_identity){uploadStatus.textContent="当前后端尚未加载上传重试保护，请更新后端后再上传；本次未发送文件。";return;}
                if(capabilities?.upload_request_identity){
                    const selected=paperFile.files[0],digest=await crypto.subtle.digest("SHA-256",await selected.arrayBuffer());
                    const signature=JSON.stringify([selected.name,paperTitle.value.trim(),Array.from(new Uint8Array(digest),b=>b.toString(16).padStart(2,"0")).join("")]);
                    let pending=pendingUpload();
                    if(pending && pending.signature!==signature && !confirm("上次上传尚未核对，文件或标题已不同。是否明确开始一次新的上传？原请求仍保留，可在原项目核对。"))return;
                    if(!pending || pending.signature!==signature)pending={request_id:crypto.randomUUID(),signature};
                    rememberUpload(pending);fd.append("request_id",pending.request_id);checkUpload.hidden=false;
                }
                uploadStatus.textContent="正在保存或解析资料；响应未确认时请核对原上传，不要重复开始。";
                const resp = await fetch(
                    `/api/projects/${uploadProjectId}/upload-paper`,
                    { method: "POST", body: fd }
                );
                const data = await resp.json();
                if(resp.status===202){uploadStatus.textContent="原上传正在处理中，可稍后点击核对状态；不会重复解析。";return;}
                if (resp.ok) {
                    rememberUpload(null);checkUpload.hidden=true;
                    window.dispatchEvent(new CustomEvent('sra:papers-changed',{detail:{projectId:uploadProjectId}}));
                    if(state.currentProjectId===uploadProjectId && card.isConnected)showToast(data.parse_error ? data.message : `${data.already_uploaded?'已核对原上传':'文献已保存'}：${data.title}（${data.chunks} 个正文片段）`, data.parse_error ? "warning" : "success");
                    paperFile.value = "";
                    paperTitle.value = "";
                    // 刷新当前页（论文页会重新拉取列表，新建页无副作用）
                    if(state.currentProjectId===uploadProjectId && card.isConnected)renderPage();
                } else {
                    uploadStatus.textContent="上传失败：" + (data.detail || "未知错误")+"；可先核对原上传。";
                    if(state.currentProjectId===uploadProjectId && card.isConnected)showToast("上传失败：" + (data.detail || "未知错误"), "error");
                }
            } catch (e) {
                uploadStatus.textContent="上传未完成："+e.message+"。请点击核对上次上传，不会自动重发或重新解析。";
            } finally {
                paperBtn.disabled = false;
                paperBtn.textContent = "上传文献";
            }
        });
        card.appendChild(paperSection);

        // 分隔
        card.appendChild(el("div", { class: "download-divider" }));

        if (papersOnly) return card;
        // 上传主题文件
        const topicSection = el("div", { class: "upload-section" }, [
            el("div", { class: "field-label" }, "上传主题描述文件"),
        ]);
        const topicFile = el("input", { type: "file", accept: ".txt,.md", class: "upload-input" });
        const topicBtn = el("button", { class: "btn btn-secondary btn-sm", text: "上传并覆盖主题" });
        topicSection.appendChild(topicFile);
        topicSection.appendChild(topicBtn);

        topicBtn.addEventListener("click", async () => {
            if (!topicFile.files.length) {
                showToast("请选择文件", "error");
                return;
            }
            const fd = new FormData();
            fd.append("file", topicFile.files[0]);
            topicBtn.disabled = true;
            topicBtn.textContent = "上传中…";
            try {
                await window.SRA_RESOURCES?.checkFile(topicFile.files[0]);
                const resp = await fetch(
                    `/api/projects/${state.currentProjectId}/upload-topic`,
                    { method: "POST", body: fd }
                );
                const data = await resp.json();
                if (resp.ok) {
                    showToast("主题已更新", "success");
                    // 同步到 topic 输入框
                    const ti = document.getElementById("topic-input");
                    if (ti) ti.value = data.topic;
                    if (state.statusCache) state.statusCache.topic = data.topic;
                    updateProjectIdDisplay();
                } else {
                    showToast("上传失败：" + (data.detail || "未知错误"), "error");
                }
            } catch (e) {
                showToast("上传失败：" + e.message, "error");
            } finally {
                topicBtn.disabled = false;
                topicBtn.textContent = "上传并覆盖主题";
            }
        });
        card.appendChild(topicSection);

        return card;
    }

    function renderCreateResult(container, data) {
        clear(container);
        const pid = el("div", { class: "project-id-display" }, [
            el("span", { text: "Project ID：" }),
            el("strong", { text: data.project_id }),
            el("button", {
                class: "copy-btn",
                text: "复制",
                onclick: async () => {
                    try {
                        await navigator.clipboard.writeText(data.project_id);
                        showToast("已复制", "success");
                    } catch (e) {
                        showToast("复制失败", "error");
                    }
                },
            }),
        ]);
        container.appendChild(pid);

        // 主题展示（含恢复的主题）
        if (data.topic) {
            const topicBox = el("div", { class: "project-topic-display mt-12" }, [
                el("span", { class: "project-topic-label", text: "研究主题：" }),
                el("span", { class: "project-topic-value", text: data.topic }),
            ]);
            container.appendChild(topicBox);
        }

        // 研究领域展示（领域自适应：同一套流程跑任意学科）
        const dm = data.domain || state.domain;
        if (dm) {
            container.appendChild(el("div", { class: "project-topic-display mt-8" }, [
                el("span", { class: "project-topic-label", text: "研究领域：" }),
                el("span", { class: "project-topic-value", text: domainLabel(dm) }),
            ]));
        }

        const actions = el("div", { class: "btn-row mt-16" }, [
            el("button", { class: "btn btn-success", onclick: () => startPipeline() }, data.continuation?.label || "启动 / 继续研究任务"),
            el("button", { class: "btn btn-secondary", onclick: () => setActivePage("progress") }, "查看进度"),
        ]);

        container.appendChild(actions);
    }

    function activateProject(data) {
        stopPolling();
        if(state.currentProjectId!==data.project_id) state.currentConversationId=null;
        state.currentProjectId=data.project_id;
        const url=new URL(window.location.href);url.searchParams.set("project",data.project_id);window.history.replaceState(null,"",url);
        state.dashboardCache=null;state.researchReportCache=null;state.papersCache=null;
        state.pollViewFingerprint=null;state.humanFingerprint=null;state.humanDraft="";
        state.runMode=data.run_mode || "";state.domain=data.domain || "";
        updateProjectIdDisplay();
        state.statusCache=data;
        state.workflowCache=data.workflow ? {projectId:data.project_id,...data.workflow} : null;
        saveProjectToStorage(data.project_id);
        updateBadges(data);renderSidebarNotes();renderSidebarDownload();startPolling();
    }

    async function startPipeline(forceWriting) {
        const pid=state.currentProjectId;
        if (!pid || state.resumingProjectId===pid) return;
        state.resumingProjectId=pid;
        try {
            const data = forceWriting ? await api("POST", `/api/projects/${pid}/run`, {force_writing:true})
                : await api("POST", `/api/projects/${pid}/resume`);
            if(pid!==state.currentProjectId) return;
            await pollStatus();
            showToast(data.message || "已继续当前任务", "success");
            if(!forceWriting && ["record","results"].includes(data.action) && data.record_kind) {
                startResearchRecord(data.record_kind,data.related_id || null);return;
            }
            if(!forceWriting && data.action==="review_record") {
                const latest=await api("GET",`/api/projects/${pid}/workspace?include_chat=false`);
                if(pid!==state.currentProjectId) return;
                const item=latest.workspace.items.find(record=>record.item_id===data.record_id);
                if(item) {startResearchRecord(item.kind,null,item);return;}
            }
            if(!forceWriting && data.action==="plan_ready" && data.record_id) {
                const latest=await api("GET",`/api/projects/${pid}/workspace?include_chat=false`);
                if(pid!==state.currentProjectId) return;
                const item=latest.workspace.items.find(record=>record.item_id===data.record_id);
                if(item) {openResearchRecord(item);return;}
            }
            setActivePage(forceWriting ? "progress" : data.page || "progress");
        } catch (e) {
            if(pid===state.currentProjectId) showToast("继续失败：" + e.message, "error");
        } finally {if(state.resumingProjectId===pid) state.resumingProjectId=null;}
    }

    // ===== 方向推荐页面 =====

    // ===== 方向推荐：维度对比总览表（纯展示，不参与排序/选择） =====
    // 注意：此表仅按服务端已排好的顺序展示，绝不在前端重排，
    // 保证卡片数字下标 = 服务端 recommendations 下标 = 提交文本 String(i+1)。

    // 增长率格式化：模块级函数，对比表与卡片共用
    const fmtGrowth = g => (g == null ? "N/A" : `${g > 0 ? "+" : ""}${Math.round(g * 100)}%`);

    function buildRecCompareTable(recommendations, selectedTopic) {
        const dimClass = v => {
            if (!v) return "";
            const s = String(v).toLowerCase();
            if (s === "high" || s === "hard") return "dim-high";
            if (s === "medium" || s === "med" || s === "moderate") return "dim-med";
            if (s === "low" || s === "easy") return "dim-low";
            return "";
        };
        const dimText = v => {
            if (!v) return "N/A";
            const map = {
                high: "高", hard: "高", medium: "中", med: "中", moderate: "中",
                low: "低", easy: "低",
            };
            return map[String(v).toLowerCase()] || v;
        };

        const head = el("div", { class: "rec-compare-row rec-compare-head" }, [
            el("span", { class: "c-idx" }, "#"),
            el("span", { class: "c-topic" }, "推荐主题"),
            el("span", { class: "c-dim", title: "只按本轮检索样本量和近三年样本比例计算，不代表全领域热门度" }, "样本活跃度"),
            el("span", { class: "c-dim", title: "实现该方向的难易程度：高=需大量实验/算力，低=可行性更高" }, "难度"),
            el("span", { class: "c-dim", title: "创新度：该方向相对已有工作的新颖程度，高=填补空白或提出新范式" }, "创新度"),
            el("span", { class: "c-dim", title: "关联度：该方向与你的研究兴趣的匹配程度，高=贴合输入兴趣" }, "关联度"),
            el("span", { class: "c-dim", title: "本轮采样不能计算可信的年度增长率" }, "增长率"),
        ]);
        const rows = [head];

        recommendations.forEach((rec, i) => {
            const isSelected = selectedTopic && rec.topic === selectedTopic;
            const pop = rec.popularity_score;
            const row = el("div", { class: "rec-compare-row" }, [
                el("span", { class: "c-idx" }, `[${i + 1}]`),
                el("span", { class: "c-topic", title: rec.topic || "" },
                    isSelected ? `✓ ${rec.topic || "N/A"}` : (rec.topic || "N/A")),
                el("span", { class: "c-dim pop-cell" }, [
                    el("span", { class: "pop-bar" }, [
                        el("span", { class: "pop-fill", style: `width:${Math.min(100, Math.max(0, pop || 0))}%` }),
                    ]),
                    el("span", { class: "pop-val" }, `${pop ?? "N/A"}`),
                ]),
                el("span", { class: `c-dim ${dimClass(rec.difficulty)}` }, [
                    el("span", { class: "dim-dot" }),
                    dimText(rec.difficulty),
                ]),
                el("span", { class: `c-dim ${dimClass(rec.novelty)}` }, [
                    el("span", { class: "dim-dot" }),
                    dimText(rec.novelty),
                ]),
                el("span", { class: `c-dim ${dimClass(rec.relevance)}` }, [
                    el("span", { class: "dim-dot" }),
                    dimText(rec.relevance),
                ]),
                el("span", { class: "c-dim c-dim-col" }, rec.evidence_papers?.length ? "未验证" : fmtGrowth(rec.growth_rate)),
            ]);
            rows.push(row);
        });

        // 表尾图例：说明每个维度的含义，避免用户看不懂
        rows.push(el("div", { class: "rec-compare-row rec-compare-legend" }, [
            el("span", { class: "c-idx" }, "ℹ"),
            el("span", { class: "c-topic", text: "样本活跃度仅供本轮对比；各项依据见下方卡片。增长率未验证。" }),
            el("span", { class: "c-dim" }, ""),
            el("span", { class: "c-dim" }, ""),
            el("span", { class: "c-dim" }, ""),
            el("span", { class: "c-dim" }, ""),
            el("span", { class: "c-dim" }, ""),
        ]));

        return el("div", { class: "rec-compare mt-12" }, rows);
    }

    async function retryTopicDiscovery() {
        const pid = state.currentProjectId;
        const current = state.statusCache;
        if (!pid || !current?.topic) return;
        try {
            await api("POST", `/api/projects/${pid}/run-topic-discovery`, {interest:current.topic,domain:current.domain || state.domain});
            state.runMode = "topic_discovery";
            await pollStatus();
            setActivePage("progress");
        } catch (error) { showToast("选题探索启动失败：" + error.message, "error"); }
    }

    function renderTopicDiscovery(content) {
        const status = state.statusCache;
        const legacyOutsideScope = status?.domain && !RESEARCH_DOMAINS.some(d => d.value === status.domain) && !status.domain.startsWith("stem:");
        const td = status?.topic_discovery || {};
        const recommendations = td.recommendations || [];
        const selectedTopic = td.selected_topic || "";
        const interest = td.interest || status?.topic || "";
        const canSelectTopic = !legacyOutsideScope && status?.status === "pending_human"
            && status?.pending_human?.context?.node_id === "topic_select";

        const card = el("div", { class: "card" }, [
            el("h2", { class: "page-title" }, "选题探索"),
            el("p", { class: "muted small" },
                "围绕计算机与 AI 研究兴趣，检索细分方向的真实论文；无可核验证据时不生成推荐。"),
            el("p", { class: "muted small" }, RESEARCH_SCOPE_NOTICE),
        ]);
        if (legacyOutsideScope) {
            card.appendChild(el("p", {class:"alert alert-warning"}, "当前项目属于暂未开放的历史领域。已有推荐和材料仍可查看；如需计算机研究，请新建项目，不会改写本项目。"));
        } else if (status && !["running", "pending_human"].includes(status.status)) {
            card.appendChild(el("button", {class:"btn btn-secondary",onclick:retryTopicDiscovery}, "继续探索选题"));
        }

        // 研究兴趣显示
        if (interest) {
            card.appendChild(el("div", { class: "field" }, [
                el("span", { class: "field-label" }, "研究兴趣："),
                el("span", { text: interest }),
            ]));
        }

        // 状态横幅
        if (status && status.status) {
            card.appendChild(el("div", {
                html: statusBanner(status.status, status.summary, status.error, status.recommendation, status.advice),
            }));
        }

        // 等待人工选择
        if (status && status.status === "pending_human") {
            card.appendChild(el("div", { class: "alert alert-warning" }, [
                el("p", {}, "系统已生成推荐主题，等待你选择。"),
                el("button", { class: "btn", onclick: () => setActivePage("human") }, "去选择主题"),
            ]));
        }

        // 推荐结果列表（服务端已按本轮样本活跃度 popularity_score 降序返回，前端不要二次排序，
        // 否则数字下标与 TopicSelectHuman 的服务端解析不一致会导致选错主题）
        if (recommendations.length > 0) {
            const recSection = el("div", { class: "mt-16" }, [
                el("h3", { class: "section-title" }, `推荐研究主题（${recommendations.length} 个，按推荐评分排序）`),
            ]);

            // 维度对比总览表（纯展示，点击选择仍走下方卡片；排序与选择契约不受影响）
            recSection.appendChild(buildRecCompareTable(recommendations, selectedTopic));

            recommendations.forEach((rec, i) => {
                const isSelected = selectedTopic && rec.topic === selectedTopic;
                const recCard = el("div", {
                    class: "card rec-card" + (isSelected ? " rec-selected" : ""),
                    onclick: canSelectTopic ? () => selectTopic(i, rec) : null,
                }, [
                    el("div", { class: "rec-header" }, [
                        el("span", { class: "rec-index" }, `[${i + 1}]`),
                        el("span", { class: "rec-topic", text: rec.topic || "N/A" }),
                        isSelected ? el("span", { class: "badge badge-success" }, "已选择") : null,
                    ]),
                    // 本轮检索样本活跃度条形（0-100）
                    el("div", { class: "rec-popbar" }, [
                        el("span", { class: "rec-popbar-label" }, rec.evidence_papers?.length ? "样本活跃度" : "热门度"),
                        el("span", { class: "rec-popbar-track" }, [
                            el("span", { class: "rec-popbar-fill", style: `width:${Math.min(100, Math.max(0, rec.popularity_score || 0))}%` }),
                        ]),
                        el("span", { class: "rec-popbar-val" }, `${rec.popularity_score ?? "N/A"}`),
                    ]),
                    el("div", { class: "rec-body" }, [
                        ...(rec.popularity_basis ? [el("div", { class: "rec-field" }, [
                            el("span", { class: "rec-label", text: "评分依据：" }),
                            el("span", { text: rec.popularity_basis }),
                        ])] : []),
                        el("div", { class: "rec-field" }, [
                            el("span", { class: "rec-label", text: "理由：" }),
                            el("span", { text: rec.rationale || "N/A" }),
                        ]),
                        el("div", { class: "rec-field" }, [
                            el("span", { class: "rec-label", text: "创新切入点：" }),
                            el("span", { text: rec.innovation_point || "N/A" }),
                        ]),
                        el("div", { class: "rec-field" }, [
                            el("span", { class: "rec-label", text: "推荐研究对象：" }),
                            el("span", { text: (rec.recommended_objects || rec.recommended_materials || []).join(", ") || "N/A" }),
                        ]),
                        el("div", { class: "rec-field" }, [
                            el("span", { class: "rec-label", text: "趋势摘要：" }),
                            el("span", { text: rec.trend_summary || "N/A" }),
                        ]),
                        ...(rec.relevance_basis ? [el("div", { class: "rec-field" }, [
                            el("span", { class: "rec-label", text: "关联度依据：" }),
                            el("span", { text: rec.relevance_basis }),
                        ])] : []),
                        ...(rec.novelty_basis ? [el("div", { class: "rec-field" }, [
                            el("span", { class: "rec-label", text: "创新度依据：" }),
                            el("span", { text: rec.novelty_basis }),
                        ])] : []),
                        ...(rec.difficulty_basis ? [el("div", { class: "rec-field" }, [
                            el("span", { class: "rec-label", text: "难度依据：" }),
                            el("span", { text: rec.difficulty_basis }),
                        ])] : []),
                        ...(rec.evidence_papers?.length ? [el("div", { class: "rec-field" }, [
                            el("span", { class: "rec-label", text: "检索论文：" }),
                            el("span", {}, rec.evidence_papers.map((paper, index) =>
                                el("a", { href: paper.url, target: "_blank", rel: "noopener noreferrer", text: `${index + 1}. ${paper.title} (${paper.year || "?"}) ` })
                            )),
                        ])] : []),
                        el("div", { class: "rec-tags" }, [
                            el("span", { class: "badge badge-neutral", title: "实现该方向的难易程度：高=需大量实验/算力，低=可行性更高", text: `难度: ${rec.difficulty || "N/A"}` }),
                            el("span", { class: "badge badge-info", title: "创新度：该方向相对已有工作的新颖程度，高=填补空白或提出新范式", text: `创新度: ${rec.novelty || "N/A"}` }),
                            el("span", { class: "badge badge-warning", title: rec.popularity_basis || "模拟评分", text: `${rec.evidence_papers?.length ? "样本活跃度" : "模拟评分"}: ${rec.popularity_score != null ? rec.popularity_score : "N/A"}` }),
                            el("span", { class: "badge badge-success", title: "关联度：该方向与你的研究兴趣的匹配程度，高=贴合输入兴趣", text: `关联度: ${rec.relevance || "N/A"}` }),
                            el("span", { class: "badge badge-accent", title: rec.evidence_papers?.length ? "检索样本不支持可靠年度增长率估计" : "仅模拟数据", text: rec.evidence_papers?.length ? "年度增长率: 未验证" : `模拟增长率: ${fmtGrowth(rec.growth_rate)}` }),
                        ]),
                        el("div", { class: "rec-hint small muted mt-8" },
                            canSelectTopic ? "点击卡片即可选择该主题" : "历史推荐，仅供查看；不会影响当前运行的节点"),
                    ]),
                ]);
                recSection.appendChild(recCard);
            });

            card.appendChild(recSection);
        }

        // 已完成且选择了主题 → 提供开始文献调研按钮
        if (selectedTopic) {
            card.appendChild(el("div", { class: "alert alert-success mt-16" }, [
                el("p", {}, `已选择主题：${selectedTopic}`),
            ]));
            card.appendChild(el("div", { class: "btn-row mt-16" }, [
                ...(status?.status === "completed" && status?.run_mode === "topic_discovery" ? [el("button", {
                    class: "btn btn-success",
                    onclick: () => startPipeline(),
                }, "使用该主题开始文献调研")] : []),
                el("button", {
                    class: "btn btn-secondary",
                    onclick: () => setActivePage("progress"),
                }, "查看进度"),
            ]));
        }

        // 运行中提示
        if (status && status.status === "running" && recommendations.length === 0) {
            card.appendChild(el("div", { class: "alert alert-info" }, [
                el("p", {}, "正在分析领域趋势，获取论文数据并生成推荐…"),
                el("p", { class: "muted small" }, "此过程可能需要 1-3 分钟（真实模式）或几秒（dry_run 模式）"),
            ]));
        }

        content.appendChild(card);
    }

    // 点击推荐卡片选择主题（数字下标 = 服务端 ctx 中 recommendations 下标，与服务端 TopicSelectHuman 解析一致）
    async function selectTopic(i, rec) {
        if (!state.currentProjectId) return;
        if (state.statusCache?.pending_human?.context?.node_id !== "topic_select") {
            showToast("当前不是选择主题节点，推荐结果仅供查看", "info");
            return;
        }
        try {
            await api("POST", `/api/projects/${state.currentProjectId}/human-response`, {
                action: "continue",
                text: String(i + 1),
                expected_node_id: "topic_select",
            });
            showToast("已选择：" + (rec.topic || ""), "success");
            state.humanFingerprint = null; // 强制下一轮刷新页面状态
            // 后端在方向推荐完成后会自动启动 research pipeline。
            // 这里轮询几次确认状态从 completed 切到 running 即跳到进度页。
            setActivePage("progress");
            for (let k = 0; k < 8; k++) {
                await pollStatus();
                if (state.statusCache && state.statusCache.status === "running") {
                    renderPage();
                    return;
                }
                await new Promise(r => setTimeout(r, 500));
            }
            renderPage();
        } catch (e) {
            showToast("选择失败：" + e.message, "error");
        }
    }

    // ===== 2. 研究进度页 =====

    function renderProgress(content) {
        if(window.SRA_WORKSPACE_VIEW){window.SRA_WORKSPACE_VIEW.mount(content,'activities',window.__sraApp);return;}
        const data = state.statusCache;
        content.appendChild(el("div", {class:"page-header"}, [el("h2", {class:"page-title"}, "任务进度"), el("p", {class:"page-desc"}, "查看后台任务、失败原因与待确认事项。任务完成不等于研究结论成立；浏览已有材料不会取消正在运行的任务。") ]));
        const workspaceData=api("GET",`/api/projects/${state.currentProjectId}/workspace?include_chat=false`);
        renderProjectManagement(content,workspaceData);
        renderResearchFlow(content,workspaceData);
        if (!data) {
            content.appendChild(el("div", { class: "loading" }, "加载中…"));
            pollStatus();
            return;
        }

        // 顶部状态
        content.insertAdjacentHTML("beforeend",
            statusBanner(data.status, data.summary, data.error, data.recommendation, data.advice));
        content.appendChild(renderContinuationCard(data));

        // 阶段进度条
        content.appendChild(renderStageProgress(data));

        // 当前执行提示卡（长任务不干等：正在做什么 + 下一步）
        content.appendChild(renderCurrentNode(data));

        // 计数卡片
        content.appendChild(renderCounts(data.counts || {}));

        // 操作区
        const actionCard = el("div", { class: "card" }, [
            el("div", { class: "card-title" }, "当前主线与后台材料任务"),
            el("div", { class: "btn-row" }, [
                el("button", {
                    class: "btn btn-success",
                    onclick: () => startPipeline(),
                }, data.continuation?.label || "继续当前任务"),
                el("button", {
                    class: "btn btn-secondary",
                    onclick: () => { pollStatus(); showToast("已刷新", "success"); },
                }, "刷新状态"),
            ]),
        ]);
        content.appendChild(actionCard);
        const engine=data.workflow?.execution;
        if(engine?.action==="resume" && data.continuation?.source==="research") {
            const explicit=el("button",{class:"btn btn-secondary btn-sm",onclick:async()=>{
                if(!confirm("继续后台材料生成任务？可能调用模型。不会更换已确认的研究主线，也不会自动执行实验。")) return;
                try {
                    if(engine.run_mode==="topic_discovery") await api("POST",`/api/projects/${state.currentProjectId}/run-topic-discovery`,{interest:data.topic,domain:data.domain});
                    else await api("POST",`/api/projects/${state.currentProjectId}/run`,{});
                    await pollStatus();
                } catch(error) {showToast(error.message,"error");}
            }},"继续后台材料生成（需模型调用）");
            actionCard.append(el("p",{class:"muted small"},"研究主线与后台执行状态分开：已有记录可继续整理，也可明确选择恢复后台生成任务。"),explicit);
        }

        // 历史执行阶段仅供查看，不再显示后续执行或写入入口。
        if (data.status === "experiment_pending") {
            const waitCard = el("div", { class: "card" }, [
                el("div", { class: "card-title" }, "历史实验任务 — 仅供查看与导出"),
                el("p", { class: "muted small" },
                    "当前产品止于研究方案，不继续该历史实验阶段，不生成实验任务或接收结果回填。已有记录与任务说明书可查看和下载，不会重跑或改写。"),
            ]);
            const waitRow = el("div", { class: "btn-row" }, [
                el("button", {
                    class: "btn btn-secondary",
                    onclick: () => { setActivePage("experiments"); },
                }, "查看历史任务说明书"),
                el("button", {
                    class: "btn btn-secondary",
                    onclick: () => { pollStatus(); showToast("已刷新状态", "success"); },
                }, "刷新状态"),
                el("button", {class:"btn btn-secondary",onclick:()=>setActivePage("writing")}, "查看历史稿件"),
            ]);
            waitCard.appendChild(waitRow);
            content.appendChild(waitCard);
        }

        // 实验失败时的特殊操作区
        if (data.status === "experiment_failed") {
            const failCard = el("div", { class: "card" }, [
                el("div", { class: "card-title" }, "历史实验阶段 — 仅供查看"),
                el("p", { class: "muted small" },
                    "此处保留原执行状态，不据此推断研究假设被反驳。当前产品不继续该阶段，可查看和导出已有记录。"),
            ]);
            const failRow = el("div", { class: "btn-row" }, [
                el("button", {class:"btn btn-secondary",onclick:()=>setActivePage("writing")}, "查看历史稿件与局限"),
                el("button", {
                    class: "btn btn-secondary",
                    onclick: () => {
                        setActivePage("experiments");
                    },
                }, "查看历史实验记录"),
            ]);
            failCard.appendChild(failRow);
            content.appendChild(failCard);
        }

        // 中止/失败时的操作
        if (!data.product_boundary?.restricted && (data.status === "aborted" || data.status === "failed")) {
            const errCard = el("div", { class: "card" }, [
                el("div", { class: "card-title" }, "异常恢复"),
                el("p", { class: "muted small" },
                    `Pipeline 状态为 ${data.status}。可以重新启动继续执行，或查看错误信息。`),
                el("div", { class: "btn-row" }, [
                    el("button", {
                        class: "btn btn-success",
                        onclick: () => startPipeline(),
                    }, "重新启动"),
                ]),
            ]);
            content.appendChild(errCard);
        }

        // 节点历史时间线
        content.appendChild(renderTimeline(data.node_history || []));
    }

    function renderCurrentNode(data) {
        const card = el("div", { class: "card current-node-card" });
        const position = NAVIGATION.executionProgress(data, FLOW_STEPS);
        const cur = position.nodeId ? {node_id:position.nodeId} : null;
        const next = data.next_nodes || [];
        const prefix = data.status === "pending_human" ? "等待确认：" :
            ["failed", "blocked", "aborted"].includes(data.status) ? "失败位置：" :
            data.status === "experiment_pending" ? "历史执行位置：" :
            data.status === "completed" ? "材料任务结束于：" :
            data.status === "running" && ["method_review","experiment_review","revise"].includes(position.nodeId) ? "正在处理审核意见：" :
            data.status === "running" ? "正在执行：" : "上次执行到：";
        card.dataset.executionNode = position.nodeId;
        card.appendChild(el("div", {class:"muted small"}, `当前阶段：${STAGE_LABELS[position.stage] || position.stage || "尚未启动"}`));

        // 当前执行
        if (cur && cur.node_id) {
            const head = el("div", { class: "current-node-head" }, [
                el("span", { class: "current-node-dot" }),
                el("div", {}, [
                    el("div", { class: "current-node-title" }, [
                        el("strong", { text: prefix }),
                        el("span", { text: nodeLabel(cur.node_id) }),
                        el("span", { class: "badge badge-info mono small", text: cur.node_id }),
                    ]),
                    el("div", { class: "current-node-hint muted small" },
                        data.product_boundary?.restricted ? "此历史执行阶段受限，仅可查看与导出已有材料，不继续执行。" :
                        data.status === "running" ? "后台正在执行，浏览其他页面不会取消任务。" :
                        data.status === "pending_human" ? "请前往「待确认事项」审核后继续。" :
                        data.status === "experiment_pending" ? "历史实验材料可查看与导出，当前不开放结果回填。" :
                        data.status === "completed" ? "后台材料任务已结束，请核对问题和方案；不代表结论已验证。" :
                        "可保留已有成果，从这个位置恢复；不会重新检索已完成阶段。"),
                ]),
            ]);
            card.appendChild(head);

            // 节点级进度条（如 paper_fetch 检索进度：已完成子问题 / 总数 + 已抓取篇数）
            const prog = data.node_progress;
            if (prog && prog.total > 0 && (!cur || (cur.node_id || "").includes("paper_fetch"))) {
                const pct = Math.min(100, Math.max(0, prog.percent || 0));
                const bar = el("div", { class: "node-progress" }, [
                    el("div", { class: "node-progress-track" }, [
                        el("div", { class: "node-progress-fill", style: `width:${pct}%` }),
                    ]),
                    el("div", { class: "node-progress-text muted small" },
                        `论文检索进度：${pct}%（子问题 ${prog.done}/${prog.total}，已抓取 ${prog.fetched || 0} 篇）`),
                ]);
                card.appendChild(bar);
            }

            // 下一步预告
            if (!data.product_boundary?.restricted && next && next.length) {
                const steps = next.map(n =>
                    el("span", { class: "next-node-chip", text: nodeLabel(n) }));
                card.appendChild(el("div", { class: "next-node-row" }, [
                    el("span", { class: "muted small", text: "下一步：" }),
                    ...steps,
                ]));
            }
        } else if (data.status === "pending_human") {
            card.appendChild(el("div", { class: "current-node-head" }, [
                el("span", { class: "current-node-dot warn" }),
                el("div", {}, [
                    el("div", { class: "current-node-title" },
                        el("strong", { text: "等待人工确认：" })),
                    el("div", { class: "current-node-hint muted small" },
                        "请在下方操作区或相应页面完成确认后继续。"),
                ]),
            ]));
        } else {
            card.appendChild(el("div", { class: "current-node-hint muted small" },
                data.status === "created" ? "尚未启动，请点击启动。" :
                data.status === "completed" ? "后台材料任务已结束，不代表研究完成或结论已验证。" : "正在读取执行位置…"));
        }
        return card;
    }

    function renderStageProgress(data) {
        const card = el("div", { class: "card" });
        card.appendChild(el("div", { class: "card-title" }, "研究方案流程（历史执行状态保留）"));
        const stageStatuses = data.stage_statuses || {};
        const currentStage = data.current_stage;

        const progress = el("div", { class: "stage-progress" });
        STAGES.filter(s => ["research", "ideation", "design"].includes(s)).forEach((s, i) => {
            const status = stageStatuses[s] || "not_started";
            const awaitingResults = s === "experiment" && data.status === "experiment_pending";
            let cls = "";
            if (awaitingResults) cls = "active";
            else if (status === "done") cls = "done";
            else if (s === currentStage && status !== "not_started") cls = "active";
            else if (status === "in_progress" || status === "pending_review" || status === "blocked") cls = "active";

            const step = el("div", { class: `stage-step ${cls}`, "data-stage":s, "aria-current":cls === "active" ? "step" : "false" }, [
                el("div", { class: "stage-name", text: STAGE_LABELS[s] || s }),
                el("div", { class: "stage-status", text: awaitingResults ? "等待实际结果" : ({done:"已完成",in_progress:"进行中",pending_review:"等待确认",blocked:"需处理",not_started:"尚未开始"}[status] || status) }),
            ]);
            progress.appendChild(step);
        });
        card.appendChild(progress);
        if (data.product_boundary?.restricted) card.appendChild(el("p",{class:"muted small"},`历史执行阶段：${STAGE_LABELS[data.current_stage] || data.current_stage || "未知"}，当前仅可查看与导出。`));
        return card;
    }

    function renderCounts(counts) {
        const wrap = el("div", { class: "counts-grid" });
        const items = [
            { label: "论文", value: counts.papers, extra: "research 阶段产出" },
            { label: "思路", value: counts.ideas, extra: "ideation 阶段产出" },
            { label: "论断", value: counts.claims, extra: "原流程保存的论断" },
            { label: "实验", value: counts.experiments, extra: "experiment 阶段产出" },
        ];
        items.forEach(it => {
            wrap.appendChild(el("div", { class: "count-card" }, [
                el("div", { class: "count-label", text: it.label }),
                el("div", { class: "count-value", text: String(it.value != null ? it.value : 0) }),
                el("div", { class: "count-extra", text: it.extra }),
            ]));
        });
        return wrap;
    }

    function renderTimeline(history) {
        const card = el("div", { class: "card" });
        card.appendChild(el("div", { class: "card-title" }, "节点执行历史"));
        if (!history || history.length === 0) {
            card.appendChild(el("div", { class: "list-empty" }, "暂无节点执行记录"));
            return card;
        }
        const tl = el("div", { class: "timeline" });
        history.forEach(h => {
            const status = h.status || "unknown";
            let cls = "";
            if (status === "success") cls = "success";
            else if (status === "failed") cls = "failed";
            else if (status === "pending_human") cls = "pending";
            else if (status === "skipped") cls = "skipped";

            const item = el("div", { class: `timeline-item ${cls}` });
            item.appendChild(el("div", { class: "timeline-head" }, [
                el("span", { class: "timeline-node-id", text: nodeLabel(h.node_id) }),
                el("span", { class: "timeline-node-type mono", text: h.node_id || "" }),
                el("span", { class: "timeline-time", text: formatTime(h.timestamp) }),
            ]));
            item.appendChild(el("div", { class: "timeline-summary" },
                h.summary || `（无摘要，状态=${status}）`));
            tl.appendChild(item);
        });
        card.appendChild(tl);
        return card;
    }

    // ===== 3. 论文浏览页 =====

    async function renderPapers(content) {
        const paperProjectId=state.currentProjectId;
        content.appendChild(el("div", { class: "loading" }, "加载中…"));
        try {
            // 所有读取固定到进入页面时的项目；切换后不混入新项目的数据。
            const evData = await api("GET", `/api/projects/${paperProjectId}/evidence`)
                .catch(() => null);
            const paperData = await api("GET", `/api/projects/${paperProjectId}/papers`);
            const unlinkedData = await api("GET", `/api/projects/${paperProjectId}/unlinked-papers`)
                .catch(() => null);
            const papers = paperData.papers || [];
            const shared=await api('GET',`/api/projects/${paperProjectId}/workspace?include_chat=false`).catch(()=>null);
            if(paperProjectId!==state.currentProjectId || state.currentPage!=='papers' || !content.isConnected)return;
            updateBadges({...state.statusCache,project_id:paperProjectId,counts:{...state.statusCache?.counts,papers:papers.length}});
            const question=shared?.workflow?.flow?.question;
            for(const p of papers)p.ui_relation=question?.paper_ids?.includes(p.paper_id)?'已关联当前研究问题：'+question.title:p.relevance_reason || '尚未关联到当前已确认问题';
            const unlinked = (unlinkedData && unlinkedData.papers) || [];
            state.papersCache = papers; // 供 rerenderPaperList 前端过滤复用
            clear(content);

            // ===== 未入库候选视图 =====
            if (state.papersView === "unlinked") {
                renderUnlinkedPapers(content, unlinked, (unlinkedData && unlinkedData.filter_note) || "");
                return;
            }

            content.appendChild(el("div", { class: "page-header" }, [
                el("h2", { class: "page-title" }, "文献库"),
                el("p", { class: "page-desc" }, `共 ${papers.length} 篇入库论文，点击条目展开详情。`),
            ]));
            // Reuse the existing upload component; do not require a new search
            // or send users to an unused workspace application to add material.
            content.appendChild(el("details", { class: "paper-import" }, [
                el("summary", {}, "导入已有文献（PDF / TXT / MD）"),
                renderUploadCard(true),
            ]));
            const taskAudit = el('section', {class:'paper-task-audit', 'aria-label':'对话检索审计轨迹'});
            content.appendChild(taskAudit);
            renderSearchAudit(taskAudit, evData?.conversation_search, paperProjectId);
            // 检索证据链卡片（真实模式下由 Sciverse/arXiv/S2 检索命中记录生成）
            const archivedEvidence=el('details',{class:'paper-evidence-archive'},[el('summary',{},'历史检索记录与技术详情（不是当前任务统计）'),renderEvidenceCard(evData)]);
            content.appendChild(archivedEvidence);
            // 未入库论文入口卡片：始终显示（0 篇时提示暂无候选），标注筛选/去重数量
            {
                const rejected = unlinked.filter(p => p.reason === "score_rejected").length;
                const merged = unlinked.filter(p => p.reason === "dedup_merged").length;
                const unclassified = unlinked.length - rejected - merged;
                const desc = unlinked.length
                    ? `${unlinked.length} 篇检索命中但未入库的候选（低分 ${rejected} · 重复 ${merged} · 待核实 ${unclassified}），可手动补录入库`
                    : "尚无历史候选记录；不代表本轮没有返回资料。当前检索范围和排除数量请查看对话中的任务卡。";
                archivedEvidence.appendChild(el("div", {
                    class: "card unlinked-entry",
                    id: "unlinked-entry",
                    onclick: () => { state.papersView = "unlinked"; renderPage(); },
                }, [
                    el("div", { class: "unlinked-entry-main" }, [
                        el("span", { class: "unlinked-entry-title" }, "未入库论文"),
                        el("span", { class: "unlinked-entry-desc", text: desc }),
                    ]),
                    el("span", { class: "unlinked-entry-arrow" }, "查看 →"),
                ]));
            }
            if (!papers.length) {
                content.appendChild(el("div", { class: "list-empty" }, "暂无论文。可在上方导入已有资料，或返回科研对话明确请求检索；浏览不会自动启动任务。"));
                return;
            }
            // 筛选工具栏（搜索/年份/期刊/排序），前端过滤
            content.appendChild(buildPaperFilterBar(papers));

            // 应用筛选 + 排序
            const filtered = filterPapers(papers, state.papersFilter);

            const list = el("div", { class: "list" });
            filtered.forEach(p => list.appendChild(renderPaperItem(p)));
            content.appendChild(list);
            // 空结果提示由 rerenderPaperList 统一维护，这里不重复添加

            // 证据溯源定位：跳转本页后定位到指定论文（滚动 + 高亮 + 展开）
            const targetId = state.pendingPaperId;
            if (targetId) {
                state.pendingPaperId = null;
                const cards = list.querySelectorAll(".list-item");
                for (const card of cards) {
                    if (card.getAttribute("data-paper-id") === targetId) {
                        card.scrollIntoView({ behavior: "smooth", block: "center" });
                        card.classList.add("paper-flash");
                        setTimeout(() => card.classList.remove("paper-flash"), 2600);
                        // 自动展开详情
                        if (!card.classList.contains("expanded")) {
                            card.classList.add("expanded");
                            if (!card.querySelector(".list-item-body")) {
                                const p = papers.find(x => x.paper_id === targetId);
                                if (p) card.appendChild(buildPaperBody(p));
                            }
                        }
                        break;
                    }
                }
            }
        } catch (e) {
            if(paperProjectId!==state.currentProjectId || state.currentPage!=='papers')return;
            clear(content);
            content.appendChild(el("div", { class: "status-banner danger" },
                `加载失败：${escapeHtml(e.message)}`));
        }
    }

    // ===== 论文筛选工具栏（搜索/年份/期刊/排序，前端过滤）=====

    function buildPaperFilterBar(allPapers) {
        const F = state.papersFilter;
        const wrap = el("div", { class: "card paper-filter-bar" });

        // 关键词搜索
        const qInput = el("input", {
            class: "input paper-filter-input paper-filter-q",
            type: "text",
            placeholder: "搜索标题、作者或摘要…",
            value: F.q,
        });
        qInput.addEventListener("input", () => { F.q = qInput.value.trim(); rerenderPaperList(); });

        // 年份范围
        const yMin = el("input", {
            class: "input paper-filter-year",
            type: "number", min: "1000", max: "2100",
            placeholder: "起始年",
            value: F.yearMin,
        });
        yMin.addEventListener("input", () => { F.yearMin = yMin.value; rerenderPaperList(); });
        const yMax = el("input", {
            class: "input paper-filter-year",
            type: "number", min: "1000", max: "2100",
            placeholder: "结束年",
            value: F.yearMax,
        });
        yMax.addEventListener("input", () => { F.yearMax = yMax.value; rerenderPaperList(); });

        // 期刊筛选（下拉列出已有的 venue 供选择）
        const venueOpts = [];
        const venueSet = new Set();
        allPapers.forEach(p => {
            const v = (p.venue || "").trim();
            if (v) venueSet.add(v);
        });
        venueOpts.push(el("option", { value: "", text: "全部期刊" }));
        [...venueSet].sort().forEach(v => {
            venueOpts.push(el("option", { value: v, text: truncateText(v, 40) }));
        });
        const venueSel = el("select", { class: "input paper-filter-select" });
        venueOpts.forEach(o => venueSel.appendChild(o));
        venueSel.value = F.venue || "";
        venueSel.addEventListener("change", () => { F.venue = venueSel.value; rerenderPaperList(); });

        // 中科院分区筛选
        const casSel = el("select", { class: "input paper-filter-select paper-filter-cas" });
        [
            ["", "全部分区"],
            ["1", "1区（顶刊）"],
            ["2", "2区"],
            ["3", "3区"],
            ["4", "4区"],
        ].forEach(([val, label]) => {
            casSel.appendChild(el("option", { value: val, text: label }));
        });
        casSel.value = F.casZone || "";
        casSel.addEventListener("change", () => { F.casZone = casSel.value; rerenderPaperList(); });

        // 最低影响因子
        const ifMin = el("input", {
            class: "input paper-filter-year paper-filter-if",
            type: "number", min: "0", max: "100", step: "0.1",
            placeholder: "最低IF",
            value: F.ifMin,
        });
        ifMin.addEventListener("input", () => { F.ifMin = ifMin.value; rerenderPaperList(); });

        // 排序
        const sortSel = el("select", { class: "input paper-filter-select" });
        [
            ["newest", "年份 ↓（最新优先）"],
            ["oldest", "年份 ↑（最早优先）"],
            ["relevance", "相关度 ↓"],
            ["if_desc", "影响因子 ↓"],
            ["if_asc", "影响因子 ↑"],
            ["title", "标题 A→Z"],
        ].forEach(([val, label]) => {
            sortSel.appendChild(el("option", { value: val, text: label }));
        });
        sortSel.value = F.sort || "newest";
        sortSel.addEventListener("change", () => { F.sort = sortSel.value; rerenderPaperList(); });

        // 仅可下载 PDF 复选框
        const pdfOnlyCb = el("input", {
            type: "checkbox",
            id: "pdf-only-toggle",
            class: "paper-filter-checkbox",
        });
        pdfOnlyCb.checked = !!F.pdfOnly;
        pdfOnlyCb.addEventListener("change", () => { F.pdfOnly = pdfOnlyCb.checked; rerenderPaperList(); });
        const pdfOnlyLabel = el("label", {
            class: "paper-filter-check-label",
            for: "pdf-only-toggle",
            text: "有 PDF 线索",
        });

        // 结果计数 + 重置
        const countLabel = el("span", { class: "paper-filter-count", text: `${allPapers.length} 篇` });
        const resetBtn = el("button", {
            class: "btn btn-outline btn-sm",
            text: "重置",
            onclick: () => {
                F.q = ""; F.yearMin = ""; F.yearMax = ""; F.venue = ""; F.sort = "newest";
                F.casZone = ""; F.ifMin = ""; F.pdfOnly = false;
                qInput.value = ""; yMin.value = ""; yMax.value = "";
                venueSel.value = ""; casSel.value = ""; ifMin.value = ""; sortSel.value = "newest";
                pdfOnlyCb.checked = false;
                rerenderPaperList();
            },
        });

        wrap.appendChild(el("div", { class: "paper-filter-row" }, [
            qInput,
            el("span", { class: "paper-filter-label", text: "年份" }),
            yMin, el("span", { class: "paper-filter-sep", text: "–" }), yMax,
            countLabel,resetBtn,
        ]));
        const advanced=el('details',{class:'paper-advanced'}),advancedTitle=el('summary',{},'高级筛选与排序');
        advanced.appendChild(advancedTitle);
        advanced.appendChild(el('div',{class:'paper-filter-row'},[
            el("span", { class: "paper-filter-label", text: "期刊" }),
            venueSel,
            el("span", { class: "paper-filter-label", text: "分区" }),
            casSel,
            el("span", { class: "paper-filter-label", text: "最低IF" }),
            ifMin,
            el("span", { class: "paper-filter-label", text: "排序" }),
            sortSel,
            el("span", { class: "paper-filter-check-wrap" }, [pdfOnlyCb, pdfOnlyLabel]),
        ]));
        const labelFilters=()=>{advancedTitle.textContent='高级筛选与排序'+(F.venue || F.casZone || F.ifMin || F.pdfOnly || F.sort!=='newest'?'（已启用）':'');};
        advanced.addEventListener('change',labelFilters);advanced.addEventListener('input',labelFilters);labelFilters();wrap.appendChild(advanced);
        return wrap;
    }

    function filterPapers(allPapers, F) {
        const q = (F.q || "").toLowerCase().trim();
        const yMin = parseInt(F.yearMin, 10);
        const yMax = parseInt(F.yearMax, 10);
        const venueQ = (F.venue || "").toLowerCase().trim();
        const casZone = F.casZone || "";
        const ifMinVal = parseFloat(F.ifMin);
        const pdfOnly = !!F.pdfOnly;

        let out = allPapers.filter(p => {
            // 仅筛选有本地文件或直链线索；远端链接是否可获取仍须下载时验证。
            if (pdfOnly) {
                const pdfUrl = p.pdf_url || "";
                const arxivOk = !!p.arxiv_id || !!p.pdf_path;
                const directOk = pdfUrl && !pdfUrl.includes("doi.org");
                if (!arxivOk && !directOk) return false;
            }
            // 关键词：标题/作者/摘要/ID/来源
            if (q) {
                const hay = [
                    p.title || "", (p.authors || []).join(" "),
                    p.abstract || "", p.paper_id || "",
                    p.venue || "", p.source || "", p.doi || "",
                ].join(" ").toLowerCase();
                if (!hay.includes(q)) return false;
            }
            // 年份范围
            const y = p.year;
            if (y && !isNaN(yMin)) { if (y < yMin) return false; }
            if (y && !isNaN(yMax)) { if (y > yMax) return false; }
            // 期刊关键词
            if (venueQ && !((p.venue || "").toLowerCase().includes(venueQ))) return false;
            // 中科院分区筛选
            if (casZone && (p.cas_zone || "") !== casZone) return false;
            // 最低影响因子筛选
            if (!isNaN(ifMinVal) && (Number(p.impact_factor) || 0) < ifMinVal) return false;
            return true;
        });

        // 排序
        const sort = F.sort || "newest";
        out.sort((a, b) => {
            if (sort === "oldest") return (a.year || 0) - (b.year || 0);
            if (sort === "relevance") return (Number(b.relevance_score) || 0) - (Number(a.relevance_score) || 0);
            if (sort === "if_desc") return (Number(b.impact_factor) || 0) - (Number(a.impact_factor) || 0);
            if (sort === "if_asc") return (Number(a.impact_factor) || 0) - (Number(b.impact_factor) || 0);
            if (sort === "title") return String(a.title || "").localeCompare(String(b.title || ""));
            // newest 默认：年份新→旧，同年前靠 relevance
            const d = (b.year || 0) - (a.year || 0);
            return d !== 0 ? d : ((Number(b.relevance_score) || 0) - (Number(a.relevance_score) || 0));
        });
        return out;
    }

    // 仅重绘论文列表区（不重新拉接口），保持工具栏状态
    function rerenderPaperList() {
        const content = document.getElementById("content");
        if (!content) return;
        const listEl = content.querySelector(".list");
        const emptyEl = content.querySelector(".paper-filter-empty");
        const countEl = content.querySelector(".paper-filter-count");
        if (!listEl) return;
        // 重新读取当前缓存的 papers（从 DOM 之外保留一份更稳妥：这里直接用接口缓存）
        // renderPapers 内部维护的最新数据在 state.papersCache
        const papers = state.papersCache || [];
        const filtered = filterPapers(papers, state.papersFilter);
        listEl.innerHTML = "";
        filtered.forEach(p => listEl.appendChild(renderPaperItem(p)));
        if (countEl) countEl.textContent = `${filtered.length} / ${papers.length} 篇`;
        // 空结果提示挪到 toolbar 下方统一插入/移除
        const bar = content.querySelector(".paper-filter-bar");
        if (emptyEl) emptyEl.remove();
        if (!filtered.length && bar) {
            const tip = el("div", { class: "list-empty paper-filter-empty",
                text: "没有符合筛选条件的论文，试试放宽年份/期刊范围或清空搜索词。" });
            bar.after(tip);
        }
    }

    // ===== 未入库论文候选视图 =====

    async function renderUnlinkedPapers(content, unlinked, filterNote) {
        const rejected = unlinked.filter(p => p.reason === "score_rejected").length;
        const merged = unlinked.filter(p => p.reason === "dedup_merged").length;
        const unclassified = unlinked.length - rejected - merged;
        content.appendChild(el("div", { class: "page-header" }, [
            el("h2", { class: "page-title" }, "未入库论文"),
            el("p", { class: "page-desc" },
                `共 ${unlinked.length} 篇检索命中但未入库的候选论文，可手动补录入库。`),
            el("div", { class: "filter-stat-row" }, [
                el("span", { class: "badge badge-warning", text: `相关度<0.5 被筛选 ${rejected} 篇` }),
                el("span", { class: "badge badge-info", text: `与已入库重复去重 ${merged} 篇` }),
                el("span", { class: "badge badge-neutral", text: `待核实 ${unclassified} 篇` }),
                el("span", { class: "badge badge-neutral", text: `总计未入库 ${unlinked.length} 篇` }),
            ]),
        ]));
        // 返回按钮
        content.appendChild(el("div", { class: "btn-row mt-0 mb-12" }, [
            el("button", {
                class: "btn btn-secondary btn-sm",
                onclick: () => { state.papersView = "all"; renderPage(); },
            }, "← 返回文献库"),
        ]));
        // 量化标准说明卡片（为什么这些命中没入库）
        if (filterNote) {
            content.appendChild(el("div", { class: "card filter-note-card" }, [
                el("div", { class: "filter-note-title" }, "为什么命中子问题却没入库？"),
                el("div", { class: "filter-note-body", text: filterNote }),
                el("div", { class: "filter-note-body" },
                    "下方标注可核实的未入库原因；无法确定原因的候选会显示“待核实”，不推断其相关度。"),
            ]));
        }
        if (!unlinked.length) {
            content.appendChild(el("div", { class: "list-empty" },
                "暂无未入库候选：当前检索命中的证据均已关联入库或被去重剔除"));
            return;
        }
        const list = el("div", { class: "list" });
        unlinked.forEach(p => list.appendChild(renderUnlinkedItem(p)));
        content.appendChild(list);
    }

    function renderUnlinkedItem(p) {
        const item = el("div", { class: "list-item unlinked-item" });
        const headChildren = [
            el("span", { class: "list-item-title", text: p.title || "(无标题)" }),
            el("span", { class: "badge badge-neutral", text: p.source || "?" }),
            el("span", { class: "badge badge-info",
                text: `命中 ${p.hit_count} 个子问题` }),
        ];
        // 未入库原因徽章
        if (p.reason === "dedup_merged") {
            headChildren.push(el("span", { class: "badge reason-badge reason-dedup",
                title: p.reason_detail || "", text: "未入库 · 与已入库重复" }));
        } else if (p.reason === "score_rejected") {
            headChildren.push(el("span", { class: "badge reason-badge reason-score",
                title: p.reason_detail || "", text: "未入库 · 相关度 < 0.5" }));
        } else {
            headChildren.push(el("span", { class: "badge badge-neutral",
                title: p.reason_detail || "", text: "未入库 · 待核实" }));
        }
        if (Number(p.evidence_score || 0) > 0) {
            headChildren.push(el("span", {
                class: "badge ev-src-badge ev-src-sciverse",
                text: `证据分 ${Number(p.evidence_score).toFixed(2)}`,
            }));
        }
        const head = el("div", { class: "list-item-head" }, headChildren);
        item.appendChild(head);
        if (p.snippet) {
            item.appendChild(el("div", { class: "unlinked-snippet" },
                (p.snippet || "").slice(0, 220)));
        }
        // 操作：查看证据片段详情（折叠展开）+ 入库按钮
        const actions = el("div", { class: "unlinked-actions" }, [
            el("span", { class: "unlinked-subq",
                text: `命中子问题：${p.subquery || "—"}` }),
            el("button", {
                class: "btn btn-primary btn-sm",
                onclick: (ev) => {
                    ev.stopPropagation();
                    importUnlinkedPaper(p);
                },
            }, "入库"),
        ]);
        item.appendChild(actions);
        // 点击展开详情
        item.addEventListener("click", () => {
            const expanded = item.classList.toggle("expanded");
            if (expanded && !item.querySelector(".unlinked-body")) {
                const body = el("div", { class: "unlinked-body" });
                body.innerHTML = `
                    <dl>
                        <dt>来源</dt><dd>${escapeHtml(p.source || "—")}</dd>
                        <dt>外部 ID</dt><dd class="mono">${escapeHtml(p.external_id || "—")}</dd>
                        <dt>未入库原因</dt><dd>${escapeHtml(p.reason_detail || p.reason || "—")}</dd>
                        <dt>证据片段</dt><dd>${escapeHtml(p.snippet || "（无片段）")}</dd>
                        <dt>命中子问题</dt><dd>${escapeHtml(p.subquery || "—")}</dd>
                    </dl>
                `;
                item.appendChild(body);
            }
        });
        return item;
    }

    async function importUnlinkedPaper(p) {
        try {
            const res = await api("POST", `/api/projects/${state.currentProjectId}/papers/import`, {
                external_id: p.external_id,
                title: p.title,
                snippet: p.snippet,
            });
            // 入库成功后重新拉取未入库列表刷新
            state.papersView = "unlinked";
            const fresh = await api("GET", `/api/projects/${state.currentProjectId}/unlinked-papers`)
                .catch(() => null);
            const unlinked = (fresh && fresh.papers) || [];
            const note = (fresh && fresh.filter_note) || "";
            const content = document.getElementById("content");
            clear(content);
            await renderUnlinkedPapers(content, unlinked, note);
            // 顶部提示
            const banner = el("div", { class: "status-banner success" },
                `${res.message || "入库成功"}（已返回未入库列表）`);
            content.insertBefore(banner, content.firstChild);
        } catch (e) {
            alert(`入库失败：${e.message || e}`);
        }
    }

    function renderSearchAudit(host, data, pid, append=false) {
        if(!append) {
            clear(host);
            host.appendChild(el('h3',{class:'card-title'},'检索证据链 · 对话任务审计'));
            host.appendChild(el('p',{class:'muted small'},'按实际任务展示来源、筛选和入库记录；各次检索可能重复，不累计为文献库总数。'));
        }
        if(!data) {
            host.appendChild(el('p',{class:'muted small'},'当前未能读取对话检索审计。旧流程记录仍可在下方查看；这不表示当前项目没有检索。'));
            return;
        }
        if(!append && !data.tasks.length)host.appendChild(el('p',{class:'muted small'},'本项目尚无已保存的对话检索任务。手动导入的资料不计为检索。'));
        for(const task of data.tasks) {
            const coverage=task.coverage;
            const row=el('details',{class:'card paper-search-audit','data-task-id':task.task_id});
            row.appendChild(el('summary',{},`${task.topic} · ${task.status_label}`));
            // Server coverage is the report's saved snapshot. Running tasks
            // without a report show only counters they have actually saved.
            const stats=coverage ? `候选 ${coverage.screened_candidates} · 纳入 ${coverage.included_count} · 已返回但排除 ${coverage.excluded_count} · 新入库 ${coverage.newly_saved_count} · 关联已有 ${coverage.linked_existing_count}`
                : `已保存候选 ${task.candidates_count} · 已保存关联 ${task.saved_papers_count}（尚无报告范围快照）`;
            row.appendChild(el('p',{class:'small'},stats));
            if(coverage)row.appendChild(el('p',{class:'small muted'},`各次有效返回 ${coverage.valid_returned_attempt_total} 条（含重复）${coverage.unique_before_limit==null?'':` · 去重后 ${coverage.unique_before_limit} 条`}${coverage.omitted_by_candidate_limit ? ` · 候选上限外未处理 ${coverage.omitted_by_candidate_limit} 条`:''}。摘要 ${coverage.abstract_count} · 仅元信息 ${coverage.metadata_only_count} · 正文 ${coverage.full_text_read_count}。`));
            if(task.error)row.appendChild(el('p',{class:'small text-warning'},task.error));
            const calls=el('details',{},[el('summary',{},`来源调用记录（${task.source_calls.length} 条）`)]);
            const labels={ok:'成功返回',success:'成功返回',zero:'成功无结果',error:'调用失败'};
            for(const call of task.source_calls)calls.appendChild(el('p',{class:'small'},`${call.source} · ${call.query || '未记录查询'} · ${labels[call.status] || call.status}${call.degraded?'（部分解析失败，已保留有效资料）':''} · 有效返回 ${call.count ?? '未知'}${call.error?' · '+call.error:''}`));
            row.appendChild(calls);
            if(coverage?.excluded?.length) {
                const excluded=el('details',{},[el('summary',{},'筛选排除及原因')]);
                for(const item of coverage.excluded)excluded.appendChild(el('p',{class:'small'},`${item.title}：${item.reason}`));
                row.appendChild(excluded);
            }
            row.appendChild(el('button',{class:'btn btn-secondary btn-sm',onclick:()=>window.__sraApp.navigate({projectId:pid,conversationId:task.conversation_id,page:'dashboard'})},'查看原对话任务'));
            if(task.has_report)row.appendChild(el('a',{class:'btn btn-secondary btn-sm',href:`/api/projects/${encodeURIComponent(pid)}/research-tasks/${encodeURIComponent(task.task_id)}/export`,download:''},'下载对应报告'));
            host.appendChild(row);
        }
        if(data.next_offset!==null) {
            const more=el('button',{class:'btn btn-secondary btn-sm',onclick:async()=>{
                more.disabled=true;
                try {
                    const next=await api('GET',`/api/projects/${encodeURIComponent(pid)}/evidence?offset=${data.next_offset}&limit=10`);
                    if(pid!==state.currentProjectId || !host.isConnected)return;
                    more.remove();renderSearchAudit(host,next.conversation_search,pid,true);
                }catch(e){more.disabled=false;showToast('审计记录读取失败，请重试。','error');}
            }},'查看更多检索记录');
            host.appendChild(more);
        }
    }

    function renderEvidenceCard(evData) {
        const stats = (evData && evData.stats) || { total: 0, by_source: {}, linked: 0 };
        const entries = (evData && evData.entries) || [];
        const bySource = stats.by_source || {};
        // 区分检索 vs 手动补录：检索抓取 = total - manual；检索已入库 = linked - manual（近似，
        // manual 条目 paper_id 均已回填）；手动补录 = manual
        const manual = Number(stats.manual || 0);
        const retrieved = Number(stats.retrieved || Math.max((stats.total || 0) - manual, 0));
        const srcBadges = Object.keys(bySource)
            .filter(src => src !== "manual")
            .map(src =>
                el("span", { class: `badge ev-src-badge ev-src-${src}`, text: `${src} ${bySource[src]}` }));
        if (manual > 0) {
            srcBadges.push(el("span", { class: "badge ev-src-badge ev-src-manual", text: `手动补录 ${manual}` }));
        }

        const card = el("div", { class: "card ev-card" }, [
            el("div", { class: "ev-head" }, [
                el("span", { class: "ev-title" }, "旧流程来源关联记录"),
                el("span", { class: "badge badge-info", text: `检索抓取 ${retrieved} 条` }),
                el("span", { class: "badge badge-success", text: `检索已入库 ${Math.max(Number(stats.linked || 0) - manual, 0)} 条` }),
                el("span", { class: "badge badge-warning", text: `未入库 ${stats.unlinked || 0} 条` }),
                ...srcBadges,
            ]),
            el("div", { class: "ev-desc" },
                "这里保留旧流程已保存的来源关联记录，不代表当前对话任务的检索覆盖范围。入库、纳入综合和正式采纳是不同操作；当前任务的实际来源、筛选和排除统计请在对话任务卡查看。技术定位信息仅用于核对原记录。" +
                (manual > 0
                    ? "另有 " + manual + " 条为手动补录/上传文献（不属检索，单独计数）。"
                    : "")),
        ]);

        if (!entries.length) {
            card.appendChild(el("div", { class: "ev-empty" },
                "暂无历史来源关联记录。可返回科研对话查看当前任务，不会自动启动检索。"));
            return card;
        }

        // 按子问题分组展示
        const groups = {};
        entries.forEach(e => {
            const key = e.subquery || "(未标注子问题)";
            (groups[key] = groups[key] || []).push(e);
        });
        const body = el("div", { class: "ev-groups" });
        Object.keys(groups).forEach(sq => {
            const items = groups[sq];
            // 配额构成：同一子问题按数据源统计（Sciverse N + arXiv N），
            // 说明「命中条数」来自每条子问题的固定抓取配额，而非相关性筛剩下的数量。
            const bySrc = {};
            items.forEach(e => { bySrc[e.source] = (bySrc[e.source] || 0) + 1; });
            const srcParts = Object.keys(bySrc).map(s =>
                `${s === "sciverse" ? "Sciverse" : s} ${bySrc[s]}`);
            const linkedInSq = items.filter(e => e.paper_id).length;
            const group = el("div", { class: "ev-group" }, [
                el("div", { class: "ev-group-head" }, [
                    el("span", { class: "ev-group-q", text: sq }),
                    el("span", { class: "ev-group-count", text:
                        `抓取 ${items.length} 条（${srcParts.join(" + ")}）` }),
                    el("span", { class: "ev-group-linked", text:
                        `关联入库 ${linkedInSq} 条` }),
                ]),
            ]);
            const listEl = el("div", { class: "ev-group-body" });
            items.slice(0, 60).forEach(e => listEl.appendChild(renderEvidenceEntry(e)));
            group.appendChild(listEl);
            body.appendChild(group);
        });
        card.appendChild(body);
        return card;
    }

    function renderEvidenceEntry(e) {
        const src = e.source || "?";
        // 主行：来源徽章 + 标题（固定单行省略，完整标题放 title 悬停可看）
        const titleText = e.title || "(无标题)";
        const main = el("div", { class: "ev-entry-main" }, [
            el("span", { class: `badge ev-src-badge ev-src-${src}`, text: src }),
            el("span", { class: "ev-entry-title", title: titleText, text: titleText }),
        ]);
        // 元信息行：证据分 / doc_id / 偏移 / 关联依据 整齐排一行
        const meta = el("div", { class: "ev-entry-meta" });
        if (src === "sciverse") {
            meta.appendChild(el("span", { class: "ev-entry-score",
                text: `证据分 ${Number(e.evidence_score || 0).toFixed(2)}` }));
            meta.appendChild(el("span", { class: "mono ev-entry-id",
                text: `doc:${e.external_id || "-"}` }));
            if (Number(e.offset || 0) > 0) {
                meta.appendChild(el("span", { class: "ev-entry-offset",
                    text: `@偏移${e.offset}` }));
            }
        } else {
            const eid = e.external_id || "";
            if (eid) meta.appendChild(el("span", { class: "mono ev-entry-id", text: eid }));
        }
        // 关联依据（量化可审计）：match_type 说明为何关联该论文；
        // paper_id 为空 → 检索命中但未关联（被 filter 相关性筛选/去重剔除）。
        if (e.paper_id) {
            const reason = e.match_type || "证据来源关联";
            meta.appendChild(el("span", { class: "ev-entry-linked",
                text: `已入库 · ${reason}` }));
            if (Number(e.paper_relevance || 0) > 0) {
                meta.appendChild(el("span", { class: "ev-entry-rel",
                    text: `相关度 ${Number(e.paper_relevance).toFixed(2)}` }));
            }
        } else {
            meta.appendChild(el("span", { class: "ev-entry-unlinked",
                text: "未入库 · 被筛选/去重剔除" }));
        }
        return el("div", { class: "ev-entry" }, [main, meta]);
    }

    function renderPaperItem(p) {
        const item = el("div", { class: "list-item" });
        item.setAttribute("data-paper-id", p.paper_id || "");
        const headChildren = [
            el("span", { class: "list-item-title", text: p.title || "(无标题)" }),
            p.year ? el("span", { class: "badge badge-info badge-stage", text: String(p.year) }) : null,
            p.venue ? el("span", { class: "badge badge-neutral", text: p.venue }) : null,
        ];

        // 期刊影响因子徽章（IF > 0 才显示）
        const ifVal = Number(p.impact_factor) || 0;
        if (ifVal > 0) {
            const ifCls = ifVal >= 15 ? "badge-if-high" : (ifVal >= 8 ? "badge-if-mid" : "badge-if-low");
            headChildren.push(el("span", {
                class: `badge ${ifCls}`,
                title: p.cas_subcategory || "",
                text: `IF ${ifVal.toFixed(1)}`,
            }));
        }
        // 中科院分区标签
        if (p.cas_zone) {
            const zoneCls = p.cas_zone === "1" ? "badge-cas-1" : (p.cas_zone === "2" ? "badge-cas-2" : "badge-cas-low");
            const zoneText = p.is_top_journal ? `${p.cas_zone}区 Top` : `${p.cas_zone}区`;
            headChildren.push(el("span", {
                class: `badge ${zoneCls}`,
                title: p.cas_subcategory || "",
                text: zoneText,
            }));
        }

        // 数据源徽章（三源融合，标注证据分）
        if (p.source === "sciverse") {
            headChildren.push(el("span", {
                class: "badge ev-src-badge ev-src-sciverse",
                text: `SC ${Number(p.evidence_score || 0).toFixed(2)}`,
            }));
        } else if (p.source) {
            headChildren.push(el("span", {
                class: `badge ev-src-badge ev-src-${p.source}`,
                text: p.source,
            }));
        }
        // 主题相关度徽章（filter 阶段量化打分 0~1，可点击展开查看依据）
        if (Number(p.relevance_score || 0) > 0) {
            const rs = Number(p.relevance_score);
            const cls = rs >= 0.7 ? "ev-entry-rel" : (rs >= 0.5 ? "ev-entry-rel-mid" : "ev-entry-rel-low");
            headChildren.push(el("span", {
                class: `badge ${cls}`,
                title: p.relevance_reason || "",
                text: `相关度 ${rs.toFixed(2)}`,
            }));
        }
        headChildren.push(el("span", { class: "list-item-meta", text:
            (p.authors || []).slice(0, 2).join(", ") + ((p.authors || []).length > 2 ? " et al." : "") }));

        // 直链可下载；DOI/标题仅可尝试寻找开放获取版本，不能保证付费墙论文。
        const pdfUrl = p.pdf_url || (p.arxiv_id ? `https://arxiv.org/pdf/${p.arxiv_id}.pdf` : "");
        const isDoiFallback = pdfUrl && pdfUrl.includes("doi.org") && !p.arxiv_id;
        if (p.pdf_path || pdfUrl || p.doi || (p.title && p.source_stage !== "upload")) {
            const uncertain = !p.pdf_path && (!pdfUrl || isDoiFallback);
            const pdfBtn = el("button", {
                class: "btn btn-sm btn-pdf-download",
                title: uncertain ? "尝试查找开放获取 PDF；付费墙论文可能无法下载" : "下载 PDF（后端代理保存到本地）",
                text: uncertain ? "尝试 PDF ↓" : "PDF ↓",
            });
            pdfBtn.addEventListener("click", async (e) => {
                e.stopPropagation();
                pdfBtn.disabled = true;
                pdfBtn.textContent = "下载中…";
                try {
                    await downloadPaperPdf(state.currentProjectId, p.paper_id);
                } catch (err) {
                    alert(`PDF 下载失败：${err.message || err}`);
                } finally {
                    pdfBtn.disabled = false;
                    pdfBtn.textContent = uncertain ? "尝试 PDF ↓" : "PDF ↓";
                }
            });
            headChildren.push(pdfBtn);
        }

        const head = el("div", { class: "list-item-head" }, headChildren);
        head.querySelectorAll('.badge-if-high,.badge-if-mid,.badge-if-low,.badge-cas-1,.badge-cas-2,.badge-cas-low,.ev-src-sciverse,.ev-entry-rel,.ev-entry-rel-mid,.ev-entry-rel-low').forEach(n=>n.remove());
        item.appendChild(head);
        item.appendChild(el('p',{class:'paper-relation'},p.ui_relation || p.relevance_reason || '与当前问题的关联尚待核对'));
        item.appendChild(el('p',{class:'muted small'},'阅读范围：'+(p.reading_scope || (p.abstract?'摘要与元信息':'仅元信息'))+(p.parse_error?' · 解析未完成，请查看提示':'')));
        const paperToggle=head.querySelector('.list-item-title');
        paperToggle.setAttribute('role','button');paperToggle.tabIndex=0;paperToggle.setAttribute('aria-label','查看资料：'+(p.title||'无标题'));paperToggle.setAttribute('aria-expanded','false');
        paperToggle.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();item.click();}};

        item.addEventListener("click", () => {
            const expanded = item.classList.toggle("expanded");
            paperToggle.setAttribute('aria-expanded',String(expanded));
            if (expanded && !item.querySelector(".list-item-body")) {
                const body = buildPaperBody(p);
                item.appendChild(body);
            }
        });
        return item;
    }

    // 异步下载论文 PDF（fetch 二进制 → blob → 触发浏览器下载）
    async function downloadPaperPdf(projectId, paperId) {
        const url = `/api/projects/${projectId}/papers/${paperId}/pdf`;
        const resp = await fetch(url);
        if (!resp.ok) {
            let msg = `HTTP ${resp.status}`;
            try {
                const data = await resp.json();
                if (data && data.detail) msg = data.detail;
            } catch (e) { /* ignore */ }
            throw new Error(msg);
        }
        const blob = await resp.blob();
        // 触发浏览器下载（用后端返回的文件名，兜底用 paperId）
        const cd = resp.headers.get("Content-Disposition") || "";
        const m = cd.match(/filename\*?=(?:UTF-8''|")?([^";]+)/i);
        const fname = m ? decodeURIComponent(m[1]) : `${paperId}.pdf`;
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = fname;
        document.body.appendChild(a);
        a.click();
        setTimeout(() => {
            URL.revokeObjectURL(a.href);
            a.remove();
        }, 1000);
    }

    // ===== 研究过程：复用原页面与知识库，不另建一套导航或复制数据 =====

    const RECORD_LABELS = {note:"灵感记录", question:"研究问题", plan:"研究方案", result:"实际结果", finding:"研究判断", outline:"写作提纲"};
    const RECORD_HINTS = {
        question:"描述具体场景、现有方法的限制、你想验证的问题。标明依据；尚未查证的创新点请写成假设。",
        plan:"写清数据、基线、改变的条件、评价指标、资源预算，以及什么结果支持或不支持设想。",
        result:"只填写实际运行的环境、设置、输出和问题。失败也可以记录；不要把预期效果填写成结果。",
        finding:"结合实际结果说明支持、反驳还是无法定论，同时列出局限。运行失败不等于设想被反驳。",
        outline:"按背景、问题、方法、结果和局限组织提纲。缺少实验证据的段落标注待补充，不生成虚构结论。",
    };

    function researchTabs(content, label, tabs, selected, select) {
        const bar = el("div", {class:"research-page-tabs", role:"tablist", "aria-label":label});
        tabs.forEach(([id,title]) => bar.appendChild(el("button", {
            class:"research-page-tab", role:"tab", "aria-selected":String(id===selected),
            onclick:() => select(id),
        }, title)));
        const panel = el("section", {role:"tabpanel", "aria-label":tabs.find(t=>t[0]===selected)[1]});
        content.append(bar,panel);
        return panel;
    }

    function renderQuestions(content, forcedTab) {
        const tab = forcedTab || state.questionTab;
        content.appendChild(el("div", {class:"page-header"}, [
            el("h2", {class:"page-title"}, "研究问题"),
            el("p", {class:"page-desc"}, "从文献和灵感中收敛一个可研究、可验证的问题。候选思路、文献疑问和论断证据放在一起查看，但不把它们直接当成已成立的创新。"),
        ]));
        const panel = researchTabs(content, "研究问题内容", [["questions","我的研究问题"],["gaps","文献中的待核实问题"],["evidence","论断与证据"]], tab, id=>{state.questionTab=id;setActivePage("gaps");});
        if (tab === "evidence") renderClaims(panel);
        else if (tab === "gaps") renderGaps(panel);
        else {
            const records = el("section"); panel.appendChild(records);
            renderResearchRecords(records,["question"]);
            if(state.workflowCache?.projectId===state.currentProjectId || state.statusCache?.workflow) return;
            const ideas = el("div", {class:"card"}, [el("h3",{class:"card-title"},"已有候选思路"),el("p",{class:"muted small"},"保留原系统产生的思路；它们不是已确认的问题或创新，需先核查文献与可行性。")]);
            panel.appendChild(ideas);
            const pid=state.currentProjectId;
            api("GET",`/api/projects/${pid}/research-materials`).then(data=>{
                if (!data.ideas.length) ideas.appendChild(el("p",{class:"muted small"},"暂无候选思路，可以先从论文局限或灵感笔记整理。"));
                data.ideas.forEach(i=>ideas.appendChild(el("details",{class:"research-record"},[
                    el("summary",{},truncateText(i.text,90)),el("p",{class:"research-record-text"},i.text),
                    el("button",{class:"btn btn-secondary btn-sm",onclick:()=>discussRecord(pid,null,`请帮我审视这条候选思路，先问清楚研究场景和证据，不直接宣称创新成立：\n${i.text}`)},"与科研指导讨论"),
                ])));
            }).catch(e=>ideas.appendChild(el("p",{class:"text-danger"},`候选思路加载失败：${e.message}`)));
        }
    }

    function renderMethods(content, forcedTab) {
        const tab=forcedTab || state.methodTab;
        content.appendChild(el("div",{class:"page-header"},[el("h2",{class:"page-title"},"研究指南"),el("p",{class:"page-desc"},"保留原来的阅读和写作知识，按当前任务查阅，不作为必须逐项完成的任务节点。") ]));
        const panel=researchTabs(content,"研究指南内容",[["reading","论文阅读"],["writing","论文写作"]],tab,id=>{state.methodTab=id;setActivePage("methods");});
        if(tab==="writing") renderWritingMethod(panel); else renderReadingMethod(panel);
    }

    const RECORD_PAGES = {question:"gaps", plan:"experiments", result:"experiments", finding:"experiments", note:"notes", outline:"writing"};
    function readResearchFocus(pid) {
        if(state.workflowCache?.projectId===pid) return state.workflowCache.focus || {};
        try { const value=JSON.parse(localStorage.getItem(`sra.research-focus.${pid}`) || "{}"); return value && typeof value==="object" ? value : {}; }
        catch (_) { return {}; }
    }
    function rememberResearchFocus(pid,focus) {
        try { localStorage.setItem(`sra.research-focus.${pid}`,JSON.stringify(focus)); } catch (_) { /* 本次选择仍可用。 */ }
    }
    function recordFormIsDirty() {
        return !!document.querySelector('.research-record-form[data-dirty="true"], .project-brief-form[data-dirty="true"]');
    }
    function openResearchRecord(item) {
        if(item.kind==="note") {discussRecord(state.currentProjectId,item.item_id,`请围绕已保存的笔记「${item.title}」检查它与当前研究的关系。`);return;}
        const existing=document.querySelector(`[data-record-id="${CSS.escape(item.item_id)}"]`);
        if(existing) {existing.open=true;existing.scrollIntoView({block:"nearest"});return;}
        if(recordFormIsDirty()) {showToast("当前记录尚未保存，请先保存或取消输入再跳转。","warning");return;}
        state.pendingRecordId=item.item_id;
        if(item.kind==="question") state.questionTab="questions";
        setActivePage(RECORD_PAGES[item.kind] || "gaps");
    }
    function startResearchRecord(kind,relatedId=null,revision=null) {
        if(["result","finding","outline"].includes(kind)) {showToast("本轮止于研究方案；历史结果和稿件仅供查看、导出。","warning");return;}
        if(recordFormIsDirty()) {showToast("当前记录尚未保存，请先保存或取消输入。","warning");return;}
        state.recordEditorIntent={pid:state.currentProjectId,kind,relatedId,revision};
        if(kind==="question") state.questionTab="questions";
        setActivePage(RECORD_PAGES[kind] || "gaps");
    }
    function renderProjectManagement(content,workspaceData) {
        const pid=state.currentProjectId;
        if(!pid || !window.SRA_PROJECT_MANAGEMENT) return;
        const box=el("section",{class:"card project-management","aria-label":"项目管理"},el("p",{class:"muted small"},"正在整理项目目标与已有工作…"));
        content.appendChild(box);
        Promise.resolve(workspaceData).then(data=>{
            if(pid!==state.currentProjectId || !box.isConnected) return;
            const workspace=data.workspace, baseline={goal:"",background:"",resources:"",time_budget:"",...workspace.brief};
            box.replaceChildren(el("h3",{class:"card-title"},"项目管理"));
            const summary=el("div");box.appendChild(summary);
            const editor=el("details",{class:"project-brief-editor"},el("summary",{},"编辑目标与资源"));
            const form=el("form",{class:"project-brief-form"});
            const inputs={};
            const labels={goal:"研究目标",background:"已有基础",resources:"可用资源",time_budget:"时间安排"};
            const placeholders={goal:"例如：本学期完成一个范围明确的研究问题与对照验证",background:"已有论文、代码、知识或已完成的工作",resources:"设备、数据、可用代码，以及暂时无法取得的资源",time_budget:"例如：六周内完成，每周约八小时"};
            for(const field of Object.keys(labels)) {
                const input=el("textarea",{rows:field==="time_budget" ? 2 : 3,maxLength:field==="time_budget" ? 1000 : 2000,"aria-label":labels[field],placeholder:placeholders[field]});
                input.value=baseline[field];inputs[field]=input;
                form.appendChild(el("label",{},[labels[field],input]));
            }
            const draftKey=`sra.project-brief.draft.${pid}`;
            const notice=el("p",{class:"small",role:"status"});
            let restoredBaseChanged=false;
            try {
                const draft=JSON.parse(sessionStorage.getItem(draftKey) || "null");
                if(draft?.values && Object.keys(inputs).every(field=>typeof draft.values[field]==="string")) {
                    for(const field of Object.keys(inputs)) inputs[field].value=draft.values[field].slice(0,inputs[field].maxLength);
                    form.dataset.dirty="true";editor.open=true;
                    restoredBaseChanged=JSON.stringify(draft.baseBrief)!==JSON.stringify(baseline);
                    notice.textContent=restoredBaseChanged ? "已恢复未保存草稿；服务器上的项目约束也已变化，请逐项核对后再保存。" : "已恢复本次浏览器会话的草稿，尚未保存到项目。";
                }
            } catch (_) { /* 草稿不可用时仍可正常编辑。 */ }
            const values=()=>Object.fromEntries(Object.entries(inputs).map(([field,input])=>[field,input.value.trim()]));
            const acknowledgment=el("input",{type:"checkbox","aria-label":"确认约束变更需复核"});
            const requireReview=workspace.items.some(item=>item.status==="active" && item.kind!=="note");
            acknowledgment.required=(requireReview || restoredBaseChanged) && JSON.stringify(values())!==JSON.stringify(baseline);
            const warning=el("label",{class:"project-brief-confirm"},[acknowledgment,requireReview ? "我已核对约束变更。保存后，现有研究记录会标为待复核，旧内容不会删除，实验不会自动重跑。" : "我已核对服务器最新约束，确认以本次输入保存。"]);
            warning.hidden=!(requireReview || restoredBaseChanged);
            const submit=el("button",{type:"submit",class:"btn"},"保存项目约束");
            const cancel=el("button",{type:"button",class:"btn btn-secondary",onclick:()=>{
                for(const field of Object.keys(inputs)) inputs[field].value=baseline[field];
                form.dataset.dirty="false";acknowledgment.checked=false;
                acknowledgment.required=false;
                try {sessionStorage.removeItem(draftKey);} catch (_) {}
                notice.textContent="已放弃本次未保存修改。";editor.open=false;
                if(state.currentPage==="progress") renderPage(true);
            }},"取消本次修改");
            form.addEventListener("input",()=>{
                acknowledgment.required=(requireReview || restoredBaseChanged) && JSON.stringify(values())!==JSON.stringify(baseline);
                if(JSON.stringify(values())===JSON.stringify(baseline)) {
                    form.dataset.dirty="false";try {sessionStorage.removeItem(draftKey);} catch (_) {}
                } else {
                    form.dataset.dirty="true";
                    try {sessionStorage.setItem(draftKey,JSON.stringify({values:values(),baseBrief:baseline}));} catch (_) {}
                }
            });
            form.addEventListener("submit",async event=>{
                event.preventDefault();
                const brief=values();
                if(JSON.stringify(brief)===JSON.stringify(baseline)) {notice.textContent="内容没有变化，无需保存；不会触发复核。";return;}
                if((requireReview || restoredBaseChanged) && !acknowledgment.checked) {notice.textContent="请先确认约束变更对已有记录的影响。";return;}
                submit.disabled=true;notice.textContent="正在保存项目约束…";
                try {
                    await api("PUT",`/api/projects/${pid}/workspace/brief`,{expected_revision:workspace.revision,brief});
                    form.dataset.dirty="false";try {sessionStorage.removeItem(draftKey);} catch (_) {}
                    window.dispatchEvent(new CustomEvent("sra:records-changed",{detail:{projectId:pid,source:"project-management"}}));
                    if(pid===state.currentProjectId) {showToast("项目约束已保存；请留意需要复核的记录。","success");renderPage(true);}
                } catch(error) {
                    notice.textContent=`保存失败：${error.message}。输入已保留在本次浏览器会话中；刷新后请核对最新记录再提交。`;
                    submit.disabled=false;
                }
            });
            form.append(warning,notice,el("div",{class:"btn-row"},[submit,cancel]));
            editor.appendChild(el("p",{class:"muted small"},"编辑期间不会被后台轮询覆盖。首次补填约束也遵循原有复核规则；不需要改变研究条件时可以保持原值。"));
            editor.appendChild(form);box.appendChild(editor);
            let lastManagementSignature=null;
            function paintSummary(force=false) {
                const status=state.statusCache?.project_id===pid ? state.statusCache : {};
                const focus=readResearchFocus(pid);
                const unified=status.workflow && status.workflow.revision>=(data.workflow?.revision || 0) ? status.workflow : data.workflow;
                const signature=JSON.stringify({status:status.status,error:status.error,experiments:status.counts?.experiments,focus,revision:workspace.revision,workflow:unified});
                if(!force && signature===lastManagementSignature) return;
                lastManagementSignature=signature;
                const info=window.SRA_PROJECT_MANAGEMENT.summarize(workspace,data.papers || [],status,focus,unified);
                summary.replaceChildren(el("p",{class:"project-goal"},baseline.goal || "尚未填写研究目标，可以先补充目标、时间与资源限制。"));
                const states=el("div",{class:"project-state-grid"},[
                    el("div",{},[el("span",{class:"muted small"},"科研推进状态"),el("strong",{},info.researchState)]),
                    el("div",{},[el("span",{class:"muted small"},"后台任务状态"),el("strong",{},info.systemState)]),
                ]);summary.appendChild(states);
                const resources=el("p",{class:"muted small"},`时间：${baseline.time_budget || "未填写"}；资源：${baseline.resources || "未填写"}`);summary.appendChild(resources);
                const inventory=info.inventory;
                const materials=el("details",{class:"project-materials"},el("summary",{},`已有材料 · ${inventory.papers} 篇论文 · ${inventory.plans} 条方案 · ${inventory.results} 条结果记录`));
                materials.append(el("p",{class:"muted small"},`研究问题 ${inventory.questions} 条；研究判断 ${inventory.findings} 条；待复核 ${inventory.needsReview} 条。结果记录由用户提交，不等于系统独立复算；论文入库不代表全文已读。`),el("p",{class:"muted small"},`任务来源库保留 ${inventory.legacyExperiments} 条实验记录；来源与关联记录不会相加当作两个独立实验。`));summary.appendChild(materials);
                const blockers=el("details",{class:"project-blockers"},el("summary",{},`当前阻碍与待核对事项 ${info.blockers.length} 项`));
                const list=el("ul");info.blockers.forEach(text=>list.appendChild(el("li",{},text)));
                blockers.appendChild(list);if(info.systemError) blockers.appendChild(el("p",{class:"text-danger small"},`另有后台错误：${info.systemError}。运行错误与科研设想被反驳不是同一回事。`));
                summary.append(el("p",{class:"project-current-blocker"},info.blockers[0] || "暂无自动识别的记录缺项，仍需人工核对证据与局限。"),blockers);
                const taskKey=`sra.project-tasks.hidden.${pid}`;
                let hidden=[];try {const value=JSON.parse(localStorage.getItem(taskKey)||"[]");if(Array.isArray(value)) hidden=value.filter(x=>typeof x==="string");} catch (_) {}
                const tasks=el("div",{class:"project-next-tasks"},el("h4",{},"下一步任务"));
                const visibleTasks=info.tasks.filter(task=>!hidden.includes(task.id));
                const moreTasks=el("details",{class:"project-more-tasks"},el("summary",{},`其他建议 ${Math.max(0,visibleTasks.length-1)} 项`));
                const compact=!!box.closest(".overview-work-grid");
                visibleTasks.forEach((task,index)=>{
                    const card=el("div",{class:"project-task"},[el("strong",{},task.title),el("p",{},task.text)]);
                    const go=()=>{
                        if(task.action==="brief") {editor.open=true;inputs.goal.focus();return;}
                        if(task.action==="papers") {setActivePage("papers");return;}
                        const next=info.flow.next;
                        if(next.action==="create") startResearchRecord(next.kind,next.relatedId);
                        else if(next.action==="revise") startResearchRecord(next.item.kind,null,next.item);
                        else if(next.action==="select") document.querySelector('.research-flow-card select')?.focus();
                        else if(next.action==="plan_ready") openResearchRecord(next.item);
                        else discussRecord(pid,info.flow.question?.item_id || null,`请核对当前研究方案的依据与局限：${next.text}`);
                    };
                    const discuss=()=>discussRecord(pid,info.flow.question?.item_id || null,`我想调整下一步任务「${task.title}」。原建议：${task.text}。项目目标：${baseline.goal || "待明确"}；时间：${baseline.time_budget || "待明确"}；资源：${baseline.resources || "待明确"}。请先讨论，不直接启动任务。`);
                    card.appendChild(el("div",{class:"btn-row"},[
                        el("button",{class:"btn btn-secondary btn-sm",onclick:go},"去处理"),
                        el("button",{class:"btn btn-secondary btn-sm",onclick:discuss},"讨论调整"),
                        el("button",{class:"project-muted-button",title:"仅在本机隐藏这条建议，不代表任务已完成",onclick:()=>{hidden.push(task.id);try {localStorage.setItem(taskKey,JSON.stringify(hidden));} catch (_) {}paintSummary(true);}},"暂不提醒"),
                    ]));(compact && index>0 ? moreTasks : tasks).appendChild(card);
                });
                if(compact && visibleTasks.length>1) tasks.appendChild(moreTasks);
                if(hidden.length) tasks.appendChild(el("button",{class:"project-muted-button",onclick:()=>{try {localStorage.removeItem(taskKey);} catch (_) {}paintSummary(true);}},"恢复本机隐藏的建议"));
                tasks.appendChild(el("p",{class:"muted small"},compact ? "建议操作不会自动执行实验，也不会标记研究完成。" : "处理按钮只打开现有入口，不自动保存记录或调用模型；暂不提醒只是本机偏好，不会消除阻碍或标记研究已完成。"));summary.appendChild(tasks);
            }
            box.refreshManagement=()=>paintSummary();
            paintSummary();
        }).catch(error=>{if(box.isConnected)box.replaceChildren(el("p",{class:"text-danger small"},`项目管理加载失败：${error.message}`));});
    }
    for(const eventName of ["sra:research-focus","sra:project-status"]) window.addEventListener(eventName,()=>{
        document.querySelectorAll('.project-management').forEach(box=>box.refreshManagement?.());
    });
    function renderResearchFlow(content,knownData=null) {
        const pid=state.currentProjectId;
        if(!pid || !window.SRA_RESEARCH_FLOW) return;
        const box=el("section",{class:"card research-flow-card","aria-label":"研究主线"},el("p",{class:"muted small"},"正在核对研究记录的关联…"));
        content.appendChild(box);
        let focus=readResearchFocus(pid);
        const load=knownData ? Promise.resolve(knownData) : api("GET",`/api/projects/${pid}/workspace?include_chat=false`);
        load.then(data=>{
            if(pid!==state.currentProjectId || !box.isConnected) return;
            if(data.workflow) focus=data.workspace.focus;
            async function choose(next) {
                if(!data.workflow) {focus=next;rememberResearchFocus(pid,focus);paint();return;}
                box.querySelectorAll("select,button").forEach(input=>input.disabled=true);
                try {
                    const updated=await api("PUT",`/api/projects/${pid}/workspace/focus`,{expected_revision:data.workspace.revision,focus:next});
                    if(pid!==state.currentProjectId || !box.isConnected) return;
                    data=updated;focus=updated.workspace.focus;rememberResearchFocus(pid,focus);
                    state.workflowCache={projectId:pid,...updated.workflow};
                    if(state.statusCache?.project_id===pid) state.statusCache.workflow=updated.workflow;
                    window.dispatchEvent(new CustomEvent("sra:records-changed",{detail:{projectId:pid,source:"focus"}}));
                } catch(error) {showToast("主线未切换："+error.message,"error");paint();}
            }
            function paint() {
                const flow=data.workflow?.flow || window.SRA_RESEARCH_FLOW.analyze(data.workspace,data.papers || [],focus);
                const compact=!!box.closest(".overview-work-grid");
                box.replaceChildren(el("h3",{class:"card-title"},"研究主线"),el("p",{class:"muted small"},"围绕同一问题关联、审阅与导出方案；上游材料变化时优先复核。本轮止于方案，不进入实验和写作，也不代表结论已验证。"));
                if(!compact) box.appendChild(el("p",{class:"muted small"},`当前记录主线：${flow.question?.title || "尚未选择问题"} → ${flow.plan?.title || "尚未选择方案"}；可用结果 ${flow.validResults.length} 条。原流程已有的文献、方案和稿件保留，无需为查看它们重复新建记录。`));
                const fields=el("div",{class:"research-flow-selectors"});
                const question=el("select",{"aria-label":"当前研究问题"},el("option",{value:""},flow.questions.length ? "请选择本次推进的问题" : "尚无已保存的问题"));
                flow.questions.forEach(item=>question.appendChild(el("option",{value:item.item_id},item.title+(item.needs_review ? "（待复核）" : ""))));
                question.value=flow.question?.item_id || "";
                question.onchange=()=>{
                    if(recordFormIsDirty()) {question.value=flow.question?.item_id || "";showToast("请先保存当前记录，再切换研究问题。","warning");return;}
                    choose({question:question.value,plan:""});
                };
                fields.appendChild(el("label",{},["当前研究问题",question]));
                if(flow.plans.length>1) {
                    const plan=el("select",{"aria-label":"当前研究方案"},el("option",{value:""},"请选择当前方案，避免混用不同实验条件"));
                    flow.plans.forEach(item=>plan.appendChild(el("option",{value:item.item_id},item.title+(item.needs_review ? "（待复核）" : ""))));
                    plan.value=flow.plan?.item_id || "";
                    plan.onchange=()=>{
                        if(recordFormIsDirty()) {plan.value=flow.plan?.item_id || "";showToast("请先保存当前记录，再切换研究方案。","warning");return;}
                        choose({question:flow.question.item_id,plan:plan.value});
                    };
                    fields.appendChild(el("label",{},["当前研究方案",plan]));
                }
                box.appendChild(fields);
                const steps=el("ol",{class:"research-flow-steps","aria-label":"研究记录衔接"});
                flow.steps.forEach(step=>steps.appendChild(el("li",{class:step.count ? "has-record" : "missing-record"},[el("span",{class:"small"},step.label),el("strong",{},step.state)])));
                box.appendChild(steps);
                const next=el("div",{class:"research-flow-next"},[el("strong",{},"建议下一步"),el("p",{},flow.next.text)]);
                if(flow.next.action==="create") next.appendChild(el("button",{class:"btn btn-secondary btn-sm",onclick:()=>startResearchRecord(flow.next.kind,flow.next.relatedId)},`补充${RECORD_LABELS[flow.next.kind]}`));
                if(flow.next.action==="revise") next.appendChild(el("button",{class:"btn btn-secondary btn-sm",onclick:()=>startResearchRecord(flow.next.item.kind,null,flow.next.item)},"核对并修订记录"));
                if(flow.next.action==="plan_ready") next.appendChild(el("button",{class:"btn btn-secondary btn-sm",onclick:()=>openResearchRecord(flow.plan)},"审阅当前方案"));
                if(flow.question) next.appendChild(el("button",{class:"btn btn-secondary btn-sm",onclick:()=>discussRecord(pid,flow.question.item_id,`请围绕「${flow.question.title}」检查研究主线。当前记录状态：${flow.steps.map(x=>x.label+"："+x.state).join("；")}。${flow.next.text} 请区分已有证据、待验证设想和用户提交的结果，不启动实验。`)},"讨论当前问题"));
                box.appendChild(next);
                flow.issues.forEach(text=>box.appendChild(el("p",{class:"small text-warning"},text)));
                const links=el("div",{class:"research-flow-links"});
                [flow.question,flow.plan,...flow.validResults,...flow.findings].filter(Boolean).forEach(item=>links.appendChild(el("button",{class:"research-record-link",onclick:()=>openResearchRecord(item)},`${RECORD_LABELS[item.kind]}：${item.title}`)));
                box.appendChild(links);
                const pending=data.workspace.items.filter(item=>item.status==="active" && item.needs_review);
                if(pending.length) {
                    const review=el("details",{class:"research-flow-review"},el("summary",{},`待复核记录 ${pending.length} 条（未计作已衔接的工作）`));
                    pending.forEach(item=>review.appendChild(el("button",{class:"research-record-link",onclick:()=>openResearchRecord(item)},`${RECORD_LABELS[item.kind]}：${item.title}`)));
                    box.appendChild(review);
                }
                if(!data.workspace.items.length && !compact) box.appendChild(el("p",{class:"muted small"},"已有论文、方法与任务保留；可在下方直接关联，确认后纳入同一主线，不会自动改成已验证。"));
                const signature=`${pid}:${flow.question?.item_id || ""}`;
                if(state.publishedResearchFocus!==signature) {
                    state.publishedResearchFocus=signature;
                    window.dispatchEvent(new CustomEvent("sra:research-focus",{detail:{projectId:pid,itemId:flow.question?.item_id || null,title:flow.question?.title || ""}}));
                }
            }
            paint();
        }).catch(error=>{if(box.isConnected) box.replaceChildren(el("p",{class:"text-danger small"},`研究主线加载失败：${error.message}`),el("button",{class:"btn btn-secondary btn-sm",onclick:()=>{box.remove();renderResearchFlow(content);}},"重试研究主线"));});
    }

    function discussRecord(pid,itemId,message) {
        window.dispatchEvent(new CustomEvent("sra:discuss",{detail:{projectId:pid,itemId,message}}));
    }

    function renderAvailableMaterials(data,kinds,pid) {
        const box=el("section",{class:"card source-materials","aria-label":"已有材料关联"});
        const types=[];
        if(kinds.includes("question")) types.push("idea","gap");
        if(kinds.includes("plan")) types.push("method","experiment_plan");
        if(kinds.includes("result")) types.push("experiment_result");
        const materials=(data.workflow.materials || []).filter(source=>types.includes(source.kind));
        const group=el("details",{},el("summary",{},`已有材料 · ${materials.length} 项可核对来源`));
        group.open=materials.length>0 && (!data.workflow.flow.question || kinds.includes("plan") && !data.workflow.flow.plan);
        group.appendChild(el("p",{class:"muted small"},"原流程的思路、方法与实验记录保留原件。核对后可直接关联到当前主线；关联不会执行实验、确认创新或重复计算产物数量。"));
        if(!materials.length) group.appendChild(el("p",{class:"muted small"},"暂无对应材料。可以手动整理，或在任务进度中查看后台材料生成。"));
        const labels={idea:"候选思路",gap:"文献问题",method:"方法文档",experiment_plan:"实验任务",experiment_result:"结果记录"};
        materials.forEach(source=>{
            const item=el("details",{class:"source-material","data-source-kind":source.kind,"data-source-id":source.source_id},[
                el("summary",{},`${labels[source.kind]} · ${source.title}${source.linked_item_id ? source.needs_update ? "（来源已更新，待复核）" : "（已关联）" : ""}`),
                el("p",{class:"muted small"},source.warning || "已有生成材料，仍需核对依据与可实施性。"),
                el("pre",{class:"research-record-text"},source.content || "正文为空，请先补全来源材料。"),
            ]);
            if(source.truncated) item.appendChild(el("p",{class:"text-warning small"},"材料较长，此处及关联记录仅保留前 16000 字符；完整内容以原始文档为准，来源与版本仍保留。"));
            const kind=source.kind==="experiment_result" ? "result" : ["method","experiment_plan"].includes(source.kind) ? "plan" : "question";
            const related=kind==="plan" ? data.workflow.flow.question : kind==="result" ? data.workflow.flow.plan : null;
            const wrongTask=kind==="result" && (related?.source_ref?.kind!=="experiment_plan" || related.source_ref.source_id!==source.source_id);
            const linked=data.workspace.items.find(record=>record.item_id===source.linked_item_id);
            const viewLinked=linked && !source.needs_update && !linked.needs_review;
            const button=el("button",{class:"btn btn-secondary btn-sm"},viewLinked ? "查看已关联记录" : source.needs_update ? "核对后更新关联记录" : `确认关联为${RECORD_LABELS[kind]}`);
            button.disabled=!viewLinked && (kind==="result" || !source.available || source.importable===false || kind!=="question" && !related || related?.needs_review || wrongTask);
            if(button.disabled) item.appendChild(el("p",{class:"muted small"},wrongTask ? "请先选择同一实验任务对应的方案，再关联这条结果。" : kind!=="question" && !related ? "先在上方明确当前研究问题或方案。" : "来源不可用或主线待复核，请先处理。"));
            button.onclick=async()=>{
                if(viewLinked) {openResearchRecord(linked);return;}
                if(recordFormIsDirty()) {showToast("请先保存或取消当前输入，再关联已有材料。","warning");return;}
                if(!confirm(`确认将「${source.title}」关联为${RECORD_LABELS[kind]}？\n${related ? `关联到：${related.title}\n` : ''}原材料与历史版本保留，不会执行实验或将论断标为已验证。`)) return;
                button.disabled=true;
                try {
                    const updated=await api("POST",`/api/projects/${pid}/workspace/link-material`,{expected_revision:data.workspace.revision,kind:source.kind,source_id:source.source_id,fingerprint:source.fingerprint,related_id:related?.item_id || null,confirmed:true});
                    if(pid!==state.currentProjectId) return;
                    state.workspaceCounts=updated.counts;state.workspaceCountsProjectId=pid;
                    state.workflowCache={projectId:pid,...updated.workflow};
                    if(state.statusCache?.project_id===pid) state.statusCache.workflow=updated.workflow;
                    showToast("已关联到当前研究主线；原材料保留。","success");
                    window.dispatchEvent(new CustomEvent("sra:records-changed",{detail:{projectId:pid,source:"material-link"}}));
                } catch(error) {showToast("未关联："+error.message,"error");button.disabled=false;}
            };
            item.appendChild(button);group.appendChild(item);
        });
        box.appendChild(group);return box;
    }

    async function renderResearchRecords(content,kinds) {
        const pid=state.currentProjectId;
        content.replaceChildren(el("p",{class:"muted small"},"加载已保存的研究记录…"));
        try {
            const data=await api("GET",`/api/projects/${pid}/workspace?include_chat=false`);
            if(data.workflow && pid===state.currentProjectId) state.workflowCache={projectId:pid,...data.workflow};
            window.dispatchEvent(new CustomEvent("sra:workspace-loaded",{detail:{projectId:pid,counts:data.counts}}));
            clear(content);
            const workspace=data.workspace;
            if(!kinds.includes("outline")) renderResearchFlow(content,data);
            if(data.workflow && !kinds.includes("outline")) content.appendChild(renderAvailableMaterials(data,kinds,pid));
            const card=el("div",{class:"card research-records"},[
                el("h3",{class:"card-title"},"已保存的"+kinds.map(k=>RECORD_LABELS[k]).join(" · ")),
                el("p",{class:"muted small"},"这里汇集手动保存和对话中明确采纳的记录，未采纳的模型建议不会自动写入。保存不会启动后台任务，也不会把旧论断自动改成已验证。"),
            ]);
            const refresh=el("button",{class:"btn btn-secondary btn-sm",onclick:()=>renderResearchRecords(content,kinds)},"刷新记录");
            card.appendChild(refresh);
            const records=workspace.items.filter(i=>kinds.includes(i.kind));
            if(!records.length) card.appendChild(el("p",{class:"list-empty"},"还没有保存对应记录。可在下方手动整理，或先到科研对话主页讨论后再采纳。"));
            records.slice().reverse().forEach(item=>{
                const stale=item.status!=="active" || item.needs_review;
                const label=item.status!=="active" ? "历史版本" : item.needs_review ? "依据已变化，待复核" : item.source_ref ? "已关联已有材料，非独立核验" : "已保存，非独立核验";
                const body=el("div",{class:"research-record-text"});
                if(item.kind==='plan'){
                    const summary=item.plan?.sections.filter(s=>['objective','method','validation','conditions'].includes(s.key)).map(s=>s.statements.map(t=>t.text).join('；')).join('\n\n') || item.content;
                    body.appendChild(el('p',{},summary.slice(0,500)+(summary.length>500?'…':'')));
                    body.appendChild(el('button',{class:'btn btn-secondary btn-sm',onclick:()=>window.SRA_CONVERSATIONS.showPlan(pid,item.item_id)},'打开完整方案'));
                }else window.SRA_DOCUMENT.render(body,item.content);
                const d=el("details",{class:"research-record","data-record-id":item.item_id},[el("summary",{},`${RECORD_LABELS[item.kind]} · ${item.title}`),el("p",{class:stale ? "text-warning small" : "muted small"},label),body]);
                if(item.result_status) d.appendChild(el("p",{class:"small"},`运行状态：${({completed:"已完成",failed:"运行失败",inconclusive:"无法定论"})[item.result_status]}`));
                if(item.metrics && Object.keys(item.metrics).length) d.appendChild(el("pre",{class:"code-block"},JSON.stringify(item.metrics,null,2)));
                if(item.assessment) d.appendChild(el("p",{class:"small"},`用户判断：${({supports:"支持设想",refutes:"不支持设想",inconclusive:"无法定论"})[item.assessment]}`));
                if(item.related_ids.length) {
                    const related=el("div",{class:"research-flow-links small"},"关联记录：");
                    item.related_ids.forEach(id=>{const target=workspace.items.find(x=>x.item_id===id);related.appendChild(target ? el("button",{class:"research-record-link",onclick:()=>openResearchRecord(target)},`${RECORD_LABELS[target.kind]}：${target.title}`) : el("span",{class:"text-warning"},`${id}（记录缺失）`));});d.appendChild(related);
                }
                if(item.paper_ids.length) {
                    const sources=el("div",{class:"research-flow-links small"},"参考文献：");
                    item.paper_ids.forEach(id=>sources.appendChild(el("button",{class:"research-record-link",onclick:()=>window.__sraGoPaper(id)},data.papers.find(x=>x.paper_id===id)?.title || `${id}（请核对来源）`)));d.appendChild(sources);
                }
                d.appendChild(el("button",{class:"btn btn-secondary btn-sm",onclick:()=>discussRecord(pid,item.item_id,`请结合已保存的「${item.title}」帮我检查依据和下一步。`)},"讨论这条记录"));
                if(item.status==="active" && ["note","question","plan"].includes(item.kind)) d.appendChild(el("button",{class:"btn btn-secondary btn-sm",onclick:()=>startResearchRecord(item.kind,null,item)},"修订这条记录"));
                card.appendChild(d);
            });
            const editor=el("details",{class:"research-record-editor"});
            editor.appendChild(el("summary",{},"手动新增研究记录"));
            const form=el("form",{class:"research-record-form"});
            const writableKinds=kinds.filter(k=>["note","question","plan"].includes(k));
            const type=el("select",{"aria-label":"记录类型"},writableKinds.map(k=>el("option",{value:k},RECORD_LABELS[k])));
            const hint=el("p",{class:"muted small"});
            const title=el("input",{type:"text",required:true,maxLength:200,"aria-label":"记录标题",placeholder:"记录标题"});
            const body=el("textarea",{required:true,maxLength:16000,rows:5,"aria-label":"记录内容"});
            const refs=el("select",{"aria-label":"关联记录"});
            const refsField=el("label",{},["关联已有记录",refs]);
            const run=el("select",{"aria-label":"运行状态"},[["completed","已完成（需填写实际指标）"],["failed","运行失败"],["inconclusive","无法定论"]].map(([value,text])=>el("option",{value},text)));
            const runField=el("label",{},["运行状态",run]);
            const metrics=el("textarea",{rows:2,"aria-label":"实际指标",placeholder:'实际指标，JSON 格式，例如 {"accuracy": 0.71}；失败可留空'});
            const assessment=el("select",{"aria-label":"支持程度"},[["inconclusive","无法定论"],["supports","支持设想"],["refutes","不支持设想"]].map(([value,text])=>el("option",{value},text)));
            const assessmentField=el("label",{},["结果对设想的支持程度",assessment]);
            const sources=el("details",{class:"research-record-sources"},[el("summary",{},"关联文献（可选）")]);
            data.papers.forEach(p=>sources.appendChild(el("label",{},[el("input",{type:"checkbox",value:p.paper_id,name:"paper"}),p.title])));
            if(!data.papers.length) sources.appendChild(el("p",{class:"muted small"},"当前文献库为空，保存的问题只能视为待查证想法。"));
            function chooseType() {
                const kind=type.value;
                hint.textContent=RECORD_HINTS[kind];body.placeholder=RECORD_HINTS[kind];
                const requiredKind=({plan:"question",result:"plan",finding:"result"})[kind];
                const options=workspace.items.filter(i=>i.status==="active" && !i.needs_review && (requiredKind ? i.kind===requiredKind : kind==="outline"));
                refs.replaceChildren(el("option",{value:""},requiredKind ? "请选择一个已保存的关联记录" : "不关联"));
                options.forEach(i=>refs.appendChild(el("option",{value:i.item_id},`${RECORD_LABELS[i.kind] || "记录"}：${i.title}`)));
                refs.required=!!requiredKind;refsField.hidden=!requiredKind && kind!=="outline";
                runField.hidden=metrics.hidden=kind!=="result";assessmentField.hidden=kind!=="finding";
            }
            type.addEventListener("change",chooseType);chooseType();
            const message=el("p",{class:"small",role:"status"});
            const submit=el("button",{type:"submit",class:"btn"},"保存记录");
            const intent=state.recordEditorIntent;
            const revision=intent?.pid===pid && kinds.includes(intent.kind) ? intent.revision : null;
            if(intent?.pid===pid && kinds.includes(intent.kind)) {
                state.recordEditorIntent=null;editor.open=true;type.value=intent.kind;chooseType();
                if(intent.relatedId && [...refs.options].some(option=>option.value===intent.relatedId)) refs.value=intent.relatedId;
                if(revision) {
                    title.value=revision.title;body.value=revision.content;
                    if(revision.related_ids.length===1 && [...refs.options].some(option=>option.value===revision.related_ids[0])) refs.value=revision.related_ids[0];
                    sources.querySelectorAll('input[name="paper"]').forEach(input=>{input.checked=revision.paper_ids.includes(input.value);});
                    type.disabled=true;submit.textContent="保存修订版本";
                    editor.querySelector("summary").textContent="修订研究记录（保留旧版本）";
                    hint.textContent+=" 修订会保留旧记录，并把依赖旧版本的下游记录标为待复核；历史实验指标不自动复制。";
                    if(revision.result_status) run.value=revision.result_status;
                    const acknowledge=el("input",{type:"checkbox",required:true,"aria-label":"已核对修订依据"});
                    form.appendChild(el("label",{class:"research-revision-confirm"},[acknowledge,"我已核对本次修订的依据和关联，知道下游记录需要重新复核。"]));
                }
            }
            form.append(type,hint,title,body,refsField,runField,metrics,assessmentField,sources,message,submit);
            form.addEventListener("input",()=>{form.dataset.dirty="true";});
            form.addEventListener("change",()=>{form.dataset.dirty="true";});
            form.addEventListener("submit",async event=>{
                event.preventDefault();submit.disabled=true;message.textContent="正在保存…";
                try {
                    const item={kind:type.value,title:title.value.trim(),content:body.value.trim(),paper_ids:Array.from(form.querySelectorAll('input[name="paper"]:checked')).map(n=>n.value),related_ids:refs.value ? [refs.value] : []};
                    if(type.value==="result") {item.result_status=run.value;item.metrics=metrics.value.trim() ? JSON.parse(metrics.value) : {};}
                    if(type.value==="finding") item.assessment=assessment.value;
                    if(revision) item.supersedes_id=revision.item_id;
                    const saved=await api("POST",`/api/projects/${pid}/workspace/items`,{expected_revision:workspace.revision,item});
                    if(revision?.kind==="question") {
                        const focus=readResearchFocus(pid);
                        if(focus.question===revision.item_id || workspace.items.filter(i=>i.kind==="question" && i.status==="active").length===1) rememberResearchFocus(pid,{question:saved.workspace.items.at(-1).item_id,plan:""});
                    }
                    form.dataset.dirty="false";
                    window.dispatchEvent(new CustomEvent("sra:records-changed",{detail:{projectId:pid,source:"form"}}));
                    if(state.currentProjectId===pid && content.isConnected) renderResearchRecords(content,kinds);
                } catch(e) {message.textContent=`保存失败：${e.message}。输入已保留；版本冲突时请先复制输入，刷新记录后核对再提交。`;submit.disabled=false;}
            });
            editor.appendChild(form);
            if(writableKinds.length) card.appendChild(editor);
            else card.appendChild(el("p",{class:"muted small"},"历史记录可查看、导出；本轮不新增或修订结果、研究判断和写作记录。"));
            content.appendChild(card);
            if(state.pendingRecordId) {
                const target=content.querySelector(`[data-record-id="${CSS.escape(state.pendingRecordId)}"]`);
                if(target) {state.pendingRecordId=null;target.open=true;target.scrollIntoView({block:"nearest"});}
            }
        } catch(e) {
            content.replaceChildren(el("div",{class:"status-banner danger"},`研究记录加载失败：${e.message}`),el("button",{class:"btn btn-secondary",onclick:()=>renderResearchRecords(content,kinds)},"重试加载记录"));
        }
    }

    window.addEventListener("sra:records-changed",event=>{
        if(event.detail.projectId!==state.currentProjectId || ["form","project-management"].includes(event.detail.source)) return;
        // Central conversations refresh shared materials in place. Rebuilding
        // the whole dashboard would close the drawer and discard its notice.
        if(state.currentPage==="dashboard" && window.SRA_CONVERSATIONS) return;
        if(document.querySelector('.research-record-form[data-dirty="true"], .project-brief-form[data-dirty="true"]')) {showToast("研究记录已更新，可保存当前输入后刷新查看。","success");return;}
        if(["dashboard","progress","gaps","experiments","writing"].includes(state.currentPage)) renderPage(true);
    });

    function renderWriting(content) {
        const pid=state.currentProjectId;
        content.appendChild(el("div",{class:"page-header"},[el("h2",{class:"page-title"},"历史材料"),el("p",{class:"page-desc"},"历史稿件、提纲和版本保留查看与导出。本轮产品止于研究方案，不新增稿件或写作记录。") ]));
        const row=el("div",{class:"btn-row mb-12"},[
            el("button",{class:"btn btn-secondary",onclick:()=>setActivePage("writing-method")},"查看写作指南"),
            el("a",{class:"btn btn-secondary",href:`${SRA_API_BASE}/api/projects/${pid}/workspace/export?format=md`,download:"research-records.md"},"导出研究记录（Markdown）"),
            el("a",{class:"btn btn-secondary",href:`${SRA_API_BASE}/api/projects/${pid}/workspace/export?format=json`,download:"research-records.json"},"导出记录（JSON）"),
        ]);content.appendChild(row);
        const records=el("section");content.appendChild(records);renderResearchRecords(records,["outline"]);
        const artifacts=el("div",{class:"card"},[el("h3",{class:"card-title"},"已有稿件与修改意见"),el("p",{class:"muted small"},"读取原流程实际保存的最新产物，不会自动生成论文。新提纲不会自动同步覆盖旧稿件，使用前请核对版本与证据。")]);content.appendChild(artifacts);
        api("GET",`/api/projects/${pid}/research-materials`).then(data=>{
            if(!data.artifacts.length) artifacts.appendChild(el("p",{class:"list-empty"},"暂无已保存的稿件或修改意见。可以先保存写作提纲，待证据充分后再整理正文。"));
            data.artifacts.forEach(a=>{
                const item=el("details",{class:"research-record"},[el("summary",{},`${a.artifact_type==="paper_draft" ? "稿件" : "修改意见"} · ${a.title} · v${a.version}`)]);
                const body=el("div",{class:"research-record-text"});
                if(a.available) window.SRA_DOCUMENT.render(body,a.content);
                item.appendChild(a.available ? body : el("p",{class:"text-warning"},a.issue));
                if(a.truncated) item.appendChild(el("p",{class:"muted small"},"正文较长，这里展示前 20 万字符。完整文件可通过原有产物下载查看。"));
                artifacts.appendChild(item);
            });
        }).catch(e=>artifacts.appendChild(el("p",{class:"text-danger"},`稿件加载失败：${e.message}`)));
    }

    // ===== 4. 论断与证据（保留历史 claims 深链，归入研究问题） =====

    let claimsFilter = "all";

    async function renderClaims(content) {
        content.appendChild(el("div", { class: "loading" }, "加载中…"));
        try {
            const data = await api("GET", `/api/projects/${state.currentProjectId}/claims`);
            clear(content);
            content.appendChild(el("div", { class: "page-header" }, [
                el("h3", { class: "card-title" }, "论断与证据"),
                el("p", { class: "page-desc" }, `共 ${data.claims.length} 条原系统论断。用于核对来源、冲突和实验关联；历史验证状态不等于独立科学核验，也不保证创新成立。`),
            ]));

            // 筛选条
            const statuses = ["all", "draft", "evidence_linked", "verified", "refuted", "superseded"];
            const bar = el("div", { class: "filter-bar" });
            bar.appendChild(el("span", { class: "filter-label", text: "状态筛选：" }));
            statuses.forEach(s => {
                const chip = el("span", {
                    class: "filter-chip" + (claimsFilter === s ? " active" : ""),
                    text: ({all:"全部", draft:"草稿", evidence_linked:"已关联证据", verified:"历史标记：已验证", refuted:"已反驳", superseded:"已替换"})[s] || s,
                    onclick: () => {
                        claimsFilter = s;
                        renderPage();
                    },
                });
                bar.appendChild(chip);
            });
            content.appendChild(bar);

            const filtered = claimsFilter === "all"
                ? data.claims
                : data.claims.filter(c => c.status === claimsFilter);

            if (!filtered.length) {
                content.appendChild(el("div", { class: "list-empty" }, "暂无符合条件的论断。先明确问题和研究依据，不需要为了推进流程而凑论断。"));
                return;
            }
            const list = el("div", { class: "list" });
            filtered.forEach(c => list.appendChild(renderClaimItem(c)));
            content.appendChild(list);
        } catch (e) {
            clear(content);
            content.appendChild(el("div", { class: "status-banner danger" },
                `加载失败：${escapeHtml(e.message)}`));
        }
    }

    // 可复用的证据列表构建器：返回 DOM 节点
    // refs: [{type:"paper"/"experiment", id:"...", chunk_id?:"..."}, ...] 或 [string, ...]
    function buildEvidenceList(refs) {
        const evList = el("div", { class: "evidence-list" });
        (refs || []).forEach(ref => {
            const refObj = typeof ref === "string" ? { id: ref, type: "paper" } : ref;
            const refType = refObj.type || "paper";
            const refId = refObj.id || refObj.paper_id || "?";
            const refChunk = refObj.chunk_id || "";
            const typeIcon = refType === "paper" ? "📄" : refType === "experiment" ? "🔬" : "📌";
            const evItem = el("div", { class: "evidence-item" });
            evItem.appendChild(el("span", { class: "evidence-icon", text: typeIcon }));
            evItem.appendChild(el("span", { class: "evidence-type", text: refType }));
            if (refType === "paper") {
                // 优先展示论文标题（可读），id 作为副信息
                const refTitle = refObj.title || refId;
                const link = el("a", {
                    class: "evidence-link",
                    text: refTitle,
                    title: `在文献库查看来源 (${refId})`,
                    onclick: (e) => {
                        e.stopPropagation();
                        state.pendingPaperId = refId;
                        setActivePage("papers");
                    },
                });
                link.style.cursor = "pointer";
                link.style.color = "var(--color-primary)";
                link.style.textDecoration = "underline";
                evItem.appendChild(link);
                if (refObj.title && refTitle !== refId) {
                    evItem.appendChild(el("span", {
                        class: "evidence-id small muted mono",
                        text: refId,
                    }));
                }
                // 溯源片段（可悬停查看）
                if (refObj.snippet) {
                    const snip = el("span", {
                        class: "evidence-snippet small muted",
                        text: `「${refObj.snippet.slice(0, 120)}${refObj.snippet.length > 120 ? "…" : ""}」`,
                    });
                    evItem.appendChild(snip);
                }
            } else if (refType === "experiment") {
                const link = el("a", {
                    class: "evidence-link",
                    text: refId,
                    title: "点击跳转到实验列表",
                    onclick: (e) => {
                        e.stopPropagation();
                        setActivePage("experiments");
                    },
                });
                link.style.cursor = "pointer";
                link.style.color = "var(--color-primary)";
                link.style.textDecoration = "underline";
                evItem.appendChild(link);
            } else {
                evItem.appendChild(el("span", { class: "evidence-id", text: refId }));
            }
            if (refChunk) {
                evItem.appendChild(el("span", { class: "evidence-chunk small muted", text: `chunk: ${refChunk}` }));
            }
            evList.appendChild(evItem);
        });
        return evList;
    }

    function renderClaimItem(c) {
        const item = el("div", { class: "list-item" });
        item.appendChild(el("div", { class: "list-item-head" }, [
            el("span", { class: "list-item-title", text: c.statement || "(无陈述)" }),
            el("span", { class: "badge badge-info", text: c.role || "contribution" }),
            c.evidence_count > 0
                ? el("span", { class: "badge badge-neutral", text: `证据 ${c.evidence_count}` })
                : el("span", { class: "badge badge-warning", text: "证据 0 · 待验证" }),
        ]));
        // 状态徽章单独一行（便于颜色识别）
        item.appendChild(el("div", { class: "mt-8" }));
        item.insertAdjacentHTML("beforeend", `<div class="mt-8">${statusBadge(c.status)}</div>`);

        item.addEventListener("click", () => {
            const expanded = item.classList.toggle("expanded");
            if (expanded && !item.querySelector(".list-item-body")) {
                item.appendChild(buildClaimBody(c));
            }
        });
        return item;
    }

    // Claim 详情正文：陈述 + 角色 + 证据链 + 状态时间（可点击溯源）
    function buildClaimBody(c) {
        const body = el("div", { class: "list-item-body" });
        const refs = c.evidence_refs || [];
        // 关联冲突提示（Claim 处于争议中）
        if (c.conflicts && c.conflicts.length) {
            body.appendChild(el("div", { class: "status-banner warning mt-8" },
                `该论断引用文献存在 ${c.conflicts.length} 处冲突结论，需核查研究条件和原文。`));
        }
        body.insertAdjacentHTML("beforeend", `
            <dl>
                <dt>论断编号</dt><dd class="mono">${escapeHtml(c.claim_id || "—")}</dd>
                <dt>角色</dt><dd>${escapeHtml((c.role || "contribution").replace("_", " "))}</dd>
                <dt>状态</dt><dd>${escapeHtml((c.status || "draft").replace("_", " "))}</dd>
                <dt>来源阶段</dt><dd>${escapeHtml(c.source_stage || "—")}</dd>
                <dt>创建时间</dt><dd class="mono">${escapeHtml(formatTime(c.created_at) || "—")}</dd>
                ${c.verified_at ? `<dt>验证时间</dt><dd class="mono">${escapeHtml(formatTime(c.verified_at))}</dd>` : ""}
            </dl>
        `);
        // 证据链（可点击跳转论文/实验）
        if (refs.length) {
            const evDiv = el("div", { class: "evidence-chain mt-8" });
            evDiv.appendChild(el("div", { class: "small evidence-title" }, "证据溯源链："));
            evDiv.appendChild(buildEvidenceList(refs));
            body.appendChild(evDiv);
        } else {
            body.appendChild(el("div", { class: "small muted mt-8" },
                "尚未关联证据。请核查论文与已有原始材料；本轮不新增实验结果，没有证据的论断不能当作已成立的结论。"));
        }
        return body;
    }

    function buildPaperBody(p) {
        const body = el("div", { class: "list-item-body" });
        body.addEventListener('click',e=>e.stopPropagation());
        // DOI/标题论文也可按需查找 OA PDF，明确标记为尝试。
        const pdfUrl = p.pdf_url || (p.arxiv_id ? `https://arxiv.org/pdf/${p.arxiv_id}.pdf` : "");
        const isDoiFallback = pdfUrl && pdfUrl.includes("doi.org") && !p.arxiv_id;
        const uncertain = !p.pdf_path && (!pdfUrl || isDoiFallback);
        let linksHtml = "";
        if (p.url && /^https?:\/\//i.test(p.url)) {
            linksHtml += `<a href="${escapeHtml(p.url)}" target="_blank" class="link-external">原文链接 ↗</a>`;
        }
        if (p.doi_url) {
            linksHtml += ` <a href="${escapeHtml(p.doi_url)}" target="_blank" class="link-external">DOI ↗</a>`;
        }
        if (p.pdf_path || pdfUrl || p.doi || (p.title && p.source_stage !== "upload")) {
            linksHtml += ` <a href="#" class="link-external js-pdf-download" data-paper-id="${escapeHtml(p.paper_id)}">${uncertain ? "尝试获取开放 PDF ↗" : "下载 PDF ↗"}</a>`;
        }
        if (p.pdf_path) {
            linksHtml += ` <span class="badge badge-success">本地 PDF 已保存</span>`;
        }
        if (!linksHtml) linksHtml = '<span class="muted">无可用链接</span>';
        // 期刊质量信息行
        let qualityHtml = "";
        const ifVal = Number(p.impact_factor) || 0;
        if (ifVal > 0 || p.cas_zone) {
            qualityHtml = `<dt>期刊质量</dt><dd>`;
            if (ifVal > 0) qualityHtml += `影响因子 <strong>${ifVal.toFixed(1)}</strong>`;
            if (p.cas_zone) qualityHtml += ` · 中科院 ${p.cas_zone}区${p.is_top_journal ? " Top" : ""}`;
            if (p.cas_subcategory) qualityHtml += `（${escapeHtml(p.cas_subcategory)}）`;
            qualityHtml += `</dd>`;
        }
        body.innerHTML = `
                    <p>${linksHtml}</p>
                    ${p.reading_scope ? `<p>阅读范围：${escapeHtml(p.reading_scope)}</p>` : ''}
                    ${p.parse_error ? `<p class="ch-warning">解析提示：${escapeHtml(p.parse_error)}</p>` : ''}
                    ${p.abstract ? `<div class="mt-8"><strong>${p.source_stage === "upload" ? "正文开头节选（非论文摘要）" : "摘要"}：</strong><br>${escapeHtml(p.abstract)}</div>` : '<p class="muted">仅元信息，没有可供阅读的摘要或正文。</p>'}
                    <details class="paper-technical"><summary>来源、版本与技术详情</summary><dl>
                        <dt>Paper ID</dt><dd class="mono">${escapeHtml(p.paper_id)}</dd>
                        <dt>作者</dt><dd>${escapeHtml((p.authors || []).join(", ") || "—")}</dd>
                        <dt>年份</dt><dd>${p.year || "—"}</dd>
                        <dt>会议/期刊</dt><dd>${escapeHtml(p.venue || "—")}</dd>
                        ${qualityHtml}
                        <dt>arXiv ID</dt><dd class="mono">${escapeHtml(p.arxiv_id || "—")}</dd>
                        <dt>DOI</dt><dd class="mono">${escapeHtml(p.doi || "—")}</dd>
                        <dt>来源阶段</dt><dd>${escapeHtml(p.source_stage || "—")}</dd>
                        ${p.reading_scope ? `<dt>阅读范围</dt><dd>${escapeHtml(p.reading_scope)}</dd>` : ""}
                        ${p.parse_error ? `<dt>解析提示</dt><dd>${escapeHtml(p.parse_error)}</dd>` : ""}
                        <dt>数据源</dt><dd>${escapeHtml(p.source || "—")}${p.source_subquery ? `（命中子问题：${escapeHtml(p.source_subquery)}）` : ""}</dd>
                        ${p.doc_id ? `<dt>Sciverse doc_id</dt><dd class="mono">${escapeHtml(p.doc_id)}${p.offset ? ` @偏移${p.offset}` : ""}</dd>` : ""}
                        ${p.evidence_score ? `<dt>Sciverse 证据分</dt><dd>${escapeHtml(String(Number(p.evidence_score).toFixed(3)))}</dd>` : ""}
                        ${p.relevance_score ? `<dt>主题相关度</dt><dd>${escapeHtml(String(Number(p.relevance_score).toFixed(2)))}${p.relevance_reason ? `（${escapeHtml(p.relevance_reason)}）` : ""}</dd>` : ""}
                        <dt>入库时间</dt><dd class="mono">${escapeHtml(formatTime(p.created_at))}</dd>
                    </dl></details>
                `;
        const dlLink = body.querySelector(".js-pdf-download");
        if (dlLink) {
            dlLink.addEventListener("click", async (ev) => {
                ev.preventDefault();
                ev.stopPropagation();
                try {
                    await downloadPaperPdf(state.currentProjectId, p.paper_id);
                } catch (err) {
                    alert(`PDF 下载失败：${err.message || err}`);
                }
            });
        }
        return body;
    }

    // ===== 3.5 研究缺口页（Task 3：Research Gap 识别） =====

    let gapsFilter = "all";

    const GAP_TYPE_META = {
        contradiction: { label: "矛盾结论", cls: "gap-tag-danger" },
        unexplored: { label: "待探索问题", cls: "gap-tag-info" },
        missing_link: { label: "缺失知识连接", cls: "gap-tag-warning" },
    };
    const GAP_ACTION_META = {
        high: { label: "高可操作性", cls: "gap-act-high" },
        medium: { label: "中", cls: "gap-act-medium" },
        low: { label: "低", cls: "gap-act-low" },
    };

    async function renderGaps(content) {
        content.appendChild(el("div", { class: "loading" }, "加载研究缺口…"));
        let data;
        try {
            data = await api("GET", `/api/projects/${state.currentProjectId}/gaps`);
        } catch (e) {
            content.appendChild(el("div", { class: "status-banner danger" },
                "加载失败：" + (e.message || e)));
            return;
        }
        clear(content);
        const gaps = data.gaps || [];
        const stats = data.stats || {};

        content.appendChild(el("h3", { class: "card-title" }, "文献中的待核实问题"));
        const byType = stats.by_type || {};
        const typeBrief = Object.entries(byType)
            .map(([k, v]) => `${GAP_TYPE_META[k] ? GAP_TYPE_META[k].label : k} ${v} 个`)
            .join(" · ") || "暂无";
        content.appendChild(el("p", { class: "page-desc" },
            `共 ${gaps.length} 条候选线索（${typeBrief}）。这些是基于已检索材料提出的疑问，不是“全领域无人研究”的证明。请先核对引用、研究条件和相关工作，再收敛为自己的研究问题。`));

        if (!gaps.length) {
            content.appendChild(el("div", { class: "card mat-empty" },
                "暂无自动提取的线索。可先在文献库阅读论文，并在「我的研究问题」记录具体问题；不必等待整条任务跑完。"));
            return;
        }

        // 类型筛选条
        const bar = el("div", { class: "filter-bar" });
        bar.appendChild(el("span", { class: "filter-label", text: "类型筛选：" }));
        ["all", "contradiction", "unexplored", "missing_link"].forEach(t => {
            const label = t === "all" ? "全部" : (GAP_TYPE_META[t] ? GAP_TYPE_META[t].label : t);
            bar.appendChild(el("span", {
                class: "filter-chip" + (gapsFilter === t ? " active" : ""),
                text: label,
                onclick: () => {
                    gapsFilter = t;
                    renderPage();
                },
            }));
        });
        content.appendChild(bar);

        // Gap 卡片列表（按优先级 1 最高在前，接口已排序）
        const sorted = gapsFilter === "all"
            ? gaps
            : gaps.filter(g => g.gap_type === gapsFilter);
        const list = el("div", { class: "gap-list" });
        for (const g of sorted) {
            list.appendChild(renderGapCard(g));
        }
        content.appendChild(list);

        // 文献冲突裁决区块（会议纪要：同一结论冲突时按期刊等级/文献新旧二次验证，用户可裁决）
        content.appendChild(await renderConflictAdjudication());
    }

    // ===== 文献冲突裁决（Task 2：冲突二次验证） =====
    // 每条冲突：support/refute 双方证据 → 自动裁决建议（期刊等级+文献新度打分）
    // → 用户可一键采纳 / 存疑，裁决结果写回 DB（metadata.adjudication）
    const CONFLICT_VERDICT_META = {
        adopt_support: { label: "采纳支持方", cls: "verdict-support" },
        adopt_refute: { label: "采纳反对方", cls: "verdict-refute" },
        suspect: { label: "双方存疑", cls: "verdict-suspect" },
        unknown: { label: "证据不足", cls: "verdict-unknown" },
    };

    async function renderConflictAdjudication() {
        const card = el("div", { class: "card conflict-adjudication" });
        let data;
        try {
            data = await api("GET", `/api/projects/${state.currentProjectId}/conflicts`);
        } catch (e) {
            return card; // 静默：冲突区块失败不影响缺口页
        }
        const conflicts = data.conflicts || [];
        card.appendChild(el("div", { class: "card-title" },
            `文献冲突裁决（${conflicts.length} 处）· 自动建议 + 人工裁决`));
        card.appendChild(el("p", { class: "muted small mb-12" },
            "同一结论存在冲突的文献自动按「期刊等级（IF/分区）> 文献新度」加权打分给出采纳建议，你可一键裁决覆盖（结果用于后续分析前剔除低可信来源）。"));
        if (!conflicts.length) {
            card.appendChild(el("div", { class: "list-empty" },
                "暂无文献冲突（交叉验证未发现矛盾结论）"));
            return card;
        }

        const list = el("div", { class: "list" });
        conflicts.forEach((c, i) => {
            const item = el("div", { class: "list-item conflict-item" });
            // 头部：编号 + 陈述 + 裁决状态徽章
            const vm = CONFLICT_VERDICT_META[c.auto_verdict] || CONFLICT_VERDICT_META.suspect;
            const head = el("div", { class: "list-item-head" }, [
                el("span", { class: "conflict-number", text: `#${i + 1}` }),
                el("span", { class: "list-item-title", text: c.claim || "(无陈述)" }),
            ]);
            if (c.adjudicated) {
                head.appendChild(el("span", {
                    class: `conflict-verdict-badge ${vm.cls}`,
                    title: "已人工裁决（可重新裁决覆盖）",
                    text: `已裁决：${vm.label}`,
                }));
            }
            item.appendChild(head);

            // 双方证据质量条
            const sides = el("div", { class: "conflict-sides" });
            const mkSide = (label, stance, score, info, sources) => {
                const side = el("div", {
                    class: `conflict-side ${stance}${score >= 0.3 ? " stronger" : ""}`,
                });
                side.appendChild(el("div", { class: "conflict-side-head" }, [
                    el("span", { class: `conflict-stance-tag ${stance}`, text: label }),
                    el("span", { class: "conflict-side-score", text: `质量 ${(score * 100).toFixed(0)}` }),
                    el("span", { class: "muted small", text: info || "" }),
                ]));
                const srcs = sources || [];
                if (srcs.length) {
                    const ul = el("ul", { class: "conflict-src-list" });
                    srcs.slice(0, 3).forEach(s => {
                        const li = el("li", { class: "small" });
                        const pid = s.paper_id || "";
                        li.appendChild(el("span", { text: `[${pid.slice(-8)}] ${s.title || "来源论文"}` }));
                        if (pid) {
                            li.appendChild(el("a", {
                                class: "gap-ev-link",
                                text: "查看 →",
                                href: "#",
                                onclick: (e) => {
                                    e.preventDefault(); e.stopPropagation();
                                    state.pendingPaperId = pid;
                                    setActivePage("papers");
                                },
                            }));
                        }
                        ul.appendChild(li);
                    });
                    side.appendChild(ul);
                }
                return side;
            };
            sides.appendChild(mkSide("支持方", "support", c.support_score || 0, c.support_info, c.sources.filter(s => s.stance === "support")));
            sides.appendChild(mkSide("反对方", "refute", c.refute_score || 0, c.refute_info, c.sources.filter(s => s.stance === "refute")));
            item.appendChild(sides);

            // 自动裁决建议
            item.appendChild(el("div", { class: "conflict-auto-verdict" }, [
                el("span", { class: "conflict-auto-label" }, "裁决建议："),
                el("span", { class: `conflict-verdict-badge ${vm.cls}`, text: vm.label }),
                el("span", { class: "muted small", text: ` — ${c.auto_reason || ""}` }),
            ]));

            // 裁决按钮（人工覆盖）
            const btnRow = el("div", { class: "btn-row conflict-adjudicate-btns" }, [
                el("button", {
                    class: "btn btn-sm btn-success",
                    onclick: () => adjudicateConflict(c.conflict_id, "adopt_support", item),
                }, "采纳支持方"),
                el("button", {
                    class: "btn btn-sm btn-warning",
                    onclick: () => adjudicateConflict(c.conflict_id, "adopt_refute", item),
                }, "采纳反对方"),
                el("button", {
                    class: "btn btn-sm btn-secondary",
                    onclick: () => adjudicateConflict(c.conflict_id, "suspect", item),
                }, "双方存疑"),
            ]);
            item.appendChild(btnRow);
            list.appendChild(item);
        });
        card.appendChild(list);
        return card;
    }

    // 提交冲突裁决并就地刷新该卡片
    async function adjudicateConflict(conflictId, verdict, item) {
        try {
            const res = await api("POST",
                `/api/projects/${state.currentProjectId}/conflicts/${conflictId}/adjudicate`,
                { verdict, note: "" });
            showToast(res.message || "裁决已保存", "success");
            // 就地更新：重新拉冲突列表，替换当前冲突卡片
            const data = await api("GET", `/api/projects/${state.currentProjectId}/conflicts`);
            const conf = (data.conflicts || []).find(x => x.conflict_id === conflictId);
            if (conf && item && item.parentNode) {
                const vm = CONFLICT_VERDICT_META[conf.auto_verdict] || CONFLICT_VERDICT_META.suspect;
                const badge = item.querySelector(".conflict-verdict-badge");
                if (badge) {
                    badge.className = `conflict-verdict-badge ${vm.cls}`;
                    badge.textContent = `已裁决：${vm.label}`;
                    badge.title = "已人工裁决（可重新裁决覆盖）";
                }
                const auto = item.querySelector(".conflict-auto-verdict");
                if (auto) {
                    auto.querySelector(".conflict-verdict-badge").className = `conflict-verdict-badge ${vm.cls}`;
                    auto.querySelector(".conflict-verdict-badge").textContent = vm.label;
                    auto.querySelector(".muted").textContent = " — 人工裁决已生效";
                }
            }
        } catch (e) {
            showToast("裁决失败：" + (e.message || e), "error");
        }
    }

    function renderGapCard(g) {
        const typeMeta = GAP_TYPE_META[g.gap_type] || { label: g.gap_type || "未知", cls: "gap-tag-neutral" };
        const actMeta = GAP_ACTION_META[g.actionability] || { label: g.actionability || "中", cls: "gap-act-medium" };
        const sourceLabel = g.source === "data_driven" ? "数据驱动"
            : g.source === "db_driven" ? "数据库驱动"
            : g.source === "hybrid" ? "LLM + 数据"
            : g.source === "placeholder" ? "占位"
            : "LLM 分析";

        const card = el("div", { class: "gap-card" });

        // 头部：类型徽章 + 优先级 + 来源 + statement
        const head = el("div", { class: "gap-head" });
        head.appendChild(el("span", { class: `gap-tag ${typeMeta.cls}`, text: typeMeta.label }));
        head.appendChild(el("span", { class: "gap-priority", text: `优先级 P${g.priority || 3}` }));
        head.appendChild(el("span", { class: "gap-source", text: sourceLabel }));
        head.appendChild(el("span", { class: `gap-act ${actMeta.cls}`, text: `可操作性：${actMeta.label}` }));
        card.appendChild(head);

        card.appendChild(el("div", { class: "gap-statement", text: g.statement || "（无陈述）" }));

        if (g.detail) {
            card.appendChild(el("div", { class: "gap-detail", text: g.detail }));
        }

        // 关联研究对象（字段名随领域变化：材料体系/细胞系/模型/靶点…）
        const mats = g.related_materials || [];
        if (mats.length) {
            const row = el("div", { class: "gap-mats" });
            row.appendChild(el("span", { class: "gap-row-label", text: "关联对象：" }));
            mats.forEach(m => row.appendChild(el("span", { class: "mat-chip", text: m })));
            card.appendChild(row);
        }

        // 证据链（可溯源）
        const evs = g.evidence || [];
        if (evs.length) {
            const evBlock = el("div", { class: "gap-evidence" });
            evBlock.appendChild(el("div", { class: "gap-row-label", text: `证据链（${evs.length} 条，可溯源）：` }));
            evs.slice(0, 3).forEach(ev => {
                const title = ev.title || ev.paper_id || "来源论文";
                const snippet = ev.snippet ? ` — ${ev.snippet.slice(0, 120)}${ev.snippet.length > 120 ? "…" : ""}` : "";
                const row = el("div", { class: "gap-ev-item" });
                row.appendChild(el("span", { text: `[${ev.paper_id ? ev.paper_id.slice(-8) : "?"}] ${title}${snippet}` }));
                if (ev.paper_id) {
                    row.appendChild(el("a", {
                        class: "gap-ev-link",
                        text: "查看论文 →",
                        href: "#",
                        onclick: (e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            state.pendingPaperId = ev.paper_id;
                            setActivePage("papers");
                        },
                    }));
                }
                evBlock.appendChild(row);
            });
            if (evs.length > 3) {
                evBlock.appendChild(el("div", { class: "gap-ev-more", text: `…另有 ${evs.length - 3} 条证据` }));
            }
            card.appendChild(evBlock);
        }

        // 建议行动
        const acts = g.suggested_actions || [];
        if (acts.length) {
            const actRow = el("div", { class: "gap-actions" });
            actRow.appendChild(el("span", { class: "gap-row-label", text: "建议行动：" }));
            acts.forEach(a => actRow.appendChild(el("span", { class: "gap-act-chip", text: a })));
            card.appendChild(actRow);
        }

        // 数据库证据链（Materials Project / OQMD / NOMAD，与文献证据构成双证据链）
        const dbEvs = g.db_evidence || [];
        if (dbEvs.length) {
            const dbBlock = el("div", { class: "gap-dbevidence" });
            dbBlock.appendChild(el("div", { class: "gap-row-label", text: `数据库证据（${dbEvs.length} 条，MP/OQMD/NOMAD）：` }));
            dbEvs.slice(0, 3).forEach(ev => {
                const formula = ev.formula || ev.name || "?";
                const mp = ev.mp || {};
                const oqmd = ev.oqmd || {};
                const nomad = ev.nomad || {};
                const chips = [];
                chips.push(`MP ${mp.matched ? `命中 ${mp.entry_count ?? 0} 条` : "未命中"}`);
                if (mp.band_gap != null) chips.push(`带隙 ${mp.band_gap} eV`);
                chips.push(`OQMD ${oqmd.matched ? `命中 ${oqmd.entry_count ?? 0} 条` : "未命中"}`);
                if (oqmd.stability) chips.push(oqmd.stability);
                chips.push(`NOMAD ${nomad.matched ? `命中 ${nomad.entry_count ?? 0} 条` : "未命中"}`);
                const row = el("div", { class: "gap-dbev-item" });
                row.appendChild(el("span", { text: `[${formula}] ${chips.join(" · ")}` }));
                dbBlock.appendChild(row);
            });
            if (dbEvs.length > 3) {
                dbBlock.appendChild(el("div", { class: "gap-ev-more", text: `…另有 ${dbEvs.length - 3} 条数据库证据` }));
            }
            card.appendChild(dbBlock);
        }

        return card;
    }

    // ===== 5. 实验页 =====

    async function renderExperiments(content) {
        if(window.SRA_WORKSPACE_VIEW){window.SRA_WORKSPACE_VIEW.mount(content,'plans',window.__sraApp);return;}
        content.appendChild(el("div", { class: "loading" }, "加载中…"));
        try {
            const [data, tasks] = await Promise.all([
                api("GET", `/api/projects/${state.currentProjectId}/experiments`),
                api("GET", `/api/projects/${state.currentProjectId}/experiment-tasks`).catch(() => null),
            ]);
            clear(content);
            content.appendChild(el("div", { class: "page-header" }, [
                el("h2", { class: "page-title" }, "研究方案"),
                el("p", { class: "page-desc" }, `围绕当前问题形成、审阅与导出方案，本轮不进入实验或后续写作。${data.experiments.length} 条历史实验记录仍可查看；方案和预期不代表结论已验证。`),
            ]));
            const records = el("section", {"aria-label":"研究方案与实际结果"});
            content.appendChild(records);
            renderResearchRecords(records, ["plan", "result", "finding"]);
            content.appendChild(buildMethodDocCard());
            // 实验任务说明书卡片：任务在项目 experiments/ 目录，系统只给任务不给代码
            if (tasks && tasks.has_tasks) {
                content.appendChild(buildTaskSpecCard(tasks, content));
            }
            if (!data.experiments.length) {
                content.appendChild(el("div", { class: "list-empty" }, "暂无原流程实验记录。可在上方整理、审阅与导出研究方案；本轮不执行实验或新增结果。"));
                return;
            }
            const list = el("div", { class: "list" });
            data.experiments.forEach(e => list.appendChild(renderExperimentItem(e)));
            content.appendChild(list);

            // 实验定位（Claim 证据溯源跳转）
            const targetId = state.pendingExperimentId;
            if (targetId) {
                state.pendingExperimentId = null;
                const cards = list.querySelectorAll(".list-item");
                for (const card of cards) {
                    if (card.getAttribute("data-exp-id") === targetId) {
                        card.scrollIntoView({ behavior: "smooth", block: "center" });
                        card.classList.add("paper-flash");
                        setTimeout(() => card.classList.remove("paper-flash"), 2600);
                        if (!card.classList.contains("expanded")) {
                            card.classList.add("expanded");
                            const exp = data.experiments.find(x => x.experiment_id === targetId);
                            if (exp && !card.querySelector(".list-item-body")) {
                                // 复用 renderExperimentItem 的展开逻辑：触发一次展开
                                const body = el("div", { class: "list-item-body" });
                                body.innerHTML = buildExperimentBody(exp);
                                card.appendChild(body);
                            }
                        }
                        break;
                    }
                }
            }
        } catch (e) {
            clear(content);
            content.appendChild(el("div", { class: "status-banner danger" },
                `加载失败：${escapeHtml(e.message)}`));
        }
    }

    // 实验任务说明书卡片：查看 / 下载 / 结果回填
    function buildTaskSpecCard(tasks, content) {
        const pid = state.currentProjectId;
        const card = el("div", { class: "card" });
        card.appendChild(el("div", { class: "card-title" },
                "任务来源包（可下载，系统不代跑实验）"));
        const planCount = (tasks.plan && tasks.plan.experiments && tasks.plan.experiments.length) || 0;
        card.appendChild(el("p", { class: "muted small" },
            `共 ${planCount} 个实验任务，已导出到项目 ${tasks.export_dir} 目录`
            + "（experiment_plan.json + README.md）。此为历史材料，仅供查看与导出；当前不开放执行或结果回填。"));

        const readmePre = el("div", {
            class: "research-document",
            style: "display:none; max-height:520px; overflow:auto;",
        });
        window.SRA_DOCUMENT.render(readmePre,tasks.readme);

        const row = el("div", { class: "btn-row mt-8" });
        row.appendChild(el("button", {
            class: "btn btn-secondary",
            onclick: () => {
                readmePre.style.display = readmePre.style.display === "none" ? "block" : "none";
            },
        }, "查看任务说明书"));
        const dl = el("a", {
            class: "btn btn-secondary",
            href: SRA_API_BASE + `/api/projects/${state.currentProjectId}/download/experiment-tasks`,
        }, "下载说明书（.md）");
        dl.setAttribute("download", "experiment_tasks.md");
        row.appendChild(dl);
        card.appendChild(row);
        card.appendChild(readmePre);

        // Historical task packages are read-only in this product.
        return card;
    }

    function renderExperimentItem(e) {
        const item = el("div", { class: "list-item" });
        item.setAttribute("data-exp-id", e.experiment_id || "");
        item.appendChild(el("div", { class: "list-item-head" }, [
            el("span", { class: "list-item-title", text: e.name || "(无名称)" }),
        ]));
        item.insertAdjacentHTML("beforeend",
            `<div class="mt-8">${statusBadge(e.status)} <span class="badge badge-neutral">关联 ${e.verifies_claim_ids?.length || 0} 条论断</span></div>`);

        item.addEventListener("click", () => {
            const expanded = item.classList.toggle("expanded");
            if (expanded && !item.querySelector(".list-item-body")) {
                const body = el("div", { class: "list-item-body" });
                body.innerHTML = buildExperimentBody(e);
                item.appendChild(body);
            }
        });
        return item;
    }

    function buildExperimentBody(e) {
        return `
                    <dl>
                        <dt>Experiment ID</dt><dd class="mono">${escapeHtml(e.experiment_id)}</dd>
                        <dt>名称</dt><dd>${escapeHtml(e.name)}</dd>
                        <dt>状态</dt><dd>${escapeHtml(e.status)}</dd>
                        <dt>关联论断</dt><dd class="mono">${escapeHtml((e.verifies_claim_ids || []).join(", ") || "—")}</dd>
                        <dt>开始时间</dt><dd class="mono">${escapeHtml(formatTime(e.started_at))}</dd>
                        <dt>完成时间</dt><dd class="mono">${escapeHtml(formatTime(e.completed_at))}</dd>
                        <dt>创建时间</dt><dd class="mono">${escapeHtml(formatTime(e.created_at))}</dd>
                    </dl>
                    ${e.result_summary ? `<div class="mt-8"><strong>结果摘要：</strong><br>${escapeHtml(e.result_summary)}</div>` : ""}
                    ${e.metrics && Object.keys(e.metrics).length
                        ? `<div class="mt-8"><strong>回填指标：</strong><pre class="code-block">${escapeHtml(JSON.stringify(e.metrics, null, 2))}</pre></div>`
                        : ""}
                    ${e.anomaly_notes ? `<div class="mt-8" style="color:var(--color-danger)"><strong>异常记录：</strong><br>${escapeHtml(e.anomaly_notes)}</div>` : ""}
                    ${e.config && Object.keys(e.config).length
                        ? `<div class="mt-8"><strong>实验配置：</strong></div><pre class="code-block">${escapeHtml(JSON.stringify(e.config, null, 2))}</pre>`
                        : ""}
                `;
    }

    // ===== 6. 灵感笔记页 =====

    const noteSaves = new Map();
    function noteDraft(pid, value) {
        const key = 'sra.notes.draft.' + pid;
        try {
            if (value === null) { localStorage.removeItem(key); return {text:'',version:''}; }
            if (value !== undefined) localStorage.setItem(key, JSON.stringify(value));
            return JSON.parse(localStorage.getItem(key) || 'null') || {text:'',version:''};
        } catch (_) { return value || {text:'',version:''}; }
    }
    async function renderNotes(content) {
        const pid = state.currentProjectId;
        clear(content);
        content.appendChild(el("div", { class: "page-header" }, [
            el("h2", { class: "page-title" }, "灵感笔记"),
            el("p", { class: "page-desc" }, "记录灵感、疑问与下一步想法。笔记可供科研指导参考，不会自动启动任务，也不等于已经核实的结论。"),
        ]));

        // 输入卡片
        const card = el("div", { class: "card" });
        card.appendChild(el("div", { class: "card-title" }, "新增笔记"));
        card.appendChild(el("label", { class: "field-label", for: "note-input" }, "科研灵感记录"));
        const textarea = el("textarea", {
            class: "textarea",
            id: "note-input",
            placeholder: "记录此刻的想法、对某个论断的质疑、或下一步要尝试的方向…（支持多行，Ctrl+Enter 提交）",
        });
        const mountedDraft=noteDraft(pid);
        textarea.value = mountedDraft.text;
        const status = el('p', {class:'muted small',role:'status'}, '未保存内容为本机草稿，不是正式研究条件。');
        function changed() {
            noteDraft(pid, {text:textarea.value,version:crypto.randomUUID()});
            status.textContent = textarea.value ? '草稿已保留在本机，尚未保存为笔记。' : '草稿已清空。';
        }
        textarea.addEventListener('input', changed);
        let composing = false;
        textarea.addEventListener('compositionstart',()=>{composing=true;});
        textarea.addEventListener('compositionend',()=>{composing=false;});
        async function saveNote() {
            if (noteSaves.has(pid)) return;
            const snapshot = {text:textarea.value,version:noteDraft(pid).version};
            if (!snapshot.text.trim()) { showToast('笔记内容不能为空','error'); return; }
            save.disabled=true;status.textContent='正在保存本次提交的笔记，可继续编辑草稿。';
            const pending=api('POST',`/api/projects/${encodeURIComponent(pid)}/notes`,{text:snapshot.text.trim()});
            noteSaves.set(pid,pending);
            try {
                await pending;
                // Compare the draft generation, not only its text: clear/retype,
                // same-project remounts and cross-project responses are distinct.
                if (noteDraft(pid).version===snapshot.version && noteDraft(pid).text===snapshot.text) {
                    noteDraft(pid,null);
                    if (textarea.isConnected && state.currentProjectId===pid) textarea.value='';
                }
                if (textarea.isConnected && state.currentProjectId===pid) {
                    status.textContent=textarea.value ? '本次笔记已保存；新输入仍是未提交草稿。' : '笔记已保存。';
                    await loadNotes();
                    renderSidebarNotes();
                }
            } catch(e) {
                if (textarea.isConnected && state.currentProjectId===pid) status.textContent='保存未确认，草稿仍保留。可先查看历史笔记，再点击保存重试。';
            } finally {
                noteSaves.delete(pid);
                if (textarea.isConnected) save.disabled=false;
            }
        }
        textarea.addEventListener('keydown',e=>{
            if ((e.ctrlKey||e.metaKey) && e.key==='Enter' && !e.isComposing && !composing && !e.repeat) { e.preventDefault();saveNote(); }
        });
        const save=el('button',{class:'btn',onclick:saveNote},'保存笔记');
        save.disabled=noteSaves.has(pid);
        card.appendChild(textarea);
        card.appendChild(status);
        card.appendChild(el("div", { class: "btn-row mt-16" }, [
            save,
            el("button", {
                class: "btn btn-secondary",
                onclick: () => { textarea.value='';noteDraft(pid,null);status.textContent='草稿已清空。'; },
            }, "清空"),
        ]));
        content.appendChild(card);

        // 笔记列表
        const listCard = el("div", { class: "card" });
        listCard.appendChild(el("div", { class: "card-title" }, "历史笔记"));
        async function loadNotes() {try {
            const data = await api("GET", `/api/projects/${encodeURIComponent(pid)}/notes`);
            if (!textarea.isConnected || state.currentProjectId!==pid) return;
            listCard.replaceChildren(el('div',{class:'card-title'},'历史笔记'));
            const notes = data.notes || [];
            if (!notes.length) {
                listCard.appendChild(el("div", { class: "list-empty" }, "暂无笔记"));
            } else {
                notes.slice().reverse().forEach(n => {
                    listCard.appendChild(el("div", { class: "note-item" }, [
                        el("p", { class: "note-text", text: n.text }),
                        el("div", { class: "note-time", text: formatTime(n.created_at) }),
                    ]));
                });
            }
        } catch (e) {
            listCard.appendChild(el("div", { class: "status-banner danger" },
                `加载失败：${escapeHtml(e.message)}`));
        }}
        content.appendChild(listCard);
        await loadNotes();
        save.disabled=noteSaves.has(pid);
        if(mountedDraft.version && !noteDraft(pid).version && textarea.value===mountedDraft.text)textarea.value='';
        const activeSave = noteSaves.get(pid), mountedText=textarea.value;
        if(activeSave) activeSave.then(()=>{
            if(!textarea.isConnected || state.currentProjectId!==pid)return;
            save.disabled=false;
            if(!noteDraft(pid).version && textarea.value===mountedText)textarea.value='';
            status.textContent=textarea.value ? '本次笔记已保存；新输入仍是未提交草稿。' : '笔记已保存。';
            loadNotes();
        },()=>{if(textarea.isConnected){save.disabled=false;status.textContent='保存未确认，草稿保留，请核对历史笔记后重试。';}});
    }

    // ===== 6c. 论文阅读方法论（静态知识页，无需项目） =====

    function renderReadingMethod(content) {
        clear(content);
        content.appendChild(el("div", { class: "page-header" }, [
            el("h2", { class: "page-title" }, "论文阅读方法论"),
            el("p", { class: "page-desc" },
                "读论文不是为了读完，而是为了服务科研。按你的【目的】和【所处阶段】选择对应的读法，不必从头到尾逐字精读。"),
        ]));

        // 顶部快速导航（按目的跳转）
        const navTargets = [
            ["method-core", "核心理念"],
            ["method-stages", "五阶段路线"],
            ["method-purpose", "按目的选读法"],
            ["method-screen", "文献筛选"],
            ["method-notes", "笔记方法"],
            ["method-present", "组会讲论文"],
        ];
        content.appendChild(el("div", { class: "method-nav" }, navTargets.map(([id, label]) =>
            el("button", {
                class: "method-chip",
                onclick: () => {
                    const target = document.getElementById(id);
                    if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
                },
            }, label)
        )));

        // 1. 核心理念
        const coreCard = el("div", { class: "card method-section", id: "method-core" });
        coreCard.appendChild(el("div", { class: "card-title" }, "① 核心理念：Why → What → How → Ideas"));
        coreCard.appendChild(el("p", { class: "muted small" },
            "读论文不是勾画抄写，而是回答问题。每篇论文读完，至少能回答：为什么做（Why）、做了什么（What）、怎么做（How）、我能怎么创新（Ideas）。"));
        coreCard.appendChild(el("div", { class: "method-grid" }, [
            methodMiniCard("Why", "为什么做？", "研究动机、现有方法的不足、要解决的核心问题"),
            methodMiniCard("What", "做了什么？", "提出了什么方法/框架/数据集，核心贡献是什么"),
            methodMiniCard("How", "怎么做？", "输入输出、整体流程、关键技术模块"),
            methodMiniCard("Ideas", "我能怎么创新？", "不足、FutureWork、实验空白、模块替换、跨领域迁移"),
        ]));
        coreCard.appendChild(el("div", { class: "method-callout" }, [
            el("strong", { text: "六问阅读模板：" }),
            el("span", { text: " Why（为什么做）→ What（提出了什么方法）→ How（如何实现）→ Result（效果如何）→ Weakness（有哪些不足）→ Ideas（我能如何改进）。逐问作答，读完了模板也就填满了。" }),
        ]));
        coreCard.appendChild(buildRatioTable());
        content.appendChild(coreCard);

        // 2. 五阶段路线
        const stagesCard = el("div", { class: "card method-section", id: "method-stages" });
        stagesCard.appendChild(el("div", { class: "card-title" }, "② 按自身层次：五阶段路线图"));
        stagesCard.appendChild(el("p", { class: "muted small" },
            "对照自己在哪个阶段，只做该阶段该做的事——入门期纠结公式细节、进阶期还在泛读综述，都是低效阅读。"));
        [
            ["阶段 1 · 刚入门找方向", "优先读综述 + 硕博论文第一章建立领域认知。只关注 Why 和 What，公式和实验细节直接跳过。"],
            ["阶段 2 · 建立领域地图", "边读边整理文献表格：问题 / 方法 / 数据集 / 创新点四列。读完一批，领域版图自然成形。"],
            ["阶段 3 · 精读 Top 论文", "三遍读法：第一遍摘要+图表+结论；第二遍方法流程图；第三遍公式、算法与实验细节。"],
            ["阶段 4 · 寻找创新点", "盯住四类信号：论文自述不足、FutureWork、实验空白（没比的 baseline / 没测的场景）、可替换模块与跨学科迁移。"],
            ["阶段 5 · 实验驱动阅读", "带着具体问题定向检索文献，以「解决当前问题」为目标，读完整篇不如读准三段。"],
        ].forEach(([title, desc]) => {
            stagesCard.appendChild(el("div", { class: "method-stage" }, [
                el("div", { class: "method-stage-title", text: title }),
                el("div", { class: "method-stage-desc", text: desc }),
            ]));
        });
        content.appendChild(stagesCard);

        // 3. 按目的选读法
        const purposeCard = el("div", { class: "card method-section", id: "method-purpose" });
        purposeCard.appendChild(el("div", { class: "card-title" }, "③ 按阅读目的选读法（精读 / 选读 / 略读）"));
        purposeCard.appendChild(el("p", { class: "muted small" },
            "同一篇论文，目的不同，读的部分完全不同。先明确目的，再按下表选路径。"));
        purposeCard.appendChild(buildPurposeTable());
        content.appendChild(purposeCard);

        // 4. 文献筛选
        const screenCard = el("div", { class: "card method-section", id: "method-screen" });
        screenCard.appendChild(el("div", { class: "card-title" }, "④ 文献筛选：三级漏斗，忌盲目囤积"));
        screenCard.appendChild(el("div", { class: "method-funnel" }, [
            methodStepCard("1", "看标题", "与研究方向相关才进入下一级"),
            methodStepCard("2", "看关键词", "与研究内容匹配才继续"),
            methodStepCard("3", "读摘要", "背景/目的/结果/结论相关 → 这就是要找的关键文献"),
        ]));
        screenCard.appendChild(el("div", { class: "method-callout" }, [
            el("strong", { text: "两个提效工具：" }),
            el("span", { text: " 文献鸟（storkapp.me）——按关键词定时推送新文献到邮箱；Connected Papers——输入标题/DOI 生成知识图谱：颜色越深发表越新、圆圈越大引用越高、连线越黑关联越强。" }),
        ]));
        screenCard.appendChild(el("div", { class: "method-callout" }, [
            el("strong", { text: "图谱入口：" }),
            el("span", { text: " Prior Works 是被引最多的开山之作（了解领域最好的敲门砖）；Derivative Works 是最新综述与后续工作（追踪前沿的垫脚石）。" }),
        ]));
        content.appendChild(screenCard);

        // 5. 笔记方法
        const notesCard = el("div", { class: "card method-section", id: "method-notes" });
        notesCard.appendChild(el("div", { class: "card-title" }, "⑤ 笔记方法：为自己服务，量化收获"));
        notesCard.appendChild(el("div", { class: "method-grid method-grid-3" }, [
            methodMiniCard("不是勾画抄写", "", "要写下自己的思考：这篇文章好在哪里、我能借鉴什么"),
            methodMiniCard("不是给别人看", "", "用自己顺手的语言、缩写、习惯记录，看得懂就行"),
            methodMiniCard("不是文献综述", "", "按目的有重点地读，不需要前因后果面面俱到"),
        ]));
        notesCard.appendChild(el("div", { class: "method-callout" }, [
            el("strong", { text: "记录五步：" }),
            el("span", { text: " ① 作者/单位/期刊（只记第一或通讯作者）；② 基本框架：背景、目的、结论摘录；③ 结果与讨论的关键图表与机理；④ 强迫自己回答「它好在哪、为什么能上顶刊」；⑤ 强迫总结可借鉴之处。" }),
        ]));
        notesCard.appendChild(el("div", { class: "method-callout highlight" }, [
            el("strong", { text: "「125」原则（量化每次阅读收获）：" }),
            el("span", { text: " 每篇论文带走 1 个研究思路 + 2 个值得模仿的图表（形式/配色）+ 5 个好句式（写作时模仿套用）。" }),
        ]));
        content.appendChild(notesCard);

        // 6. 组会讲论文
        const presentCard = el("div", { class: "card method-section", id: "method-present" });
        presentCard.appendChild(el("div", { class: "card-title" }, "⑥ 组会讲论文：篇幅分配决定评价"));
        presentCard.appendChild(buildPresentPriority());
        presentCard.appendChild(el("div", { class: "method-callout" }, [
            el("strong", { text: "摘要四问：" }),
            el("span", { text: " Why（为什么做，动机是什么）→ How（用了什么方法）→ What（得到了什么结果）→ Meaningful（这个结果有什么意义/价值）。" }),
        ]));
        presentCard.appendChild(el("div", { class: "method-callout" }, [
            el("strong", { text: "图表解读：" }),
            el("span", { text: " 看每张 figure/table 都要问「这组数据支撑了什么观点」——不要只看数字，要理解数据背后的结论。" }),
        ]));
        presentCard.appendChild(el("div", { class: "method-callout" }, [
            el("strong", { text: "笔记标记五要素：" }),
            el("span", { text: " 动机（为什么做）· 解决的问题（核心挑战）· 创新点（新技术/新方法）· 方法 pipeline（整体流程）· 指标计算（评价指标与算法）。" }),
        ]));
        content.appendChild(presentCard);

        // 尾注：创新方向的套路方法论属于系统内部策略，
        // 在「方向推荐 / 研究缺口 / 思路探讨」生成建议时内部使用，不直接展示。
        content.appendChild(el("div", { class: "card method-section", id: "method-next" }, [
            el("div", { class: "card-title" }, "⑦ 找创新点的下一步"),
            el("p", { class: "muted small" },
                "完成阅读与笔记积累后，可以结合「选题探索」与「研究问题」中的文献线索提出候选问题。建议仍需核对最接近的文献、资源条件和可检验性；有论文来源不等于创新性已确认。"),
        ]));
    }

    function methodMiniCard(title, subtitle, desc) {
        return el("div", { class: "method-mini" }, [
            el("div", { class: "method-mini-title" }, subtitle ? `${title} — ${subtitle}` : title),
            el("div", { class: "method-mini-desc muted small", text: desc }),
        ]);
    }

    function methodStepCard(no, title, desc) {
        return el("div", { class: "method-step" }, [
            el("span", { class: "method-step-no", text: no }),
            el("div", {}, [
                el("div", { class: "method-mini-title", text: title }),
                el("div", { class: "method-mini-desc muted small", text: desc }),
            ]),
        ]);
    }

    function buildRatioTable() {
        const table = el("table", { class: "rm-table" }, [
            el("thead", {}, el("tr", {}, [
                el("th", { text: "阶段" }), el("th", { text: "综述" }), el("th", { text: "硕博论文" }),
                el("th", { text: "普通/顶刊论文" }), el("th", { text: "代码复现" }),
            ])),
        ]);
        const tbody = el("tbody", {}, [
            el("tr", {}, [
                el("td", { text: "入门（未定方向）" }), el("td", { text: "60%" }), el("td", { text: "20%" }),
                el("td", { text: "15%（顶刊）" }), el("td", { text: "5%" }),
            ]),
            el("tr", {}, [
                el("td", { text: "方向已定" }), el("td", { text: "20%" }), el("td", { text: "—" }),
                el("td", { text: "20% 普通 + 60% Top" }), el("td", { text: "按需" }),
            ]),
        ]);
        table.appendChild(tbody);
        return el("div", { class: "mt-8" }, [
            el("div", { class: "method-mini-title", text: "推荐阅读比例" }),
            table,
        ]);
    }

    function buildPurposeTable() {
        const rows = [
            ["了解背景知识", "摘要 → 结论 → 前言", "—", "结果与讨论 → 材料与方法"],
            ["把握研究方向", "摘要 → 结论 → 前言", "结果与讨论（不足与展望）", "材料与方法"],
            ["学习课题设计思路", "摘要 → 结论与结果 → 材料与方法", "前言（创新点与可行性）", "前言、结论泛读"],
            ["提升逻辑写作能力", "摘要 → 前言", "结果与讨论（小标题、段首段尾）", "材料与方法"],
        ];
        const table = el("table", { class: "rm-table" }, [
            el("thead", {}, el("tr", {}, [
                el("th", { text: "阅读目的" }), el("th", { text: "精读（按顺序）" }),
                el("th", { text: "选择性阅读" }), el("th", { text: "略读" }),
            ])),
        ]);
        const tbody = el("tbody");
        rows.forEach(r => {
            tbody.appendChild(el("tr", {}, r.map(c => el("td", { text: c }))));
        });
        table.appendChild(tbody);
        return el("div", { class: "mt-8" }, [
            el("div", { class: "method-mini-title", text: "研究型论文结构：摘要 · 前言 · 材料与方法 · 结果与讨论 · 结论" }),
            table,
            el("p", { class: "muted small mt-8" },
                "提示：写作能力提升重在精读引言（最能体现逻辑框架）+ 琢磨结果与讨论的小标题如何体现亮点、每段开头结尾如何衔接。"),
        ]);
    }

    function buildPresentPriority() {
        const items = [
            ["★★★ 最核心", "科学问题",
                "多花篇幅讲清楚问题本身：可用论文自带概念图，或根据其综述自己整理。再用一页 PPT 摆出 A/B/C 三类已有方法对比并附出处——问题讲清了，知识面一下就展开。"],
            ["★★ 次要核心", "方法与技术实现",
                "一定讲明白输入输出与整体流程：数据 → 操作 → 模型 → 中间格式（如向量）→ 目的（分类/回归）。先讲整体流程，再按论文 block 逐块讲；公式看不懂没关系，说清做了哪些操作、为什么能解决对应挑战即可。"],
            ["★ 不太重要", "大领域背景与实验结果",
                "背景一整页全是字基本必被批——任何一页都不要全是字，文字放备注里用嘴讲。实验结果不用讲太细，除非设计新颖；但数据划分与指标计算要熟，尤其与自己的方向契合时。"],
        ];
        const wrap = el("div", { class: "method-priority" });
        items.forEach(([level, title, desc]) => {
            wrap.appendChild(el("div", { class: "method-priority-row" }, [
                el("span", { class: "method-priority-level", text: level }),
                el("div", {}, [
                    el("div", { class: "method-mini-title", text: title }),
                    el("div", { class: "method-mini-desc muted small", text: desc }),
                ]),
            ]));
        });
        return wrap;
    }

    // ===== 6d. 论文写作方法论（静态知识页，无需项目） =====

    function renderWritingMethod(content) {
        clear(content);
        content.appendChild(el("div", { class: "page-header" }, [
            el("h2", { class: "page-title" }, "论文写作方法论"),
            el("p", { class: "page-desc" },
                "写作不是第 5 阶段才开始，而是从方向发现起每个阶段都在把成果变成可写、可查、可验证的形态。这是思路库，不是流水线：按阶段和任务取用，不必逐步执行。"),
        ]));

        const navTargets = [
            ["wm-principles", "底层原则"],
            ["wm-pipeline", "与流程对接"],
            ["wm-modules", "模块地图"],
            ["wm-order", "写作顺序"],
            ["wm-rules", "取用规则"],
        ];
        content.appendChild(el("div", { class: "method-nav" }, navTargets.map(([id, label]) =>
            el("button", {
                class: "method-chip",
                onclick: () => {
                    const t = document.getElementById(id);
                    if (t) t.scrollIntoView({ behavior: "smooth", block: "start" });
                },
            }, label)
        )));

        // 1. 底层原则
        const prCard = el("div", { class: "card method-section", id: "wm-principles" });
        prCard.appendChild(el("div", { class: "card-title" }, "① 底层原则"));
        [
            ["写作是思考工具", "越早写出核心思想和贡献，越早暴露没想清楚的地方"],
            ["题目宁小勿大", "能一句话说清，并且有文献支撑"],
            ["一个核心思想", "读者读完能复述一句"],
            ["贡献先行", "贡献要具体、可检验，先于正文写出，由它驱动整篇结构"],
            ["先搭骨架再填内容", "借结构不借内容"],
            ["证据先于文字", "先有图表、数据、实验结果，再写叙述；没有证据的论断标为推断"],
            ["论证链闭合", "问题、原因、证据、对策或结论一一对应"],
            ["先易后难", "先写最熟、最不费脑的部分，摘要和标题最后写"],
            ["诚信底线", "文献、数据、结论只能来自证据库；回溯不到的标「待核实」，不编造"],
        ].forEach(([t, d]) => {
            prCard.appendChild(el("div", { class: "method-stage" }, [
                el("div", { class: "method-stage-title", text: t }),
                el("div", { class: "method-stage-desc", text: d }),
            ]));
        });
        content.appendChild(prCard);

        // 2. 与 5 阶段 Pipeline 对接
        const pipeCard = el("div", { class: "card method-section", id: "wm-pipeline" });
        pipeCard.appendChild(el("div", { class: "card-title" }, "② 与本系统 5 阶段 Pipeline 的对接"));
        pipeCard.appendChild(buildPipelineTable());
        pipeCard.appendChild(el("div", { class: "method-callout highlight" }, [
            el("strong", { text: "实验与写作的先后（两条路径皆合法）：" }),
            el("span", { text: " 骨架驱动——先整理要回答的问题与证据清单，再设计实验；实验驱动——先完成核心试跑，记录支持、反驳或无法定论的结果，再展开论证。背景和方法可以提前整理，最终结论须有依据。" }),
        ]));
        pipeCard.appendChild(el("div", { class: "method-callout" }, [
            el("strong", { text: "实验的分工：" }),
            el("span", { text: " 系统提供实验任务规划：写清检验什么设想、执行步骤、前置条件和判定标准。用户自行实现并执行，提交实际结果；任务说明书不等于已交付可运行代码。" }),
        ]));
        content.appendChild(pipeCard);

        // 3. 模块地图
        const modCard = el("div", { class: "card method-section", id: "wm-modules" });
        modCard.appendChild(el("div", { class: "card-title" }, "③ 模块地图（M1-M9：意图 + 判据）"));
        modCard.appendChild(el("p", { class: "muted small" },
            "系统会按阶段自动取用这些模块（方向收敛、文献卡片、贡献表、证据清单、自检审计均已内建到对应环节）；了解它们有助于你在人工确认节点判断产出质量。"));
        [
            ["M1 选题收敛", "把方向收敛成一句话能说清的小题目；文献稀少就改题，不硬写", "有一句话问题陈述 + 若干直接相关文献"],
            ["M2 文献卡片", "每篇记录：研究什么/核心发现/可引用点/局限/与本文关系；汇总领域脉络", "能写出领域脉络，且缺口有证据条目支撑"],
            ["M3 核心思想与贡献", "一句核心思想 + 2-4 条可检验贡献 + 空骨架", "只读贡献，评审就能判断文章值不值得读"],
            ["M4 证据先行", "每个实验模块写清验证什么、需要什么数据、什么结果算支持/反驳；骨架驱动与实验驱动两条路径按证据成熟度选择", "实验围绕要证明的贡献展开，无缺口隐藏"],
            ["M5 分段起草", "先写最熟部分，每段一个中心句，论断挂证据", "每段可提炼一句话要点"],
            ["M6 分析深化", "从描述现象推进到解释机制，问题-原因-证据-结论对齐", "对照表里没有对不上的行"],
            ["M7 自检审计", "逆向提纲、贡献兑现、逐句回溯证据、读者测试", "审计问题清单清零"],
            ["M8 领域适配", "按领域口径检查（材料：计算预测 vs 实验验证；ML 攻防：威胁模型、基线、消融）", "领域审稿人挑不出口径硬伤"],
            ["M9 文风格式", "面向评委用自然第一人称叙事；学术正文遵循 venue 惯例", "没有标题堆叠式 AI 腔"],
        ].forEach(([t, d, c]) => {
            modCard.appendChild(el("div", { class: "method-stage" }, [
                el("div", { class: "method-stage-title", text: t }),
                el("div", { class: "method-stage-desc" }, [
                    el("div", { text: d }),
                    el("div", { class: "muted small", text: `判据：${c}` }),
                ]),
            ]));
        });
        content.appendChild(modCard);

        // 4. 写作顺序先验
        const orderCard = el("div", { class: "card method-section", id: "wm-order" });
        orderCard.appendChild(el("div", { class: "card-title" }, "④ 写作顺序先验（按文章类型选）"));
        orderCard.appendChild(buildOrderTable());
        orderCard.appendChild(el("div", { class: "method-callout" }, [
            el("strong", { text: "相关工作放哪：" }),
            el("span", { text: " IMRaD 类通常压缩进引言；也有主张放靠后位置、先讲自己的思想。由 agent 按领域与目标期刊惯例决定并记录理由。" }),
        ]));
        orderCard.appendChild(el("div", { class: "method-callout highlight" }, [
            el("strong", { text: "冷启动最小动作：" }),
            el("span", { text: " 只做三件事——定一个小题目、找 5 篇文献、搭目录。不要求一晚写完，先动起来。" }),
        ]));
        content.appendChild(orderCard);

        // 5. 取用规则
        const ruleCard = el("div", { class: "card method-section", id: "wm-rules" });
        ruleCard.appendChild(el("div", { class: "card-title" }, "⑤ 取用规则（不同任务的强度参考）"));
        ruleCard.appendChild(buildIntensityTable());
        ruleCard.appendChild(el("div", { class: "method-callout" }, [
            el("strong", { text: "两条硬规则：" }),
            el("span", { text: " 先诊断后取用（判断阶段、任务、已有材料、评审要求再选模块）；不回头重做（任务已越过某阶段就不退回，发现证据缺口才回退补证据）。" }),
        ]));
        content.appendChild(ruleCard);
    }

    function buildPipelineTable() {
        const rows = [
            ["1 方向发现", "把推荐方向收敛成一句话问题与边界，确认文献可得性", "方向卡片的问题陈述"],
            ["2 文献调研", "按文献卡片视角记录；缺口附证据 ID 并标类型；产出领域脉络", "文献卡片、领域脉络、研究缺口"],
            ["3 思路探讨", "每条思路一句核心思想 + 2-4 条可检验贡献（系统已加可检验性评分）", "思路与 Claim、贡献表"],
            ["4 实验规划", "写清目标、步骤、前置条件和支持／反驳标准，由用户实现与执行；按证据成熟度安排试跑与写作", "实验任务说明书（experiment_plan，不包含执行代码）"],
            ["5 论文写作", "证据就位后按骨架家族起草、分析深化、自检审计、文风处理", "初稿、审计清单"],
        ];
        const table = el("table", { class: "rm-table" }, [
            el("thead", {}, el("tr", {}, [
                el("th", { text: "阶段" }), el("th", { text: "写作视角要做的事" }), el("th", { text: "产出落点" }),
            ])),
        ]);
        const tbody = el("tbody");
        rows.forEach(r => tbody.appendChild(el("tr", {}, r.map(c => el("td", { text: c })))));
        table.appendChild(tbody);
        return el("div", { class: "mt-8" }, table);
    }

    function buildOrderTable() {
        const rows = [
            ["实验/方法型（IMRaD）", "方法 → 结果（先图表后文字）→ 引言 → 讨论 → 结论 → 摘要与标题", "方法部分你最熟，通常先写"],
            ["分析/对策型（课程小论文）", "背景 → 现状分析 → 问题分析 → 对策 → 结论 → 摘要", "先写最不费脑的背景或现状，先动起来"],
            ["综述型", "先定分类学（按什么维度切分领域）→ 逐类填充 → 引言、展望和摘要", "分类学定了，其余是填充"],
            ["提交文档型（竞赛/项目书）", "问题 → 方案 → 证据 → 价值，围绕一个核心思想组织", "评委视角：先讲清问题再讲方案"],
        ];
        const table = el("table", { class: "rm-table" }, [
            el("thead", {}, el("tr", {}, [
                el("th", { text: "文章类型" }), el("th", { text: "建议写作顺序" }), el("th", { text: "理由" }),
            ])),
        ]);
        const tbody = el("tbody");
        rows.forEach(r => tbody.appendChild(el("tr", {}, r.map(c => el("td", { text: c })))));
        table.appendChild(tbody);
        return el("div", { class: "mt-8" }, table);
    }

    function buildIntensityTable() {
        const rows = [
            ["只要调研报告", "M1、M2 重点", "M3-M9 可跳过"],
            ["拿到思路写开题/提案", "M1、M2、M3", "M4、M5 轻量"],
            ["完整论文（全流程）", "按阶段取用", "全部可用"],
            ["实验做完只写论文", "M3、M4、M5、M7 重点，M8 按领域", "M1、M2 可跳过"],
            ["竞赛/项目提交文档", "M3、M7、M9", "M2、M4 轻量"],
            ["改稿或续写", "M7 + 相关单模块", "其余跳过"],
        ];
        const table = el("table", { class: "rm-table" }, [
            el("thead", {}, el("tr", {}, [
                el("th", { text: "任务" }), el("th", { text: "重点模块" }), el("th", { text: "可省略" }),
            ])),
        ]);
        const tbody = el("tbody");
        rows.forEach(r => tbody.appendChild(el("tr", {}, r.map(c => el("td", { text: c })))));
        table.appendChild(tbody);
        return el("div", { class: "mt-8" }, table);
    }

    // ===== 6b. 侧边栏灵感笔记小组件 =====

    let sidebarNotesRequest = 0;
    async function renderSidebarNotes() {
        const requestId = ++sidebarNotesRequest;
        const projectId = state.currentProjectId;
        const widget = document.getElementById("sidebar-notes");
        if (!widget) return;
        if (!state.currentProjectId) {
            widget.style.display = "none";
            return;
        }
        widget.style.display = "";
        const listEl = document.getElementById("sidebar-notes-list");
        try {
            const data = await api("GET", `/api/projects/${projectId}/notes`);
            if (requestId !== sidebarNotesRequest || projectId !== state.currentProjectId) return;
            const notes = (data.notes || []).slice().reverse().slice(0, 3);
            state.notesCount = (data.notes || []).length;
            updateBadges(state.statusCache || {});
            if (listEl) {
                clear(listEl);
                if (!notes.length) {
                    listEl.appendChild(el("div", { class: "sidebar-notes-empty" }, "暂无灵感记录"));
                } else {
                    notes.forEach(n => {
                        listEl.appendChild(el("div", { class: "sidebar-note-item" }, [
                            el("div", { class: "sidebar-note-text", text: n.text }),
                            el("div", { class: "sidebar-note-time", text: formatTime(n.created_at) }),
                        ]));
                    });
                }
            }
        } catch (e) {
            // 静默
        }
    }

    async function saveSidebarNote() {
        if (!state.currentProjectId) return;
        const input = document.getElementById("sidebar-note-input");
        const text = (input && input.value || "").trim();
        if (!text) {
            showToast("笔记内容不能为空", "error");
            return;
        }
        try {
            await api("POST", `/api/projects/${state.currentProjectId}/notes`, { text });
            if (input) input.value = "";
            showToast("灵感已记录", "success");
            renderSidebarNotes();
        } catch (e) {
            showToast("保存失败：" + e.message, "error");
        }
    }

    // ===== 7. 人工节点交互页 =====

    function renderHuman(content) {
        const data = state.statusCache;
        const pending = data && data.pending_human;

        content.appendChild(el("div", { class: "page-header" }, [
            el("h2", { class: "page-title" }, "待确认事项"),
            el("p", { class: "page-desc" },
                "需要你选择主题、调整方法或审核任务时，在这里确认后后台才继续。科研对话只用于讨论，不会代替这里的正式确认。"),
        ]));

        if (!pending) {
            state.humanDraft = ""; // 无等待请求，清空草稿
            content.appendChild(el("div", { class: "human-empty" }, [
                el("div", { text: "当前没有需要你确认的事项。", class: "mb-0" }),
                el("div", { class: "small muted mt-8" },
                    `Pipeline 状态：${data ? data.status : "未知"}`),
            ]));
            const card = el("div", { class: "card" }, [
                el("div", { class: "card-title" }, "提示"),
                el("p", { class: "muted small" },
                    "此页面会自动每 2 秒检查一次是否有人工节点请求，无需手动刷新。"),
                el("div", { class: "btn-row mt-16" }, [
                    el("button", { class: "btn btn-secondary", onclick: () => pollStatus() }, "立即刷新"),
                ]),
            ]);
            content.appendChild(card);
            return;
        }

        // 有 pending 请求：渲染 prompt / options / textarea / 提交按钮
        const prompt = pending.prompt || "(无 prompt)";
        const options = Array.isArray(pending.options) ? pending.options : [];
        const allowFreeText = pending.allow_free_text !== false;
        const ctxDict = pending.context || {};
        // 检索范围配置（确认检索方向节点会下发 search_prefs）
        const searchPrefs = ctxDict.search_prefs || null;
        const savedPrefs = (data && data.search_prefs) || state.searchPrefs || {};

        const card = el("div", { class: "card human-card" });

        // Prompt 文本
        card.appendChild(el("div", { class: "card-title" }, "Pipeline 等待你的输入"));
        const promptBox = el("pre", { class: "human-prompt", text: prompt });
        card.appendChild(promptBox);

        // 检索范围配置表单（年份区间/期刊关键词）——确认检索方向节点
        if (searchPrefs) {
            const prefsBox = el("div", { class: "human-context mt-12 search-prefs-box" });
            prefsBox.appendChild(el("div", { class: "field-label mb-8" }, "📌 检索范围配置（抓取时按此过滤，可留空不限）"));

            const yMin = el("input", {
                class: "input paper-filter-year",
                type: "number", min: "1000", max: "2100",
                placeholder: "起始年（如 2018）",
                value: searchPrefs.year_min != null ? String(searchPrefs.year_min) : (savedPrefs.year_min || ""),
            });
            const yMax = el("input", {
                class: "input paper-filter-year",
                type: "number", min: "1000", max: "2100",
                placeholder: "结束年（如 2025）",
                value: searchPrefs.year_max != null ? String(searchPrefs.year_max) : (savedPrefs.year_max || ""),
            });
            const vHint = el("input", {
                class: "input paper-filter-input",
                type: "text",
                placeholder: "期刊/venue 关键词（如 Nature、ACS，多个用空格）",
                value: searchPrefs.venue_hint != null ? searchPrefs.venue_hint : (savedPrefs.venue_hint || ""),
            });

            const row1 = el("div", { class: "paper-filter-row" });
            row1.appendChild(el("span", { class: "paper-filter-label", text: "年份" }));
            row1.appendChild(yMin);
            row1.appendChild(el("span", { class: "paper-filter-sep", text: "–" }));
            row1.appendChild(yMax);
            row1.appendChild(el("span", { class: "paper-filter-label", text: "期刊" }));
            row1.appendChild(vHint);
            prefsBox.appendChild(row1);
            prefsBox.appendChild(el("div", { class: "small muted mt-8" },
                "抓取阶段会按此范围向 arXiv / Semantic Scholar 下发年份过滤；期刊关键词在拿到元数据后过滤。留空则不限。"));

            // 保存到模块级，提交时随 human-response 携带
            state.searchPrefsForm = { yMin, yMax, vHint };
            card.appendChild(prefsBox);
        }

        // 上下文信息（如推荐主题、节点 ID 等）
        const ctxKeys = Object.keys(ctxDict);
        if (ctxKeys.length) {
            const ctxBox = el("div", { class: "human-context mt-12" });
            ctxBox.appendChild(el("div", { class: "small muted mb-8" }, "上下文信息："));
            ctxKeys.forEach(k => {
                ctxBox.appendChild(el("div", { class: "small" },
                    `${k}: ${typeof ctxDict[k] === "object" ? JSON.stringify(ctxDict[k]) : String(ctxDict[k])}`));
            });
            card.appendChild(ctxBox);
        }

        // 选项按钮（若有）
        let selectedOption = null; // 当前选中的 option
        if (options.length) {
            const optsBox = el("div", { class: "human-options mt-16" });
            optsBox.appendChild(el("div", { class: "field-label mb-8" }, "选择一项："));
            options.forEach((opt, i) => {
                const optLabel = typeof opt === "string" ? opt : (opt.label || opt.value || JSON.stringify(opt));
                const optValue = typeof opt === "string" ? opt : (opt.value || optLabel);
                const btn = el("button", {
                    class: "btn btn-outline human-option-btn",
                    text: `[${i + 1}] ${optLabel}`,
                    onclick: () => {
                        selectedOption = optValue;
                        // 视觉选中态
                        optsBox.querySelectorAll(".human-option-btn").forEach(b => b.classList.remove("btn-success"));
                        btn.classList.add("btn-success");
                    },
                });
                optsBox.appendChild(btn);
            });
            card.appendChild(optsBox);
        }

        // 自由文本输入
        let textarea = null;
        if (allowFreeText) {
            const taField = el("div", { class: "field mt-16" }, [
                el("label", { class: "field-label", for: "human-response-input" },
                    options.length ? "或输入自定义响应：" : "输入响应："),
            ]);
            textarea = el("textarea", {
                class: "textarea",
                id: "human-response-input",
                placeholder: "请输入你的响应...",
                rows: 4,
            });
            // 恢复草稿（避免轮询重建时丢失输入）
            textarea.value = state.humanDraft || "";
            textarea.addEventListener("input", () => {
                state.humanDraft = textarea.value;
            });
            taField.appendChild(textarea);
            card.appendChild(taField);
        }

        // 提交按钮
        const actionRow = el("div", { class: "btn-row mt-16" }, [
            el("button", {
                class: "btn btn-success",
                id: "human-submit-continue",
                onclick: () => submitHumanResponse("continue", selectedOption, textarea),
            }, "提交并继续"),
            el("button", {
                class: "btn btn-secondary",
                id: "human-submit-abort",
                onclick: () => submitHumanResponse("abort", null, null),
            }, "中止 Pipeline"),
        ]);
        card.appendChild(actionRow);

        content.appendChild(card);
    }

    async function submitHumanResponse(action, selectedOption, textarea) {
        if (!state.currentProjectId) return;
        const text = textarea ? textarea.value.trim() : "";
        if (action === "continue" && !text && !selectedOption) {
            showToast("请填写响应内容或选择一个选项", "error");
            return;
        }
        // 检索范围配置：收集表单值（如有）
        let searchPrefs = null;
        const form = state.searchPrefsForm;
        if (form) {
            const yMinRaw = (form.yMin.value || "").trim();
            const yMaxRaw = (form.yMax.value || "").trim();
            const vHint = (form.vHint.value || "").trim();
            searchPrefs = {
                year_min: yMinRaw ? Number(yMinRaw) : null,
                year_max: yMaxRaw ? Number(yMaxRaw) : null,
                venue_hint: vHint || "",
            };
            state.searchPrefs = searchPrefs;
        }
        const payload = { action, text, selected_option: selectedOption };
        if (searchPrefs) payload.search_prefs = searchPrefs;
        try {
            await api("POST", `/api/projects/${state.currentProjectId}/human-response`, payload);
            state.humanDraft = "";
            state.humanFingerprint = null;
            showToast(action === "abort" ? "Pipeline 已中止" : "已提交响应，Pipeline 继续执行", "success");
            await pollStatus();
            renderPage();
        } catch (e) {
            showToast("提交失败：" + e.message, "error");
        }
    }

    // ===== 6c. 文献调研报告页（research 阶段核心产出）=====

    async function renderResearchReport(content) {
        if(window.SRA_WORKSPACE_VIEW){window.SRA_WORKSPACE_VIEW.mount(content,'reports',window.__sraApp);return;}
        content.appendChild(el("div", { class: "loading" }, "加载调研报告…"));
        try {
            const data = await api("GET", `/api/projects/${state.currentProjectId}/research-report`);
            state.researchReportCache = data;
            clear(content);

            content.appendChild(el("div", { class: "page-header" }, [
                el("h2", { class: "page-title" }, "研究现状"),
                el("p", { class: "page-desc" },
                    "回看已检索文献中的共识、分歧和待核实问题，为选题提供背景。内容只覆盖当前材料，不是完整领域综述；创新性仍需补查相关工作。原有调研报告和导出保留。"),
            ]));

            const report = data.report;
            if (!report) {
                content.appendChild(el("div", { class: "card" }, [
                    el("div", { class: "list-empty" },
                        "尚未形成研究现状汇总。可以先到文献库导入、筛选论文；已有报告将在这里展示。"),
                    el("div", { class: "btn-row mt-16" }, [
                        el("button", {
                            class: "btn btn-success",
                            onclick: () => setActivePage("papers"),
                        }, "去文献库"),
                    ]),
                ]));
                return;
            }

            // 顶部统计
            content.appendChild(el("div", { class: "counts-grid" }, [
                el("div", { class: "count-card highlight" }, [
                    el("div", { class: "count-label" }, "候选问题"),
                    el("div", { class: "count-value", text: String((report.gaps || []).length) }),
                    el("div", { class: "count-extra" }, "待核查相关工作与具体条件"),
                ]),
                el("div", { class: "count-card" }, [
                    el("div", { class: "count-label" }, "共识"),
                    el("div", { class: "count-value", text: String((report.consensus || []).length) }),
                    el("div", { class: "count-extra" }, "多方一致认同的陈述"),
                ]),
                el("div", { class: "count-card" }, [
                    el("div", { class: "count-label" }, "冲突"),
                    el("div", { class: "count-value", text: String((report.conflicts || []).length) }),
                    el("div", { class: "count-extra" }, "陈述相反的论断"),
                ]),
                el("div", { class: "count-card" }, [
                    el("div", { class: "count-label" }, "原报告置信度"),
                    el("div", { class: "count-value", text: (report.overall_confidence || 0).toFixed(2) }),
                    el("div", { class: "count-extra" }, "系统估计值，不是准确率或创新评分"),
                ]),
            ]));

            // 导航统计只由统一状态更新，浏览报告不再覆盖菜单数字。

            // Research Gaps（最重要的产出）
            const gapsCard = el("div", { class: "card" });
            gapsCard.appendChild(el("div", { class: "card-title" },
                `Research Gaps（${(report.gaps || []).length} 个）· 可证伪识别`));
            const gaps = report.gaps || [];
            if (!gaps.length) {
                gapsCard.appendChild(el("div", { class: "list-empty" }, "未识别到 Research Gap"));
            } else {
                const list = el("div", { class: "list" });
                gaps.forEach((g, i) => {
                    // ===== 结构化 Gap 字段解析（兼容旧版字符串）=====
                    const isStructured = g && typeof g === "object";
                    const gapText = isStructured
                        ? (g.gap || g.description || JSON.stringify(g))
                        : (g || "");
                    const gapType = (isStructured && g.type) ? g.type : "underexplored";
                    const importance = (isStructured && typeof g.importance === "number")
                        ? g.importance : null;
                    const actionability = (isStructured && g.actionability)
                        ? g.actionability : "medium";
                    const citedPids = (isStructured && Array.isArray(g.cited_paper_ids))
                        ? g.cited_paper_ids : [];
                    const citedChunks = (isStructured && Array.isArray(g.cited_chunk_ids))
                        ? g.cited_chunk_ids : [];
                    const rationale = (isStructured && g.rationale) ? g.rationale : "";

                    const item = el("div", { class: "list-item gap-item" });

                    // 头部：序号 + 标题 + type 彩色徽章
                    const headChildren = [
                        el("span", { class: "gap-number", text: `#${i + 1}` }),
                        el("span", { class: "list-item-title", text: gapText }),
                    ];
                    // type 徽章
                    headChildren.push(el("span", {
                        class: `gap-type-badge gap-type-${gapType}`,
                        text: gapType,
                        title: `Research Gap 类型：${gapType}`,
                    }));
                    // actionability 徽章
                    headChildren.push(el("span", {
                        class: `gap-actionability gap-action-${actionability}`,
                        text: `可操作 ${actionability}`,
                        title: `可操作性：${actionability}（高/中/低）`,
                    }));
                    // importance 显示
                    if (importance !== null) {
                        headChildren.push(el("span", {
                            class: "gap-importance mono small muted",
                            text: `重要性 ${importance.toFixed(2)}`,
                            title: "重要性 0~1",
                        }));
                    }
                    item.appendChild(el("div", { class: "list-item-head" }, headChildren));

                    // rationale
                    if (rationale) {
                        item.appendChild(el("div", { class: "gap-rationale small" },
                            `依据：${rationale}`));
                    }

                    // evidence（结构化：可点击 paper_id）
                    if (citedPids.length) {
                        const evBox = el("div", { class: "gap-evidence mt-8" });
                        evBox.appendChild(el("span", { class: "gap-evidence-label small muted", text: "证据链 paper_id：" }));
                        citedPids.slice(0, 5).forEach(pid => {
                            const link = el("span", {
                                class: "gap-paper-badge badge badge-info",
                                text: String(pid).length > 16 ? String(pid).slice(0, 14) + "…" : String(pid),
                                title: `点击跳转到论文浏览页：${pid}\n${citedChunks.length ? `含 chunk: ${citedChunks.length}` : ""}`,
                                onclick: (ev) => {
                                    ev.stopPropagation();
                                    state.pendingPaperId = pid;
                                    setActivePage("papers");
                                },
                            });
                            link.style.cursor = "pointer";
                            evBox.appendChild(link);
                        });
                        if (citedPids.length > 5) {
                            evBox.appendChild(el("span", {
                                class: "small muted",
                                text: ` 等 ${citedPids.length} 篇`,
                            }));
                        }
                        item.appendChild(evBox);
                    } else {
                        // 旧版兼容：从 evidence/source_papers 字段提取
                        const legacy = isStructured
                            ? (g.evidence || g.source_papers || [])
                            : [];
                        if (legacy.length) {
                            const evBox = el("div", { class: "gap-evidence mt-8" });
                            evBox.appendChild(el("span", {
                                class: "small muted",
                                text: `证据来源（旧版）：${legacy.slice(0, 3).join("、")}${legacy.length > 3 ? ` 等 ${legacy.length} 篇` : ""}`,
                            }));
                            item.appendChild(evBox);
                        }
                    }

                    // 可展开：展示完整字段
                    item.addEventListener("click", () => {
                        if (item.classList.contains("expanded")) {
                            item.classList.remove("expanded");
                            const body = item.querySelector(".list-item-body");
                            if (body) body.remove();
                            return;
                        }
                        item.classList.add("expanded");
                        const body = el("div", { class: "list-item-body" });
                        body.innerHTML = `
                            <dl>
                                <dt>Gap</dt><dd>${escapeHtml(gapText)}</dd>
                                <dt>类型</dt><dd><span class="gap-type-badge gap-type-${gapType}">${escapeHtml(gapType)}</span></dd>
                                <dt>重要性</dt><dd>${importance !== null ? importance.toFixed(2) : "—"}</dd>
                                <dt>可操作性</dt><dd><span class="gap-actionability gap-action-${actionability}">${escapeHtml(actionability)}</span></dd>
                                <dt>关联 paper_id</dt><dd class="mono">${citedPids.length ? escapeHtml(citedPids.join(", ")) : "—"}</dd>
                                <dt>关联 chunk_id</dt><dd class="mono">${citedChunks.length ? escapeHtml(citedChunks.join(", ")) : "—"}</dd>
                                <dt>依据</dt><dd>${rationale ? escapeHtml(rationale) : "—"}</dd>
                            </dl>
                        `;
                        item.appendChild(body);
                    });
                    list.appendChild(item);
                });
                gapsCard.appendChild(list);
            }
            content.appendChild(gapsCard);

            // 共识
            const consCard = el("div", { class: "card" });
            consCard.appendChild(el("div", { class: "card-title" },
                `共识（${(report.consensus || []).length} 条）`));
            const cons = report.consensus || [];
            if (!cons.length) {
                consCard.appendChild(el("div", { class: "list-empty" }, "暂无共识"));
            } else {
                const ul = el("ul", { class: "feature-list" });
                cons.forEach(c => {
                    const text = typeof c === "string" ? c : (c.statement || c.consensus || JSON.stringify(c));
                    ul.appendChild(el("li", {}, text));
                });
                consCard.appendChild(ul);
            }
            content.appendChild(consCard);

            // 冲突
            const confCard = el("div", { class: "card" });
            confCard.appendChild(el("div", { class: "card-title" },
                `冲突结论（${(report.conflicts || []).length} 处）· 处置建议`));
            const confs = report.conflicts || [];
            if (!confs.length) {
                confCard.appendChild(el("div", { class: "list-empty" }, "暂无冲突"));
            } else {
                const list = el("div", { class: "list" });
                confs.forEach((c, i) => {
                    const item = el("div", { class: "list-item conflict-item" });
                    let summary, suggestion, sources, positions;
                    if (typeof c === "string") {
                        summary = c; suggestion = ""; sources = []; positions = [];
                    } else {
                        summary = c.summary || c.conflict || c.description || c.topic || c.claim || "";
                        suggestion = c.suggestion || c.resolution || c.disposition || "";
                        sources = c.sources || c.papers || [];
                        positions = c.positions || c.sides || [];
                        // 若仍无 summary，把对象的 key-value 拼成可读文本（避免 JSON.stringify）
                        if (!summary) {
                            const parts = [];
                            for (const [k, v] of Object.entries(c)) {
                                if (typeof v === "string" && v.length < 200) {
                                    parts.push(`${k}: ${v}`);
                                }
                            }
                            summary = parts.join("；") || "(冲突详情无法解析)";
                        }
                    }
                    item.appendChild(el("div", { class: "list-item-head" }, [
                        el("span", { class: "conflict-number", text: `#${i + 1}` }),
                        el("span", { class: "list-item-title", text: summary }),
                    ]));
                    // 冲突双方立场列表（若存在）
                    if (positions && positions.length) {
                        const posList = el("ul", { class: "conflict-positions" });
                        positions.forEach(p => {
                            posList.appendChild(el("li", { class: "small", text: typeof p === "string" ? p : (p.position || p.side || JSON.stringify(p)) }));
                        });
                        item.appendChild(posList);
                    }
                    if (suggestion) {
                        item.appendChild(el("div", { class: "conflict-suggestion" },
                            `处置：${suggestion}`));
                    }
                    if (sources && sources.length) {
                        item.appendChild(el("div", { class: "gap-evidence" },
                            `涉及文献：${sources.slice(0, 3).join("、")}`));
                    }
                    // 结构化 source_paper_ids（证据链增强）
                    const confPids = (typeof c === "object" && Array.isArray(c.source_paper_ids))
                        ? c.source_paper_ids : [];
                    if (confPids.length) {
                        const evBox = el("div", { class: "gap-evidence mt-8" });
                        evBox.appendChild(el("span", { class: "small muted", text: "证据 paper_id： " }));
                        confPids.slice(0, 5).forEach(pid => {
                            const link = el("span", {
                                class: "gap-paper-badge badge badge-info",
                                text: String(pid).length > 16 ? String(pid).slice(0, 14) + "…" : String(pid),
                                title: `点击跳转到论文浏览页：${pid}`,
                                onclick: (ev) => {
                                    ev.stopPropagation();
                                    state.pendingPaperId = pid;
                                    setActivePage("papers");
                                },
                            });
                            link.style.cursor = "pointer";
                            evBox.appendChild(link);
                        });
                        item.appendChild(evBox);
                    }
                    list.appendChild(item);
                });
                confCard.appendChild(list);
            }
            content.appendChild(confCard);

            // 操作区
            content.appendChild(el("div", { class: "card" }, [
                el("div", { class: "card-title" }, "后续操作"),
                el("div", { class: "btn-row" }, [
                    el("button", {
                        class: "btn btn-secondary",
                        onclick: () => setActivePage("papers"),
                    }, "查看论文详情"),
                    el("button", {
                        class: "btn btn-secondary",
                        onclick: () => renderResearchReport(content),
                    }, "刷新报告"),
                ]),
            ]));
        } catch (e) {
            clear(content);
            content.appendChild(el("div", { class: "status-banner danger" },
                `加载失败：${escapeHtml(e.message)}`));
        }
    }

    // ===== 7. 人工节点交互页 =====

    // 结构化渲染 prompt 文本：识别编号列表、项目符号、标题行
    // 把一整块纯文本拆成有视觉层次的 DOM 节点，而非 <pre> 或单 div
    function renderPromptStructured(text) {
        const wrap = el("div", { class: "request-prompt-structured" });
        const lines = (text || "").split("\n");
        let currentList = null;  // 当前正在累积的 <ol> 或 <ul>
        let listType = null;     // "ol" / "ul"

        const flushList = () => {
            if (currentList) {
                wrap.appendChild(currentList);
                currentList = null;
                listType = null;
            }
        };

        lines.forEach(rawLine => {
            const line = rawLine.replace(/\s+$/, "");  // 去行尾空格
            const trimmed = line.trim();

            // 空行：结束当前列表
            if (!trimmed) {
                flushList();
                return;
            }

            // 编号列表项：  1. xxx  或  1) xxx  或  1、xxx
            const olMatch = trimmed.match(/^(\d+)[.、)]\s*(.+)/);
            if (olMatch) {
                if (listType !== "ol") { flushList(); currentList = el("ol", { class: "prompt-ol" }); listType = "ol"; }
                currentList.appendChild(el("li", { class: "prompt-li", text: olMatch[2] }));
                return;
            }

            // 项目符号：  - xxx  或  • xxx
            const ulMatch = trimmed.match(/^[-•·]\s*(.+)/);
            if (ulMatch) {
                if (listType !== "ul") { flushList(); currentList = el("ul", { class: "prompt-ul" }); listType = "ul"; }
                currentList.appendChild(el("li", { class: "prompt-li", text: ulMatch[1] }));
                return;
            }

            // 非列表行：先 flush，再判断是否是标题行（以冒号结尾且较短）
            flushList();
            if (trimmed.length <= 40 && /[:：]$/.test(trimmed)) {
                wrap.appendChild(el("div", { class: "prompt-heading", text: trimmed }));
            } else {
                wrap.appendChild(el("div", { class: "prompt-text", text: trimmed }));
            }
        });
        flushList();
        return wrap;
    }

    // ===== localStorage 持久化 =====

    function saveProjectToStorage(projectId) {
        try {
            localStorage.setItem("sra_project_id", projectId);
        } catch (e) { /* 隐私模式忽略 */ }
    }

    function clearProjectFromStorage() {
        try {
            localStorage.removeItem("sra_project_id");
        } catch (e) { /* 忽略 */ }
        // 同步清空侧边栏/顶栏徽章（论文数/缺口/Claim/实验等）
        ["badge-papers", "badge-gaps", "badge-report-gaps",
         "badge-claims", "badge-experiments", "badge-notes", "badge-human",
         "badge-topic-discovery", "badge-progress"].forEach(id => {
            const node = document.getElementById(id);
            if (node) { node.style.display = "none"; node.textContent = ""; }
        });
        // 重置导航条 → 全部 pending（无状态时 flowStepState 返回 pending）
        state.lastFlowActiveIdx = null;
        const host = document.getElementById("flow-nav-host");
        if (host) renderFlowNav();
        // 顶栏状态徽章也清空
        const topStatus = document.getElementById("topbar-status");
        if (topStatus) topStatus.style.display = "none";
    }

    function getProjectFromStorage() {
        try {
            return localStorage.getItem("sra_project_id");
        } catch (e) { return null; }
    }

    // ===== 初始化 =====

    async function init() {
        // 绑定导航
        document.querySelectorAll(".nav-item").forEach(n => {
            n.addEventListener("click", () => {
                if(n.getAttribute('aria-disabled')==='true'){showToast('请先选择一个项目；浏览不会自动创建研究。','info');return;}
                const page = n.getAttribute("data-page");
                if (page) setActivePage(page);
            });
        });

        // 顶部帮助按钮 + 帮助面板关闭
        const helpBtn = document.getElementById("topbar-help");
        if (helpBtn) helpBtn.addEventListener("click", toggleHelpPanel);
        const helpClose = document.getElementById("help-panel-close");
        if (helpClose) helpClose.addEventListener("click", closeHelpPanel);
        // 点击帮助面板背景关闭
        const helpPanel = document.getElementById("help-panel");
        if (helpPanel) {
            helpPanel.addEventListener("click", (e) => {
                if (e.target === helpPanel) closeHelpPanel();
            });
        }

        // 键盘快捷键
        setupKeyboardShortcuts();
        renderNavigationHelp();

        // 侧边栏灵感笔记小组件
        const snSave = document.getElementById("sidebar-note-save");
        if (snSave) snSave.addEventListener("click", saveSidebarNote);
        const snLink = document.getElementById("sidebar-note-link");
        if (snLink) snLink.addEventListener("click", () => setActivePage("notes"));
        const snInput = document.getElementById("sidebar-note-input");
        if (snInput) snInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                saveSidebarNote();
            }
        });
        // URL 深链支持：?project=<project_id>&page=<page>&paper=<paper_id>&exp=<exp_id>
        if(window.SRA_CONVERSATIONS) {
            const route=(input={})=>{
                const pid=Object.hasOwn(input,"projectId") ? input.projectId : state.currentProjectId;
                if(pid!==state.currentProjectId) {stopPolling();state.statusCache=null;state.workflowCache=null;state.domain="";}
                state.currentProjectId=pid;
                state.currentConversationId=pid ? (Object.hasOwn(input,"conversationId") ? input.conversationId : state.currentConversationId) : null;
                updateProjectIdDisplay();
                setActivePage(input.page || "dashboard",{replace:!!input.replace});
                document.body.classList.remove("ch-nav-open");
                document.getElementById("ch-menu")?.setAttribute("aria-expanded","false");
            };
            window.__sraApp={navigate:route,getRoute:()=>({projectId:state.currentProjectId,conversationId:state.currentConversationId,page:state.currentPage}),adopt:(pid,cid)=>{
                state.currentProjectId=pid;state.currentConversationId=cid;updateProjectIdDisplay();
                const url=new URL(location.href);url.searchParams.set("project",pid);url.searchParams.set("conversation",cid);url.searchParams.set("page","dashboard");
                history.pushState(null,"",url);
            }};
            const fromURL=()=>{
                const q=new URLSearchParams(location.search);
                state.pendingPaperId=q.get("paper");state.pendingExperimentId=q.get("exp");
                route({projectId:q.get("project"),conversationId:q.get("conversation"),page:q.get("page") || "dashboard",replace:true});
            };
            window.SRA_CONVERSATIONS.init(window.__sraApp);
            window.addEventListener("popstate",fromURL);
            document.getElementById("research-guide-toggle").onclick=()=>route({page:"dashboard"});
            fromURL();
            return;
        }
        //（page 直达页面；paper/exp 用于证据溯源定位到具体论文/实验）
        const params = new URLSearchParams(window.location.search);
        const urlProject = (params.get("project") || "").trim();
        const urlPage = (params.get("page") || "").trim();
        const urlPaper = (params.get("paper") || "").trim();
        const urlExp = (params.get("exp") || "").trim();
        if (urlProject) {
            state.currentProjectId = urlProject;
            updateProjectIdDisplay();
            if (urlPaper) state.pendingPaperId = urlPaper;
            if (urlExp) state.pendingExperimentId = urlExp;
            startPolling();
            if (urlPage && Object.hasOwn(NAVIGATION.pages, urlPage)) {
                setActivePage(urlPage);
            } else {
                setActivePage("progress");
            }
        }

        // 从 localStorage 恢复上次项目（解决刷新丢失问题）
        const savedPid = getProjectFromStorage();
        if (savedPid && !urlProject) {
            try {
                const data = await api("GET", `/api/projects/${savedPid}/status`);
                if (data && data.project_id) {
                    state.currentProjectId = savedPid;
                    state.statusCache = data;
                    state.runMode = data.run_mode || "";
                    updateProjectIdDisplay();
                    updateBadges(data);
                    startPolling();
                    renderSidebarNotes();
                    renderSidebarDownload();
                    setActivePage("dashboard");
                }
            } catch (e) {
                // 项目已不存在（服务器重启等），清除存储
                clearProjectFromStorage();
            }
        }

        // 默认页（仅当 URL 深链/localStorage 恢复都未设置项目时进入 dashboard，
        // 避免覆盖上方 urlProject 深链指定的 page）
        if (!urlProject && urlPage && Object.hasOwn(NAVIGATION.pages, urlPage)) {
            setActivePage(urlPage);
        } else if (!state.currentProjectId) {
            setActivePage("dashboard");
        }
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, {once:true});
    else init();
})();

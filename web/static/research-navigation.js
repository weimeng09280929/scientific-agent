/* 菜单、页面标题、指导上下文和历史链接共用同一份定义。后端阶段 ID 不变。 */
(() => {
    "use strict";
    const pages = {
        dashboard: {title:"科研对话", area:"overview"},
        "project-overview": {title:"项目总览", area:"overview"},
        create: {title:"我的项目", area:"overview"},
        "topic-discovery": {title:"选题探索", area:"question"},
        papers: {title:"文献与资料", area:"literature"},
        "research-report": {title:"研究现状", area:"literature"},
        gaps: {title:"研究问题", area:"question"},
        claims: {title:"研究问题 · 论断与证据", area:"question", owner:"gaps"},
        experiments: {title:"研究方案", area:"study"},
        writing: {title:"历史材料", area:"writing"},
        progress: {title:"任务活动", area:"overview"},
        notes: {title:"笔记", area:"question"},
        human: {title:"待确认事项", area:"overview"},
        methods: {title:"研究指南", area:"literature"},
        "reading-method": {title:"研究指南 · 论文阅读", area:"literature", owner:"methods"},
        "writing-method": {title:"研究指南 · 论文写作", area:"writing", owner:"methods"},
    };
    // 后台执行位置与用户浏览位置分开计算；兼容旧字符串和当前节点对象。
    function executionProgress(status, steps) {
        const data = status || {}, history = data.node_history || [];
        const current = data.current_node;
        const liveId = typeof current === "string" ? current : current?.node_id;
        const pendingId = data.pending_human?.node_id || data.pending_human?.context?.node_id;
        const latest = history.filter(item => !String(item.node_id || "").startsWith("cp_")).at(-1);
        const nodeId = pendingId || liveId || latest?.node_id || "";
        const usedDiscovery = !!(data.topic_discovery?.recommendations?.length || data.topic_discovery?.selected_topic)
            || data.run_mode === "topic_discovery" || history.some(item => ["trend_fetch", "trend_analysis", "topic_recommend", "topic_select"].includes(item.node_id));
        const finished = data.status === "completed" && data.run_mode !== "topic_discovery";
        let stepIndex = finished ? -1 : steps.findIndex(step => step.nodes.includes(nodeId));
        let stage = stepIndex >= 0 ? steps[stepIndex].stage : data.current_stage || "";
        if (data.status === "experiment_pending") {
            stage = "experiment";
            stepIndex = steps.findLastIndex(step => step.stage === stage);
        } else if (!finished && stepIndex < 0 && data.status !== "created") {
            stepIndex = steps.findIndex(step => step.stage === stage);
        }
        const states = steps.map((step, index) => {
            if (!usedDiscovery && step.stage === "topic_discovery") return history.length ? "skipped" : "pending";
            if (index === stepIndex) return ["failed", "blocked", "aborted"].includes(data.status) ? "failed" : "active";
            if (finished || data.stage_statuses?.[step.stage] === "done" || index < stepIndex) return "done";
            return "pending";
        });
        return {nodeId, stage, stepIndex, states, finished};
    }
    Object.values(pages).forEach(Object.freeze);
    window.SRA_NAVIGATION = Object.freeze({
        pages:Object.freeze(pages),
        executionProgress,
        shortcuts:Object.freeze({"1":"dashboard", "2":"create", "3":"progress", "4":"research-report", "5":"papers", "6":"gaps", "7":"experiments", "8":"writing", "9":"notes"}),
    });
})();

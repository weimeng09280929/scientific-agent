/* 只读推导研究主线：不启动任务、不生成指标、不将记录完整误作科学验证。 */
((root) => {
    "use strict";
    function analyze(workspace = {}, papers = [], focus = {}) {
        const items = workspace.items || [];
        const byId = new Map(items.map(item => [item.item_id, item]));
        const active = items.filter(item => item.status === "active");
        const knownPapers = new Set(papers.map(paper => paper.paper_id));
        function usable(item, seen = new Set()) {
            if (!item || item.status !== "active" || item.needs_review || seen.has(item.item_id)) return false;
            if (item.kind === "plan" && new Set((item.related_ids || []).filter(id => byId.get(id)?.kind === "question")).size !== 1) return false;
            const next = new Set(seen); next.add(item.item_id);
            return (item.paper_ids || []).every(id => knownPapers.has(id))
                && (item.related_ids || []).every(id => usable(byId.get(id), next));
        }
        function select(options, id) {
            return id ? options.find(item => item.item_id === id) || null : options.length === 1 ? options[0] : null;
        }
        const questions = active.filter(item => item.kind === "question");
        const question = select(questions, focus.question);
        const plans = question ? active.filter(item => item.kind === "plan" && (item.related_ids || []).includes(question.item_id)) : [];
        const plan = select(plans, focus.plan);
        const results = plan ? active.filter(item => item.kind === "result" && (item.related_ids || []).includes(plan.item_id)) : [];
        const validResults = results.filter(usableResult);
        function usableResult(item) {
            if (!usable(item)) return false;
            if (item.result_status !== "completed") return ["failed", "inconclusive"].includes(item.result_status);
            const values = Object.values(item.metrics || {});
            return values.length > 0 && values.every(value => typeof value === "number" && Number.isFinite(value));
        }
        const resultIds = new Set(validResults.map(item => item.item_id));
        const measuredIds = new Set(validResults.filter(item => item.result_status === "completed").map(item => item.item_id));
        const findings = active.filter(item => item.kind === "finding" && usable(item)
            && (item.related_ids || []).some(id => resultIds.has(id))
            && (["supports", "refutes"].includes(item.assessment)
                ? (item.related_ids || []).some(id => measuredIds.has(id)) : item.assessment === "inconclusive"));
        const branch = [question, plan, ...validResults, ...findings].filter(Boolean);
        const paperIds = [...new Set(branch.flatMap(item => item.paper_ids || []))];
        const issues = [];
        if (question && !(question.paper_ids || []).length) issues.push("当前问题尚未关联论文，研究依据仍需补充；这不阻止先保存待查证的想法。");
        if (paperIds.some(id => !knownPapers.has(id))) issues.push("主线引用的部分论文不在当前文献库中，需要核对来源。");
        if (results.length !== validResults.length) issues.push("部分结果需要复核或缺少有效实测指标，未作为可用结果计入。");
        const steps = [
            {kind: "question", label: "研究问题", count: question ? 1 : 0, state: question ? usable(question) ? "已保存问题" : "问题待复核" : "尚未选择问题"},
            {kind: "plan", label: "方法方案", count: plan ? 1 : 0, state: plan ? usable(plan) ? "已关联方案" : "方案待复核" : plans.length > 1 ? "请选择当前方案" : "尚无关联方案"},
            {kind: "result", label: "实际结果", count: validResults.length, state: measuredIds.size ? "已有实测指标" : validResults.some(item => item.result_status === "failed") ? "记录了运行失败" : validResults.length ? "结果尚无法定论" : "等待结果记录"},
            {kind: "finding", label: "结果判断", count: findings.length, state: findings.length > 1 ? "有多条判断，请核对" : findings.length ? ({supports: "用户判断：支持", refutes: "用户判断：反驳", inconclusive: "用户判断：无法定论"})[findings[0].assessment] : "尚未记录判断"},
        ];
        let next;
        if (!question) next = questions.length ? {action: "select", text: "请选择本次推进的研究问题，不将不同问题的材料混在一起。"} : {action: "create", kind: "question", text: "先保存一个具体、可以检验的研究问题；已有论文和原流程产物不需要删除或重跑。"};
        else if (!usable(question)) next = {action: "revise", item: question, text: "问题或其依据已变化，先修订问题，再逐项核对下游方案与结果。"};
        else if (!plan) next = plans.length ? {action: "select", text: "当前问题有多个方案，请明确本次要推进哪一个。"} : {action: "create", kind: "plan", relatedId: question.item_id, text: "为当前问题保存方法方案，写清对照、数据条件、指标和支持／反驳标准。"};
        else if (!usable(plan)) next = {action: "revise", item: plan, text: "当前方案依赖已变化，先重新核对并修订方案，保留旧版本。"};
        else next = {action: "plan_ready", item: plan, text: "方案已整理，可审阅、修改和导出；本轮止于研究方案，不进入实验或后续写作。"};
        return {questions, question, plans, plan, results, validResults, findings, steps:steps.slice(0,2), historicalSteps:steps.slice(2), issues, next,
            planReady:!!question && !!plan && usable(question) && usable(plan),
            paperIds, recordsReady: !!question && !!plan && usable(question) && usable(plan) && validResults.length > 0 && findings.length > 0};
    }
    const api = {analyze};
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    root.SRA_RESEARCH_FLOW = api;
})(typeof window !== "undefined" ? window : globalThis);

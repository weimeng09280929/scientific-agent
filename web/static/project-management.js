/* 项目管理的只读建议：研究状态与后台状态分开，不自动确认科研结论。 */
((root) => {
    "use strict";
    function summarize(workspace={}, papers=[], status={}, focus={}, workflow=null) {
        const flow=workflow?.flow || root.SRA_RESEARCH_FLOW.analyze(workspace,papers,focus);
        const brief=workspace.brief || {}, active=(workspace.items || []).filter(item=>item.status==="active");
        const review=active.filter(item=>item.needs_review);
        const labels={running:"后台运行中",in_progress:"后台运行中",pending_human:"等待人工确认",completed:"后台任务已结束",failed:"后台任务失败",blocked:"后台任务受阻",stopped:"后台任务已停止",aborted:"后台任务已取消",created:"尚未启动后台任务",experiment_pending:"历史实验阶段（仅查看）",experiment_failed:"历史实验阶段（仅查看）"};
        const blockers=[...flow.issues];
        if(!brief.goal?.trim()) blockers.unshift("尚未明确项目目标，建议先说明这次希望完成什么。");
        if(!brief.resources?.trim() || !brief.time_budget?.trim()) blockers.push("设备、数据或时间限制尚未填写，后续方案仍需核对可实施性。");
        if(!papers.length) blockers.push("当前文献库为空；有关研究现状和创新性的判断缺少可核对材料。");
        if(review.length) blockers.push(`有 ${review.length} 条有效记录需要复核，不能作为已确认的研究进展。`);
        if(!["discuss","plan_ready"].includes(flow.next.action)) blockers.push(flow.next.text);
        let researchState;
        if(flow.next.action==="select") researchState=flow.question ? "待选择当前方案" : "待选择研究问题";
        else if(flow.next.action==="revise") researchState="研究依据待复核";
        else if(flow.next.kind==="question") researchState="研究问题待明确";
        else if(flow.next.kind==="plan") researchState="方法方案待完善";
        else if(flow.next.kind==="result") researchState="等待实际结果";
        else if(flow.next.kind==="finding") researchState=flow.validResults.some(item=>item.result_status==="failed") ? "执行问题待分析" : "结果含义待判断";
        else if(flow.next.action==="plan_ready") researchState="方案已整理，待审阅与导出";
        else researchState="请核对当前方案与依据";
        const tasks=[];
        if(!brief.goal?.trim() || !brief.resources?.trim() || !brief.time_budget?.trim()) tasks.push({id:"brief",action:"brief",title:"补充项目目标与限制",text:"写清研究目标、已有基础、时间和可用资源，让后续建议符合实际条件。"});
        tasks.push({id:`flow:${flow.question?.item_id || "none"}:${flow.plan?.item_id || "none"}:${flow.next.action}:${flow.next.kind || ""}`,action:"flow",title:({select:"明确本次推进的主线",revise:"复核已有研究记录",plan_ready:"审阅、修改与导出方案"})[flow.next.action] || `补充${({question:"研究问题",plan:"方法方案"})[flow.next.kind] || "研究条件"}`,text:flow.next.text});
        if(!papers.length || flow.question && !(flow.question.paper_ids || []).length) tasks.push({id:`papers:${flow.question?.item_id || "none"}`,action:"papers",title:"补充并核对相关文献",text:"查找最接近的工作，并把实际参考的论文关联到研究问题；不把有摘要视为已读全文。"});
        return {flow,brief,researchState,systemState:labels[status.status] || "正在读取后台状态",systemError:status.error || "",blockers:[...new Set(blockers)],tasks:tasks.slice(0,3),inventory:{papers:papers.length,questions:active.filter(i=>i.kind==="question").length,plans:active.filter(i=>i.kind==="plan").length,results:active.filter(i=>i.kind==="result").length,findings:active.filter(i=>i.kind==="finding").length,needsReview:review.length,legacyExperiments:status.counts?.experiments || 0}};
    }
    const api={summarize};
    if(typeof module!=="undefined" && module.exports) module.exports=api;
    root.SRA_PROJECT_MANAGEMENT=api;
})(typeof window!=="undefined" ? window : globalThis);

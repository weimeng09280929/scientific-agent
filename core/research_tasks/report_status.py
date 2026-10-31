"""Scalar-only, version-local content labels, independent of task lifecycle."""


def report_status(status, mode=None, synthesis_note="", catalog_kind=None):
    if mode == "rules" and synthesis_note:
        content, label = "synthesis_failed_catalog_only", "资料目录已保存，模型内容整理未成功"
    elif mode == "rules" and catalog_kind == "metadata_only":
        content, label = "metadata_catalog_only", "仅元信息目录，未做内容综合"
    elif mode == "rules":
        content, label = "rules_catalog_only", "规则资料目录，未做内容综合"
    elif mode == "model":
        content, label = "synthesized", "研究内容已整理（基于所提供材料）"
    else:
        content, label = "unknown", "历史报告，内容状态未记录"
    prefix = {"partial":"部分完成（有来源失败）；", "failed":"任务失败；已有报告保留；",
              "cancelled":"任务已取消；已有报告保留；", "interrupted":"任务已中断；已有报告保留；"}.get(status, "")
    return {"content_status":content, "content_label":label, "status_label":prefix+label}

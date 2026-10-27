"""Read model configuration without constructing clients or testing connectivity."""

from __future__ import annotations

import os
from collections.abc import Mapping

import yaml

from core.config import GlobalConfig
from core.llm.task_router import TaskRouter


# Only tasks used by the public discussion/search/plan workspace are required.
WORKSPACE_TASKS = (
    ("literature_search", "研究方向讨论"),
    ("paper_chunk_summarize", "材料阅读"),
    ("ideation_brainstorm", "研究问题讨论"),
    ("design_method_formalize", "方案生成与修订"),
    ("research_plan", "检索查询规划"),
    ("research_rank", "检索结果筛选"),
    ("research_synthesize", "研究现状整理"),
)

_CREDENTIAL_ATTRIBUTES = {
    "openai": "openai_api_key_env",
    "anthropic": "anthropic_api_key_env",
    "minimax": "minimax_api_key_env",
    "mimo": "mimo_api_key_env",
    "deepseek": "deepseek_api_key_env",
    "spark": "spark_api_key_env",
    "siliconflow": "siliconflow_api_key_env",
    "local": "local_model_base_url_env",
}


def runtime_status(
    config: GlobalConfig, environ: Mapping[str, str] | None = None
) -> dict:
    """Inspect active configuration; never return credentials, endpoints or paths."""
    environ = os.environ if environ is None else environ
    try:
        router = TaskRouter.load(config.llm.tasks_config_path)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError):
        router = None

    checks = []
    for task_type, label in WORKSPACE_TASKS:
        status, message = "missing_route", "未找到此功能的任务路由，请检查任务配置。"
        if router is None:
            status, message = "invalid_route", "任务配置无法读取，请检查配置文件后重启服务。"
        elif task_type in router.list_tasks():
            route = router.get(task_type)
            if (
                not isinstance(route.provider, str)
                or not route.provider.strip()
                or not isinstance(route.model, str)
                or not route.model.strip()
            ):
                status, message = "invalid_route", "此功能缺少有效的服务商或模型配置。"
            elif route.provider not in _CREDENTIAL_ATTRIBUTES:
                status, message = "unsupported_provider", "此功能的服务商尚未接入，请调整任务路由。"
            else:
                key_name = getattr(config.llm, _CREDENTIAL_ATTRIBUTES[route.provider])
                if str(environ.get(key_name) or "").strip():
                    status = "configured_unverified"
                    message = "已检测到所需配置，尚未验证连接、权限、余额或模型可用性。"
                else:
                    status = "missing_credentials"
                    message = (
                        "尚未配置本地模型服务地址。"
                        if route.provider == "local"
                        else "尚未配置此功能所用服务商的模型凭据。"
                    )
        checks.append(
            {"task_type": task_type, "label": label, "status": status, "message": message}
        )

    configured = all(c["status"] == "configured_unverified" for c in checks)
    if config.dry_run:
        status, title, message = (
            "rules_mode",
            "当前使用规则指导",
            "可整理问题和已有材料，不调用模型。规则回复不等同于模型科研分析；在线文献检索仍需要网络。",
        )
    elif not configured:
        status, title, message = (
            "needs_configuration",
            "模型配置尚未完成",
            "当前已启用模型模式，但部分科研功能缺少配置。请先补齐配置，或体验无需模型的离线教学案例。",
        )
    else:
        status, title, message = (
            "configured_unverified",
            "模型配置已检测到，连接尚未验证",
            "此检查未发送模型请求；实际调用仍取决于网络、权限、余额和模型是否可用。",
        )

    actions = []
    if not configured:
        actions.append(
            {
                "kind": "configure_model",
                "label": "补齐模型配置",
                "detail": "在服务端配置当前任务路由对应的模型凭据；填写其他服务商的密钥不会自动切换路由。",
            }
        )
    if config.dry_run or not configured:
        actions.append(
            {
                "kind": "restart_server",
                "label": "配置后重启服务",
                "detail": "按 .env.example 配置，确认需要真实模型调用时再设置 SRA_DRY_RUN=false，重启后重新检查。",
            }
        )
    actions.append(
        {
            "kind": "try_teaching",
            "label": "先体验离线教学",
            "detail": "教学案例使用预先整理的资料，不访问外网、不调用模型，可体验从阅读到方案审阅的流程。",
        }
    )
    return {
        "schema_version": "runtime-status.v1",
        "mode": "rules" if config.dry_run else "model",
        "status": status,
        "title": title,
        "message": message,
        "connection_status": "not_checked",
        "model_calls_made": False,
        "checks": checks,
        "actions": actions,
        "offline_experience": {
            "available": True,
            "label": "离线教学案例",
            "description": "无需配置模型，可查看案例依据、选择研究问题、审阅和导出方案。",
        },
    }

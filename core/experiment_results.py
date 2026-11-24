"""用户实验结果的输入契约。完成执行不等于证明研究设想。"""
from __future__ import annotations

import math
import hashlib
import json
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


def has_real_metrics(metrics) -> bool:
    return isinstance(metrics, dict) and bool(metrics) and all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        for value in metrics.values()
    )


def experiment_signature(store, experiment_ids) -> str:
    """Detect changed inputs, not scientific validity; exclude unrelated legacy tasks."""
    rows = []
    from core.knowledge.store import StoreError
    for experiment_id in sorted(experiment_ids):
        try:
            experiment = store.get_experiment(experiment_id)
            rows.append(experiment.model_dump(mode="json"))
        except StoreError:
            # Dry-run IDs and genuinely missing records remain distinguishable
            # without pretending that a result has been independently verified.
            rows.append({"experiment_id": experiment_id, "missing": True})
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class SubmittedResult(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str = Field(default="", max_length=300)
    experiment_id: str | None = Field(default=None, max_length=200)
    status: Literal["success", "completed", "failed", "inconclusive"]
    metrics: dict[str, Annotated[float, Field(strict=True)]] = Field(default_factory=dict, max_length=100)
    summary: str = Field(default="", max_length=6000)
    claim_assessments: dict[str, Literal["supports", "refutes", "inconclusive"]] = Field(default_factory=dict, max_length=100)

    @model_validator(mode="after")
    def check_execution(self):
        if not self.experiment_id and not self.name.strip():
            raise ValueError("须提供 experiment_id 或任务名称")
        if self.status in ("success", "completed") and not self.metrics:
            raise ValueError("已完成的计算实验必须填写实际数值指标；失败或无法定论请选择对应状态")
        if self.status not in ("success", "completed") and any(v != "inconclusive" for v in self.claim_assessments.values()):
            raise ValueError("运行失败或无法定论不能用于确认支持或反驳研究设想")
        return self

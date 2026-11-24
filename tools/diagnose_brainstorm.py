"""复用已有材料检查模型响应；不保存思路、不修改项目。仅打印响应结构诊断。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime.cli import _load_env
from core.config import get_config
from core.knowledge import KnowledgeStore
from core.llm import LLMRegistry
from core.llm.base import StructuredOutputRequest
from core.orchestration.context import ExecutionContext
from core.workflow_checkpoint import restore_context
from stages.common import DRY_RUN, KNOWLEDGE_STORE, LLM_REGISTRY, RESEARCH_TOPIC
from stages.ideation.agents import BrainstormAgent, BrainstormSchema


def main():
    _load_env()
    config = get_config()
    store = KnowledgeStore(config.paths.project_db(sys.argv[1]))
    context = ExecutionContext(project_id=sys.argv[1])
    restore_context(context, store, False)
    context.set(DRY_RUN, False)
    context.set(KNOWLEDGE_STORE, store)
    context.set(RESEARCH_TOPIC, store.get_kv("research.topic", ""))
    calls = []
    class Capture:
        def structured_output(self, **kwargs):
            calls.append(kwargs)
            raise RuntimeError("capture only; no project mutation")
    context.set(LLM_REGISTRY, Capture())
    BrainstormAgent("brainstorm").run(context)
    if not calls:
        raise RuntimeError("No model call captured")
    registry = LLMRegistry.from_config(config)
    cfg = registry._router.get("ideation_brainstorm")
    provider = registry._get_provider(cfg.provider)
    args = {key: value for key, value in calls[0].items() if key != "task_type"}
    request = StructuredOutputRequest(**args, max_tokens=cfg.max_tokens)
    messages = provider._build_messages_for_structured(request, BrainstormSchema)
    response = provider._client.chat.completions.create(
        model=cfg.model, messages=messages, temperature=cfg.temperature,
        max_tokens=cfg.max_tokens, response_format={"type":"json_object"},
    )
    content = response.choices[0].message.content or ""
    print(json.dumps({"finish_reason":response.choices[0].finish_reason,
        "content_length":len(content), "prompt_chars":len(args.get("prompt", "")),
        "max_tokens":cfg.max_tokens, "usage":response.usage.model_dump() if response.usage else None}, ensure_ascii=False))
    try:
        parsed = json.loads(provider._extract_json(content))
        print(json.dumps({"top_level_keys":list(parsed)[:12], "idea_count":len(parsed.get("ideas", []))}, ensure_ascii=False))
        try:
            result = BrainstormSchema.model_validate(parsed)
            print(json.dumps({"valid":True, "idea_count":len(result.ideas)}))
        except Exception as error:
            print(json.dumps({"valid":False, "errors":[{"loc":e["loc"],"type":e["type"]} for e in error.errors()]}, ensure_ascii=False))
    except ValueError:
        print(json.dumps({"valid_json":False}))


if __name__ == "__main__":
    main()

"""Run the six authorized paper-reading replays in a standalone audit ledger."""

from argparse import ArgumentParser
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3

import httpx

from core.invocation_audit import AuditScope, stamp
from core.tools.search_result import retry_delay
from tools.serve_budgeted_local import MODEL as FLASH, validate_prices
from tools.serve_guided_experience import load_tokendance_endpoint, tokendance_price_snapshot, visible_content

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "docs" / "paper-understanding-accuracy"
ROUND = "pro-b6-261111"
RUN_ROOT = ROOT / ".pytest_cache" / "real-user-acceptance-stage5" / "rounds" / ROUND
MODEL = "deepseek-v4-pro"
ENDPOINT = "https://tokendance.space/gateway/v1/chat/completions"
PRICE_URL = "https://www.tokendance.space/portal/api/models/" + MODEL
MAX_BYTES = 104_000
MAX_OUTPUT = 8192
CASES = ("D1", "D2", "B1", "B1f", "B2", "G1")
FROZEN = {
    "model-inputs.json": "5ad0cb8068505f778d3b6510c1495859baf66eb10cf8a4e50dd5269c9a82d5e1",
    "gateway-responses.json": "900bfddc490036cb294c74bd52976a12bc4bc57b2b2bf1d146df3391b8203f21",
    "criteria.md": "75ad8dce972b951fd22b7f7bb9e760161cfc099ec441f234a70203fb9d83be61",
    "attempt-index.json": "9aef93a9453dcda5fad1fc7cf42c4c25bc28e3bd97cf23967a4caf92a244e824",
}


def frozen_requests(archive=ARCHIVE):
    contents = {}
    for name, expected in FROZEN.items():
        blob = (archive / name).read_bytes()
        if sha256(blob).hexdigest() != expected:
            raise ValueError(f"冻结文件校验失败：{name}")
        contents[name] = blob
    index = [row for row in json.loads(contents["attempt-index.json"]) if row["mode"] == "B"]
    if tuple(row["case"] for row in index) != CASES:
        raise ValueError("B 组范围与授权不符")
    inputs = {row["attempt_id"]: row for row in json.loads(contents["model-inputs.json"])}
    result = {}
    for row in index:
        source = inputs[row["attempt_id"]]
        payload = source["request"]
        if (source["attempt"] != row["attempt"] or payload["model"] != FLASH
                or payload["temperature"] != 0.2 or payload["max_tokens"] != MAX_OUTPUT
                or payload["response_format"] != {"type": "json_object"}
                or set(payload) != {"messages", "model", "max_tokens", "response_format", "temperature"}
                or not payload["messages"]
                or any(set(message) != {"role", "content"}
                       or message["role"] not in {"system", "user", "assistant"}
                       or not isinstance(message["content"], str) for message in payload["messages"])):
            raise ValueError("冻结请求形状与授权不符")
        result[row["case"]] = source
    return result


def check_wire(case, request, approved, credential):
    if not credential or request.headers.get("authorization") != f"Bearer {credential}":
        raise ValueError("缺少本轮 TokenDance 鉴权")
    if request.method != "POST" or str(request.url) != ENDPOINT or case not in approved:
        raise ValueError("请求不属于本轮六题或指定端点")
    if len(request.content) > MAX_BYTES or credential.encode() in request.content:
        raise ValueError("请求超出文字范围或大小限制")
    expected = {**approved[case]["request"], "model": MODEL}
    # Compare the actual serialized body, including field types and all text.
    if request.content != httpx.Request("POST", ENDPOINT, json=expected).content:
        raise ValueError("实际入模内容与冻结载荷不一致")


def current_prices(client):
    response = client.get(PRICE_URL, follow_redirects=False)
    response.raise_for_status()
    snapshot = tokendance_price_snapshot(response.json())
    if "openai:chat-completions" not in snapshot.get("supported_protocols", []):
        raise ValueError("目录未确认候选支持已批准的请求协议")
    pricing = snapshot["pricing"]
    groups = [pricing]
    timed = pricing.get("time_pricing", {})
    groups.append({"items": timed.get("base_items", []), "reference": timed.get("base_reference", [])})
    groups.extend(timed.get("options", []))
    rates = {"basis": PRICE_URL + " (verified peak CNY/M tokens)"}
    for key, suffix in (("input", "input_tokens"), ("output", "completion_tokens")):
        rates[key] = max(float(plan["rate"]) for group in groups for field in ("items", "reference")
                         for item in group.get(field, []) if item["id"] == "openai:chat-completions:" + suffix
                         for plan in item["plans"])
    validate_prices(snapshot, model=MODEL, pricing=rates, require_cny=True)
    return snapshot, rates


def prepare_ledger(directory, snapshot, rates):
    if directory.resolve() != directory or (directory / "comparison.sqlite").resolve() != directory / "comparison.sqlite":
        raise ValueError("对照账本路径不安全")
    path = directory / "comparison.sqlite"
    directory.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        # A missing ledger in an existing round must not reset spent attempts.
        directory.mkdir()
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("CREATE TABLE IF NOT EXISTS comparison_config (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        conn.execute("""CREATE TABLE IF NOT EXISTS comparison_cases (
            name TEXT PRIMARY KEY, flash_attempt TEXT NOT NULL, request_sha256 TEXT NOT NULL,
            claimed_at TEXT NOT NULL, audit_id TEXT, receipt TEXT)""")
        data = {"model": MODEL, "endpoint": ENDPOINT, "cases": list(CASES), "frozen": FROZEN,
                "max_attempts": len(CASES), "cost_limit": None, "max_bytes": MAX_BYTES, "max_output": MAX_OUTPUT,
                "rates": rates, "pricing_checked_at": stamp(), "price_snapshot": snapshot}
        row = conn.execute("SELECT data FROM comparison_config WHERE id=?", (ROUND,)).fetchone()
        if row:
            existing = json.loads(row[0])
            if any(existing.get(k) != v for k, v in data.items() if k not in ("price_snapshot", "pricing_checked_at")):
                raise ValueError("账本固定范围或保护费率与当前执行不符")
        else:
            conn.execute("INSERT INTO comparison_config VALUES (?,?)", (ROUND, json.dumps(data)))
    return path


def gateway_receipt(response, credential):
    info = {"status": response.status_code, "id": None, "actual_model": None, "usage": None, "choices": []}
    try:
        body = response.json()
    except ValueError:
        return info
    if not isinstance(body, dict):
        return info
    if response.is_success:
        info["id"] = visible_content(body.get("id"), credential)
        info["actual_model"] = visible_content(body.get("model"), credential)
        usage = body.get("usage")
        if isinstance(usage, dict):
            info["usage"] = {
                k: v for k, v in usage.items()
                if k.endswith("_tokens") and type(v) is int and v >= 0
            }
            for key in ("prompt_tokens_details", "completion_tokens_details"):
                if isinstance(usage.get(key), dict):
                    info["usage"][key] = {k: v for k, v in usage[key].items() if type(v) is int and v >= 0}
        choices = body.get("choices")
        for choice in choices if isinstance(choices, list) else []:
            if isinstance(choice, dict) and isinstance(choice.get("message"), dict):
                info["choices"].append({
                    "finish_reason": visible_content(choice.get("finish_reason"), credential),
                    "content": visible_content(choice["message"].get("content"), credential),
                })
    elif isinstance(body.get("error"), dict):
        code = body["error"].get("code")
        info["error_code"] = visible_content(code, credential) if isinstance(code, str) else None
    return info


def send_case(client, database, case, approved, credential, rates):
    if case not in approved or case not in CASES:
        raise ValueError("请求不属于本轮六题")
    payload = {**approved[case]["request"], "model": MODEL}
    request = client.build_request("POST", ENDPOINT, json=payload,
                                   headers={"Authorization": f"Bearer {credential}"})
    check_wire(case, request, approved, credential)
    with closing(sqlite3.connect(database, timeout=15)) as conn, conn:
        claimed = conn.execute("INSERT OR IGNORE INTO comparison_cases VALUES (?,?,?,?,NULL,NULL)",
                               (case, approved[case]["attempt_id"], sha256(request.content).hexdigest(), stamp()))
        if not claimed.rowcount:
            return "already_claimed"

    scope = AuditScope(database, ROUND, f"{ROUND}:{case}", group_id=ROUND, request_id=case,
                       limits={"model": len(CASES), "source": 0}, pricing=rates, expected_model=MODEL)
    reserved = ((len(request.content) + 4096) * rates["input"] + MAX_OUTPUT * rates["output"]) / 1_000_000
    ticket = scope.begin("network", "model", MODEL, reserved_cost=reserved)
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("UPDATE comparison_cases SET audit_id=? WHERE name=?", (ticket[0], case))
    try:
        response = client.send(request, follow_redirects=False)
    except httpx.HTTPError as exc:
        outcome = type(exc).__name__
        receipt = {"received": False, "outcome": outcome}
        scope.finish(ticket, outcome=outcome)
    else:
        receipt = gateway_receipt(response, credential)
        outcome = "success" if response.is_success else "http_error"
        scope.finish(ticket, outcome=outcome, http_status=response.status_code, usage=receipt["usage"],
                     retry_after=retry_delay(response.headers.get("Retry-After")))
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("UPDATE comparison_cases SET receipt=? WHERE name=?", (json.dumps(receipt, ensure_ascii=False), case))
    return outcome


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--allow-live", action="store_true")
    args = parser.parse_args()
    approved = frozen_requests()
    if not args.allow_live:
        print(f"Frozen archive verified: {len(approved)} cases. Live transport disabled.")
        return
    if load_tokendance_endpoint() != ENDPOINT:
        parser.error("当前配置端点与六题授权不符")
    credential = os.environ["DEEPSEEK_API_KEY"].strip()
    with httpx.Client(transport=httpx.HTTPTransport(retries=0), follow_redirects=False,
                      trust_env=False, timeout=120) as client:
        snapshot, rates = current_prices(client)
        database = prepare_ledger(RUN_ROOT, snapshot, rates)
        print(f"Verified pricing (CNY/M): {rates['input']} / {rates['output']}", flush=True)
        for case in CASES:
            outcome = send_case(client, database, case, approved, credential, rates)
            print(f"{case}: {outcome}", flush=True)


if __name__ == "__main__":
    main()

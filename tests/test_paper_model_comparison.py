from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import sqlite3
from threading import Event

import httpx
import pytest

from tools import compare_paper_models as comparison


SECRET = "test-bearer-never-record"


@pytest.fixture(scope="module")
def approved():
    return comparison.frozen_requests()


@pytest.fixture(scope="module")
def catalog():
    records = json.loads((comparison.ARCHIVE / "candidate-catalog.json").read_bytes())
    return records["checks"][comparison.MODEL]["public_model"]


@pytest.fixture
def ledger(tmp_path, catalog):
    def price(request):
        assert str(request.url) == comparison.PRICE_URL
        assert "authorization" not in request.headers
        return httpx.Response(200, json=catalog)

    with httpx.Client(transport=httpx.MockTransport(price)) as client:
        snapshot, rates = comparison.current_prices(client)
    assert rates["input"] == 9 and rates["output"] == 27
    return comparison.prepare_ledger(tmp_path / "round", snapshot, rates), rates


def receipt():
    return httpx.Response(200, json={
        "model": comparison.MODEL, "id": "gateway-test-id",
        "choices": [{"message": {"content": "visible " + SECRET + "<think>hidden</think>",
                                 "reasoning_content": "internal"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
    })


def test_wire_is_exact_except_model(approved):
    assert tuple(approved) == comparison.CASES
    for case, source in approved.items():
        payload = {**source["request"], "model": comparison.MODEL}
        request = httpx.Request("POST", comparison.ENDPOINT, json=payload,
                                headers={"Authorization": f"Bearer {SECRET}"})
        comparison.check_wire(case, request, approved, SECRET)
        assert {**json.loads(request.content), "model": comparison.FLASH} == source["request"]


@pytest.mark.parametrize("change", ["endpoint", "model", "temperature", "content", "max_tokens", "format", "extra", "auth", "case"])
def test_wire_rejects_scope_changes(approved, change):
    payload = {**approved["D1"]["request"], "model": comparison.MODEL}
    case = "D1"
    endpoint = comparison.ENDPOINT
    secret = SECRET
    if change == "endpoint":
        endpoint = "https://tokendance.space/other"
    elif change == "content":
        payload["messages"] = [*payload["messages"], {"role": "user", "content": "changed"}]
    elif change == "auth":
        secret = "wrong"
    elif change == "case":
        case = "H1"
    else:
        field, value = {
            "model": ("model", comparison.FLASH), "temperature": ("temperature", 0.3),
            "max_tokens": ("max_tokens", 12000), "format": ("response_format", {"type": "text"}),
            "extra": ("seed", 42),
        }[change]
        payload[field] = value
    request = httpx.Request("POST", endpoint, json=payload, headers={"Authorization": f"Bearer {secret}"})
    with pytest.raises(ValueError):
        comparison.check_wire(case, request, approved, SECRET)


@pytest.mark.parametrize("name", comparison.FROZEN)
def test_frozen_files_cannot_be_changed(tmp_path, name):
    for filename in comparison.FROZEN:
        data = (comparison.ARCHIVE / filename).read_bytes()
        (tmp_path / filename).write_bytes(data + (b" " if name == filename else b""))
    with pytest.raises(ValueError, match=name):
        comparison.frozen_requests(tmp_path)


def test_six_attempts_include_redirect_and_timeout(approved, catalog, ledger):
    database, rates = ledger
    calls = []

    def gateway(request):
        case = comparison.CASES[len(calls)]
        comparison.check_wire(case, request, approved, SECRET)
        calls.append(case)
        if case == "D1":
            return httpx.Response(302, headers={"Location": "https://forbidden.example/"})
        if case == "D2":
            raise httpx.ReadTimeout("unknown receipt", request=request)
        return receipt()

    with httpx.Client(transport=httpx.MockTransport(gateway), follow_redirects=True) as client:
        results = [comparison.send_case(client, database, case, approved, SECRET, rates)
                   for case in comparison.CASES]
        assert results == ["http_error", "ReadTimeout", *(["success"] * 4)]
        same_database = comparison.prepare_ledger(database.parent, catalog, rates)
        for case in comparison.CASES:
            assert comparison.send_case(client, same_database, case, approved, SECRET, rates) == "already_claimed"
        with pytest.raises(ValueError):
            comparison.send_case(client, same_database, "H1", approved, SECRET, rates)
    assert tuple(calls) == comparison.CASES
    with closing(sqlite3.connect(database)) as conn:
        rows = conn.execute(
            "SELECT comparison_cases.name,receipt,cost,reserved_cost FROM comparison_cases JOIN sra_call_audit "
            "ON id=audit_id ORDER BY started_at"
        ).fetchall()
    assert len(rows) == 6
    assert rows[0][2] is None and rows[1][2] is None
    assert all(row[3] > 0 for row in rows)
    assert all(row[2] == pytest.approx(0.0063) for row in rows[2:])
    assert all(word not in rows[-1][1] for word in (SECRET, "hidden", "internal"))


def test_simultaneous_case_claim_does_not_repeat_network(approved, ledger):
    database, rates = ledger
    waiting = Event()
    release = Event()

    def gateway(_request):
        waiting.set()
        assert release.wait(5)
        return receipt()

    with httpx.Client(transport=httpx.MockTransport(gateway)) as client, ThreadPoolExecutor(2) as pool:
        first = pool.submit(comparison.send_case, client, database, "D1", approved, SECRET, rates)
        try:
            assert waiting.wait(5)
            duplicate = pool.submit(comparison.send_case, client, database, "D1", approved, SECRET, rates)
            assert duplicate.result(timeout=3) == "already_claimed"
        finally:
            release.set()
        assert first.result(timeout=3) == "success"


def test_missing_ledger_cannot_reopen_spent_round(tmp_path, catalog, ledger):
    _, rates = ledger
    round_path = tmp_path / "used-round"
    round_path.mkdir()
    with pytest.raises(FileExistsError):
        comparison.prepare_ledger(round_path, catalog, rates)


def test_unapproved_public_pricing_is_rejected(catalog):
    different = {**catalog, "slug": comparison.FLASH}
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=different))) as client:
        with pytest.raises(ValueError):
            comparison.current_prices(client)

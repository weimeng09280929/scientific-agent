import threading
import time

from core.orchestration.node import HumanRequest, HumanResponse
from web.api import HumanCallbackBridge


def test_human_request_waits_for_explicit_response():
    bridge = HumanCallbackBridge()
    result = []
    request = HumanRequest(prompt="选择主题", context={"node_id": "topic_select"})
    worker = threading.Thread(target=lambda: result.append(bridge.make_callback("project")(request)))
    worker.start()
    try:
        deadline = time.monotonic() + 2
        while bridge.get_pending("project") is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert bridge.get_pending("project") is not None
        assert worker.is_alive()
        assert bridge.submit("project", HumanResponse(action="continue", text="2"))
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert result[0].action == "continue"
        assert result[0].text == "2"
    finally:
        bridge.clear("project")
        worker.join(timeout=2)

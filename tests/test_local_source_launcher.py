"""Launcher network boundary: no actual source or model requests."""
import pytest
import requests
from core.llm import LLMRegistry
from core.research_tasks import sources
from tools.serve_local import disable_live_calls

@pytest.mark.parametrize('model,sources_enabled',[(False,False),(True,False),(False,True),(True,True)])
def test_independent_source_and_model_opt_in(monkeypatch,model,sources_enabled):
    original_sources=sources.SOURCE_FUNCS
    original_request=requests.sessions.Session.request
    original_factory=LLMRegistry.__dict__['from_config']
    monkeypatch.setattr(sources,'SOURCE_FUNCS',original_sources)
    monkeypatch.setattr(requests.sessions.Session,'request',original_request)
    monkeypatch.setattr(LLMRegistry,'from_config',original_factory)
    disable_live_calls(allow_model=model,allow_sources=sources_enabled)
    assert (sources.SOURCE_FUNCS is original_sources)==sources_enabled
    assert (requests.sessions.Session.request is original_request)==sources_enabled
    assert (LLMRegistry.__dict__['from_config'] is original_factory)==model
    if not sources_enabled:
        result=sources.call_source('openalex','offline test',max_results=1,timeout=1)
        assert result.status=='error' and result.error_class=='local_calls_disabled'
        assert not result.retryable and '未启用' in result.error

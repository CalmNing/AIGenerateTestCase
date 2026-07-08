from types import SimpleNamespace

from db.models import TestCase
from app.pipeline.pipeline_runner import (
    _build_pipeline_scenario_steps,
    _normalize_pipeline_api_step,
)


def test_normalize_pipeline_api_step_keeps_executor_parameter_location():
    step = {
        "endpoint_ref": 1,
        "parameters": [{"key": "id", "value": "{{recordId}}", "in_field": "path"}],
        "headers": [{"key": "Authorization", "value": "Bearer {{token}}", "in_field": "header"}],
        "post_actions": [{"type": "extract_jsonpath", "key": "recordId", "jsonpath": "$.data.id"}],
    }

    normalized = _normalize_pipeline_api_step(step, {1: 101})

    assert normalized["type"] == "api_call"
    assert normalized["endpoint_id"] == 101
    assert "endpoint_ref" not in normalized
    assert normalized["parameters"][0]["in"] == "path"
    assert "in_field" not in normalized["parameters"][0]
    assert normalized["headers"][0]["in"] == "header"
    assert normalized["post_actions"][0]["key"] == "recordId"


def test_build_pipeline_scenario_steps_normalizes_steps_for_execution():
    testcase = TestCase(
        case_name="update record",
        preset_conditions=[
            {
                "endpoint_ref": 1,
                "body": "{}",
                "post_actions": [{"type": "extract_jsonpath", "key": "recordId", "jsonpath": "$.data.id"}],
            }
        ],
        steps=[
            {
                "endpoint_ref": 2,
                "parameters": [{"key": "id", "value": "{{recordId}}", "in_field": "path"}],
            }
        ],
    )
    endpoint_map = {
        101: SimpleNamespace(id=101, method="POST", path="/records", url=None, name="create record"),
        102: SimpleNamespace(id=102, method="GET", path="/records/{id}", url=None, name="get record"),
    }

    steps = _build_pipeline_scenario_steps(testcase, endpoint_map, {1: 101, 2: 102})

    assert len(steps) == 2
    assert steps[0]["is_preset"] is True
    assert steps[0]["endpoint_id"] == 101
    assert steps[0]["post_actions"][0]["jsonpath"] == "$.data.id"
    assert steps[1]["endpoint_id"] == 102
    assert steps[1]["parameters"][0]["in"] == "path"
    assert steps[1]["path"] == "/records/{id}"

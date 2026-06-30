"""Guards the public import surface of the api_test_tool façade after the split."""
import importlib


def test_public_api_importable():
    mod = importlib.import_module("app.services.api_test_tool")
    for name in [
        "run_endpoint_steps", "run_scenario", "run_endpoint",
        "create_unit_test_scenario", "detect_base_url", "endpoints_from_spec",
        "generate_body_from_schema", "infer_step_dependencies",
        "parse_spec_text", "sync_project_from_spec",
    ]:
        assert hasattr(mod, name), f"missing public name: {name}"

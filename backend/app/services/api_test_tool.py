"""Façade for the API testing engine.

The implementation was split into focused modules (Phase 2); this module
preserves the historical public import surface
`from app.services.api_test_tool import ...` used by the route layer.

DAG: scenario_runner -> step_runner -> {swagger_parser, assertion_engine}
     swagger_parser -> {schema_utils, assertion_engine}
     step_runner/scenario_runner -> schema_utils
"""
# Leaf helpers (schema + assertions) — re-exported for any internal importer.
from app.services.schema_utils import *  # noqa: F401,F403
from app.services.assertion_engine import *  # noqa: F401,F403

# Public API surface (the names the route layer imports).
from app.services.swagger_parser import (  # noqa: F401
    HTTP_METHODS,
    REMOVED_FROM_SPEC_TAG,
    parse_spec_text,
    detect_base_url,
    endpoints_from_spec,
    sync_project_from_spec,
    swagger_config_docs,
    merge_specs,
    load_openapi_from_url,
    suggest_project_name,
    generate_body_from_schema,
    build_body_from_schema,
)
from app.services.step_runner import (  # noqa: F401
    run_endpoint,
    run_endpoint_steps,
    generate_unit_test_steps,
    create_unit_test_scenario,
)
from app.services.scenario_runner import (  # noqa: F401
    run_scenario,
    infer_step_dependencies,
)

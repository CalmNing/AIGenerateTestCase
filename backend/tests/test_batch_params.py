"""Batch params service tests: column types, normalization, scoping, failure policy, image base64."""

import asyncio
import base64
import io

import httpx
from PIL import Image

from app.services.batch_params import (
    COLUMN_IMAGE,
    COLUMN_IMAGE_BASE64,
    COLUMN_TEXT,
    MAX_BATCH_ROWS,
    ON_ERROR_CONTINUE,
    ON_ERROR_STOP,
    has_batch,
    normalize_batch_params,
    row_to_variables,
    run_batch,
)


def _run(batch_params, base_map, forward_one):
    return asyncio.run(run_batch(batch_params, base_map, forward_one))


def _row_vars(columns, values):
    return asyncio.run(row_to_variables(columns, values))


def _png_b64() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


class TestTypedColumns:
    """number / boolean / json 三种新列类型的转换与校验。"""

    def test_number_column_passes_valid_and_strips(self):
        variables = _row_vars(
            [{"name": "num", "type": "number"}],
            [" 42 "],
        )
        assert variables == {"num": "42"}

    def test_number_column_rejects_invalid(self):
        variables = _row_vars(
            [{"name": "num", "type": "number"}],
            ["abc"],
        )
        assert variables == {}

    def test_number_column_accepts_float(self):
        variables = _row_vars(
            [{"name": "num", "type": "number"}],
            ["3.14"],
        )
        assert variables == {"num": "3.14"}

    def test_boolean_column_normalizes(self):
        variables = _row_vars(
            [{"name": "flag", "type": "boolean"}],
            ["TRUE"],
        )
        assert variables == {"flag": "true"}

        variables = _row_vars(
            [{"name": "flag", "type": "boolean"}],
            ["0"],
        )
        assert variables == {"flag": "false"}

        variables = _row_vars(
            [{"name": "flag", "type": "boolean"}],
            ["否"],
        )
        assert variables == {"flag": "false"}

    def test_boolean_column_rejects_invalid(self):
        variables = _row_vars(
            [{"name": "flag", "type": "boolean"}],
            ["maybe"],
        )
        assert variables == {}

    def test_json_column_minifies_valid(self):
        variables = _row_vars(
            [{"name": "payload", "type": "json"}],
            ['{ "a": 1, "b": "中文" }'],
        )
        assert variables == {"payload": '{"a":1,"b":"中文"}'}

    def test_json_column_rejects_invalid(self):
        variables = _row_vars(
            [{"name": "payload", "type": "json"}],
            ["{not json"],
        )
        assert variables == {}

    def test_mixed_columns_in_one_row(self):
        variables = _row_vars(
            [
                {"name": "deviceId", "type": "text"},
                {"name": "count", "type": "number"},
                {"name": "enabled", "type": "boolean"},
                {"name": "extra", "type": "json"},
                {"name": "bad", "type": "number"},
            ],
            ["d1", "7", "yes", '{"k":1}', "oops"],
        )
        assert variables == {
            "deviceId": "d1",
            "count": "7",
            "enabled": "true",
            "extra": '{"k":1}',
        }

    def test_normalize_preserves_new_types(self):
        columns, _rows, _ = normalize_batch_params({
            "columns": [
                {"name": "n", "type": "number"},
                {"name": "b", "type": "boolean"},
                {"name": "j", "type": "json"},
            ],
            "rows": [["1", "true", "{}"]],
        })
        assert [c["type"] for c in columns] == ["number", "boolean", "json"]


class TestNormalize:
    def test_string_columns_become_text(self):
        columns, rows, on_error = normalize_batch_params({
            "columns": ["deviceId", "tenant"],
            "rows": [["d1", "a"], ["d2", "b"]],
        })

        assert [c["name"] for c in columns] == ["deviceId", "tenant"]
        assert all(c["type"] == COLUMN_TEXT for c in columns)
        assert rows == [["d1", "a"], ["d2", "b"]]
        assert on_error == ON_ERROR_CONTINUE

    def test_column_as_dict_form(self):
        columns, _rows, _ = normalize_batch_params({
            "columns": [{"name": "deviceId"}, {"key": "tenant"}],
            "rows": [["d1", "a"]],
        })

        assert [c["name"] for c in columns] == ["deviceId", "tenant"]

    def test_column_types_preserved(self):
        columns, _rows, _ = normalize_batch_params({
            "columns": [
                {"name": "a", "type": "text"},
                {"name": "img", "type": "image", "compress": False, "format": "data_url"},
                {"name": "b64", "type": "image_base64"},
            ],
            "rows": [["1", "2", "3"]],
        })

        assert columns[0]["type"] == COLUMN_TEXT
        assert columns[1]["type"] == COLUMN_IMAGE
        assert columns[1]["compress"] is False
        assert columns[1]["format"] == "data_url"
        assert columns[2]["type"] == COLUMN_IMAGE_BASE64

    def test_unknown_column_type_falls_back_to_text(self):
        columns, _rows, _ = normalize_batch_params({
            "columns": [{"name": "a", "type": "weird"}],
            "rows": [["1"]],
        })
        assert columns[0]["type"] == COLUMN_TEXT

    def test_duplicate_columns_removed(self):
        columns, _rows, _ = normalize_batch_params({
            "columns": ["a", "a", "b"],
            "rows": [["1", "2"]],
        })

        assert [c["name"] for c in columns] == ["a", "b"]

    def test_short_row_is_padded(self):
        _columns, rows, _ = normalize_batch_params({
            "columns": ["a", "b", "c"],
            "rows": [["1"]],
        })

        assert rows == [["1", "", ""]]

    def test_long_row_is_trimmed(self):
        _columns, rows, _ = normalize_batch_params({
            "columns": ["a"],
            "rows": [["1", "2", "3"]],
        })

        assert rows == [["1"]]

    def test_non_dict_returns_empty(self):
        assert normalize_batch_params(None) == ([], [], ON_ERROR_CONTINUE)
        assert normalize_batch_params("x") == ([], [], ON_ERROR_CONTINUE)

    def test_invalid_on_error_falls_back_to_continue(self):
        _c, _r, on_error = normalize_batch_params({
            "columns": ["a"], "rows": [["1"]], "on_error": "weird",
        })

        assert on_error == ON_ERROR_CONTINUE

    def test_rows_truncated_to_max(self):
        rows_in = [[str(i)] for i in range(MAX_BATCH_ROWS + 20)]
        _columns, rows, _ = normalize_batch_params({"columns": ["a"], "rows": rows_in})

        assert len(rows) == MAX_BATCH_ROWS


class TestRowToVariables:
    def test_maps_columns_to_values(self):
        columns, _rows, _ = normalize_batch_params({
            "columns": ["a", "b"], "rows": [["1", "2"]],
        })
        assert _row_vars(columns, ["1", "2"]) == {"a": "1", "b": "2"}

    def test_empty_value_skipped(self):
        columns, _rows, _ = normalize_batch_params({
            "columns": ["a", "b"], "rows": [["1", ""]],
        })
        assert _row_vars(columns, ["1", ""]) == {"a": "1"}


class TestHasBatch:
    def test_false_without_columns_or_rows(self):
        assert has_batch(None) is False
        assert has_batch({"columns": [], "rows": []}) is False
        assert has_batch({"columns": ["a"], "rows": []}) is False
        assert has_batch({"columns": [], "rows": [["1"]]}) is False

    def test_true_with_both(self):
        assert has_batch({"columns": ["a"], "rows": [["1"]]}) is True


class TestRunBatch:
    def test_runs_each_row_once(self):
        calls = []

        async def forward_one(param_map):
            calls.append(dict(param_map))
            return {"status_code": 200, "data": {"ok": True}}

        batch = _run(
            {"columns": ["deviceId"], "rows": [["d1"], ["d2"]]},
            {"base": "B"},
            forward_one,
        )

        assert len(calls) == 2
        assert calls[0]["deviceId"] == "d1"
        assert calls[1]["deviceId"] == "d2"
        assert batch["total"] == 2
        assert batch["passed"] == 2
        assert batch["failed"] == 0
        assert batch["stopped"] is False

    def test_row_vars_do_not_leak_between_rows(self):
        seen = []

        async def forward_one(param_map):
            seen.append(param_map.get("deviceId"))
            return {"status_code": 200}

        _run({"columns": ["deviceId"], "rows": [["d1"], ["d2"]]}, {}, forward_one)

        assert seen == ["d1", "d2"]

    def test_base_map_visible_to_every_row(self):
        calls = []

        async def forward_one(param_map):
            calls.append(dict(param_map))
            return {"status_code": 200}

        _run(
            {"columns": ["a"], "rows": [["1"], ["2"]]},
            {"baseUrl": "http://x"},
            forward_one,
        )

        assert all(c["baseUrl"] == "http://x" for c in calls)

    def test_continue_policy_runs_all_rows(self):
        calls = []

        async def forward_one(param_map):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return {"status_code": 200}

        batch = _run(
            {"columns": ["a"], "rows": [["1"], ["2"], ["3"]], "on_error": "continue"},
            {},
            forward_one,
        )

        assert len(calls) == 3
        assert batch["failed"] == 1
        assert batch["passed"] == 2
        assert batch["stopped"] is False

    def test_stop_policy_halts_after_first_failure(self):
        calls = []

        async def forward_one(param_map):
            calls.append(1)
            raise RuntimeError("boom")

        batch = _run(
            {"columns": ["a"], "rows": [["1"], ["2"], ["3"]], "on_error": "stop"},
            {},
            forward_one,
        )

        assert len(calls) == 1
        assert batch["failed"] == 1
        assert batch["stopped"] is True

    def test_non_2xx_counts_as_failed(self):
        async def forward_one(param_map):
            return {"status_code": 500, "data": "err"}

        batch = _run({"columns": ["a"], "rows": [["1"]]}, {}, forward_one)

        assert batch["failed"] == 1
        assert batch["passed"] == 0
        assert batch["results"][0]["status_code"] == 500

    def test_stop_policy_halts_on_non_2xx_response(self):
        """非 2xx 也应触发"遇错即停"，不能只对异常生效。"""
        calls = []

        async def forward_one(param_map):
            calls.append(1)
            status = 500 if len(calls) == 2 else 200
            return {"status_code": status}

        batch = _run(
            {"columns": ["a"], "rows": [["1"], ["2"], ["3"]], "on_error": "stop"},
            {},
            forward_one,
        )

        assert len(calls) == 2
        assert batch["failed"] == 1
        assert batch["passed"] == 1
        assert batch["stopped"] is True

    def test_continue_policy_keeps_going_after_non_2xx(self):
        calls = []

        async def forward_one(param_map):
            calls.append(1)
            status = 500 if len(calls) == 2 else 200
            return {"status_code": status}

        batch = _run(
            {"columns": ["a"], "rows": [["1"], ["2"], ["3"]], "on_error": "continue"},
            {},
            forward_one,
        )

        assert len(calls) == 3
        assert batch["failed"] == 1
        assert batch["passed"] == 2
        assert batch["stopped"] is False


class TestImageColumn:
    def test_image_column_converts_base64(self):
        b64 = _png_b64()
        columns, _rows, _ = normalize_batch_params({
            "columns": [{"name": "img", "type": "image", "compress": False}],
            "rows": [[b64]],
        })

        variables = _row_vars(columns, [b64])

        assert "img" in variables
        assert base64.b64decode(variables["img"])[:4] == b"\x89PNG"

    def test_image_column_data_url_format(self):
        b64 = _png_b64()
        columns, _rows, _ = normalize_batch_params({
            "columns": [{
                "name": "img", "type": "image", "compress": False, "format": "data_url",
            }],
            "rows": [[b64]],
        })

        variables = _row_vars(columns, [b64])

        assert variables["img"] == f"data:image/png;base64,{b64}"

    def test_image_base64_column_validated(self):
        b64 = _png_b64()
        columns, _rows, _ = normalize_batch_params({
            "columns": [{"name": "img", "type": "image_base64", "compress": False}],
            "rows": [[b64]],
        })

        variables = _row_vars(columns, [b64])

        assert variables["img"] == b64

    def test_invalid_image_value_skipped(self):
        columns, _rows, _ = normalize_batch_params({
            "columns": [{"name": "img", "type": "image"}],
            "rows": [["not-an-image"]],
        })

        variables = _row_vars(columns, ["not-an-image"])

        assert "img" not in variables

    def test_empty_image_value_skipped(self):
        columns, _rows, _ = normalize_batch_params({
            "columns": [{"name": "img", "type": "image"}],
            "rows": [[""]],
        })

        assert _row_vars(columns, [""]) == {}

    def test_remote_image_url_is_fetched(self, monkeypatch):
        png = base64.b64decode(_png_b64())
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, content=png, headers={"Content-Type": "image/png"})

        real_client = httpx.AsyncClient

        def _factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(*args, **kwargs)

        monkeypatch.setattr(
            "app.services.batch_params.httpx.AsyncClient", _factory
        )

        columns, _rows, _ = normalize_batch_params({
            "columns": [{"name": "img", "type": "image", "compress": False}],
            "rows": [["http://img.test/a.png"]],
        })

        variables = _row_vars(columns, ["http://img.test/a.png"])

        assert seen == ["http://img.test/a.png"]
        assert base64.b64decode(variables["img"])[:4] == b"\x89PNG"

    def test_remote_image_failure_skips_variable(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        real_client = httpx.AsyncClient

        def _factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(*args, **kwargs)

        monkeypatch.setattr(
            "app.services.batch_params.httpx.AsyncClient", _factory
        )

        columns, _rows, _ = normalize_batch_params({
            "columns": [{"name": "img", "type": "image"}],
            "rows": [["http://img.test/missing.png"]],
        })

        assert _row_vars(columns, ["http://img.test/missing.png"]) == {}

    def test_image_column_converted_per_row(self):
        b64 = _png_b64()
        seen = []

        async def forward_one(param_map):
            seen.append(param_map.get("img"))
            return {"status_code": 200}

        _run(
            {
                "columns": [{"name": "img", "type": "image", "compress": False}],
                "rows": [[b64], [b64]],
            },
            {},
            forward_one,
        )

        assert len(seen) == 2
        assert all(v == b64 for v in seen)

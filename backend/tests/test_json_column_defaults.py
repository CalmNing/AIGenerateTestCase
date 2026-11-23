"""自动迁移的 JSON 列默认值测试。

背景：早期迁移给所有 JSON 列统一用 '[]' 作默认值，导致 dict 类型的列
（如 savedrequest.batch_params）反序列化出数组，前端拿到后按对象访问会崩。

约定：
- _json_default_for() 返回**纯 JSON 文本**（{} / []），不含 SQL 引号；
- 拼 DDL 的地方自己加引号（DEFAULT '{}'）；
- 修复存量数据的地方直接用纯 JSON 文本。
"""

from db.db import _JSON_DICT_COLUMNS, _JSON_LIST_COLUMNS, _json_default_for


class TestJsonDefaultFor:
    def test_dict_columns_use_object(self):
        for col in ("batch_params", "ui_binding", "request_schema", "response_schema"):
            assert _json_default_for(col) == "{}", col

    def test_list_columns_use_array(self):
        for col in ("parameters", "pre_steps", "headers", "post_extractions"):
            assert _json_default_for(col) == "[]", col

    def test_unknown_column_keeps_legacy_default(self):
        # 未知列不冒险改结构，仍用数组
        assert _json_default_for("some_unknown_column") == "[]"

    def test_dict_and_list_sets_do_not_overlap(self):
        assert not (_JSON_DICT_COLUMNS & _JSON_LIST_COLUMNS)

    def test_return_value_is_valid_json(self):
        import json

        for col in ("batch_params", "parameters"):
            assert json.loads(_json_default_for(col)) == (
                {} if col in _JSON_DICT_COLUMNS else []
            )

    def test_return_value_has_no_sql_quotes(self):
        """返回的是纯 JSON 文本，引号由调用方拼 DDL 时加。"""
        value = _json_default_for("batch_params")
        assert not value.startswith("'")
        assert not value.endswith("'")


class TestSavedRequestBatchParamsShape:
    """batch_params 在数据库里必须是 dict，而不是 list。"""

    def test_batch_params_default_is_not_a_list(self):
        from db.models import SavedRequest

        req = SavedRequest(name="t", method="GET", url="http://x")
        # 模型默认是 None（不是 []）
        assert req.batch_params is None or isinstance(req.batch_params, dict)
        assert not isinstance(req.batch_params, list)

    def test_batch_params_accepts_dict(self):
        from db.models import SavedRequest

        req = SavedRequest(
            name="t",
            method="GET",
            url="http://x",
            batch_params={"columns": [{"name": "a", "type": "text"}], "rows": [["1"]]},
        )
        assert isinstance(req.batch_params, dict)
        assert req.batch_params["columns"][0]["name"] == "a"

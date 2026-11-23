"""定时任务图片 base64 参数测试。

覆盖：
  1. image_base64 类型的任务参数作为普通变量注入 param_map；
  2. file 类型参数不作为变量注入（值是 fileId，属文件上传语义）；
  3. 普通文本参数照常注入；
  4. 非法 base64 不阻断任务参数构建。
"""

import base64
import io

from PIL import Image

from app.routes.proxy import build_param_map
from app.scheduler import _decode_task_image_base64, _task_param_entries


def _png_b64() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


class TestTaskParamEntries:
    def test_text_param_kept(self):
        entries = _task_param_entries([{"key": "a", "value": "1", "type": "text"}])
        assert entries == [{"key": "a", "value": "1", "type": "text"}]

    def test_image_base64_param_kept(self):
        params = [{"key": "img", "value": "AAA", "type": "image_base64"}]
        assert _task_param_entries(params) == params

    def test_file_param_excluded(self):
        params = [{"key": "f", "value": "file-123", "type": "file"}]
        assert _task_param_entries(params) == []

    def test_param_without_value_excluded(self):
        assert _task_param_entries([{"key": "a", "value": "", "type": "text"}]) == []

    def test_non_dict_ignored(self):
        assert _task_param_entries(["oops", None, 1]) == []


class TestParamMapInjection:
    def test_image_base64_injected_as_variable(self, db_session):
        b64 = _png_b64()
        param_map = build_param_map(
            db_session,
            None,
            [{"key": "imgVar", "value": b64, "type": "image_base64"}],
        )
        assert param_map["imgVar"] == b64

    def test_file_param_not_injected(self, db_session):
        param_map = build_param_map(
            db_session,
            None,
            [{"key": "fileVar", "value": "file-123", "type": "file"}],
        )
        assert "fileVar" not in param_map

    def test_text_param_injected(self, db_session):
        param_map = build_param_map(
            db_session,
            None,
            [{"key": "textVar", "value": "hello", "type": "text"}],
        )
        assert param_map["textVar"] == "hello"


class TestDecodeTaskImageBase64:
    def test_plain_base64_returned_as_is(self):
        b64 = _png_b64()
        assert _decode_task_image_base64({"key": "img", "value": b64}) == b64

    def test_data_url_prefix_is_accepted(self):
        b64 = _png_b64()
        # 带 data: 前缀也能被识别为合法图片（值本身保持原样）
        out = _decode_task_image_base64({"key": "img", "value": f"data:image/png;base64,{b64}"})
        assert out.startswith("data:image/png;base64,")

    def test_invalid_value_falls_back_to_raw(self):
        out = _decode_task_image_base64({"key": "img", "value": "not-an-image"})
        assert out == "not-an-image"

    def test_empty_value_returns_empty(self):
        assert _decode_task_image_base64({"key": "img", "value": ""}) == ""

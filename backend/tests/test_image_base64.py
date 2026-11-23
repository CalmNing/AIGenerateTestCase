"""Image base64 handling tests: utility functions + pre-step integration."""

import asyncio
import base64
import io

import httpx
from PIL import Image

from app.services.image_base64 import (
    FORMAT_DATA_URL,
    FORMAT_RAW,
    decode_base64_image,
    encode_image,
    format_output,
    sniff_image_mime,
    split_data_url,
    to_base64_variable,
)
from app.services.pre_steps import run_pre_steps


def _png_bytes(color=(255, 0, 0), size=(40, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


class TestSniffMime:
    def test_png(self):
        assert sniff_image_mime(_png_bytes()) == "image/png"

    def test_jpeg(self):
        buffer = io.BytesIO()
        Image.new("RGB", (10, 10)).save(buffer, format="JPEG")
        assert sniff_image_mime(buffer.getvalue()) == "image/jpeg"

    def test_non_image_returns_none(self):
        assert sniff_image_mime(b"hello world") is None
        assert sniff_image_mime(b"") is None


class TestSplitDataUrl:
    def test_splits_data_url(self):
        mime, payload = split_data_url("data:image/png;base64,AAAA")
        assert mime == "image/png"
        assert payload == "AAAA"

    def test_plain_value_passthrough(self):
        mime, payload = split_data_url("AAAA")
        assert mime is None
        assert payload == "AAAA"


class TestDecode:
    def test_decodes_plain_base64(self):
        raw = _png_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        decoded, mime = decode_base64_image(b64)
        assert decoded == raw
        assert mime == "image/png"

    def test_decodes_data_url(self):
        raw = _png_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        decoded, mime = decode_base64_image(f"data:image/png;base64,{b64}")
        assert decoded == raw
        assert mime == "image/png"

    def test_invalid_returns_none(self):
        assert decode_base64_image("not-base64!!") == (None, None)
        assert decode_base64_image("") == (None, None)


class TestEncode:
    def test_compress_returns_jpeg(self):
        b64, mime = encode_image(_png_bytes(), compress=True)
        assert mime == "image/jpeg"
        assert base64.b64decode(b64)[:2] == b"\xff\xd8"

    def test_no_compress_keeps_png(self):
        b64, mime = encode_image(_png_bytes(), compress=False)
        assert mime == "image/png"
        assert base64.b64decode(b64)[:4] == b"\x89PNG"

    def test_non_image_returns_none(self):
        assert encode_image(b"not an image", compress=False) == (None, None)


class TestFormatOutput:
    def test_raw_format(self):
        assert format_output("AAA", "image/png", FORMAT_RAW) == "AAA"

    def test_data_url_format(self):
        assert format_output("AAA", "image/png", FORMAT_DATA_URL) == "data:image/png;base64,AAA"


class TestToBase64Variable:
    def test_raw_output(self):
        raw = _png_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        out = to_base64_variable(b64, compress=False, fmt=FORMAT_RAW)
        assert out == b64

    def test_data_url_output(self):
        raw = _png_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        out = to_base64_variable(b64, compress=False, fmt=FORMAT_DATA_URL)
        assert out == f"data:image/png;base64,{b64}"

    def test_data_url_input_gets_normalized(self):
        raw = _png_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        out = to_base64_variable(f"data:image/png;base64,{b64}", compress=False, fmt=FORMAT_RAW)
        assert out == b64

    def test_non_image_returns_none(self):
        assert to_base64_variable("hello", compress=False) is None


class TestPreStepImageExtraction:
    def _install_mock(self, monkeypatch, handler):
        real_client = httpx.AsyncClient

        def _factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(*args, **kwargs)

        monkeypatch.setattr("app.services.pre_steps.httpx.AsyncClient", _factory)

    def test_extracts_image_as_base64_variable(self, monkeypatch):
        raw = _png_bytes()
        b64 = base64.b64encode(raw).decode("ascii")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {"img": f"data:image/png;base64,{b64}"}})

        self._install_mock(monkeypatch, handler)

        variables, results = asyncio.run(run_pre_steps([{
            "type": "request",
            "url": "http://biz.test/img",
            "extracts": [{
                "key": "imgVar",
                "jsonpath": "$.data.img",
                "type": "image_base64",
                "compress": False,
                "format": FORMAT_RAW,
            }],
        }], {}))

        assert "imgVar" in variables
        assert variables["imgVar"] == b64
        assert results[0]["extracted"]["imgVar"] == b64

    def test_image_extract_data_url_format(self, monkeypatch):
        raw = _png_bytes()
        b64 = base64.b64encode(raw).decode("ascii")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"img": b64})

        self._install_mock(monkeypatch, handler)

        variables, _results = asyncio.run(run_pre_steps([{
            "type": "request",
            "url": "http://biz.test/img",
            "extracts": [{
                "key": "imgVar",
                "jsonpath": "$.img",
                "type": "image_base64",
                "compress": False,
                "format": FORMAT_DATA_URL,
            }],
        }], {}))

        assert variables["imgVar"] == f"data:image/png;base64,{b64}"

    def test_invalid_image_is_skipped(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"img": "not-an-image"})

        self._install_mock(monkeypatch, handler)

        variables, results = asyncio.run(run_pre_steps([{
            "type": "request",
            "url": "http://biz.test/img",
            "extracts": [{
                "key": "imgVar",
                "jsonpath": "$.img",
                "type": "image_base64",
            }],
        }], {}))

        assert "imgVar" not in variables
        assert results[0]["extracted"] == {}

    def test_plain_json_extract_unaffected(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {"token": "tok-1"}})

        self._install_mock(monkeypatch, handler)

        variables, _results = asyncio.run(run_pre_steps([{
            "type": "request",
            "url": "http://biz.test/login",
            "extracts": [{"key": "token", "jsonpath": "$.data.token"}],
        }], {}))

        assert variables["token"] == "tok-1"

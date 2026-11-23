"""定时任务 file_params 代理转发分支的响应透传测试。

覆盖：/forward 返回 {detail} 错误结构（HTTPException / 校验失败）时，
执行结果里必须带上真实状态码与错误详情，而不是 status_code=500、body=null。
"""


class TestProxyDetailPassthrough:
    def _parse(self, proxy_result, proxy_status):
        """复刻 scheduler file_params 分支的解析逻辑。"""
        if "detail" in proxy_result and "status_code" not in proxy_result:
            return proxy_status, proxy_result.get("detail")
        return proxy_result.get("status_code", proxy_status), proxy_result.get("data")

    def test_detail_error_surfaces(self):
        # /forward 抛 HTTPException(500) 时返回 {detail}
        status_code, body = self._parse(
            {"detail": "Proxy error: upstream timeout"}, 500,
        )
        assert status_code == 500
        assert body == "Proxy error: upstream timeout"

    def test_validation_error_surfaces(self):
        # Pydantic 422 返回 {detail: [...]}，旧逻辑会变成 status_code=500/body=null
        detail = [{"loc": ["body", "headers"], "msg": "invalid"}]
        status_code, body = self._parse({"detail": detail}, 422)
        assert status_code == 422
        assert body == detail

    def test_normal_response_unchanged(self):
        # 正常（含上游 500）响应保持 {status_code, data} 结构解析
        status_code, body = self._parse(
            {"status_code": 500, "data": {"code": 50000, "msg": "err"}}, 200,
        )
        assert status_code == 500
        assert body == {"code": 50000, "msg": "err"}

    def test_success_response_unchanged(self):
        status_code, body = self._parse(
            {"status_code": 200, "data": {"ok": True}}, 200,
        )
        assert status_code == 200
        assert body == {"ok": True}
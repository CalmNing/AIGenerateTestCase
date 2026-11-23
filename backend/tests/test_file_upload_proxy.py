"""文件上传代理路由测试。

覆盖后端代理上传链路:
  1. 正常上传——先申请预签名地址,再 PUT 到对象存储,返回 fileId;
  2. 秒传(fastUploadFinish)——不再 PUT 文件;
  3. 业务系统返回异常——转成 502;
  4. 环境参数缺少 baseUrl / access-token——返回 400;
  5. 文件为空——返回 400;
  6. 无权限用户指定 environment_id 时被降级到默认环境。

用 httpx.MockTransport 拦截后端对业务系统的出站请求,不触网。
"""

import asyncio
import io

import httpx
import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile as StarletteUploadFile

import app.routes.file_upload as route
from db.models import GlobalParameter


@pytest.fixture()
def fake_user():
    from app.auth import UserInfo
    return UserInfo(user_id="u1", username="tester", roles=["admin"])


@pytest.fixture()
def env_with_token(db_session):
    env = GlobalParameter(
        name="上传环境",
        is_default=True,
        parameters=[
            {"key": "baseUrl", "value": "http://biz.example.com"},
            {"key": "access-token", "value": "tok-123"},
        ],
    )
    db_session.add(env)
    db_session.commit()
    db_session.refresh(env)
    return env


def _install_mock(monkeypatch, handler):
    """把后端出站 httpx 客户端换成 MockTransport(不触网)。"""
    real_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(route.httpx, "AsyncClient", _factory)


def _upload(db_session, user, filename="a.txt", content=b"hello", environment_id=None):
    """构造 UploadFile 并调用路由函数。"""
    upload = StarletteUploadFile(file=io.BytesIO(content), filename=filename, size=len(content))
    upload.headers = {"content-type": "text/plain"}
    return asyncio.run(
        route.upload_file(
            file=upload,
            environment_id=environment_id,
            session=db_session,
            user=user,
        )
    )

def test_upload_puts_to_presigned_url_and_returns_file_id(db_session, fake_user, env_with_token, monkeypatch):
    """非秒传:先 POST 申请预签名地址,再 PUT 文件,最终返回 fileId。"""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": [
                        {
                            "fileName": "a.txt",
                            "fileId": "fid-001",
                            "uploadUrl": "http://oss.example.com/put/a.txt?sig=1",
                            "fastUploadFinish": False,
                        }
                    ],
                },
            )
        return httpx.Response(200)

    _install_mock(monkeypatch, handler)

    result = _upload(db_session, fake_user)

    assert result.code == 200
    assert result.data["fileId"] == "fid-001"
    assert result.data["fileName"] == "a.txt"
    assert result.data["fileSize"] == 5

    assert len(seen) == 2
    meta_req, put_req = seen
    # 申请预签名地址:带 access-token 头 + 文件元信息
    assert meta_req.method == "POST"
    assert str(meta_req.url) == "http://biz.example.com/builder-base/file/upload-url"
    assert meta_req.headers["access-token"] == "tok-123"
    meta_body = meta_req.read().decode("utf-8")
    assert "a.txt" in meta_body
    # 秒传判定依赖 fileMd5
    assert "fileMd5" in meta_body
    # 实际上传:PUT 到预签名地址
    assert put_req.method == "PUT"
    assert str(put_req.url) == "http://oss.example.com/put/a.txt?sig=1"
    assert put_req.read() == b"hello"


def test_upload_skips_put_when_fast_upload_finishes(db_session, fake_user, env_with_token, monkeypatch):
    """秒传命中:只申请预签名地址,不再 PUT 文件。"""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "code": 200,
                "data": [
                    {
                        "fileName": "a.txt",
                        "fileId": "fid-fast",
                        "uploadUrl": "http://oss.example.com/put/a.txt",
                        "fastUploadFinish": True,
                    }
                ],
            },
        )

    _install_mock(monkeypatch, handler)

    result = _upload(db_session, fake_user)

    assert result.data["fileId"] == "fid-fast"
    assert len(seen) == 1
    assert seen[0].method == "POST"


def test_business_error_becomes_502(db_session, fake_user, env_with_token, monkeypatch):
    """业务系统返回非 200 code -> 502。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 500, "msg": "业务系统繁忙"})

    _install_mock(monkeypatch, handler)

    with pytest.raises(HTTPException) as exc:
        _upload(db_session, fake_user)

    assert exc.value.status_code == 502
    assert "业务系统繁忙" in exc.value.detail


def test_missing_file_id_becomes_502(db_session, fake_user, env_with_token, monkeypatch):
    """业务系统未返回 fileId -> 502。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": 200, "data": [{"fileName": "a.txt", "fastUploadFinish": True}]},
        )

    _install_mock(monkeypatch, handler)

    with pytest.raises(HTTPException) as exc:
        _upload(db_session, fake_user)

    assert exc.value.status_code == 502
    assert "fileId" in exc.value.detail

def test_storage_put_failure_becomes_502(db_session, fake_user, env_with_token, monkeypatch):
    """对象存储 PUT 失败 -> 502。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": [
                        {
                            "fileName": "a.txt",
                            "fileId": "fid-002",
                            "uploadUrl": "http://oss.example.com/put/a.txt",
                            "fastUploadFinish": False,
                        }
                    ],
                },
            )
        return httpx.Response(403)

    _install_mock(monkeypatch, handler)

    with pytest.raises(HTTPException) as exc:
        _upload(db_session, fake_user)

    assert exc.value.status_code == 502
    assert "403" in exc.value.detail


def test_missing_base_url_returns_400(db_session, fake_user, monkeypatch):
    """环境参数没有 baseUrl -> 400,不发起任何出站请求。"""
    env = GlobalParameter(name="空环境", is_default=True, parameters=[])
    db_session.add(env)
    db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("不应发起出站请求")

    _install_mock(monkeypatch, handler)

    with pytest.raises(HTTPException) as exc:
        _upload(db_session, fake_user)

    assert exc.value.status_code == 400
    assert "baseUrl" in exc.value.detail


def test_missing_access_token_returns_400(db_session, fake_user, monkeypatch):
    """环境参数没有 access-token -> 400。"""
    env = GlobalParameter(
        name="无token环境",
        is_default=True,
        parameters=[{"key": "baseUrl", "value": "http://biz.example.com"}],
    )
    db_session.add(env)
    db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("不应发起出站请求")

    _install_mock(monkeypatch, handler)

    with pytest.raises(HTTPException) as exc:
        _upload(db_session, fake_user)

    assert exc.value.status_code == 400
    assert "access-token" in exc.value.detail


def test_empty_file_returns_400(db_session, fake_user, env_with_token, monkeypatch):
    """空文件 -> 400。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("不应发起出站请求")

    _install_mock(monkeypatch, handler)

    with pytest.raises(HTTPException) as exc:
        _upload(db_session, fake_user, content=b"")

    assert exc.value.status_code == 400
    assert "为空" in exc.value.detail


def test_environment_id_downgraded_without_manage_permission(db_session, monkeypatch):
    """无 global_parameter:manage 时,传入的 environment_id 被降级为默认环境。"""
    from app.auth import UserInfo

    default_env = GlobalParameter(
        name="默认环境",
        is_default=True,
        parameters=[
            {"key": "baseUrl", "value": "http://default.example.com"},
            {"key": "access-token", "value": "tok-default"},
        ],
    )
    other_env = GlobalParameter(
        name="受限环境",
        is_default=False,
        parameters=[
            {"key": "baseUrl", "value": "http://other.example.com"},
            {"key": "access-token", "value": "tok-other"},
        ],
    )
    db_session.add_all([default_env, other_env])
    db_session.commit()
    db_session.refresh(other_env)

    user = UserInfo(user_id="u2", username="plain", roles=["user"])
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "code": 200,
                "data": [
                    {"fileName": "a.txt", "fileId": "fid-003", "fastUploadFinish": True}
                ],
            },
        )

    _install_mock(monkeypatch, handler)

    _upload(db_session, user, environment_id=other_env.id)

    # 走的是默认环境,而不是被降级前指定的受限环境
    assert str(seen[0].url) == "http://default.example.com/builder-base/file/upload-url"
    assert seen[0].headers["access-token"] == "tok-default"

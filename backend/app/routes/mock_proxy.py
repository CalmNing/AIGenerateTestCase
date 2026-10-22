"""Mock 正向代理管理 API。

admin router：代理启停/状态、转发规则 CRUD —— 需 MOCK_MANAGE 权限。
public router：CA 证书下载（免认证，供用户导入信任区）。
"""
from typing import Optional

from fastapi import APIRouter, status
from fastapi.responses import Response as HTTPResponse
from pydantic import BaseModel
from sqlmodel import select

from app.deps import SessionDep, CurrentUser
from db.models import MockConfig, MockProxyRule
from utils.base_response import Response

# 需要权限的管理路由
router = APIRouter(prefix="/mock-proxy", tags=["mock-proxy"])

# 免认证公开路由（CA 证书下载）
public_router = APIRouter(prefix="/mock-proxy", tags=["mock-proxy"])


class ProxyStartRequest(BaseModel):
    port: Optional[int] = None


def _normalize_service_prefix(value: str) -> Optional[str]:
    """规范化规则的服务前缀；非法返回 None。

    合法：空、/contract、/service/contract（以 / 开头、不以 / 结尾、不含 { } *）
    """
    prefix = (value or "").strip()
    if not prefix:
        return ""
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    if len(prefix) > 1 and prefix.endswith("/"):
        prefix = prefix.rstrip("/")
    if any(token in prefix for token in ("{", "}", "*")):
        return None
    if " " in prefix or "//" in prefix:
        return None
    return prefix


def _local_ip() -> str:
    """探测本机局域网 IP（UDP connect 不发包，仅路由表查询），失败回退主机名解析。"""
    import socket

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


@router.get("/status", response_model=Response[dict])
def get_proxy_status(session: SessionDep, user: CurrentUser):
    """获取代理运行状态、规则数量与平台 Mock 数量。"""
    from app.services.mock_proxy import get_proxy_state

    rules_count = len(session.exec(select(MockProxyRule)).all())
    enabled_configs = len(
        session.exec(select(MockConfig).where(MockConfig.enabled == True)).all()
    )
    state = get_proxy_state()
    return Response(data={
        "running": state["running"],
        "port": state["port"],
        "rules_count": rules_count,
        "platform_configs": enabled_configs,
        "local_ip": _local_ip(),
    })


@router.post("/start", response_model=Response[dict])
async def start_proxy_api(session: SessionDep, user: CurrentUser, body: ProxyStartRequest):
    """启动 Mock 代理监听。"""
    from app.services import mock_proxy
    from config import config_manager

    port = body.port or config_manager.get_mock_proxy_port()
    if not (1 <= port <= 65535):
        return Response(code=status.HTTP_400_BAD_REQUEST, message="端口必须在 1-65535 之间")

    started = await mock_proxy.start_proxy(port)
    if not started:
        state = mock_proxy.get_proxy_state()
        return Response(data={"running": True, "port": state["port"], "rules_count": _rules_count(session)},
                        message=f"Mock 代理已在端口 {state['port']} 运行")

    # 记忆状态以便后端重启后自动恢复
    config_manager.set("mock_proxy_enabled", True)
    config_manager.set("mock_proxy_port", port)
    return Response(data={"running": True, "port": port, "rules_count": _rules_count(session)},
                    message=f"Mock 代理已启动，监听端口 {port}")


@router.post("/stop", response_model=Response[dict])
async def stop_proxy_api(session: SessionDep, user: CurrentUser):
    """停止 Mock 代理监听。"""
    from app.services import mock_proxy
    from config import config_manager

    await mock_proxy.stop_proxy()
    config_manager.set("mock_proxy_enabled", False)
    return Response(data={"running": False, "port": None, "rules_count": _rules_count(session)},
                    message="Mock 代理已停止")


def _rules_count(session) -> int:
    return len(session.exec(select(MockProxyRule)).all())


@router.get("/rules", response_model=Response[list])
def get_rules(session: SessionDep, user: CurrentUser):
    """获取全部转发规则。"""
    rules = session.exec(select(MockProxyRule).order_by(MockProxyRule.created_at.desc())).all()
    return Response(data=rules)


@router.post("/rules", response_model=Response[MockProxyRule])
def create_rule(session: SessionDep, user: CurrentUser, rule: MockProxyRule):
    """创建转发规则。"""
    if not (rule.host_pattern or "").strip():
        return Response(code=status.HTTP_400_BAD_REQUEST, message="主机模式不能为空")
    # 校验路径模式可编译
    from app.services.mock_proxy import compile_path_pattern
    compile_path_pattern(rule.path_pattern)

    prefix = _normalize_service_prefix(rule.service_prefix)
    if prefix is None:
        return Response(code=status.HTTP_400_BAD_REQUEST, message="服务前缀需形如 /contract，且不能含 { } 或 *")
    rule.service_prefix = prefix
    rule.user_id = user.user_id
    rule.host_pattern = rule.host_pattern.strip()
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return Response(data=rule, message="转发规则已创建")


@router.put("/rules/{rule_id}", response_model=Response[MockProxyRule])
def update_rule(session: SessionDep, user: CurrentUser, rule_id: int, rule: MockProxyRule):
    """更新转发规则。"""
    db_rule = session.get(MockProxyRule, rule_id)
    if not db_rule:
        return Response(code=status.HTTP_404_NOT_FOUND, message="转发规则不存在")
    if not ((rule.host_pattern or "") .strip() or (db_rule.host_pattern or "").strip()):
        return Response(code=status.HTTP_400_BAD_REQUEST, message="主机模式不能为空")
    from app.services.mock_proxy import compile_path_pattern
    compile_path_pattern(rule.path_pattern)

    rule_data = rule.model_dump(exclude_unset=True)
    if "service_prefix" in rule_data:
        prefix = _normalize_service_prefix(rule_data["service_prefix"])
        if prefix is None:
            return Response(code=status.HTTP_400_BAD_REQUEST, message="服务前缀需形如 /contract，且不能含 { } 或 *")
        rule_data["service_prefix"] = prefix
    rule_data.pop("created_at", None)
    rule_data.pop("updated_at", None)
    db_rule.sqlmodel_update(rule_data)
    session.add(db_rule)
    session.commit()
    session.refresh(db_rule)
    return Response(data=db_rule, message="转发规则已更新")


@router.delete("/rules/{rule_id}", response_model=Response)
def delete_rule(session: SessionDep, user: CurrentUser, rule_id: int):
    """删除转发规则。"""
    db_rule = session.get(MockProxyRule, rule_id)
    if not db_rule:
        return Response(code=status.HTTP_404_NOT_FOUND, message="转发规则不存在")
    session.delete(db_rule)
    session.commit()
    return Response(message="转发规则已删除")



class PreviewRequest(BaseModel):
    host: str
    method: str = "GET"
    path: str = "/"
    query: Optional[dict] = None
    body: Optional[str] = None
    headers: Optional[dict] = None


def _representative_path(config) -> str:
    """把配置的 url_path 里的 {param} 换成 1，得到可用于覆盖率判定的代表路径。"""
    import re as _re
    path = config.url_path or "/"
    return _re.sub(r"\{[^}]+\}", "1", path)


@router.get("/platform-mocks", response_model=Response[dict])
def get_platform_mocks(session: SessionDep, user: CurrentUser):
    """接口平台 Mock 在代理侧的覆盖情况。

    判定与生产匹配逻辑同源：把配置路径的 {param} 换成 1，再用规则的
    服务前缀 + 主机 + 路径范围验证「这条 Mock 会不会被代理拦截」。
    """
    from app.services.mock_matcher import (
        compile_path_pattern, match_address, match_host, path_matches,
    )

    rules = session.exec(select(MockProxyRule).where(MockProxyRule.enabled == True)).all()
    configs = session.exec(
        select(MockConfig).where(MockConfig.enabled == True).order_by(MockConfig.id)
    ).all()

    items = []
    covered_count = 0
    for config in configs:
        covering = []
        representative = _representative_path(config)
        for rule in rules:
            prefix = (rule.service_prefix or "").rstrip("/")
            full_path = (prefix + representative) if prefix else representative
            host_ok = match_host(rule.host_pattern, _host_sample(rule.host_pattern))
            path_ok = (
                path_matches(compile_path_pattern(rule.path_pattern), full_path)
                or path_matches(compile_path_pattern(rule.path_pattern), representative)
            )
            if host_ok and path_ok:
                covering.append({"id": rule.id, "name": rule.name, "host_pattern": rule.host_pattern})

        bound = bool(getattr(config, "proxy_urls", None))
        is_covered = bool(covering) or bound
        if is_covered:
            covered_count += 1
        items.append({
            "id": config.id,
            "name": config.name,
            "method": config.method,
            "url_path": config.url_path,
            "hosts": getattr(config, "hosts", None) or [],
            "proxy_urls": getattr(config, "proxy_urls", None) or [],
            "enabled": config.enabled,
            "covered": is_covered,
            "bound": bound,
            "covering_rules": covering,
        })

    return Response(data={
        "items": items,
        "enabled_configs": len(configs),
        "covered_configs": covered_count,
        "rules_count": len(rules),
    })


def _host_sample(host_pattern: str) -> str:
    """把 *.example.com 变成可判定的具体主机，供覆盖率演示用。"""
    pattern = (host_pattern or "").strip()
    if pattern.startswith("*."):
        return "demo" + pattern[1:]
    return pattern


@router.post("/preview", response_model=Response[dict])
async def preview_mock(session: SessionDep, user: CurrentUser, body: PreviewRequest):
    """预览某个真实请求会不会被代理拦成 Mock（不写流量日志）。"""
    from app.routes.mock_server import resolve_mock_response
    from app.services.mock_matcher import path_candidates
    from app.services.mock_proxy import find_matching_rule

    rule = find_matching_rule(body.host, body.path)
    prefix = (rule.service_prefix or "") if rule is not None else ""
    candidates = path_candidates(body.path, prefix)
    label = f"[预览:{(rule.name or '').strip() or '规则' + str(rule.id)}]" if rule is not None else "[预览:接口地址]"

    resolution = None
    try:
        resolution = await resolve_mock_response(
            body.path, body.method.upper(), body.query or {}, body.body, body.headers or {},
            host=body.host, path_candidates=candidates,
            log_label=label, save_log=False,
        )
    except Exception as exc:  # noqa: BLE001 - 预览失败按未命中处理并回传原因
        return Response(data={
            "intercepted": rule is not None,
            "matched": False,
            "error": str(exc),
            "rule": None if rule is None else {"id": rule.id, "name": rule.name},
            "candidates": candidates,
        })

    data = {
        "intercepted": rule is not None,
        "matched": resolution is not None,
        "source": None,
        "rule": None if rule is None else {
            "id": rule.id, "name": rule.name, "service_prefix": rule.service_prefix,
        },
        "candidates": candidates,
    }
    if resolution is not None:
        data.update({
            "source": "config" if resolution.config_id is not None else "project",
            "config_id": resolution.config_id,
            "config_name": resolution.config_name,
            "status_code": resolution.status_code,
            "body_preview": (resolution.body or "")[:2000],
            "matched_url": resolution.matched_url,
        })
    return Response(data=data)


@public_router.get("/ca-cert")
def download_ca_cert():
    """下载 MITM CA 根证书（免认证，供导入信任区）。"""
    from fastapi import HTTPException
    from app.services.proxy_cert import load_ca_cert_pem

    try:
        pem = load_ca_cert_pem()
    except Exception:
        raise HTTPException(status_code=500, detail="CA 证书生成失败")
    return HTTPResponse(
        content=pem,
        media_type="application/x-x509-ca-cert",
        headers={"Content-Disposition": 'attachment; filename="mock-proxy-ca.pem"'},
    )

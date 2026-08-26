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
from db.models import MockProxyRule
from utils.base_response import Response

# 需要权限的管理路由
router = APIRouter(prefix="/mock-proxy", tags=["mock-proxy"])

# 免认证公开路由（CA 证书下载）
public_router = APIRouter(prefix="/mock-proxy", tags=["mock-proxy"])


class ProxyStartRequest(BaseModel):
    port: Optional[int] = None


@router.get("/status", response_model=Response[dict])
def get_proxy_status(session: SessionDep, user: CurrentUser):
    """获取代理运行状态与规则数量。"""
    from app.services.mock_proxy import get_proxy_state

    rules_count = len(session.exec(select(MockProxyRule)).all())
    state = get_proxy_state()
    return Response(data={
        "running": state["running"],
        "port": state["port"],
        "rules_count": rules_count,
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

from typing import List

from fastapi import APIRouter, status
from sqlmodel import select

from app.deps import SessionDep
from db.models import MockConfig
from utils.base_response import Response

router = APIRouter(prefix="/mock-configs", tags=["mock-configs"])


@router.get("", response_model=Response[List[MockConfig]])
def get_mock_configs(session: SessionDep):
    """获取所有Mock配置"""
    configs = session.exec(select(MockConfig).order_by(MockConfig.created_at.desc())).all()
    return Response(data=configs)


@router.post("", response_model=Response[MockConfig])
def create_mock_config(session: SessionDep, config: MockConfig):
    """创建Mock配置"""
    session.add(config)
    session.commit()
    session.refresh(config)
    return Response(data=config, message="Mock配置已创建")


@router.put("/{config_id}", response_model=Response[MockConfig])
def update_mock_config(session: SessionDep, config_id: int, config: MockConfig):
    """更新Mock配置"""
    db_config = session.get(MockConfig, config_id)
    if not db_config:
        return Response(code=status.HTTP_404_NOT_FOUND, message="Mock配置不存在")
    config_data = config.model_dump(exclude_unset=True)
    config_data.pop("created_at", None)
    config_data.pop("updated_at", None)
    db_config.sqlmodel_update(config_data)
    session.add(db_config)
    session.commit()
    session.refresh(db_config)
    return Response(data=db_config, message="Mock配置已更新")


@router.delete("/{config_id}", response_model=Response)
def delete_mock_config(session: SessionDep, config_id: int):
    """删除Mock配置"""
    db_config = session.get(MockConfig, config_id)
    if not db_config:
        return Response(code=status.HTTP_404_NOT_FOUND, message="Mock配置不存在")
    session.delete(db_config)
    session.commit()
    return Response(message="Mock配置已删除")

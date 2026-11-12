"""系统配置共享读取。

数据不按用户隔离后，系统配置全局唯一（SystemConfig.user_id IS NULL）；
历史遗留的按用户配置行作为只读回退（取最近更新的一条），保证升级后配置不丢。
"""
from typing import Optional

from sqlmodel import select

from db.models import SystemConfig


def get_shared_system_config(session) -> Optional[SystemConfig]:
    """返回全局共享的系统配置行。

    优先取 user_id IS NULL 的全局行；不存在时回退最近更新的历史按用户行
    （只读回退，不修改数据，避免并发写入竞争）。
    """
    config = session.exec(
        select(SystemConfig).where(SystemConfig.user_id.is_(None))
    ).first()
    if config:
        return config
    return session.exec(
        select(SystemConfig).order_by(SystemConfig.updated_at.desc())
    ).first()

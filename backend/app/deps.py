from typing import Generator, Annotated

from fastapi import Depends
from sqlmodel import Session

from db.db import engine
from app.auth import get_current_user, UserInfo


def get_db() -> Generator[Session, None, None]:
    """获取数据库会话"""
    with Session(engine) as session:
        yield session
    
SessionDep = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[UserInfo, Depends(get_current_user)]

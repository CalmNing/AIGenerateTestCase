from typing import Generator, Annotated

from fastapi import Depends
from sqlmodel import Session

from db.db import engine


def get_db() -> Generator[Session, None, None]:
    """获取数据库会话"""
    with Session(engine) as session:
        yield session
    
SessionDep = Annotated[Session, Depends(get_db)]
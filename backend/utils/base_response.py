from typing import Generic, Optional

from pydantic import BaseModel
from typing_extensions import TypeVar

T = TypeVar("T")


class Response(BaseModel, Generic[T]):
    code: int = 200
    message: str = "Success"
    data: Optional[T] = "null"
    model_config = {
        "arbitrary_types_allowed": True,
    }

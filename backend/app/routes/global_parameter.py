import re
from copy import deepcopy
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from app.deps import SessionDep, CurrentUser
from db.models import GlobalParameter
from utils.base_response import Response

router = APIRouter(prefix="/global-parameters", tags=["global-parameters"])

# 复制环境时生成的名称后缀：「(复制)」/「(复制 2)」/「(复制 3)」…
_COPY_SUFFIX_RE = re.compile(r"\s*\(复制(?:\s*\d+)?\)\s*$")


def _strip_copy_suffix(name: str) -> str:
    """去掉末尾的 (复制) / (复制 2) 后缀得到基名；全被剥掉时回退原名。"""
    return _COPY_SUFFIX_RE.sub("", name).strip() or name


def _build_unique_copy_name(source_name: str, existing_names: set[str]) -> str:
    """基于源环境名生成唯一的副本名：原名 (复制)、原名 (复制 2)…"""
    base = _strip_copy_suffix(source_name)
    candidate = f"{base} (复制)"
    if candidate not in existing_names:
        return candidate
    n = 2
    while f"{base} (复制 {n})" in existing_names:
        n += 1
    return f"{base} (复制 {n})"


@router.get("", response_model=Response)
def get_global_parameters(session: SessionDep, user: CurrentUser):
    """获取所有全局参数配置"""
    try:
        global_parameters = session.exec(
            select(GlobalParameter)
        ).all()
        return Response(code=200, message="Success", data=global_parameters)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取全局参数配置失败: {str(e)}")


@router.post("", response_model=Response)
def create_global_parameter(global_parameter: GlobalParameter, session: SessionDep, user: CurrentUser):
    """创建全局参数配置"""
    try:
        # 自动填充 user_id
        global_parameter.user_id = user.user_id
        # 如果设置为默认环境，将其他环境的默认状态设为False
        if global_parameter.is_default:
            for param in session.exec(select(GlobalParameter)).all():
                param.is_default = False
                session.add(param)
        
        session.add(global_parameter)
        session.commit()
        session.refresh(global_parameter)
        return Response(code=200, message="Success", data=global_parameter)
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"创建全局参数配置失败: {str(e)}")


@router.post("/{parameter_id}/copy", response_model=Response)
def copy_global_parameter(parameter_id: int, session: SessionDep, user: CurrentUser):
    """复制全局参数配置（环境）：生成新环境并完整复制其参数"""
    try:
        source = session.get(GlobalParameter, parameter_id)
        if not source:
            raise HTTPException(status_code=404, detail="全局参数配置不存在")

        all_parameters = session.exec(select(GlobalParameter)).all()
        existing_names = {param.name for param in all_parameters}
        copied = GlobalParameter(
            name=_build_unique_copy_name(source.name, existing_names),
            parameters=deepcopy(source.parameters),
            # 副本不继承默认标记，保证全局默认环境唯一且仍属于源环境
            is_default=False,
            user_id=user.user_id,
        )

        session.add(copied)
        session.commit()
        session.refresh(copied)
        return Response(code=200, message="Success", data=copied)
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"复制全局参数配置失败: {str(e)}")


@router.put("/{parameter_id}", response_model=Response)
def update_global_parameter(parameter_id: int, global_parameter: GlobalParameter, session: SessionDep, user: CurrentUser):
    """更新全局参数配置"""
    try:
        db_global_parameter = session.get(GlobalParameter, parameter_id)
        if not db_global_parameter:
            raise HTTPException(status_code=404, detail="全局参数配置不存在")
        
        # 如果设置为默认环境，将其他环境的默认状态设为False
        if global_parameter.is_default:
            for param in session.exec(select(GlobalParameter)).all():
                param.is_default = False
                session.add(param)
        
        # 更新字段
        for field, value in global_parameter.model_dump(exclude_unset=True).items():
            setattr(db_global_parameter, field, value)
        
        session.add(db_global_parameter)
        session.commit()
        session.refresh(db_global_parameter)
        return Response(code=200, message="Success", data=db_global_parameter)
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"更新全局参数配置失败: {str(e)}")


@router.delete("/{parameter_id}", response_model=Response)
def delete_global_parameter(parameter_id: int, session: SessionDep, user: CurrentUser):
    """删除全局参数配置"""
    try:
        db_global_parameter = session.get(GlobalParameter, parameter_id)
        if not db_global_parameter:
            raise HTTPException(status_code=404, detail="全局参数配置不存在")
        
        session.delete(db_global_parameter)
        session.commit()
        return Response(code=200, message="Success", data=None)
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"删除全局参数配置失败: {str(e)}")


@router.get("/default", response_model=Response)
def get_default_global_parameter(session: SessionDep, user: CurrentUser):
    """获取默认全局参数配置"""
    try:
        default_parameter = session.exec(
            select(GlobalParameter).where(
                GlobalParameter.is_default == True
            )
        ).first()
        if not default_parameter:
            # 如果没有默认环境，返回第一个环境
            default_parameter = session.exec(
                select(GlobalParameter)
            ).first()
        return Response(code=200, message="Success", data=default_parameter)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取默认全局参数配置失败: {str(e)}")


class ExtractionRule(BaseModel):
    """提取规则"""
    variable: str   # 要保存的环境变量名
    jsonpath: str   # JSONPath 表达式，如 $.data.token


class ExtractAndSaveRequest(BaseModel):
    """从响应中提取变量并保存到环境"""
    environment_id: int
    response_data: dict | list
    extractions: List[ExtractionRule]


@router.post("/extract-and-save", response_model=Response)
def extract_and_save(request: ExtractAndSaveRequest, session: SessionDep, user: CurrentUser):
    """从响应数据中通过 JSONPath 提取变量，保存到指定环境参数中"""
    try:
        from jsonpath_ng import parse

        env = session.get(GlobalParameter, request.environment_id)
        if not env:
            raise HTTPException(status_code=404, detail="环境配置不存在")

        # 构建现有参数的 key->index 映射
        params = list(env.parameters)
        param_index = {p.get("key"): i for i, p in enumerate(params) if isinstance(p, dict) and p.get("key")}
        extracted = {}

        for rule in request.extractions:
            try:
                jsonpath_expr = parse(rule.jsonpath)
                matches = jsonpath_expr.find(request.response_data)
                if matches:
                    value = matches[0].value
                    extracted[rule.variable] = str(value) if not isinstance(value, str) else value

                    # 更新或添加到参数列表
                    if rule.variable in param_index:
                        idx = param_index[rule.variable]
                        params[idx] = {**params[idx], "value": extracted[rule.variable]}
                    else:
                        params.append({"key": rule.variable, "value": extracted[rule.variable]})
                        param_index[rule.variable] = len(params) - 1
            except Exception as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"JSONPath 提取失败 [{rule.variable}]: {rule.jsonpath}, 错误: {str(e)}",
                )

        env.parameters = params
        session.add(env)
        session.commit()
        session.refresh(env)

        return Response(code=200, message="Success", data=extracted)
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"提取保存失败: {str(e)}")

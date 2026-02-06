from fastapi import APIRouter, HTTPException, Depends
from sqlmodel import Session, select
from typing import List, Optional

from db.db import get_db
from db.models import GlobalParameter
from utils.base_response import Response as BaseResponse

router = APIRouter(prefix="/global-parameters", tags=["global-parameters"])


@router.get("", response_model=BaseResponse)
def get_global_parameters(db: Session = Depends(get_db)):
    """获取所有全局参数配置"""
    try:
        global_parameters = db.exec(select(GlobalParameter)).all()
        return BaseResponse(code=200, msg="Success", success=True, data=global_parameters)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取全局参数配置失败: {str(e)}")


@router.post("", response_model=BaseResponse)
def create_global_parameter(global_parameter: GlobalParameter, db: Session = Depends(get_db)):
    """创建全局参数配置"""
    try:
        # 如果设置为默认环境，将其他环境的默认状态设为False
        if global_parameter.is_default:
            db.exec(select(GlobalParameter).where(GlobalParameter.is_default == True)).all()
            for param in db.exec(select(GlobalParameter)).all():
                param.is_default = False
                db.add(param)
        
        db.add(global_parameter)
        db.commit()
        db.refresh(global_parameter)
        return BaseResponse(code=200, msg="Success", success=True, data=global_parameter)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"创建全局参数配置失败: {str(e)}")


@router.put("/{parameter_id}", response_model=BaseResponse)
def update_global_parameter(parameter_id: int, global_parameter: GlobalParameter, db: Session = Depends(get_db)):
    """更新全局参数配置"""
    try:
        db_global_parameter = db.get(GlobalParameter, parameter_id)
        if not db_global_parameter:
            raise HTTPException(status_code=404, detail="全局参数配置不存在")
        
        # 如果设置为默认环境，将其他环境的默认状态设为False
        if global_parameter.is_default:
            for param in db.exec(select(GlobalParameter)).all():
                param.is_default = False
                db.add(param)
        
        # 更新字段
        for field, value in global_parameter.dict(exclude_unset=True).items():
            setattr(db_global_parameter, field, value)
        
        db.add(db_global_parameter)
        db.commit()
        db.refresh(db_global_parameter)
        return BaseResponse(code=200, msg="Success", success=True, data=db_global_parameter)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"更新全局参数配置失败: {str(e)}")


@router.delete("/{parameter_id}", response_model=BaseResponse)
def delete_global_parameter(parameter_id: int, db: Session = Depends(get_db)):
    """删除全局参数配置"""
    try:
        db_global_parameter = db.get(GlobalParameter, parameter_id)
        if not db_global_parameter:
            raise HTTPException(status_code=404, detail="全局参数配置不存在")
        
        db.delete(db_global_parameter)
        db.commit()
        return BaseResponse(code=200, msg="Success", success=True, data=None)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"删除全局参数配置失败: {str(e)}")


@router.get("/default", response_model=BaseResponse)
def get_default_global_parameter(db: Session = Depends(get_db)):
    """获取默认全局参数配置"""
    try:
        default_parameter = db.exec(select(GlobalParameter).where(GlobalParameter.is_default == True)).first()
        if not default_parameter:
            # 如果没有默认环境，返回第一个环境
            default_parameter = db.exec(select(GlobalParameter)).first()
        return BaseResponse(code=200, msg="Success", success=True, data=default_parameter)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取默认全局参数配置失败: {str(e)}")

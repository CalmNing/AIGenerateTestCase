from datetime import datetime

try:
    from fastapi import FastAPI, Body  # type: ignore
    from fastapi.middleware.cors import CORSMiddleware  # type: ignore
except Exception as e:
    raise RuntimeError("Missing dependency 'fastapi'. Install with: pip install fastapi uvicorn") from e

from fastapi import HTTPException, status

from backend.db.db_manager import db_manager

try:
    from pydantic import BaseModel
except Exception as e:
    raise RuntimeError("Missing dependency 'pydantic'. Install with: pip install pydantic") from e


# 请求模型
class CreateSessionRequest(BaseModel):
    name: str


# 创建FastAPI应用
app = FastAPI(
    title="测试用例生成工具API",
    description="测试用例生成工具的后端API",
    version="1.0.0"
)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 在生产环境中应该配置具体的前端域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 健康检查
@app.get("/health")
def health_check():
    return {"status": "ok", "message": "测试用例生成工具API运行正常"}


# 会话管理API
@app.get("/api/sessions")
def get_sessions():
    """获取所有会话"""
    sessions = db_manager.get_sessions()
    return {
        "code": 200,
        "message": "获取会话成功",
        "data": [
            {
                "id": session.id,
                "name": session.name,
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat()
            }
            for session in sessions
        ]
    }


@app.post("/api/sessions")
def create_session(request: CreateSessionRequest):
    """创建新会话"""
    session = db_manager.create_session(request.name)
    return {
        "code": 200,
        "message": "创建会话成功",
        "data": {
            "id": session.id,
            "name": session.name,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat()
        }
    }


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    """删除会话"""
    result = db_manager.delete_session(session_id)
    if result:
        return {
            "code": 200,
            "message": "删除会话成功"
        }
    else:
        return {
            "code": 500,
            "message": "删除会话失败"
        }


# 测试用例管理API
@app.get("/api/sessions/{session_id}/testcases")
def get_testcases(session_id: str, case_name: str = None, status: str = None):
    """获取会话的测试用例"""
    testcases = db_manager.get_testcases(session_id)
    totalNumber = len(testcases)
    completed = sum(1 for tc in testcases if tc.status == "completed")
    pending = sum(1 for tc in testcases if tc.status != "completed")

    # 按用例名称筛选
    if case_name:
        testcases = [tc for tc in testcases if case_name in tc.case_name]

    # 按状态筛选
    if status:
        testcases = [tc for tc in testcases if tc.status == status]

    return {
        "code": 200,
        "message": "获取测试用例成功",
        "data": {
            "items": [
                {
                    "id": tc.id,
                    "case_name": tc.case_name,
                    "case_level": tc.case_level,
                    "status": tc.status,
                    "preset_conditions": tc.preset_conditions,
                    "steps": tc.steps,
                    "expected_results": tc.expected_results,
                    "created_at": tc.created_at.isoformat()
                }
                for tc in testcases
            ],
            "totalNumber": totalNumber,
            "completed": completed,
            "pending": pending
        }
    }


# 请求模型
class GenerateTestcasesRequest(BaseModel):
    requirement: str
    model_type: str = "api"  # "api" 或 "ollama"
    api_key: str = ""  # API 密钥（当 model_type 为 "api" 时使用）
    ollama_url: str = ""  # Ollama URL（当 model_type 为 "ollama" 时使用）
    ollama_model: str = ""  # Ollama 模型名称（当 model_type 为 "ollama" 时使用）


@app.post("/api/sessions/{session_id}/testcases")
def generate_testcases(session_id: str, request: GenerateTestcasesRequest):
    """生成测试用例"""
    from backend.utils.model_utils import generate_testcases

    # 基本输入校验
    if not request.requirement or not request.requirement.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="requirement 不能为空")

    # 回退配置：如果请求未提供 api_key/ollama 配置，则从 config_manager 读取
    api_key = request.api_key
    ollama_url = request.ollama_url
    ollama_model = request.ollama_model

    # 模型参数校验
    if request.model_type == "api" and not api_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="api_key 未提供!")
    if request.model_type == "ollama" and (not ollama_url or not ollama_model):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Ollama 配置不完整（ollama_url/ollama_model）")

    try:
        testcases = generate_testcases(
            request.requirement,
            session_id=session_id,
            model_type=request.model_type,
            api_key=api_key,
            ollama_url=ollama_url,
            ollama_model=ollama_model,

        )
        saved_testcases = db_manager.save_testcases(session_id, testcases)
        return {
            "code": 200,
            "message": "生成测试用例成功",
            "data": [
                {
                    "id": tc.id,
                    "case_name": tc.case_name,
                    "case_level": tc.case_level,
                    "preset_conditions": tc.preset_conditions,
                    "steps": tc.steps,
                    "expected_results": tc.expected_results,
                    "created_at": tc.created_at.isoformat(),
                    "updated_at": tc.updated_at.isoformat()
                }
                for tc in saved_testcases
            ]
        }
    except Exception as e:
        return {
            "code": 500,
            "message": f"生成测试用例失败: {str(e)}"
        }


@app.put("/api/sessions/{session_id}/testcases/{testcase_id}")
def update_testcase(session_id: str, testcase_id: int, testcase: dict):
    """更新测试用例"""
    from backend.db.models import TestCase

    updated_testcase = TestCase(
        id=testcase_id,
        status=testcase.get("status"),
        case_name=testcase.get("case_name"),
        case_level=testcase.get("case_level"),
        preset_conditions=testcase.get("preset_conditions"),
        steps=testcase.get("steps"),
        updated_at=datetime.now(),
        expected_results=testcase.get("expected_results"),
        session_id=session_id
    )

    result = db_manager.save_testcase(session_id, updated_testcase)
    if result:
        return {
            "code": 200,
            "message": "更新测试用例成功"
        }
    else:
        return {
            "code": 500,
            "message": "更新测试用例失败"
        }


@app.delete("/api/sessions/{session_id}/testcases/{testcase_id}")
def delete_testcase(session_id: str, testcase_id: int):
    """删除测试用例"""
    result = db_manager.delete_testcase(session_id, testcase_id)
    if result:
        return {
            "code": 200,
            "message": "删除测试用例成功"
        }
    else:
        return {
            "code": 500,
            "message": "删除测试用例失败"
        }

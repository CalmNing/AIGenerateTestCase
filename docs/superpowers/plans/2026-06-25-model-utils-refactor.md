# model_utils.py 重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 1934 行的 `model_utils.py` 拆分为 4 个职责清晰的模块：`prompts.py`、`models.py`、`helpers.py`、`model_utils.py`。

**Architecture:** 按职责提取：提示词→配置类，Pydantic 模型→数据层，工具函数→辅助层，核心生成逻辑保留。通过 re-export 保持对外接口不变。

**Tech Stack:** Python 3.11, Pydantic, LangChain, SQLModel

## Global Constraints

- Python 3.11, FastAPI, SQLModel + SQLite
- 不修改前端、数据库模型、API 路由逻辑
- 不改变任何函数签名和行为
- `testcase.py` 的 `from utils.model_utils import ModelServiceUnavailableError, generate_testcases` 保持不变

---

## File Structure

| 文件 | 职责 | 来源 |
|------|------|------|
| `backend/utils/prompts.py` | 提示词配置类 | 从 model_utils.py 提取 SYSTEM_PROMPT (L897-1049) |
| `backend/utils/models.py` | Pydantic 数据模型 | 从 model_utils.py 提取 (L1056-1143) |
| `backend/utils/helpers.py` | 工具函数、异常类、回调类 | 从 model_utils.py 提取 (L187-895, L1271-1307) |
| `backend/utils/model_utils.py` | 核心生成逻辑 + re-export | 保留 create_local_model + generate_testcases |

---

### Task 1: 创建 prompts.py — 提示词配置

**Files:**
- Create: `backend/utils/prompts.py`

**Interfaces:**
- Produces: `PromptConfig.SYSTEM_PROMPT: str`

- [ ] **Step 1: 创建 prompts.py**

```python
"""提示词配置模块。"""


class PromptConfig:
    """AI 生成测试用例的提示词配置。"""

    SYSTEM_PROMPT = """你是一位软件测试专家，你的任务是帮助用户设计测试用例。
你需要根据用户的需求，设计出符合要求的测试用例。
你需要理解需求，使用合适的测试用例设计方法来设计用例。

测试用例应该包含以下信息：
- 用例名称
- 用例级别
- 前置条件
- 用例步骤
- 预期结果
```

**注意：** 将 `model_utils.py` 第 897-1049 行的 `SYSTEM_PROMPT = """..."""` 完整复制到这里，作为 `PromptConfig.SYSTEM_PROMPT` 的值。保持缩进正确（在类体内需多缩进一级）。

- [ ] **Step 2: 验证语法**

```bash
cd backend && python -c "import ast; ast.parse(open('utils/prompts.py', encoding='utf-8').read()); print('Syntax OK')"
```

Expected: `Syntax OK`

- [ ] **Step 3: 验证导入**

```bash
cd backend && python -c "from utils.prompts import PromptConfig; assert len(PromptConfig.SYSTEM_PROMPT) > 100; print('Import OK, prompt length:', len(PromptConfig.SYSTEM_PROMPT))"
```

Expected: `Import OK, prompt length: >100`

- [ ] **Step 4: Commit**

```bash
git add backend/utils/prompts.py
git commit -m "refactor: extract SYSTEM_PROMPT to prompts.py config class"
```

---

### Task 2: 创建 models.py — Pydantic 数据模型

**Files:**
- Create: `backend/utils/models.py`

**Interfaces:**
- Produces: `AssertionRule`, `ApiCallKeyValue`, `ApiCallStep`, `TestCase`, `ResponseFormat`, `TestCaseDesignMethod`, `AllowedValue`

- [ ] **Step 1: 创建 models.py**

将 `model_utils.py` 第 1050-1143 行的以下内容复制过来：

```python
"""AI 测试用例生成的数据模型定义。"""

from typing import List, Optional, Literal, Any

from pydantic import BaseModel, Field, model_validator


# 定义字面量类型：仅允许 1/2/3/4
AllowedValue = Literal[1, 2, 3, 4]


# 定义断言规则
class AssertionRule(BaseModel):
    """单个断言规则，匹配 api_test_tool._run_assertions 的期望格式"""
    type: str = Field(..., description="断言类型: status_code | status_code_range | response_time_lt | jsonpath_exists | jsonpath_equals")
    value: Optional[Any] = Field(default=None, description="断言值")
    min: Optional[int] = Field(default=None, description="范围最小值（用于 status_code_range）")
    max: Optional[int] = Field(default=None, description="范围最大值（用于 status_code_range）")
    jsonpath: Optional[str] = Field(default=None, description="JSONPath 表达式")

    @model_validator(mode='after')
    def validate_assertion_type(self) -> 'AssertionRule':
        """按断言类型验证必需字段。"""
        t = self.type
        if t == 'status_code' and self.value is None:
            raise ValueError(f'status_code 断言必须提供 value 参数')
        if t == 'status_code_range' and (self.min is None or self.max is None):
            raise ValueError(f'status_code_range 断言必须同时提供 min 和 max 参数')
        if t == 'jsonpath_exists' and not self.jsonpath:
            raise ValueError(f'jsonpath_exists 断言必须提供 jsonpath 参数')
        if t == 'jsonpath_equals' and (not self.jsonpath or self.value is None):
            raise ValueError(f'jsonpath_equals 断言必须同时提供 jsonpath 和 value 参数')
        return self


class ApiCallKeyValue(BaseModel):
    """API 调用步骤中的键值对（headers/parameters/variables）"""
    key: str = Field(..., description="字段名")
    value: str = Field(default="", description="字段值，可用 {{变量名}} 引用环境变量")
    in_field: Optional[str] = Field(default=None, alias="in", description="参数位置: query/path/header（仅 parameters 使用）")
    model_config = {"populate_by_name": True}


class ApiCallStep(BaseModel):
    """结构化的 API 调用步骤，对应执行时的 api_call 步骤"""
    endpoint_ref: int = Field(..., description="关联的接口编号（在 API 信息中的序号，从1开始）")
    description: str = Field(default="", description="该步骤的测试描述")
    headers: Optional[List[ApiCallKeyValue]] = Field(default=None, description="请求头，覆盖接口默认值")
    parameters: Optional[List[ApiCallKeyValue]] = Field(default=None, description="请求参数（含 query/path），覆盖接口默认值")
    body: Optional[str] = Field(default=None, description="请求体 JSON 字符串，覆盖接口默认值。使用 {{变量名}} 引用环境变量")
    variables: Optional[List[ApiCallKeyValue]] = Field(default=None, description="环境变量（可在 body/parameters/headers 中用 {{key}} 引用）")
    assertions: Optional[List[AssertionRule]] = Field(default=None, description="该步骤的断言规则，覆盖用例级和接口级断言")


# 定义测试用例数据类
class TestCase(BaseModel):
    """自定义 TestCase schema."""
    case_name: str = Field(..., description="用例名称")
    steps: List[Union[str, ApiCallStep]] = Field(..., description="用例步骤，支持纯文本步骤和结构化 api_call 步骤")
    case_level: Optional[AllowedValue] = Field(default=4, description="用例级别: 1-功能 2-边界 3-异常 4-场景")
    preset_conditions: List[Union[str, ApiCallStep]] = Field(default_factory=list, description="前置条件，支持纯文本和 api_call 步骤")
    expected_results: List[str] = Field(default_factory=list, description="预期结果列表")
    api_endpoint_ref: Optional[List[int]] = Field(default=None, description="关联的接口编号列表")


class ResponseFormat(BaseModel):
    """定义结构化输出格式"""
    testcases: List[TestCase] = Field(..., description="测试用例列表")


class TestCaseDesignMethod(BaseModel):
    """自定义 TestCaseDesignMethod schema."""
    method: str
    description: str | None = None
```

**注意：** 从原始文件精确复制，确保每个类的字段、类型注解、描述完全一致。

- [ ] **Step 2: 验证语法**

```bash
cd backend && python -c "import ast; ast.parse(open('utils/models.py', encoding='utf-8').read()); print('Syntax OK')"
```

Expected: `Syntax OK`

- [ ] **Step 3: 验证导入**

```bash
cd backend && python -c "from utils.models import AssertionRule, ApiCallStep, TestCase, ResponseFormat; print('Import OK')"
```

Expected: `Import OK`

- [ ] **Step 4: Commit**

```bash
git add backend/utils/models.py
git commit -m "refactor: extract Pydantic models to models.py"
```

---

### Task 3: 创建 helpers.py — 工具函数和辅助类

**Files:**
- Create: `backend/utils/helpers.py`

**Interfaces:**
- Produces: 所有工具函数、异常类、回调类（供 model_utils.py 导入）

- [ ] **Step 1: 创建 helpers.py**

将 `model_utils.py` 中以下函数/类提取到这里：

**需要导入的依赖：**
```python
import json
import logging
import os
import re
import time
from typing import Any

import yaml
from langchain_core.callbacks import BaseCallbackHandler
from sqlmodel import select, desc

from db.models import HistoryPrompt, ApiEndpoint
from utils.history_prompt_cleaner import clean_history_prompt_content
```

**需要提取的函数/类（从 model_utils.py 精确复制）：**

1. `_load_json_object` (L187-253)
2. `_McpPermissionError` (L254-257)
3. `_McpToolValidationError` (L258-261)
4. `ModelServiceUnavailableError` (L262-265)
5. `_is_model_service_unavailable` (L266-278)
6. `_extract_mcp_permission_error` (L279-300)
7. `_extract_mcp_validation_error` (L301-316)
8. `_extract_json_with_response` (L317-340)
9. `_is_history_noise_segment` (L341-356)
10. `_clean_history_prompt_content` (L357-360)
11. `_normalize_history_module_id` (L361-369)
12. `_upsert_history_prompt` (L370-405)
13. `_repair_json` (L406-481)
14. `_extract_json_with_response` (L482-536)
15. `_normalize_level` (L537-579)
16. `_normalize_testcase_dict` (L580-731)
17. `_normalize_extracted_json` (L732-848)
18. `_TokenUsageCallback` (L849-895)
19. `_load_skill_bodies` (L1271-1306)
20. `_build_history_context` (L1307-1340)

**注意：** 有些函数内部相互调用（如 `_normalize_testcase_dict` 调用 `_normalize_level`），确保提取后调用关系正确。函数签名和实现必须与原始完全一致。

- [ ] **Step 2: 验证语法**

```bash
cd backend && python -c "import ast; ast.parse(open('utils/helpers.py', encoding='utf-8').read()); print('Syntax OK')"
```

Expected: `Syntax OK`

- [ ] **Step 3: 验证导入**

```bash
cd backend && python -c "from utils.helpers import ModelServiceUnavailableError, _repair_json, _normalize_testcase_dict; print('Import OK')"
```

Expected: `Import OK`

- [ ] **Step 4: Commit**

```bash
git add backend/utils/helpers.py
git commit -m "refactor: extract utility functions and helper classes to helpers.py"
```

---

### Task 4: 更新 model_utils.py — 精简为核心逻辑 + re-export

**Files:**
- Modify: `backend/utils/model_utils.py`

**Interfaces:**
- Consumes: `PromptConfig` from prompts.py, models from models.py, helpers from helpers.py
- Produces: `generate_testcases()`, `create_local_model()`, re-exports for backward compatibility

- [ ] **Step 1: 替换 model_utils.py**

将 `model_utils.py` 替换为以下结构（保留核心逻辑，删除已提取的代码）：

```python
"""AI 测试用例生成核心模块。"""

import json
import logging
import os
import re
import time
import uuid
from typing import List, Optional, Union, Any

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr

from sqlmodel import select

from db.models import TestCase as DBTestCase, ApiScenario, ApiEndpoint

# 从拆分模块导入
from utils.prompts import PromptConfig
from utils.models import (
    AllowedValue, AssertionRule, ApiCallKeyValue, ApiCallStep,
    TestCase, ResponseFormat, TestCaseDesignMethod,
)
from utils.helpers import (
    ModelServiceUnavailableError,
    _McpPermissionError,
    _McpToolValidationError,
    _is_model_service_unavailable,
    _extract_mcp_permission_error,
    _extract_mcp_validation_error,
    _load_json_object,
    _extract_json_with_response,
    _repair_json,
    _normalize_level,
    _normalize_testcase_dict,
    _normalize_extracted_json,
    _is_history_noise_segment,
    _clean_history_prompt_content,
    _normalize_history_module_id,
    _upsert_history_prompt,
    _TokenUsageCallback,
    _load_skill_bodies,
    _build_history_context,
)

# 保持兼容性 re-export
SYSTEM_PROMPT = PromptConfig.SYSTEM_PROMPT

logger = logging.getLogger(__name__)
checkpointer = InMemorySaver()
```

然后保留以下核心函数（从原始文件复制，不修改函数体）：

- `create_local_model()` (L1146-1270)
- `generate_testcases()` (L1341-1934)

**注意：** `generate_testcases` 内部引用了大量已提取的函数，现在通过顶部导入可用。函数体无需修改。

- [ ] **Step 2: 验证语法**

```bash
cd backend && python -c "import ast; ast.parse(open('utils/model_utils.py', encoding='utf-8').read()); print('Syntax OK')"
```

Expected: `Syntax OK`

- [ ] **Step 3: 验证核心导入**

```bash
cd backend && python -c "from utils.model_utils import ModelServiceUnavailableError, generate_testcases, SYSTEM_PROMPT; print('Import OK'); print('Prompt length:', len(SYSTEM_PROMPT))"
```

Expected: `Import OK` and prompt length > 100

- [ ] **Step 4: 验证所有模块可导入**

```bash
cd backend && python -c "from utils.prompts import PromptConfig; from utils.models import TestCase, ApiCallStep; from utils.helpers import _repair_json; from utils.model_utils import generate_testcases; print('All imports OK')"
```

Expected: `All imports OK`

- [ ] **Step 5: Commit**

```bash
git add backend/utils/model_utils.py
git commit -m "refactor: slim down model_utils.py to core logic + re-exports

Removed extracted code to:
- utils/prompts.py (system prompt config)
- utils/models.py (Pydantic models)
- utils/helpers.py (utility functions and helper classes)

Backward compatibility maintained via re-exports."
```

---

### Task 5: 端到端验证

**Files:**
- None (验证任务)

- [ ] **Step 1: 验证 testcase.py 导入正常**

```bash
cd backend && python -c "from app.routes.testcase import generate_testcases; print('testcase.py import OK')"
```

Expected: `testcase.py import OK`

- [ ] **Step 2: 验证所有模块间依赖正确**

```bash
cd backend && python -c "
from utils.prompts import PromptConfig
from utils.models import TestCase, ApiCallStep, ResponseFormat
from utils.helpers import ModelServiceUnavailableError, _repair_json, _normalize_testcase_dict
from utils.model_utils import generate_testcases, create_local_model, SYSTEM_PROMPT
assert SYSTEM_PROMPT == PromptConfig.SYSTEM_PROMPT
print('All cross-module dependencies OK')
"
```

Expected: `All cross-module dependencies OK`

- [ ] **Step 3: 验证文件行数符合预期**

```bash
cd backend && wc -l utils/prompts.py utils/models.py utils/helpers.py utils/model_utils.py
```

Expected: 各文件行数与设计文档大致一致

- [ ] **Step 4: 最终 Commit**

```bash
git add -A
git commit -m "refactor: complete model_utils.py decomposition - all verifications passed"
```

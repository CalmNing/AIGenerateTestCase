# model_utils.py 重构设计

## 背景

`backend/utils/model_utils.py` 当前 1934 行，混合了提示词配置、Pydantic 模型、工具函数和核心生成逻辑，可读性差。

## 目标

将文件拆分为 4 个职责清晰的模块，保持对外接口不变。

## 文件结构

```
backend/utils/
├── model_utils.py    # 核心生成逻辑（精简后）
├── prompts.py        # 提示词配置类
├── models.py         # Pydantic 数据模型
└── helpers.py        # 工具函数、异常类、回调类
```

## 各文件内容

### prompts.py (~170 行)

- `class PromptConfig` — 静态配置类
  - `SYSTEM_PROMPT: str` — 系统提示词常量

### models.py (~100 行)

- `class AssertionRule(BaseModel)` — 断言规则
- `class ApiCallKeyValue(BaseModel)` — API 调用键值对
- `class ApiCallStep(BaseModel)` — API 调用步骤
- `class TestCase(BaseModel)` — 测试用例 schema
- `class ResponseFormat(BaseModel)` — 结构化输出格式
- `class TestCaseDesignMethod(BaseModel)` — 测试设计方法

### helpers.py (~600 行)

- **异常类：** `_McpPermissionError`、`_McpToolValidationError`、`ModelServiceUnavailableError`
- **工具函数：** `_load_json_object`、`_repair_json`、`_extract_json_with_response`、`_normalize_level`、`_normalize_testcase_dict`、`_normalize_extracted_json`、`_is_history_noise_segment`、`_clean_history_prompt_content`、`_normalize_history_module_id`、`_upsert_history_prompt`、`_load_skill_bodies`、`_build_history_context`
- **回调类：** `_TokenUsageCallback`
- **辅助函数：** `_is_model_service_unavailable`、`_extract_mcp_permission_error`、`_extract_mcp_validation_error`

### model_utils.py (~1000 行，精简后)

- `create_local_model()` — 创建本地模型
- `generate_testcases()` — 核心生成函数
- 从其他文件 re-export 关键符号保持兼容

## 兼容性

`testcase.py` 的现有导入保持不变：
```python
from utils.model_utils import ModelServiceUnavailableError, generate_testcases
```

通过 `model_utils.py` 顶部的 re-export 实现：
```python
from utils.helpers import ModelServiceUnavailableError
```

## 不涉及

- 不修改前端
- 不修改数据库模型
- 不修改 API 路由逻辑
- 不改变任何函数签名和行为

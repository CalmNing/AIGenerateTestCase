# 设计：提高测试用例生成的 Token 缓存命中率

## 问题诊断

排查 `backend/utils/model_utils.py` → `build_chat_model` → `config.py` 全链路后，确认**命中率几乎为 0 的根因是：全程没有开启 prompt caching**。

- `llm_factory.build_chat_model` 的 `extra_body` 只设了 `{"thinking": {"type": "disabled"}}`，**从未设置 `enable_prompt_cache`**。
- 全代码库 grep 确认没有任何 `enable_prompt_cache` / `prompt_cache_hit_tokens` 注入点（仅有 JWKS / Docker / MCP 工具列表 / skills 等无关缓存）。
- `_TokenUsageCallback` 只统计字符数，没有缓存埋点，所以当前根本无法观测命中率。

### 命中率低的两个次要原因

1. **静态系统提示词本来就是最好的缓存前缀**：`BASE_PROMPT + API_PROMPT / FUNCTIONAL_PROMPT`（约 4000 token）位于消息最前端，同类型请求完全一致 → 只要开启缓存，这是最大的收益点。
2. **agent / MCP 路径把动态工具列表拼进了系统提示词**：`tool_names` 随可用 MCP 工具变化，导致前缀不稳定。需要让工具列表排序确定化，使相同工具集的请求共享同一前缀。
3. **消息顺序已较优**：system（静态）在前，human（需求，最易变）在后；接口测试的 `api_context`（同会话复用）也在 human 前缀位置。无需大改顺序。

## 设计方案

### 1. 在 `build_chat_model` 开启 prompt caching（核心修复）
- 新增参数 `prompt_cache: bool | None = None`：
  - `None`（默认）→ 按 `base_url` 主机名自动判断：`api.deepseek.com` 等含 "deepseek" 的主机 → 开启。
  - 显式 `True/False` → 强制覆盖（供 config 开关使用）。
- 开启时向 `extra_body` 注入 `enable_prompt_cache: True`，与现有 thinking 标记合并。
- 抽一个 `_is_deepseek_host(base_url)` 便于单测。

### 2. config 增加开关
- `default_config` 增加 `prompt_cache_enabled: None`（None = 自动按 DeepSeek 判定）。
- `ConfigManager` 增加 `get_prompt_cache_enabled() -> Optional[bool]`。
- 前端/调用方可写 `true/false` 强制覆盖；不写则走自动判定，避免不兼容网关报错。

### 3. 所有生成路径透传该设置
- `model_utils.generate_testcases` 读取 `config_manager.get_prompt_cache_enabled()`，传给三处 `build_chat_model(...)` 调用，以及 `create_testcase_agent(...)`。
- `create_testcase_agent` 增加 `prompt_cache` 参数并透传给内部 `build_chat_model`。

### 4. 稳定 agent 路径的系统提示词前缀
- `create_testcase_agent` 中 `tool_names` 按 `t.name` 排序生成，保证相同工具集的请求系统前缀完全一致。

### 5. 缓存命中率埋点（验证手段）
- 在 `_TokenUsageCallback.on_llm_end` 解析 usage：
  - OpenAI/LangChain 结构：`response.llm_output["token_usage"]` 含 `prompt_cache_hit_tokens` / `prompt_cache_creation_tokens`（DeepSeek 字段）。
  - 兜底：`usage_metadata` 的 `cache_read_input_tokens` / `cache_creation_input_tokens`（Anthropic 命名）。
- 打印：`prompt_tokens / cache_hit / cache_creation / 命中率%`，并保存到回调实例供需要时使用。

### 6. Fallback 原始 API 调用对齐
- `_fallback_raw_api_call` 复用 `model.extra_body`（已含缓存决定）到 `client.chat.completions.create(extra_body=...)`，保持与主路径一致，避免对不兼容网关误开启。

## 预期收益
- 同类型（功能/接口）连续请求：系统提示词前缀（~4000 token）命中缓存，单请求省下约 80%+ 的 prompt token 重算。
- 同一会话内多次生成（接口测试）：`api_context` 前缀也叠加命中。
- agent 路径：同一工具集的多次 ReAct 轮次前缀共享缓存。

## 验证
- 单测 `tests/test_llm_factory.py`：自动判定（deepseek 开 / 其它关）、强制开 / 强制关。
- 运行现有测试套件确保无回归。
- 日志中观察 `[缓存命中]` 字段，确认开启后 `cache_hit` 随连续请求上升。

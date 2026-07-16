# 实现报告：拆分静态/动态前缀以提升生成用例 Token 缓存命中率

> 配套文档：`docs/cache-hit-root-cause.md`（根因分析）、`plan.md`（实现计划）
> 日期：2026-07-15

## 做了什么
把「大段可复用内容（接口规格 `api_context`、技能正文 `skill`）」从**可变的 human 消息**移到**静态 system 前缀**，让同一接口项目 / 技能组合反复生成时，前缀 KV 被 DeepSeek 缓存命中。

## 改动清单
| 文件 | 改动 |
|------|------|
| `backend/utils/model_utils.py` | 新增 `_build_generation_messages()` helper；`generate_testcases` 增加 `api_context=""` 参数；移除「skill 正文 prepend 进 requirement」逻辑；直连 / agent / ollama 三路径统一用 `system_text`(system) + `human_text`(human)；历史保存改存 `human_text` |
| `backend/app/routes/testcase.py` | 停止把 `api_context` 并入 `effective_requirement`，改为单独 `api_context=` 传参（前端无需改动） |
| `backend/tests/test_cache_prefix.py` | 新建 6 个 TDD 用例，验证拆分契约 |

## 拆分契约（helper 行为）
- `system_text` = `selected_prompt` +（可选）`## 参考技能与方法论` +（可选）`## 接口上下文（核心依据）`
  - 同一接口项目 / 同一技能组合下**逐字节一致** → DeepSeek 前缀 KV 缓存稳定锚点
  - `selected_prompt` 永远开头，保证全局基础提示词始终可命中
- `human_text` = 用户真实需求（接口测试加 `## 补充背景（次要）` 标签）→ 每次不同，放末尾

## 验证结果
- `tests/test_cache_prefix.py`：6 passed（api_context 进 system 不进 human、skill 进 system、相同参数前缀逐字节一致、功能性无补充标签、无 api_context 时 system==base、三段组合顺序正确）
- 全量 `tests/`：**133 passed**（原 127 + 6），无回归
- 运行时冒烟：确认 system 含 api_context/skill、human 不含 api_context、接口测试 human 带补充背景标签

## 预期收益
| 场景 | 改造前可缓存前缀 | 改造后可缓存前缀 |
|------|------------------|------------------|
| 接口测试（同项目反复生成） | ~4000 token（仅 base） | base + 技能 + **api_context（数千~上万 token）** |
| 同技能组合功能测试 | ~4000 token | base + 技能正文 |
| 纯自由文本功能测试 | ~4000 token | ~4000 token（需求本身可变，收益有限，符合理论上限） |

## 如何验证命中率提升
日志中搜 `[缓存命中]`（上一轮已在 `utils/helpers.py` 埋点），观察同一接口项目连续生成时 `cache_hit` 随请求上升。注意 DeepSeek 磁盘缓存 TTL 较短、需 ≥2 次同前缀请求才形成公共前缀单元，跨长间隔稀疏请求仍会 miss，属正常。

## 未改动（保持）
- 缓存开关 `enable_prompt_cache` 与 `[缓存命中]` 埋点（上轮已完成）
- 前端（无需改动，`api_context` 由服务端构建）
- 路由其余逻辑、异常处理、历史提示词语义（仅去除冗余 api_context 重复）

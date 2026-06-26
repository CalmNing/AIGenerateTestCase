# Task 3: 增强系统提示词中的依赖关系指导 - 完成报告

## 实现内容

在 `backend/utils/model_utils.py` 的 `SYSTEM_PROMPT` 字符串中追加了依赖关系指导段落。该段落位于覆盖率要求之后、API 测试用例示例之前，指导 AI 模型如何处理 `[依赖补全]` 标记的接口：

- 被标记接口作为前置步骤使用
- 在 `preset_conditions` 中生成 `api_call` 步骤调用新增接口
- 通过 `variables` 定义记录 ID 变量
- 后续步骤中使用 `{{record_id}}` 引用
- 每个依赖场景生成成功和异常用例

## 测试结果

- Python 语法检查：通过（使用 `utf-8-sig` 编码处理预存在的 BOM）
- 提示词内容验证：通过（`[依赖补全]` 已存在于 `SYSTEM_PROMPT`）

## 修改文件

- `backend/utils/model_utils.py` — 在 `SYSTEM_PROMPT` 中追加 7 行依赖关系指导文本

## 自检发现

- 文件存在预存在的 BOM 字符（U+FEFF），这是编码问题，非本次修改引入
- 使用 `utf-8-sig` 编码可正常解析语法

## 提交记录

- Commit: `61b1743` feat: add dependency inference guidance to system prompt

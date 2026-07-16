# 生成测试用例：需求文件上传（.docx / .txt / .md）

> 按 `/Superpowers` 流程实现：Brainstorming → (TDD) 实现 → 验证。

## 做了什么

让「深度生成（4 阶段）」入口支持上传需求文档，解析后作为生成测试用例的依据。

- **支持格式**：`.docx`（现代 Word）、`.txt`、`.md`（Markdown 按纯文本读）
- **合并规则**：文件内容作为生成主体；需求文本框里已输入的文字作为「补充背景」拼接在文件内容之后
- **接入入口**：仅 `generate-v2`（`POST /testcases/generate-v2`，即「深度生成」主按钮）

## 改动清单

| 文件 | 变更 |
|------|------|
| `backend/utils/file_parser.py` | **新增**。解析核心 `_parse_content` + 异步 `parse_uploaded_file`：`.docx` 用 python-docx 抽段落与表格；`.txt/.md` 按 `utf-8 → gbk → gb18030 → latin-1` 回退解码；校验扩展名 / 空文件 / 单文件 10MB / 内容 120k 字符截断；不支持或空解析抛 `ValueError` |
| `backend/app/routes/pipeline.py` | `generate_v2` 新增 `file: UploadFile = File(None)`；解析后按上述规则合并进 `requirement_text`；无文件且无文本返回 400 |
| `backend/requirements.txt` | 追加 `python-docx>=1.1.0`（已在 `backend/.venv` 安装 1.2.0） |
| `backend/tests/test_file_parser.py` | **新增**。10 个单测：`.txt` / `.md` / gbk 回退 / `.docx`(含表格) / 不支持格式 / 空文件 / 超限 / None / UploadFile 读路径 |
| `frontend/src/services/api.ts` | `generateV2` options 增加 `file?: File | null`，有值时 `formData.append('file', file)` |
| `frontend/src/components/test-case/TestCaseGenerator.tsx` | 需求区新增上传按钮 + 已选文件名 chip（可移除）+ 提示；生成按钮改为「有文件或有文本」即可点；`handleDeepGenerate` 传 `file`；pipeline 完成后清空选择 |
| `frontend/src/components/test-case/TestCaseGenerator.css` | 上传行 / 文件名 chip / 移除按钮 / 提示 样式 |

## 验证结果

- 后端单测：`backend/.venv/Scripts/python.exe -m pytest tests/test_file_parser.py` → **10 passed**
- 前端类型检查：`tsc --noEmit` → 退出码 0、零错误
- 注：该仓库前端无 ESLint 配置文件，`npm run lint` 在当前环境无法运行，故以 `tsc --noEmit` 代替类型/规范校验

## 使用方式

1. 在「生成测试用例」面板点击 **上传需求文件**，选择 `.docx` / `.txt` / `.md`
2. （可选）在需求文本框补充额外说明
3. 点击 **深度生成（4 阶段）** 即可基于文件内容生成测试用例

## 备注

- 旧单次生成接口 `POST /{session_id}/testcases` 未接入文件上传（按你的选择仅改「深度生成」）
- 该旧接口前端的图上传 `file` 字段后端仍无对应参数（历史空操作，不影响本次功能）

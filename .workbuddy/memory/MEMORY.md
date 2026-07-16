# 项目长期记忆（MEMORY.md）

## 飞书文档嵌入表格提取（关键约定）

- **必须靠 docx blocks REST API 发现嵌入表格 token，绝不能依赖 `rawContent` 正文正则。**
  `docx_v1_document_rawContent` 是纯文本，嵌入的 sheet/bitable 块在其中不出现、也不留 token 引用（实测仅留空行）。
- 发现 token 的正确做法：用 `tenant_access_token`（应用身份）直连
  `GET /open-apis/docx/v1/documents/{document_id}/blocks`，遍历 `data.items`，
  按 `block_type` 提取：`30`(sheet)→`block.sheet.token`（已是 `spreadsheetToken_sheetId`）；
  `14`(bitable)→`block.bitable.token`+`table_id`（拼成 `appToken_tableId`）。
  `document_id` 来自 wiki 节点的 `obj_token`（docx 真实 ID，非 wiki 节点 token）。
- 读取单元格：`GET /open-apis/sheets/v2/spreadsheets/{spreadsheetToken}/values/{sheetId}!A1:Z300`，
  Bearer `tenant_access_token`。**勿用** `sheets/v3/.../sheets` 枚举子表（对该 token 返回 404）。
- `lark-mcp` 不暴露 sheets / blocks 读取工具，表格/块发现必须走飞书开放平台 REST API 直连。
- 项目落地：`backend/utils/document_prefetch.py` 的 `fetch_feishu_document` 已实现上述完整链路；
  凭据来自 `config.feishu_app_id/feishu_app_secret` 或 MCP 配置 `-a/-s`；权限范围需
  `sheets:spreadsheet:read` + `bitable:app:read`。
- 用户确认：《方案3-安全AI相关》里的嵌入表格是**电子表格(sheet)**，非多维表格；token 前缀 `NLWU` 不代表多维表格。

## 项目文档与功能边界

- **`AGENTS.md` 只是开发指导文档，不参与运行、不影响任何项目功能**；项目真实功能只活在代码里。
- **项目功能绝不能依赖 `AGENTS.md`**：是否把约定同步进它是"文档准确性"问题，与功能生效无关。改了代码 + 测过 = 功能生效，与 `AGENTS.md` 写不写无关。

## 用户偏好

- 提取飞书/外部文档时：只要**原文**，不要分析、改写或重组结构；忠实还原标题层级、列表嵌套及图片/表格位置（表格/图片用极简占位即可，可保留 token 便于复取）。
- 沟通偏好：直接、给可执行的修复方案与根因，不要冗长解释。

## 生成测试用例：需求文件上传能力

- 支持在「深度生成（4阶段）」入口上传需求文件：`.docx`（python-docx 抽段落+表格，并抽取 Word 中**嵌入的 Excel 对象**）、`.pdf`（pdfplumber 抽每页文本 + 表格重建为 markdown）、`.txt`、`.md`（均当文本）。
- 后端：文件在 `backend/app/routes/pipeline.py` 的 `generate_v2` 经 `file: UploadFile` 接收，交给 `backend/utils/file_parser.py::parse_uploaded_file` 解析；**文件内容作为生成主体，文本框文字作为「补充背景」拼接**（合并规则见该路由）。单文件上限 10MB。
- 前端：按钮在 `frontend/src/components/test-case/TestCaseGenerator.tsx` 需求区，`file` 经 `api.ts` 的 `generateV2({file})` 以 `multipart/form-data` 上传；文件选择 `accept=".docx,.pdf,.txt,.md"`。
- 依赖：`python-docx`（已在 `backend/.venv` 安装，并在 `requirements.txt` 声明）、`pdfplumber>=0.11.0`（同在 venv 与 requirements.txt）。**注意**：后端实际运行在系统 Python `F:\env\Python313`（非 venv），该环境已自带 `pdfplumber 0.11.9`、`python-docx`、`fastapi`、`uvicorn`；venv 仅作为备选/构建环境。
- PDF 限制：仅支持「文本型」PDF。扫描件/纯图片型 PDF，以及**用矢量图形/图片绘制的表格（无文字层）**，pdfplumber 抽不到文本，会触发「解析后内容为空」报错；此类需 OCR（未实现，不在范围）。`F:/Download/Demo.pdf` 即为此类（整页仅 17 个文字字形，表格是矢量绘制），纯文本抽取无解，用户已知悉且不要求 OCR。

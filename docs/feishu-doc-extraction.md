# 飞书文档提取方法（含嵌入的 sheet / 多维表格）

本文档记录如何从飞书文档（wiki / docx）中提取**完整内容**，尤其是文档中嵌入的
**电子表格（sheet）和多维表格（bitable）**——这两类嵌入块在默认文档正文接口里
只会被当成占位引用，必须额外读取才能拿到单元格内容。

---

## 1. 总览：飞书文档由三层内容组成

| 层 | 内容 | 提取方式 |
|----|------|----------|
| 正文 | 标题、段落、列表、图片引用等 | MCP 工具 `docx_v1_document_rawContent` |
| 图片 | 文档内嵌图片 | MCP 工具 `docx_v1_documentBlock_list` 取 `image.token`，再 `drive_v1_media_batchGetTmpDownloadUrl` / 直连 `/drive/v1/medias/{token}/download`（需文档下载权限，见 [feishu-image-extraction.md](./feishu-image-extraction.md)） |
| 嵌入表格 | 电子表格 / 多维表格 | **直连飞书 Sheets / Bitable API**（见下） |

> ⚠️ 关键：文档正文接口（`docx_v1_document_rawContent`）返回的是**纯文本**，
> 嵌入的 sheet / 多维表格块在其中**根本不出现、也不留任何 token 引用**（实测该接口
> 对嵌入表格只留下空行）。因此**不能靠正文正则抽取 token**——必须先通过 docx
> blocks REST API 拿到嵌入块，再从中取出子表 token。这也是最初功能「读不到表格」的根因。

---

## 2. 读取嵌入表格的正确方式

### 2.1 为什么不能走 MCP（lark-mcp）

项目用的飞书 MCP（`@larksuiteoapi/lark-mcp`，即 `lark-mcp`）**只暴露以下工具集**：
`im`、`base`（多维表格）、`doc`（docx + wiki + drive）、`task`、`calendar`。
**它没有任何 `sheets`（电子表格）读取工具**，也没有能把电子表格内容读出来的接口。

因此：通过 MCP 只能拿到正文，**表格必须用飞书开放平台 REST API 直连读取**。

### 2.2 发现 token：docx blocks REST API（必须）

`rawContent` 不含表格引用，所以要先列出文档块，从嵌入块里取子表 token。
用应用身份 `tenant_access_token` 直连：

```
GET https://open.feishu.cn/open-apis/docx/v1/documents/{document_id}/blocks?page_size=500
Authorization: Bearer {tenant_access_token}
```

遍历 `data.items`，按 `block_type` 提取：

| block_type | 含义 | token 字段 |
|------------|------|-----------|
| `30` | 嵌入电子表格（iSheet） | `block["sheet"]["token"]`（已是 `spreadsheetToken_sheetId` 形态） |
| `14` | 嵌入多维表格（iBitable） | `block["bitable"]["token"]` + `block["bitable"]["table_id"]` → 拼成 `appToken_tableId` |

> `document_id` 来自 wiki 节点的 `obj_token`（即 docx 真实的 document_id，
> 不是 wiki 节点 token）。一次请求默认最多返回若干块，文档较大时按 `page_token` 翻页。

### 2.3 token 形态

文档里的嵌入表格块 token 形如：

```
NLWUsvjrfhWUgVtIXD9cpm8jnQh_5iKWZs
\___________ ______________/ \___/
       |                    |
  spreadsheetToken       sheetId（子表 ID）
```

- 下划线前 = 表格（spreadsheet / bitable）token
- 下划线后 = 该表格内的某个子表（sheet / table）ID

> 注意：不要被 token 前缀 `NLWU` 误导成「它一定是多维表格」。在本项目中它确实是
> **电子表格（spreadsheet）**，权限范围应申请 `sheets:spreadsheet:read`，用**应用身份**
> （tenant_access_token）读取。请以文档作者/使用者确认为准，不要仅凭前缀臆断。

### 2.4 应用身份（tenant_access_token）读取步骤

1. **换取 tenant_access_token**（应用身份，无需用户 OAuth 授权）：

   ```
   POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal
   Content-Type: application/json
   { "app_id": "<APP_ID>", "app_secret": "<APP_SECRET>" }
   ```
   响应 `code==0` 时取 `tenant_access_token`。

2. **读取单元格**（Sheets API v2）：

   ```
   GET https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{spreadsheetToken}/values/{sheetId}!A1:Z300
   Authorization: Bearer {tenant_access_token}
   ```
   响应里取 `data.valueRange.values`（二维数组，每行是单元格列表）。

3. **坑位提醒**：
   - `GET /open-apis/sheets/v3/spreadsheets/{token}/sheets` 对这类
     `spreadsheetToken_sheetId` 形态的 token **返回 404**（无法用它枚举子表），
     必须直接用上面第 2 步的 `sheetId` 按范围读取。
   - 多维表格（bitable）则用 `bitable_v1_appTableRecord_search`，权限范围
     `bitable:app:read`；但本项目当前需求是电子表格，走 Sheets API 即可。

### 2.5 转成 Markdown 表格

把 `values` 二维数组直接渲染成 Markdown 表格，并：
- 去掉整行全空的行；
- 裁掉每行尾部的空单元格（电子表格常有多余空列）；
- 第一行下方加 `| --- | --- | ... |` 分隔行。

---

## 3. 在项目代码中的落地

实现位于 `backend/utils/document_prefetch.py`：

- `fetch_feishu_document(text, mcp_configs)`：先经 MCP 取正文，再调用
  `_discover_embedded_sheet_tokens()` 通过 docx blocks REST API 发现嵌入表格 token
  （并兜底从正文正则抽），最后用 `_collect_sheet_markdown()` 逐个读取并追加到正文之后。
- `_discover_embedded_sheet_tokens(document_id, app_id, app_secret)`：
  直连 `docx/v1/documents/{id}/blocks`，按 block_type 30/14 提取 token。
- `fetch_feishu_sheet(spreadsheet_token, sheet_id, app_id, app_secret)`：
  封装上面的 tenant_access_token + Sheets API 读取。
- `_sheet_rows_to_markdown(rows)`：二维数组 → Markdown 表格（含尾部空列裁剪）。
- `FEISHU_SHEET_TOKEN_RE`：兜底从正文里启发式抽取 `spreadsheetToken_sheetId` 形态 token。
- `_resolve_feishu_credentials(mcp_configs)`：
  优先读 `config.feishu_app_id / feishu_app_secret`，
  其次从 Feishu MCP（stdio `lark-mcp`）配置的 `-a`/`-s` 参数中提取。

所有新逻辑都是 **additive 且异常安全**：缺凭据 / token 无效 / API 失败都会静默跳过，
不影响原有的正文预取行为。

### 3.1 启用所需配置

在 `backend/data/config.json` 中配置飞书应用凭据：

```json
{
  "feishu_app_id": "<APP_ID>",
  "feishu_app_secret": "<APP_SECRET>"
}
```

并在飞书开放平台给该应用开通权限范围：
- `sheets:spreadsheet:read`（电子表格读取）
- `bitable:app:read`（多维表格读取，如文档含多维表格）

### 3.2 测试

`backend/tests/test_document_prefetch.py` 覆盖了 token 拆分、Markdown 转换、
token 正则、`_collect_sheet_markdown` 合并去重、`_discover_embedded_sheet_tokens`
从 blocks 解析等纯函数与桩测试。整条链路（blocks REST API 发现 token + Sheets API 读表）
已用真实文档端到端验证通过：对《方案3-安全AI相关》成功取出两个嵌入表格。

---

## 4. 一句话流程（给人看的「提示词」版）

> 读取飞书文档时，分三步：① 用 MCP 的 `docx_v1_document_rawContent` 取正文；
> ② **用应用身份 `tenant_access_token` 直连 `GET /docx/v1/documents/{id}/blocks`**，
> 遍历块，从 `block_type=30(sheet)` 的 `block.sheet.token` 或 `block_type=14(bitable)`
> 的 `block.bitable.token + table_id` 取出嵌入表格 token（注意：`rawContent` 纯文本
> **不含**这些引用，不能靠正文正则）；
> ③ 用 `tenant_access_token` 直连飞书 Sheets API
> `GET /sheets/v2/spreadsheets/{表格token}/values/{子表token}!A1:Z300` 读取单元格，
> 转成 Markdown 表格追加到正文。注意 lark-mcp 没有 sheets 工具，表格必须走 API 直连，
> 且不要试图用 v3 的 `/sheets` 枚举子表（对该 token 会 404）。

---
name: feishu-doc
description: >
  Feishu (飞书) document read-only tool. Activate when user mentions Feishu docs,
  cloud docs, docx links, or needs to read documents. Fetches full document
  content in one request. No block-level or write operations.
license: MIT
allowed-tools: feishu_doc
---

# Feishu Document Tool (Read-Only, Full Content)

When the `feishu_doc` tool is available, call it with the `action` parameter and relevant keyword arguments.

## Tool Signature

```
feishu_doc(action: str, **kwargs) -> str
```

## Token Extraction

From URL `https://xxx.feishu.cn/docx/ABC123def` → `doc_token` = `ABC123def`

## Actions

### Read Document (only action)

```
feishu_doc(action="read", doc_token="ABC123def")
```

Fetches the full document content in one request. Returns: document title and plain text content.

> Only `read` is supported. No block-level or write operations.

## Configuration

The tool requires Feishu App credentials configured in the application:
- `feishu_app_id` — Feishu App ID
- `feishu_app_secret` — Feishu App Secret

Required Feishu app permissions:
- `docx:document`
- `docx:document:readonly`
- `docx:document.block:convert`
- `drive:drive`

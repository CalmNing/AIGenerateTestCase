"""端到端验证 fetch_feishu_document：桩 MCP 只替换正文拉取（用真实正文，
其中不含 token），其余全部走真实链路（tenant token -> blocks REST API 发现
token -> Sheets API 读表）。验证修复后功能对真实文档能取出嵌入表格。"""
import asyncio
import json
import sys

sys.path.insert(0, "F:/VSCode/ai-generate-testcase/backend")

import utils.document_prefetch as dp
import utils.lanhu_mcp_adapter as adapter

CLI_ID = "cli_aa997e12a3b81cc5"
CLI_SEC = "PokwTOV3ZsykLev86D52mhxpMTY56IjZ"
WIKI_URL = "https://p0ysjqezntx.feishu.cn/wiki/UtezwlPXyin5BekqsjxcccKjnIh"

# 真实正文（来自 docx_v1_document_rawContent，已确认不含嵌入表格 token）
REAL_BODY = (
    "方案3-安全AI相关\n一、用户故事与范围限定\n1.1 问题背景\n问题陈述\n"
    "随着工程数字工地的普及，视频AI能力已成为现场安全管控的核心手段。\n"
    "使用量级与频率预估\n\n1.3 价值点分析\n二、功能模块拆解\n\n"
    "三、功能模块详细设计\n"
)

NODE_RESP = json.dumps({"node": {"obj_token": "UNKAdB6EioKM0ExXjDJcJpLEn5c"}})
RAW_RESP = json.dumps({"content": REAL_BODY})


class FakeMcpClient:
    tools = [
        {"name": "wiki_v2_space_getNode"},
        {"name": "docx_v1_document_rawContent"},
    ]

    async def call_tool(self, name, arguments):
        if name == "wiki_v2_space_getNode":
            return NODE_RESP
        if name == "docx_v1_document_rawContent":
            return RAW_RESP
        return json.dumps({"error": "unknown"})

    async def disconnect(self):
        pass


async def fake_connect_single_server(config):
    return config.get("name", "feishu"), FakeMcpClient(), None


async def main():
    adapter.connect_single_server = fake_connect_single_server
    mcp_configs = [{
        "name": "feishu",
        "type": "http",
        "url": "http://localhost:9999/mcp",  # 不会被真正连接（已桩）
        "enabled": True,
        "args": ["-a", CLI_ID, "-s", CLI_SEC],
    }]
    result = await dp.fetch_feishu_document(WIKI_URL, mcp_configs)
    assert result, "fetch_feishu_document 返回空 -> 失败"
    print("=== 是否含正文关键句 ===")
    print("使用量级与频率预估:", "使用量级与频率预估" in result)
    print("=== 嵌入表格格式校验（不应出现 token 标题行）===")
    assert "### 嵌入表格" not in result, "输出不应含 '### 嵌入表格 <token>' 标题行"
    assert "NLWUsvjrfhWUgVtIXD9cpm8jnQh_5iKWZs" not in result, "输出不应含表格 token"
    assert "NLWUsvjrfhWUgVtIXD9cpm8jnQh_ScT4oV" not in result, "输出不应含表格 token"
    print("OK: 输出中不含 token 标题与 token 字符串")
    # 但应保留表格内容本身
    assert "维度" in result and "预估" in result, "表1 内容应保留"
    assert "视频AI" in result, "表2 内容应保留"
    print("OK: 两个表格的正文内容均保留")
    print("\n=== 文档尾部（正文 + 表格合并后）===")
    print(result[-800:])


asyncio.run(main())

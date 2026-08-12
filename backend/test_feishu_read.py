"""测试飞书文档读取 - 复制生成测试用例中的文档预取逻辑，注入打印"""
import asyncio
import sys
import os
import logging

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")


async def main():
    from utils.document_prefetch import fetch_feishu_document
    from sqlmodel import select
    from sqlmodel.orm.session import Session
    from db.db import engine
    from db.models import McpServer

    # 加载 MCP 配置
    _EXCLUDE = {"id", "created_at", "updated_at", "user_id"}
    with Session(engine) as session:
        servers = session.exec(
            select(McpServer).where(McpServer.enabled == True)
        ).all()
        mcp_configs = [s.model_dump(exclude=_EXCLUDE) for s in servers]

    print(f"MCP 配置: {len(mcp_configs)} 个")
    for c in mcp_configs:
        print(f"  - {c.get('name')} ({c.get('type')}): {len(c.get('enabled_tools') or [])} tools")

    feishu_url = "https://p0ysjqezntx.feishu.cn/wiki/DU5twmPoviDDEikPnuGcuVVxnlb"
    print(f"\n测试 URL: {feishu_url}")
    print("=" * 60)

    # 调用文档读取
    content = await fetch_feishu_document(feishu_url, mcp_configs)

    if not content:
        print("获取失败，返回 None")
        return

    print(f"总长度: {len(content)} 字")

    # 分析内容结构
    board_marker = "## 画板内容"
    if board_marker in content:
        idx = content.index(board_marker)
        print(f"正文: {idx} 字")
        print(f"画板: {len(content) - idx} 字")
    else:
        print("未检测到画板内容标记")

    # 打印前 2000 字
    print("\n" + "=" * 60)
    print("内容预览（前 2000 字）")
    print("=" * 60)
    print(content[:2000])

    if len(content) > 2000:
        print(f"\n... (共 {len(content)} 字，省略 {len(content) - 2000} 字)")

    print("\n完成")


if __name__ == "__main__":
    asyncio.run(main())

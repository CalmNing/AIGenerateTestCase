"""飞书画板内容解析器

将飞书 whiteboard API 返回的节点数据解析为结构化 Markdown 文本。
支持画板类型：思维导图、流程图/形状、便签、连接线等。

飞书画板节点类型（block_type 或 node.type）：
- mind_map: 思维导图节点（最常见）
- composite_shape: 复合形状（椭圆、矩形等，常用于流程图）
- text_shape: 文本形状
- sticky_note: 便签
- connector: 连接线（通常忽略，用于推断关系）
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def parse_board_nodes(nodes: list[dict]) -> str:
    """主入口：将画板节点数据解析为结构化 Markdown。

    根据节点类型自动选择解析策略：
    - 思维导图 → 缩进树形文本
    - 流程图/形状 → 按位置排序的文本
    - 便签 → 附注文本
    """
    if not nodes:
        return ""

    # 统计节点类型
    type_counts: dict[str, int] = {}
    for node in nodes:
        t = node.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    logger.info("画板节点统计: %s", type_counts)

    parts: list[str] = []

    # 1. 思维导图（优先，信息密度最高）
    mind_map_nodes = [n for n in nodes if n.get("type") == "mind_map"]
    if mind_map_nodes:
        md = _parse_mind_map(mind_map_nodes)
        if md.strip():
            parts.append(md)

    # 2. 复合形状（流程图中的椭圆、矩形等）
    shape_nodes = [n for n in nodes if n.get("type") in ("composite_shape", "text_shape")]
    if shape_nodes and not mind_map_nodes:  # 有思维导图时跳过形状（通常思维导图已包含所有信息）
        md = _parse_shapes(shape_nodes)
        if md.strip():
            parts.append(md)

    # 3. 便签
    sticky_nodes = [n for n in nodes if n.get("type") == "sticky_note"]
    if sticky_nodes:
        md = _parse_sticky_notes(sticky_nodes)
        if md.strip():
            parts.append(md)

    return "\n\n".join(parts)


def _parse_mind_map(nodes: list[dict]) -> str:
    """将思维导图节点解析为缩进树形文本。

    思维导图节点结构：
    - node.id: 节点 ID
    - node.mind_map_node.parent_id: 父节点 ID
    - node.mind_map_node.children: 子节点 ID 列表（可能不存在）
    - node.mind_map_root.right_children: 根节点的子节点列表
    - node.text.text: 文本内容
    """
    # 构建节点映射
    node_map: dict[str, dict] = {}
    for node in nodes:
        nid = node.get("id")
        if not nid:
            continue
        mm_node = node.get("mind_map_node", {})
        mm_root = node.get("mind_map_root", {})
        text_obj = node.get("text", {})
        text_content = text_obj.get("text", "") if isinstance(text_obj, dict) else ""

        # 也尝试从 rich_text 提取
        if not text_content and isinstance(text_obj, dict):
            rich_text = text_obj.get("rich_text", {})
            paragraphs = rich_text.get("paragraphs", [])
            texts = []
            for para in paragraphs:
                for elem in para.get("elements", []):
                    text_element = elem.get("text_element", {})
                    if text_element.get("text"):
                        texts.append(text_element["text"])
            text_content = "".join(texts)

        parent_id = mm_node.get("parent_id", "") if isinstance(mm_node, dict) else ""
        children = mm_node.get("children", []) if isinstance(mm_node, dict) else []

        # 根节点的子节点在 mind_map_root.right_children 中
        if mm_root and isinstance(mm_root, dict):
            right_children = mm_root.get("right_children", [])
            if right_children:
                children = right_children
            parent_id = ""

        node_map[nid] = {
            "id": nid,
            "parent_id": parent_id,
            "children": children,
            "text": text_content,
            "is_root": bool(mm_root),
        }

    if not node_map:
        return ""

    # 第一步：通过 children 字段建立父子关系
    # 如果一个节点的 children 列表中有某个 ID，就把那个 ID 的 parent_id 设为当前节点
    for nid, node in node_map.items():
        for child_id in node["children"]:
            if child_id in node_map:
                node_map[child_id]["parent_id"] = nid

    # 找到根节点（parent_id 为空或不在 node_map 中）
    roots = []
    for nid, node in node_map.items():
        pid = node["parent_id"]
        if not pid or pid not in node_map:
            roots.append(nid)

    # 如果没有找到根节点，尝试从 mind_map_root 标记查找
    if not roots:
        roots = [nid for nid, n in node_map.items() if n.get("is_root")]

    # 如果仍然没有，选第一个节点
    if not roots and node_map:
        roots = [next(iter(node_map))]

    # 递归构建树形文本
    lines: list[str] = []
    visited: set[str] = set()

    def _walk(nid: str, depth: int):
        if nid in visited or nid not in node_map:
            return
        visited.add(nid)
        node = node_map[nid]
        text = node["text"].strip()
        if not text:
            text = "(空)"
        indent = "  " * depth
        lines.append(f"{indent}{text}")
        for child_id in node["children"]:
            _walk(child_id, depth + 1)

    for root_id in roots:
        _walk(root_id, 0)

    # 处理未访问的节点（可能是孤立节点）
    for nid, node in node_map.items():
        if nid not in visited and node["text"].strip():
            lines.append(f"(孤立) {node['text'].strip()}")

    return "\n".join(lines)


def _parse_shapes(nodes: list[dict]) -> str:
    """将形状节点（流程图等）解析为按位置排序的文本。

    形状节点按 y 坐标排序（从上到下），同行按 x 坐标排序（从左到右）。
    """
    items: list[tuple[float, float, str]] = []

    for node in nodes:
        x = node.get("x", 0)
        y = node.get("y", 0)
        text = _extract_shape_text(node)
        if text.strip():
            items.append((y, x, text.strip()))

    if not items:
        return ""

    # 按 y 排序，再按 x 排序
    items.sort(key=lambda t: (t[0], t[1]))

    lines: list[str] = []
    last_y = None
    for y, _x, text in items:
        # 如果 y 坐标差距较大，添加空行分隔
        if last_y is not None and abs(y - last_y) > 100:
            lines.append("")
        lines.append(text)
        last_y = y

    return "\n".join(lines)


def _extract_shape_text(node: dict) -> str:
    """从形状节点中提取文本内容。"""
    # 尝试 text.text
    text_obj = node.get("text", {})
    if isinstance(text_obj, dict):
        text = text_obj.get("text", "")
        if text:
            return text

    # 尝试 composite_shape 的文本
    cs = node.get("composite_shape", {})
    if isinstance(cs, dict):
        text = cs.get("text", "")
        if text:
            return text

    return ""


def _parse_sticky_notes(nodes: list[dict]) -> str:
    """将便签节点解析为附注文本。"""
    lines: list[str] = []
    for node in nodes:
        text = _extract_shape_text(node)
        if text.strip():
            lines.append(f"> {text.strip()}")
    return "\n".join(lines)

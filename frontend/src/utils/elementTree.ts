import { PageElement, PageLocator } from '../types';

/** 树节点拍平为带 path 的列表(DFS,保留 children 结构引用不修改原节点) */
export interface FlatElement {
  element: PageElement;
  /** 祖先 key 链(根→叶,不含自身) */
  path: string[];
}

export const flattenElementTree = (nodes: PageElement[] | undefined, path: string[] = []): FlatElement[] => {
  const out: FlatElement[] = [];
  for (const node of nodes || []) {
    if (!node || typeof node !== 'object') continue;
    out.push({ element: node, path });
    if (node.children?.length) {
      out.push(...flattenElementTree(node.children, [...path, node.key]));
    }
  }
  return out;
};

/** key → 祖先 key 链(执行/校验分组用) */
export const pathByKey = (nodes: PageElement[] | undefined): Record<string, string[]> => {
  const map: Record<string, string[]> = {};
  for (const { element, path } of flattenElementTree(nodes)) {
    map[element.key] = path;
  }
  return map;
};

/** 按 key 深度更新节点(patch 应用到匹配节点) */
export const updateByKey = (
  nodes: PageElement[],
  key: string,
  patch: Partial<PageElement>
): PageElement[] =>
  nodes.map((node) => {
    if (node.key === key) return { ...node, ...patch };
    if (node.children?.length) {
      return { ...node, children: updateByKey(node.children, key, patch) };
    }
    return node;
  });

/** 按 key 深度删除节点(连同子树) */
export const removeByKey = (nodes: PageElement[], key: string): PageElement[] =>
  nodes
    .filter((node) => node.key !== key)
    .map((node) =>
      node.children?.length ? { ...node, children: removeByKey(node.children, key) } : node
    );

/** 按 key 深度更新某条定位器 */
export const updateLocatorByKey = (
  nodes: PageElement[],
  elementKey: string,
  locatorIndex: number,
  patch: Partial<PageLocator>
): PageElement[] =>
  nodes.map((node) => {
    if (node.key === elementKey) {
      const locators = (node.locators ?? []).map((loc, j) =>
        j === locatorIndex ? { ...loc, ...patch } : loc
      );
      return { ...node, locators };
    }
    if (node.children?.length) {
      return {
        ...node,
        children: updateLocatorByKey(node.children, elementKey, locatorIndex, patch),
      };
    }
    return node;
  });

/** 按 key 追加定位器 */
export const addLocatorByKey = (nodes: PageElement[], elementKey: string): PageElement[] =>
  nodes.map((node) => {
    if (node.key === elementKey) {
      return {
        ...node,
        locators: [...(node.locators ?? []), { strategy: 'css', value: '', stability: 50 }],
      };
    }
    if (node.children?.length) {
      return { ...node, children: addLocatorByKey(node.children, elementKey) };
    }
    return node;
  });

/** 按 key 删除第 index 条定位器 */
export const removeLocatorByKey = (
  nodes: PageElement[],
  elementKey: string,
  locatorIndex: number
): PageElement[] =>
  nodes.map((node) => {
    if (node.key === elementKey) {
      return { ...node, locators: (node.locators ?? []).filter((_, j) => j !== locatorIndex) };
    }
    if (node.children?.length) {
      return { ...node, children: removeLocatorByKey(node.children, elementKey, locatorIndex) };
    }
    return node;
  });

/** 生成不与已有 key 冲突的 element_N */
export const nextElementKey = (nodes: PageElement[]): string => {
  const used = new Set(flattenElementTree(nodes).map(({ element }) => element.key));
  let index = nodes.length + 1;
  while (used.has(`element_${index}`)) index += 1;
  return `element_${index}`;
};

/** 在 parentKey 节点下插入子元素(放在 children 最前;找不到父级则原样返回) */
export const addChildByKey = (
  nodes: PageElement[],
  parentKey: string,
  child: PageElement
): PageElement[] =>
  nodes.map((node) => {
    if (node.key === parentKey) {
      return { ...node, children: [child, ...(node.children ?? [])] };
    }
    if (node.children?.length) {
      return { ...node, children: addChildByKey(node.children, parentKey, child) };
    }
    return node;
  });

/** 按 probeTrigger 将**根节点**分组(旧扁平数据仍可能只有 probeTrigger 无 children) */
export const groupRootElements = (
  items: PageElement[],
  baseLabel: string
): Array<{ group: string; items: PageElement[] }> => {
  const groups = new Map<string, PageElement[]>();
  items.forEach((el) => {
    const g = el.probeTrigger?.trim() || baseLabel;
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g)!.push(el);
  });
  return [...groups.entries()].map(([group, list]) => ({ group, items: list }));
};

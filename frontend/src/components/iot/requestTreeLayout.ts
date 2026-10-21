import type { SavedRequest, SavedRequestFolder, SavedRequestLayoutGroup } from '../../types';

/**
 * 侧栏树的布局计算（纯函数，便于单测）。
 *
 * 树的每个容器（某个文件夹内部，或根层）渲染时先列请求、再列子文件夹，
 * 所以顺序天然分成两条互不干扰的列表：`requestIds` 与 `folderIds`。
 * 拖放只改这两条列表与节点的归属，最终整组顺序提交给后端写回 sort_order。
 */

export type DragEntryType = 'folder' | 'request';
export type DropPosition = 'before' | 'after' | 'inside';

/** 树里的一个容器：某个文件夹内部，或根层（parentId 为 null） */
export interface TreeContainer {
  key: string;
  /** 后端 parent_id / folder_id；根层为 null */
  parentId: number | null;
  folderIds: number[];
  requestIds: number[];
}

export const ROOT_CONTAINER_KEY = 'root';

export const containerKeyOfFolder = (parentId?: number | null): string =>
  parentId === null || parentId === undefined ? ROOT_CONTAINER_KEY : `folder-${parentId}`;

export const folderEntryKey = (id: number): string => `folder-${id}`;
export const requestEntryKey = (id: number): string => `request-${id}`;

export const parseEntryKey = (key: string): { type: DragEntryType; id: number } | null => {
  const separator = key.indexOf('-');
  if (separator <= 0) return null;
  const type = key.slice(0, separator) === 'folder' ? 'folder' : 'request';
  const id = Number(key.slice(separator + 1));
  return Number.isFinite(id) ? { type, id } : null;
};

/** 把后端返回的（可能嵌套的）文件夹树摊平，兼容扁平 parent_id 形态 */
export const flattenFolders = (folders: SavedRequestFolder[]): SavedRequestFolder[] => {
  const out: SavedRequestFolder[] = [];
  const walk = (list: SavedRequestFolder[], inheritedParent: number | null) => {
    list.forEach((folder) => {
      const parentId = folder.parent_id ?? inheritedParent;
      out.push({ ...folder, parent_id: parentId });
      if (folder.children && folder.children.length > 0) walk(folder.children, folder.id);
    });
  };
  walk(folders, null);
  return out;
};

/** 依据后端返回的顺序（已按 sort_order 排序）构建容器表 */
export const buildContainers = (
  folders: SavedRequestFolder[],
  requests: SavedRequest[],
): Map<string, TreeContainer> => {
  const containers = new Map<string, TreeContainer>();
  const ensure = (key: string, parentId: number | null): TreeContainer => {
    const existing = containers.get(key);
    if (existing) return existing;
    const created: TreeContainer = { key, parentId, folderIds: [], requestIds: [] };
    containers.set(key, created);
    return created;
  };

  const flat = flattenFolders(folders);
  // 每个文件夹（包括空的）都要有容器：空文件夹同样是合法的拖放落点
  flat.forEach((folder) => ensure(`folder-${folder.id}`, folder.id));
  flat.forEach((folder) => {
    ensure(containerKeyOfFolder(folder.parent_id), folder.parent_id ?? null).folderIds.push(folder.id);
  });

  requests.forEach((request) => {
    ensure(containerKeyOfFolder(request.folder_id), request.folder_id ?? null).requestIds.push(request.id);
  });

  ensure(ROOT_CONTAINER_KEY, null);
  return containers;
};

/** nodeId 是否位于 ancestorId 的子树内（禁止把文件夹拖进自己的子孙） */
export const isDescendantFolder = (
  folders: SavedRequestFolder[],
  nodeId: number | null | undefined,
  ancestorId: number,
): boolean => {
  if (nodeId === null || nodeId === undefined) return false;
  const byId = new Map<number, SavedRequestFolder>();
  flattenFolders(folders).forEach((folder) => byId.set(folder.id, folder));
  const seen = new Set<number>();
  let current: number | null | undefined = nodeId;
  while (current !== null && current !== undefined && !seen.has(current)) {
    if (current === ancestorId) return true;
    seen.add(current);
    current = byId.get(current)?.parent_id ?? null;
  }
  return false;
};

const locateEntry = (
  containers: Map<string, TreeContainer>,
  key: string,
): { container: TreeContainer; type: DragEntryType; id: number; index: number } | null => {
  const parsed = parseEntryKey(key);
  if (!parsed) return null;
  for (const container of containers.values()) {
    const list = parsed.type === 'folder' ? container.folderIds : container.requestIds;
    const index = list.indexOf(parsed.id);
    if (index >= 0) return { container, type: parsed.type, id: parsed.id, index };
  }
  return null;
};

const cloneContainers = (containers: Map<string, TreeContainer>): Map<string, TreeContainer> => {
  const snapshot = new Map<string, TreeContainer>();
  containers.forEach((container, key) => {
    snapshot.set(key, {
      key,
      parentId: container.parentId,
      folderIds: [...container.folderIds],
      requestIds: [...container.requestIds],
    });
  });
  return snapshot;
};

const toGroup = (container: TreeContainer): SavedRequestLayoutGroup => ({
  parent_id: container.parentId,
  folder_ids: [...container.folderIds],
  request_ids: [...container.requestIds],
});

/**
 * 计算一次拖放后的完整布局。
 *
 * - `position` 为 before / after 表示放到目标行的前 / 后，inside 表示放进文件夹内部；
 * - 跨类型拖放（请求拖到文件夹行）按目标容器内该类型的末尾处理，与渲染顺序一致；
 * - 非法操作（拖到自己身上、把文件夹拖进自己的子树）返回 null。
 */
export const computeLayoutGroups = (
  containers: Map<string, TreeContainer>,
  folders: SavedRequestFolder[],
  draggedKey: string,
  targetKey: string,
  position: DropPosition,
): SavedRequestLayoutGroup[] | null => {
  if (draggedKey === targetKey) return null;

  const dragged = locateEntry(containers, draggedKey);
  if (!dragged) return null;

  let targetContainerKey: string;
  let insertIndex: number;

  if (position === 'inside') {
    // 「全部请求 / 未分类」两行代表根层，拖上去即移动到根层末尾
    if (targetKey === ROOT_CONTAINER_KEY) {
      const root = containers.get(ROOT_CONTAINER_KEY);
      if (!root) return null;
      targetContainerKey = ROOT_CONTAINER_KEY;
      insertIndex = (dragged.type === 'folder' ? root.folderIds : root.requestIds).length;
    } else {
      if (!targetKey.startsWith('folder-')) return null;
      const targetFolder = parseEntryKey(targetKey);
      if (!targetFolder) return null;
      if (dragged.type === 'folder' && isDescendantFolder(folders, targetFolder.id, dragged.id)) return null;
      targetContainerKey = folderEntryKey(targetFolder.id);
      const inside = containers.get(targetContainerKey);
      if (!inside) return null;
      insertIndex = (dragged.type === 'folder' ? inside.folderIds : inside.requestIds).length;
    }
  } else if (targetKey === ROOT_CONTAINER_KEY) {
    // 根层伪节点（「全部请求 / 未分类」）没有前后可言，一律落到根层首 / 尾
    const root = containers.get(ROOT_CONTAINER_KEY);
    if (!root) return null;
    targetContainerKey = ROOT_CONTAINER_KEY;
    insertIndex = position === 'before' ? 0 : (dragged.type === 'folder' ? root.folderIds : root.requestIds).length;
  } else {
    const target = locateEntry(containers, targetKey);
    if (!target) return null;
    if (dragged.type === 'folder' && isDescendantFolder(folders, target.container.parentId, dragged.id)) return null;
    targetContainerKey = target.container.key;
    if (target.type === dragged.type) {
      insertIndex = position === 'before' ? target.index : target.index + 1;
    } else {
      // 跨类型：文件夹永远排在请求前面，因此新的请求排在容器内请求列表的末尾
      const sameTypeList = dragged.type === 'folder' ? target.container.folderIds : target.container.requestIds;
      insertIndex = sameTypeList.length;
    }
  }

  const snapshot = cloneContainers(containers);
  const sourceContainer = snapshot.get(dragged.container.key);
  const targetContainer = snapshot.get(targetContainerKey);
  if (!sourceContainer || !targetContainer) return null;

  const sourceList = dragged.type === 'folder' ? sourceContainer.folderIds : sourceContainer.requestIds;
  const removedAt = sourceList.indexOf(dragged.id);
  if (removedAt < 0) return null;
  sourceList.splice(removedAt, 1);

  const targetList = dragged.type === 'folder' ? targetContainer.folderIds : targetContainer.requestIds;
  // 同一容器内往后拖时，先删除会让插入点前移一位
  if (sourceContainer.key === targetContainer.key && removedAt < insertIndex) insertIndex -= 1;
  targetList.splice(Math.max(0, Math.min(insertIndex, targetList.length)), 0, dragged.id);

  const groups: SavedRequestLayoutGroup[] = [toGroup(sourceContainer)];
  if (targetContainer.key !== sourceContainer.key) groups.push(toGroup(targetContainer));
  return groups;
};

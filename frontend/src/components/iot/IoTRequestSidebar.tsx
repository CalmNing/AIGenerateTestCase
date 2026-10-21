import React, { useState, useEffect, useMemo } from 'react';
import { Input, Button, Tooltip, Space, Dropdown } from 'antd';
import type { MenuProps } from 'antd';
import {
  PlusOutlined,
  SyncOutlined,
  HistoryOutlined,
  RightOutlined,
  LeftOutlined,
  FolderOutlined,
  FolderOpenOutlined,
  InboxOutlined,
  DownOutlined,
  DeleteOutlined,
  MoreOutlined,
  EditOutlined,
  SnippetsOutlined,
  SortAscendingOutlined,
  FolderAddOutlined,
} from '@ant-design/icons';
import { SavedRequest, SavedRequestFolder, SavedRequestLayoutGroup } from '../../types';
import {
  ROOT_CONTAINER_KEY,
  buildContainers,
  computeLayoutGroups,
  folderEntryKey,
  isDescendantFolder,
  requestEntryKey,
  type DropPosition,
} from './requestTreeLayout';
import './IoTRequestSidebar.css';

export interface IoTRequestSidebarProps {
  folders: SavedRequestFolder[];
  /** 全部已保存请求（后端已按 updated_at 排序），前端按 folder_id 分组成树 */
  requests: SavedRequest[];
  /** 刷新中 */
  loading: boolean;
  /** 当前标签页对应的已保存请求 → 行高亮 */
  activeSavedRequestId?: number;
  /** 当前选中的文件夹：undefined=全部，null=未分类，number=具体文件夹 */
  selectedFolderId?: number | null;
  /** 点击「全部请求」/「未分类」/具体文件夹时回调（父组件用它决定新建请求归属该文件夹） */
  onSelectFolder?: (folderId: number | null | undefined) => void;
  onOpenRequest: (request: SavedRequest) => void;
  onCopyRequest: (request: SavedRequest) => void;
  onDeleteRequest: (id: number) => void;
  onCreateFolder: (name: string, parentId: number | null) => Promise<boolean>;
  onRenameFolder: (id: number, name: string) => Promise<boolean>;
  onDeleteFolder: (id: number) => void;
  onRefresh: () => void;
  onOpenPushLog: () => void;
  /** 打开「新建」弹窗（请求类型 + 文件夹） */
  onOpenNewItem: () => void;
  /** 在指定文件夹内新建请求（Postman: Add request） */
  onAddRequest: (folderId: number | null) => void;
  /** 重命名已保存的请求（行内编辑） */
  onRenameRequest: (id: number, name: string) => Promise<boolean>;
  /** 复制文件夹（Postman: Duplicate） */
  onDuplicateFolder: (id: number) => void;
  /** 文件夹排序方向切换（Postman: Sort） */
  onSortFolder?: (id: number, direction: 'asc' | 'desc') => void;
  /** 拖拽后保存布局：分组为受影响父级的完整顺序，父组件负责提交后端并刷新 */
  onSaveLayout?: (groups: SavedRequestLayoutGroup[]) => Promise<boolean>;
}

const MIN_SIDEBAR_WIDTH = 200;
const MAX_SIDEBAR_WIDTH = 420;
const DEFAULT_SIDEBAR_WIDTH = 280;
const SIDEBAR_WIDTH_KEY = 'iot.sidebarWidth';
const SIDEBAR_COLLAPSED_KEY = 'iot.sidebarCollapsed';
const UNCATEGORIZED_ID = -1;
/** 「全部请求 / 未分类」两行都代表根层：布局 key 相同，高亮 key 各自独立 */
const ROOT_ALL_ROW_KEY = 'root-all';
const ROOT_UNCATEGORIZED_ROW_KEY = 'root-uncategorized';
const INDENT_BASE = 8;
const HTTP_METHOD_KEYS = ['get', 'post', 'put', 'delete', 'patch'];

interface FilteredFolder {
  folder: SavedRequestFolder;
  requests: SavedRequest[];
  children: FilteredFolder[];
}

const isFilteredFolder = (value: FilteredFolder | null): value is FilteredFolder => value !== null;

const readStoredSidebarWidth = (): number => {
  try {
    const raw = window.localStorage.getItem(SIDEBAR_WIDTH_KEY);
    const parsed = raw ? Number(raw) : NaN;
    if (!Number.isFinite(parsed)) return DEFAULT_SIDEBAR_WIDTH;
    return Math.max(MIN_SIDEBAR_WIDTH, Math.min(MAX_SIDEBAR_WIDTH, Math.round(parsed)));
  } catch {
    return DEFAULT_SIDEBAR_WIDTH;
  }
};

const readStoredCollapsed = (): boolean => {
  try {
    return window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === '1';
  } catch {
    return false;
  }
};

/** 局部协议徽标：复用全局 .iot-method-badge 样式，避免与主文件局部组件循环依赖 */
const ProtocolBadge: React.FC<{ request: SavedRequest }> = ({ request }) => {
  if (request.protocol === 'mqtt') {
    return <span className="iot-method-badge iot-method-badge--mqtt">MQTT</span>;
  }
  if (request.protocol === 'websocket') {
    return <span className="iot-method-badge iot-method-badge--ws">WS</span>;
  }
  const method = (request.method || 'GET').toUpperCase();
  const key = method.toLowerCase();
  const modifier = HTTP_METHOD_KEYS.includes(key) ? ` iot-method-badge--${key}` : '';
  return <span className={`iot-method-badge${modifier}`}>{method}</span>;
};

const getRequestSubtitle = (request: SavedRequest): string => {
  if (request.protocol === 'mqtt') {
    return [request.mqtt_config?.broker, request.mqtt_config?.topic].filter(Boolean).join(' → ');
  }
  if (request.protocol === 'websocket') {
    return request.ws_config?.url || request.url || '';
  }
  return request.url || '';
};

const IoTRequestSidebar: React.FC<IoTRequestSidebarProps> = ({
  folders,
  requests,
  loading,
  activeSavedRequestId,
  selectedFolderId,
  onSelectFolder,
  onOpenRequest,
  onCopyRequest,
  onDeleteRequest,
  onCreateFolder,
  onRenameFolder,
  onDeleteFolder,
  onRefresh,
  onOpenPushLog,
  onOpenNewItem,
  onAddRequest,
  onRenameRequest,
  onDuplicateFolder,
  onSortFolder,
  onSaveLayout,
}) => {
  const [width, setWidth] = useState<number>(() => readStoredSidebarWidth());
  const [collapsed, setCollapsed] = useState<boolean>(() => readStoredCollapsed());
  const [isDragging, setIsDragging] = useState(false);
  const [searchText, setSearchText] = useState('');
  const [collapsedFolderIds, setCollapsedFolderIds] = useState<Set<number>>(() => new Set<number>());
  const [showCreateInput, setShowCreateInput] = useState(false);
  const [creatingParentId, setCreatingParentId] = useState<number | null>(null);
  const [newFolderName, setNewFolderName] = useState('');
  const [editingFolderId, setEditingFolderId] = useState<number | null>(null);
  const [editingFolderName, setEditingFolderName] = useState('');
  const [editingRequestId, setEditingRequestId] = useState<number | null>(null);
  const [editingRequestName, setEditingRequestName] = useState('');

  const startXRef = React.useRef(0);
  const startWidthRef = React.useRef(width);
  const widthRef = React.useRef(width);
  const createSubmittingRef = React.useRef(false);
  const renameSubmittingRef = React.useRef(false);
  const renameRequestSubmittingRef = React.useRef(false);

  useEffect(() => {
    widthRef.current = width;
  }, [width]);

  useEffect(() => {
    try {
      window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? '1' : '0');
    } catch {
      /* localStorage 不可用时忽略 */
    }
  }, [collapsed]);

  const handleResizeMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    startXRef.current = e.clientX;
    startWidthRef.current = width;
    setIsDragging(true);
  };

  useEffect(() => {
    if (!isDragging) return;
    const handleMouseMove = (e: MouseEvent) => {
      const next = Math.max(
        MIN_SIDEBAR_WIDTH,
        Math.min(MAX_SIDEBAR_WIDTH, startWidthRef.current + (e.clientX - startXRef.current)),
      );
      widthRef.current = next;
      setWidth(next);
    };
    const handleMouseUp = () => {
      setIsDragging(false);
      try {
        window.localStorage.setItem(SIDEBAR_WIDTH_KEY, String(widthRef.current));
      } catch {
        /* localStorage 不可用时忽略 */
      }
    };
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging]);

  /* --- 拖拽排序 / 移动（Postman 风格） --- */

  // 搜索会把树过滤成子集，此时拖拽提交的顺序会丢节点，因此搜索态直接禁用拖拽
  const keyword = searchText.trim().toLowerCase();
  const isSearching = keyword.length > 0;

  /** 当前拖拽的节点 key（folder-1 / request-2），null 表示没有正在拖拽 */
  const [draggingKey, setDraggingKey] = useState<string | null>(null);
  /** 拖放指示线：目标 key + 落点（前 / 后 / 内部） */
  const [dropHint, setDropHint] = useState<{ key: string; position: DropPosition } | null>(null);
  const draggingKeyRef = React.useRef<string | null>(null);
  const dropHintRef = React.useRef<{ key: string; position: DropPosition } | null>(null);

  useEffect(() => {
    draggingKeyRef.current = draggingKey;
  }, [draggingKey]);

  /** 依据后端返回顺序（含 sort_order）构建容器表，拖放计算全部基于它 */
  const containers = useMemo(() => buildContainers(folders, requests), [folders, requests]);

  /** 拖到侧栏外松手时不会触发 onDrop，靠全局捕获兜底清理拖拽状态 */
  useEffect(() => {
    const handleGlobalDragEnd = () => {
      setDraggingKey(null);
      setDropHint(null);
    };
    window.addEventListener('dragend', handleGlobalDragEnd);
    window.addEventListener('drop', handleGlobalDragEnd);
    return () => {
      window.removeEventListener('dragend', handleGlobalDragEnd);
      window.removeEventListener('drop', handleGlobalDragEnd);
    };
  }, []);

  /** 拖动过程中禁止选中文字，避免出现蓝色选区 */
  useEffect(() => {
    if (!draggingKey) return;
    const previous = document.body.style.userSelect;
    document.body.style.userSelect = 'none';
    return () => {
      document.body.style.userSelect = previous;
    };
  }, [draggingKey]);

  const clearDragState = () => {
    draggingKeyRef.current = null;
    dropHintRef.current = null;
    setDraggingKey(null);
    setDropHint(null);
  };

  const handleDragStart = (event: React.DragEvent, key: string) => {
    if (!onSaveLayout || isSearching) {
      event.preventDefault();
      return;
    }
    event.stopPropagation();
    event.dataTransfer.effectAllowed = 'move';
    // Firefox 必须 setData 才会真正开始拖拽
    event.dataTransfer.setData('text/plain', key);
    // 同步写 ref：dragover 可能紧跟着 dragstart 在同一帧触发，等 useEffect 会读到旧值
    draggingKeyRef.current = key;
    setDraggingKey(key);
    setDropHint(null);
  };

  /** 该行能否作为「放进文件夹内部」的落点：请求行不行，自己的子孙文件夹也不行 */
  const canDropInside = (key: string): boolean => {
    if (key === ROOT_CONTAINER_KEY) return true;
    if (!key.startsWith('folder-')) return false;
    const dragged = draggingKeyRef.current;
    if (!dragged || !dragged.startsWith('folder-')) return true;
    const draggedId = Number(dragged.slice('folder-'.length));
    const targetId = Number(key.slice('folder-'.length));
    return !isDescendantFolder(folders, targetId, draggedId) && targetId !== draggedId;
  };

  const handleDragOver = (event: React.DragEvent, key: string, allowInside: boolean, highlightKey: string) => {
    if (!draggingKeyRef.current || !onSaveLayout || isSearching) return;
    if (draggingKeyRef.current === key) {
      dropHintRef.current = null;
      setDropHint(null);
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = 'move';

    // 行内三段：上 1/4 → 插到前面，下 1/4 → 插到后面，中间 → 放进文件夹
    const insideAllowed = allowInside && canDropInside(key);
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = rect.height > 0 ? (event.clientY - rect.top) / rect.height : 0.5;
    let position: DropPosition;
    if (!insideAllowed) position = ratio < 0.5 ? 'before' : 'after';
    else if (ratio < 0.25) position = 'before';
    else if (ratio > 0.75) position = 'after';
    else position = 'inside';

    // 同步写 ref，drop 里以它为准（同一帧内的 dragover → drop 拿不到新的 state）
    dropHintRef.current = { key: highlightKey, position };
    setDropHint((previous) =>
      previous && previous.key === highlightKey && previous.position === position
        ? previous
        : { key: highlightKey, position },
    );
  };

  const handleDrop = async (
    event: React.DragEvent,
    key: string,
    allowInside: boolean,
    highlightKey: string,
  ) => {
    if (!onSaveLayout || isSearching) return;
    event.preventDefault();
    event.stopPropagation();

    const draggedKey = draggingKeyRef.current;
    if (!draggedKey || draggedKey === key) {
      clearDragState();
      return;
    }

    // 落点以拖拽时记录的指示线为准，避免松手瞬间的坐标抖动改变位置
    const hint = dropHintRef.current;
    const position: DropPosition =
      hint && hint.key === highlightKey
        ? hint.position
        : allowInside && canDropInside(key)
          ? 'inside'
          : 'after';

    const groups = computeLayoutGroups(containers, folders, draggedKey, key, position);
    clearDragState();
    if (!groups) return;

    const ok = await onSaveLayout(groups);
    if (!ok) {
      // 保存失败不做乐观更新，直接以後端为准重拉
      onRefresh();
    }
  };

  /**
   * `key` 是布局计算用的容器 / 节点标识；`rowKey` 只用于高亮。
   * 「全部请求」与「未分类」都代表根层，逻辑 key 相同但各占一行，高亮要分开。
   */
  const dragPropsFor = (key: string, allowInside: boolean, locked = false, rowKey?: string) => {
    const highlightKey = rowKey ?? key;
    return {
      // 搜索态下树被过滤过，提交的顺序会丢节点，因此禁用拖拽
      draggable: Boolean(onSaveLayout) && !locked && !isSearching,
      onDragStart: (event: React.DragEvent) => handleDragStart(event, key),
      onDragOver: (event: React.DragEvent) => handleDragOver(event, key, allowInside, highlightKey),
      onDrop: (event: React.DragEvent) => {
        void handleDrop(event, key, allowInside, highlightKey);
      },
      onDragEnd: clearDragState,
    };
  };

  /** 目标是当前拖拽节点的祖先 → 禁止高亮「放进去」，否则会形成环 */
  const dropHintClass = (key: string): string => {
    if (!dropHint || dropHint.key !== key) return '';
    if (dropHint.position === 'inside') return ' is-drop-inside';
    return dropHint.position === 'before' ? ' is-drop-before' : ' is-drop-after';
  };

  const isDraggingClass = (key: string): string => (draggingKey === key ? ' is-dragging-node' : '');

  // 兼容后端两种形态：扁平列表（按 parent_id 组树）与已嵌套 children 的树
  const flatFolders = useMemo(() => {
    const result: SavedRequestFolder[] = [];
    const walk = (list: SavedRequestFolder[], parentId: number | null) => {
      list.forEach((folder) => {
        result.push({ ...folder, parent_id: folder.parent_id ?? parentId, children: [] });
        if (folder.children && folder.children.length > 0) walk(folder.children, folder.id);
      });
    };
    walk(folders, null);
    return result;
  }, [folders]);

  const folderTree = useMemo(() => {
    const byId = new Map<number, SavedRequestFolder>();
    flatFolders.forEach((folder) => byId.set(folder.id, folder));
    const roots: SavedRequestFolder[] = [];
    flatFolders.forEach((folder) => {
      const parentId = folder.parent_id;
      const parent = parentId === null || parentId === undefined ? undefined : byId.get(parentId);
      if (parent && parent.id !== folder.id) parent.children.push(folder);
      else roots.push(folder);
    });
    return roots;
  }, [flatFolders]);

  const { requestsByFolder, uncategorizedRequests } = useMemo(() => {
    const byFolder = new Map<number, SavedRequest[]>();
    const uncategorized: SavedRequest[] = [];
    requests.forEach((request) => {
      const folderId = request.folder_id;
      if (folderId === null || folderId === undefined) {
        uncategorized.push(request);
        return;
      }
      const bucket = byFolder.get(folderId);
      if (bucket) bucket.push(request);
      else byFolder.set(folderId, [request]);
    });
    return { requestsByFolder: byFolder, uncategorizedRequests: uncategorized };
  }, [requests]);

  const { visibleFolders, visibleUncategorized } = useMemo(() => {
    const matches = (value: string) => value.toLowerCase().includes(keyword);

    const build = (folder: SavedRequestFolder, keepAll: boolean): FilteredFolder | null => {
      const directRequests = requestsByFolder.get(folder.id) ?? [];
      const childFolders = folder.children ?? [];
      if (keepAll || matches(folder.name)) {
        return {
          folder,
          requests: directRequests,
          children: childFolders.map((child) => build(child, true)).filter(isFilteredFolder),
        };
      }
      const keptRequests = directRequests.filter((request) => matches(request.name));
      const keptChildren = childFolders.map((child) => build(child, false)).filter(isFilteredFolder);
      if (keptRequests.length === 0 && keptChildren.length === 0) return null;
      return { folder, requests: keptRequests, children: keptChildren };
    };

    if (!isSearching) {
      return {
        visibleFolders: folderTree.map((folder) => build(folder, true)).filter(isFilteredFolder),
        visibleUncategorized: uncategorizedRequests,
      };
    }
    return {
      visibleFolders: folderTree.map((folder) => build(folder, false)).filter(isFilteredFolder),
      visibleUncategorized: uncategorizedRequests.filter((request) => matches(request.name)),
    };
  }, [folderTree, requestsByFolder, uncategorizedRequests, keyword, isSearching]);

  const isFolderExpanded = (folderId: number) => isSearching || !collapsedFolderIds.has(folderId);

  const toggleFolder = (folderId: number) => {
    setCollapsedFolderIds((prev) => {
      const next = new Set(prev);
      if (next.has(folderId)) next.delete(folderId);
      else next.add(folderId);
      return next;
    });
  };

  const expandFolder = (folderId: number) => {
    setCollapsedFolderIds((prev) => {
      if (!prev.has(folderId)) return prev;
      const next = new Set(prev);
      next.delete(folderId);
      return next;
    });
  };

  const openCreateInput = (parentId: number | null) => {
    createSubmittingRef.current = false;
    setShowCreateInput(true);
    setCreatingParentId(parentId);
    setNewFolderName('');
  };

  const closeCreateInput = () => {
    setShowCreateInput(false);
    setCreatingParentId(null);
    setNewFolderName('');
  };

  const submitCreate = async (parentId: number | null) => {
    if (createSubmittingRef.current) return;
    const name = newFolderName.trim();
    if (!name) {
      closeCreateInput();
      return;
    }
    createSubmittingRef.current = true;
    try {
      const ok = await onCreateFolder(name, parentId);
      if (ok) {
        closeCreateInput();
        if (parentId !== null) expandFolder(parentId);
      }
    } catch (error) {
      console.error('创建文件夹失败:', error);
    } finally {
      createSubmittingRef.current = false;
    }
  };

  const startRename = (folder: SavedRequestFolder) => {
    renameSubmittingRef.current = false;
    setEditingFolderId(folder.id);
    setEditingFolderName(folder.name);
  };

  const submitRename = async (folderId: number) => {
    if (renameSubmittingRef.current) return;
    const name = editingFolderName.trim();
    if (!name) {
      setEditingFolderId(null);
      setEditingFolderName('');
      return;
    }
    renameSubmittingRef.current = true;
    try {
      const ok = await onRenameFolder(folderId, name);
      if (ok) {
        setEditingFolderId(null);
        setEditingFolderName('');
      }
    } catch (error) {
      console.error('重命名文件夹失败:', error);
    } finally {
      renameSubmittingRef.current = false;
    }
  };

  /**
   * 菜单项点击后阻止事件继续冒泡到所在行。
   * 菜单渲染在 Portal 中，但 React 事件仍沿组件树上传；
   * 若不拦截，点菜单项会同时触发行 onClick（如打开请求 → 打开两个标签页）。
   * 必须在 menu 级 onClick 里处理：菜单项自己的 onClick 先执行，此回调随后拿到 domEvent。
   */
  const stopMenuEvent: MenuProps['onClick'] = ({ domEvent }) => {
    domEvent?.stopPropagation?.();
  };

  /* --- 文件夹操作菜单（Postman 风格：悬停 + / ⋯，右键菜单） --- */

  const buildFolderMenu = (folder: SavedRequestFolder): MenuProps['items'] => [
    {
      key: 'add-request',
      label: '新建请求',
      icon: <PlusOutlined />,
      onClick: () => onAddRequest(folder.id),
    },
    {
      key: 'add-folder',
      label: '新建子文件夹',
      icon: <FolderAddOutlined />,
      onClick: () => openCreateInput(folder.id),
    },
    { type: 'divider' },
    {
      key: 'rename',
      label: '重命名',
      icon: <EditOutlined />,
      onClick: () => startRename(folder),
    },
    {
      key: 'duplicate',
      label: '创建副本',
      icon: <SnippetsOutlined />,
      onClick: () => onDuplicateFolder(folder.id),
    },
    {
      key: 'sort',
      label: '排序',
      icon: <SortAscendingOutlined />,
      children: [
        {
          key: 'sort-asc',
          label: '名称升序',
          onClick: () => onSortFolder?.(folder.id, 'asc'),
        },
        {
          key: 'sort-desc',
          label: '名称降序',
          onClick: () => onSortFolder?.(folder.id, 'desc'),
        },
      ],
    },
    { type: 'divider' },
    {
      key: 'delete',
      label: '删除',
      icon: <DeleteOutlined />,
      danger: true,
      onClick: () => onDeleteFolder(folder.id),
    },
  ];

  /* --- 请求实体操作菜单（与文件夹一致的 Postman 交互：⋯ + 右键） --- */

  const buildRequestMenu = (request: SavedRequest): MenuProps['items'] => [
    {
      key: 'rename',
      label: '重命名',
      icon: <EditOutlined />,
      onClick: () => startRenameRequest(request),
    },
    {
      key: 'duplicate',
      label: '创建副本',
      icon: <SnippetsOutlined />,
      onClick: () => onCopyRequest(request),
    },
    { type: 'divider' },
    {
      key: 'delete',
      label: '删除',
      icon: <DeleteOutlined />,
      danger: true,
      onClick: () => onDeleteRequest(request.id),
    },
  ];

  const startRenameRequest = (request: SavedRequest) => {
    renameRequestSubmittingRef.current = false;
    setEditingRequestId(request.id);
    setEditingRequestName(request.name);
  };

  const submitRenameRequest = async (request: SavedRequest) => {
    if (renameRequestSubmittingRef.current) return;
    const name = editingRequestName.trim();
    if (!name || name === request.name) {
      setEditingRequestId(null);
      setEditingRequestName('');
      return;
    }
    renameRequestSubmittingRef.current = true;
    try {
      await onRenameRequest(request.id, name);
      setEditingRequestId(null);
      setEditingRequestName('');
    } catch (error) {
      console.error('重命名请求失败:', error);
    } finally {
      renameRequestSubmittingRef.current = false;
    }
  };

  const renderRequestNode = (request: SavedRequest): React.ReactNode => {
    const subtitle = getRequestSubtitle(request);
    const isActive = activeSavedRequestId === request.id;
    const isEditing = editingRequestId === request.id;
    const entryKey = requestEntryKey(request.id);
    return (
      <Dropdown
        key={request.id}
        menu={{ items: buildRequestMenu(request), onClick: stopMenuEvent }}
        trigger={['contextMenu']}
        placement="bottomLeft"
      >
        <div
          className={`iot-tree-request${isActive ? ' is-active' : ''}${dropHintClass(entryKey)}${isDraggingClass(entryKey)}`}
          // 缩进由父级 iot-tree-children 容器累计，行内只保留基础内边距，避免双重缩进
          style={{ paddingLeft: INDENT_BASE }}
          onClick={() => onOpenRequest(request)}
          title={subtitle || request.name}
          {...dragPropsFor(entryKey, false, isEditing)}
        >
          <ProtocolBadge request={request} />
          <span className="iot-tree-request-info">
            {isEditing ? (
              <Input
                size="small"
                className="iot-tree-request-rename-input"
                value={editingRequestName}
                autoFocus
                onClick={(e) => e.stopPropagation()}
                onChange={(e) => setEditingRequestName(e.target.value)}
                onPressEnter={() => submitRenameRequest(request)}
                onBlur={() => submitRenameRequest(request)}
              />
            ) : (
              <span className="iot-tree-request-name">{request.name}</span>
            )}
          </span>
          <span className="iot-tree-request-actions">
            {/* 与文件夹一致：悬停显示「⋯」，避免常驻按钮挤压名称 */}
            <Dropdown
              menu={{ items: buildRequestMenu(request), onClick: stopMenuEvent }}
              trigger={['click']}
              placement="bottomRight"
            >
              <Button
                type="text"
                size="small"
                className="iot-tree-request-action"
                icon={<MoreOutlined />}
                aria-label="更多操作"
                onClick={(e) => e.stopPropagation()}
              />
            </Dropdown>
          </span>
        </div>
      </Dropdown>
    );
  };

  const renderFolderNode = (node: FilteredFolder): React.ReactNode => {
    const { folder, requests: folderRequests, children } = node;
    const hasChildren = children.length > 0 || folderRequests.length > 0;
    const expanded = isFolderExpanded(folder.id);
    const isEditing = editingFolderId === folder.id;
    const isCreatingChild = showCreateInput && creatingParentId === folder.id;
    const isActive = selectedFolderId === folder.id;

    return (
      <React.Fragment key={folder.id}>
        {/* Postman：文件夹行同时支持「⋯」菜单与右键菜单，共用同一份菜单项 */}
        <Dropdown
          menu={{ items: buildFolderMenu(folder), onClick: stopMenuEvent }}
          trigger={['contextMenu']}
          placement="bottomLeft"
        >
          <div
            className={`iot-tree-folder${isActive ? ' is-active' : ''}${expanded ? ' is-expanded' : ''}${dropHintClass(folderEntryKey(folder.id))}${isDraggingClass(folderEntryKey(folder.id))}`}
            style={{ paddingLeft: INDENT_BASE }}
            onClick={() => onSelectFolder?.(folder.id)}
            data-folder-id={folder.id}
            {...dragPropsFor(folderEntryKey(folder.id), true, isEditing)}
          >
          <span
            className="iot-tree-folder-toggle"
            onClick={(e) => {
              e.stopPropagation();
              toggleFolder(folder.id);
            }}
          >
            {hasChildren ? (
              expanded ? (
                <DownOutlined />
              ) : (
                <RightOutlined />
              )
            ) : (
              <span className="iot-tree-folder-toggle-placeholder" />
            )}
          </span>
          <span className="iot-tree-folder-icon">
            {expanded ? <FolderOpenOutlined /> : <FolderOutlined />}
          </span>
          {isEditing ? (
            <Input
              size="small"
              className="iot-tree-folder-rename-input"
              value={editingFolderName}
              autoFocus
              onClick={(e) => e.stopPropagation()}
              onChange={(e) => setEditingFolderName(e.target.value)}
              onPressEnter={() => submitRename(folder.id)}
              onBlur={() => submitRename(folder.id)}
            />
          ) : (
            <span className="iot-tree-folder-name" title={folder.name}>
              {folder.name}
            </span>
          )}
          <span className="iot-tree-folder-actions">
            {/* Postman：悬停显示「+」与「⋯」，避免常驻按钮挤压名称 */}
            <Tooltip title="新建请求">
              <Button
                type="text"
                size="small"
                className="iot-tree-folder-action"
                icon={<PlusOutlined />}
                aria-label="在此文件夹新建请求"
                onClick={(e) => {
                  e.stopPropagation();
                  onAddRequest(folder.id);
                }}
              />
            </Tooltip>
            <Dropdown
              menu={{ items: buildFolderMenu(folder), onClick: stopMenuEvent }}
              trigger={['click']}
              placement="bottomRight"
            >
              <Button
                type="text"
                size="small"
                className="iot-tree-folder-action"
                icon={<MoreOutlined />}
                aria-label="更多操作"
                onClick={(e) => e.stopPropagation()}
              />
            </Dropdown>
          </span>
        </div>
        </Dropdown>
        {/* 子级统一放在带引导竖线的容器里：缩进由容器承担，竖线沿深度累计 */}
        {isCreatingChild && (
          <div className="iot-tree-children">
            <div className="iot-tree-folder-create">
              <Input
                size="small"
                autoFocus
                placeholder="输入文件夹名称"
                className="iot-tree-folder-create-input"
                value={newFolderName}
                onChange={(e) => setNewFolderName(e.target.value)}
                onPressEnter={() => submitCreate(folder.id)}
                onBlur={() => submitCreate(folder.id)}
              />
            </div>
          </div>
        )}
        {expanded && hasChildren && (
          <div className="iot-tree-children">
            {folderRequests.map((request) => renderRequestNode(request))}
            {children.map((child) => renderFolderNode(child))}
          </div>
        )}
      </React.Fragment>
    );
  };

  const renderAllRequestsNode = (): React.ReactNode => (
    <div
      className={`iot-tree-folder iot-tree-node--all${selectedFolderId === undefined ? ' is-active' : ''}${dropHintClass(ROOT_ALL_ROW_KEY)}`}
      style={{ paddingLeft: INDENT_BASE }}
      onClick={() => onSelectFolder?.(undefined)}
      {...dragPropsFor(ROOT_CONTAINER_KEY, true, true, ROOT_ALL_ROW_KEY)}
    >
      <span className="iot-tree-folder-toggle">
        <span className="iot-tree-folder-toggle-placeholder" />
      </span>
      <span className="iot-tree-folder-icon">
        <FolderOutlined />
      </span>
      <span className="iot-tree-folder-name">全部请求</span>
    </div>
  );

  const renderUncategorizedNode = (items: SavedRequest[]): React.ReactNode => {
    const expanded = isFolderExpanded(UNCATEGORIZED_ID);
    return (
      <React.Fragment key="iot-uncategorized">
        <div
          className={`iot-tree-folder${selectedFolderId === null ? ' is-active' : ''}${expanded ? ' is-expanded' : ''}${dropHintClass(ROOT_UNCATEGORIZED_ROW_KEY)}`}
          style={{ paddingLeft: INDENT_BASE }}
          onClick={() => onSelectFolder?.(null)}
          {...dragPropsFor(ROOT_CONTAINER_KEY, true, true, ROOT_UNCATEGORIZED_ROW_KEY)}
        >
          <span
            className="iot-tree-folder-toggle"
            onClick={(e) => {
              e.stopPropagation();
              toggleFolder(UNCATEGORIZED_ID);
            }}
          >
            {expanded ? <DownOutlined /> : <RightOutlined />}
          </span>
          <span className="iot-tree-folder-icon">
            <InboxOutlined />
          </span>
          <span className="iot-tree-folder-name">未分类</span>
          <span className="iot-tree-folder-count">{items.length}</span>
          {/* 未分类也给出「+」，与真实文件夹保持一致的交互 */}
          <span className="iot-tree-folder-actions">
            <Tooltip title="新建请求">
              <Button
                type="text"
                size="small"
                className="iot-tree-folder-action"
                icon={<PlusOutlined />}
                aria-label="新建请求"
                onClick={(e) => {
                  e.stopPropagation();
                  onAddRequest(null);
                }}
              />
            </Tooltip>
          </span>
        </div>
        {expanded && items.length > 0 && (
          <div className="iot-tree-children">
            {items.map((request) => renderRequestNode(request))}
          </div>
        )}
      </React.Fragment>
    );
  };

  const hasNoRequests = requests.length === 0;
  const hasNoMatch = isSearching && visibleFolders.length === 0 && visibleUncategorized.length === 0;

  return (
    <aside
      className={`iot-sidebar${collapsed ? ' is-collapsed' : ''}${isDragging ? ' is-dragging' : ''}`}
      style={{ width: collapsed ? 0 : width }}
    >
      {collapsed ? (
        <Tooltip title="展开保存的请求" placement="right">
          <button
            type="button"
            className="iot-sidebar-expand-btn"
            aria-label="展开保存的请求"
            onClick={() => setCollapsed(false)}
          >
            <RightOutlined />
          </button>
        </Tooltip>
      ) : (
        <>
          <div className="iot-sidebar-header">
            <span className="iot-sidebar-title">保存的请求</span>
            <Space size={2} className="iot-sidebar-header-actions">
              <Tooltip title="新建">
                <Button
                  type="text"
                  size="small"
                  className="iot-sidebar-header-btn"
                  icon={<PlusOutlined />}
                  aria-label="新建"
                  onClick={onOpenNewItem}
                />
              </Tooltip>
              <Tooltip title="刷新">
                <Button
                  type="text"
                  size="small"
                  className="iot-sidebar-header-btn"
                  icon={<SyncOutlined />}
                  loading={loading}
                  aria-label="刷新"
                  onClick={onRefresh}
                />
              </Tooltip>
              <Tooltip title="请求记录">
                <Button
                  type="text"
                  size="small"
                  className="iot-sidebar-header-btn"
                  icon={<HistoryOutlined />}
                  aria-label="请求记录"
                  onClick={onOpenPushLog}
                />
              </Tooltip>
              <Tooltip title="折叠侧栏" placement="left">
                <Button
                  type="text"
                  size="small"
                  className="iot-sidebar-header-btn"
                  icon={<LeftOutlined />}
                  aria-label="折叠侧栏"
                  onClick={() => setCollapsed(true)}
                />
              </Tooltip>
            </Space>
          </div>

          <div className="iot-sidebar-search">
            <Input.Search
              size="small"
              allowClear
              placeholder="搜索请求或文件夹"
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              onSearch={(value) => setSearchText(value)}
            />
          </div>

          {/* 拖到列表空白处 = 落到根层末尾，避免拖拽在空白区松手时静默失效 */}
          <div
            className="iot-sidebar-tree"
            onDragOver={(event) => {
              const dragged = draggingKeyRef.current;
              if (!dragged || !onSaveLayout) return;
              event.preventDefault();
              event.dataTransfer.dropEffect = 'move';
              if (dropHintRef.current?.key !== ROOT_ALL_ROW_KEY) {
                dropHintRef.current = { key: ROOT_ALL_ROW_KEY, position: 'inside' };
                setDropHint({ key: ROOT_ALL_ROW_KEY, position: 'inside' });
              }
            }}
            onDrop={(event) => {
              const dragged = draggingKeyRef.current;
              if (!dragged || !onSaveLayout) return;
              event.preventDefault();
              const groups = computeLayoutGroups(containers, folders, dragged, ROOT_CONTAINER_KEY, 'inside');
              clearDragState();
              if (groups) void onSaveLayout(groups);
            }}
          >
            {hasNoRequests ? (
              <div className="iot-sidebar-empty">
                <InboxOutlined className="iot-sidebar-empty-icon" />
                <span>暂无保存的请求</span>
              </div>
            ) : (
              <>
                {renderAllRequestsNode()}
                {hasNoMatch ? (
                  <div className="iot-sidebar-empty">
                    <InboxOutlined className="iot-sidebar-empty-icon" />
                    <span>未找到匹配的请求</span>
                  </div>
                ) : (
                  <>
                    {visibleFolders.map((folder) => renderFolderNode(folder))}
                    {visibleUncategorized.length > 0 && renderUncategorizedNode(visibleUncategorized)}
                  </>
                )}
              </>
            )}
          </div>

          <div
            className="iot-sidebar-resize"
            role="separator"
            aria-orientation="vertical"
            onMouseDown={handleResizeMouseDown}
          />
        </>
      )}
    </aside>
  );
};

export default IoTRequestSidebar;

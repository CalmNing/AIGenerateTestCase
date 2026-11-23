import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Form, Select, TreeSelect, Input, InputNumber, Button, Space, message, Modal, Tooltip, Radio, Switch, Tag, Checkbox } from 'antd';
import { SendOutlined, PlusOutlined, MinusOutlined, CopyOutlined, SaveOutlined, SyncOutlined, FormatPainterOutlined, QuestionCircleOutlined, EditOutlined, UpOutlined, DownOutlined, ApiOutlined, SwapOutlined, CloudServerOutlined, FolderAddOutlined } from '@ant-design/icons';
import CodeMirror from '@uiw/react-codemirror';
import { json } from '@codemirror/lang-json';
import { savedRequestApi, savedRequestFolderApi, globalParameterApi, proxyApi, mqttApi, wsApi } from '../../services/api';
import { SavedRequest as SavedRequestType, SavedRequestFolder, SavedRequestLayoutGroup, MqttConfig, MqttPublishResult, WsConfig, WsConnectResult, PreStep, PreStepKv, BatchParams } from '../../types';
import FileUpload, { UploadedFileResult } from '../FileUpload';
import PushLogDrawer from './PushLogDrawer';
import PreStepsEditor from './PreStepsEditor';
import BatchParamsEditor from './BatchParamsEditor';
import { copyTextToClipboard } from '../../utils/clipboard';
import IoTRequestSidebar from './IoTRequestSidebar';
import './IoTDataPushPlatform.css';

interface HeaderItem {
  key: string;
  value: string;
  /** 是否启用（Postman 风格勾选），false 时不参与发送 */
  enabled?: boolean;
  /** 备注说明，仅用于展示与持久化 */
  description?: string;
}

interface ParameterItem {
  key: string;
  value: string;
  type?: 'text' | 'file';
  file?: UploadedFileResult;
  /** 是否启用（Postman 风格勾选），false 时不参与发送 */
  enabled?: boolean;
  /** 备注说明，仅用于展示与持久化 */
  description?: string;
}

interface ExtractionRule {
  variable: string;
  jsonpath: string;
}

interface Environment {
  id: string;
  name: string;
  parameters: ParameterItem[];
}

type SavedRequest = SavedRequestType;

// --- Utility Functions ---

function findInlineComment(line: string): string | null {
  let inStr = false;
  let ch = '';
  for (let i = 0; i < line.length; i++) {
    if (inStr) {
      if (line[i] === '\\' && i + 1 < line.length) { i++; continue; }
      if (line[i] === ch) inStr = false;
    } else if (line[i] === '"' || line[i] === "'") {
      inStr = true; ch = line[i];
    } else if (line[i] === '/' && i + 1 < line.length) {
      if (line[i + 1] === '/') return line.substring(i).trimEnd();
      if (line[i + 1] === '*') {
        const end = line.indexOf('*/', i + 2);
        return end > -1 ? line.substring(i, end + 2) : line.substring(i).trimEnd();
      }
    }
  }
  return null;
}

function formatJsonWithComments(input: string): string {
  const lines = input.split('\n');
  const pendingComments: string[] = [];
  const keyStandaloneComments = new Map<string, string[]>();
  const keyInlineComments = new Map<string, string>();

  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith('//') || trimmed.startsWith('/*')) {
      pendingComments.push(trimmed);
      continue;
    }
    const noComment = trimmed.replace(/\/\/.*$/, '').replace(/\/\*[\s\S]*?\*\//, '');
    const keyMatch = noComment.match(/^"([^"]+)"\s*:/);
    if (keyMatch) {
      const key = keyMatch[1];
      if (pendingComments.length > 0) {
        keyStandaloneComments.set(key, [...pendingComments]);
        pendingComments.length = 0;
      }
      const inline = findInlineComment(trimmed);
      if (inline) keyInlineComments.set(key, inline);
    }
  }

  const placeholders = new Map<string, string>();
  let placeholderIndex = 0;
  const cleaned = stripJsonComments(input).replace(/([:,[])\s*(?!"|\d|true|false|null|\[|\{(?!\{))((?:[^\s,}\]]*?(?:\{\{(?:[^{}]|\{[^{}]*\})*\}\}|\$\{[^}]*\})[^\s,}\]]*?)+)/g, (_match, prefix, valuePart) => {
    const placeholder = `___PLACEHOLDER_${placeholderIndex}___`;
    placeholders.set(placeholder, valuePart);
    placeholderIndex++;
    return `${prefix} "${placeholder}"`;
  });

  const obj = JSON.parse(cleaned);
  let formatted = JSON.stringify(obj, null, 2);

  placeholders.forEach((original, placeholder) => {
    formatted = formatted.split(`"${placeholder}"`).join(original);
  });

  const out: string[] = [];
  for (const line of formatted.split('\n')) {
    const km = line.match(/^\s*"([^"]+)"\s*:/);
    if (km) {
      const key = km[1];
      const indent = line.match(/^(\s*)/)?.[1] || '';
      const standalones = keyStandaloneComments.get(key);
      if (standalones) {
        for (const c of standalones) out.push(indent + c);
      }
      let outLine = line;
      const inline = keyInlineComments.get(key);
      if (inline) outLine += '  ' + inline;
      out.push(outLine);
    } else {
      out.push(line);
    }
  }
  return out.join('\n');
}

function stripJsonComments(str: string): string {
  let result = '';
  let i = 0;
  let inString = false;
  let stringChar = '';
  while (i < str.length) {
    if (inString) {
      if (str[i] === '\\' && i + 1 < str.length) {
        result += str[i] + str[i + 1];
        i += 2;
        continue;
      }
      if (str[i] === stringChar) {
        inString = false;
      }
      result += str[i];
      i++;
    } else if (str[i] === '"' || str[i] === "'") {
      inString = true;
      stringChar = str[i];
      result += str[i];
      i++;
    } else if (str[i] === '/' && i + 1 < str.length && str[i + 1] === '/') {
      while (i < str.length && str[i] !== '\n') i++;
    } else if (str[i] === '/' && i + 1 < str.length && str[i + 1] === '*') {
      i += 2;
      while (i < str.length && !(str[i] === '*' && i + 1 < str.length && str[i + 1] === '/')) i++;
      if (i < str.length) i += 2;
    } else {
      result += str[i];
      i++;
    }
  }
  return result;
}

function parseJsonWithComments(str: string): any {
  return JSON.parse(stripJsonComments(str));
}

// --- JSON Syntax Highlighter (pure CSS, nested collapsible tree for response) ---

const LARGE_RESPONSE_LIMIT = 100 * 1024;

function escapeJsonText(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function highlightJsonScalar(value: any): string {
  if (value === null) return '<span class="iot-json-null">null</span>';
  if (typeof value === 'boolean') return `<span class="iot-json-boolean">${value}</span>`;
  if (typeof value === 'number') return `<span class="iot-json-number">${value}</span>`;
  if (typeof value === 'string') return `<span class="iot-json-string">"${escapeJsonText(value)}"</span>`;
  return escapeJsonText(String(value));
}

function renderJsonNode(value: any, indent: number, depth: number): string {
  if (value === null || typeof value !== 'object') return highlightJsonScalar(value);

  const pad = ' '.repeat(depth * indent);
  const padInner = ' '.repeat((depth + 1) * indent);
  const isArray = Array.isArray(value);
  const openBracket = isArray ? '[' : '{';
  const closeBracket = isArray ? ']' : '}';

  const entries = isArray
    ? value.map((item: any) => `${padInner}${renderJsonNode(item, indent, depth + 1)}`)
    : Object.keys(value).map(key =>
        `${padInner}<span class="iot-json-key">"${escapeJsonText(key)}"</span><span class="iot-json-colon">: </span>${renderJsonNode(value[key], indent, depth + 1)}`
      );
  if (entries.length === 0) return `<span class="iot-json-bracket">${openBracket}${closeBracket}</span>`;

  const countLabel = isArray ? `${entries.length} 项` : `${entries.length} 个字段`;
  return (
    `<span class="iot-json-node is-open">` +
    `<span class="iot-json-toggle" title="点击折叠/展开">▾</span>` +
    `<span class="iot-json-bracket">${openBracket}</span>` +
    `<span class="iot-json-children">\n${entries.join(',\n')}\n${pad}<span class="iot-json-bracket">${closeBracket}</span></span>` +
    `<span class="iot-json-preview"><span class="iot-json-ellipsis">…</span><span class="iot-json-bracket">${closeBracket}</span><span class="iot-json-count">${countLabel}</span></span>` +
    `</span>`
  );
}

function highlightJson(value: any, indent: number = 2): string {
  return renderJsonNode(value, indent, 0);
}

function measureJsonDepth(value: any, depth: number = 0): number {
  if (value === null || typeof value !== 'object') return depth;
  if (Array.isArray(value)) {
    let max = depth + 1;
    for (const item of value) max = Math.max(max, measureJsonDepth(item, depth + 1));
    return max;
  }
  let max = depth + 1;
  for (const child of Object.values(value)) max = Math.max(max, measureJsonDepth(child, depth + 1));
  return max;
}

// --- Method Badge Component ---

const MethodBadge: React.FC<{ method: string; size?: 'sm' | 'md' }> = ({ method, size = 'sm' }) => {
  const m = method.toUpperCase();
  const cls = `iot-method-badge iot-method-badge--${m.toLowerCase()}`;
  return (
    <span className={cls} style={size === 'md' ? { fontSize: 12, padding: '3px 10px' } : undefined}>
      {m}
    </span>
  );
};

// --- Protocol ---

type PushProtocol = 'http' | 'mqtt' | 'websocket';

// MQTT 徽标（已保存请求列表 / 方法位置）
const MqttBadge: React.FC<{ size?: 'sm' | 'md' }> = ({ size = 'sm' }) => (
  <span
    className="iot-method-badge iot-method-badge--mqtt"
    style={size === 'md' ? { fontSize: 12, padding: '3px 10px' } : undefined}
  >
    MQTT
  </span>
);

// WebSocket 徽标
const WsBadge: React.FC<{ size?: 'sm' | 'md' }> = ({ size = 'sm' }) => (
  <span
    className="iot-method-badge iot-method-badge--ws"
    style={size === 'md' ? { fontSize: 12, padding: '3px 10px' } : undefined}
  >
    WS
  </span>
);

const createDefaultWsConfig = (): WsConfig => ({
  url: '',
  headers: [],
  maxMessages: 50,
  timeoutSeconds: 10,
  protocols: '',
  sendMessage: '',
});

// 后端返回的 ws_config 可能缺字段（历史数据或未持久化），这里补齐默认值
const normalizeWsConfig = (config?: WsConfig | null): WsConfig => {
  const base = createDefaultWsConfig();
  if (!config) return base;
  const maxMessages = Number(config.maxMessages);
  const timeoutSeconds = Number(config.timeoutSeconds);
  return {
    ...base,
    ...config,
    headers: Array.isArray(config.headers) ? config.headers : [],
    maxMessages: Number.isFinite(maxMessages) && maxMessages > 0 ? Math.min(500, Math.floor(maxMessages)) : 50,
    timeoutSeconds: Number.isFinite(timeoutSeconds) && timeoutSeconds > 0 ? Math.min(60, Math.floor(timeoutSeconds)) : 10,
    protocols: config.protocols || '',
    sendMessage: config.sendMessage || '',
  };
};

/** 子协议输入框（逗号分隔）转数组 */
const parseProtocols = (value?: string): string[] | undefined => {
  const list = (value || '')
    .split(',')
    .map(item => item.trim())
    .filter(Boolean);
  return list.length > 0 ? list : undefined;
};

/** 请求头行数组转对象，过滤空 key */
const headersToRecord = (headers: Array<{ key: string; value: string; enabled?: boolean }>): Record<string, string> | undefined => {
  const record: Record<string, string> = {};
  for (const header of headers) {
    if (header.enabled === false) continue;
    if (header.key.trim()) record[header.key.trim()] = header.value;
  }
  return Object.keys(record).length > 0 ? record : undefined;
};

/** WebSocket closed_by 取值转中文说明 */
const WS_CLOSED_BY_LABELS: Record<string, string> = {
  server: '服务端关闭',
  client: '客户端关闭',
  timeout: '空闲超时',
  max_messages: '达到最大消息数',
  error: '连接异常',
};

const formatClosedBy = (closedBy?: string | null): string =>
  (closedBy && WS_CLOSED_BY_LABELS[closedBy]) || closedBy || '未知';

const createDefaultMqttConfig = (): MqttConfig => ({
  broker: '',
  port: '',
  topic: '',
  payload: '',
  qos: 0,
  retain: false,
  client_id: '',
  username: '',
  password: '',
  tls: false,
});

// 后端返回的 mqtt_config 可能缺字段，这里补齐默认值，避免受控组件拿到 undefined
const normalizeMqttConfig = (config?: MqttConfig | null): MqttConfig => {
  const base = createDefaultMqttConfig();
  if (!config) return base;
  const qos = Number(config.qos);
  return {
    ...base,
    ...config,
    port: config.port != null ? String(config.port) : '',
    qos: qos === 1 || qos === 2 ? qos : 0,
    retain: Boolean(config.retain),
    tls: Boolean(config.tls),
  };
};

/**
 * 把 Broker 地址与端口合并成最终连接地址。
 * - 端口留空：直接用 Broker 地址（地址里没端口时后端按协议取默认端口）
 * - Broker 已带端口：以地址内的端口为准，忽略端口输入框
 * - ws/wss 带路径：端口插在 host 与 path 之间，如 ws://host:8083/mqtt
 */
const composeBrokerAddress = (config: MqttConfig): string => {
  const broker = (config.broker || '').trim();
  const port = (config.port || '').trim();
  if (!broker || !port) return broker;
  const schemeMatch = broker.match(/^([a-zA-Z][a-zA-Z0-9+.-]*):\/\//);
  const rest = schemeMatch ? broker.slice(schemeMatch[0].length) : broker;
  const slashIndex = rest.indexOf('/');
  const hostPart = slashIndex >= 0 ? rest.slice(0, slashIndex) : rest;
  const pathPart = slashIndex >= 0 ? rest.slice(slashIndex) : '';
  if (/:\d+$/.test(hostPart)) return broker;
  const withPort = `${hostPart}:${port}${pathPart}`;
  return schemeMatch ? `${schemeMatch[0]}${withPort}` : withPort;
};

// --- Tab Interface ---

type ConfigTabKey = 'params' | 'headers' | 'body' | 'pre' | 'batch' | 'extract' | 'mqtt' | 'ws';
type ResponseTabKey = 'body' | 'headers' | 'pre' | 'messages' | 'result';

interface Tab {
  id: string;
  name: string;
  /** 协议：HTTP（默认）| MQTT | WebSocket */
  protocol: PushProtocol;
  method: string;
  url: string;
  headers: HeaderItem[];
  parameters: ParameterItem[];
  postExtractions?: ExtractionRule[];
  /** 前置步骤：发请求取变量 / 直接设置变量 */
  preSteps?: PreStep[];
  /** 前置参数列表：按行循环发送主请求 */
  batchParams?: BatchParams;
  body?: string;
  /** MQTT 配置，仅 protocol === 'mqtt' 时有效 */
  mqtt?: MqttConfig;
  /** 最近一次 MQTT 发布结果，仅 protocol === 'mqtt' 时展示 */
  mqttResult?: MqttPublishResult | null;
  /** WebSocket 配置，仅 protocol === 'websocket' 时有效 */
  ws?: WsConfig;
  /** 最近一次 WebSocket 连接结果，仅 protocol === 'websocket' 时展示 */
  wsResult?: WsConnectResult | null;
  savedRequestId?: number;
  /**
   * 该标签页保存后应归属的文件夹。
   * - number：文件夹 id
   * - null：未分类
   * - undefined：尚未确定（初始标签页 / 从任意位置新建），保存时让用户选择
   * 已保存的请求用 savedRequestId 对应的记录里的 folder_id 为准。
   */
  folderId?: number | null;
  hasUnsavedChanges?: boolean;
  baseline?: string;
  response?: any;
  responseTime?: number;
}

/** 请求级前置参数：后端旧数据行的 batch_params 存的是 []（数组）而不是对象，
 * 直接当对象用会强访 batchParams.columns 而报错，这里统一归一化。 */
const normalizeBatchParams = (raw: any): BatchParams => {
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    return {
      columns: Array.isArray(raw.columns) ? raw.columns : [],
      rows: Array.isArray(raw.rows) ? raw.rows : [],
      on_error: raw.on_error === 'stop' ? 'stop' : 'continue',
    };
  }
  return { columns: [], rows: [], on_error: 'continue' };
};

/**
 * 归一化前置步骤数据：后端旧数据的 request 步骤 headers/params 存的是
 * {k:v} 字典（Record<string,string>），新版编辑器用 PreStepKv[] 数组；
 * 这里统一转成数组形态，保证旧数据在新编辑器里可正常展示与编辑。
 */
const normalizePreSteps = (raw: any): PreStep[] => {
  if (!Array.isArray(raw)) return [];
  return raw.map((step: any): PreStep => {
    if (!step || typeof step !== 'object') return step;
    const toKvList = (v: any): PreStepKv[] | undefined => {
      if (Array.isArray(v)) return v;
      if (v && typeof v === 'object') {
        return Object.entries(v).map(([key, value]) => ({ key, value: String(value), enabled: true }));
      }
      return undefined;
    };
    const headers = toKvList(step.headers);
    const params = toKvList(step.params);
    return { ...step, ...(headers ? { headers } : {}), ...(params ? { params } : {}) };
  });
};

const tabContentKey = (
  tab: Pick<Tab, 'name' | 'protocol' | 'method' | 'url' | 'body' | 'headers' | 'parameters' | 'postExtractions' | 'preSteps' | 'batchParams' | 'mqtt' | 'ws'>
): string =>
  JSON.stringify([
    tab.name,
    tab.protocol || 'http',
    tab.method,
    tab.url,
    tab.body || '',
    tab.headers || [],
    tab.parameters || [],
    tab.postExtractions || [],
    tab.preSteps || [],
    tab.batchParams || null,
    tab.protocol === 'mqtt' ? (tab.mqtt || null) : null,
    tab.protocol === 'websocket' ? (tab.ws || null) : null,
  ]);

const markTabClean = (tab: Tab): Tab => ({
  ...tab,
  hasUnsavedChanges: false,
  baseline: tabContentKey(tab),
});

const applyTabChange = (tab: Tab, updates: Partial<Tab>): Tab => {
  const merged = { ...tab, ...updates };
  const baseline = tab.baseline ?? tabContentKey(tab);
  return { ...merged, hasUnsavedChanges: tabContentKey(merged) !== baseline };
};

interface IoTDataPushPlatformProps {
  currentEnvironmentId?: string;
  canManageGlobalParams?: boolean;
  /**
   * 环境数据变化通知（后置提取写回变量、增删改环境等）。
   * 本组件维护自己的 environments 副本，父级（App.tsx）也有一份；
   * 没有这个回调时，后置提取存入的变量只在本组件刷新，父级与变量助手看不到。
   */
  onEnvironmentsChanged?: () => void;
}

let tabCounter = 0;

// HTTP 徽标（与 MQTT / WS 徽标同一套样式语言）
const HttpBadge: React.FC<{ size?: 'sm' | 'md' }> = ({ size = 'sm' }) => (
  <span
    className="iot-method-badge iot-method-badge--http"
    style={size === 'md' ? { fontSize: 12, padding: '3px 10px' } : undefined}
  >
    HTTP
  </span>
);

/** 新建请求时可选的协议类型：与 Postman 的请求类型一致，创建后不可更改 */
const NEW_REQUEST_TYPES: Array<{ value: PushProtocol; label: string; description: string }> = [
  { value: 'http', label: 'HTTP', description: 'REST / HTTP 接口调试与数据推送' },
  { value: 'websocket', label: 'WebSocket', description: '长连接：收发文本 / 二进制消息' },
  { value: 'mqtt', label: 'MQTT', description: '发布 / 订阅：向 Broker 发布消息' },
];

const newRequestName = (protocol: PushProtocol): string =>
  protocol === 'mqtt' ? '新 MQTT 请求' : protocol === 'websocket' ? '新 WebSocket 请求' : '新请求';

/** 在文件夹树中按 id 查找节点（兼容后端扁平 parent_id 与嵌套 children 两种形态） */
const findFolderById = (
  nodes: SavedRequestFolder[],
  id: number,
): SavedRequestFolder | null => {
  for (const node of nodes) {
    if (node.id === id) return node;
    const hit = findFolderById(node.children || [], id);
    if (hit) return hit;
  }
  return null;
};

/** 按名称重排指定文件夹的同级兄弟节点（递归处理整棵树） */
const sortFolderTree = (
  nodes: SavedRequestFolder[],
  targetId: number,
  direction: 'asc' | 'desc',
): SavedRequestFolder[] => {
  const compare = (a: SavedRequestFolder, b: SavedRequestFolder) =>
    direction === 'asc'
      ? a.name.localeCompare(b.name, 'zh-Hans-CN')
      : b.name.localeCompare(a.name, 'zh-Hans-CN');

  const walk = (list: SavedRequestFolder[]): SavedRequestFolder[] => {
    const mapped = list.map((node) => ({
      ...node,
      children: walk(node.children || []),
    }));
    // 找到目标节点所在的层级：含目标则排这一层
    if (mapped.some((node) => node.id === targetId)) {
      return [...mapped].sort(compare);
    }
    return mapped;
  };

  return walk(nodes);
};

const buildNewTab = (protocol: PushProtocol, folderId?: number | null): Tab => ({
  id: 'tab-' + Date.now() + '-' + (++tabCounter),
  name: newRequestName(protocol),
  protocol,
  method: 'GET',
  url: '',
  headers: [{ key: 'Content-Type', value: 'application/json' }],
  parameters: [],
  body: '',
  mqtt: createDefaultMqttConfig(),
  ws: createDefaultWsConfig(),
  folderId,
  hasUnsavedChanges: false,
});

const IoTDataPushPlatform: React.FC<IoTDataPushPlatformProps> = ({
  currentEnvironmentId: propEnvironmentId,
  canManageGlobalParams = false,
  onEnvironmentsChanged,
}) => {
  const [form] = Form.useForm();
  const [activeTabId, setActiveTabId] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [sendLoading, setSendLoading] = useState(false);
  const [testLoading, setTestLoading] = useState(false);
  const [saveLoading, setSaveLoading] = useState(false);
  const [headers, setHeaders] = useState<HeaderItem[]>([{ key: 'Content-Type', value: 'application/json' }]);
  const [parameters, setParameters] = useState<ParameterItem[]>([]);
  const [savedRequests, setSavedRequests] = useState<SavedRequest[]>([]);
  const [_saveRequestName, setSaveRequestName] = useState('');
  const [tabs, setTabs] = useState<Tab[]>([]);
  const [folders, setFolders] = useState<SavedRequestFolder[]>([]);
  const [selectedFolderId, setSelectedFolderId] = useState<number | null | undefined>(undefined);
  const [environments, setEnvironments] = useState<Environment[]>([{ id: 'env-1', name: '默认环境', parameters: [] }]);
  const [currentEnvironmentId, setCurrentEnvironmentId] = useState<string>(() => {
    return localStorage.getItem('currentEnvironmentId') || 'env-1';
  });
  const [isGlobalParamsModalVisible, setIsGlobalParamsModalVisible] = useState(false);
  const [showPushLog, setShowPushLog] = useState(false);
  const [isNewRequestModalVisible, setIsNewRequestModalVisible] = useState(false);
  const [newItemMode, setNewItemMode] = useState<'type' | 'folder'>('type');
  const [newItemFolderName, setNewItemFolderName] = useState('');
  const [newItemFolderParent, setNewItemFolderParent] = useState<number>(0);
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [postExtractions, setPostExtractions] = useState<ExtractionRule[]>([]);
  /** 前置步骤：发请求取变量 / 直接设置变量 */
  const [preSteps, setPreSteps] = useState<PreStep[]>([]);
  /** 前置参数列表（批量执行） */
  const [batchParams, setBatchParams] = useState<BatchParams>({ columns: [], rows: [], on_error: 'continue' });
  /** 保存请求时选择归属文件夹的弹窗：visible + 当前选中值（null = 未分类） */
  const [saveFolderModalVisible, setSaveFolderModalVisible] = useState(false);
  const [saveFolderChoice, setSaveFolderChoice] = useState<number | null>(null);
  const saveFolderResolveRef = useRef<((folderId: number | null | undefined) => void) | null>(null);

  const jsonExtensions = useMemo(() => [json()], []);

  const activeTab = useMemo(() => tabs.find(t => t.id === activeTabId), [tabs, activeTabId]);
  const response = activeTab?.response || null;
  const responseTime = activeTab?.responseTime || 0;
  const isMqtt = activeTab?.protocol === 'mqtt';
  const isWs = activeTab?.protocol === 'websocket';
  const mqttConfig = normalizeMqttConfig(activeTab?.mqtt);
  const wsConfig = normalizeWsConfig(activeTab?.ws);
  const wsResult = activeTab?.wsResult ?? null;
  const wsMessages = wsResult?.messages ?? [];
  const wsSucceeded = !!wsResult && wsResult.success !== false && wsResult.connected && !wsResult.error;

  const handleBodyChange = useCallback((value: string) => {
    form.setFieldsValue({ body: value });
    setTabs(prevTabs => prevTabs.map(tab =>
      tab.id === activeTabId ? applyTabChange(tab, { body: value }) : tab
    ));
  }, [activeTabId]);

  // --- Response Split（请求区 / 响应区上下拖拽） ---

  const mainRef = useRef<HTMLDivElement>(null);
  const tabstripListRef = useRef<HTMLDivElement>(null);
  const [responseSplit, setResponseSplit] = useState<number>(() => {
    const saved = Number(localStorage.getItem('iot.responseSplit'));
    return Number.isFinite(saved) && saved >= 15 && saved <= 85 ? saved : 50;
  });
  const [responseCollapsed, setResponseCollapsed] = useState(false);
  const [isDraggingResponse, setIsDraggingResponse] = useState(false);

  const handleResponseDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsDraggingResponse(true);
  }, []);

  useEffect(() => {
    if (!isDraggingResponse) return;
    const rect = mainRef.current?.getBoundingClientRect();

    const handleMouseMove = (e: MouseEvent) => {
      if (!rect || rect.height <= 0) return;
      const fromBottom = rect.bottom - e.clientY;
      // 请求区至少保留 220px（标签条 + 名称行 + 地址行 + 配置区），避免把编辑器压没
      const maxPercent = Math.max(15, Math.min(85, ((rect.height - 220) / rect.height) * 100));
      setResponseSplit(Math.max(15, Math.min(maxPercent, (fromBottom / rect.height) * 100)));
    };

    const handleMouseUp = () => {
      setIsDraggingResponse(false);
      setResponseSplit(prev => {
        localStorage.setItem('iot.responseSplit', String(Math.round(prev)));
        return prev;
      });
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDraggingResponse]);

  // --- Data Fetching ---

  useEffect(() => {
    fetchSavedRequests();
    fetchFolders();
  }, []);

  useEffect(() => {
    if (canManageGlobalParams) {
      fetchGlobalParameters();
    }
  }, [canManageGlobalParams]);

  useEffect(() => {
    if (propEnvironmentId && propEnvironmentId !== currentEnvironmentId) {
      setCurrentEnvironmentId(propEnvironmentId);
    }
  }, [propEnvironmentId]);

  // Initialize default tab
  useEffect(() => {
    const defaultTab = buildNewTab('http');
    setTabs([markTabClean(defaultTab)]);
    setActiveTabId(defaultTab.id);
  }, []);

  // 激活标签变化时滚动到可视区域，保证新增按钮不会被挤走
  useEffect(() => {
    if (!activeTabId) return;
    const list = tabstripListRef.current;
    if (!list) return;
    const target = list.querySelector<HTMLElement>(`[data-tab-id="${activeTabId}"]`);
    target?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }, [activeTabId, tabs.length]);

  // Ctrl+S / Cmd+S 快捷保存当前标签页请求配置
  const saveHandlerRef = useRef<() => void>(() => {});
  saveHandlerRef.current = () => {
    if (activeTabId && !saveLoading) {
      void handleSaveRequest();
    }
  };
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) {
        e.preventDefault();
        saveHandlerRef.current();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  const getCurrentEnvironment = (): Environment => {
    return environments.find(env => env.id === currentEnvironmentId) || environments[0];
  };

  // --- Environment Management ---

  const handleEnvironmentParameterChange = (index: number, field: 'key' | 'value', value: string) => {
    setEnvironments(prev => {
      const envIndex = prev.findIndex(env => env.id === currentEnvironmentId);
      if (envIndex === -1) return prev;
      const newParameters = [...prev[envIndex].parameters];
      newParameters[index] = { ...newParameters[index], [field]: value };
      const newEnvironments = [...prev];
      newEnvironments[envIndex] = { ...newEnvironments[envIndex], parameters: newParameters };
      return newEnvironments;
    });
  };

  const handleSaveEnvironmentParameter = async () => {
    const env = environments.find(item => item.id === currentEnvironmentId);
    if (env) {
      await saveEnvironmentToBackend(env);
      onEnvironmentsChanged?.();
    }
  };

  const handleAddEnvironmentParameter = async () => {
    const newEnvironments = [...environments];
    const envIndex = newEnvironments.findIndex(env => env.id === currentEnvironmentId);
    if (envIndex !== -1) {
      const newParameters = [...newEnvironments[envIndex].parameters, { key: '', value: '' }];
      newEnvironments[envIndex] = { ...newEnvironments[envIndex], parameters: newParameters };
      setEnvironments(newEnvironments);
      await saveEnvironmentToBackend(newEnvironments[envIndex]);
      onEnvironmentsChanged?.();
    }
  };

  const handleRemoveEnvironmentParameter = async (index: number) => {
    const newEnvironments = [...environments];
    const envIndex = newEnvironments.findIndex(env => env.id === currentEnvironmentId);
    if (envIndex !== -1) {
      const newParameters = newEnvironments[envIndex].parameters.filter((_, i) => i !== index);
      newEnvironments[envIndex] = { ...newEnvironments[envIndex], parameters: newParameters };
      setEnvironments(newEnvironments);
      await saveEnvironmentToBackend(newEnvironments[envIndex]);
      onEnvironmentsChanged?.();
    }
  };

  const [newEnvironmentName, setNewEnvironmentName] = useState('');
  const [isAddEnvModalVisible, setIsAddEnvModalVisible] = useState(false);

  const handleAddEnvironment = () => {
    setNewEnvironmentName('');
    setIsAddEnvModalVisible(true);
  };

  const handleConfirmAddEnvironment = async () => {
    if (!newEnvironmentName || newEnvironmentName.trim() === '') {
      message.error('环境名称不能为空');
      return;
    }

    const newEnv: Environment = {
      id: `env-${Date.now()}`,
      name: newEnvironmentName.trim(),
      parameters: []
    };

    const savedEnv = await saveEnvironmentToBackend(newEnv);
    if (savedEnv) {
      const updatedEnv = { ...newEnv, id: savedEnv.id.toString() };
      setEnvironments([...environments, updatedEnv]);
      setCurrentEnvironmentId(updatedEnv.id);
      setIsAddEnvModalVisible(false);
      onEnvironmentsChanged?.();
    }
  };

  const handleRemoveEnvironment = async (envId: string) => {
    if (environments.length <= 1) {
      message.warning('至少需要保留一个环境');
      return;
    }

    Modal.confirm({
      title: '确认删除',
      content: '确定要删除这个环境吗？此操作不可撤销。',
      okText: '确定',
      okType: 'danger',
      cancelText: '取消',
      className: 'iot-modal iot-confirm',
      onOk: async () => {
        const deleted = await deleteEnvironmentFromBackend(envId);
        if (deleted) {
          const newEnvironments = environments.filter(env => env.id !== envId);
          setEnvironments(newEnvironments);
          if (currentEnvironmentId === envId) {
            setCurrentEnvironmentId(newEnvironments[0].id);
          }
          onEnvironmentsChanged?.();
        }
      },
    });
  };

  const [editingEnvId, setEditingEnvId] = useState<string | null>(null);
  const [editingEnvName, setEditingEnvName] = useState('');
  /** 正在复制的环境 id（用于复制按钮的 loading 态） */
  const [copyingEnvId, setCopyingEnvId] = useState<string | null>(null);

  const handleEditEnvironmentName = (env: Environment) => {
    setEditingEnvId(env.id);
    setEditingEnvName(env.name);
  };

  const handleSaveEnvironmentName = async () => {
    if (!editingEnvName || editingEnvName.trim() === '') {
      message.error('环境名称不能为空');
      return;
    }

    const envIndex = environments.findIndex(e => e.id === editingEnvId);
    if (envIndex !== -1) {
      const newEnvironments = [...environments];
      newEnvironments[envIndex] = { ...newEnvironments[envIndex], name: editingEnvName.trim() };
      setEnvironments(newEnvironments);
      await saveEnvironmentToBackend(newEnvironments[envIndex]);
      setEditingEnvId(null);
      onEnvironmentsChanged?.();
    }
  };

  const handleCancelEditEnvironmentName = () => {
    setEditingEnvId(null);
    setEditingEnvName('');
  };

  // 复制环境（含全部参数）：一键创建并切换到新环境
  const handleCopyEnvironment = async (env: Environment) => {
    if (!canManageGlobalParams) {
      message.warning('无权限复制环境');
      return;
    }
    // 'env-1' 是尚未落库的本地占位环境，没有真实 id 可复制
    if (!env.id || env.id === 'env-1' || !parseInt(env.id)) {
      message.warning('该环境尚未保存到服务器，无法复制');
      return;
    }

    setCopyingEnvId(env.id);
    try {
      const response = await globalParameterApi.copyEnvironment(parseInt(env.id));
      if (response.code === 200 && response.data) {
        // fetchGlobalParameters 刷新列表并切换当前环境；补写 localStorage 以便刷新后仍停留在新环境
        await fetchGlobalParameters(response.data.id.toString());
        localStorage.setItem('currentEnvironmentId', response.data.id.toString());
        message.success(`环境已复制：${response.data.name}`);
      } else {
        message.error(response.message || '复制环境失败');
      }
    } catch (error) {
      console.error('Failed to copy environment:', error);
      message.error('复制环境失败');
    } finally {
      setCopyingEnvId(null);
    }
  };

  const handleSwitchEnvironment = async (envId: string) => {
    setCurrentEnvironmentId(envId);
    localStorage.setItem('currentEnvironmentId', envId);
    const currentEnv = environments.find(env => env.id === envId);
    if (currentEnv) {
      await saveEnvironmentToBackend({ ...currentEnv, is_default: true });
    }
  };

  // --- Tab ↔ Form Sync ---

  useEffect(() => {
    const tab = tabs.find(t => t.id === activeTabId);
    if (tab) {
      form.setFieldsValue({ method: tab.method, url: tab.url, body: tab.body });
      setHeaders(tab.headers);
      setParameters(tab.parameters);
      setPostExtractions(tab.postExtractions || []);
      setPreSteps(normalizePreSteps(tab.preSteps));
      setBatchParams(normalizeBatchParams(tab.batchParams));
    }
  }, [activeTabId, tabs, form]);

  // --- API Calls ---

  const fetchSavedRequests = async (folderId?: number | null) => {
    try {
      const response = await savedRequestApi.getRequests(folderId);
      if (response.code === 200 && response.data) {
        setSavedRequests(response.data);
      }
    } catch (error) {
      console.error('Failed to fetch saved requests:', error);
      message.error('获取保存的请求配置失败');
    }
  };

  const fetchFolders = async () => {
    try {
      const response = await savedRequestFolderApi.getFolders();
      if (response.code === 200 && response.data) {
        setFolders(response.data);
      }
    } catch (error) {
      console.error('Failed to fetch folders:', error);
    }
  };

  const fetchGlobalParameters = async (envId?: string | null) => {
    try {
      const response = await globalParameterApi.getEnvironments();
      if (response.code === 200 && response.data) {
        const backendEnvironments = response.data.map((env: any) => ({
          id: env.id.toString(),
          name: env.name,
          parameters: env.parameters || []
        }));
        if (backendEnvironments.length > 0) {
          setEnvironments(backendEnvironments);
          // 通知父级刷新它那份 environments 副本(后置提取写入的变量要能立刻看到)
          onEnvironmentsChanged?.();
          if (envId) {
            setCurrentEnvironmentId(envId);
            return;
          }
          const savedEnvId = localStorage.getItem('currentEnvironmentId');
          if (savedEnvId && backendEnvironments.find((env: any) => env.id === savedEnvId)) {
            setCurrentEnvironmentId(savedEnvId);
          } else {
            const defaultEnv = backendEnvironments.find((env: any) => env.is_default) || backendEnvironments[0];
            setCurrentEnvironmentId(defaultEnv.id);
          }
        }
      }
    } catch (error) {
      console.error('Failed to fetch global parameters:', error);
    }
  };

  const saveEnvironmentToBackend = async (environment: any) => {
    if (!canManageGlobalParams) return null;
    try {
      if (parseInt(environment.id)) {
        const response = await globalParameterApi.updateEnvironment(parseInt(environment.id), {
          name: environment.name,
          parameters: environment.parameters,
          is_default: environment.is_default || false
        });
        return response.data;
      } else {
        const response = await globalParameterApi.createEnvironment({
          name: environment.name,
          parameters: environment.parameters,
          is_default: environment.is_default || false
        });
        return response.data;
      }
    } catch (error) {
      console.error('Failed to save environment:', error);
      message.error('保存环境配置失败');
      return null;
    }
  };

  const deleteEnvironmentFromBackend = async (environmentId: string) => {
    if (!canManageGlobalParams) return false;
    try {
      if (parseInt(environmentId)) {
        await globalParameterApi.deleteEnvironment(parseInt(environmentId));
        return true;
      }
      return false;
    } catch (error) {
      console.error('Failed to delete environment:', error);
      message.error('删除环境配置失败');
      return false;
    }
  };

  // --- Header / Param / Extraction Handlers ---

  const methods = ['GET', 'POST', 'PUT', 'DELETE', 'PATCH'];

  const updateCurrentTab = (updates: Partial<Tab>, markClean = false) => {
    setTabs(prevTabs => prevTabs.map(tab => {
      if (tab.id !== activeTabId) return tab;
      if (markClean) return markTabClean({ ...tab, ...updates });
      return applyTabChange(tab, updates);
    }));
  };

  const handleRemoveHeader = (index: number) => {
    const newHeaders = headers.filter((_, i) => i !== index);
    setHeaders(newHeaders);
    updateCurrentTab({ headers: newHeaders });
  };

  const handleHeaderChange = (index: number, field: 'key' | 'value' | 'description', value: string) => {
    const newHeaders = [...headers];
    newHeaders[index] = { ...newHeaders[index], [field]: value };
    setHeaders(newHeaders);
    updateCurrentTab({ headers: newHeaders });
  };

  const handleHeaderToggle = (index: number, enabled: boolean) => {
    const newHeaders = [...headers];
    newHeaders[index] = { ...newHeaders[index], enabled };
    setHeaders(newHeaders);
    updateCurrentTab({ headers: newHeaders });
  };

  const handleHeadersToggleAll = (enabled: boolean) => {
    const newHeaders = headers.map(header => ({ ...header, enabled }));
    setHeaders(newHeaders);
    updateCurrentTab({ headers: newHeaders });
  };

  const handleAddHeader = () => {
    const newHeaders = [...headers, { key: '', value: '' }];
    setHeaders(newHeaders);
    updateCurrentTab({ headers: newHeaders });
  };

  const handleAddParameter = () => {
    const newParameters = [...parameters, { key: '', value: '' }];
    setParameters(newParameters);
    updateCurrentTab({ parameters: newParameters });
  };

  const handleRemoveParameter = (index: number) => {
    const newParameters = parameters.filter((_, i) => i !== index);
    setParameters(newParameters);
    updateCurrentTab({ parameters: newParameters });
  };

  const handleParameterChange = (index: number, field: 'key' | 'value' | 'description', value: string) => {
    const newParameters = [...parameters];
    newParameters[index] = { ...newParameters[index], [field]: value };
    setParameters(newParameters);
    updateCurrentTab({ parameters: newParameters });
  };

  const handleParameterToggle = (index: number, enabled: boolean) => {
    const newParameters = [...parameters];
    newParameters[index] = { ...newParameters[index], enabled };
    setParameters(newParameters);
    updateCurrentTab({ parameters: newParameters });
  };

  const handleParametersToggleAll = (enabled: boolean) => {
    const newParameters = parameters.map(param => ({ ...param, enabled }));
    setParameters(newParameters);
    updateCurrentTab({ parameters: newParameters });
  };

  const handleParameterTypeChange = (index: number, type: 'text' | 'file') => {
    const newParameters = [...parameters];
    newParameters[index] = { ...newParameters[index], type, file: undefined, value: type === 'file' ? '' : (newParameters[index].value || '') };
    setParameters(newParameters);
    updateCurrentTab({ parameters: newParameters });
  };

  const handleParameterFileChange = (index: number, fileResult?: UploadedFileResult) => {
    const newParameters = [...parameters];
    if (fileResult) {
      newParameters[index] = { ...newParameters[index], file: fileResult, value: fileResult.fileId };
    } else {
      newParameters[index] = { ...newParameters[index], file: undefined, value: '' };
    }
    setParameters(newParameters);
    updateCurrentTab({ parameters: newParameters });
  };

  const handleAddExtraction = () => {
    const newExtractions = [...postExtractions, { variable: '', jsonpath: '' }];
    setPostExtractions(newExtractions);
    updateCurrentTab({ postExtractions: newExtractions });
  };

  const handleRemoveExtraction = (index: number) => {
    const newExtractions = postExtractions.filter((_, i) => i !== index);
    setPostExtractions(newExtractions);
    updateCurrentTab({ postExtractions: newExtractions });
  };

  // --- 前置步骤 handlers ---

  const handleAddPreStep = () => {
    // 参数统一在前置参数中设置，新步骤默认为「自己配置」的请求类型
    const newSteps: PreStep[] = [...preSteps, { type: 'request', source: 'custom', method: 'GET', url: '', headers: [], params: [], extracts: [], enabled: true }];
    setPreSteps(newSteps);
    updateCurrentTab({ preSteps: newSteps });
  };

  const handleRemovePreStep = (index: number) => {
    const newSteps = preSteps.filter((_, i) => i !== index);
    setPreSteps(newSteps);
    updateCurrentTab({ preSteps: newSteps });
  };

  const handlePreStepChange = (index: number, updates: Partial<PreStep>) => {
    const newSteps = [...preSteps];
    newSteps[index] = { ...newSteps[index], ...updates };
    setPreSteps(newSteps);
    updateCurrentTab({ preSteps: newSteps });
  };

  const handleAddPreStepExtract = (index: number) => {
    const step = preSteps[index];
    if (!step) return;
    const extracts = [...(step.extracts || []), { key: '', jsonpath: '' }];
    handlePreStepChange(index, { extracts });
  };

  const handleRemovePreStepExtract = (stepIndex: number, extractIndex: number) => {
    const step = preSteps[stepIndex];
    if (!step) return;
    const extracts = (step.extracts || []).filter((_, i) => i !== extractIndex);
    handlePreStepChange(stepIndex, { extracts });
  };

  const handlePreStepExtractChange = (stepIndex: number, extractIndex: number, field: 'key' | 'jsonpath', value: string) => {
    const step = preSteps[stepIndex];
    if (!step) return;
    const extracts = [...(step.extracts || [])];
    extracts[extractIndex] = { ...extracts[extractIndex], [field]: value };
    handlePreStepChange(stepIndex, { extracts });
  };

  const handleExtractionChange = (index: number, field: 'variable' | 'jsonpath', value: string) => {
    const newExtractions = [...postExtractions];
    newExtractions[index] = { ...newExtractions[index], [field]: value };
    setPostExtractions(newExtractions);
    updateCurrentTab({ postExtractions: newExtractions });
  };

  // --- Protocol / MQTT / WebSocket Handlers ---

  const handleMqttChange = (updates: Partial<MqttConfig>) => {
    const tab = tabs.find(t => t.id === activeTabId);
    const current = tab?.mqtt ? normalizeMqttConfig(tab.mqtt) : createDefaultMqttConfig();
    updateCurrentTab({ mqtt: { ...current, ...updates } });
  };

  // --- WebSocket handlers ---

  const handleWsChange = (updates: Partial<WsConfig>) => {
    const tab = tabs.find(t => t.id === activeTabId);
    const current = tab?.ws ? normalizeWsConfig(tab.ws) : createDefaultWsConfig();
    updateCurrentTab({ ws: { ...current, ...updates } });
  };

  const handleWsHeaderChange = (index: number, field: 'key' | 'value' | 'description', value: string) => {
    const current = normalizeWsConfig(activeTab?.ws);
    const next = current.headers.map((header, i) =>
      i === index ? { ...header, [field]: value } : header
    );
    handleWsChange({ headers: next });
  };

  const handleWsHeaderToggle = (index: number, enabled: boolean) => {
    const current = normalizeWsConfig(activeTab?.ws);
    const next = current.headers.map((header, i) =>
      i === index ? { ...header, enabled } : header
    );
    handleWsChange({ headers: next });
  };

  const handleWsHeadersToggleAll = (enabled: boolean) => {
    const current = normalizeWsConfig(activeTab?.ws);
    handleWsChange({ headers: current.headers.map(header => ({ ...header, enabled })) });
  };

  const handleAddWsHeader = () => {
    const current = normalizeWsConfig(activeTab?.ws);
    handleWsChange({ headers: [...current.headers, { key: '', value: '' }] });
  };

  const handleRemoveWsHeader = (index: number) => {
    const current = normalizeWsConfig(activeTab?.ws);
    handleWsChange({ headers: current.headers.filter((_, i) => i !== index) });
  };

  const handleTestWsConnection = async () => {
    const wsConfigValue = normalizeWsConfig(activeTab?.ws);
    const url = wsConfigValue.url.trim();
    if (!url) {
      message.warning('请先填写 WebSocket 地址');
      return;
    }
    setTestLoading(true);
    try {
      const res = await wsApi.testConnection({
        url,
        headers: headersToRecord(wsConfigValue.headers),
        protocols: parseProtocols(wsConfigValue.protocols),
        timeout_seconds: wsConfigValue.timeoutSeconds,
      });
      const result = res.data;
      if (res.code === 200 && result?.connected) {
        message.success(`连接成功（${result.duration_ms}ms）`);
      } else {
        message.error(`连接失败：${result?.message || res.message || '未知错误'}`);
      }
    } catch (error: any) {
      const detail = error.response?.data?.message || error.response?.data?.detail || error.message;
      message.error(`连接失败：${detail || '无法建立 WebSocket 连接'}`);
    } finally {
      setTestLoading(false);
    }
  };

  const handleTestMqttConnection = async () => {
    const mqtt = activeTab?.mqtt ? normalizeMqttConfig(activeTab.mqtt) : createDefaultMqttConfig();
    const broker = composeBrokerAddress(mqtt);
    if (!broker) {
      message.warning('请先填写 Broker 地址');
      return;
    }
    setTestLoading(true);
    try {
      const res = await mqttApi.testConnection({
        broker,
        client_id: mqtt.client_id?.trim() || undefined,
        username: mqtt.username?.trim() || undefined,
        password: mqtt.password || undefined,
        tls: mqtt.tls || undefined,
      });
      const result = res.data;
      if (res.code === 200 && result?.connected) {
        message.success(`连接成功（${result.duration_ms}ms）${result.message ? '：' + result.message : ''}`);
      } else {
        message.error(`连接失败：${result?.message || res.message || '未知错误'}`);
      }
    } catch (error: any) {
      const detail = error.response?.data?.message || error.response?.data?.detail || error.message;
      message.error(`连接失败：${detail || '无法连接到 Broker'}`);
    } finally {
      setTestLoading(false);
    }
  };

  // --- Tab Management ---

  /**
   * 新建标签页。folderId 只在「明确在某个文件夹下新建」时传入：
   * 侧栏文件夹「+」→ 该文件夹；侧栏「未分类 +」→ null；
   * 其他入口不传 → 保存时让用户选择归属文件夹（默认未分类）。
   */
  const addNewTab = (protocol: PushProtocol, folderId?: number | null) => {
    const newTab = buildNewTab(protocol, folderId);
    setTabs(prevTabs => [...prevTabs, markTabClean(newTab)]);
    setActiveTabId(newTab.id);
  };

  const closeTab = (tabId: string) => {
    if (tabs.length === 1) {
      message.warning('至少需要保留一个标签页');
      return;
    }

    const tabToClose = tabs.find(tab => tab.id === tabId);
    if (tabToClose?.hasUnsavedChanges) {
      Modal.confirm({
        title: '确认关闭',
        content: '当前标签页有未保存的更改，确定要关闭吗？',
        okText: '不保存并关闭',
        okType: 'danger',
        cancelText: '取消',
        className: 'iot-modal iot-confirm',
        onOk: () => {
          const newTabs = tabs.filter(tab => tab.id !== tabId);
          setTabs(newTabs);
          if (tabId === activeTabId) setActiveTabId(newTabs[0].id);
        },
      });
    } else {
      const newTabs = tabs.filter(tab => tab.id !== tabId);
      setTabs(newTabs);
      if (tabId === activeTabId) setActiveTabId(newTabs[0].id);
    }
  };

  const createTabFromSavedRequest = (request: SavedRequest) => {
    const existingTab = tabs.find(t => t.savedRequestId === request.id);
    if (existingTab) {
      setActiveTabId(existingTab.id);
      setSaveRequestName(request.name);
      return;
    }
    const protocol: PushProtocol =
      request.protocol === 'mqtt' ? 'mqtt' : request.protocol === 'websocket' ? 'websocket' : 'http';
    const mqttConfig = normalizeMqttConfig(request.mqtt_config);
    // WS 配置兜底：优先用 ws_config，缺失时用顶层 url/headers 重建，保证不丢 URL
    const wsConfig = normalizeWsConfig(
      request.ws_config ?? {
        url: request.url,
        headers: (request.headers || []).map(h => ({ key: h.key, value: h.value })),
        maxMessages: 50,
        timeoutSeconds: 10,
      }
    );
    const newTab: Tab = {
      id: `tab-${Date.now()}-${++tabCounter}`,
      name: request.name,
      protocol,
      method: request.method,
      // MQTT 地址落在 mqtt.broker、WS 地址落在 ws.url，HTTP 才用 url
      url: protocol === 'http' ? request.url : '',
      headers: request.headers,
      parameters: request.parameters || [],
      body: protocol === 'http' ? request.body : '',
      mqtt: mqttConfig,
      ws: wsConfig,
      // MQTT / WS 没有 HTTP 响应，历史遗留的 response 不带到新标签页
      response: undefined,
      responseTime: 0,
      mqttResult: null,
      wsResult: null,
      postExtractions: (request as any).post_extractions || [],
      preSteps: normalizePreSteps((request as any).pre_steps),
      batchParams: normalizeBatchParams((request as any).batch_params),
      savedRequestId: request.id,
      folderId: request.folder_id ?? null,
      hasUnsavedChanges: false
    };
    setTabs(prevTabs => [...prevTabs, markTabClean(newTab)]);
    setActiveTabId(newTab.id);
    // 打开已保存请求时默认定位到“请求体”页签（HTTP）/“Payload”（MQTT）；WS 无请求体，保持连接配置
    setConfigTabs(prev => ({
      ...prev,
      [newTab.id]: protocol === 'websocket' ? 'ws' : 'body',
    }));
    setSaveRequestName(request.name);
  };

  // --- Send Request ---

  // MQTT 发布：把发布结果转成响应区可展示的结构
  const handleMqttPublish = async () => {
    const tab = tabs.find(t => t.id === activeTabId);
    const mqtt = tab?.mqtt ? normalizeMqttConfig(tab.mqtt) : createDefaultMqttConfig();
    const broker = composeBrokerAddress(mqtt);
    const topic = mqtt.topic.trim();

    if (!broker) {
      message.warning('请先填写 Broker 地址');
      return;
    }
    if (!topic) {
      message.warning('请先填写 Topic');
      return;
    }

    const envId = canManageGlobalParams ? parseInt(currentEnvironmentId) || null : null;
    // 本地参数按后端 build_param_map 约定传 [{key,value}]，与环境参数合并后替换 {{variable}}
    const envParams = getCurrentEnvironment().parameters
      .filter(param => param.key && param.value)
      .map(param => ({ key: param.key, value: param.value }));

    setSendLoading(true);
    try {
      const res = await mqttApi.publish({
        broker,
        topic,
        payload: mqtt.payload || '',
        qos: mqtt.qos,
        retain: mqtt.retain,
        client_id: mqtt.client_id?.trim() || undefined,
        username: mqtt.username?.trim() || undefined,
        password: mqtt.password || undefined,
        tls: mqtt.tls || undefined,
        environment_id: envId,
        parameters: envParams.length > 0 ? envParams : undefined,
      });

      const result: MqttPublishResult = res.data ?? {
        success: false,
        broker,
        topic,
        qos: mqtt.qos,
        retain: mqtt.retain,
        payload: mqtt.payload || '',
        duration_ms: 0,
        error: res.message || '发布失败',
      };

      // 发布结果不回写 mqtt 配置（避免 trim 造成的"未保存"误判），只更新展示状态
      updateCurrentTab({
        mqttResult: result,
        responseTime: result.duration_ms || 0,
      });

      if (res.code === 200 && result.success) {
        message.success(`发布成功（${result.duration_ms}ms）→ ${result.topic || topic}`);
      } else {
        message.error(`发布失败: ${result.error || res.message || '未知错误'}`);
      }
    } catch (error: any) {
      const detail = error.response?.data?.message || error.response?.data?.detail || error.message || '未知错误';
      updateCurrentTab({
        mqttResult: {
          success: false,
          broker,
          topic,
          qos: mqtt.qos,
          retain: mqtt.retain,
          payload: mqtt.payload || '',
          duration_ms: 0,
          error: String(detail),
        },
        responseTime: 0,
      });
      message.error(`发布失败: ${detail}`);
    } finally {
      setSendLoading(false);
    }
  };

  // WebSocket 连接：连接并采集消息，结果写入标签页供消息流面板展示
  const handleWsConnect = async () => {
    const wsValue = normalizeWsConfig(activeTab?.ws);
    const url = wsValue.url.trim();
    if (!url) {
      message.warning('请先填写 WebSocket 地址');
      return;
    }

    const envId = canManageGlobalParams ? parseInt(currentEnvironmentId) || null : null;
    const envParams = getCurrentEnvironment().parameters
      .filter(param => param.key && param.value)
      .map(param => ({ key: param.key, value: param.value }));

    setSendLoading(true);
    const startedAt = Date.now();
    try {
      const res = await wsApi.connect({
        url,
        headers: headersToRecord(wsValue.headers),
        environment_id: envId,
        parameters: envParams.length > 0 ? envParams : undefined,
        max_messages: wsValue.maxMessages,
        timeout_seconds: wsValue.timeoutSeconds,
        protocols: parseProtocols(wsValue.protocols),
        send_message: wsValue.sendMessage?.trim() || undefined,
      });

      const result: WsConnectResult = res.data ?? {
        success: false,
        url,
        connected: false,
        messages: [],
        received: 0,
        duration_ms: Date.now() - startedAt,
        closed_by: 'error',
        error: res.message || '连接失败',
      };

      updateCurrentTab({ wsResult: result, responseTime: result.duration_ms || 0 });

      if (res.code === 200 && result.success !== false && result.connected) {
        message.success(`连接成功，收到 ${result.received} 条消息（${result.duration_ms}ms）`);
      } else if (res.code === 200 && result.connected) {
        // 连接成功但被服务端关闭 / 超时，属于正常结束
        message.info(`连接已结束（${result.closed_by}），收到 ${result.received} 条消息`);
      } else {
        message.error(`连接失败: ${result.error || res.message || '未知错误'}`);
      }
    } catch (error: any) {
      const detail = error.response?.data?.message || error.response?.data?.detail || error.message || '未知错误';
      updateCurrentTab({
        wsResult: {
          success: false,
          url,
          connected: false,
          messages: [],
          received: 0,
          duration_ms: Date.now() - startedAt,
          closed_by: 'error',
          error: String(detail),
        },
        responseTime: 0,
      });
      message.error(`连接失败: ${detail}`);
    } finally {
      setSendLoading(false);
    }
  };

  const handleSendRequest = async () => {
    // MQTT 分支：无 URL/请求体概念，走独立发布流程
    if (activeTab?.protocol === 'mqtt') {
      await handleMqttPublish();
      return;
    }
    // WebSocket 分支：连接并采集消息
    if (activeTab?.protocol === 'websocket') {
      await handleWsConnect();
      return;
    }
    const requestUrl = String(form.getFieldValue('url') ?? activeTab?.url ?? '').trim();
    if (!requestUrl) {
      form.scrollToField('url');
      message.warning('请输入请求 URL');
      return;
    }
    try {
      const values = await form.validateFields();
      const { method, url, body } = values;

      setSendLoading(true);
      const startTime = Date.now();

      const processedHeaders = headers.reduce((acc, header) => {
        if (header.enabled !== false && header.key && header.value) acc[header.key] = header.value;
        return acc;
      }, {} as Record<string, string>);

      const processedParams = parameters.reduce((acc, param) => {
        if (param.enabled !== false && param.key && param.value) acc[param.key] = param.value;
        return acc;
      }, {} as Record<string, string>);

      const fileParams: Array<{ key: string; fileId: string; fileName: string }> = [];
      for (const param of parameters) {
        if (param.enabled !== false && param.type === 'file' && param.key && param.file) {
          fileParams.push({ key: param.key, fileId: param.file.fileId, fileName: param.file.fileName });
        }
      }

      let requestData: any = undefined;
      if (['POST', 'PUT', 'PATCH'].includes(method) && body) {
        try { requestData = parseJsonWithComments(body); } catch { requestData = body; }
      }

      const envId = canManageGlobalParams ? parseInt(currentEnvironmentId) || null : null;
      const proxyResponse = await proxyApi.forwardRequest({
        url, method, headers: processedHeaders, data: requestData,
        params: processedParams,
        file_params: fileParams.length > 0 ? fileParams : undefined,
        environment_id: envId,
        pre_steps: preSteps.length > 0 ? preSteps : undefined,
        batch_params: batchParams.columns.length > 0 && batchParams.rows.length > 0 ? batchParams : undefined,
      });

      const endTime = Date.now();
      const axiosResponse = {
        status: proxyResponse.status_code,
        statusText: '',
        headers: proxyResponse.headers,
        data: proxyResponse.data,
        // 前置步骤执行记录：响应区「前置步骤」页签展示请求/响应摘要
        pre_steps: (proxyResponse as any).pre_steps || [],
      };

      updateCurrentTab({ method, url, body, headers, parameters, response: axiosResponse, responseTime: endTime - startTime }, true);

      // 批量模式：只弹一条汇总提示
      const batchInfo = (proxyResponse as any).batch;
      if (batchInfo) {
        const stoppedText = batchInfo.stopped ? ' （已中止）' : '';
        if (batchInfo.failed > 0) {
          message.warning(
            '批量执行完成：共 ' + batchInfo.total + ' 项，成功 ' + batchInfo.passed + ' 项，失败 ' + batchInfo.failed + ' 项' + stoppedText
          );
        } else {
          message.success(
            '批量执行完成：共 ' + batchInfo.total + ' 项，全部成功' + stoppedText
          );
        }
        return;
      }

      message.success('请求成功');

      if (canManageGlobalParams && postExtractions.length > 0 && axiosResponse.data && envId) {
        try {
          const extractResponse = await globalParameterApi.extractAndSaveVariables({
            environment_id: envId,
            response_data: axiosResponse.data,
            extractions: postExtractions.filter(e => e.variable && e.jsonpath),
          });
          if (extractResponse.code === 200 && extractResponse.data) {
            const extracted = extractResponse.data as unknown as Record<string, string>;
            const names = Object.entries(extracted).map(([k, v]) => k + '=' + v).join(', ');
            message.success('变量已提取: ' + names);
            // 重新拉取环境列表:既刷新本组件,也通知父级刷新,保证新变量各处可见
            await fetchGlobalParameters(String(envId));
          }
        } catch (extractError: any) {
          const detail = extractError.response?.data?.detail || extractError.message;
          message.error(`提取失败: ${detail}`);
        }
      }
    } catch (error: any) {
      if (error.response) {
        if (error.response.data) {
          const proxyError = error.response.data;
          updateCurrentTab({
            response: { status: proxyError.status_code || error.response.status, statusText: '', headers: proxyError.headers || {}, data: proxyError.data || error.response.data },
            responseTime: 0
          });
          message.error(`请求失败: ${proxyError.status_code || error.response.status} ${proxyError.detail || 'Unknown error'}`);
        } else {
          updateCurrentTab({ response: error.response, responseTime: 0 });
          message.error(`请求失败: ${error.response.status} ${error.response.statusText}`);
        }
      } else if (error.request) {
        message.error('请求失败: 没有收到响应');
      } else {
        message.error(`请求失败: ${error.message}`);
      }
    } finally {
      setSendLoading(false);
    }
  };

  // --- Save Request ---

  /**
   * 弹出「选择保存位置」对话框，返回用户选中的文件夹 id。
   * - number：具体文件夹
   * - null：未分类（用户不选 / 明确选未分类）
   * - undefined：用户取消（放弃本次保存）
   */
  const promptSaveFolder = (): Promise<number | null | undefined> => {
    // 上一次未关闭的弹窗先结算，避免 Promise 悬挂
    saveFolderResolveRef.current?.(undefined);
    setSaveFolderChoice(null);
    setSaveFolderModalVisible(true);
    return new Promise<number | null | undefined>((resolve) => {
      saveFolderResolveRef.current = resolve;
    });
  };

  const closeSaveFolderModal = (folderId: number | null | undefined) => {
    setSaveFolderModalVisible(false);
    const resolve = saveFolderResolveRef.current;
    saveFolderResolveRef.current = null;
    if (resolve) resolve(folderId);
  };

  const handleSaveRequest = async () => {
    try {
      const currentTab = tabs.find(t => t.id === activeTabId);
      const protocol: PushProtocol =
        currentTab?.protocol === 'mqtt' ? 'mqtt' : currentTab?.protocol === 'websocket' ? 'websocket' : 'http';
      // MQTT / WS 请求不校验 HTTP URL 字段（表单里 URL 输入框被 Broker/Topic 或 WS URL 替代）
      const isHttp = protocol === 'http';
      const values = isHttp
        ? await form.validateFields()
        : {} as { method?: string; url?: string; body?: string };
      const method = isHttp ? values.method : (currentTab?.method || 'GET');
      const url = isHttp ? values.url : '';
      const body = isHttp ? values.body : '';

      const requestName = currentTab?.name?.trim() || '新请求';

      // 保存目标以"当前标签页"的 savedRequestId 为准。
      // 不能用组件级 state 判断：切换标签页后，同一次渲染里读到的仍是上一个标签页的值，
      // 会导致保存写到错误的记录（标签页 id 一直不更新）。
      const targetSavedId = currentTab?.savedRequestId ?? null;

      const mqttConfig = currentTab?.mqtt ? normalizeMqttConfig(currentTab.mqtt) : createDefaultMqttConfig();
      const wsConfigValue = currentTab?.ws ? normalizeWsConfig(currentTab.ws) : createDefaultWsConfig();

      // —— 归属文件夹 ——
      // 1) 已保存的请求：保留它当前所在的文件夹（侧栏可能已拖拽调整，不该被保存动作改掉）；
      // 2) 新建的请求：用标签页创建时绑定的文件夹（文件夹上「+」新建时才有）；
      // 3) 没有绑定（比如标签条「+」或「新建」对话框创建）：弹窗让用户选择，
      //    不选择则落到「未分类」。这里刻意不再用侧栏当前选中的文件夹做默认值。
      const existingSaved = targetSavedId != null ? savedRequests.find(r => r.id === targetSavedId) : undefined;
      let folderIdForSave: number | null;
      if (existingSaved) {
        folderIdForSave = existingSaved.folder_id ?? null;
      } else if (currentTab?.folderId !== undefined) {
        folderIdForSave = currentTab.folderId;
      } else {
        const pickedFolderId = await promptSaveFolder();
        // undefined = 用户关闭了弹窗，视为取消本次保存
        if (pickedFolderId === undefined) return;
        folderIdForSave = pickedFolderId;
      }

      setSaveLoading(true);

      const payload: any = {
        name: requestName,
        method, url, headers, parameters, body,
        post_extractions: postExtractions,
        pre_steps: preSteps,
        batch_params: batchParams,
        folder_id: folderIdForSave,
        protocol,
        // HTTP / MQTT 请求不写 ws_config，避免污染历史数据
        mqtt_config: isMqtt ? mqttConfig : null,
        ws_config: protocol === 'websocket' ? wsConfigValue : null,
      };

      const applySaved = (saved: SavedRequest, created: boolean) => {
        setSavedRequests(prev => prev.some(r => r.id === saved.id)
          ? prev.map(r => (r.id === saved.id ? saved : r))
          : [saved, ...prev]);
        setTabs(prevTabs => prevTabs.map(tab =>
          tab.id === activeTabId ? markTabClean({ ...tab, name: requestName, protocol, method, url, headers, parameters, body, postExtractions, mqtt: mqttConfig, ws: wsConfigValue, savedRequestId: saved.id, folderId: folderIdForSave }) : tab
        ));
        message.success(created ? '请求配置已保存' : '请求配置已更新');
      };

      if (targetSavedId != null) {
        const response = await savedRequestApi.updateRequest(targetSavedId, payload);
        if (response.code === 200 && response.data) {
          applySaved(response.data, false);
        } else if (response.code === 404) {
          // 目标记录已被删除，转为新建，避免标签页 id 指向不存在的记录
          const createResponse = await savedRequestApi.createRequest(payload as any);
          if (createResponse.code === 200 && createResponse.data) {
            applySaved(createResponse.data, true);
          } else {
            message.error(createResponse.message || '保存请求配置失败');
          }
        } else {
          message.error(response.message || '保存请求配置失败');
        }
      } else {
        const response = await savedRequestApi.createRequest(payload as any);
        if (response.code === 200 && response.data) {
          applySaved(response.data, true);
        } else {
          message.error(response.message || '保存请求配置失败');
        }
      }
    } catch (error) {
      console.error('Save request failed:', error);
      message.error('保存请求配置失败');
    } finally {
      // 侧栏树需要全量数据才能正确分组，保存后统一拉全量，避免分组错乱
      fetchSavedRequests();
      setSaveLoading(false);
    }
  };

  const handleDeleteSavedRequest = async (id: number) => {
    Modal.confirm({
      title: '确认删除',
      content: '确定要删除这个保存的请求配置吗？此操作不可撤销。',
      okText: '确定',
      okType: 'danger',
      cancelText: '取消',
      className: 'iot-modal iot-confirm',
      onOk: async () => {
        try {
          setLoading(true);
          const response = await savedRequestApi.deleteRequest(id);
          if (response.code === 200) {
            setSavedRequests(savedRequests.filter(r => r.id !== id));
            message.success('请求配置已删除');
          } else {
            message.error(response.message || '删除请求配置失败');
          }
        } catch (error) {
          console.error('Delete request failed:', error);
          message.error('删除请求配置失败');
        } finally {
          setLoading(false);
        }
      },
    });
  };

  const handleCopySavedRequest = async (record: SavedRequest) => {
    try {
      setLoading(true);
      const response = await savedRequestApi.createRequest({
        name: `${record.name}_copy`,
        method: record.method,
        url: record.url,
        headers: record.headers,
        parameters: record.parameters,
        body: record.body,
        post_extractions: (record as any).post_extractions,
        // 副本跟随原请求归属：原请求在哪个文件夹，副本就落在哪个文件夹（未分类则同为未分类）
        folder_id: record.folder_id ?? null,
        protocol: record.protocol === 'mqtt' ? 'mqtt' : record.protocol === 'websocket' ? 'websocket' : 'http',
        mqtt_config: record.protocol === 'mqtt' ? normalizeMqttConfig(record.mqtt_config) : null,
        ws_config: record.protocol === 'websocket' ? normalizeWsConfig(record.ws_config) : null,
      } as any);
      if (response.code === 200 && response.data) {
        setSavedRequests([response.data, ...savedRequests]);
        message.success('请求配置已复制');
      }
    } catch (error) {
      console.error('Copy request failed:', error);
      message.error('复制请求配置失败');
    } finally {
      setLoading(false);
    }
  };

  // --- Folder Handlers ---

  // 侧栏树一次性取全量请求，这里只保留选中态（新建请求归属该文件夹）
  const handleSelectFolder = (folderId: number | null | undefined) => {
    setSelectedFolderId(folderId);
  };

  const handleCreateFolder = async (name: string, parentId: number | null): Promise<boolean> => {
    if (!name.trim()) return false;
    try {
      const payload: any = { name: name.trim() };
      if (parentId) payload.parent_id = parentId;
      const response = await savedRequestFolderApi.createFolder(payload);
      if (response.code === 200 && response.data) {
        fetchFolders();
        message.success('文件夹已创建');
        return true;
      }
      return false;
    } catch (error) {
      console.error('Create folder failed:', error);
      message.error('创建文件夹失败');
      return false;
    }
  };

  const handleRenameFolder = async (folderId: number, name: string): Promise<boolean> => {
    if (!name.trim()) return false;
    try {
      const response = await savedRequestFolderApi.updateFolder(folderId, { name: name.trim() });
      if (response.code === 200 && response.data) {
        fetchFolders();
        message.success('文件夹已重命名');
        return true;
      }
      return false;
    } catch (error) {
      console.error('Rename folder failed:', error);
      message.error('重命名文件夹失败');
      return false;
    }
  };

  const handleDeleteFolder = async (folderId: number) => {
    Modal.confirm({
      title: '确认删除文件夹',
      content: '确定要删除此文件夹吗？文件夹必须为空才能删除。',
      okText: '确定',
      okType: 'danger',
      cancelText: '取消',
      className: 'iot-modal iot-confirm',
      onOk: async () => {
        try {
          const response = await savedRequestFolderApi.deleteFolder(folderId);
          if (response.code === 200) {
            if (selectedFolderId === folderId) setSelectedFolderId(undefined);
            fetchFolders();
            message.success('文件夹已删除');
          } else {
            message.error(response.message || '删除文件夹失败');
          }
        } catch (error) {
          console.error('Delete folder failed:', error);
          message.error('删除文件夹失败');
        }
      },
    });
  };

  /** 重命名已保存的请求（侧栏行内编辑，直接落库） */
  const handleRenameRequest = async (id: number, name: string): Promise<boolean> => {
    const trimmed = name.trim();
    if (!trimmed) return false;
    try {
      const response = await savedRequestApi.updateRequest(id, { name: trimmed });
      if (response.code === 200) {
        setSavedRequests(prev => prev.map(r => (r.id === id ? { ...r, name: trimmed } : r)));
        // 已打开的标签页同步名称，避免侧栏与标签页不一致
        setTabs(prev => prev.map(tab =>
          tab.savedRequestId === id ? { ...tab, name: trimmed } : tab
        ));
        message.success('请求已重命名');
        return true;
      }
      message.error(response.message || '重命名请求失败');
      return false;
    } catch (error) {
      console.error('Rename request failed:', error);
      message.error('重命名请求失败');
      return false;
    }
  };

  // --- Postman 风格文件夹操作 ---

  /** 新建请求：在指定文件夹内直接新建一个 HTTP 标签页，并将其作为保存归属 */
  const handleAddRequestInFolder = (folderId: number | null) => {
    setSelectedFolderId(folderId);
    addNewTab('http', folderId);
  };

  /** 创建副本：复制文件夹（含层级关系），名称追加「副本」 */
  const handleDuplicateFolder = async (folderId: number) => {
    const source = findFolderById(folders, folderId);
    if (!source) return;
    try {
      setLoading(true);
      const response = await savedRequestFolderApi.createFolder({
        name: `${source.name} 副本`,
        parent_id: source.parent_id ?? null,
      });
      if (response.code === 200) {
        await fetchFolders();
        message.success('文件夹已复制');
      } else {
        message.error(response.message || '复制文件夹失败');
      }
    } catch (error) {
      console.error('Duplicate folder failed:', error);
      message.error('复制文件夹失败');
    } finally {
      setLoading(false);
    }
  };

  /** 排序：按名称重排同级文件夹（本地排序，不改后端数据） */
  const handleSortFolder = (folderId: number, direction: 'asc' | 'desc') => {
    setFolders((prev) => sortFolderTree(prev, folderId, direction));
  };

  /**
   * 拖拽后保存布局：把受影响父级的完整顺序提交后端，再重拉侧栏数据。
   * 这里不做乐观更新——后端按列表下标写回 sort_order 并同步归属，
   * 重拉一次即可拿到权威顺序，同时把「拖进别的文件夹」的结果同步到请求列表。
   */
  const handleSaveLayout = async (groups: SavedRequestLayoutGroup[]): Promise<boolean> => {
    if (groups.length === 0) return true;
    try {
      setLoading(true);
      const response = await savedRequestFolderApi.saveLayout(groups);
      if (response.code !== 200) {
        message.error(response.message || '保存排序失败');
        return false;
      }
      await Promise.all([fetchFolders(), fetchSavedRequests()]);
      return true;
    } catch (error) {
      console.error('Save layout failed:', error);
      message.error('保存排序失败');
      return false;
    } finally {
      setLoading(false);
    }
  };

  // --- 新建（请求类型 / 文件夹，与 Postman 的 New 对话框一致） ---

  /** 上级文件夹可选列表：兼容后端返回嵌套 children 或扁平 parent_id 两种形态 */
  const flatFolderOptions = useMemo(() => {
    const out: Array<{ id: number; label: string }> = [];
    const hasNested = folders.some(folder => (folder.children || []).length > 0);
    if (hasNested) {
      const walk = (nodes: SavedRequestFolder[], depth: number) => {
        nodes.forEach(node => {
          out.push({ id: node.id, label: '　'.repeat(depth) + node.name });
          if (node.children && node.children.length > 0) walk(node.children, depth + 1);
        });
      };
      walk(folders, 0);
    } else {
      const byParent = new Map<number | null, SavedRequestFolder[]>();
      folders.forEach(folder => {
        const key = folder.parent_id ?? null;
        byParent.set(key, [...(byParent.get(key) || []), folder]);
      });
      const walk = (parentId: number | null, depth: number) => {
        (byParent.get(parentId) || []).forEach(node => {
          out.push({ id: node.id, label: '　'.repeat(depth) + node.name });
          walk(node.id, depth + 1);
        });
      };
      walk(null, 0);
      folders.forEach(folder => {
        if (!out.some(item => item.id === folder.id)) out.push({ id: folder.id, label: folder.name });
      });
    }
    return out;
  }, [folders]);

  /** 保存请求弹窗的文件夹选择树（未分类由「清空选择」表达，这里只列真实文件夹） */
  const saveFolderTreeData = useMemo(() => {
    type FolderTreeNode = { title: string; value: number; key: number; children: FolderTreeNode[] };
    const hasNested = folders.some(folder => (folder.children || []).length > 0);
    if (hasNested) {
      const walk = (nodes: SavedRequestFolder[]): FolderTreeNode[] =>
        nodes.map(folder => ({
          title: folder.name,
          value: folder.id,
          key: folder.id,
          children: walk(folder.children || []),
        }));
      return walk(folders);
    }
    const byParent = new Map<number | null, SavedRequestFolder[]>();
    folders.forEach(folder => {
      const key = folder.parent_id ?? null;
      byParent.set(key, [...(byParent.get(key) || []), folder]);
    });
    const walk = (parentId: number | null): FolderTreeNode[] =>
      (byParent.get(parentId) || []).map(folder => ({
        title: folder.name,
        value: folder.id,
        key: folder.id,
        children: walk(folder.id),
      }));
    return walk(null);
  }, [folders]);

  const openNewItemModal = () => {
    setNewItemMode('type');
    setNewItemFolderName('');
    setNewItemFolderParent(0);
    setIsNewRequestModalVisible(true);
  };

  const closeNewItemModal = () => {
    setIsNewRequestModalVisible(false);
    setNewItemMode('type');
    setNewItemFolderName('');
  };

  const handleCreateFolderFromModal = async () => {
    const name = newItemFolderName.trim();
    if (!name) {
      message.warning('请输入文件夹名称');
      return;
    }
    setCreatingFolder(true);
    const created = await handleCreateFolder(name, newItemFolderParent > 0 ? newItemFolderParent : null);
    setCreatingFolder(false);
    if (created) closeNewItemModal();
  };

  // --- Response Status Helper ---

  const getStatusClass = (status: number) => {
    if (status >= 200 && status < 300) return 'is-success';
    if (status >= 400 && status < 500) return 'is-warning';
    return 'is-error';
  };

  // --- Response HTML ---

  const responseBodyRef = useRef<HTMLDivElement>(null);

  const responseText = useMemo(() => {
    if (!response) return '';
    if (typeof response.data === 'string') return response.data;
    try {
      return JSON.stringify(response.data, null, 2);
    } catch {
      return String(response.data ?? '');
    }
  }, [response]);

  const responseSize = useMemo(() => new Blob([responseText]).size, [responseText]);
  const isLargeResponse = responseSize > LARGE_RESPONSE_LIMIT;

  const parsedJsonData = useMemo(() => {
    if (!response || response.data == null) return null;
    const data: any = response.data;
    if (typeof data !== 'string') return data;
    const trimmed = data.trim();
    if (!trimmed) return null;
    try {
      return JSON.parse(trimmed);
    } catch {
      return null;
    }
  }, [response]);

  const responseHtml = useMemo(() => {
    if (isLargeResponse || parsedJsonData == null) return '';
    try {
      return highlightJson(parsedJsonData);
    } catch {
      return '';
    }
  }, [parsedJsonData, isLargeResponse]);

  const rawToolbarNote = useMemo(() => {
    if (isLargeResponse) return parsedJsonData != null ? '响应内容过大，已停用层级折叠视图' : '响应内容过大';
    if (parsedJsonData == null) return '内容不是有效 JSON，以原始文本展示';
    return '';
  }, [isLargeResponse, parsedJsonData]);

  const responseJsonDepth = useMemo(() => {
    if (responseHtml) return measureJsonDepth(parsedJsonData);
    return 0;
  }, [responseHtml, parsedJsonData]);

  const handleResponseTreeClick = (e: React.MouseEvent) => {
    const target = e.target as HTMLElement;
    const toggle = target.closest('.iot-json-toggle');
    const preview = target.closest('.iot-json-preview');
    if (!toggle && !preview) return;
    const node = (toggle || preview)?.closest('.iot-json-node');
    if (!node) return;
    if (toggle) node.classList.toggle('is-open');
    else node.classList.add('is-open');
  };

  const handleJsonCollapseAll = () => {
    responseBodyRef.current?.querySelectorAll('.iot-json-node').forEach(el => el.classList.remove('is-open'));
  };

  const handleJsonExpandAll = () => {
    responseBodyRef.current?.querySelectorAll('.iot-json-node').forEach(el => el.classList.add('is-open'));
  };

  // --- Postman 风格展示态（仅影响渲染，不改动既有数据流） ---

  const [configTabs, setConfigTabs] = useState<Record<string, ConfigTabKey>>({});
  const [responseTabs, setResponseTabs] = useState<Record<string, ResponseTabKey>>({});

  const defaultConfigTab = (protocol?: PushProtocol): ConfigTabKey =>
    protocol === 'mqtt' ? 'mqtt' : protocol === 'websocket' ? 'ws' : 'params';

  const currentConfigTab: ConfigTabKey = activeTabId
    ? (configTabs[activeTabId] || defaultConfigTab(activeTab?.protocol))
    : 'params';

  const setCurrentConfigTab = (key: ConfigTabKey) => {
    if (!activeTabId) return;
    setConfigTabs(prev => ({ ...prev, [activeTabId]: key }));
  };

  const currentResponseTab: ResponseTabKey = activeTabId ? (responseTabs[activeTabId] || 'body') : 'body';

  const setCurrentResponseTab = (key: ResponseTabKey) => {
    if (!activeTabId) return;
    setResponseTabs(prev => ({ ...prev, [activeTabId]: key }));
  };

  const responseHeaders = useMemo<Record<string, string>>(() => {
    const raw = (response as { headers?: unknown } | null)?.headers;
    if (!raw || typeof raw !== 'object') return {};
    const out: Record<string, string> = {};
    Object.entries(raw as Record<string, unknown>).forEach(([key, value]) => {
      if (value == null) return;
      out[key] = Array.isArray(value) ? value.join(', ') : String(value);
    });
    return out;
  }, [response]);

  const formatBytes = (bytes: number): string => {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1024 / 1024).toFixed(2) + ' MB';
  };

  const handleCopyResponse = () => {
    if (isMqtt) {
      const payload = activeTab?.mqttResult;
      if (payload == null) return;
      copyTextToClipboard(JSON.stringify(payload, null, 2)).then((ok) => {
        if (ok) message.success('已复制到剪贴板');
        else message.error('复制失败，请手动选择文本复制');
      });
      return;
    }
    if (isWs) {
      const payload = activeTab?.wsResult;
      if (payload == null) return;
      copyTextToClipboard(JSON.stringify(payload, null, 2)).then((ok) => {
        if (ok) message.success('已复制到剪贴板');
        else message.error('复制失败，请手动选择文本复制');
      });
      return;
    }
    if (!response) return;
    copyTextToClipboard(responseText).then((ok) => {
      if (ok) message.success('已复制响应体内容');
      else message.error('复制失败，请手动选择文本复制');
    });
  };

  /** 前置步骤执行结果：每个步骤的请求摘要 + 响应体 + 提取变量（响应区「前置步骤」页签） */
  const renderPreStepResults = (steps: Array<Record<string, any>>) => {
    if (!steps || steps.length === 0) {
      return (
        <div className="iot-response-empty">
          <span className="iot-response-empty-text">本次推送未执行前置步骤</span>
        </div>
      );
    }
    return (
      <div className="iot-prestep-results">
        {steps.map((step, i) => {
          const ok = step.ok === true;
          const isParams = step.type === 'params';
          const isSetVar = step.type === 'set_variable';
          const responseBody = step.response_data != null
            ? (typeof step.response_data === 'string' ? step.response_data : JSON.stringify(step.response_data, null, 2))
            : '';
          const extracted: Record<string, string> = step.extracted || {};
          return (
            <div
              key={i}
              style={{
                border: '1px solid var(--color-border-light)',
                borderRadius: 8,
                padding: 10,
                marginBottom: 10,
                background: 'var(--color-bg-container, #fff)',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
                <Tag color={ok ? 'green' : 'red'}>{ok ? '成功' : '失败'}</Tag>
                <span style={{ fontWeight: 600, fontSize: 12 }}>步骤 {typeof step.index === 'number' ? step.index + 1 : i + 1}</span>
                {isParams ? (
                  <span style={{ fontSize: 12, color: 'var(--color-text-tertiary)' }}>参数注入</span>
                ) : isSetVar ? (
                  <span style={{ fontSize: 12, color: 'var(--color-text-tertiary)' }}>设置变量</span>
                ) : (
                  <span style={{ fontSize: 12, fontFamily: 'monospace' }}>{step.method || 'GET'} {step.url || ''}</span>
                )}
                {typeof step.status_code === 'number' && step.status_code > 0 && (
                  <Tag color={step.status_code >= 200 && step.status_code < 300 ? 'green' : 'orange'}>{step.status_code}</Tag>
                )}
              </div>
              {isSetVar && (
                <pre className="iot-response-pre" style={{ maxHeight: 80 }}>
                  {(step.key || '(未命名)') + ': ' + String(step.value ?? '')}
                </pre>
              )}
              {isParams && (
                <pre className="iot-response-pre" style={{ maxHeight: 120 }}>
                  {Object.entries(step.params || {}).map(([k, v]) => k + ': ' + String(v)).join('\n') || '(空)'}
                </pre>
              )}
              {!isParams && !isSetVar && step.error && (
                <pre className="iot-response-pre" style={{ maxHeight: 120, color: 'var(--color-error, #ff4d4f)' }}>
                  {step.error}
                </pre>
              )}
              {!isParams && !isSetVar && responseBody && (
                <div>
                  <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-tertiary)', marginBottom: 4 }}>响应体</div>
                  <pre className="iot-response-pre" style={{ maxHeight: 180 }}>{responseBody}</pre>
                </div>
              )}
              {Object.keys(extracted).length > 0 && (
                <div style={{ marginTop: 6 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-tertiary)', marginBottom: 4 }}>提取变量</div>
                  <pre className="iot-response-pre" style={{ maxHeight: 120 }}>
                    {Object.entries(extracted).map(([k, v]) => k + ' = ' + String(v)).join('\n')}
                  </pre>
                </div>
              )}
            </div>
          );
        })}
      </div>
    );
  };

  const renderResponseHeaders = () => {
    const entries = Object.entries(responseHeaders);
    if (entries.length === 0) {
      return (
        <div className="iot-response-empty">
          <span className="iot-response-empty-text">未返回响应头</span>
        </div>
      );
    }
    return (
      <div className="iot-response-headers-table">
        {entries.map(([key, value]) => (
          <div className="iot-response-headers-row" key={key}>
            <span className="iot-response-headers-key">{key}</span>
            <span className="iot-response-headers-value">{value}</span>
          </div>
        ))}
      </div>
    );
  };

  const configTabItems = (): Array<{ key: ConfigTabKey; label: string }> => {
    if (isMqtt) {
      return [
        { key: 'mqtt', label: '连接配置' },
        { key: 'body', label: 'Payload' },
      ];
    }
    if (isWs) {
      return [
        { key: 'ws', label: '连接配置' },
        { key: 'headers', label: '请求头' + (wsConfig.headers.length ? ' (' + wsConfig.headers.length + ')' : '') },
      ];
    }
    const items: Array<{ key: ConfigTabKey; label: string }> = [
      { key: 'pre', label: '前置步骤' + (preSteps.length ? ' (' + preSteps.length + ')' : '') },
      { key: 'batch', label: '前置参数' + ((batchParams.columns.length && batchParams.rows.length) ? ' (' + batchParams.rows.length + ')' : '') },
      { key: 'params', label: '参数' + (parameters.length ? ' (' + parameters.length + ')' : '') },
      { key: 'headers', label: '请求头' + (headers.length ? ' (' + headers.length + ')' : '') },
      { key: 'body', label: '请求体' },
    ];
    if (canManageGlobalParams) {
      items.push({ key: 'extract', label: '后置提取' + (postExtractions.length ? ' (' + postExtractions.length + ')' : '') });
    }
    return items;
  };

  const renderKvTable = (opts: {
    items: Array<{ key: string; value: string; enabled?: boolean; description?: string }>;
    onChange: (index: number, field: 'key' | 'value' | 'description', value: string) => void;
    onRemove: (index: number) => void;
    onAdd: () => void;
    onToggle?: (index: number, enabled: boolean) => void;
    onToggleAll?: (enabled: boolean) => void;
    addLabel: string;
    keyPlaceholder?: string;
    valuePlaceholder?: string;
    withEnabled?: boolean;
    withDescription?: boolean;
    keyWidth?: number;
    renderLeading?: (item: { key: string; value: string; enabled?: boolean; description?: string }, index: number) => React.ReactNode;
    renderValue?: (item: { key: string; value: string; enabled?: boolean; description?: string }, index: number) => React.ReactNode;
  }) => {
    const withEnabled = opts.withEnabled !== false;
    const withDescription = opts.withDescription !== false;
    const allEnabled = opts.items.length > 0 && opts.items.every(item => item.enabled !== false);
    const tableClass = 'iot-kv-table'
      + (withEnabled ? '' : ' iot-kv-table--no-enabled')
      + (withDescription ? '' : ' iot-kv-table--no-desc');
    return (
      <div className={tableClass}>
        <div className="iot-kv-table-head">
          {withEnabled && (
            <Checkbox
              checked={allEnabled}
              indeterminate={!allEnabled && opts.items.some(item => item.enabled !== false)}
              onChange={(e) => opts.onToggleAll && opts.onToggleAll(e.target.checked)}
            />
          )}
          <span>Key</span>
          <span>Value</span>
          {withDescription && <span>描述</span>}
          <span />
        </div>
        {opts.items.length === 0 && (
          <div className="iot-kv-table-empty">暂无内容，点击下方按钮添加</div>
        )}
        {opts.items.map((item, index) => (
          <div className={'iot-kv-table-row' + (item.enabled === false ? ' is-disabled' : '')} key={index}>
            {withEnabled && (
              <Checkbox
                checked={item.enabled !== false}
                onChange={(e) => opts.onToggle && opts.onToggle(index, e.target.checked)}
              />
            )}
            <Space size={6} style={{ width: '100%', minWidth: 0, overflow: 'hidden' }}>
              {opts.renderLeading && opts.renderLeading(item, index)}
              <Input
                size="small"
                placeholder={opts.keyPlaceholder || 'Key'}
                value={item.key}
                onChange={(e) => opts.onChange(index, 'key', e.target.value)}
                style={{ width: opts.keyWidth || 180, flex: 1, minWidth: 0 }}
              />
            </Space>
            <div className="iot-kv-table-value">
              {opts.renderValue ? opts.renderValue(item, index) : (
                <Input
                  size="small"
                  placeholder={opts.valuePlaceholder || 'Value'}
                  value={item.value}
                  onChange={(e) => opts.onChange(index, 'value', e.target.value)}
                />
              )}
            </div>
            {withDescription && (
              <Input
                size="small"
                placeholder="备注（可选）"
                value={item.description || ''}
                onChange={(e) => opts.onChange(index, 'description', e.target.value)}
              />
            )}
            <Button size="small" icon={<MinusOutlined />} className="iot-delete-btn" onClick={() => opts.onRemove(index)} />
          </div>
        ))}
        <Button type="dashed" icon={<PlusOutlined />} onClick={opts.onAdd} className="iot-add-btn" block>
          {opts.addLabel}
        </Button>
      </div>
    );
  };

  // --- Render ---

  return (
    <div className="iot-container">
      <div className="iot-workspace">
        <IoTRequestSidebar
          folders={folders}
          requests={savedRequests}
          loading={loading}
          selectedFolderId={selectedFolderId}
          activeSavedRequestId={activeTab?.savedRequestId}
          onSelectFolder={handleSelectFolder}
          onOpenRequest={createTabFromSavedRequest}
          onCopyRequest={handleCopySavedRequest}
          onDeleteRequest={handleDeleteSavedRequest}
          onCreateFolder={handleCreateFolder}
          onRenameFolder={handleRenameFolder}
          onDeleteFolder={handleDeleteFolder}
          onRefresh={() => { fetchSavedRequests(); fetchFolders(); }}
          onOpenPushLog={() => setShowPushLog(true)}
          onOpenNewItem={openNewItemModal}
          onAddRequest={handleAddRequestInFolder}
          onRenameRequest={handleRenameRequest}
          onDuplicateFolder={handleDuplicateFolder}
          onSortFolder={handleSortFolder}
          onSaveLayout={handleSaveLayout}
        />

        <div ref={mainRef} className="iot-main">
          {/* 请求标签条 */}
          <div className="iot-tabstrip">
            <div ref={tabstripListRef} className="iot-tabstrip-list">
              {tabs.map(tab => (
                <div
                  key={tab.id}
                  data-tab-id={tab.id}
                  className={'iot-tab' + (tab.id === activeTabId ? ' is-active' : '')}
                  onClick={() => setActiveTabId(tab.id)}
                  title={tab.name}
                >
                  {tab.protocol === 'mqtt' ? <MqttBadge /> : tab.protocol === 'websocket' ? <WsBadge /> : <MethodBadge method={tab.method} />}
                  <span className={'iot-tab-name' + (tab.hasUnsavedChanges ? ' iot-tab-unsaved' : '')}>{tab.name}</span>
                  <button
                    className="iot-tab-close"
                    onClick={(e) => { e.stopPropagation(); closeTab(tab.id); }}
                  >
                    &times;
                  </button>
                </div>
              ))}
              <Tooltip title="新建 HTTP 请求" overlayClassName="iot-tooltip" placement="bottom">
                <button
                  type="button"
                  className="iot-tabstrip-add"
                  onClick={() => addNewTab('http')}
                  aria-label="新建 HTTP 请求"
                >
                  <PlusOutlined />
                </button>
              </Tooltip>
            </div>
          </div>

          {/* 请求名 + 协议 + 保存 */}
          <div className="iot-request-header">
            <Tooltip title="请求类型在新建时选定，创建后不可更改" overlayClassName="iot-tooltip">
              <span className="iot-request-protocol">
                {isMqtt ? <MqttBadge size="md" /> : isWs ? <WsBadge size="md" /> : <HttpBadge size="md" />}
              </span>
            </Tooltip>
            <input
              className="iot-request-name-input"
              value={activeTab?.name || ''}
              onChange={(e) => {
                const newName = e.target.value;
                setTabs(prevTabs => prevTabs.map(tab =>
                  tab.id === activeTabId ? applyTabChange(tab, { name: newName }) : tab
                ));
                setSaveRequestName(newName);
              }}
              placeholder="未命名请求"
            />
            {activeTab?.hasUnsavedChanges && <span className="iot-unsaved-dot" title="有未保存的更改" />}
            <Tooltip
              title={activeTab?.savedRequestId != null ? '更新已保存的请求配置' : '保存为新请求配置'}
              overlayClassName="iot-tooltip"
            >
              <Button icon={<SaveOutlined />} onClick={handleSaveRequest} loading={saveLoading} className="iot-btn-save">
                保存
              </Button>
            </Tooltip>
          </div>

          <Form form={form} layout="vertical" size="middle" className="iot-form" style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
            <div className={`iot-url-bar${isMqtt ? ' iot-url-bar--mqtt' : ''}`}>
              {isMqtt ? (
                <div className="iot-mqtt-address-bar">
                  <div className="iot-mqtt-address-row">
                    <Input
                      value={mqttConfig.broker}
                      className="iot-broker-input"
                      placeholder="mqtt://host:1883"
                      onChange={(e) => handleMqttChange({ broker: e.target.value })}
                      addonBefore={<span className="iot-mqtt-addon">Broker</span>}
                    />
                    <Input
                      value={mqttConfig.port || ''}
                      className="iot-port-input"
                      placeholder="1883"
                      title="端口：留空则使用地址中的端口，或按协议默认（1883/8883/8083/8084）"
                      onChange={(e) => handleMqttChange({ port: e.target.value.replace(/[^0-9]/g, '') })}
                      addonBefore={<span className="iot-mqtt-addon">端口</span>}
                    />
                  </div>
                  <Input
                    value={mqttConfig.topic}
                    className="iot-topic-input"
                    placeholder="Topic，如 iot/device/1/data，支持 {{variable}}"
                    onChange={(e) => handleMqttChange({ topic: e.target.value })}
                    addonBefore={<span className="iot-mqtt-addon">Topic</span>}
                  />
                </div>
              ) : isWs ? (
                <div className="iot-ws-address-bar">
                  <Input
                    value={wsConfig.url}
                    className="iot-ws-url-input"
                    placeholder="ws://host:port/path?id=xxx&token=xxx，支持 {{variable}}"
                    onChange={(e) => handleWsChange({ url: e.target.value })}
                    onPressEnter={handleSendRequest}
                    addonBefore={<span className="iot-mqtt-addon">WS</span>}
                  />
                </div>
              ) : (
                <Space.Compact style={{ flex: 1, minWidth: 0 }}>
                  <Form.Item name="method" noStyle>
                    <Select
                      style={{ width: 110 }}
                      className="iot-method-select"
                      onChange={(value: string) => updateCurrentTab({ method: value })}
                    >
                      {methods.map(m => (
                        <Select.Option key={m} value={m}>
                          <MethodBadge method={m} size="md" />
                        </Select.Option>
                      ))}
                    </Select>
                  </Form.Item>
                  <Form.Item name="url" noStyle rules={[{ required: true, message: '请输入请求 URL' }]}>
                    <Input
                      placeholder="请输入请求 URL，支持 {{variable}} 变量"
                      className="iot-url-input"
                      onChange={(e) => updateCurrentTab({ url: e.target.value })}
                    />
                  </Form.Item>
                </Space.Compact>
              )}
              <Form.Item name="body" hidden>
                <Input />
              </Form.Item>
              <div className="iot-url-actions">
                {isMqtt && (
                  <Tooltip title="测试与 Broker 的连通性" overlayClassName="iot-tooltip">
                    <Button
                      icon={<SyncOutlined />}
                      onClick={handleTestMqttConnection}
                      loading={testLoading}
                      className="iot-btn-test-connection"
                    >
                      测试连接
                    </Button>
                  </Tooltip>
                )}
                {isWs && (
                  <Tooltip title="测试 WebSocket 握手连通性" overlayClassName="iot-tooltip">
                    <Button
                      icon={<SyncOutlined />}
                      onClick={handleTestWsConnection}
                      loading={testLoading}
                      className="iot-btn-test-connection"
                    >
                      测试连接
                    </Button>
                  </Tooltip>
                )}
                <Button
                  type="primary"
                  className={`iot-btn-send ${sendLoading ? 'is-loading' : ''}`}
                  icon={<SendOutlined />}
                  onClick={handleSendRequest}
                  loading={sendLoading}
                >
                  {isMqtt ? '发布' : isWs ? '连接' : '发送'}
                </Button>
              </div>
            </div>

            <div className="iot-config-area">
              <div className="iot-config-tabs">
                {configTabItems().map(item => (
                  <button
                    key={item.key}
                    className={'iot-config-tab' + (currentConfigTab === item.key ? ' is-active' : '')}
                    onClick={() => setCurrentConfigTab(item.key)}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
              <div className="iot-config-body">
                {isMqtt ? (
                  currentConfigTab === 'body' ? (
                    <div className="iot-panel" style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                      <div className="iot-panel-header">
                        <span className="iot-panel-header-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          {isMqtt ? 'Payload' : '请求体'}
                          {isMqtt && <Tag className="iot-mqtt-payload-tag" color="purple">MQTT</Tag>}
                        </span>
                        <Button
                          type="text"
                          icon={<FormatPainterOutlined />}
                          className="iot-format-btn"
                          onClick={() => {
                            const currentBody = isMqtt ? mqttConfig.payload : form.getFieldValue('body');
                            if (currentBody) {
                              try {
                                const formatted = formatJsonWithComments(currentBody);
                                if (isMqtt) {
                                  handleMqttChange({ payload: formatted });
                                } else {
                                  form.setFieldsValue({ body: formatted });
                                  updateCurrentTab({ body: formatted });
                                }
                                message.success('JSON 格式化成功');
                              } catch {
                                message.error('JSON 格式不正确');
                              }
                            }
                          }}
                          size="small"
                        >
                          格式化
                        </Button>
                      </div>
                      <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }} className="iot-codemirror">
                        <CodeMirror
                          value={isMqtt ? mqttConfig.payload : (form.getFieldValue('body') || '')}
                          height="100%"
                          extensions={jsonExtensions}
                          onChange={isMqtt
                            ? (value: string) => handleMqttChange({ payload: value })
                            : handleBodyChange}
                          placeholder={isMqtt ? '请输入要发布的 Payload（JSON 或纯文本），支持 {{variable}} 变量' : '请输入请求体（JSON 格式）'}
                          basicSetup={{
                            lineNumbers: true,
                            foldGutter: true,
                            highlightActiveLine: true,
                          }}
                        />
                      </div>
                    </div>
                  ) : (
                    <div className="iot-mqtt-config">
                      <div className="iot-mqtt-config-title">MQTT 连接配置</div>
                      <div className="iot-mqtt-field iot-mqtt-field--full">
                        <span className="iot-mqtt-label">QoS</span>
                        <Select
                          value={mqttConfig.qos}
                          className="iot-mqtt-qos"
                          onChange={(value) => handleMqttChange({ qos: value as 0 | 1 | 2 })}
                          options={[
                            { value: 0, label: '0 · 最多一次' },
                            { value: 1, label: '1 · 至少一次' },
                            { value: 2, label: '2 · 恰好一次' },
                          ]}
                        />
                      </div>
                      <div className="iot-mqtt-field iot-mqtt-field--full">
                        <span className="iot-mqtt-label">Retain</span>
                        <div className="iot-mqtt-switch-group">
                          <Switch
                            checked={mqttConfig.retain}
                            size="small"
                            onChange={(checked) => handleMqttChange({ retain: checked })}
                          />
                          <span className="iot-mqtt-switch-hint">保留最后一条消息</span>
                        </div>
                      </div>
                      <div className="iot-mqtt-field iot-mqtt-field--full">
                        <span className="iot-mqtt-label">TLS</span>
                        <div className="iot-mqtt-switch-group">
                          <Switch
                            checked={mqttConfig.tls}
                            size="small"
                            onChange={(checked) => handleMqttChange({ tls: checked })}
                          />
                          <span className="iot-mqtt-switch-hint">加密连接（8883 / wss）</span>
                        </div>
                      </div>
                      <div className="iot-mqtt-field iot-mqtt-field--full">
                        <span className="iot-mqtt-label">Client ID</span>
                        <Input
                          value={mqttConfig.client_id}
                          placeholder="留空则由 Broker 自动分配"
                          onChange={(e) => handleMqttChange({ client_id: e.target.value })}
                        />
                      </div>
                      <div className="iot-mqtt-field iot-mqtt-field--full">
                        <span className="iot-mqtt-label">Username</span>
                        <Input
                          value={mqttConfig.username}
                          placeholder="可选"
                          onChange={(e) => handleMqttChange({ username: e.target.value })}
                        />
                      </div>
                      <div className="iot-mqtt-field iot-mqtt-field--full">
                        <span className="iot-mqtt-label">Password</span>
                        <Input.Password
                          value={mqttConfig.password}
                          placeholder="可选"
                          onChange={(e) => handleMqttChange({ password: e.target.value })}
                        />
                      </div>
                      <div className="iot-mqtt-hint">
                        Payload 在「Payload」分页编辑，支持 {'{{variable}}'} 变量替换
                      </div>
                    </div>
                  )
                ) : isWs ? (
                  currentConfigTab === 'headers' ? (
                    <div className="iot-config-section">
                      {renderKvTable({
                        items: wsConfig.headers,
                        onChange: handleWsHeaderChange,
                        onRemove: handleRemoveWsHeader,
                        onAdd: handleAddWsHeader,
                        onToggle: handleWsHeaderToggle,
                        onToggleAll: handleWsHeadersToggleAll,
                        addLabel: '添加请求头',
                      })}
                    </div>
                  ) : (
                    <div className="iot-ws-config">
                      <div className="iot-ws-config-title">WebSocket 连接配置</div>
                      <div className="iot-ws-field iot-ws-field--full">
                        <span className="iot-ws-label">最大消息数</span>
                        <InputNumber
                          min={1}
                          max={500}
                          precision={0}
                          value={wsConfig.maxMessages}
                          onChange={(value) => handleWsChange({ maxMessages: typeof value === 'number' ? value : 50 })}
                        />
                      </div>
                      <div className="iot-ws-field iot-ws-field--full">
                        <span className="iot-ws-label">空闲超时</span>
                        <InputNumber
                          min={1}
                          max={60}
                          precision={0}
                          value={wsConfig.timeoutSeconds}
                          onChange={(value) => handleWsChange({ timeoutSeconds: typeof value === 'number' ? value : 10 })}
                          addonAfter="秒"
                        />
                      </div>
                      <div className="iot-ws-field iot-ws-field--full">
                        <span className="iot-ws-label">子协议</span>
                        <Input
                          value={wsConfig.protocols || ''}
                          placeholder="可选，多个用英文逗号分隔，如 graphql-ws,json"
                          onChange={(e) => handleWsChange({ protocols: e.target.value })}
                        />
                      </div>
                      <div className="iot-ws-field iot-ws-field--full iot-ws-field--textarea">
                        <span className="iot-ws-label">首发消息</span>
                        <Input.TextArea
                          rows={2}
                          value={wsConfig.sendMessage || ''}
                          placeholder="连接成功后先发送一条文本消息（可选）"
                          onChange={(e) => handleWsChange({ sendMessage: e.target.value })}
                        />
                      </div>
                      <div className="iot-ws-hint">
                        收到的消息在响应区「消息流」查看，支持 {'{{variable}}'} 变量替换
                      </div>
                    </div>
                  )
                ) : (
                  <>
                    {currentConfigTab === 'params' && (
                      <div className="iot-config-section">
                        {renderKvTable({
                          items: parameters,
                          onChange: handleParameterChange,
                          onRemove: handleRemoveParameter,
                          onAdd: handleAddParameter,
                          onToggle: handleParameterToggle,
                          onToggleAll: handleParametersToggleAll,
                          addLabel: '添加参数',
                          keyPlaceholder: '参数名',
                          valuePlaceholder: '参数值',
                          renderLeading: (item, index) => {
                            const param = item as ParameterItem;
                            return (
                              <Radio.Group
                                value={param.type || 'text'}
                                size="small"
                                optionType="button"
                                buttonStyle="solid"
                                options={[{ label: '文本', value: 'text' }, { label: '文件', value: 'file' }]}
                                onChange={(e) => handleParameterTypeChange(index, e.target.value as 'text' | 'file')}
                              />
                            );
                          },
                          renderValue: (item, index) => {
                            const param = item as ParameterItem;
                            if (param.type !== 'file') {
                              return (
                                <Input
                                  size="small"
                                  placeholder="参数值"
                                  value={param.value}
                                  onChange={(e) => handleParameterChange(index, 'value', e.target.value)}
                                />
                              );
                            }
                            return (
                              <FileUpload
                                value={param.file ? [param.file] : []}
                                onChange={(files) => handleParameterFileChange(index, files[0])}
                                maxSize={50}
                                onUploadSuccess={(result) => {
                                  const newParams = [...parameters];
                                  newParams[index] = { ...newParams[index], file: result, value: result.fileId };
                                  setParameters(newParams);
                                }}
                                environmentId={parseInt(currentEnvironmentId) || null}
                              />
                            );
                          },
                        })}
                      </div>
                    )}
                    {currentConfigTab === 'headers' && (
                      <div className="iot-config-section">
                        {renderKvTable({
                          items: headers,
                          onChange: handleHeaderChange,
                          onRemove: handleRemoveHeader,
                          onAdd: handleAddHeader,
                          onToggle: handleHeaderToggle,
                          onToggleAll: handleHeadersToggleAll,
                          addLabel: '添加请求头',
                        })}
                      </div>
                    )}
                    {currentConfigTab === 'body' && (
                    <div className="iot-panel" style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                      <div className="iot-panel-header">
                        <span className="iot-panel-header-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          {isMqtt ? 'Payload' : '请求体'}
                          {isMqtt && <Tag className="iot-mqtt-payload-tag" color="purple">MQTT</Tag>}
                        </span>
                        <Button
                          type="text"
                          icon={<FormatPainterOutlined />}
                          className="iot-format-btn"
                          onClick={() => {
                            const currentBody = isMqtt ? mqttConfig.payload : form.getFieldValue('body');
                            if (currentBody) {
                              try {
                                const formatted = formatJsonWithComments(currentBody);
                                if (isMqtt) {
                                  handleMqttChange({ payload: formatted });
                                } else {
                                  form.setFieldsValue({ body: formatted });
                                  updateCurrentTab({ body: formatted });
                                }
                                message.success('JSON 格式化成功');
                              } catch {
                                message.error('JSON 格式不正确');
                              }
                            }
                          }}
                          size="small"
                        >
                          格式化
                        </Button>
                      </div>
                      <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }} className="iot-codemirror">
                        <CodeMirror
                          value={isMqtt ? mqttConfig.payload : (form.getFieldValue('body') || '')}
                          height="100%"
                          extensions={jsonExtensions}
                          onChange={isMqtt
                            ? (value: string) => handleMqttChange({ payload: value })
                            : handleBodyChange}
                          placeholder={isMqtt ? '请输入要发布的 Payload（JSON 或纯文本），支持 {{variable}} 变量' : '请输入请求体（JSON 格式）'}
                          basicSetup={{
                            lineNumbers: true,
                            foldGutter: true,
                            highlightActiveLine: true,
                          }}
                        />
                      </div>
                    </div>
                    )}
                    {currentConfigTab === 'batch' && (
                      <BatchParamsEditor value={batchParams} onChange={setBatchParams} />
                    )}
                    {currentConfigTab === 'pre' && (
                      <PreStepsEditor
                        steps={preSteps}
                        savedRequests={savedRequests.filter(r => (r.protocol || 'http') === 'http')}
                        onChange={handlePreStepChange}
                        onRemove={handleRemovePreStep}
                        onAdd={handleAddPreStep}
                        onAddExtract={handleAddPreStepExtract}
                        onRemoveExtract={handleRemovePreStepExtract}
                        onExtractChange={handlePreStepExtractChange}
                      />
                    )}
                    {currentConfigTab === 'extract' && canManageGlobalParams && (
                      <div className="iot-config-section">
                        <div className="iot-config-section-hint">
                          从响应 JSON 中提取值并保存到当前环境变量
                          <Tooltip
                            title={
                              <div style={{ fontSize: 12, lineHeight: 1.8 }}>
                                <div>从响应 JSON 中提取值并保存到当前环境变量</div>
                                <div style={{ marginTop: 4, fontWeight: 600 }}>JSONPath 语法示例：</div>
                                <div><code>$.data.token</code> — 提取 data.token</div>
                                <div><code>$.data.list[0].id</code> — 提取数组第一项的 id</div>
                                <div><code>$..name</code> — 递归查找所有 name 字段</div>
                              </div>
                            }
                            overlayClassName="iot-tooltip"
                          >
                            <QuestionCircleOutlined style={{ color: 'var(--color-text-tertiary)', fontSize: 13 }} />
                          </Tooltip>
                        </div>
                        {postExtractions.length === 0 ? (
                          <div className="iot-extraction-empty">
                            暂无提取规则
                            <Button type="link" size="small" onClick={handleAddExtraction} className="iot-extraction-add-link">
                              添加
                            </Button>
                          </div>
                        ) : (
                          renderKvTable({
                            items: postExtractions.map(rule => ({ key: rule.variable, value: rule.jsonpath })),
                            onChange: (index, field, value) => {
                              if (field === 'key') handleExtractionChange(index, 'variable', value);
                              else if (field === 'value') handleExtractionChange(index, 'jsonpath', value);
                            },
                            onRemove: handleRemoveExtraction,
                            onAdd: handleAddExtraction,
                            addLabel: '添加提取规则',
                            keyPlaceholder: '变量名',
                            valuePlaceholder: 'JSONPath，如 $.data.token',
                            withEnabled: false,
                            withDescription: false,
                          })
                        )}
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          </Form>

          {/* 请求区 / 响应区上下拖拽分割 */}
          {!responseCollapsed && (
            <div
              className={'iot-split-handle' + (isDraggingResponse ? ' is-active' : '')}
              onMouseDown={handleResponseDragStart}
              title="拖拽调整响应区高度"
            />
          )}

          {/* 响应面板 */}
          <div
            className={'iot-response-panel' + (responseCollapsed ? ' is-collapsed' : '')}
            style={{ flex: responseCollapsed ? '0 0 34px' : '0 0 ' + responseSplit + '%' }}
          >
            <div
              className={'iot-response-tabsbar' + (responseCollapsed ? ' is-collapsed' : '')}
              onClick={responseCollapsed ? (e) => {
                // 折叠态整条可点展开，但点在条内的按钮（如「复制」）上时不展开
                if ((e.target as HTMLElement).closest('button')) return;
                setResponseCollapsed(false);
              } : undefined}
              title={responseCollapsed ? '点击展开响应区' : undefined}
            >
              <div className="iot-response-tabs">
                {isMqtt ? (
                  <span className="iot-response-tab is-active">发布结果</span>
                ) : isWs ? (
                  <>
                    <button
                      className={'iot-response-tab' + (currentResponseTab !== 'result' ? ' is-active' : '')}
                      onClick={() => setCurrentResponseTab('messages')}
                    >
                      消息流{wsMessages.length ? ' (' + wsMessages.length + ')' : ''}
                    </button>
                    <button
                      className={'iot-response-tab' + (currentResponseTab === 'result' ? ' is-active' : '')}
                      onClick={() => setCurrentResponseTab('result')}
                    >
                      连接结果
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      className={'iot-response-tab' + (currentResponseTab !== 'headers' ? ' is-active' : '')}
                      onClick={() => setCurrentResponseTab('body')}
                    >
                      响应体
                    </button>
                    <button
                      className={'iot-response-tab' + (currentResponseTab === 'headers' ? ' is-active' : '')}
                      onClick={() => setCurrentResponseTab('headers')}
                    >
                      响应头
                    </button>
                    {(response as any)?.pre_steps?.length > 0 && (
                      <button
                        className={'iot-response-tab' + (currentResponseTab === 'pre' ? ' is-active' : '')}
                        onClick={() => setCurrentResponseTab('pre')}
                      >
                        前置步骤 ({(response as any).pre_steps.length})
                      </button>
                    )}
                  </>
                )}
              </div>
              <div className="iot-response-meta">
                {isMqtt && activeTab?.mqttResult && (
                  <>
                    <span className={'iot-response-status-code ' + (activeTab.mqttResult.success ? 'is-success' : 'is-error')}>
                      {activeTab.mqttResult.success ? '发布成功' : '发布失败'}
                    </span>
                    <span className="iot-response-time">耗时: <strong>{activeTab.mqttResult.duration_ms}ms</strong></span>
                  </>
                )}
                {isWs && wsResult && (
                  <>
                    <span className={'iot-response-status-code ' + (wsSucceeded ? 'is-success' : 'is-error')}>
                      {wsSucceeded ? '连接成功' : '连接失败'}
                    </span>
                    <span className="iot-response-time">耗时: <strong>{wsResult.duration_ms}ms</strong></span>
                    <span className="iot-response-time">消息: <strong>{wsResult.received} 条</strong></span>
                  </>
                )}
                {!isMqtt && !isWs && response && (
                  <>
                    <span className={'iot-response-status-code ' + getStatusClass(response.status)}>
                      {response.status} {response.statusText}
                    </span>
                    <span className="iot-response-time">耗时: <strong>{responseTime}ms</strong></span>
                    <span className="iot-response-time">大小: <strong>{formatBytes(responseSize)}</strong></span>
                  </>
                )}
                <Button
                  icon={<CopyOutlined />}
                  className="iot-copy-btn"
                  size="small"
                  onClick={handleCopyResponse}
                  disabled={isMqtt ? !activeTab?.mqttResult : isWs ? !wsResult : !response}
                >
                  复制
                </Button>
                <Tooltip title={responseCollapsed ? '展开响应区' : '折叠响应区'} overlayClassName="iot-tooltip">
                  <Button
                    type="text"
                    size="small"
                    className="iot-response-toggle"
                    icon={responseCollapsed ? <UpOutlined /> : <DownOutlined />}
                    onClick={() => setResponseCollapsed(prev => !prev)}
                  />
                </Tooltip>
              </div>
            </div>

            {!responseCollapsed && (
              isWs && currentResponseTab !== 'result' ? (
                <div className="iot-panel" style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                  <div className="iot-panel-header">
                    <span className="iot-panel-header-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      消息流（{wsMessages.length} 条）
                      {wsResult?.closed_by && (
                        <Tag className="iot-ws-closed-tag" color="cyan">{formatClosedBy(wsResult.closed_by)}</Tag>
                      )}
                    </span>
                    {sendLoading && <span className="iot-ws-hint">连接中…</span>}
                  </div>
                  <div className="iot-ws-message-list">
                    {wsMessages.length === 0 ? (
                      <div className="iot-ws-empty">点击连接后此处显示收到的消息</div>
                    ) : (
                      wsMessages.map(msg => (
                        <div className="iot-ws-message" key={msg.seq}>
                          <div className="iot-ws-message-head">
                            <span className="iot-ws-message-seq">#{msg.seq}</span>
                            <span>{Number.isFinite(msg.ts) ? `${msg.ts.toFixed(2)}s` : '-'}</span>
                            <span>{msg.size}B</span>
                          </div>
                          <div className="iot-ws-message-content">
                            {msg.type === 'binary' ? (
                              <>
                                <span className="iot-ws-message-binary">&lt;binary {msg.size} bytes&gt;</span>
                                {msg.base64 && (
                                  <details className="iot-ws-message-base64">
                                    <summary>展开 base64</summary>
                                    <pre>{msg.base64}</pre>
                                  </details>
                                )}
                              </>
                            ) : msg.content}
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              ) : (

                <div className="iot-response-container">
                  {isMqtt ? (
                    activeTab?.mqttResult ? (
                      <>
                        <div className="iot-response-body">
                          <div className="iot-mqtt-result">
                            <div className="iot-mqtt-result-row">
                              <span className="iot-mqtt-result-label">Broker</span>
                              <span className="iot-mqtt-result-value">{activeTab.mqttResult.broker}</span>
                            </div>
                            <div className="iot-mqtt-result-row">
                              <span className="iot-mqtt-result-label">Topic</span>
                              <span className="iot-mqtt-result-value">{activeTab.mqttResult.topic}</span>
                            </div>
                            <div className="iot-mqtt-result-row">
                              <span className="iot-mqtt-result-label">QoS</span>
                              <span className="iot-mqtt-result-value">{activeTab.mqttResult.qos}</span>
                            </div>
                            <div className="iot-mqtt-result-row">
                              <span className="iot-mqtt-result-label">Retain</span>
                              <span className="iot-mqtt-result-value">{activeTab.mqttResult.retain ? '是' : '否'}</span>
                            </div>
                            {activeTab.mqttResult.error && (
                              <div className="iot-mqtt-result-row">
                                <span className="iot-mqtt-result-label">错误</span>
                                <span className="iot-mqtt-result-value iot-mqtt-result-error">{activeTab.mqttResult.error}</span>
                              </div>
                            )}
                            <pre className="iot-response-pre iot-mqtt-result-payload">
                              {activeTab.mqttResult.payload || '(空 Payload)'}
                            </pre>
                          </div>
                        </div>
                      </>
                    ) : (
                      <div className="iot-response-empty">
                        <SendOutlined className="iot-response-empty-icon" />
                        <span className="iot-response-empty-text">点击发布按钮查看发布结果</span>
                      </div>
                    )
                  ) : isWs ? (
                    wsResult ? (
                      <>
                        <div className="iot-response-body">
                          <div className="iot-ws-result">
                            <div className="iot-ws-result-row">
                              <span className="iot-ws-result-label">地址</span>
                              <span className="iot-ws-result-value">{wsResult.url}</span>
                            </div>
                            <div className="iot-ws-result-row">
                              <span className="iot-ws-result-label">状态</span>
                              <span className={`iot-ws-result-value ${wsSucceeded ? 'is-success' : 'is-error'}`}>
                                {wsSucceeded ? '连接成功' : '连接失败'}
                              </span>
                            </div>
                            <div className="iot-ws-result-row">
                              <span className="iot-ws-result-label">收到消息</span>
                              <span className="iot-ws-result-value">{wsResult.received} 条</span>
                            </div>
                            <div className="iot-ws-result-row">
                              <span className="iot-ws-result-label">耗时</span>
                              <span className="iot-ws-result-value">{wsResult.duration_ms}ms</span>
                            </div>
                            <div className="iot-ws-result-row">
                              <span className="iot-ws-result-label">关闭原因</span>
                              <span className="iot-ws-result-value">{formatClosedBy(wsResult.closed_by)}</span>
                            </div>
                            {wsResult.error && (
                              <div className="iot-ws-result-row">
                                <span className="iot-ws-result-label">错误</span>
                                <span className="iot-ws-result-value iot-ws-result-error">{wsResult.error}</span>
                              </div>
                            )}
                          </div>
                        </div>
                      </>
                    ) : (
                      <div className="iot-response-empty">
                        <SendOutlined className="iot-response-empty-icon" />
                        <span className="iot-response-empty-text">点击连接按钮查看连接结果</span>
                      </div>
                    )
                  ) : response ? (
                    <div className="iot-response-body">
                      {currentResponseTab === 'pre' ? (
                        renderPreStepResults((response as any).pre_steps)
                      ) : currentResponseTab === 'headers' ? (
                        renderResponseHeaders()
                      ) : responseHtml ? (
                        <>
                          <div className="iot-response-toolbar">
                            <span className="iot-response-toolbar-note">{rawToolbarNote}</span>
                            {responseJsonDepth > 2 && (
                              <span className="iot-response-toolbar-meta">{responseJsonDepth} 层</span>
                            )}
                            <Button
                              type="text"
                              size="small"
                              className="iot-json-collapse-btn"
                              onClick={handleJsonCollapseAll}
                            >
                              折叠
                            </Button>
                            <Button
                              type="text"
                              size="small"
                              className="iot-json-collapse-btn"
                              onClick={handleJsonExpandAll}
                            >
                              展开
                            </Button>
                          </div>
                          <div
                            ref={responseBodyRef}
                            className="iot-response-pre iot-response-tree"
                            onClick={handleResponseTreeClick}
                            dangerouslySetInnerHTML={{ __html: responseHtml }}
                          />
                        </>
                      ) : (
                        <pre className="iot-response-pre">{responseText}</pre>
                      )}
                    </div>
                  ) : (
                    <div className="iot-response-empty">
                      <SendOutlined className="iot-response-empty-icon" />
                      <span className="iot-response-empty-text">点击发送按钮查看响应结果</span>
                    </div>
                  )}
                </div>
              )
            )}
          </div>
        </div>
      </div>

      {/* 保存请求：选择归属文件夹（不选 = 未分类） */}
      <Modal
        title="保存请求"
        open={saveFolderModalVisible}
        onCancel={() => closeSaveFolderModal(undefined)}
        width={460}
        className="iot-modal iot-save-folder-modal"
        maskClosable={false}
        footer={[
          <Button key="cancel" onClick={() => closeSaveFolderModal(undefined)}>
            取消
          </Button>,
          <Button key="uncategorized" onClick={() => closeSaveFolderModal(null)}>
            不选择（未分类）
          </Button>,
          <Button key="ok" type="primary" className="iot-btn-primary" onClick={() => closeSaveFolderModal(saveFolderChoice)}>
            保存
          </Button>,
        ]}
      >
        <p className="iot-new-request-hint">
          当前请求还没有归属文件夹，请选择要保存到哪个文件夹；不选择则保存到「未分类」。
        </p>
        <TreeSelect
          style={{ width: '100%' }}
          value={saveFolderChoice ?? undefined}
          onChange={(value) => setSaveFolderChoice(typeof value === 'number' ? value : null)}
          treeData={saveFolderTreeData}
          treeDefaultExpandAll
          allowClear
          placeholder="不选择则保存到「未分类」"
          showSearch
          treeNodeFilterProp="title"
        />
      </Modal>

      {/* 新建：请求类型 + 文件夹（与 Postman 的 New 对话框一致） */}
      <Modal
        title="新建"
        open={isNewRequestModalVisible}
        onCancel={closeNewItemModal}
        footer={null}
        width={660}
        className="iot-modal iot-new-request-modal"
      >
        {newItemMode === 'type' ? (
          <>
            <p className="iot-new-request-hint">选择请求类型（创建后类型不可更改），或新建文件夹</p>
            <div className="iot-type-grid">
              {NEW_REQUEST_TYPES.map(item => (
                <button
                  key={item.value}
                  type="button"
                  className={'iot-type-card iot-type-card--' + item.value}
                  onClick={() => {
                    addNewTab(item.value);
                    closeNewItemModal();
                  }}
                >
                  <span className={'iot-type-icon iot-type-icon--' + item.value}>
                    {item.value === 'http' ? <ApiOutlined /> : item.value === 'websocket' ? <SwapOutlined /> : <CloudServerOutlined />}
                  </span>
                  <span className="iot-type-name">{item.label}</span>
                  <span className="iot-type-desc">{item.description}</span>
                </button>
              ))}
              <button
                type="button"
                className="iot-type-card iot-type-card--folder"
                onClick={() => setNewItemMode('folder')}
              >
                <span className="iot-type-icon iot-type-icon--folder">
                  <FolderAddOutlined />
                </span>
                <span className="iot-type-name">文件夹</span>
                <span className="iot-type-desc">把相关请求归类管理（Collection）</span>
              </button>
            </div>
          </>
        ) : (
          <>
            <button type="button" className="iot-new-folder-back" onClick={() => setNewItemMode('type')}>
              ← 返回类型选择
            </button>
            <div className="iot-new-folder-form">
              <div className="iot-new-folder-field">
                <span className="iot-new-folder-label">名称</span>
                <Input
                  autoFocus
                  placeholder="文件夹名称"
                  value={newItemFolderName}
                  onChange={(e) => setNewItemFolderName(e.target.value)}
                  onPressEnter={handleCreateFolderFromModal}
                />
              </div>
              <div className="iot-new-folder-field">
                <span className="iot-new-folder-label">上级文件夹</span>
                <Select
                  value={newItemFolderParent}
                  onChange={(value) => setNewItemFolderParent(value)}
                  style={{ width: '100%' }}
                  options={[{ value: 0, label: '根目录' }, ...flatFolderOptions.map(item => ({ value: item.id, label: item.label }))]}
                />
              </div>
              <div className="iot-new-folder-actions">
                <Button onClick={closeNewItemModal}>取消</Button>
                <Button type="primary" className="iot-btn-primary" loading={creatingFolder} onClick={handleCreateFolderFromModal}>
                  创建
                </Button>
              </div>
            </div>
          </>
        )}
      </Modal>

      {/* Global Params Modal */}
      <Modal
        title="全局参数配置"
        open={isGlobalParamsModalVisible && canManageGlobalParams}
        onOk={async () => {
          await handleSaveEnvironmentParameter();
          setIsGlobalParamsModalVisible(false);
        }}
        onCancel={() => setIsGlobalParamsModalVisible(false)}
        width={600}
        className="iot-modal"
      >
        <div style={{ marginBottom: 16 }}>
          <div style={{ marginBottom: 20 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <h3 style={{ margin: 0, fontSize: 14, fontWeight: 'bold', color: 'var(--color-text)' }}>环境管理</h3>
              <Button type="primary" size="small" className="iot-btn-primary" onClick={handleAddEnvironment}>
                添加环境
              </Button>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
              {environments.map(env => (
                <div
                  key={env.id}
                  className={`iot-env-tag ${currentEnvironmentId === env.id ? 'is-active' : ''}`}
                  onClick={() => handleSwitchEnvironment(env.id)}
                >
                  {editingEnvId === env.id ? (
                    <Space size="small" style={{ alignItems: 'center' }}>
                      <Input
                        size="small"
                        value={editingEnvName}
                        onChange={(e) => setEditingEnvName(e.target.value)}
                        onPressEnter={handleSaveEnvironmentName}
                        style={{ width: 120 }}
                        className="iot-form"
                      />
                      <Button size="small" type="link" style={{ color: 'var(--color-primary)', padding: 0 }} onClick={(e) => { e.stopPropagation(); handleSaveEnvironmentName(); }}>保存</Button>
                      <Button size="small" type="link" style={{ color: 'var(--color-text-tertiary)', padding: 0 }} onClick={(e) => { e.stopPropagation(); handleCancelEditEnvironmentName(); }}>取消</Button>
                    </Space>
                  ) : (
                    <Space size="small" style={{ alignItems: 'center' }}>
                      <span>{env.name}</span>
                      <Tooltip title="复制该环境">
                        <Button type="text" size="small" icon={<CopyOutlined />} loading={copyingEnvId === env.id} style={{ color: 'inherit', padding: 0, width: 20, height: 20 }} onClick={(e) => { e.stopPropagation(); handleCopyEnvironment(env); }} />
                      </Tooltip>
                      <Button type="text" size="small" icon={<EditOutlined />} style={{ color: 'inherit', padding: 0, width: 20, height: 20 }} onClick={(e) => { e.stopPropagation(); handleEditEnvironmentName(env); }} />
                      {environments.length > 1 && (
                        <Button type="text" size="small" style={{ color: 'inherit', padding: 0, width: 20, height: 20 }} onClick={(e) => { e.stopPropagation(); handleRemoveEnvironment(env.id); }}>
                          &times;
                        </Button>
                      )}
                    </Space>
                  )}
                </div>
              ))}
            </div>
          </div>

          <p style={{ color: 'var(--color-text-secondary)', marginBottom: 12, fontSize: 13 }}>
            全局参数将应用于所有请求，可在 URL、请求头和请求体中使用 {'{{variable}}'} 或 ${'{variable}'} 语法引用
          </p>

          {getCurrentEnvironment().parameters.map((param, index) => (
            <Space key={index} style={{ width: '100%', marginBottom: 12 }} align="center">
              <Input
                placeholder="参数名"
                value={param.key}
                onChange={(e) => handleEnvironmentParameterChange(index, 'key', e.target.value)}
                style={{ width: 150 }}
                className="iot-form"
              />
              <Input
                placeholder="参数值"
                value={param.value}
                onChange={(e) => handleEnvironmentParameterChange(index, 'value', e.target.value)}
                style={{ flex: 1 }}
                className="iot-form"
              />
              <Button icon={<MinusOutlined />} className="iot-delete-btn" onClick={() => handleRemoveEnvironmentParameter(index)} size="small" />
            </Space>
          ))}

          <Button
            type="dashed"
            icon={<PlusOutlined />}
            onClick={handleAddEnvironmentParameter}
            className="iot-add-btn"
            style={{ marginTop: 8, width: '100%' }}
          >
            添加全局参数
          </Button>
        </div>
      </Modal>

      {/* Add Environment Modal */}
      <Modal
        title="创建新环境"
        open={isAddEnvModalVisible && canManageGlobalParams}
        onOk={handleConfirmAddEnvironment}
        onCancel={() => setIsAddEnvModalVisible(false)}
        width={400}
        className="iot-modal"
      >
        <Input
          placeholder="请输入环境名称"
          value={newEnvironmentName}
          onChange={(e) => setNewEnvironmentName(e.target.value)}
          style={{ marginBottom: 16 }}
          className="iot-form"
        />
        <div style={{ color: 'var(--color-text-tertiary)', fontSize: 12 }}>
          环境名称用于区分不同的参数配置集
        </div>
      </Modal>

      {/* Push request log drawer */}
      <PushLogDrawer open={showPushLog} onClose={() => setShowPushLog(false)} />
    </div>
  );
};

export default IoTDataPushPlatform;

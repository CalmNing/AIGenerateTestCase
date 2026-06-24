import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Form, Select, Input, Button, Tabs, Space, message, Modal, Tooltip, Radio } from 'antd';
import { SendOutlined, PlusOutlined, MinusOutlined, CopyOutlined, SaveOutlined, SyncOutlined, FormatPainterOutlined, LeftOutlined, RightOutlined, QuestionCircleOutlined, EditOutlined, ApiOutlined, InboxOutlined } from '@ant-design/icons';
import CodeMirror from '@uiw/react-codemirror';
import { json } from '@codemirror/lang-json';
import { savedRequestApi, globalParameterApi, proxyApi } from '../services/api';
import { SavedRequest as SavedRequestType } from '../types';
import FileUpload, { UploadedFileResult } from './FileUpload';
import VariableAssistant from './VariableAssistant';
import './IoTDataPushPlatform.css';

interface HeaderItem {
  key: string;
  value: string;
}

interface ParameterItem {
  key: string;
  value: string;
  type?: 'text' | 'file';
  file?: UploadedFileResult;
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
  const cleaned = stripJsonComments(input).replace(/([:\[,])\s*(?!"|\d|true|false|null|\[|\{(?!\{))((?:[^\s,}\]]*?(?:\{\{(?:[^{}]|\{[^{}]*\})*\}\}|\$\{[^}]*\})[^\s,}\]]*?)+)/g, (_match, prefix, valuePart) => {
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

// --- JSON Syntax Highlighter (pure CSS, no CodeMirror dependency for response) ---

function highlightJson(value: any, indent: number = 2): string {
  if (value === null) return '<span class="iot-json-null">null</span>';
  if (typeof value === 'boolean') return `<span class="iot-json-boolean">${value}</span>`;
  if (typeof value === 'number') return `<span class="iot-json-number">${value}</span>`;
  if (typeof value === 'string') {
    const escaped = value
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
    return `<span class="iot-json-string">"${escaped}"</span>`;
  }

  const pad = (level: number) => ' '.repeat(level * indent);
  const padInner = (level: number) => ' '.repeat((level + 1) * indent);

  if (Array.isArray(value)) {
    if (value.length === 0) return '<span class="iot-json-bracket">[]</span>';
    const items = value.map(v => `${padInner(1)}${highlightJson(v, indent)}`);
    return `<span class="iot-json-bracket">[</span>\n${items.join(',\n')}\n${pad(1)}<span class="iot-json-bracket">]</span>`;
  }

  if (typeof value === 'object') {
    const keys = Object.keys(value);
    if (keys.length === 0) return '<span class="iot-json-bracket">{}</span>';
    const entries = keys.map(k => {
      const escapedKey = k.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
      return `${padInner(1)}<span class="iot-json-key">"${escapedKey}"</span><span class="iot-json-colon">: </span>${highlightJson(value[k], indent)}`;
    });
    return `<span class="iot-json-bracket">{</span>\n${entries.join(',\n')}\n${pad(1)}<span class="iot-json-bracket">}</span>`;
  }

  return String(value);
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

// --- Tab Interface ---

interface Tab {
  id: string;
  name: string;
  method: string;
  url: string;
  headers: HeaderItem[];
  parameters: ParameterItem[];
  postExtractions?: ExtractionRule[];
  body?: string;
  savedRequestId?: number;
  hasUnsavedChanges?: boolean;
  response?: any;
  responseTime?: number;
}

interface IoTDataPushPlatformProps {
  currentEnvironmentId?: string;
  canManageGlobalParams?: boolean;
}

let tabCounter = 0;

const IoTDataPushPlatform: React.FC<IoTDataPushPlatformProps> = ({
  currentEnvironmentId: propEnvironmentId,
  canManageGlobalParams = false,
}) => {
  const [form] = Form.useForm();
  const [activeTabId, setActiveTabId] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [sendLoading, setSendLoading] = useState(false);
  const [saveLoading, setSaveLoading] = useState(false);
  const [headers, setHeaders] = useState<HeaderItem[]>([{ key: 'Content-Type', value: 'application/json' }]);
  const [parameters, setParameters] = useState<ParameterItem[]>([]);
  const [savedRequests, setSavedRequests] = useState<SavedRequest[]>([]);
  const [saveRequestName, setSaveRequestName] = useState('');
  const [editingRequest, setEditingRequest] = useState<SavedRequest | null>(null);
  const [tabs, setTabs] = useState<Tab[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>([{ id: 'env-1', name: '默认环境', parameters: [] }]);
  const [currentEnvironmentId, setCurrentEnvironmentId] = useState<string>(() => {
    return localStorage.getItem('currentEnvironmentId') || 'env-1';
  });
  const [isGlobalParamsModalVisible, setIsGlobalParamsModalVisible] = useState(false);
  const [postExtractions, setPostExtractions] = useState<ExtractionRule[]>([]);
  const [searchRequests, setSearchRequests] = useState('');

  const jsonExtensions = useMemo(() => [json()], []);

  const activeTab = useMemo(() => tabs.find(t => t.id === activeTabId), [tabs, activeTabId]);
  const response = activeTab?.response || null;
  const responseTime = activeTab?.responseTime || 0;

  const handleBodyChange = useCallback((value: string) => {
    form.setFieldsValue({ body: value });
    setTabs(prevTabs => prevTabs.map(tab =>
      tab.id === activeTabId ? { ...tab, body: value, hasUnsavedChanges: true } : tab
    ));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTabId]);

  // --- Drag Resize ---
  const containerRef = useRef<HTMLDivElement>(null);
  const [leftWidth, setLeftWidth] = useState(35);
  const [middleWidth, setMiddleWidth] = useState(40);
  const [rightCollapsed, setRightCollapsed] = useState(false);
  const [rightWidth, setRightWidth] = useState(25);
  const [isDragging, setIsDragging] = useState<'left' | 'right' | null>(null);
  const startXRef = useRef(0);
  const startWidthsRef = useRef({ left: 0, middle: 0, right: 0 });

  const handleMouseDown = useCallback((divider: 'left' | 'right', e: React.MouseEvent) => {
    e.preventDefault();
    setIsDragging(divider);
    startXRef.current = e.clientX;
    startWidthsRef.current = { left: leftWidth, middle: middleWidth, right: rightWidth };
  }, [leftWidth, middleWidth, rightWidth]);

  useEffect(() => {
    if (!isDragging) return;
    const containerWidth = containerRef.current?.offsetWidth || window.innerWidth;
    const minW = 15;
    const savedRightWidth = rightCollapsed ? 0 : startWidthsRef.current.right;

    const handleMouseMove = (e: MouseEvent) => {
      const dx = e.clientX - startXRef.current;
      const dxPercent = (dx / containerWidth) * 100;

      if (isDragging === 'left') {
        const newLeft = Math.max(minW, Math.min(containerWidth * 0.6, startWidthsRef.current.left + dxPercent));
        const newMiddle = 100 - newLeft - savedRightWidth;
        if (newMiddle >= minW) {
          setLeftWidth(newLeft);
          setMiddleWidth(newMiddle);
        }
      } else {
        const newMiddle = Math.max(minW, Math.min(containerWidth * 0.6, startWidthsRef.current.middle + dxPercent));
        const newRight = 100 - startWidthsRef.current.left - newMiddle;
        if (newRight >= minW || rightCollapsed) {
          setMiddleWidth(newMiddle);
          if (!rightCollapsed) setRightWidth(newRight);
        }
      }
    };

    const handleMouseUp = () => setIsDragging(null);

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging, rightCollapsed]);

  // --- Data Fetching ---

  useEffect(() => {
    fetchSavedRequests();
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
    const defaultTab: Tab = {
      id: `tab-${Date.now()}-${++tabCounter}`,
      name: '新请求',
      method: 'GET',
      url: '',
      headers: [{ key: 'Content-Type', value: 'application/json' }],
      parameters: [],
      body: '',
      hasUnsavedChanges: false
    };
    setTabs([defaultTab]);
    setActiveTabId(defaultTab.id);
  }, []);

  const getCurrentEnvironment = (): Environment => {
    return environments.find(env => env.id === currentEnvironmentId) || environments[0];
  };

  // --- Environment Management ---

  const handleEnvironmentParameterChange = async (index: number, field: 'key' | 'value', value: string) => {
    const newEnvironments = [...environments];
    const envIndex = newEnvironments.findIndex(env => env.id === currentEnvironmentId);
    if (envIndex !== -1) {
      const newParameters = [...newEnvironments[envIndex].parameters];
      newParameters[index] = { ...newParameters[index], [field]: value };
      newEnvironments[envIndex] = { ...newEnvironments[envIndex], parameters: newParameters };
      setEnvironments(newEnvironments);
      await saveEnvironmentToBackend(newEnvironments[envIndex]);
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
        }
      },
    });
  };

  const [editingEnvId, setEditingEnvId] = useState<string | null>(null);
  const [editingEnvName, setEditingEnvName] = useState('');

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
    }
  };

  const handleCancelEditEnvironmentName = () => {
    setEditingEnvId(null);
    setEditingEnvName('');
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
    }
  }, [activeTabId, tabs, form]);

  // --- API Calls ---

  const fetchSavedRequests = async () => {
    try {
      const response = await savedRequestApi.getRequests();
      if (response.code === 200 && response.data) {
        setSavedRequests(response.data);
      }
    } catch (error) {
      console.error('Failed to fetch saved requests:', error);
      message.error('获取保存的请求配置失败');
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

  const updateCurrentTab = (updates: Partial<Tab>) => {
    setTabs(prevTabs => prevTabs.map(tab =>
      tab.id === activeTabId ? { ...tab, ...updates, hasUnsavedChanges: true } : tab
    ));
  };

  const handleRemoveHeader = (index: number) => {
    const newHeaders = headers.filter((_, i) => i !== index);
    setHeaders(newHeaders);
    updateCurrentTab({ headers: newHeaders });
  };

  const handleHeaderChange = (index: number, field: 'key' | 'value', value: string) => {
    const newHeaders = [...headers];
    newHeaders[index] = { ...newHeaders[index], [field]: value };
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

  const handleParameterChange = (index: number, field: 'key' | 'value', value: string) => {
    const newParameters = [...parameters];
    newParameters[index] = { ...newParameters[index], [field]: value };
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

  const handleExtractionChange = (index: number, field: 'variable' | 'jsonpath', value: string) => {
    const newExtractions = [...postExtractions];
    newExtractions[index] = { ...newExtractions[index], [field]: value };
    setPostExtractions(newExtractions);
    updateCurrentTab({ postExtractions: newExtractions });
  };

  // --- Tab Management ---

  const addNewTab = () => {
    const newTab: Tab = {
      id: `tab-${Date.now()}-${++tabCounter}`,
      name: '新请求',
      method: 'GET',
      url: '',
      headers: [{ key: 'Content-Type', value: 'application/json' }],
      parameters: [],
      body: '',
      hasUnsavedChanges: false
    };
    setTabs(prevTabs => [...prevTabs, newTab]);
    setActiveTabId(newTab.id);
    setEditingRequest(null);
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
    const newTab: Tab = {
      id: `tab-${Date.now()}-${++tabCounter}`,
      name: request.name,
      method: request.method,
      url: request.url,
      headers: request.headers,
      parameters: request.parameters || [],
      body: request.body,
      postExtractions: (request as any).post_extractions || [],
      savedRequestId: request.id,
      hasUnsavedChanges: false
    };
    setTabs(prevTabs => [...prevTabs, newTab]);
    setActiveTabId(newTab.id);
    setEditingRequest(request);
    setSaveRequestName(request.name);
  };

  // --- Send Request ---

  const handleSendRequest = async () => {
    try {
      const values = await form.validateFields();
      const { method, url, body } = values;

      setSendLoading(true);
      const startTime = Date.now();

      const processedHeaders = headers.reduce((acc, header) => {
        if (header.key && header.value) acc[header.key] = header.value;
        return acc;
      }, {} as Record<string, string>);

      const processedParams = parameters.reduce((acc, param) => {
        if (param.key && param.value) acc[param.key] = param.value;
        return acc;
      }, {} as Record<string, string>);

      const fileParams: Array<{ key: string; fileId: string; fileName: string }> = [];
      for (const param of parameters) {
        if (param.type === 'file' && param.key && param.file) {
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
      });

      const endTime = Date.now();
      const axiosResponse = {
        status: proxyResponse.status_code,
        statusText: '',
        headers: proxyResponse.headers,
        data: proxyResponse.data
      };

      updateCurrentTab({ method, url, body, headers, parameters, response: axiosResponse, responseTime: endTime - startTime });
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
            fetchGlobalParameters(String(envId));
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

  const handleSaveRequest = async () => {
    try {
      const values = await form.validateFields();
      const { method, url, body } = values;

      const currentTab = tabs.find(t => t.id === activeTabId);
      const requestName = currentTab?.name?.trim() || '新请求';

      setSaveLoading(true);

      // If no savedRequestId yet, set editingRequest to null for create mode
      if (currentTab?.savedRequestId) {
        const saved = savedRequests.find(r => r.id === currentTab.savedRequestId);
        if (saved) setEditingRequest(saved);
      } else {
        setEditingRequest(null);
      }

      const payload = {
        name: requestName,
        method, url, headers, parameters, body,
        post_extractions: postExtractions
      };

      if (editingRequest) {
        const response = await savedRequestApi.updateRequest(editingRequest.id, payload);
        if (response.code === 200 && response.data) {
          setSavedRequests(savedRequests.map(r => r.id === editingRequest.id ? response.data! : r));
          setTabs(prevTabs => prevTabs.map(tab =>
            tab.id === activeTabId ? { ...tab, name: requestName, method, url, headers, parameters, body, postExtractions, savedRequestId: response.data!.id, hasUnsavedChanges: false } : tab
          ));
          message.success('请求配置已更新');
        }
      } else {
        const response = await savedRequestApi.createRequest(payload as any);
        if (response.code === 200 && response.data) {
          setSavedRequests([response.data, ...savedRequests]);
          setTabs(prevTabs => prevTabs.map(tab =>
            tab.id === activeTabId ? { ...tab, name: requestName, method, url, headers, parameters, body, postExtractions, savedRequestId: response.data!.id, hasUnsavedChanges: false } : tab
          ));
          setEditingRequest(response.data);
          message.success('请求配置已保存');
        }
      }
    } catch (error) {
      console.error('Save request failed:', error);
      message.error('保存请求配置失败');
    } finally {
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
        post_extractions: (record as any).post_extractions
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

  // --- Response Status Helper ---

  const getStatusClass = (status: number) => {
    if (status >= 200 && status < 300) return 'is-success';
    if (status >= 400 && status < 500) return 'is-warning';
    return 'is-error';
  };

  // --- Response HTML ---

  const responseHtml = useMemo(() => {
    if (!response?.data) return '';
    try {
      return highlightJson(response.data);
    } catch {
      return '';
    }
  }, [response?.data]);

  // --- Saved Requests Filtered ---
  const filteredSavedRequests = useMemo(() =>
    savedRequests.filter(r => !searchRequests || r.name.toLowerCase().includes(searchRequests.toLowerCase())),
    [savedRequests, searchRequests]
  );

  // --- Render ---

  return (
    <div className="iot-container">
      {/* Toolbar */}
      <div className="iot-toolbar">
        <span className="iot-toolbar-title">
          <ApiOutlined style={{ fontSize: 16 }} />
          IoT 数据推送平台
        </span>
        <Space>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={addNewTab}
            className="iot-btn-primary"
          >
            新增请求
          </Button>
        </Space>
      </div>

      {/* Three-panel Layout */}
      <div ref={containerRef} className="iot-layout">
        {/* Left Panel: Request Config */}
        <div
          className={`iot-panel iot-panel-left ${sendLoading ? 'is-sending' : ''}`}
          style={{ width: `${rightCollapsed ? leftWidth + rightWidth / 2 : leftWidth}%` }}
        >
          <Tabs
            activeKey={activeTabId}
            onChange={setActiveTabId}
            size="small"
            className="iot-tabs"
            style={{ height: '100%', display: 'flex', flexDirection: 'column' }}
            items={tabs.map(tab => ({
              key: tab.id,
              label: (
                <Space size={4}>
                  <span className={tab.hasUnsavedChanges ? 'iot-tab-unsaved' : ''}>{tab.name}</span>
                  <button
                    className="iot-tab-close"
                    onClick={(e) => { e.stopPropagation(); closeTab(tab.id); }}
                  >
                    &times;
                  </button>
                </Space>
              ),
              children: (
                <div style={{ height: '100%', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                  {/* Editable Request Name */}
                  <div className="iot-request-name-bar">
                    <input
                      className="iot-request-name-input"
                      value={activeTab?.name || ''}
                      onChange={(e) => {
                        const newName = e.target.value;
                        setTabs(prevTabs => prevTabs.map(tab =>
                          tab.id === activeTabId ? { ...tab, name: newName, hasUnsavedChanges: true } : tab
                        ));
                        setSaveRequestName(newName);
                      }}
                      placeholder="未命名请求"
                    />
                    {activeTab?.hasUnsavedChanges && <span className="iot-unsaved-dot" title="有未保存的更改" />}
                  </div>

                  <Form form={form} layout="vertical" size="middle" className="iot-form" style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
                    {/* URL Bar + Actions */}
                    <div className="iot-url-bar">
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
                        <Form.Item name="url" noStyle rules={[{ required: true, message: '请输入URL' }]}>
                          <Input
                            placeholder="请输入请求 URL，支持 {{variable}} 变量"
                            className="iot-url-input"
                            onChange={(e) => updateCurrentTab({ url: e.target.value })}
                          />
                        </Form.Item>
                        <Form.Item name="body" hidden>
                          <Input />
                        </Form.Item>
                      </Space.Compact>
                      <div className="iot-url-actions">
                        <Button
                          type="primary"
                          className={`iot-btn-send ${sendLoading ? 'is-loading' : ''}`}
                          icon={<SendOutlined />}
                          onClick={handleSendRequest}
                          loading={sendLoading}
                        >
                          发送
                        </Button>
                        <Tooltip title={editingRequest ? '更新已保存的请求配置' : '保存为新请求配置'} overlayClassName="iot-tooltip">
                          <Button
                            icon={<SaveOutlined />}
                            onClick={handleSaveRequest}
                            loading={saveLoading}
                            style={{ borderColor: 'var(--color-border)', color: 'var(--color-text-secondary)' }}
                          />
                        </Tooltip>
                      </div>
                    </div>

                    {/* Scrollable Config Sections */}
                    <div style={{ flex: 1, overflow: 'auto', padding: '0 14px 14px' }}>

                    {/* Headers */}
                    <Form.Item label="请求头" style={{ marginBottom: 12 }}>
                      <div className="iot-kv-section">
                        {headers.map((header, index) => (
                          <div className="iot-kv-row" key={index}>
                            <Input
                              placeholder="Key"
                              value={header.key}
                              onChange={(e) => handleHeaderChange(index, 'key', e.target.value)}
                              style={{ width: 180 }}
                            />
                            <Input
                              placeholder="Value"
                              value={header.value}
                              onChange={(e) => handleHeaderChange(index, 'value', e.target.value)}
                              style={{ flex: 1 }}
                            />
                            <Button
                              icon={<MinusOutlined />}
                              className="iot-delete-btn"
                              onClick={() => handleRemoveHeader(index)}
                            />
                          </div>
                        ))}
                        <Button
                          type="dashed"
                          icon={<PlusOutlined />}
                          onClick={handleAddHeader}
                          className="iot-add-btn"
                          block
                        >
                          添加请求头
                        </Button>
                      </div>
                    </Form.Item>

                    {/* Parameters */}
                    <Form.Item label="请求参数" style={{ marginBottom: canManageGlobalParams ? 12 : 0 }}>
                      <div className="iot-kv-section">
                        {parameters.map((param, index) => (
                          <div key={index} style={{ marginBottom: 6, display: 'flex', alignItems: 'flex-start', gap: 6 }}>
                            <div className="iot-param-type">
                              <Radio.Group
                                value={param.type || 'text'}
                                size="small"
                                optionType="button"
                                buttonStyle="solid"
                                options={[
                                  { label: '文本', value: 'text' },
                                  { label: '文件', value: 'file' },
                                ]}
                                onChange={(e) => handleParameterTypeChange(index, e.target.value as 'text' | 'file')}
                              />
                            </div>
                            <Input
                              placeholder="参数名"
                              value={param.key}
                              onChange={(e) => handleParameterChange(index, 'key', e.target.value)}
                              style={{ width: 140 }}
                            />
                            {param.type === 'file' ? (
                              <div style={{ flex: 1, minWidth: 200 }}>
                                <FileUpload
                                  value={param.file ? [param.file] : []}
                                  onChange={(files) => handleParameterFileChange(index, files[0])}
                                  maxSize={50}
                                  onUploadSuccess={(result) => {
                                    const newParams = [...parameters];
                                    newParams[index] = { ...newParams[index], file: result, value: result.fileId };
                                    setParameters(newParams);
                                  }}
                                  baseUrl={getCurrentEnvironment().parameters.find(p => p.key === 'baseUrl')?.value}
                                  accessToken={getCurrentEnvironment().parameters.find(p => p.key === 'access-token')?.value}
                                />
                              </div>
                            ) : (
                              <Input
                                placeholder="参数值"
                                value={param.value}
                                onChange={(e) => handleParameterChange(index, 'value', e.target.value)}
                                style={{ flex: 1, minWidth: 150 }}
                              />
                            )}
                            <Button
                              icon={<MinusOutlined />}
                              className="iot-delete-btn"
                              onClick={() => handleRemoveParameter(index)}
                            />
                          </div>
                        ))}
                        <Button
                          type="dashed"
                          icon={<PlusOutlined />}
                          onClick={handleAddParameter}
                          className="iot-add-btn"
                          block
                        >
                          添加参数
                        </Button>
                      </div>
                    </Form.Item>

                    {/* Post Extractions */}
                    {canManageGlobalParams && (
                      <Form.Item
                        label={
                          <Space size={4}>
                            <span>后置提取</span>
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
                          </Space>
                        }
                        style={{ marginBottom: 0 }}
                      >
                        {postExtractions.length === 0 ? (
                          <div className="iot-extraction-empty">
                            暂无提取规则
                            <Button type="link" size="small" onClick={handleAddExtraction} className="iot-extraction-add-link">
                              添加
                            </Button>
                          </div>
                        ) : (
                          <div className="iot-kv-section">
                            {postExtractions.map((rule, index) => (
                              <div className="iot-kv-row" key={index}>
                                <Input
                                  placeholder="变量名"
                                  value={rule.variable}
                                  onChange={(e) => handleExtractionChange(index, 'variable', e.target.value)}
                                  style={{ width: 140 }}
                                />
                                <Input
                                  placeholder="JSONPath，如 $.data.token"
                                  value={rule.jsonpath}
                                  onChange={(e) => handleExtractionChange(index, 'jsonpath', e.target.value)}
                                  style={{ flex: 1 }}
                                />
                                <Button
                                  icon={<MinusOutlined />}
                                  className="iot-delete-btn"
                                  onClick={() => handleRemoveExtraction(index)}
                                />
                              </div>
                            ))}
                            <Button
                              type="dashed"
                              icon={<PlusOutlined />}
                              onClick={handleAddExtraction}
                              className="iot-add-btn"
                              block
                            >
                              添加提取规则
                            </Button>
                          </div>
                        )}
                      </Form.Item>
                    )}
                    </div>
                  </Form>
                </div>
              )
            }))}
          />
        </div>

        {/* Left Drag Handle */}
        {!rightCollapsed && (
          <div
            className={`iot-drag-handle ${isDragging === 'left' ? 'is-active' : ''}`}
            onMouseDown={(e) => handleMouseDown('left', e)}
          />
        )}

        {/* Middle Panel: Request Body + Response */}
        <div
          className="iot-panel-middle"
          style={{ width: rightCollapsed ? `${middleWidth + rightWidth / 2}%` : `${middleWidth}%` }}
        >
          {/* Request Body */}
          <div className="iot-panel" style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            <div className="iot-panel-header">
              <span className="iot-panel-header-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                请求体
              </span>
              <Button
                type="text"
                icon={<FormatPainterOutlined />}
                className="iot-format-btn"
                onClick={() => {
                  const currentBody = form.getFieldValue('body');
                  if (currentBody) {
                    try {
                      const formatted = formatJsonWithComments(currentBody);
                      form.setFieldsValue({ body: formatted });
                      updateCurrentTab({ body: formatted });
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
                value={form.getFieldValue('body') || ''}
                height="100%"
                extensions={jsonExtensions}
                onChange={handleBodyChange}
                placeholder="请输入请求体（JSON 格式）"
                basicSetup={{
                  lineNumbers: true,
                  foldGutter: true,
                  highlightActiveLine: true,
                }}
              />
            </div>
          </div>

          {/* Response */}
          <div className="iot-response-container">
            {response ? (
              <>
                <div className="iot-response-header">
                  <div className="iot-response-status">
                    <span className="iot-panel-header-title">响应结果</span>
                    <span className={`iot-response-status-code ${getStatusClass(response.status)}`}>
                      {response.status} {response.statusText}
                    </span>
                    <span className="iot-response-time">
                      耗时: <strong>{responseTime}ms</strong>
                    </span>
                  </div>
                  <Button
                    icon={<CopyOutlined />}
                    className="iot-copy-btn"
                    onClick={() => {
                      navigator.clipboard.writeText(JSON.stringify(response.data, null, 2));
                      message.success('响应结果已复制到剪贴板');
                    }}
                    size="small"
                  >
                    复制
                  </Button>
                </div>
                <div className="iot-response-body">
                  {responseHtml ? (
                    <pre
                      className="iot-response-pre"
                      dangerouslySetInnerHTML={{ __html: responseHtml }}
                    />
                  ) : (
                    <pre className="iot-response-pre">
                      {typeof response.data === 'string' ? response.data : JSON.stringify(response.data, null, 2)}
                    </pre>
                  )}
                </div>
              </>
            ) : (
              <div className="iot-response-empty">
                <SendOutlined className="iot-response-empty-icon" />
                <span className="iot-response-empty-text">点击发送按钮查看响应结果</span>
              </div>
            )}
          </div>
        </div>

        {/* Right Drag Handle */}
        {!rightCollapsed && (
          <div
            className={`iot-drag-handle ${isDragging === 'right' ? 'is-active' : ''}`}
            onMouseDown={(e) => handleMouseDown('right', e)}
          />
        )}

        {/* Right Panel: Saved Requests */}
        <div
          className={`iot-panel iot-panel-right ${rightCollapsed ? 'is-collapsed' : ''}`}
          style={{
            width: rightCollapsed ? 0 : `${rightWidth}%`,
            minWidth: rightCollapsed ? 0 : 200,
            overflow: 'hidden',
            transition: rightCollapsed ? 'width 0.25s ease, min-width 0.25s ease' : undefined,
          }}
        >
          {rightCollapsed && (
            <Tooltip title="展开保存的请求" placement="left" overlayClassName="iot-tooltip">
              <button className="iot-collapse-btn" onClick={() => setRightCollapsed(false)}>
                <LeftOutlined style={{ fontSize: 12 }} />
              </button>
            </Tooltip>
          )}

          {!rightCollapsed && (
            <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
              <div className="iot-panel-header">
                <span className="iot-panel-header-title">保存的请求</span>
                <Space size={4}>
                  <Button
                    icon={<SyncOutlined />}
                    className="iot-refresh-btn"
                    onClick={fetchSavedRequests}
                    size="small"
                    loading={loading}
                  />
                  <Tooltip title="收起" overlayClassName="iot-tooltip">
                    <Button
                      type="text"
                      icon={<RightOutlined />}
                      onClick={() => setRightCollapsed(true)}
                      size="small"
                      style={{ color: 'var(--color-text-tertiary)' }}
                    />
                  </Tooltip>
                </Space>
              </div>
              <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--color-border)' }}>
                <Input.Search
                  placeholder="搜索名称..."
                  allowClear
                  size="small"
                  className="iot-search-input"
                  onChange={(e) => setSearchRequests(e.target.value)}
                  onSearch={(val) => setSearchRequests(val)}
                />
              </div>
              <div className="iot-saved-list">
                {filteredSavedRequests.length === 0 ? (
                  <div className="iot-saved-empty">
                    <InboxOutlined style={{ fontSize: 24, opacity: 0.3 }} />
                    <span>暂无保存的请求</span>
                  </div>
                ) : (
                  filteredSavedRequests.map(record => (
                    <div key={record.id} className="iot-saved-card">
                      <div className="iot-saved-card-main" onClick={() => createTabFromSavedRequest(record)}>
                        <MethodBadge method={record.method} />
                        <div className="iot-saved-card-info">
                          <span className="iot-saved-card-name">{record.name}</span>
                          {record.url && <span className="iot-saved-card-url">{record.url}</span>}
                        </div>
                      </div>
                      <div className="iot-saved-card-actions">
                        <button className="iot-saved-item-action iot-saved-item-action--info" onClick={() => handleCopySavedRequest(record)}>复制</button>
                        <button className="iot-saved-item-action iot-saved-item-action--danger" onClick={() => handleDeleteSavedRequest(record.id)}>删除</button>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
        </div>

        {/* Collapsed expand button */}
        {rightCollapsed && (
          <Tooltip title="展开保存的请求" placement="left" overlayClassName="iot-tooltip">
            <button className="iot-collapse-btn" onClick={() => setRightCollapsed(false)}>
              <LeftOutlined style={{ fontSize: 12 }} />
            </button>
          </Tooltip>
        )}
      </div>

      {/* Global Params Modal */}
      <Modal
        title="全局参数配置"
        open={isGlobalParamsModalVisible && canManageGlobalParams}
        onOk={() => setIsGlobalParamsModalVisible(false)}
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

      <VariableAssistant
        environmentId={currentEnvironmentId ? parseInt(currentEnvironmentId) || undefined : undefined}
        environmentVariables={getCurrentEnvironment().parameters.filter(p => p.key).map(p => ({ key: p.key, value: p.value }))}
      />
    </div>
  );
};

export default IoTDataPushPlatform;

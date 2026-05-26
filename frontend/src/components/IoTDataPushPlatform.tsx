import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Form, Select, Input, Button, Tabs, Space, Table, message, Modal, Tooltip, Radio } from 'antd';
import { SendOutlined, PlusOutlined, MinusOutlined, CopyOutlined, SaveOutlined, SyncOutlined, FormatPainterOutlined, LeftOutlined, RightOutlined, QuestionCircleOutlined, EditOutlined } from '@ant-design/icons';
import CodeMirror from '@uiw/react-codemirror';
import { json } from '@codemirror/lang-json';
import { savedRequestApi, globalParameterApi, proxyApi } from '../services/api';
import { SavedRequest as SavedRequestType } from '../types';
import FileUpload, { UploadedFileResult } from './FileUpload';

interface HeaderItem {
  key: string;
  value: string;
}

interface ParameterItem {
  key: string;
  value: string;
  type?: 'text' | 'file';  // 参数类型：文本或文件
  file?: UploadedFileResult; // 文件上传结果（type=file 时有效）
}

interface ExtractionRule {
  variable: string;   // 环境变量名
  jsonpath: string;   // JSONPath 表达式
}


interface Environment {
  id: string;
  name: string;
  parameters: ParameterItem[];
}

type SavedRequest = SavedRequestType;

/**
 * 在一行 JSON 文本中找到非字符串内的行内注释起始位置
 */
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

/**
 * 格式化带注释和模板表达式的 JSON，保留注释和模板
 */
function formatJsonWithComments(input: string): string {
  const lines = input.split('\n');

  // 1. 提取注释，关联到最近的后续 key
  const pendingComments: string[] = [];
  const keyStandaloneComments = new Map<string, string[]>(); // key -> 其上方的注释
  const keyInlineComments = new Map<string, string>();        // key -> 行尾注释

  for (const line of lines) {
    const trimmed = line.trim();

    // 独立注释行
    if (trimmed.startsWith('//') || trimmed.startsWith('/*')) {
      pendingComments.push(trimmed);
      continue;
    }

    // 检测 key 行
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

  // 2. 替换未带引号的模板表达式为带引号的占位符，使其可被 JSON.parse 解析
  //    匹配位置：对象值(:)、数组元素([或,)
  //    已被双引号包裹的模板（如 "{{$date(...)}}"）本身已是合法 JSON 字符串，无需处理
  //    对于模板表达式与其他字符拼接的情况（如 {{$a}}.{{$b}}），整个值作为一个占位符
  const placeholders = new Map<string, string>();
  let placeholderIndex = 0;
  const cleaned = stripJsonComments(input).replace(/([:\[,])\s*(?!"|\d|true|false|null|\[|\{(?!\{))((?:[^\s,}\]]*?(?:\{\{(?:[^{}]|\{[^{}]*\})*\}\}|\$\{[^}]*\})[^\s,}\]]*?)+)/g, (_match, prefix, valuePart) => {
    const placeholder = `___PLACEHOLDER_${placeholderIndex}___`;
    placeholders.set(placeholder, valuePart);
    placeholderIndex++;
    return `${prefix} "${placeholder}"`;
  });

  // 3. 解析 → 格式化
  const obj = JSON.parse(cleaned);
  let formatted = JSON.stringify(obj, null, 2);

  // 4. 还原模板表达式：将带引号的占位符替换回原始未引号包裹的模板表达式
  placeholders.forEach((original, placeholder) => {
    formatted = formatted.split(`"${placeholder}"`).join(original);
  });

  // 5. 将注释插回格式化后的 JSON
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

/**
 * 去除 JSON 字符串中的注释（支持 // 单行注释和 /* *\/ 多行注释）
 */
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
      // 单行注释：跳过到行末
      while (i < str.length && str[i] !== '\n') i++;
    } else if (str[i] === '/' && i + 1 < str.length && str[i + 1] === '*') {
      // 多行注释：跳过到 */
      i += 2;
      while (i < str.length && !(str[i] === '*' && i + 1 < str.length && str[i + 1] === '/')) i++;
      if (i < str.length) i += 2; // 跳过 */
    } else {
      result += str[i];
      i++;
    }
  }
  return result;
}

/**
 * 安全解析带注释的 JSON 字符串
 */
function parseJsonWithComments(str: string): any {
  return JSON.parse(stripJsonComments(str));
}

interface Tab {
  id: string;
  name: string;
  method: string;
  url: string;
  headers: HeaderItem[];
  parameters: ParameterItem[];
  postExtractions?: ExtractionRule[];
  body?: string;
  savedRequestId?: number; // 关联的保存请求ID
  hasUnsavedChanges?: boolean; // 是否有未保存的更改
  response?: any; // 当前 tab 的响应结果
  responseTime?: number; // 当前 tab 的响应耗时
}

interface IoTDataPushPlatformProps {
  currentEnvironmentId?: string;
  canManageGlobalParams?: boolean;
}

const IoTDataPushPlatform: React.FC<IoTDataPushPlatformProps> = ({
  currentEnvironmentId: propEnvironmentId,
  canManageGlobalParams = false,
}) => {
  const [form] = Form.useForm();
  const [activeTabId, setActiveTabId] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [headers, setHeaders] = useState<HeaderItem[]>([{ key: 'Content-Type', value: 'application/json' }]);
  const [parameters, setParameters] = useState<ParameterItem[]>([]);
  const [savedRequests, setSavedRequests] = useState<SavedRequest[]>([]);
  const [isSaveModalVisible, setIsSaveModalVisible] = useState(false);
  const [saveRequestName, setSaveRequestName] = useState('');
  const [editingRequest, setEditingRequest] = useState<SavedRequest | null>(null);
  const [tabs, setTabs] = useState<Tab[]>([]); // 标签页列表
  const [environments, setEnvironments] = useState<Environment[]>([{ id: 'env-1', name: '默认环境', parameters: [] }]); // 环境列表
  const [currentEnvironmentId, setCurrentEnvironmentId] = useState<string>(() => {
    return localStorage.getItem('currentEnvironmentId') || 'env-1';
  });
  const [isGlobalParamsModalVisible, setIsGlobalParamsModalVisible] = useState(false); // 全局参数模态框
  const [postExtractions, setPostExtractions] = useState<ExtractionRule[]>([]); // 后置提取规则
  const [searchRequests, setSearchRequests] = useState(''); // 保存的请求搜索关键词


  // 稳定 CodeMirror 扩展引用，避免每次渲染重建
  const jsonExtensions = useMemo(() => [json()], []);

  // 从当前活跃 Tab 中读取响应结果
  const activeTab = useMemo(() => tabs.find(t => t.id === activeTabId), [tabs, activeTabId]);
  const response = activeTab?.response || null;
  const responseTime = activeTab?.responseTime || 0;

  // 请求体内容变更回调
  const handleBodyChange = useCallback((value: string) => {
    form.setFieldsValue({ body: value });
    setTabs(prevTabs => prevTabs.map(tab =>
      tab.id === activeTabId ? { ...tab, body: value, hasUnsavedChanges: true } : tab
    ));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTabId]);

  // 拖拽调整面板大小
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

  // 从后端API获取保存的请求配置
  useEffect(() => {
    fetchSavedRequests();
  }, []);

  // 从后端API获取全局参数配置
  useEffect(() => {
    if (canManageGlobalParams) {
      fetchGlobalParameters();
    }
  }, [canManageGlobalParams]);

  // 同步 App.tsx 传入的环境切换
  useEffect(() => {
    if (propEnvironmentId && propEnvironmentId !== currentEnvironmentId) {
      setCurrentEnvironmentId(propEnvironmentId);
    }
  }, [propEnvironmentId]);

  // 初始化默认标签页
  useEffect(() => {
    const defaultTab: Tab = {
      id: `tab-${Date.now()}`,
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

  // 获取当前环境
  const getCurrentEnvironment = (): Environment => {
    return environments.find(env => env.id === currentEnvironmentId) || environments[0];
  };

  // 处理环境参数变更
  const handleEnvironmentParameterChange = async (index: number, field: 'key' | 'value', value: string) => {
    const newEnvironments = [...environments];
    const envIndex = newEnvironments.findIndex(env => env.id === currentEnvironmentId);
    if (envIndex !== -1) {
      const newParameters = [...newEnvironments[envIndex].parameters];
      newParameters[index] = { ...newParameters[index], [field]: value };
      newEnvironments[envIndex] = { ...newEnvironments[envIndex], parameters: newParameters };
      setEnvironments(newEnvironments);

      // 保存到后端
      await saveEnvironmentToBackend(newEnvironments[envIndex]);
    }
  };

  // 添加环境参数
  const handleAddEnvironmentParameter = async () => {
    const newEnvironments = [...environments];
    const envIndex = newEnvironments.findIndex(env => env.id === currentEnvironmentId);
    if (envIndex !== -1) {
      const newParameters = [...newEnvironments[envIndex].parameters, { key: '', value: '' }];
      newEnvironments[envIndex] = { ...newEnvironments[envIndex], parameters: newParameters };
      setEnvironments(newEnvironments);

      // 保存到后端
      await saveEnvironmentToBackend(newEnvironments[envIndex]);
    }
  };

  // 删除环境参数
  const handleRemoveEnvironmentParameter = async (index: number) => {
    const newEnvironments = [...environments];
    const envIndex = newEnvironments.findIndex(env => env.id === currentEnvironmentId);
    if (envIndex !== -1) {
      const newParameters = newEnvironments[envIndex].parameters.filter((_, i) => i !== index);
      newEnvironments[envIndex] = { ...newEnvironments[envIndex], parameters: newParameters };
      setEnvironments(newEnvironments);

      // 保存到后端
      await saveEnvironmentToBackend(newEnvironments[envIndex]);
    }
  };

  // 新环境名称状态
  const [newEnvironmentName, setNewEnvironmentName] = useState('');
  const [isAddEnvModalVisible, setIsAddEnvModalVisible] = useState(false);

  // 打开添加环境模态框
  const handleAddEnvironment = () => {
    setNewEnvironmentName('');
    setIsAddEnvModalVisible(true);
  };

  // 确认添加环境
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

    // 保存到后端
    const savedEnv = await saveEnvironmentToBackend(newEnv);
    if (savedEnv) {
      // 使用后端返回的ID
      const updatedEnv = {
        ...newEnv,
        id: savedEnv.id.toString()
      };
      setEnvironments([...environments, updatedEnv]);
      setCurrentEnvironmentId(updatedEnv.id);
      setIsAddEnvModalVisible(false);
    }
  };

  // 删除环境
  const handleRemoveEnvironment = async (envId: string) => {
    if (environments.length <= 1) {
      message.warning('至少需要保留一个环境');
      return;
    }

    const { confirm } = Modal;
    confirm({
      title: '确认删除',
      content: '确定要删除这个环境吗？此操作不可撤销。',
      okText: '确定',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        // 从后端删除
        const deleted = await deleteEnvironmentFromBackend(envId);
        if (deleted) {
          const newEnvironments = environments.filter(env => env.id !== envId);
          setEnvironments(newEnvironments);

          // 如果删除的是当前环境，切换到第一个环境
          if (currentEnvironmentId === envId) {
            setCurrentEnvironmentId(newEnvironments[0].id);
          }
        }
      },
    });
  };

  // 编辑环境名称
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
      newEnvironments[envIndex] = {
        ...newEnvironments[envIndex],
        name: editingEnvName.trim()
      };
      setEnvironments(newEnvironments);

      // 保存到后端
      await saveEnvironmentToBackend(newEnvironments[envIndex]);
      setEditingEnvId(null);
    }
  };

  const handleCancelEditEnvironmentName = () => {
    setEditingEnvId(null);
    setEditingEnvName('');
  };

  // 切换环境
  const handleSwitchEnvironment = async (envId: string) => {
    setCurrentEnvironmentId(envId);
    localStorage.setItem('currentEnvironmentId', envId);

    // 更新默认环境设置
    const currentEnv = environments.find(env => env.id === envId);
    if (currentEnv) {
      const updatedEnv = {
        ...currentEnv,
        is_default: true
      };
      await saveEnvironmentToBackend(updatedEnv);
    }
  };

  // 当活跃标签页变化时，更新表单数据
  useEffect(() => {
    const activeTab = tabs.find(tab => tab.id === activeTabId);
    if (activeTab) {
      form.setFieldsValue({
        method: activeTab.method,
        url: activeTab.url,
        body: activeTab.body
      });
      setHeaders(activeTab.headers);
      setParameters(activeTab.parameters);
      setPostExtractions(activeTab.postExtractions || []);
    }
  }, [activeTabId, tabs, form]);

  // 从后端API获取保存的请求配置
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



  // 从后端API获取全局参数配置
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
          // 优先使用 localStorage 中保存的环境，若不存在则使用默认环境
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
      // 如果获取失败，使用默认环境
      console.log('Using default environment');
    }
  };

  // 保存环境配置到后端
  const saveEnvironmentToBackend = async (environment: any) => {
    if (!canManageGlobalParams) return null;
    try {
      // 检查环境是否已存在（通过id判断）
      if (parseInt(environment.id)) {
        // 更新现有环境
        const response = await globalParameterApi.updateEnvironment(parseInt(environment.id), {
          name: environment.name,
          parameters: environment.parameters,
          is_default: environment.is_default || false
        });
        return response.data;
      } else {
        // 创建新环境
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

  // 删除环境配置
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

  const methods = ['GET', 'POST', 'PUT', 'DELETE', 'PATCH'];

  const handleRemoveHeader = (index: number) => {
    const newHeaders = headers.filter((_, i) => i !== index);
    setHeaders(newHeaders);

    // 更新当前标签页的请求头
    updateCurrentTab({
      headers: newHeaders
    });
  };

  const handleHeaderChange = (index: number, field: 'key' | 'value', value: string) => {
    const newHeaders = [...headers];
    newHeaders[index] = { ...newHeaders[index], [field]: value };
    setHeaders(newHeaders);

    // 更新当前标签页的请求头
    updateCurrentTab({
      headers: newHeaders
    });
  };

  const handleAddHeader = () => {
    const newHeaders = [...headers, { key: '', value: '' }];
    setHeaders(newHeaders);

    // 更新当前标签页的请求头
    updateCurrentTab({
      headers: newHeaders
    });
  };

  const handleAddParameter = () => {
    const newParameters = [...parameters, { key: '', value: '' }];
    setParameters(newParameters);

    // 更新当前标签页的参数
    updateCurrentTab({
      parameters: newParameters
    });
  };

  const handleRemoveParameter = (index: number) => {
    const newParameters = parameters.filter((_, i) => i !== index);
    setParameters(newParameters);

    // 更新当前标签页的参数
    updateCurrentTab({
      parameters: newParameters
    });
  };

  const handleParameterChange = (index: number, field: 'key' | 'value', value: string) => {
    const newParameters = [...parameters];
    newParameters[index] = { ...newParameters[index], [field]: value };
    setParameters(newParameters);

    // 更新当前标签页的参数
    updateCurrentTab({
      parameters: newParameters
    });
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

  // 后置提取规则操作
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

  // 更新当前标签页的内容
  const updateCurrentTab = (updates: Partial<Tab>) => {
    setTabs(prevTabs => prevTabs.map(tab =>
      tab.id === activeTabId ? { ...tab, ...updates, hasUnsavedChanges: true } : tab
    ));
  };

  // 添加新标签页
  const addNewTab = () => {
    const newTab: Tab = {
      id: `tab-${Date.now()}`,
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
    setEditingRequest(null); // 新标签页不是编辑模式
  };

  // 关闭标签页
  const closeTab = (tabId: string) => {
    if (tabs.length === 1) {
      message.warning('至少需要保留一个标签页');
      return;
    }

    // 检查是否有未保存的更改
    const tabToClose = tabs.find(tab => tab.id === tabId);
    if (tabToClose?.hasUnsavedChanges) {
      const { confirm } = Modal;
      confirm({
        title: '确认关闭',
        content: '当前标签页有未保存的更改，确定要关闭吗？',
        okText: '不保存并关闭',
        okType: 'danger',
        cancelText: '取消',
        onOk: () => {
          const newTabs = tabs.filter(tab => tab.id !== tabId);
          setTabs(newTabs);

          // 如果关闭的是当前活跃标签页，切换到第一个标签页
          if (tabId === activeTabId) {
            setActiveTabId(newTabs[0].id);
          }
        },
      });
    } else {
      const newTabs = tabs.filter(tab => tab.id !== tabId);
      setTabs(newTabs);

      // 如果关闭的是当前活跃标签页，切换到第一个标签页
      if (tabId === activeTabId) {
        setActiveTabId(newTabs[0].id);
      }
    }
  };

  // 从保存的请求创建标签页
  const createTabFromSavedRequest = (request: SavedRequest) => {
    const newTab: Tab = {
      id: `tab-${Date.now()}`,
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
    setEditingRequest(request); // 设置为编辑模式
    setSaveRequestName(request.name);
  };

  const handleSendRequest = async () => {
    try {
      const values = await form.validateFields();
      const { method, url, body } = values;

      setLoading(true);
      const startTime = Date.now();

      // 构建 headers 字典
      const processedHeaders = headers.reduce((acc, header) => {
        if (header.key && header.value) {
          acc[header.key] = header.value;
        }
        return acc;
      }, {} as Record<string, string>);

      // 构建 params 字典（文件类型参数使用 fileId 作为值）
      const processedParams = parameters.reduce((acc, param) => {
        if (param.key && param.value) {
          acc[param.key] = param.value;
        }
        return acc;
      }, {} as Record<string, string>);

      // 收集文件参数信息，供后端构建 multipart
      const fileParams: Array<{ key: string; fileId: string; fileName: string }> = [];
      for (const param of parameters) {
        if (param.type === 'file' && param.key && param.file) {
          fileParams.push({
            key: param.key,
            fileId: param.file.fileId,
            fileName: param.file.fileName,
          });
        }
      }

      // 解析 body
      let requestData: any = undefined;
      if (['POST', 'PUT', 'PATCH'].includes(method) && body) {
        try {
          requestData = parseJsonWithComments(body);
        } catch {
          requestData = body;
        }
      }

      // 发送到后端代理（变量替换在后端执行）
      const envId = canManageGlobalParams ? parseInt(currentEnvironmentId) || null : null;
      const proxyResponse = await proxyApi.forwardRequest({
        url,
        method,
        headers: processedHeaders,
        data: requestData,
        params: processedParams,
        file_params: fileParams.length > 0 ? fileParams : undefined,
        environment_id: envId,
      });

      const endTime = Date.now();
      const timeTaken = endTime - startTime;

      // Create axios-like response object
      const axiosResponse = {
        status: proxyResponse.status_code,
        statusText: '',
        headers: proxyResponse.headers,
        data: proxyResponse.data
      };

      // 更新当前标签页的内容及响应结果
      updateCurrentTab({
        method,
        url,
        body,
        headers,
        parameters,
        response: axiosResponse,
        responseTime: timeTaken,
      });

      message.success('请求成功');

      // 后置提取：从响应中提取变量并保存到环境
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
            // 刷新环境参数
            fetchGlobalParameters(String(envId));
          }
        } catch (extractError: any) {
          const detail = extractError.response?.data?.detail || extractError.message;
          message.error(`提取失败: ${detail}`);
        }
      }

    } catch (error: any) {
      if (error.response) {
        // 代理服务返回错误
        if (error.response.data) {
          // 目标服务器返回的错误
          const proxyError = error.response.data;
          const errResponse = {
            status: proxyError.status_code || error.response.status,
            statusText: '',
            headers: proxyError.headers || {},
            data: proxyError.data || error.response.data
          };
          updateCurrentTab({ response: errResponse, responseTime: 0 });
          message.error(`请求失败: ${proxyError.status_code || error.response.status} ${proxyError.detail || 'Unknown error'}`);
        } else {
          // 代理服务本身的错误
          updateCurrentTab({ response: error.response, responseTime: 0 });
          message.error(`请求失败: ${error.response.status} ${error.response.statusText}`);
        }
      } else if (error.request) {
        // 请求已发出但没有收到响应
        message.error('请求失败: 没有收到响应');
      } else {
        // 请求配置出错
        message.error(`请求失败: ${error.message}`);
      }
    } finally {
      setLoading(false);
    }

  };

  // 打开保存请求配置模态框
  const handleOpenSaveModal = () => {
    setIsSaveModalVisible(true);

    // 如果当前标签页已经关联了保存的请求，设置为编辑模式
    const activeTab = tabs.find(tab => tab.id === activeTabId);
    if (activeTab) {
      setSaveRequestName(activeTab.name);

      // 查找对应的保存请求
      if (activeTab.savedRequestId) {
        const savedRequest = savedRequests.find(req => req.id === activeTab.savedRequestId);
        if (savedRequest) {
          setEditingRequest(savedRequest);
        }
      } else {
        setEditingRequest(null);
      }
    }
  };

  // 保存请求配置
  const handleSaveRequest = async () => {
    try {
      const values = await form.validateFields();
      const { method, url, body } = values;

      if (!saveRequestName.trim()) {
        message.error('请输入请求配置名称');
        return;
      }

      setLoading(true);

      if (editingRequest) {
        // 编辑现有请求
        const updatedRequest = {
          name: saveRequestName.trim(),
          method,
          url,
          headers,
          parameters,
          body,
          post_extractions: postExtractions
        };

        const response = await savedRequestApi.updateRequest(editingRequest.id, updatedRequest);
        if (response.code === 200 && response.data) {
          // 更新本地状态
          const updatedRequests = savedRequests.map(req =>
            req.id === editingRequest.id ? response.data! : req
          );
          setSavedRequests(updatedRequests);

          // 更新当前标签页的名称和关联的保存请求ID
          setTabs(prevTabs => prevTabs.map(tab =>
            tab.id === activeTabId ? {
              ...tab,
              name: saveRequestName.trim(),
              method,
              url,
              headers,
              parameters,
              body,
              postExtractions,
              savedRequestId: response.data!.id,
              hasUnsavedChanges: false
            } : tab
          ));
          message.success('请求配置已更新');
        }
      } else {
        // 保存新请求
        const newRequest = {
          name: saveRequestName.trim(),
          method,
          url,
          headers,
          parameters,
          body,
          post_extractions: postExtractions
        };

        const response = await savedRequestApi.createRequest(newRequest as any);
        if (response.code === 200 && response.data) {
          setSavedRequests([response.data, ...savedRequests]);
          message.success('请求配置已保存');

          // 更新当前标签页的名称和关联的保存请求ID
          setTabs(prevTabs => prevTabs.map(tab =>
            tab.id === activeTabId ? {
              ...tab,
              name: saveRequestName.trim(),
              method,
              url,
              headers,
              parameters,
              body,
              postExtractions,
              savedRequestId: response.data!.id,
              hasUnsavedChanges: false
            } : tab
          ));

          // 设置为编辑模式，下次保存时更新
          setEditingRequest(response.data);
        }
      }

      setIsSaveModalVisible(false);
    } catch (error) {
      console.error('Save request failed:', error);
      message.error('保存请求配置失败');
    } finally {
      fetchSavedRequests()
      setLoading(false);
    }
  };

  // 删除保存的请求配置
  const handleDeleteSavedRequest = async (id: number) => {
    try {
      const { confirm } = Modal;
      confirm({
        title: '确认删除',
        content: '确定要删除这个保存的请求配置吗？此操作不可撤销。',
        okText: '确定',
        okType: 'danger',
        cancelText: '取消',
        onOk: async () => {
          try {
            setLoading(true);
            const response = await savedRequestApi.deleteRequest(id);
            if (response.code === 200) {
              setSavedRequests(savedRequests.filter(req => req.id !== id));
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
    } catch (error) {
      console.error('Delete request failed:', error);
      message.error('删除请求配置失败');
    }
  };

  // 复制保存的请求配置
  const handleCopySavedRequest = async (record: SavedRequest) => {
    try {
      setLoading(true);
      const copiedRequest = {
        name: `${record.name}_copy`,
        method: record.method,
        url: record.url,
        headers: record.headers,
        parameters: record.parameters,
        body: record.body,
        post_extractions: (record as any).post_extractions
      };

      const response = await savedRequestApi.createRequest(copiedRequest as any);
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

  const savedRequestsColumns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      ellipsis: true,
      width: 200,
    },
    // {
    //   title: '方法',
    //   dataIndex: 'method',
    //   key: 'method',
    //   width: 60,
    // },
    // {
    //   title: 'URL',
    //   dataIndex: 'url',
    //   key: 'url',
    //   ellipsis: true,
    // },
    {
      title: '操作',
      key: 'action',
      width: 180,
      render: (_: any, record: SavedRequest) => (
        <Space size="small">
          <Button type="text" size="small" style={{ textDecoration: 'underline', color: '#1890ff' }} onClick={() => createTabFromSavedRequest(record)}>
            打开
          </Button>
          <Button type="text" size="small" style={{ textDecoration: 'underline', color: '#1890ff' }} onClick={() => handleCopySavedRequest(record)}>
            复制
          </Button>
          <Button type="text" size="small" style={{ textDecoration: 'underline', color: '#ff4d4f' }} onClick={() => handleDeleteSavedRequest(record.id)}>
            删除
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: '8px 10px', height: 'calc(100vh - 88px)', background: '#f0f2f5', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* 顶部工具栏 */}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        padding: '8px 12px',
        background: '#fff',
        borderRadius: '6px',
        marginBottom: '8px',
        boxShadow: '0 1px 4px rgba(0, 0, 0, 0.08)',
        flexShrink: 0,
      }}>
        <span style={{ fontWeight: 600, fontSize: 15, color: '#333' }}>IoT 数据推送平台</span>
        <Space>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={addNewTab}
            size="middle"
          >
            新增请求
          </Button>
        </Space>
      </div>

      {/* 三栏可拖拽布局 */}
      <div ref={containerRef} style={{ display: 'flex', flex: 1, minHeight: 0, position: 'relative' }}>
        {/* 左侧：请求配置 */}
        <div style={{
          width: `${rightCollapsed ? leftWidth + rightWidth / 2 : leftWidth}%`,
          minWidth: 250,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          background: '#fff',
          borderRadius: '6px',
          boxShadow: '0 1px 4px rgba(0, 0, 0, 0.08)',
          marginRight: rightCollapsed ? '4px' : undefined,
        }}>
          <Tabs
            activeKey={activeTabId}
            onChange={setActiveTabId}
            size="small"
            style={{ height: '100%', display: 'flex', flexDirection: 'column', marginLeft: 10 }}
            items={tabs.map(tab => ({
              key: tab.id,
              label: (
                <Space size={4}>
                  {tab.name}
                  <Button
                    size="small"
                    type="text"
                    danger
                    onClick={(e) => {
                      e.stopPropagation();
                      closeTab(tab.id);
                    }}
                    style={{ fontSize: 12, minWidth: 16, padding: '0 2px' }}
                  >
                    ×
                  </Button>
                </Space>
              ),
              children: (
                <div style={{ height: '100%', overflow: 'auto', padding: '8px 12px' }}>
                  <Form form={form} layout="vertical" size="middle">
                    <Form.Item label="请求配置" style={{ marginBottom: 12 }}>
                      <Space.Compact style={{ width: '100%' }}>
                        <Form.Item name="method" noStyle>
                          <Select
                            style={{ width: 110 }}
                            onChange={(value: string) => updateCurrentTab({ method: value })}
                          >
                            {methods.map(method => (
                              <Select.Option key={method} value={method}>{method}</Select.Option>
                            ))}
                          </Select>
                        </Form.Item>
                        <Form.Item name="url" noStyle rules={[{ required: true, message: '请输入URL' }]}>
                          <Input
                            placeholder="请输入请求URL"
                            onChange={(e) => updateCurrentTab({ url: e.target.value })}
                          />
                        </Form.Item>
                        {/* 隐藏字段，确保 body 能被 validateFields 返回 */}
                        <Form.Item name="body" hidden>
                          <Input />
                        </Form.Item>
                      </Space.Compact>
                      <Space style={{ marginTop: 8, width: '100%' }}>
                        <Button
                          type="primary"
                          icon={<SendOutlined />}
                          onClick={handleSendRequest}
                          loading={loading}
                        >
                          发送
                        </Button>
                        <Button
                          icon={<SaveOutlined />}
                          onClick={handleOpenSaveModal}
                        >
                          保存
                        </Button>
                      </Space>
                    </Form.Item>

                    <Form.Item label="请求头" style={{ marginBottom: 12 }}>
                      <div style={{ padding: '8px', background: '#fafafa', borderRadius: '4px', maxHeight: 200, overflow: 'auto' }}>
                        {headers.map((header, index) => (
                          <Space
                            key={index}
                            style={{ width: '100%', marginBottom: '8px' }}
                            align="center"
                            size={4}
                          >
                            <Input
                              placeholder="Key"
                              value={header.key}
                              onChange={(e) => handleHeaderChange(index, 'key', e.target.value)}
                              style={{ width: 200 }}
                            // size="medium"
                            />
                            <Input
                              placeholder="Value"
                              value={header.value}
                              onChange={(e) => handleHeaderChange(index, 'value', e.target.value)}
                              style={{ flex: 1, width: 330 }}
                            // size="medium"
                            />
                            <Button
                              icon={<MinusOutlined />}
                              danger
                              onClick={() => handleRemoveHeader(index)}
                            // size="medium"
                            />
                          </Space>
                        ))}
                        <Button
                          type="dashed"
                          icon={<PlusOutlined />}
                          onClick={handleAddHeader}
                          style={{ marginTop: '4px' }}
                          // size="small"
                          block
                        >
                          添加请求头
                        </Button>
                      </div>
                    </Form.Item>

                    <Form.Item label="请求参数" style={{ marginBottom: 0 }}>
                      <div style={{ padding: '8px', background: '#fafafa', borderRadius: '4px', maxHeight: 300, overflow: 'auto' }}>
                        {parameters.map((param, index) => (
                          <div key={index} style={{ marginBottom: 6, display: 'flex', alignItems: 'flex-start', gap: 6 }}>
                            {/* 参数类型选择 */}
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
                              style={{ marginTop: 2 }}
                            />
                            <Input
                              placeholder="参数名"
                              value={param.key}
                              onChange={(e) => handleParameterChange(index, 'key', e.target.value)}
                              style={{ width: 160 }}
                            />
                            {param.type === 'file' ? (
                              /* 文件类型：显示文件上传组件 */
                              <div style={{ flex: 1, minWidth: 280 }}>
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
                              /* 文本类型：普通输入框 */
                              <Input
                                placeholder="参数值"
                                value={param.value}
                                onChange={(e) => handleParameterChange(index, 'value', e.target.value)}
                                style={{ flex: 1, minWidth: 200 }}
                              />
                            )}
                            <Button
                              icon={<MinusOutlined />}
                              danger
                              onClick={() => handleRemoveParameter(index)}
                              style={{ marginTop: 2 }}
                            />
                          </div>
                        ))}
                        <Button
                          type="dashed"
                          icon={<PlusOutlined />}
                          onClick={handleAddParameter}
                          style={{ marginTop: '4px' }}
                          block
                        >
                          添加参数
                        </Button>
                      </div>
                    </Form.Item>

                    {canManageGlobalParams && (
                    <Form.Item
                      label={
                        <Space size={4}>
                          <span>后置提取</span>
                          <Tooltip title={
                            <div style={{ fontSize: 12, lineHeight: 1.8 }}>
                              <div>从响应 JSON 中提取值并保存到当前环境变量</div>
                              <div style={{ marginTop: 4, fontWeight: 600 }}>JSONPath 语法示例：</div>
                              <div><code>$.data.token</code> — 提取 data.token</div>
                              <div><code>$.data.list[0].id</code> — 提取数组第一项的 id</div>
                              <div><code>$..name</code> — 递归查找所有 name 字段</div>
                            </div>
                          }>
                            <QuestionCircleOutlined style={{ color: '#999', fontSize: 13 }} />
                          </Tooltip>
                        </Space>
                      }
                      style={{ marginBottom: 0 }}
                    >
                      {postExtractions.length === 0 ? (
                        <div style={{ padding: '4px 8px', color: '#999', fontSize: 12 }}>
                          暂无提取规则
                          <Button type="link" size="small" onClick={handleAddExtraction} style={{ padding: 0, marginLeft: 8 }}>
                            添加
                          </Button>
                        </div>
                      ) : (
                        <div style={{ padding: '8px', background: '#fafafa', borderRadius: '4px', maxHeight: 200, overflow: 'auto' }}>
                          {postExtractions.map((rule, index) => (
                            <Space
                              key={index}
                              style={{ width: '100%', marginBottom: '8px' }}
                              align="center"
                              size={4}
                            >
                              <Input
                                placeholder="变量名"
                                value={rule.variable}
                                onChange={(e) => handleExtractionChange(index, 'variable', e.target.value)}
                                style={{ width: 150 }}
                              />
                              <Input
                                placeholder="JSONPath，如 $.data.token"
                                value={rule.jsonpath}
                                onChange={(e) => handleExtractionChange(index, 'jsonpath', e.target.value)}
                                style={{ flex: 1 }}
                              />
                              <Button
                                icon={<MinusOutlined />}
                                danger
                                onClick={() => handleRemoveExtraction(index)}
                              />
                            </Space>
                          ))}
                          <Button
                            type="dashed"
                            icon={<PlusOutlined />}
                            onClick={handleAddExtraction}
                            style={{ marginTop: '4px' }}
                            block
                          >
                            添加提取规则
                          </Button>
                        </div>
                      )}
                    </Form.Item>
                    )}
                  </Form>
                </div>
              )
            }))}
          />
        </div>

        {/* 左侧拖拽手柄 */}
        {!rightCollapsed && (
          <div
            onMouseDown={(e) => handleMouseDown('left', e)}
            style={{
              width: 6,
              cursor: 'col-resize',
              background: isDragging === 'left' ? '#1890ff' : 'transparent',
              transition: 'background 0.15s',
              flexShrink: 0,
              position: 'relative',
              zIndex: 10,
            }}
            onMouseEnter={(e) => { if (!isDragging) (e.target as HTMLElement).style.background = '#ddd'; }}
            onMouseLeave={(e) => { if (!isDragging) (e.target as HTMLElement).style.background = 'transparent'; }}
          />
        )}

        {/* 中间：请求体 + 响应结果 */}
        <div style={{
          width: rightCollapsed ? `${middleWidth + rightWidth / 2}%` : `${middleWidth}%`,
          display: 'flex',
          flexDirection: 'column',
          gap: '8px',
          minWidth: 250,
          overflow: 'hidden',
        }}>
          {/* 请求体 */}
          <div style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            background: '#fff',
            borderRadius: '6px',
            boxShadow: '0 1px 4px rgba(0, 0, 0, 0.08)',
            overflow: 'hidden',
            minHeight: 0,
          }}>
            <div style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              padding: '6px 12px',
              borderBottom: '1px solid #f0f0f0',
              flexShrink: 0,
            }}>
              <span style={{ fontWeight: 500, fontSize: 14, color: '#333', display: 'flex', alignItems: 'center', gap: 8 }}>请求体
                <Tooltip title={
                  <div style={{ maxWidth: 660, fontSize: 12, lineHeight: 1.8 }}>
                    <div style={{ fontWeight: 600, marginBottom: 4 }}>1. 内置函数 {'{{$function}}'}</div>
                    <table style={{ borderCollapse: 'collapse', width: '100%' }}>
                      <tbody>
                        <tr><td style={{ whiteSpace: 'nowrap', paddingRight: 12 }}><code>{'{{$timestamp}}'}</code></td><td>毫秒时间戳</td></tr>
                        <tr><td><code>{'{{$now}}'}</code></td><td>秒级时间戳</td></tr>
                        <tr><td><code>{'{{$date}}'}</code></td><td>当前日期 (YYYY-MM-DD)</td></tr>
                        <tr><td><code>{"{{$date('YYYY-MM-DD HH:mm:ss')}}"}</code></td><td>自定义格式日期</td></tr>
                        <tr><td><code>{'{{$randomInt}}'}</code></td><td>0~100 随机整数</td></tr>
                        <tr><td><code>{'{{$randomInt(1,1000)}}'}</code></td><td>指定范围随机整数</td></tr>
                        <tr><td><code>{'{{$uuid}}'}</code></td><td>UUID v4</td></tr>
                      </tbody>
                    </table>
                    <div style={{ fontWeight: 600, marginTop: 8, marginBottom: 4 }}>2. JS 表达式 {'{{@expression}}'}</div>
                    <table style={{ borderCollapse: 'collapse', width: '100%' }}>
                      <tbody>
                        <tr><td style={{ whiteSpace: 'nowrap', paddingRight: 12 }}><code>{'{{@Date.now()}}'}</code></td><td>JS 时间戳</td></tr>
                        <tr><td><code>{'{{@Math.random().toFixed(4)}}'}</code></td><td>随机小数</td></tr>
                        <tr><td><code>{'{{@new Date().toISOString()}}'}</code></td><td>ISO 日期</td></tr>
                        <tr><td><code>{"{{@'test_' + Math.floor(Math.random()*1000)}}"}</code></td><td>拼接表达式</td></tr>
                      </tbody>
                    </table>
                  </div>
                }
                  overlayInnerStyle={{ maxWidth: 680 }}>

                  <QuestionCircleOutlined style={{ color: '#999', cursor: 'pointer', fontSize: 13 }} />
                </Tooltip>
              </span>
              <Button
                type="text"
                icon={<FormatPainterOutlined />}
                onClick={() => {
                  const currentBody = form.getFieldValue('body');
                  if (currentBody) {
                    try {
                      const formatted = formatJsonWithComments(currentBody);
                      form.setFieldsValue({ body: formatted });
                      updateCurrentTab({ body: formatted });
                      message.success('JSON 格式化成功');
                    } catch (error) {
                      message.error('JSON 格式不正确');
                    }
                  }
                }}
                size="small"
              >
                格式化
              </Button>
            </div>
            <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
              <CodeMirror
                value={form.getFieldValue('body') || ''}
                height="100%"
                extensions={jsonExtensions}
                onChange={handleBodyChange}
                placeholder="请输入请求体（JSON格式）"
                style={{ fontSize: '16px' }}
                basicSetup={{
                  lineNumbers: true,
                  foldGutter: true,
                  highlightActiveLine: true,
                }}
              />
            </div>
          </div>

          {/* 响应结果 */}
          <div style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            background: '#fff',
            borderRadius: '6px',
            boxShadow: '0 1px 4px rgba(0, 0, 0, 0.08)',
            overflow: 'hidden',
            minHeight: 0,
          }}>
            {response ? (
              <>
                <div style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  padding: '6px 12px',
                  borderBottom: '1px solid #f0f0f0',
                  flexShrink: 0,
                }}>
                  <Space size={16}>
                    <span style={{ fontWeight: 500, fontSize: 14, color: '#333' }}>响应结果</span>
                    <span>
                      状态码: <strong style={{
                        color: response.status >= 200 && response.status < 300 ? '#52c41a' : '#ff4d4f',
                      }}>
                        {response.status} {response.statusText}
                      </strong>
                    </span>
                    <span>
                      耗时: <strong>{responseTime}ms</strong>
                    </span>
                  </Space>
                  <Button
                    icon={<CopyOutlined />}
                    onClick={() => {
                      navigator.clipboard.writeText(JSON.stringify(response.data, null, 2));
                      message.success('响应结果已复制到剪贴板');
                    }}
                    size="small"
                  >
                    复制
                  </Button>
                </div>
                <div style={{
                  flex: 1,
                  fontFamily: 'Consolas, Monaco, monospace',
                  whiteSpace: 'pre-wrap',
                  background: '#f8f9fa',
                  padding: '12px',
                  overflow: 'auto',
                  fontSize: '13px',
                  lineHeight: 1.5,
                  minHeight: 0,
                }}>
                  {JSON.stringify(response.data, null, 2)}
                </div>
              </>
            ) : (
              <div style={{
                flex: 1,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: '#bfbfbf',
                fontSize: 14,
              }}>
                点击发送按钮查看响应结果
              </div>
            )}
          </div>
        </div>

        {/* 右侧拖拽手柄 */}
        {!rightCollapsed && (
          <div
            onMouseDown={(e) => handleMouseDown('right', e)}
            style={{
              width: 6,
              cursor: 'col-resize',
              background: isDragging === 'right' ? '#1890ff' : 'transparent',
              transition: 'background 0.15s',
              flexShrink: 0,
              position: 'relative',
              zIndex: 10,
            }}
            onMouseEnter={(e) => { if (!isDragging) (e.target as HTMLElement).style.background = '#ddd'; }}
            onMouseLeave={(e) => { if (!isDragging) (e.target as HTMLElement).style.background = 'transparent'; }}
          />
        )}

        {/* 右侧：保存的请求（支持收起/展开） */}
        <div style={{
          width: rightCollapsed ? 0 : `${rightWidth}%`,
          minWidth: rightCollapsed ? 0 : 200,
          overflow: 'hidden',
          background: '#fff',
          borderRadius: '6px',
          boxShadow: rightCollapsed ? 'none' : '0 1px 4px rgba(0, 0, 0, 0.08)',
          transition: rightCollapsed ? 'width 0.2s ease, minWidth 0.2s ease' : undefined,
          flexShrink: 0,
          position: 'relative',
        }}>
          {/* 收起/展开按钮（收起状态下显示） */}
          {rightCollapsed && (
            <Tooltip title="展开保存的请求" placement="left">
              <Button
                type="text"
                icon={<LeftOutlined />}
                onClick={() => setRightCollapsed(false)}
                style={{
                  position: 'absolute',
                  top: 8,
                  right: 8,
                  zIndex: 20,
                  color: '#999',
                }}
              />
            </Tooltip>
          )}

          {!rightCollapsed && (
            <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
              <div style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                padding: '6px 12px',
                borderBottom: '1px solid #f0f0f0',
                flexShrink: 0,
              }}>
                <span style={{ fontWeight: 600, fontSize: 16, color: '#333' }}>保存的请求</span>
                <Space>
                  <Button
                    icon={<SyncOutlined />}
                    onClick={fetchSavedRequests}
                    // size="small"
                    loading={loading}
                  >
                    刷新
                  </Button>
                  <Tooltip title="收起保存的请求">
                    <Button
                      type="text"
                      icon={<RightOutlined />}
                      onClick={() => setRightCollapsed(true)}
                      // size="small"
                      style={{ color: '#999' }}
                    />
                  </Tooltip>
                </Space>
              </div>
              <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                <div style={{ padding: '8px 12px', borderBottom: '1px solid #f0f0f0' }}>
                  <Input.Search
                    // size="small"
                    placeholder="搜索名称..."
                    allowClear
                    onChange={(e) => setSearchRequests(e.target.value)}
                    onSearch={(val) => setSearchRequests(val)}
                    style={{ width: '100%' }}
                  />
                </div>
                <div style={{ flex: 1, overflow: 'auto' }}>
                  <Table
                    columns={savedRequestsColumns}
                    dataSource={savedRequests.filter(r => !searchRequests || r.name.toLowerCase().includes(searchRequests.toLowerCase()))}
                    rowKey="id"
                    pagination={{ pageSize: 20, showSizeChanger: false, size: 'small' }}
                    size="small"
                    style={{ fontSize: 13 }}
                    sticky
                  />
                </div>
              </div>
            </div>
          )}
        </div>

        {/* 收起状态下的展开按钮 */}
        {rightCollapsed && (
          <Tooltip title="展开保存的请求" placement="left">
            <Button
              type="text"
              icon={<LeftOutlined />}
              onClick={() => setRightCollapsed(false)}
              style={{
                position: 'absolute',
                right: 0,
                top: '50%',
                transform: 'translateY(-50%)',
                zIndex: 20,
                background: '#fff',
                boxShadow: '0 1px 4px rgba(0, 0, 0, 0.12)',
                borderRadius: '6px 0 0 6px',
                width: 24,
                height: 64,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: '#666',
              }}
            />
          </Tooltip>
        )}
      </div>

      {/* 保存请求配置模态框 */}
      <Modal
        title={editingRequest ? "编辑请求配置" : "保存请求配置"}
        open={isSaveModalVisible}
        onOk={handleSaveRequest}
        onCancel={() => setIsSaveModalVisible(false)}
        confirmLoading={loading}
      >
        <Input
          placeholder="请输入请求配置名称"
          value={saveRequestName}
          onChange={(e) => setSaveRequestName(e.target.value)}
          style={{ marginBottom: '16px' }}
        />
        <div style={{ color: '#999', fontSize: '12px' }}>
          保存后可在右侧"保存的请求"侧边栏中查看和管理
        </div>
      </Modal>

      {/* 全局参数配置模态框 */}
      <Modal
        title="全局参数配置"
        open={isGlobalParamsModalVisible && canManageGlobalParams}
        onOk={() => setIsGlobalParamsModalVisible(false)}
        onCancel={() => setIsGlobalParamsModalVisible(false)}
        width={600}
      >
        <div style={{ marginBottom: '16px' }}>
          {/* 环境选择和管理 */}
          <div style={{ marginBottom: '20px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
              <h3 style={{ margin: 0, fontSize: '14px', fontWeight: 'bold' }}>环境管理</h3>
              <Button
                type="primary"
                size="small"
                onClick={handleAddEnvironment}
              >
                添加环境
              </Button>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginBottom: '12px' }}>
              {environments.map(env => (
                <div
                  key={env.id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    padding: '4px 12px',
                    borderRadius: '16px',
                    background: currentEnvironmentId === env.id ? '#1890ff' : '#f0f0f0',
                    color: currentEnvironmentId === env.id ? '#fff' : '#333',
                    cursor: 'pointer',
                    fontSize: '12px',
                    gap: '8px'
                  }}
                  onClick={() => handleSwitchEnvironment(env.id)}
                >
                  {editingEnvId === env.id ? (
                    <Space size="small" style={{ alignItems: 'center' }}>
                      <Input
                        size="small"
                        value={editingEnvName}
                        onChange={(e) => setEditingEnvName(e.target.value)}
                        onPressEnter={handleSaveEnvironmentName}
                        style={{
                          width: 120,
                          background: 'rgba(255,255,255,0.9)',
                          color: '#333'
                        }}
                      />
                      <Button
                        size="small"
                        type="link"
                        style={{ color: currentEnvironmentId === env.id ? 'rgba(255,255,255,0.8)' : '#1890ff' }}
                        onClick={(e) => {
                          e.stopPropagation();
                          handleSaveEnvironmentName();
                        }}
                      >
                        保存
                      </Button>
                      <Button
                        size="small"
                        type="link"
                        style={{ color: currentEnvironmentId === env.id ? 'rgba(255,255,255,0.8)' : '#999' }}
                        onClick={(e) => {
                          e.stopPropagation();
                          handleCancelEditEnvironmentName();
                        }}
                      >
                        取消
                      </Button>
                    </Space>
                  ) : (
                    <Space size="small" style={{ alignItems: 'center' }}>
                      <span>{env.name}</span>
                      <Button
                        type="text"
                        size="small"
                        icon={<EditOutlined />}
                        style={{
                          color: currentEnvironmentId === env.id ? 'rgba(255,255,255,0.8)' : '#1890ff',
                          padding: 0,
                          margin: 0,
                          width: '20px',
                          height: '20px',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center'
                        }}
                        onClick={(e) => {
                          e.stopPropagation();
                          handleEditEnvironmentName(env);
                        }}
                      >
                      </Button>
                      {environments.length > 1 && (
                        <Button
                          type="text"
                          size="small"
                          style={{
                            color: currentEnvironmentId === env.id ? 'rgba(255,255,255,0.8)' : '#999',
                            padding: 0,
                            margin: 0,
                            width: '20px',
                            height: '20px',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center'
                          }}
                          onClick={(e) => {
                            e.stopPropagation();
                            handleRemoveEnvironment(env.id);
                          }}
                        >
                          ×
                        </Button>
                      )}
                    </Space>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* 当前环境的参数 */}
          <p style={{ color: '#666', marginBottom: '12px' }}>全局参数将应用于所有请求，可在URL、请求头和请求体中使用 &#123;&#123;variable&#125;&#125; 或 $&#123;variable&#125; 语法引用</p>

          {getCurrentEnvironment().parameters.map((param, index) => (
            <Space
              key={index}
              style={{ width: '100%', marginBottom: '12px' }}
              align="center"
            >
              <Input
                placeholder="参数名"
                value={param.key}
                onChange={(e) => handleEnvironmentParameterChange(index, 'key', e.target.value)}
                style={{ width: 150 }}
                size="middle"
              />
              <Input
                placeholder="参数值"
                value={param.value}
                onChange={(e) => handleEnvironmentParameterChange(index, 'value', e.target.value)}
                style={{ flex: 1, width: 330 }}
                size="middle"
              />
              <Button
                icon={<MinusOutlined />}
                danger
                onClick={() => handleRemoveEnvironmentParameter(index)}
                size="small"
              />
            </Space>
          ))}

          <Button
            type="dashed"
            icon={<PlusOutlined />}
            onClick={handleAddEnvironmentParameter}
            style={{ marginTop: '8px', width: '100%' }}
            size="middle"
          >
            添加全局参数
          </Button>
        </div>
      </Modal>

      {/* 添加环境模态框 */}
      <Modal
        title="创建新环境"
        open={isAddEnvModalVisible && canManageGlobalParams}
        onOk={handleConfirmAddEnvironment}
        onCancel={() => setIsAddEnvModalVisible(false)}
        width={400}
      >
        <Input
          placeholder="请输入环境名称"
          value={newEnvironmentName}
          onChange={(e) => setNewEnvironmentName(e.target.value)}
          style={{ marginBottom: '16px' }}
        />
        <div style={{ color: '#999', fontSize: '12px' }}>
          环境名称用于区分不同的参数配置集
        </div>
      </Modal>


    </div>
  );
};

export default IoTDataPushPlatform;

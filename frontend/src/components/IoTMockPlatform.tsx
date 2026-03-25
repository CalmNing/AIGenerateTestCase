import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Form, Select, Input, Button, Tabs, Space, Table, message, Modal, Tooltip } from 'antd';
import { SendOutlined, PlusOutlined, MinusOutlined, CopyOutlined, SaveOutlined, SyncOutlined, FormatPainterOutlined, LeftOutlined, RightOutlined, QuestionCircleOutlined } from '@ant-design/icons';
import axios, { AxiosRequestConfig } from 'axios';
import CodeMirror from '@uiw/react-codemirror';
import { json } from '@codemirror/lang-json';

interface HeaderItem {
  key: string;
  value: string;
}

interface ParameterItem {
  key: string;
  value: string;
}

interface Environment {
  id: string;
  name: string;
  parameters: ParameterItem[];
}

interface SavedRequest {
  id: number;
  name: string;
  method: string;
  url: string;
  headers: HeaderItem[];
  parameters: ParameterItem[];
  body?: string;
  createdAt: string;
}

interface Tab {
  id: string;
  name: string;
  method: string;
  url: string;
  headers: HeaderItem[];
  parameters: ParameterItem[];
  body?: string;
  savedRequestId?: number; // 关联的保存请求ID
  hasUnsavedChanges?: boolean; // 是否有未保存的更改
}

const IoTMockPlatform: React.FC = () => {
  const [form] = Form.useForm();
  const [activeTabId, setActiveTabId] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState<any>(null);
  const [responseTime, setResponseTime] = useState<number>(0);
  const [headers, setHeaders] = useState<HeaderItem[]>([{ key: 'Content-Type', value: 'application/json' }]);
  const [parameters, setParameters] = useState<ParameterItem[]>([]);
  const [savedRequests, setSavedRequests] = useState<SavedRequest[]>([]);
  const [isSaveModalVisible, setIsSaveModalVisible] = useState(false);
  const [saveRequestName, setSaveRequestName] = useState('');
  const [editingRequest, setEditingRequest] = useState<SavedRequest | null>(null);
  const [tabs, setTabs] = useState<Tab[]>([]); // 标签页列表
  const [environments, setEnvironments] = useState<Environment[]>([{ id: 'env-1', name: '默认环境', parameters: [] }]); // 环境列表
  const [currentEnvironmentId, setCurrentEnvironmentId] = useState<string>('env-1'); // 当前选中的环境
  const [isGlobalParamsModalVisible, setIsGlobalParamsModalVisible] = useState(false); // 全局参数模态框

  // 稳定 CodeMirror 扩展引用，避免每次渲染重建
  const jsonExtensions = useMemo(() => [json()], []);

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
    fetchGlobalParameters();
  }, []);

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
  };

  // 切换环境
  const handleSwitchEnvironment = async (envId: string) => {
    setCurrentEnvironmentId(envId);

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
    }
  }, [activeTabId, tabs, form]);

  // 从后端API获取保存的请求配置
  const fetchSavedRequests = async () => {
    try {
      const response = await axios.get('/api/saved-requests');
      if (response.data.code === 200 && response.data.data) {
        setSavedRequests(response.data.data);
      }
    } catch (error) {
      console.error('Failed to fetch saved requests:', error);
      message.error('获取保存的请求配置失败');
    }
  };

  // 从后端API获取全局参数配置
  const fetchGlobalParameters = async () => {
    try {
      const response = await axios.get('/api/global-parameters');
      if (response.data.code === 200 && response.data.data) {
        const backendEnvironments = response.data.data.map((env: any) => ({
          id: env.id.toString(),
          name: env.name,
          parameters: env.parameters || []
        }));
        if (backendEnvironments.length > 0) {
          setEnvironments(backendEnvironments);
          // 找到默认环境或第一个环境
          const defaultEnv = backendEnvironments.find((env: any) => env.is_default) || backendEnvironments[0];
          setCurrentEnvironmentId(defaultEnv.id);
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
    try {
      // 检查环境是否已存在（通过id判断）
      if (parseInt(environment.id)) {
        // 更新现有环境
        const response = await axios.put(`/api/global-parameters/${parseInt(environment.id)}`, {
          name: environment.name,
          parameters: environment.parameters,
          is_default: environment.is_default || false
        });
        return response.data.data;
      } else {
        // 创建新环境
        const response = await axios.post('/api/global-parameters', {
          name: environment.name,
          parameters: environment.parameters,
          is_default: environment.is_default || false
        });
        return response.data.data;
      }
    } catch (error) {
      console.error('Failed to save environment:', error);
      message.error('保存环境配置失败');
      return null;
    }
  };

  // 删除环境配置
  const deleteEnvironmentFromBackend = async (environmentId: string) => {
    try {
      if (parseInt(environmentId)) {
        await axios.delete(`/api/global-parameters/${parseInt(environmentId)}`);
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

      // Create parameter map for substitution
      const paramMap = new Map<string, string>();

      // Add current environment parameters first
      const currentEnv = getCurrentEnvironment();
      currentEnv.parameters.forEach(p => {
        if (p.key && p.value) {
          paramMap.set(p.key, p.value);
        }
      });

      // Add local parameters (override global if same key)
      parameters.forEach(p => {
        if (p.key && p.value) {
          paramMap.set(p.key, p.value);
        }
      });

      // Function to substitute variables in string
      const substituteVariables = (str: string): string => {
        if (!str) return str;

        // Replace {{@expression}} - execute JS expression
        // Example: {{@Date.now()}}, {{@Math.random().toFixed(2)}}, {{@new Date().toISOString()}}
        let result = str.replace(/\{\{@([^}]+)\}\}/g, (_match, expr) => {
          try {
            const fn = new Function('return ' + expr.trim());
            const val = fn();
            return val !== undefined && val !== null ? String(val) : _match;
          } catch {
            return _match;
          }
        });

        // Replace {{$functionName}} and {{$functionName(args)}} - built-in helpers
        // Built-in: $timestamp, $randomInt, $randomInt(min,max), $uuid, $date(format), $now
        result = result.replace(/\{\{(\$[^}]+)\}\}/g, (_match, expr) => {
          const trimmed = expr.trim();

          // $timestamp - 当前时间戳（毫秒）
          if (trimmed === '$timestamp') return String(Date.now());

          // $now - 当前时间戳（秒）
          if (trimmed === '$now') return String(Math.floor(Date.now() / 1000));

          // $randomInt(min,max) - 随机整数
          const randMatch = trimmed.match(/^\$randomInt\((\d+)\s*,\s*(\d+)\)$/);
          if (randMatch) {
            const min = parseInt(randMatch[1], 10);
            const max = parseInt(randMatch[2], 10);
            return String(Math.floor(Math.random() * (max - min + 1)) + min);
          }

          // $randomInt - 0~100 随机整数
          if (trimmed === '$randomInt') return String(Math.floor(Math.random() * 101));

          // $uuid - 生成 UUID v4
          if (trimmed === '$uuid') {
            return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
              const r = Math.random() * 16 | 0;
              return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
            });
          }

          // $date(format) - 按格式生成日期，格式符: YYYY MM DD HH mm ss SSS
          const dateMatch = trimmed.match(/^\$date\((.+)\)$/);
          if (dateMatch) {
            const fmt = dateMatch[1].trim().replace(/^['"]|['"]$/g, '');
            const d = new Date();
            const pad = (n: number, len = 2) => String(n).padStart(len, '0');
            return fmt
              .replace('YYYY', String(d.getFullYear()))
              .replace('MM', pad(d.getMonth() + 1))
              .replace('DD', pad(d.getDate()))
              .replace('HH', pad(d.getHours()))
              .replace('mm', pad(d.getMinutes()))
              .replace('ss', pad(d.getSeconds()))
              .replace('SSS', pad(d.getMilliseconds(), 3));
          }

          // $date - 默认格式 YYYY-MM-DD
          if (trimmed === '$date') {
            const d = new Date();
            return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
          }

          return _match;
        });

        // Replace {{variable}} syntax (user-defined parameters)
        result = result.replace(/\{\{([^}$@][^}]*)\}\}/g, (match, key) => {
          return paramMap.get(key.trim()) || match;
        });

        // Replace ${variable} syntax
        result = result.replace(/\$\{([^}]+)\}/g, (match, key) => {
          return paramMap.get(key.trim()) || match;
        });

        return result;
      };

      // Process parameters and build URL with variable substitution only
      let finalUrl = substituteVariables(url);

      // Process headers with variable substitution
      const processedHeaders = headers.reduce((acc, header) => {
        if (header.key && header.value) {
          acc[header.key] = substituteVariables(header.value);
        }
        return acc;
      }, {} as Record<string, string>);

      const config: AxiosRequestConfig = {
        method,
        url: finalUrl,
        headers: processedHeaders,
      };

      if (['POST', 'PUT', 'PATCH'].includes(method) && body) {
        try {
          // Substitute variables in body before parsing
          const substitutedBody = substituteVariables(body);
          config.data = JSON.parse(substitutedBody);
        } catch (error) {
          // If not JSON, just substitute variables
          config.data = substituteVariables(body);
        }
      }

      // Use backend proxy service to avoid CORS issues
      const proxyResponse = await axios.post('/api/proxy/forward', {
        url: finalUrl,
        method,
        headers: processedHeaders,
        data: config.data,
        params: config.params
      });

      // 更新当前标签页的内容
      updateCurrentTab({
        method,
        url,
        body,
        headers,
        parameters,
      });

      const endTime = Date.now();
      const timeTaken = endTime - startTime;

      // Create axios-like response object
      const axiosResponse = {
        status: proxyResponse.data.status_code,
        statusText: '',
        headers: proxyResponse.data.headers,
        data: proxyResponse.data.data
      };

      setResponse(axiosResponse);
      setResponseTime(timeTaken);
      message.success('请求成功');

    } catch (error: any) {
      if (error.response) {
        // 代理服务返回错误
        if (error.response.data) {
          // 目标服务器返回的错误
          const proxyError = error.response.data;
          setResponse({
            status: proxyError.status_code || error.response.status,
            statusText: '',
            headers: proxyError.headers || {},
            data: proxyError.data || error.response.data
          });
          setResponseTime(0);
          message.error(`请求失败: ${proxyError.status_code || error.response.status} ${proxyError.detail || 'Unknown error'}`);
        } else {
          // 代理服务本身的错误
          setResponse(error.response);
          setResponseTime(0);
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
          body
        };

        const response = await axios.put(`/api/saved-requests/${editingRequest.id}`, updatedRequest);
        if (response.data.code === 200 && response.data.data) {
          // 更新本地状态
          const updatedRequests = savedRequests.map(req =>
            req.id === editingRequest.id ? response.data.data : req
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
              savedRequestId: response.data.data.id,
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
          body
        };

        const response = await axios.post('/api/saved-requests', newRequest);
        if (response.data.code === 200 && response.data.data) {
          setSavedRequests([response.data.data, ...savedRequests]);
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
              savedRequestId: response.data.data.id,
              hasUnsavedChanges: false
            } : tab
          ));

          // 设置为编辑模式，下次保存时更新
          setEditingRequest(response.data.data);
        }
      }

      setIsSaveModalVisible(false);
    } catch (error) {
      console.error('Save request failed:', error);
      message.error('保存请求配置失败');
    } finally {
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
            const response = await axios.delete(`/api/saved-requests/${id}`);
            if (response.data.code === 200) {
              setSavedRequests(savedRequests.filter(req => req.id !== id));
              message.success('请求配置已删除');
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
      width: 120,
      render: (_: any, record: SavedRequest) => (
        <Space size="small">
          <Button size="small" onClick={() => createTabFromSavedRequest(record)}>
            打开
          </Button>
          <Button size="small" danger onClick={() => handleDeleteSavedRequest(record.id)}>
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
        <span style={{ fontWeight: 600, fontSize: 15, color: '#333' }}>IoT Mock 平台</span>
        <Space>
          <Button
            type="default"
            icon={<SyncOutlined />}
            onClick={() => setIsGlobalParamsModalVisible(true)}
            size="middle"
          >
            全局参数
          </Button>
          <Button
            type="default"
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
                      <div style={{ padding: '8px', background: '#fafafa', borderRadius: '4px', maxHeight: 200, overflow: 'auto' }}>
                        {parameters.map((param, index) => (
                          <Space
                            key={index}
                            style={{ width: '100%', marginBottom: '6px' }}
                            align="center"
                            size={4}
                          >
                            <Input
                              placeholder="参数名"
                              value={param.key}
                              onChange={(e) => handleParameterChange(index, 'key', e.target.value)}
                              style={{ width: 200 }}
                            // size="small"
                            />
                            <Input
                              placeholder="参数值"
                              value={param.value}
                              onChange={(e) => handleParameterChange(index, 'value', e.target.value)}
                              style={{ flex: 1, width: 330 }}
                            // size="small"
                            />
                            <Button
                              icon={<MinusOutlined />}
                              danger
                              onClick={() => handleRemoveParameter(index)}
                            // size="small"
                            />
                          </Space>
                        ))}
                        <Button
                          type="dashed"
                          icon={<PlusOutlined />}
                          onClick={handleAddParameter}
                          style={{ marginTop: '4px' }}
                          // size="small"
                          block
                        >
                          添加参数
                        </Button>
                      </div>
                    </Form.Item>
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
                      const formatted = JSON.stringify(JSON.parse(currentBody), null, 2);
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
                style={{ fontSize: '13px' }}
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
              <div style={{ flex: 1, overflow: 'hidden' }}>
                <Table
                  columns={savedRequestsColumns}
                  dataSource={savedRequests}
                  rowKey="id"
                  pagination={{ pageSize: 20, showSizeChanger: false, size: 'small' }}
                  size="small"
                  style={{ fontSize: 13 }}
                  sticky
                  scroll={{ y: 100 * 6 }}
                />
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
        open={isGlobalParamsModalVisible}
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
                  <span>{env.name}</span>
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
        open={isAddEnvModalVisible}
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

export default IoTMockPlatform;
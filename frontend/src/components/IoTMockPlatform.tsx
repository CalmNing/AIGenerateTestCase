import React, { useState, useEffect } from 'react';
import { Card, Form, Select, Input, Button, Tabs, Space, Table, message, Modal } from 'antd';
import { SendOutlined, PlusOutlined, MinusOutlined, CopyOutlined, SaveOutlined, SyncOutlined } from '@ant-design/icons';
import axios, { AxiosRequestConfig } from 'axios';

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

        // Replace {{variable}} syntax
        let result = str.replace(/\{\{([^}]+)\}\}/g, (match, key) => {
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
        url
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
    <div style={{ padding: '10px', minHeight: '70vh', maxHeight: '100vh', background: '#f0f2f5' }}>
      <Card
        title={
          <Space style={{ width: '100%', justifyContent: 'space-between', alignItems: 'center' }}>
            <span></span>
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
          </Space>
        }
        style={{
          marginBottom: '20px',
          boxShadow: '0 2px 8px rgba(0, 0, 0, 0.09)',
          borderRadius: '8px'
        }}
      >
        <div style={{ display: 'flex', gap: '20px', height: '76vh', overflow: 'auto' }}>
          {/* 左侧：请求配置 */}
          <div style={{ flex: 2, minWidth: 400, display: 'flex', flexDirection: 'column', maxHeight: '100%' }}>
            <Tabs
              activeKey={activeTabId}
              onChange={setActiveTabId}
              items={tabs.map(tab => ({
                key: tab.id,
                label: (
                  <Space>
                    {tab.name}
                    <Button
                      size="small"
                      type="text"
                      danger
                      onClick={(e) => {
                        e.stopPropagation();
                        closeTab(tab.id);
                      }}
                    >
                      ×
                    </Button>
                  </Space>
                ),
                children: (
                  <div style={{ height: '100%', overflow: 'auto', padding: '10px' }}>
                    <Form form={form} layout="vertical">
                      <Form.Item label="请求配置">
                        <Space style={{ width: '100%', flexWrap: 'wrap' }}>
                          <Form.Item name="method" noStyle>
                            <Select
                              style={{ width: 120 }}
                              size="middle"
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
                              style={{ flex: 1, minWidth: 300 }}
                              size="middle"
                              onChange={(e) => updateCurrentTab({ url: e.target.value })}
                            />
                          </Form.Item>
                          <Button
                            type="primary"
                            icon={<SendOutlined />}
                            onClick={handleSendRequest}
                            loading={loading}
                            size="middle"
                            style={{ marginLeft: '8px' }}
                          >
                            发送
                          </Button>
                          <Button
                            type="default"
                            icon={<SaveOutlined />}
                            onClick={handleOpenSaveModal}
                            size="middle"
                            style={{ marginLeft: '8px' }}
                          >
                            保存
                          </Button>
                        </Space>
                      </Form.Item>

                      <Form.Item label="请求头">
                        <div style={{ padding: '12px', background: '#fafafa', borderRadius: '4px', maxHeight: 300, overflow: 'auto' }}>
                          {headers.map((header, index) => (
                            <Space
                              key={index}
                              style={{ width: '100%', marginBottom: '8px' }}
                              align="center"
                            >
                              <Input
                                placeholder="Key"
                                value={header.key}
                                onChange={(e) => handleHeaderChange(index, 'key', e.target.value)}
                                style={{ width: 200 }}
                                size="middle"
                              />
                              <Input
                                placeholder="Value"
                                value={header.value}
                                onChange={(e) => handleHeaderChange(index, 'value', e.target.value)}
                                style={{ flex: 1 }}
                                size="middle"
                              />
                              <Button
                                icon={<MinusOutlined />}
                                danger
                                onClick={() => handleRemoveHeader(index)}
                                size="small"
                              />
                            </Space>
                          ))}
                          <Button
                            type="dashed"
                            icon={<PlusOutlined />}
                            onClick={handleAddHeader}
                            style={{ marginTop: '8px' }}
                            size="middle"
                          >
                            添加请求头
                          </Button>
                        </div>
                      </Form.Item>

                      <Form.Item label="请求参数">
                        <div style={{ padding: '12px', background: '#fafafa', borderRadius: '4px', maxHeight: 300, overflow: 'auto' }}>
                          {parameters.map((param, index) => (
                            <Space
                              key={index}
                              style={{ width: '100%', marginBottom: '8px' }}
                              align="center"
                            >
                              <Input
                                placeholder="参数名"
                                value={param.key}
                                onChange={(e) => handleParameterChange(index, 'key', e.target.value)}
                                style={{ width: 200 }}
                                size="middle"
                              />
                              <Input
                                placeholder="参数值"
                                value={param.value}
                                onChange={(e) => handleParameterChange(index, 'value', e.target.value)}
                                style={{ flex: 1 }}
                                size="middle"
                              />
                              <Button
                                icon={<MinusOutlined />}
                                danger
                                onClick={() => handleRemoveParameter(index)}
                                size="small"
                              />
                            </Space>
                          ))}
                          <Button
                            type="dashed"
                            icon={<PlusOutlined />}
                            onClick={handleAddParameter}
                            style={{ marginTop: '8px' }}
                            size="middle"
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

          {/* 中间：请求体和响应结果 */}
          <div style={{ flex: 2, minWidth: 400, display: 'flex', flexDirection: 'column', gap: '10px', height: '100%', maxHeight: '100%' }}>
            {/* 请求体 */}
            <Card
              title="请求体"
              style={{
                boxShadow: '0 2px 8px rgba(0, 0, 0, 0.09)',
                borderRadius: '8px',
                flex: 1,
                minHeight: "30%",
                maxHeight: "45%"
              }}
            >
              <Form form={form} layout="vertical">
                <Form.Item name="body" noStyle>
                  <Input.TextArea
                    rows={8}
                    placeholder="请输入请求体（JSON格式）"
                    style={{
                      fontFamily: 'monospace',
                      borderRadius: '4px',
                      border: '1px solid #d9d9d9',
                      height: '100%'
                    }}
                    size="middle"
                    onChange={(e) => updateCurrentTab({ body: e.target.value })}
                  />
                </Form.Item>
              </Form>
            </Card>

            {/* 响应结果 */}
            {response && (
              <Card
                title="响应结果"
                style={{
                  boxShadow: '0 2px 8px rgba(0, 0, 0, 0.09)',
                  borderRadius: '8px',
                  flex: 1,
                  minHeight: "30%",
                  maxHeight: "50%"
                }}
              >
                <div style={{ height: '100%', minHeight: 250, display: 'flex', flexDirection: 'column', maxHeight: 550 }}>
                  <div style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    padding: '12px',
                    background: '#fafafa',
                    borderRadius: '4px',
                    marginBottom: '12px'
                  }}>
                    <div>
                      状态码: <strong style={{
                        color: response.status >= 200 && response.status < 300 ? 'green' : 'red',
                        marginLeft: '4px'
                      }}>
                        {response.status} {response.statusText}
                      </strong>
                    </div>
                    <div>
                      响应时间: <strong style={{ marginLeft: '4px' }}>{responseTime}ms</strong>
                    </div>
                  </div>
                  <div style={{
                    fontFamily: 'monospace',
                    whiteSpace: 'pre-wrap',
                    background: '#f5f5f5',
                    padding: '16px',
                    borderRadius: '4px',
                    overflow: 'auto',
                    border: '1px solid #e8e8e8',
                    maxHeight: 170,
                    minHeight: 100
                  }}>
                    {JSON.stringify(response.data, null, 2)}
                  </div>
                  <div style={{ marginTop: '12px', textAlign: 'right' }}>
                    <Button
                      icon={<CopyOutlined />}
                      onClick={() => {
                        navigator.clipboard.writeText(JSON.stringify(response.data, null, 2));
                        message.success('响应结果已复制到剪贴板');
                      }}
                      size="small"
                    >
                      复制响应
                    </Button>
                  </div>
                </div>
              </Card>
            )}
          </div>

          {/* 右侧：保存的请求 */}
          <div style={{ flex: 1, minWidth: 300, maxHeight: '96%' }}>
            <Card
              title={
                <Space style={{ width: '100%', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span>保存的请求</span>
                  <Button
                    icon={<SyncOutlined />}
                    onClick={fetchSavedRequests}
                    size="small"
                    loading={loading}
                  >
                    刷新
                  </Button>
                </Space>
              }
              variant="outlined"
              style={{
                boxShadow: '0 2px 8px rgba(0, 0, 0, 0.09)',
                borderRadius: '8px',
                height: '100%',
                maxHeight: '100%'
              }}
            >
              <Table
                columns={savedRequestsColumns}
                dataSource={savedRequests}
                rowKey="id"
                pagination={{
                  pageSize: 10,
                  showSizeChanger: false
                }}
                size="middle"
              />
            </Card>
          </div>
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
                  style={{ flex: 1 }}
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
      </Card>
    </div>
  );
};

export default IoTMockPlatform;
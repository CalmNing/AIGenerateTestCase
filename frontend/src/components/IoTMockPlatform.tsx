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

  // 从后端API获取保存的请求配置
  useEffect(() => {
    fetchSavedRequests();
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
      width: 120,
    },
    {
      title: '方法',
      dataIndex: 'method',
      key: 'method',
      width: 60,
    },
    {
      title: 'URL',
      dataIndex: 'url',
      key: 'url',
      ellipsis: true,
    },
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
    <div style={{ padding: '20px', minHeight: '80vh', background: '#f0f2f5' }}>
      <Card 
        title={
          <Space style={{ width: '100%', justifyContent: 'space-between', alignItems: 'center' }}>
            <span>IoT Mock 平台</span>
            <Button 
              type="default" 
              icon={<PlusOutlined />} 
              onClick={addNewTab}
              size="middle"
            >
              新增请求
            </Button>
          </Space>
        } 
        style={{ 
          marginBottom: '20px',
          boxShadow: '0 2px 8px rgba(0, 0, 0, 0.09)',
          borderRadius: '8px'
        }}
      >
        <div style={{ display: 'flex', gap: '20px' }}>
          {/* 左侧：发送请求 */}
          <div style={{ flex: 1, minWidth: 300 }}>
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
                  <Form form={form} layout="vertical">
                    <Form.Item label="请求配置">
                      <Space style={{ width: '100%', flexWrap: 'wrap' }}>
                        <Form.Item name="method" noStyle initialValue="GET">
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
                      <div style={{ padding: '12px', background: '#fafafa', borderRadius: '4px' }}>
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
                      <div style={{ padding: '12px', background: '#fafafa', borderRadius: '4px' }}>
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

                    <Form.Item label="请求体">
                      <Form.Item name="body" noStyle>
                        <Input.TextArea 
                          rows={6} 
                          placeholder="请输入请求体（JSON格式）"
                          style={{ 
                            fontFamily: 'monospace',
                            borderRadius: '4px',
                            border: '1px solid #d9d9d9'
                          }}
                          size="middle"
                          onChange={(e) => updateCurrentTab({ body: e.target.value })}
                        />
                      </Form.Item>
                    </Form.Item>
                  </Form>
                )
              }))}
            />

            {response && (
              <Card 
                title="响应结果" 
                style={{ 
                  marginTop: '20px',
                  boxShadow: '0 2px 8px rgba(0, 0, 0, 0.09)',
                  borderRadius: '8px'
                }}
              >
                <Space direction="vertical" style={{ width: '100%' }}>
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
                    maxHeight: '400px', 
                    overflow: 'auto',
                    border: '1px solid #e8e8e8'
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
                </Space>
              </Card>
            )}
          </div>

          {/* 右侧：保存的请求（侧边栏） */}
          <div style={{ width: 400, minWidth: 300 }}>
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
              bordered={false}
              style={{ 
                boxShadow: '0 2px 8px rgba(0, 0, 0, 0.09)',
                borderRadius: '8px',
                height: '100%'
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
      </Card>
    </div>
  );
};

export default IoTMockPlatform;
import React, { useState, useEffect, useMemo } from 'react';
import { Form, Select, Input, Button, Space, Table, Tag, message, Modal, Switch, Tooltip, Card, Popconfirm, InputNumber, Typography } from 'antd';
import { PlusOutlined, DeleteOutlined, ReloadOutlined, CopyOutlined, ExperimentOutlined, QuestionCircleOutlined } from '@ant-design/icons';
import CodeMirror from '@uiw/react-codemirror';
import { json } from '@codemirror/lang-json';
import { mockConfigApi, globalParameterApi } from '../services/api';
import type { MockConfig, GlobalParameter } from '../types';

/**
 * 格式化带模板表达式的 JSON
 * 支持未引号包裹的模板如 {{$randomInt(1,10)}}、${resId}
 * 已被双引号包裹的模板（如 "{{$date(...)}}"）不处理
 */
function formatJsonWithTemplates(input: string): string {
  const placeholders = new Map<string, string>();
  let placeholderIndex = 0;
  const cleaned = input.replace(/([:\[,])\s*(?!"|\d|true|false|null|\[|\{(?!\{))((?:[^\s,}\]]*?(?:\{\{(?:[^{}]|\{[^{}]*\})*\}\}|\$\{[^}]*\})[^\s,}\]]*?)+)/g, (_match, prefix, valuePart) => {
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
  return formatted;
}

const { Text } = Typography;

interface HeaderItem {
  key: string;
  value: string;
}

const METHOD_OPTIONS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'].map(m => ({ label: m, value: m }));

const IoTMockPlatform: React.FC = () => {
  const [configs, setConfigs] = useState<MockConfig[]>([]);
  const [environments, setEnvironments] = useState<GlobalParameter[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [editingConfig, setEditingConfig] = useState<MockConfig | null>(null);
  const [testModalVisible, setTestModalVisible] = useState(false);
  const [testResult, setTestResult] = useState<any>(null);
  const [testing, setTesting] = useState(false);

  const [form] = Form.useForm();

  // 响应头列表（表单内部状态）
  const [responseHeaders, setResponseHeaders] = useState<HeaderItem[]>([]);

  const fetchConfigs = async () => {
    setLoading(true);
    try {
      const res = await mockConfigApi.getConfigs();
      if (res.code === 200) setConfigs(res.data || []);
    } catch { message.error('获取Mock配置失败'); }
    finally { setLoading(false); }
  };

  const fetchEnvironments = async () => {
    try {
      const res = await globalParameterApi.getEnvironments();
      if (res.code === 200) setEnvironments(res.data || []);
    } catch { /* ignore */ }
  };

  useEffect(() => { fetchConfigs(); fetchEnvironments(); }, []);

  const jsonExtensions = useMemo(() => [json()], []);

  const resetForm = () => {
    form.resetFields();
    form.setFieldsValue({ method: 'GET', status_code: 200, enabled: true });
    setResponseHeaders([]);
    setEditingConfig(null);
  };

  const openCreate = () => {
    resetForm();
    setModalVisible(true);
  };

  const openEdit = (record: MockConfig) => {
    setEditingConfig(record);
    form.setFieldsValue({
      name: record.name,
      method: record.method,
      url_path: record.url_path,
      status_code: record.status_code,
      response_body: record.response_body || '',
      enabled: record.enabled,
      environment_id: record.environment_id,
      response_count: record.response_count || 1,
      page_size: record.page_size || undefined,
      json_path: record.json_path || undefined,
    });
    setResponseHeaders(record.response_headers?.map(h => ({ key: h.key, value: h.value })) || []);
    setModalVisible(true);
  };

  const handleCopy = (record: MockConfig) => {
    setEditingConfig(null);
    form.setFieldsValue({
      name: `${record.name} (复制)`,
      method: record.method,
      url_path: record.url_path,
      status_code: record.status_code,
      response_body: record.response_body || '',
      enabled: false,
      environment_id: record.environment_id,
      json_path: record.json_path || undefined,
    });
    setResponseHeaders(record.response_headers?.map(h => ({ key: h.key, value: h.value })) || []);
    setModalVisible(true);
    message.info('已复制配置，请修改后保存');
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      const payload = {
        ...values,
        response_headers: responseHeaders.filter(h => h.key),
      };
      if (editingConfig) {
        const res = await mockConfigApi.updateConfig(editingConfig.id, payload);
        if (res.code === 200) { message.success('更新成功'); setModalVisible(false); fetchConfigs(); }
        else message.error(res.message || '更新失败');
      } else {
        const res = await mockConfigApi.createConfig(payload);
        if (res.code === 200) { message.success('创建成功'); setModalVisible(false); fetchConfigs(); }
        else message.error(res.message || '创建失败');
      }
    } catch (error: any) {
      if (!error.errorFields) message.error('操作失败');
    }
  };

  const handleToggle = async (record: MockConfig) => {
    try {
      const res = await mockConfigApi.updateConfig(record.id, { enabled: !record.enabled });
      if (res.code === 200) { message.success(record.enabled ? '已禁用' : '已启用'); fetchConfigs(); }
    } catch { message.error('操作失败'); }
  };

  const handleDelete = async (id: number) => {
    try {
      const res = await mockConfigApi.deleteConfig(id);
      if (res.code === 200) { message.success('已删除'); fetchConfigs(); }
      else message.error(res.message || '删除失败');
    } catch { message.error('删除失败'); }
  };

  const handleTest = (record: MockConfig) => {
    setEditingConfig(record);
    setTestResult(null);
    setTestModalVisible(true);
  };

  const executeTest = async () => {
    if (!editingConfig) return;
    setTesting(true);
    try {
      const url = `/api/mock${editingConfig.url_path}`;
      const params = new URLSearchParams();
      if (editingConfig.response_count > 1) {
        params.append('page', '1');
        params.append('page_size', String(editingConfig.response_count || 10));
      }
      const fullUrl = params.toString() ? `${url}?${params.toString()}` : url;
      const response = await fetch(fullUrl, { method: editingConfig.method });
      const contentType = response.headers.get('content-type') || '';
      let body: any;
      if (contentType.includes('application/json')) {
        body = await response.json();
      } else {
        body = await response.text();
      }
      setTestResult({ status: response.status, statusText: response.statusText, headers: Object.fromEntries(response.headers.entries()), body });
    } catch (e: any) {
      setTestResult({ status: 0, statusText: '请求失败', headers: {}, body: e.message });
    } finally { setTesting(false); }
  };

  const columns = [
    { title: '名称', dataIndex: 'name', key: 'name', width: 150, ellipsis: true, render: (t: string) => <Text strong ellipsis>{t}</Text> },
    { title: '方法', dataIndex: 'method', key: 'method', width: 80, render: (m: string) => <Tag color="blue">{m}</Tag> },
    { title: 'URL路径', dataIndex: 'url_path', key: 'url_path', width: 150, ellipsis: true, render: (t: string) => (
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, maxWidth: '100%' }}>
        <Text code ellipsis style={{ fontSize: 13, flex: 1, minWidth: 0 }}>
          {t}
        </Text>
        <Button
          type="text" size="small"
          icon={<CopyOutlined style={{ fontSize: 12 }} />}
          onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(`/api/mock${t}`); message.success('已复制'); }}
          style={{ flexShrink: 0, padding: 0, height: 20, width: 20, opacity: 0.65 }}
        />
      </div>
    ) },
    { title: '状态码', dataIndex: 'status_code', key: 'status_code', width: 80 },
    { title: '分页', key: 'pagination', width: 100, render: (_: any, r: MockConfig) => r.response_count > 1 ? <Tag color="green">{r.response_count}条/页</Tag> : '-' },
    { title: 'JSON路径', dataIndex: 'json_path', key: 'json_path', width: 150, render: (t: string) => t ? <Text code style={{ fontSize: 13 }}>{t}</Text> : '-' },
    { title: '环境', key: 'env', width: 90, render: (_: any, r: MockConfig) => { const env = environments.find(e => e.id === r.environment_id); return env ? <Text ellipsis>{env.name}</Text> : '-'; } },
    { title: '启用', dataIndex: 'enabled', key: 'enabled', width: 70, render: (enabled: boolean, record: MockConfig) => <Switch size="small" checked={enabled} onChange={() => handleToggle(record)} /> },
    {
      title: '操作', key: 'action', width: 200, render: (_: any, record: MockConfig) => (
        <Space size={0}>
          <Button type="text" size="small" onClick={() => handleTest(record)} style={{ color: '#52c41a' }}>测试</Button>
          <Button type="text" size="small" onClick={() => openEdit(record)} style={{ color: '#1890ff' }}>编辑</Button>
          <Button type="text" size="small" onClick={() => handleCopy(record)} style={{ color: '#1890ff' }}>复制</Button>
          <Popconfirm title="确定删除?" onConfirm={() => handleDelete(record.id)} okText="确定" cancelText="取消">
            <Button type="text" size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      )
    },
  ];

  const addHeader = () => setResponseHeaders(prev => [...prev, { key: '', value: '' }]);
  const removeHeader = (idx: number) => setResponseHeaders(prev => prev.filter((_, i) => i !== idx));
  const updateHeader = (idx: number, field: keyof HeaderItem, val: string) => {
    setResponseHeaders(prev => prev.map((h, i) => i === idx ? { ...h, [field]: val } : h));
  };

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 88px)', background: '#f5f5f5' }}>
      {/* 左侧：配置列表 */}
      <div style={{ width: '60%', padding: 16, overflow: 'auto' }}>
        <Card
          title={<Space><ExperimentOutlined />Mock 接口配置</Space>}
          extra={<Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建 Mock</Button>}
          style={{ height: '100%' }}
          bodyStyle={{ padding: 0, overflow: 'auto', height: 'calc(100% - 57px)' }}
        >
          <Table
            columns={columns}
            dataSource={configs}
            rowKey="id"
            loading={loading}
            // size="small"
            pagination={false}
            scroll={{ y: 'calc(100vh - 180px)' }}
          />
        </Card>
      </div>

      {/* 右侧：使用说明 */}
      <div style={{ width: '40%', padding: 16, overflow: 'auto' }}>
        <Card title="使用说明" style={{ marginBottom: 16 }}>
          <div style={{ lineHeight: 2, color: '#555', fontSize: 14 }}>
            <p>1. <strong>新建 Mock</strong>：配置 URL 路径、HTTP 方法和响应内容</p>
            <p>2. <strong>URL 路径</strong>：支持路径参数，如 <Text code>/users/{'{'}id{'}'}</Text>，访问 <Text code>/api/mock/users/123</Text> 时，<Text code>{'{{id}}'}</Text> 会被替换为 <Text code>123</Text></p>
            <p>3. <strong>分页功能</strong>：设置"返回数据条目数量"大于1时启用分页，通过 <Text code>?page=1&page_size=10</Text> 参数控制分页</p>
            <p>4. <strong>JSON 路径</strong>：指定响应体中哪个字段包含数组数据，如 <Text code>$.data.items</Text>，留空则自动检测</p>
            <p>5. <strong>参数化</strong>：在响应体中使用以下格式引用变量</p>
            <p style={{ marginTop: 12, padding: '8px 12px', background: '#e6f7ff', borderRadius: 6, border: '1px solid #91d5ff' }}>
              <strong>Mock 服务基础路径：</strong><Text code copyable>/api/mock</Text><br />
              例如配置路径 <Text code>/hello</Text>，访问地址为 <Text code copyable>/api/mock/hello</Text>
            </p>
          </div>
        </Card>

        <Card title="参数化语法" style={{ marginBottom: 16 }}>
          <div style={{ lineHeight: 1.8, color: '#555', fontSize: 13 }}>
            <p><strong>1. 路径参数：</strong><Text code copyable>{'{{参数名}}'}</Text> 或 <Text code copyable>{'${参数名}'}</Text></p>
            <p style={{ marginLeft: 16, color: '#666' }}>从 URL 路径中提取，如 <Text code>/users/{'{'}id{'}'}</Text> 匹配 <Text code>/users/123</Text>，则 <Text code>{'{{id}}'}</Text> = <Text code>123</Text></p>

            <p style={{ marginTop: 8 }}><strong>2. JS 表达式：</strong><Text code copyable>{'{{@表达式}}'}</Text></p>
            <p style={{ marginLeft: 16, color: '#666' }}>示例：<Text code copyable>{'{{@Math.random().toFixed(2)}}'}</Text>、<Text code copyable>{'{{@new Date().toISOString()}}'}</Text></p>

            <p style={{ marginTop: 8 }}><strong>3. 内置函数：</strong><Text code copyable>{'{{$函数}}'}</Text></p>
            <div style={{ marginLeft: 16, color: '#666' }}>
              <p>• <Text code copyable>{'{{$timestamp}}'}</Text> - 毫秒时间戳</p>
              <p>• <Text code copyable>{'{{$now}}'}</Text> - 秒级时间戳</p>
              <p>• <Text code copyable>{'{{$uuid}}'}</Text> - 生成 UUID</p>
              <p>• <Text code copyable>{'{{$randomInt}}'}</Text> - 0~100 随机整数</p>
              <p>• <Text code copyable>{'{{$randomInt(1,100)}}'}</Text> - 指定范围随机整数</p>
              <p>• <Text code copyable>{'{{$date}}'}</Text> - 当前日期 (YYYY-MM-DD)</p>
              <p>• <Text code copyable>{'{{$date(YYYY-MM-DD HH:mm:ss)}}'}</Text> - 自定义日期格式</p>
            </div>

            <p style={{ marginTop: 8 }}><strong>4. 环境变量：</strong><Text code copyable>{'{{变量名}}'}</Text> 或 <Text code copyable>{'${变量名}'}</Text></p>
            <p style={{ marginLeft: 16, color: '#666' }}>示例：<Text code copyable>{'{{baseUrl}}'}</Text>、<Text code copyable>{'${apiToken}'}</Text></p>
          </div>
        </Card>

        {/* {configs.length > 0 && (
          <Card title="已启用的 Mock 接口">
            {configs.filter(c => c.enabled).length === 0 ? (
              <Text type="secondary">暂无启用的 Mock 接口</Text>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {configs.filter(c => c.enabled).map(c => (
                  <div key={c.id} style={{ padding: '8px 12px', background: '#f6ffed', border: '1px solid #b7eb8f', borderRadius: 6, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div>
                      <Tag color="blue">{c.method}</Tag>
                      <Text code style={{ fontSize: 13 }}>{c.url_path}</Text>
                      <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>({c.status_code})</Text>
                    </div>
                    <Text copyable={{ text: `/api/mock${c.url_path}` }} style={{ fontSize: 12 }} />
                  </div>
                ))}
              </div>
            )}
          </Card>
        )} */}
      </div>

      {/* 编辑/新建弹窗 */}
      <Modal
        title={editingConfig ? '编辑 Mock 配置' : '新建 Mock 配置'}
        open={modalVisible}
        onOk={handleSubmit}
        onCancel={() => setModalVisible(false)}
        width={720}
        style={{ top: 20 }}
        bodyStyle={{ maxHeight: 'calc(100vh - 250px)', overflowY: 'auto', overflowX: 'hidden', padding: '0 10px 1px 1px' }}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="如：获取用户信息 Mock" />
          </Form.Item>

          <Space style={{ width: '100%' }} size="large">
            <Form.Item name="method" label="HTTP 方法" rules={[{ required: true }]}>
              <Select options={METHOD_OPTIONS} style={{ width: 130 }} />
            </Form.Item>
            <Form.Item name="url_path" label="URL 路径" rules={[{ required: true, message: '请输入URL路径' }]}>
              <Input placeholder="如 /api/users/{id}，实际访问为 /api/mock/api/users/123" style={{ width: 380 }} />
            </Form.Item>
            <Form.Item name="status_code" label="响应状态码">
              <InputNumber min={100} max={599} style={{ width: 100 }} />
            </Form.Item>
          </Space>
          <Space style={{ width: '100%' }} size="large">
            <Form.Item name="environment_id" label="参数化环境">
              <Select placeholder="选择环境后，响应体中的 {{变量名}} 将被替换为环境参数值（可选）" allowClear style={{ width: '100%' }}
                options={environments.map(e => ({ label: e.name, value: e.id }))} />
            </Form.Item>
            <Form.Item name="enabled" label="启用" valuePropName="checked">
              <Switch />
            </Form.Item>

          </Space>

          <Space style={{ width: '100%' }} size="large">
            <Form.Item name="response_count" label="返回数据条目数量">
              <InputNumber min={1} max={10000} style={{ width: 200 }} placeholder="1" />
            </Form.Item>
            {/* <Form.Item name="page_size" label="分页大小" extra="每页返回的数据条数，默认10">
              <InputNumber min={1} max={1000} style={{ width: 200 }} placeholder="10" />
            </Form.Item> */}
            <Form.Item name="json_path" label="JSON 路径">
              <Input placeholder="响应体中返回数据JSON路径, 如 $.data.items" style={{ width: '100%', minWidth: '350px' }} />
            </Form.Item>
          </Space>



          {/* 响应头 */}
          <div style={{ marginBottom: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <span style={{ fontWeight: 500, fontSize: 14 }}>响应头</span>
              <Button type="dashed" size="small" icon={<PlusOutlined />} onClick={addHeader}>添加</Button>
            </div>
            {responseHeaders.length === 0 && <Text type="secondary" style={{ fontSize: 13 }}>暂无自定义响应头</Text>}
            {responseHeaders.map((h, idx) => (
              <div key={idx} style={{ display: 'flex', gap: 8, marginBottom: 6, alignItems: 'center' }}>
                <Input placeholder="Header Key" value={h.key} onChange={e => updateHeader(idx, 'key', e.target.value)} style={{ flex: 1 }} />
                <Input placeholder="Header Value" value={h.value} onChange={e => updateHeader(idx, 'value', e.target.value)} style={{ flex: 1 }} />
                <Button type="text" danger size="small" icon={<DeleteOutlined />} onClick={() => removeHeader(idx)} />
              </div>
            ))}
          </div>

          {/* 响应体 */}
          <div style={{ marginBottom: 8 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontWeight: 500, fontSize: 14 }}>
                响应体
                <Tooltip
                  title={
                    <div style={{ color: '#ffffff', lineHeight: 1.8 }}>
                      <p>• <Text code copyable color="#000" style={{ backgroundColor: '#fff', padding: '2px 6px', borderRadius: 3 }}>{'{{$timestamp}}'}</Text> - 毫秒时间戳</p>
                      <p>• <Text code copyable color="#000" style={{ backgroundColor: '#fff', padding: '2px 6px', borderRadius: 3 }}>{'{{$now}}'}</Text> - 秒级时间戳</p>
                      <p>• <Text code copyable color="#000" style={{ backgroundColor: '#fff', padding: '2px 6px', borderRadius: 3 }}>{'{{$uuid}}'}</Text> - 生成 UUID</p>
                      <p>• <Text code copyable color="#000" style={{ backgroundColor: '#fff', padding: '2px 6px', borderRadius: 3 }}>{'{{$randomInt}}'}</Text> - 0~100 随机整数</p>
                      <p>• <Text code copyable color="#000" style={{ backgroundColor: '#fff', padding: '2px 6px', borderRadius: 3 }}>{'{{$randomInt(1,100)}}'}</Text> - 指定范围随机整数</p>
                      <p>• <Text code copyable color="#000" style={{ backgroundColor: '#fff', padding: '2px 6px', borderRadius: 3 }}>{'{{$date}}'}</Text> - 当前日期 (YYYY-MM-DD)</p>
                      <p>• <Text code copyable color="#000" style={{ backgroundColor: '#fff', padding: '2px 6px', borderRadius: 3 }}>{'{{$date(YYYY-MM-DD HH:mm:ss)}}'}</Text> - 自定义日期格式</p>
                    </div>
                  }
                >
                  <QuestionCircleOutlined style={{ marginLeft: 6, color: '#999', fontSize: 13 }} />
                </Tooltip>
              </span>
              <Space size={4}>
                <Button
                  type="text" size="small" onClick={() => {
                    const body = form.getFieldValue('response_body');
                    if (body) {
                      try {
                        const formatted = formatJsonWithTemplates(body);
                        form.setFieldsValue({ response_body: formatted });
                        message.success('格式化成功');
                      } catch { message.error('JSON 格式不正确'); }
                    }
                  }}
                >格式化</Button>
                <Button
                  type="text" size="small" onClick={() => {
                    form.setFieldsValue({
                      response_body: JSON.stringify({
                        code: 200,
                        message: "success",
                        data: {
                          id: "{{id}}",
                          userId: "{{userId}}",
                          timestamp: "{{$timestamp}}",
                          date: "{{$date(YYYY-MM-DD HH:mm:ss)}}",
                          random: "{{$randomInt(1,1000)}}",
                          randomFloat: "{{@Math.random().toFixed(4)}}",
                          isoTime: "{{@new Date().toISOString()}}"
                        }
                      }, null, 2)
                    });
                  }}
                >模板</Button>
                <Button
                  type="text" size="small" onClick={() => {
                    form.setFieldsValue({
                      response_body: JSON.stringify({
                        code: 200,
                        message: "success",
                        data: {
                          items: [
                            {
                              id: "{{id}}",
                              userId: "{{userId}}",
                              timestamp: "{{$timestamp}}",
                              date: "{{$date(YYYY-MM-DD HH:mm:ss)}}",
                              random: "{{$randomInt(1,1000)}}",
                              randomFloat: "{{@Math.random().toFixed(4)}}",
                              isoTime: "{{@new Date().toISOString()}}"
                            }
                          ]
                        }
                      }, null, 2)
                    });
                    form.setFieldsValue({ response_count: 100, page_size: 10, json_path: '$.data.items' });
                  }}
                >分页模板</Button>
              </Space>
            </div>
          </div>
          <Form.Item name="response_body">
            <CodeMirror
              value={form.getFieldValue('response_body') || ''}
              height="450px"
              extensions={jsonExtensions}
              onChange={(val) => form.setFieldsValue({ response_body: val })}
              placeholder="输入响应体 JSON"
              style={{ fontSize: '14px' }}
              basicSetup={{ lineNumbers: true, foldGutter: true, highlightActiveLine: true }}
            />
          </Form.Item>


        </Form>
      </Modal>

      {/* 测试弹窗 */}
      <Modal
        title={`测试 Mock - ${editingConfig?.name || ''}`}
        open={testModalVisible}
        onCancel={() => setTestModalVisible(false)}
        footer={[
          <Button key="close" onClick={() => setTestModalVisible(false)}>关闭</Button>,
          <Button key="test" type="primary" loading={testing} icon={<ReloadOutlined />} onClick={executeTest}>发送测试</Button>,
        ]}
        width={700}
      >
        {editingConfig && (
          <div>
            <div style={{ marginBottom: 12, padding: 12, background: '#f5f5f5', borderRadius: 6 }}>
              <Space>
                <Tag color="blue">{editingConfig.method}</Tag>
                <Text code copyable style={{ fontSize: 14 }}>{`/api/mock${editingConfig.url_path}`}</Text>
              </Space>
            </div>
            {testResult && (
              <div>
                <div style={{ marginBottom: 8 }}>
                  <Tag color={testResult.status >= 200 && testResult.status < 300 ? 'success' : 'error'}>
                    {testResult.status} {testResult.statusText}
                  </Tag>
                </div>
                <div style={{ background: '#f6ffed', border: '1px solid #b7eb8f', borderRadius: 6, padding: 12, maxHeight: 350, overflow: 'auto' }}>
                  <pre style={{ margin: 0, fontSize: 13, whiteSpace: 'pre-wrap' }}>
                    {typeof testResult.body === 'object' ? JSON.stringify(testResult.body, null, 2) : String(testResult.body)}
                  </pre>
                </div>
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
};

export default IoTMockPlatform;

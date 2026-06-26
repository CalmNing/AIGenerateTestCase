import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { Form, Select, Input, Button, Table, Tag, message, Modal, Switch, Tooltip, Popconfirm, InputNumber } from 'antd';
import { PlusOutlined, DeleteOutlined, ReloadOutlined, CopyOutlined, ExperimentOutlined, QuestionCircleOutlined, FileTextOutlined, ApiOutlined, CodeOutlined } from '@ant-design/icons';
import CodeMirror from '@uiw/react-codemirror';
import { json } from '@codemirror/lang-json';
import { mockConfigApi, globalParameterApi } from '../../services/api';
import type { MockConfig, GlobalParameter } from '../../types';
import './IoTMockPlatform.css';

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

interface HeaderItem {
  key: string;
  value: string;
}

const METHOD_OPTIONS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'].map(m => ({ label: m, value: m }));

const MethodBadge: React.FC<{ method: string }> = ({ method }) => {
  const cls = `mock-method-badge mock-method-badge--${method.toLowerCase()}`;
  return <span className={cls}>{method}</span>;
};

const StatusCodeBadge: React.FC<{ code: number }> = ({ code }) => {
  let cls = 'mock-status-badge';
  if (code >= 200 && code < 300) cls += ' mock-status-badge--success';
  else if (code >= 300 && code < 400) cls += ' mock-status-badge--redirect';
  else if (code >= 400 && code < 500) cls += ' mock-status-badge--client-error';
  else cls += ' mock-status-badge--server-error';
  return <span className={cls}>{code}</span>;
};

const IoTMockPlatform: React.FC = () => {
  const [configs, setConfigs] = useState<MockConfig[]>([]);
  const [environments, setEnvironments] = useState<GlobalParameter[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchConfig, setSearchConfig] = useState('');
  const [modalVisible, setModalVisible] = useState(false);
  const [editingConfig, setEditingConfig] = useState<MockConfig | null>(null);
  const [testModalVisible, setTestModalVisible] = useState(false);
  const [testResult, setTestResult] = useState<any>(null);
  const [testing, setTesting] = useState(false);

  const [form] = Form.useForm();
  const [responseHeaders, setResponseHeaders] = useState<HeaderItem[]>([]);

  // Drag handle state for resizing panels
  const [leftWidth, setLeftWidth] = useState(60);
  const [rightCollapsed, setRightCollapsed] = useState(true);
  const dragRef = React.useRef<{ startX: number; startWidth: number } | null>(null);

  const handleDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragRef.current = { startX: e.clientX, startWidth: leftWidth };
    const handleDrag = (ev: MouseEvent) => {
      if (!dragRef.current) return;
      const container = (e.target as HTMLElement).closest('.mock-container');
      if (!container) return;
      const containerWidth = container.clientWidth;
      const delta = ev.clientX - dragRef.current.startX;
      const newWidth = ((dragRef.current.startWidth / 100) * containerWidth + delta) / containerWidth * 100;
      setLeftWidth(Math.min(80, Math.max(40, newWidth)));
    };
    const handleDragEnd = () => {
      dragRef.current = null;
      document.removeEventListener('mousemove', handleDrag);
      document.removeEventListener('mouseup', handleDragEnd);
    };
    document.addEventListener('mousemove', handleDrag);
    document.addEventListener('mouseup', handleDragEnd);
  }, [leftWidth]);

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
      const method = editingConfig.method;
      const isPost = ['POST', 'PUT', 'PATCH'].includes(method);
      let fullUrl = url;
      let requestBody: any = undefined;

      if (editingConfig.response_count > 1) {
        const paginationParams = { page: 1, page_size: editingConfig.page_size || 10 };
        if (isPost) {
          requestBody = paginationParams;
        } else {
          const params = new URLSearchParams();
          params.append('page', String(paginationParams.page));
          params.append('page_size', String(paginationParams.page_size));
          fullUrl = `${url}?${params.toString()}`;
        }
      }

      const response = await fetch(fullUrl, {
        method,
        headers: isPost ? { 'Content-Type': 'application/json' } : undefined,
        body: requestBody ? JSON.stringify(requestBody) : undefined,
      });
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
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 140,
      ellipsis: true,
      render: (t: string, record: MockConfig) => (
        <span style={{ fontWeight: 500, fontSize: 13, color: record.enabled ? 'var(--color-text)' : 'var(--color-text-disabled)' }}>{t}</span>
      ),
    },
    {
      title: '方法',
      dataIndex: 'method',
      key: 'method',
      width: 80,
      render: (m: string) => <MethodBadge method={m} />,
    },
    {
      title: 'URL 路径',
      dataIndex: 'url_path',
      key: 'url_path',
      width: 180,
      ellipsis: true,
      render: (t: string) => (
        <div className="mock-url-cell">
          <span className="mock-url-path">{t}</span>
          <button
            className="mock-url-copy"
            onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(`/api/mock${t}`); message.success('已复制'); }}
            title="复制路径"
          >
            <CopyOutlined style={{ fontSize: 11 }} />
          </button>
        </div>
      ),
    },
    {
      title: '状态码',
      dataIndex: 'status_code',
      key: 'status_code',
      width: 80,
      render: (code: number) => <StatusCodeBadge code={code} />,
    },
    {
      title: '分页',
      key: 'pagination',
      width: 90,
      render: (_: any, r: MockConfig) => r.response_count > 1
        ? <span className="mock-pagination-tag">{r.response_count}条/页</span>
        : <span style={{ color: 'var(--color-text-disabled)', fontSize: 12 }}>-</span>,
    },
    {
      title: '环境',
      key: 'env',
      width: 80,
      ellipsis: true,
      render: (_: any, r: MockConfig) => {
        const env = environments.find(e => e.id === r.environment_id);
        return <span style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>{env ? env.name : '-'}</span>;
      },
    },
    {
      title: '启用',
      dataIndex: 'enabled',
      key: 'enabled',
      width: 60,
      render: (enabled: boolean, record: MockConfig) => (
        <Switch size="small" checked={enabled} onChange={() => handleToggle(record)} />
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 220,
      render: (_: any, record: MockConfig) => (
        <div className="mock-actions">
          <button className="mock-action-btn mock-action-btn--success" onClick={() => handleTest(record)}>测试</button>
          <button className="mock-action-btn mock-action-btn--warning" onClick={() => openEdit(record)}>编辑</button>
          <button className="mock-action-btn mock-action-btn--info" onClick={() => handleCopy(record)}>复制</button>
          <Popconfirm title="确定要删除这个 Mock 配置吗？" onConfirm={() => handleDelete(record.id)} okText="确定" cancelText="取消">
            <button className="mock-action-btn mock-action-btn--danger">删除</button>
          </Popconfirm>
        </div>
      ),
    },
  ];

  const addHeader = () => setResponseHeaders(prev => [...prev, { key: '', value: '' }]);
  const removeHeader = (idx: number) => setResponseHeaders(prev => prev.filter((_, i) => i !== idx));
  const updateHeader = (idx: number, field: keyof HeaderItem, val: string) => {
    setResponseHeaders(prev => prev.map((h, i) => i === idx ? { ...h, [field]: val } : h));
  };

  return (
    <div className="mock-container">
      {/* 左侧：配置列表 */}
      <div className="mock-panel-left" style={{ width: rightCollapsed ? '100%' : `${leftWidth}%` }}>
        <div className="mock-filter-bar">
          <label>名称</label>
          <Input.Search
            placeholder="搜索名称..."
            allowClear
            size="small"
            onChange={(e) => setSearchConfig(e.target.value)}
            onSearch={(val) => setSearchConfig(val)}
          />
          <div style={{ flex: 1 }} />
          <span style={{ fontSize: 12, color: 'var(--color-text-tertiary)' }}>
            {configs.filter(c => c.enabled).length}/{configs.length} 已启用
          </span>
          <Button type="primary" size="small" icon={<PlusOutlined />} onClick={openCreate}>新建 Mock</Button>
        </div>

        <div className="mock-table-wrapper">
          <Table
            columns={columns}
            dataSource={configs.filter(c => !searchConfig || c.name.toLowerCase().includes(searchConfig.toLowerCase()))}
            rowKey="id"
            loading={loading}
            size="small"
            rowClassName={(record) => record.enabled ? '' : 'mock-row-disabled'}
            pagination={{
              pageSize: 20,
              size: 'small',
              showTotal: (total) => <span style={{ color: 'var(--color-text-tertiary)', fontSize: 12 }}>共 {total} 条</span>,
            }}
            scroll={{ y: 'calc(100vh - 240px)' }}
          />
        </div>
      </div>

      {/* 拖拽分隔条 + 收起按钮 */}
      <div style={{ position: 'relative', flexShrink: 0 }}>
        <div className="mock-drag-handle" onMouseDown={handleDragStart} />
        <button
          className={`mock-collapse-btn ${rightCollapsed ? 'is-collapsed' : ''}`}
          onClick={() => setRightCollapsed(!rightCollapsed)}
          title={rightCollapsed ? '展开文档' : '收起文档'}
        />
      </div>

      {/* 右侧：使用说明 */}
      {!rightCollapsed && (
      <div className="mock-panel-right" style={{ flex: 1 }}>
        <div className="mock-doc-card">
          <div className="mock-doc-card-header">
            <FileTextOutlined style={{ color: 'var(--color-primary)' }} />
            快速入门
          </div>
          <div className="mock-doc-card-body">
            <p><strong>1. 新建 Mock</strong> — 配置 URL 路径、HTTP 方法和响应内容</p>
            <p><strong>2. URL 路径</strong> — 支持路径参数，如 <code>/users/{"{id}"}</code>，访问 <code>/api/mock/users/123</code> 时 <code>{"{{id}}"}</code> 替换为 <code>123</code></p>
            <p><strong>3. 分页</strong> — 返回条目数 &gt; 1 时启用，通过 <code>?page=1&page_size=10</code> 控制</p>
            <p><strong>4. JSON 路径</strong> — 指定数组数据字段，如 <code>$.data.items</code>，留空自动检测</p>
            <div className="mock-highlight-box">
              <strong>基础路径：</strong><code>/api/mock</code><br />
              配置路径 <code>/hello</code>，访问 <code>/api/mock/hello</code>
            </div>
          </div>
        </div>

        <div className="mock-doc-card">
          <div className="mock-doc-card-header">
            <CodeOutlined style={{ color: 'var(--color-warning)' }} />
            参数化语法
          </div>
          <div className="mock-doc-card-body">
            <table className="mock-syntax-table">
              <tbody>
                <tr>
                  <td>
                    <span className="mock-syntax-label">路径参数</span><br />
                    <code className="mock-syntax-code">{"{{参数名}}"}</code> 或 <code className="mock-syntax-code">{"${参数名}"}</code>
                  </td>
                  <td><span className="mock-syntax-desc">从 URL 路径提取，如 <code>/users/{"{id}"}</code> 匹配 <code>/users/123</code></span></td>
                </tr>
                <tr>
                  <td>
                    <span className="mock-syntax-label">JS 表达式</span><br />
                    <code className="mock-syntax-code">{"{{@表达式}}"}</code>
                  </td>
                  <td><span className="mock-syntax-desc">如 <code>{"{{@Math.random().toFixed(2)}}"}</code></span></td>
                </tr>
                <tr>
                  <td>
                    <span className="mock-syntax-label">内置函数</span><br />
                    <code className="mock-syntax-code">{"{{$函数}}"}</code>
                  </td>
                  <td>
                    <span className="mock-syntax-desc">
                      <code>{"{{$timestamp}}"}</code> 毫秒时间戳<br />
                      <code>{"{{$uuid}}"}</code> UUID<br />
                      <code>{"{{$randomInt(1,100)}}"}</code> 随机整数<br />
                      <code>{"{{$date}}"}</code> 当前日期<br />
                      <code>{"{{$date(YYYY-MM-DD HH:mm:ss)}}"}</code> 自定义格式
                    </span>
                  </td>
                </tr>
                <tr>
                  <td>
                    <span className="mock-syntax-label">环境变量</span><br />
                    <code className="mock-syntax-code">{"{{变量名}}"}</code>
                  </td>
                  <td><span className="mock-syntax-desc">如 <code>{"{{baseUrl}}"}</code>、<code>{"${apiToken}"}</code></span></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
      )}

      {/* 编辑/新建弹窗 */}
      <Modal
        title={editingConfig ? '编辑 Mock 配置' : '新建 Mock 配置'}
        open={modalVisible}
        onOk={handleSubmit}
        onCancel={() => setModalVisible(false)}
        width={720}
        style={{ top: 20 }}
        styles={{ body: { maxHeight: 'calc(100vh - 200px)', overflowY: 'auto', padding: '16px 20px' } }}
        destroyOnClose
      >
        <Form form={form} layout="vertical" className="mock-modal-form" style={{ marginBottom: 0 }}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="如：获取用户信息 Mock" />
          </Form.Item>

          <div className="mock-form-row--triple">
            <Form.Item name="url_path" label="URL 路径" rules={[{ required: true, message: '请输入URL路径' }]}>
              <Input placeholder="/api/users/{id}" />
            </Form.Item>
            <Form.Item name="method" label="HTTP 方法" rules={[{ required: true }]}>
              <Select options={METHOD_OPTIONS} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="status_code" label="状态码">
              <InputNumber min={100} max={599} style={{ width: '100%' }} />
            </Form.Item>
          </div>

          <div className="mock-form-row">
            <Form.Item name="environment_id" label="参数化环境">
              <Select placeholder="选择环境（可选）" allowClear style={{ width: '100%' }}
                options={environments.map(e => ({ label: e.name, value: e.id }))} />
            </Form.Item>
            <div style={{ display: 'flex', gap: 16 }}>
              <Form.Item name="response_count" label="数据条目数" style={{ flex: 1 }}>
                <InputNumber min={1} max={10000} style={{ width: '100%' }} placeholder="1" />
              </Form.Item>
              <Form.Item name="enabled" label="启用" valuePropName="checked" style={{ paddingTop: 4 }}>
                <Switch />
              </Form.Item>
            </div>
          </div>

          <Form.Item name="json_path" label="JSON 路径">
            <Input placeholder="响应体中数组数据的 JSON 路径，如 $.data.items" />
          </Form.Item>

          {/* 响应头 */}
          <div style={{ marginBottom: 16 }}>
            <div className="mock-section-label">
              <span className="mock-section-label-text">响应头</span>
              <Button type="dashed" size="small" icon={<PlusOutlined />} onClick={addHeader}>添加</Button>
            </div>
            {responseHeaders.length === 0 && (
              <span style={{ fontSize: 12, color: 'var(--color-text-disabled)' }}>暂无自定义响应头</span>
            )}
            {responseHeaders.map((h, idx) => (
              <div key={idx} className="mock-header-row">
                <Input placeholder="Key" value={h.key} onChange={e => updateHeader(idx, 'key', e.target.value)} style={{ flex: 1 }} />
                <Input placeholder="Value" value={h.value} onChange={e => updateHeader(idx, 'value', e.target.value)} style={{ flex: 1 }} />
                <Button type="text" danger size="small" icon={<DeleteOutlined />} onClick={() => removeHeader(idx)} />
              </div>
            ))}
          </div>

          {/* 响应体 */}
          <div style={{ marginBottom: 8 }}>
            <div className="mock-section-label">
              <span className="mock-section-label-text">
                响应体
                <Tooltip title={
                  <div style={{ lineHeight: 2, fontSize: 12 }}>
                    <div><code>{"{{$timestamp}}"}</code> 毫秒时间戳</div>
                    <div><code>{"{{$uuid}}"}</code> UUID</div>
                    <div><code>{"{{$randomInt(1,100)}}"}</code> 随机整数</div>
                    <div><code>{"{{$date}}"}</code> 当前日期</div>
                    <div><code>{"{{@[10,20,30][Math.floor(Math.random()*3)]}}"}</code> 随机选择</div>
                  </div>
                }>
                  <QuestionCircleOutlined style={{ marginLeft: 6, color: 'var(--color-text-tertiary)', fontSize: 13 }} />
                </Tooltip>
              </span>
              <div className="mock-body-toolbar">
                <button className="mock-body-btn" onClick={() => {
                  const body = form.getFieldValue('response_body');
                  if (body) {
                    try {
                      form.setFieldsValue({ response_body: formatJsonWithTemplates(body) });
                      message.success('格式化成功');
                    } catch { message.error('JSON 格式不正确'); }
                  }
                }}>格式化</button>
                <button className="mock-body-btn" onClick={() => {
                  form.setFieldsValue({
                    response_body: JSON.stringify({
                      code: 200, message: "success",
                      data: { id: "{{id}}", userId: "{{userId}}", timestamp: "{{$timestamp}}", date: "{{$date(YYYY-MM-DD HH:mm:ss)}}", random: "{{$randomInt(1,1000)}}", randomFloat: "{{@Math.random().toFixed(4)}}", isoTime: "{{@new Date().toISOString()}}" }
                    }, null, 2)
                  });
                }}>模板</button>
                <button className="mock-body-btn" onClick={() => {
                  form.setFieldsValue({
                    response_body: JSON.stringify({
                      code: 200, message: "success",
                      data: { items: [{ id: "{{id}}", userId: "{{userId}}", timestamp: "{{$timestamp}}", date: "{{$date(YYYY-MM-DD HH:mm:ss)}}", random: "{{$randomInt(1,1000)}}", randomFloat: "{{@Math.random().toFixed(4)}}", isoTime: "{{@new Date().toISOString()}}" }] }
                    }, null, 2)
                  });
                  form.setFieldsValue({ response_count: 100, page_size: 10, json_path: '$.data.items' });
                }}>分页模板</button>
              </div>
            </div>
          </div>
          <Form.Item name="response_body" style={{ marginBottom: 0 }}>
            <div className="mock-codemirror">
              <CodeMirror
                value={form.getFieldValue('response_body') || ''}
                height="380px"
                extensions={jsonExtensions}
                onChange={(val) => form.setFieldsValue({ response_body: val })}
                placeholder="输入响应体 JSON"
                basicSetup={{ lineNumbers: true, foldGutter: true, highlightActiveLine: true }}
              />
            </div>
          </Form.Item>
        </Form>
      </Modal>

      {/* 测试弹窗 */}
      <Modal
        title={`测试 Mock — ${editingConfig?.name || ''}`}
        open={testModalVisible}
        onCancel={() => setTestModalVisible(false)}
        footer={[
          <Button key="close" onClick={() => setTestModalVisible(false)}>关闭</Button>,
          <Button key="test" type="primary" loading={testing} icon={<ReloadOutlined />} onClick={executeTest}>发送测试</Button>,
        ]}
        width={700}
        styles={{ body: { padding: '16px 20px' } }}
      >
        {editingConfig && (
          <div>
            <div className="mock-test-url-bar">
              <MethodBadge method={editingConfig.method} />
              <span className="mock-test-url-text">{`/api/mock${editingConfig.url_path}`}</span>
            </div>
            {testResult && (
              <div>
                <div className="mock-test-result-header">
                  <StatusCodeBadge code={testResult.status} />
                  <span style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>{testResult.statusText}</span>
                </div>
                <div className="mock-test-result-body">
                  <pre>{typeof testResult.body === 'object' ? JSON.stringify(testResult.body, null, 2) : String(testResult.body)}</pre>
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

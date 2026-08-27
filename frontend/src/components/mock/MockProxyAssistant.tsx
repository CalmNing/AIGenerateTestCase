import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Drawer, Tabs, Table, Button, Tag, Switch, Modal, Form, Input, InputNumber,
  Space, Badge, Popconfirm, message, Typography, Descriptions, FloatButton,
  Popover, Empty, Select, Divider,
} from 'antd';
import {
  ApiOutlined, PlusOutlined, DeleteOutlined, ReloadOutlined,
  PlayCircleOutlined, PauseCircleOutlined, DownloadOutlined, LinkOutlined,
  CheckCircleFilled, QuestionCircleOutlined,
} from '@ant-design/icons';
import { mockProxyApi, MOCK_PROXY_CA_CERT_URL } from '../../services/api';
import type { MockProxyRule, MockProxyStatus, MockLogItem } from '../../types';

const { Text, Paragraph } = Typography;

const LOG_PAGE_SIZE = 20;

const MethodTag: React.FC<{ method: string }> = ({ method }) => {
  const colorMap: Record<string, string> = {
    GET: 'green', POST: 'blue', PUT: 'orange', PATCH: 'purple',
    DELETE: 'red', OPTIONS: 'cyan', HEAD: 'default',
  };
  return <Tag color={colorMap[method?.toUpperCase()] || 'default'}>{method}</Tag>;
};

const LogResultTag: React.FC<{ log: MockLogItem }> = ({ log }) => {
  const v = log.config_name || '';
  if (v.startsWith('[代理→Mock')) return <Tag color="gold">代理→Mock</Tag>;
  if (v.startsWith('[代理透传') || v.startsWith('[代理→透传')) return <Tag color="geekblue">透传</Tag>;
  if (v.startsWith('[项目Mock]')) return <Tag color="volcano">项目Mock</Tag>;
  if (!log.matched) return <Tag color="red">未命中</Tag>;
  return <Tag color="green">Mock</Tag>;
};

const METHOD_OPTIONS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD']
  .map((m) => ({ value: m, label: m }));

type LogSource = 'mock' | 'missed' | 'proxy_mock' | 'passthrough' | 'project_mock';

const SOURCE_OPTIONS: { value: LogSource; label: string }[] = [
  { value: 'mock', label: 'Mock 命中' },
  { value: 'missed', label: '未命中' },
  { value: 'proxy_mock', label: '代理→Mock' },
  { value: 'passthrough', label: '透传' },
  { value: 'project_mock', label: '项目Mock' },
];

interface RuleFormValues {
  name: string;
  host_pattern: string;
  path_pattern?: string;
  enabled: boolean;
}

function prettyMaybeJson(text?: string | null): string | undefined {
  if (!text) return undefined;
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

function formatLogTime(t?: string | null): string {
  if (!t) return '-';
  const d = new Date(t);
  if (Number.isNaN(d.getTime())) return t;
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function validateHostPattern(_rule: unknown, value?: string): Promise<void> {
  const s = (value || '').trim();
  if (!s) return Promise.reject(new Error('请输入主机模式'));
  if (/\s/.test(s)) return Promise.reject(new Error('主机模式不能包含空格'));
  if (!/^[\w.*-]+$/.test(s)) return Promise.reject(new Error('仅支持域名、IP 或通配符，如 *.example.com'));
  if (s.includes(':')) return Promise.reject(new Error('不要携带端口，代理按主机匹配'));
  if (s.includes('*') && !s.startsWith('*.')) return Promise.reject(new Error('* 只能作为前缀使用，如 *.example.com'));
  return Promise.resolve();
}

function validatePathPattern(_rule: unknown, value?: string): Promise<void> {
  const s = (value || '').trim();
  if (/\s/.test(s)) return Promise.reject(new Error('路径模式不能包含空格'));
  let depth = 0;
  for (const ch of s) {
    if (ch === '{') depth += 1;
    if (ch === '}') depth -= 1;
    if (depth < 0) break;
  }
  if (depth !== 0) return Promise.reject(new Error('{ } 占位符需成对出现'));
  return Promise.resolve();
}

function hostHint(v?: string): string {
  const s = (v || '').trim();
  if (!s) return '支持精确域名、IP 或通配符 *.example.com';
  if (s.startsWith('*.')) return `将拦截所有 ${s.slice(1)} 子域（不含根域 ${s.slice(2)}）`;
  return `将精确匹配主机 ${s}`;
}

function pathHint(v?: string): string {
  const s = (v || '').trim();
  if (!s || s === '*') return '留空或 * 表示拦截该主机下全部接口';
  if (s.endsWith('/*')) return `匹配以 ${s.slice(0, -1)} 开头的所有接口`;
  if (s.includes('{')) return `{param} 匹配单个路径段，如 ${s.replace(/\{[^}]*\}/, '123')}`;
  return `将精确匹配路径 ${s}`;
}

interface LogQuery {
  page: number;
  method?: string;
  source?: LogSource;
  search: string;
}

const MockProxyAssistant: React.FC = () => {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<MockProxyStatus | null>(null);
  const [rules, setRules] = useState<MockProxyRule[]>([]);
  const [logs, setLogs] = useState<MockLogItem[]>([]);
  const [logTotal, setLogTotal] = useState(0);
  const [loadingRules, setLoadingRules] = useState(false);
  const [loadingLogs, setLoadingLogs] = useState(false);
  const [starting, setStarting] = useState(false);
  const [portInput, setPortInput] = useState<number | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [searchInput, setSearchInput] = useState('');
  const [logQuery, setLogQuery] = useState<LogQuery>({ page: 1, search: '' });
  const [ruleModalVisible, setRuleModalVisible] = useState(false);
  const [editingRule, setEditingRule] = useState<MockProxyRule | null>(null);
  const [detailLog, setDetailLog] = useState<MockLogItem | null>(null);
  const [guideOpen, setGuideOpen] = useState(true);
  const [form] = Form.useForm<RuleFormValues>();
  const logQueryRef = useRef(logQuery);
  logQueryRef.current = logQuery;

  const running = !!status?.running;
  const hasRules = rules.length > 0;
  const setupDone = running && hasRules;
  const proxyAddr = status?.local_ip && status?.port ? `${status.local_ip}:${status.port}` : null;

  const hostVal = Form.useWatch('host_pattern', form);
  const pathVal = Form.useWatch('path_pattern', form);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await mockProxyApi.getStatus();
      if (res.code === 200 && res.data) {
        setStatus(res.data);
        if (res.data.port) setPortInput(res.data.port);
      }
    } catch {
      // 静默失败，保留上次状态
    }
  }, []);

  const fetchRules = useCallback(async () => {
    setLoadingRules(true);
    try {
      const res = await mockProxyApi.getRules();
      if (res.code === 200) setRules(res.data || []);
    } catch {
      // ignore
    } finally {
      setLoadingRules(false);
    }
  }, []);

  const fetchLogs = useCallback(async (silent = false) => {
    if (!silent) setLoadingLogs(true);
    try {
      const q = logQueryRef.current;
      const res = await mockProxyApi.getLogs({
        page: q.page,
        page_size: LOG_PAGE_SIZE,
        method: q.method || undefined,
        source: q.source,
        search: q.search.trim() || undefined,
      });
      if (res.code === 200) {
        setLogs(res.data || []);
        setLogTotal(res.pagination?.total ?? res.data?.length ?? 0);
      }
    } catch {
      // ignore
    } finally {
      if (!silent) setLoadingLogs(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  useEffect(() => {
    const period = open ? 5000 : 15000;
    const timer = setInterval(fetchStatus, period);
    return () => clearInterval(timer);
  }, [open, fetchStatus]);

  useEffect(() => {
    if (open) {
      fetchRules();
    }
  }, [open, fetchRules]);

  useEffect(() => {
    if (open) fetchLogs();
  }, [logQuery, open, fetchLogs]);

  useEffect(() => {
    if (!open || !autoRefresh) return;
    const timer = setInterval(() => fetchLogs(true), 4000);
    return () => clearInterval(timer);
  }, [open, autoRefresh, fetchLogs]);

  useEffect(() => {
    setGuideOpen(!setupDone);
  }, [setupDone]);

  const refreshAll = () => {
    fetchStatus();
    fetchRules();
    fetchLogs();
  };

  const patchLogQuery = (patch: Partial<LogQuery>) => {
    setLogQuery((prev) => ({ ...prev, ...patch }));
  };

  const handleStart = async () => {
    setStarting(true);
    try {
      const res = await mockProxyApi.start(portInput ?? undefined);
      if (res.code === 200) {
        message.success(res.message || 'Mock 代理已启动');
        setStatus((prev) => (prev ? { ...prev, running: true, port: res.data?.port ?? prev.port } : res.data ?? prev));
      } else {
        message.error(res.message || '启动失败');
      }
    } catch {
      message.error('启动失败');
    } finally {
      setStarting(false);
    }
  };

  const handleStop = async () => {
    try {
      const res = await mockProxyApi.stop();
      if (res.code === 200) {
        message.success(res.message || 'Mock 代理已停止');
        setStatus((prev) => (prev ? { ...prev, running: false, port: null } : prev));
      } else {
        message.error(res.message || '停止失败');
      }
    } catch {
      message.error('停止失败');
    }
  };

  const handleClearLogs = async () => {
    try {
      const res = await mockProxyApi.clearLogs();
      if (res.code === 200) {
        message.success(res.message || '日志已清空');
        patchLogQuery({ page: 1 });
        fetchStatus();
      } else {
        message.error(res.message || '清空失败');
      }
    } catch {
      message.error('清空失败');
    }
  };

  const handleToggleRule = async (rule: MockProxyRule, enabled: boolean) => {
    try {
      const res = await mockProxyApi.updateRule(rule.id, { enabled });
      if (res.code === 200) {
        setRules((prev) => prev.map((r) => (r.id === rule.id ? { ...r, enabled } : r)));
      } else {
        message.error(res.message || '更新失败');
      }
    } catch {
      message.error('更新失败');
    }
  };

  const handleDeleteRule = async (ruleId: number) => {
    try {
      const res = await mockProxyApi.deleteRule(ruleId);
      if (res.code === 200) {
        message.success('规则已删除');
        setRules((prev) => prev.filter((r) => r.id !== ruleId));
        fetchStatus();
      } else {
        message.error(res.message || '删除失败');
      }
    } catch {
      message.error('删除失败');
    }
  };

  const openCreateModal = () => {
    setEditingRule(null);
    form.setFieldsValue({ name: '', host_pattern: '', path_pattern: '', enabled: true });
    setRuleModalVisible(true);
  };

  const openEditModal = (rule: MockProxyRule) => {
    setEditingRule(rule);
    form.setFieldsValue({
      name: rule.name,
      host_pattern: rule.host_pattern,
      path_pattern: rule.path_pattern,
      enabled: rule.enabled,
    });
    setRuleModalVisible(true);
  };

  const handleRuleSubmit = async () => {
    try {
      const values = await form.validateFields();
      const payload = {
        name: values.name || '',
        host_pattern: values.host_pattern.trim(),
        path_pattern: (values.path_pattern || '').trim(),
        enabled: values.enabled,
      };
      const res = editingRule
        ? await mockProxyApi.updateRule(editingRule.id, payload)
        : await mockProxyApi.createRule(payload);
      if (res.code === 200) {
        message.success(editingRule ? '规则已更新' : '规则已创建');
        setRuleModalVisible(false);
        fetchRules();
        fetchStatus();
      } else {
        message.error(res.message || '保存失败');
      }
    } catch {
      // 表单校验失败
    }
  };

  const statusBadge = (
    <Badge
      status={running ? 'processing' : 'default'}
      text={running ? `运行中 · 端口 ${status?.port}` : '已停止'}
    />
  );

  const stepNumStyle: React.CSSProperties = {
    display: 'inline-flex', width: 20, height: 20, borderRadius: '50%',
    background: '#f0f0f0', color: '#999', fontSize: 12,
    alignItems: 'center', justifyContent: 'center', flexShrink: 0,
  };
  const stepTitleStyle: React.CSSProperties = { fontWeight: 500, lineHeight: '20px' };

  const ruleColumns = [
    { title: '名称', dataIndex: 'name', key: 'name', width: 130, ellipsis: true, render: (v: string) => v || '-' },
    {
      title: '匹配模式', key: 'pattern',
      render: (_: unknown, r: MockProxyRule) => (
        <div>
          <div><Text code>{r.host_pattern}</Text></div>
          <div style={{ marginTop: 4 }}>
            {r.path_pattern ? <Text code type="secondary">{r.path_pattern}</Text> : <Tag>全部路径</Tag>}
          </div>
        </div>
      ),
    },
    {
      title: '启用', dataIndex: 'enabled', key: 'enabled', width: 64,
      render: (_: boolean, record: MockProxyRule) => (
        <Switch size="small" checked={record.enabled} onChange={(v) => handleToggleRule(record, v)} />
      ),
    },
    {
      title: '操作', key: 'action', width: 100,
      render: (_: unknown, record: MockProxyRule) => (
        <Space size={4}>
          <Button type="link" size="small" onClick={() => openEditModal(record)}>编辑</Button>
          <Popconfirm title="确定删除该规则？" onConfirm={() => handleDeleteRule(record.id)}>
            <Button type="link" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const logColumns = [
    {
      title: '方法', dataIndex: 'request_method', key: 'method', width: 80,
      render: (m: string) => <MethodTag method={m} />,
    },
    { title: '路径', dataIndex: 'request_path', key: 'path', ellipsis: true },
    {
      title: '结果', dataIndex: 'config_name', key: 'source', width: 120,
      render: (_: unknown, record: MockLogItem) => <LogResultTag log={record} />,
    },
    {
      title: '状态码', dataIndex: 'response_status_code', key: 'code', width: 70,
      render: (c: number) => <Tag color={c < 400 ? 'green' : c < 500 ? 'orange' : 'red'}>{c}</Tag>,
    },
    {
      title: '时间', dataIndex: 'created_at', key: 'time', width: 135,
      render: (t: string) => formatLogTime(t),
    },
    {
      title: '', key: 'detail', width: 50,
      render: (_: unknown, record: MockLogItem) => (
        <Button type="link" size="small" onClick={() => setDetailLog(record)}>详情</Button>
      ),
    },
  ];

  const helpContent = (
    <div style={{ maxWidth: 320, fontSize: 12 }}>
      <div><Text strong>通配符语法</Text></div>
      <div style={{ marginTop: 4 }}><Text code>*.example.com</Text> 匹配任意子域（不含根域）</div>
      <div><Text code>/api/user/*</Text> 匹配该前缀下所有接口</div>
      <div><Text code>/users/{'{id}'}</Text> 单段占位符；路径留空匹配全部接口</div>
      <Divider style={{ margin: '8px 0' }} />
      仅 HTTPS 中命中规则的主机做解密拦截（需先安装 CA 证书），其余流量盲隧道透传；未命中规则的请求原样转发到真实服务。
    </div>
  );

  return (
    <>
      <FloatButton
        className="mock-proxy-float-btn"
        icon={<ApiOutlined />}
        type="primary"
        style={{ right: 24, bottom: 20 }}
        tooltip="Mock 流量代理"
        badge={running ? { dot: true, color: '#52c41a' } : undefined}
        onClick={() => setOpen(true)}
      />

      <Drawer
        title={
          <Space>
            <ApiOutlined />
            <span>Mock 流量代理</span>
            {statusBadge}
          </Space>
        }
        extra={
          <Button type="text" size="small" icon={<ReloadOutlined />} onClick={refreshAll} title="刷新" />
        }
        placement="right"
        width={720}
        open={open}
        onClose={() => setOpen(false)}
      >
        {!guideOpen ? (
          <div style={{ border: '1px solid #b7eb8f', background: '#f6ffed', borderRadius: 8, padding: '12px 16px', marginBottom: 4 }}>
            <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
              <Space wrap size={12}>
                <Badge status="processing" text={<Text strong>运行中 · 端口 {status?.port}</Text>} />
                {proxyAddr && (
                  <Text code copyable={{ text: proxyAddr }}>{proxyAddr}</Text>
                )}
              </Space>
              <Space size={12}>
                <a onClick={() => setGuideOpen(true)}>接入指引</a>
                <Button size="small" danger icon={<PauseCircleOutlined />} onClick={handleStop}>停止</Button>
              </Space>
            </Space>
            <div style={{ fontSize: 12, color: '#999', marginTop: 6 }}>
              未命中规则的请求将透传到真实服务
            </div>
          </div>
        ) : (
          <div style={{ border: '1px solid #f0f0f0', borderRadius: 8, padding: '14px 18px', marginBottom: 4 }}>
            <div style={{ fontWeight: 600, marginBottom: 10 }}>开始拦截流量</div>

            <div style={{ display: 'flex', gap: 12, padding: '8px 0' }}>
              {running ? <CheckCircleFilled style={{ color: '#52c41a', fontSize: 20, marginTop: 1 }} /> : <span style={stepNumStyle}>1</span>}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={stepTitleStyle}>启动代理服务</div>
                {running ? (
                  <Text type="secondary" style={{ fontSize: 12 }}>监听端口 {status?.port}</Text>
                ) : (
                  <Space style={{ marginTop: 8 }}>
                    <InputNumber
                      min={1}
                      max={65535}
                      value={portInput}
                      onChange={(v) => setPortInput(v)}
                      addonBefore="端口"
                      style={{ width: 150 }}
                    />
                    <Button type="primary" loading={starting} icon={<PlayCircleOutlined />} onClick={handleStart}>启动</Button>
                  </Space>
                )}
              </div>
            </div>

            <div style={{ display: 'flex', gap: 12, padding: '8px 0' }}>
              <span style={stepNumStyle}><DownloadOutlined /></span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={stepTitleStyle}>下载并信任 CA 证书</div>
                <div style={{ fontSize: 12, color: '#999', marginTop: 2 }}>拦截 HTTPS 流量需导入系统/浏览器信任区，纯 HTTP 可跳过</div>
              </div>
              <a href={MOCK_PROXY_CA_CERT_URL} download="mock-proxy-ca.pem">
                <Button size="small" icon={<DownloadOutlined />}>下载证书</Button>
              </a>
            </div>

            <div style={{ display: 'flex', gap: 12, padding: '8px 0' }}>
              <span style={stepNumStyle}>3</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={stepTitleStyle}>设备 HTTP 代理指向</div>
                <div style={{ marginTop: 4 }}>
                  {proxyAddr ? (
                    <Text code copyable={{ text: proxyAddr }}>{proxyAddr}</Text>
                  ) : (
                    <Text type="secondary" style={{ fontSize: 12 }}>启动代理后显示地址</Text>
                  )}
                </div>
              </div>
            </div>

            <div style={{ display: 'flex', gap: 12, padding: '8px 0' }}>
              {hasRules ? <CheckCircleFilled style={{ color: '#52c41a', fontSize: 20, marginTop: 1 }} /> : <span style={stepNumStyle}>4</span>}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={stepTitleStyle}>
                  添加转发规则
                  <Text type="secondary" style={{ fontWeight: 400, marginLeft: 8, fontSize: 12 }}>
                    决定哪些主机的流量会被拦截为 Mock
                  </Text>
                </div>
              </div>
              <Button size="small" icon={<PlusOutlined />} onClick={openCreateModal}>新建规则</Button>
            </div>
          </div>
        )}

        <Tabs
          style={{ marginTop: 20 }}
          defaultActiveKey="rules"
          items={[
            {
              key: 'rules',
              label: `转发规则 (${rules.length})`,
              children: (
                <>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
                    <Popover content={helpContent} trigger="click" placement="bottomLeft">
                      <Button type="text" size="small" icon={<QuestionCircleOutlined />}>语法与说明</Button>
                    </Popover>
                    <Button type="primary" icon={<PlusOutlined />} onClick={openCreateModal}>新建规则</Button>
                  </div>
                  <Table
                    rowKey="id"
                    size="small"
                    columns={ruleColumns}
                    dataSource={rules}
                    loading={loadingRules}
                    pagination={false}
                    locale={{
                      emptyText: (
                        <Empty
                          image={Empty.PRESENTED_IMAGE_SIMPLE}
                          description="暂无转发规则，添加后即可拦截对应主机的流量"
                        />
                      ),
                    }}
                  />
                </>
              ),
            },
            {
              key: 'logs',
              label: `流量日志 (${logTotal})`,
              children: (
                <>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12, marginBottom: 14 }}>
                    <Space wrap size={10}>
                      <Select
                        value={logQuery.method}
                        allowClear
                        placeholder="方法"
                        style={{ width: 96 }}
                        options={METHOD_OPTIONS}
                        onChange={(v) => patchLogQuery({ page: 1, method: v })}
                      />
                      <Select
                        value={logQuery.source}
                        allowClear
                        placeholder="结果"
                        style={{ width: 118 }}
                        options={SOURCE_OPTIONS}
                        onChange={(v) => patchLogQuery({ page: 1, source: v as LogSource })}
                      />
                      <Input.Search
                        value={searchInput}
                        placeholder="搜索路径"
                        style={{ width: 180 }}
                        allowClear
                        onSearch={(v) => patchLogQuery({ page: 1, search: v })}
                        onChange={(e) => setSearchInput(e.target.value)}
                      />
                    </Space>
                    <Space size={12}>
                      <span style={{ fontSize: 12, color: '#666' }}>
                        <Switch
                          size="small"
                          checked={autoRefresh}
                          onChange={setAutoRefresh}
                          style={{ marginRight: 6 }}
                        />
                        自动刷新
                      </span>
                      <Popconfirm title="确定清空全部流量日志？" onConfirm={handleClearLogs}>
                        <Button size="small" icon={<DeleteOutlined />}>清空</Button>
                      </Popconfirm>
                    </Space>
                  </div>
                  <Table
                    rowKey="id"
                    size="small"
                    columns={logColumns}
                    dataSource={logs}
                    loading={loadingLogs}
                    pagination={{
                      current: logQuery.page,
                      pageSize: LOG_PAGE_SIZE,
                      total: logTotal,
                      size: 'small',
                      showSizeChanger: false,
                      onChange: (p) => patchLogQuery({ page: p }),
                    }}
                    locale={{
                      emptyText: (
                        <Empty
                          image={Empty.PRESENTED_IMAGE_SIMPLE}
                          description="暂无流量记录，启动代理并将设备指向上方地址后可见"
                        />
                      ),
                    }}
                  />
                </>
              ),
            },
          ]}
        />
      </Drawer>

      <Modal
        title={editingRule ? '编辑转发规则' : '新建转发规则'}
        open={ruleModalVisible}
        onOk={handleRuleSubmit}
        onCancel={() => setRuleModalVisible(false)}
        destroyOnClose
      >
        <Form form={form} layout="vertical" initialValues={{ enabled: true }}>
          <Form.Item name="name" label="规则名称">
            <Input placeholder="如：用户服务Mock" maxLength={100} />
          </Form.Item>
          <Form.Item
            name="host_pattern"
            label="主机模式"
            rules={[{ required: true }, { validator: validateHostPattern }]}
            extra={hostHint(hostVal)}
          >
            <Input placeholder="api.example.com、10.0.0.5 或 *.example.com" />
          </Form.Item>
          <Form.Item
            name="path_pattern"
            label="路径模式"
            rules={[{ validator: validatePathPattern }]}
            extra={pathHint(pathVal)}
          >
            <Input placeholder="/api/user/* 或 /users/{id}" prefix={<LinkOutlined />} />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch checkedChildren="开" unCheckedChildren="关" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="流量详情"
        open={!!detailLog}
        onCancel={() => setDetailLog(null)}
        footer={null}
        width={860}
        destroyOnClose
      >
        {detailLog && (
          <>
            <Descriptions bordered size="small">
              <Descriptions.Item label="方法" span={1}><MethodTag method={detailLog.request_method} /></Descriptions.Item>
              <Descriptions.Item label="状态码" span={1}>
                <Tag color={detailLog.response_status_code < 400 ? 'green' : 'red'}>{detailLog.response_status_code}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="时间" span={1}>{formatLogTime(detailLog.created_at)}</Descriptions.Item>
              <Descriptions.Item label="来源" span={3}>
                <LogResultTag log={detailLog} />
                <Text type="secondary" style={{ marginLeft: 8 }}>{detailLog.config_name || '-'}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="路径" span={3}><Text code copyable>{detailLog.request_path}</Text></Descriptions.Item>
            </Descriptions>
            <div style={{ display: 'flex', gap: 16, marginTop: 16 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <Paragraph style={{ marginBottom: 6 }}><Text strong>请求体：</Text></Paragraph>
                <pre style={{ background: '#f6f6f6', padding: 10, borderRadius: 4, maxHeight: 320, overflow: 'auto', fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0 }}>
                  {prettyMaybeJson(detailLog.request_body) || '(空)'}
                </pre>
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <Paragraph style={{ marginBottom: 6 }}><Text strong>响应体：</Text></Paragraph>
                <pre style={{ background: '#f6f6f6', padding: 10, borderRadius: 4, maxHeight: 320, overflow: 'auto', fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0 }}>
                  {prettyMaybeJson(detailLog.response_body) || '(空)'}
                </pre>
              </div>
            </div>
          </>
        )}
      </Modal>
    </>
  );
};

export default MockProxyAssistant;

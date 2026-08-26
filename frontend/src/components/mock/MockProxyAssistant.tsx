import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Drawer, Tabs, Table, Button, Tag, Switch, Modal, Form, Input, InputNumber,
  Space, Badge, Tooltip, Popconfirm, message, Typography, Descriptions, Alert, FloatButton,
} from 'antd';
import {
  ApiOutlined, PlusOutlined, DeleteOutlined, ReloadOutlined,
  PlayCircleOutlined, PauseCircleOutlined, DownloadOutlined, LinkOutlined,
} from '@ant-design/icons';
import { mockProxyApi, MOCK_PROXY_CA_CERT_URL } from '../../services/api';
import type { MockProxyRule, MockProxyStatus, MockLogItem } from '../../types';

const { Text, Paragraph } = Typography;

const MethodTag: React.FC<{ method: string }> = ({ method }) => {
  const colorMap: Record<string, string> = {
    GET: 'green', POST: 'blue', PUT: 'orange', PATCH: 'purple',
    DELETE: 'red', OPTIONS: 'cyan', HEAD: 'default',
  };
  return <Tag color={colorMap[method?.toUpperCase()] || 'default'}>{method}</Tag>;
};

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

const MockProxyAssistant: React.FC = () => {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<MockProxyStatus | null>(null);
  const [rules, setRules] = useState<MockProxyRule[]>([]);
  const [logs, setLogs] = useState<MockLogItem[]>([]);
  const [loadingRules, setLoadingRules] = useState(false);
  const [loadingLogs, setLoadingLogs] = useState(false);
  const [starting, setStarting] = useState(false);
  const [portInput, setPortInput] = useState<number | null>(null);
  const [ruleModalVisible, setRuleModalVisible] = useState(false);
  const [editingRule, setEditingRule] = useState<MockProxyRule | null>(null);
  const [detailLog, setDetailLog] = useState<MockLogItem | null>(null);
  const [form] = Form.useForm<RuleFormValues>();
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

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

  const fetchLogs = useCallback(async () => {
    setLoadingLogs(true);
    try {
      const res = await mockProxyApi.getLogs({ page: 1, page_size: 50 });
      if (res.code === 200) setLogs(res.data || []);
    } catch {
      // ignore
    } finally {
      setLoadingLogs(false);
    }
  }, []);

  // Drawer 打开时拉取数据并轮询状态；关闭即停
  useEffect(() => {
    if (open) {
      fetchStatus();
      fetchRules();
      fetchLogs();
      pollRef.current = setInterval(fetchStatus, 5000);
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [open, fetchStatus, fetchRules, fetchLogs]);

  const handleStart = async () => {
    setStarting(true);
    try {
      const res = await mockProxyApi.start(portInput ?? undefined);
      if (res.code === 200) {
        message.success(res.message || 'Mock 代理已启动');
        setStatus(prev => prev ? { ...prev, running: true, port: res.data?.port ?? prev.port } : res.data ?? prev);
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
        setStatus(prev => prev ? { ...prev, running: false, port: null } : prev);
      } else {
        message.error(res.message || '停止失败');
      }
    } catch {
      message.error('停止失败');
    }
  };

  const handleToggleRule = async (rule: MockProxyRule, enabled: boolean) => {
    try {
      const res = await mockProxyApi.updateRule(rule.id, { enabled });
      if (res.code === 200) {
        setRules(prev => prev.map(r => (r.id === rule.id ? { ...r, enabled } : r)));
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
        setRules(prev => prev.filter(r => r.id !== ruleId));
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
      } else {
        message.error(res.message || '保存失败');
      }
    } catch {
      // 表单校验失败
    }
  };

  const statusBadge = (
    <Badge
      status={status?.running ? 'processing' : 'default'}
      text={status?.running ? `运行中 · 端口 ${status.port}` : '已停止'}
    />
  );

  const ruleColumns = [
    { title: '名称', dataIndex: 'name', key: 'name', ellipsis: true, render: (v: string) => v || '-' },
    {
      title: '主机模式', dataIndex: 'host_pattern', key: 'host_pattern', ellipsis: true,
      render: (v: string) => <Text code>{v}</Text>,
    },
    {
      title: '路径模式', dataIndex: 'path_pattern', key: 'path_pattern', ellipsis: true,
      render: (v: string) => (v ? <Text code>{v}</Text> : <Tag>全部路径</Tag>),
    },
    {
      title: '启用', dataIndex: 'enabled', key: 'enabled', width: 70,
      render: (_: boolean, record: MockProxyRule) => (
        <Switch size="small" checked={record.enabled} onChange={(v) => handleToggleRule(record, v)} />
      ),
    },
    {
      title: '操作', key: 'action', width: 110,
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
      title: '结果', dataIndex: 'config_name', key: 'source', width: 130,
      render: (v: string, record: MockLogItem) => {
        if (v.startsWith('[代理→Mock')) return <Tag color="gold">代理→Mock</Tag>;
        if (v.startsWith('[代理透传') || v.startsWith('[代理→透传')) return <Tag color="geekblue">透传</Tag>;
        if (v.startsWith('[项目Mock]')) return <Tag color="volcano">项目Mock</Tag>;
        if (!record.matched) return <Tag color="red">未命中</Tag>;
        return <Tag color="green">Mock</Tag>;
      },
    },
    {
      title: '状态码', dataIndex: 'response_status_code', key: 'code', width: 70,
      render: (c: number) => <Tag color={c < 400 ? 'green' : c < 500 ? 'orange' : 'red'}>{c}</Tag>,
    },
    {
      title: '时间', dataIndex: 'created_at', key: 'time', width: 95,
      render: (t: string) => (t ? new Date(t).toLocaleTimeString() : '-'),
    },
    {
      title: '', key: 'detail', width: 50,
      render: (_: unknown, record: MockLogItem) => (
        <Button type="link" size="small" onClick={() => setDetailLog(record)}>详情</Button>
      ),
    },
  ];

  return (
    <>
      {/* 全局悬浮按钮：AI 助手(140)、变量助手(80)、Mock 代理(20) */}
      <FloatButton
        className="mock-proxy-float-btn"
        icon={<ApiOutlined />}
        type="primary"
        style={{ right: 24, bottom: 20 }}
        tooltip="Mock 代理"
        onClick={() => setOpen(true)}
      />

      <Drawer
        title={
          <Space>
            <ApiOutlined />
            <span>Mock 代理</span>
            {statusBadge}
          </Space>
        }
        placement="right"
        width={560}
        open={open}
        onClose={() => setOpen(false)}
      >
        {/* 状态与控制 */}
        <div style={{ marginBottom: 16 }}>
          <Space wrap>
            <InputNumber
              min={1}
              max={65535}
              value={portInput}
              onChange={(v) => setPortInput(v)}
              disabled={!!status?.running}
              addonBefore="端口"
              style={{ width: 150 }}
            />
            {status?.running ? (
              <Button danger icon={<PauseCircleOutlined />} onClick={handleStop}>停止</Button>
            ) : (
              <Button type="primary" loading={starting} icon={<PlayCircleOutlined />} onClick={handleStart}>启动</Button>
            )}
            <Tooltip title="下载 CA 根证书并导入系统/浏览器信任区后，才能对 HTTPS 做拦截">
              <a href={MOCK_PROXY_CA_CERT_URL} download="mock-proxy-ca.pem">
                <Button icon={<DownloadOutlined />}>下载CA证书</Button>
              </a>
            </Tooltip>
            <Button icon={<ReloadOutlined />} onClick={() => { fetchStatus(); fetchRules(); fetchLogs(); }} />
          </Space>
          {status?.running && (
            <Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0, fontSize: 12 }}>
              请将浏览器/App 的 HTTP 代理指向 <Text code>本机IP:{status.port}</Text>；
              未命中规则的请求将透传到真实服务。
            </Paragraph>
          )}
        </div>

        <Tabs
          defaultActiveKey="rules"
          items={[
            {
              key: 'rules',
              label: `转发规则 (${rules.length})`,
              children: (
                <>
                  <Alert
                    type="info" showIcon style={{ marginBottom: 12 }}
                    message="仅 HTTPS 命中规则的主机做解密拦截，其余流量盲隧道透传"
                  />
                  <div style={{ marginBottom: 12 }}>
                    <Button type="primary" icon={<PlusOutlined />} onClick={openCreateModal}>新建规则</Button>
                  </div>
                  <Table
                    rowKey="id"
                    size="small"
                    columns={ruleColumns}
                    dataSource={rules}
                    loading={loadingRules}
                    pagination={false}
                    locale={{ emptyText: '暂无规则，点击「新建规则」添加' }}
                  />
                </>
              ),
            },
            {
              key: 'logs',
              label: `流量日志 (${logs.length})`,
              children: (
                <Table
                  rowKey="id"
                  size="small"
                  columns={logColumns}
                  dataSource={logs}
                  loading={loadingLogs}
                  pagination={{ pageSize: 20, size: 'small' }}
                  locale={{ emptyText: '暂无流量记录，启动代理并配置系统代理后可见' }}
                />
              ),
            },
          ]}
        />

        {/* 通配符语法速查 */}
        <div style={{ marginTop: 12, padding: '8px 12px', background: '#fafafa', borderRadius: 6, fontSize: 12, color: '#666' }}>
          <div><Text strong>语法速查：</Text></div>
          <div><Text code>*.example.com</Text> 匹配任意子域（不含根域）</div>
          <div><Text code>/api/user/*</Text> 匹配该前缀下所有接口</div>
          <div><Text code>/users/{'{id}'}</Text> 单段占位符；路径留空匹配全部接口</div>
        </div>
      </Drawer>

      {/* 新建/编辑规则弹窗 */}
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
            rules={[{ required: true, message: '请输入主机模式' }]}
          >
            <Input placeholder="api.example.com、10.0.0.5 或 *.example.com" />
          </Form.Item>
          <Form.Item
            name="path_pattern"
            label="路径模式"
            extra="留空表示拦截该主机下全部接口"
          >
            <Input placeholder="/api/user/* 或 /users/{id}" prefix={<LinkOutlined />} />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch checkedChildren="开" unCheckedChildren="关" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 日志详情弹窗 */}
      <Modal
        title="流量详情"
        open={!!detailLog}
        onCancel={() => setDetailLog(null)}
        footer={null}
        width={640}
        destroyOnClose
      >
        {detailLog && (
          <>
            <Descriptions bordered column={2} size="small">
              <Descriptions.Item label="方法" span={1}><MethodTag method={detailLog.request_method} /></Descriptions.Item>
              <Descriptions.Item label="状态码" span={1}>
                <Tag color={detailLog.response_status_code < 400 ? 'green' : 'red'}>{detailLog.response_status_code}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="来源" span={1}>{detailLog.config_name || '-'}</Descriptions.Item>
              <Descriptions.Item label="时间" span={1}>
                {new Date(detailLog.created_at).toLocaleString()}
              </Descriptions.Item>
              <Descriptions.Item label="路径" span={2}><Text code copyable>{detailLog.request_path}</Text></Descriptions.Item>
            </Descriptions>
            <Paragraph style={{ marginTop: 12, marginBottom: 4 }}><Text strong>请求体：</Text></Paragraph>
            <pre style={{ background: '#f6f6f6', padding: 8, borderRadius: 4, maxHeight: 160, overflow: 'auto', fontSize: 12 }}>
              {prettyMaybeJson(detailLog.request_body) || '(空)'}
            </pre>
            <Paragraph style={{ marginTop: 8, marginBottom: 4 }}><Text strong>响应体：</Text></Paragraph>
            <pre style={{ background: '#f6f6f6', padding: 8, borderRadius: 4, maxHeight: 240, overflow: 'auto', fontSize: 12 }}>
              {prettyMaybeJson(detailLog.response_body) || '(空)'}
            </pre>
          </>
        )}
      </Modal>
    </>
  );
};

export default MockProxyAssistant;

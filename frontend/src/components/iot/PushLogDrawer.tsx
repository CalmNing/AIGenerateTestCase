import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Button, Drawer, Empty, Input, Modal, Popconfirm, Segmented, Select, Space, Table,
  Tabs, Tag, Tooltip, Descriptions, message,
} from 'antd';
import {
  CopyOutlined, DeleteOutlined, HistoryOutlined, ReloadOutlined,
} from '@ant-design/icons';
import { pushLogApi } from '../../services/api';
import { PushLogItem } from '../../types';
import { copyTextToClipboard } from '../../utils/clipboard';
import './IoTDataPushPlatform.css';

const LOG_PAGE_SIZE = 20;

const METHOD_OPTIONS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']
  .map((m) => ({ value: m, label: m }));

const PROTOCOL_OPTIONS = [
  { value: 'all', label: '全部协议' },
  { value: 'http', label: 'HTTP' },
  { value: 'mqtt', label: 'MQTT' },
  { value: 'websocket', label: 'WebSocket' },
];

const isMqttLog = (log: PushLogItem): boolean => log.protocol === 'mqtt';
const isWsLog = (log: PushLogItem): boolean => log.protocol === 'websocket';

const MethodTag: React.FC<{ method: string }> = ({ method }) => {
  const colorMap: Record<string, string> = {
    GET: 'green', POST: 'blue', PUT: 'orange', PATCH: 'purple', DELETE: 'red',
  };
  return <Tag color={colorMap[method?.toUpperCase()] || 'default'}>{method}</Tag>;
};

// MQTT 记录用 MQTT 标签替代 HTTP 方法标签
const ProtocolTag: React.FC<{ log: PushLogItem }> = ({ log }) => {
  if (isMqttLog(log)) return <Tag color="purple">MQTT</Tag>;
  if (isWsLog(log)) return <Tag color="cyan">WS</Tag>;
  return <MethodTag method={log.request_method} />;
};

const StatusTag: React.FC<{ log: PushLogItem }> = ({ log }) => {
  if (log.error) return <Tag color="red">失败</Tag>;
  // MQTT 发布没有 HTTP 状态码，只区分成功/失败
  if (log.protocol === 'mqtt') return <Tag color="green">发布成功</Tag>;
  // WebSocket 连接同样没有 HTTP 状态码
  if (log.protocol === 'websocket') return <Tag color="green">连接完成</Tag>;
  const c = log.response_status;
  return (
    <Tag color={c >= 200 && c < 400 ? 'green' : c >= 400 && c < 500 ? 'orange' : c >= 500 ? 'red' : 'default'}>
      {c || '-'}
    </Tag>
  );
};

function formatLogTime(t?: string | null): string {
  if (!t) return '-';
  const d = new Date(t);
  if (Number.isNaN(d.getTime())) return t;
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function prettyMaybeJson(text?: string | null): string {
  if (!text) return '';
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

function shellSingleQuote(s: string): string {
  return `'${s.replace(/'/g, `'\\''`)}'`;
}

/**
 * 由推送记录生成可直接执行的 cURL 命令。
 * 默认基于解析后（变量替换完成）的请求，保证复制出去即可复现真实发送的请求。
 */
export function buildCurlCommand(log: PushLogItem, useParsed = true): string {
  const method = (log.request_method || 'GET').toUpperCase();
  const baseUrl = useParsed
    ? (log.final_url || log.source_url || '')
    : (log.source_url || log.final_url || '');
  const params = (useParsed ? log.final_params : log.request_params) || {};
  const headers = (useParsed ? log.final_headers : log.request_headers) || {};
  const body = useParsed ? log.final_body : log.request_body;

  let url = baseUrl;
  const queryEntries = Object.entries(params).filter(([k]) => k);
  if (queryEntries.length > 0) {
    const qs = queryEntries
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v ?? ''))}`)
      .join('&');
    if (qs) url += (url.includes('?') ? '&' : '?') + qs;
  }

  const lines: string[] = [`curl -X ${method} ${shellSingleQuote(url)}`];
  for (const [k, v] of Object.entries(headers)) {
    if (!k) continue;
    lines.push(`  -H ${shellSingleQuote(`${k}: ${v ?? ''}`)}`);
  }
  if (body !== null && body !== undefined && body !== '') {
    lines.push(`  --data-raw ${shellSingleQuote(String(body))}`);
  }
  return lines.join(' \\\n');
}

/**
 * 解析 MQTT broker 地址，兼容 mqtt://host:1883、mqtts://host、host:1883 等写法。
 */
function parseBroker(broker?: string | null): { host: string; port: string } {
  const raw = (broker || '').trim();
  if (!raw) return { host: '', port: '1883' };
  const withoutScheme = raw.replace(/^[a-zA-Z]+:\/\//, '');
  const slash = withoutScheme.indexOf('/');
  const hostPort = slash >= 0 ? withoutScheme.slice(0, slash) : withoutScheme;
  const tlsDefault = /^(mqtts|ssl|tls):\/\//i.test(raw);
  const idx = hostPort.lastIndexOf(':');
  if (idx > 0 && /^\d+$/.test(hostPort.slice(idx + 1))) {
    return { host: hostPort.slice(0, idx), port: hostPort.slice(idx + 1) };
  }
  return { host: hostPort, port: tlsDefault ? '8883' : '1883' };
}

// 部分后端版本可能回传 MQTT 认证信息，这里做可选兼容，避免类型断言
type MqttLogExtra = PushLogItem & {
  mqtt_username?: string | null;
  mqtt_password?: string | null;
};

/**
 * 由 MQTT 推送记录生成可直接执行的 mosquitto_pub 命令。
 */
export function buildMosquittoCommand(log: PushLogItem): string {
  const extra = log as MqttLogExtra;
  const { host, port } = parseBroker(log.mqtt_broker);
  const topic = log.mqtt_topic || '';
  const payload = log.final_body ?? log.request_body ?? '';
  const qos = log.mqtt_qos ?? 0;

  const lines: string[] = [
    `mosquitto_pub -h ${shellSingleQuote(host)} -p ${port}`,
    `  -t ${shellSingleQuote(topic)}`,
    `  -q ${qos}`,
  ];
  if (log.mqtt_retain) lines.push('  -r');
  if (log.mqtt_client_id) lines.push(`  -i ${shellSingleQuote(log.mqtt_client_id)}`);
  if (extra.mqtt_username) lines.push(`  -u ${shellSingleQuote(extra.mqtt_username)}`);
  if (extra.mqtt_password) lines.push(`  -P ${shellSingleQuote(extra.mqtt_password)}`);
  lines.push(`  -m ${shellSingleQuote(String(payload))}`);
  return lines.join(' \\\n');
}

/**
 * 由 WebSocket 推送记录生成可直接执行的 websocat 命令。
 * 默认基于解析后（变量替换完成）的 URL，请求头通过 -H 传递。
 */
export function buildWebsocatCommand(log: PushLogItem, useParsed = true): string {
  const url = (useParsed ? log.final_url : log.source_url)
    || log.final_url
    || log.source_url
    || '';
  const headers = (useParsed ? log.final_headers : log.request_headers) || {};
  const lines: string[] = ['websocat ' + shellSingleQuote(url)];
  for (const [k, v] of Object.entries(headers)) {
    if (!k) continue;
    lines.push('  -H ' + shellSingleQuote(k + ': ' + (v ?? '')));
  }
  return lines.join(' \\\n');
}

/** 按协议选择复制命令：MQTT → mosquitto_pub，WebSocket → websocat，HTTP → cURL */
export function buildPushCommand(log: PushLogItem, useParsed = true): string {
  if (isMqttLog(log)) return buildMosquittoCommand(log);
  if (isWsLog(log)) return buildWebsocatCommand(log, useParsed);
  return buildCurlCommand(log, useParsed);
}

async function copyText(text: string, tip: string): Promise<void> {
  // 统一走 copyTextToClipboard：http/局域网环境下 navigator.clipboard 不可用，
  // 由它降级到 execCommand 兜底，否则点击会直接报"复制失败"。
  const ok = await copyTextToClipboard(text);
  if (ok) {
    message.success(tip);
  } else {
    message.error('复制失败，请手动选择文本复制');
  }
}

interface PushLogDrawerProps {
  open: boolean;
  onClose: () => void;
}

const PushLogDrawer: React.FC<PushLogDrawerProps> = ({ open, onClose }) => {
  const [logs, setLogs] = useState<PushLogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [method, setMethod] = useState<string | undefined>(undefined);
  const [protocol, setProtocol] = useState<'http' | 'mqtt' | 'websocket' | 'all'>('all');
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [detailLog, setDetailLog] = useState<PushLogItem | null>(null);
  const [detailTab, setDetailTab] = useState('parsed');
  const queryRef = useRef({ page, method, search, protocol });
  queryRef.current = { page, method, search, protocol };

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const q = queryRef.current;
      const res = await pushLogApi.getLogs({
        page: q.page,
        page_size: LOG_PAGE_SIZE,
        // MQTT / WebSocket 记录没有 HTTP 方法，切到对应协议时不再叠加方法过滤
        method: q.protocol === 'mqtt' || q.protocol === 'websocket' ? undefined : q.method,
        search: q.search.trim() || undefined,
        protocol: q.protocol === 'all' ? undefined : q.protocol,
      });
      if (res.code === 200) {
        setLogs(res.data || []);
        setTotal(res.pagination?.total ?? res.data?.length ?? 0);
      } else {
        message.error(res.message || '获取推送记录失败');
      }
    } catch {
      message.error('获取推送记录失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) fetchLogs();
  }, [open, page, method, protocol, search, fetchLogs]);

  const handleClear = async () => {
    try {
      const res = await pushLogApi.clearLogs();
      if (res.code === 200) {
        message.success(res.message || '已清空全部记录');
        setPage(1);
        fetchLogs();
      } else {
        message.error(res.message || '清空失败');
      }
    } catch {
      message.error('清空失败');
    }
  };

  const handleDelete = async (id: number) => {
    try {
      const res = await pushLogApi.deleteLog(id);
      if (res.code === 200) {
        message.success('记录已删除');
        fetchLogs();
      } else {
        message.error(res.message || '删除失败');
      }
    } catch {
      message.error('删除失败');
    }
  };

  const openDetail = (log: PushLogItem) => {
    setDetailTab('parsed');
    setDetailLog(log);
  };

  const copyCurl = (log: PushLogItem, useParsed = true) =>
    copyText(
      buildPushCommand(log, useParsed),
      isMqttLog(log)
        ? 'mosquitto_pub 命令已复制到剪贴板，可直接分享'
        : isWsLog(log)
          ? 'websocat 命令已复制到剪贴板，可直接分享'
          : 'cURL 已复制到剪贴板，可直接分享',
    );

  const renderKvPre = (title: string, obj?: Record<string, string> | null) => (
    <div style={{ marginBottom: 12 }}>
      <div style={{ fontWeight: 600, marginBottom: 4, fontSize: 13 }}>{title}</div>
      {obj && Object.keys(obj).length > 0 ? (
        <pre className="iot-log-pre">
          {Object.entries(obj).map(([k, v]) => `${k}: ${v ?? ''}`).join('\n')}
        </pre>
      ) : (
        <pre className="iot-log-pre iot-log-pre--empty">(空)</pre>
      )}
    </div>
  );

  const renderBodyPre = (title: string, body?: string | null) => (
    <div style={{ marginBottom: 12 }}>
      <div style={{ fontWeight: 600, marginBottom: 4, fontSize: 13 }}>{title}</div>
      <pre className="iot-log-pre">
        {body ? prettyMaybeJson(body) : '(空)'}
      </pre>
    </div>
  );

  /** 前置步骤执行记录：每个步骤的请求摘要 + 响应体摘要 + 提取变量 */
  const renderPreSteps = (steps?: Array<Record<string, any>>) => {
    if (!steps || steps.length === 0) {
      return <pre className="iot-log-pre iot-log-pre--empty">(本次推送未执行前置步骤)</pre>;
    }
    return (
      <div>
        {steps.map((step, i) => {
          const ok = step.ok === true;
          const isParams = step.type === 'params';
          const isSetVar = step.type === 'set_variable';
          return (
            <div key={i} style={{ border: '1px solid var(--color-border-light, #f0f0f0)', borderRadius: 6, padding: 10, marginBottom: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
                <Tag color={ok ? 'green' : 'red'}>{ok ? '成功' : '失败'}</Tag>
                <span style={{ fontWeight: 600, fontSize: 13 }}>
                  步骤 {typeof step.index === 'number' ? step.index + 1 : i + 1}
                </span>
                {isParams ? (
                  <span style={{ fontSize: 12, color: 'var(--color-text-tertiary, #999)' }}>参数注入</span>
                ) : isSetVar ? (
                  <span style={{ fontSize: 12, color: 'var(--color-text-tertiary, #999)' }}>设置变量</span>
                ) : (
                  <span style={{ fontSize: 12, fontFamily: 'monospace' }}>
                    {step.method || 'GET'} {step.url || '(空)'}
                  </span>
                )}
                {typeof step.status_code === 'number' && step.status_code > 0 && (
                  <Tag color={step.status_code >= 200 && step.status_code < 300 ? 'green' : 'orange'}>{step.status_code}</Tag>
                )}
              </div>
              {isSetVar && (
                renderKvPre('设置变量', { [step.key || '(未命名)']: String(step.value ?? '') })
              )}
              {isParams && renderKvPre('注入参数', step.params || {})}
              {!isParams && !isSetVar && step.error && (
                <div style={{ marginBottom: 8 }}>
                  <div style={{ fontWeight: 600, marginBottom: 4, fontSize: 13 }}>错误</div>
                  <pre className="iot-log-pre" style={{ color: 'var(--color-error, #ff4d4f)' }}>{step.error}</pre>
                </div>
              )}
              {!isParams && !isSetVar && renderBodyPre('响应体', step.response_body)}
              {step.extracted && Object.keys(step.extracted).length > 0 && (
                renderKvPre('提取变量', step.extracted)
              )}
            </div>
          );
        })}
      </div>
    );
  };

  const columns = [
    {
      title: '协议/方法', dataIndex: 'request_method', key: 'method', width: 90,
      render: (_: string, record: PushLogItem) => <ProtocolTag log={record} />,
    },
    {
      title: '目标（解析后）', dataIndex: 'final_url', key: 'url', ellipsis: true,
      render: (v: string, record: PushLogItem) => {
        if (isMqttLog(record)) {
          const broker = record.mqtt_broker || '';
          const topic = record.mqtt_topic || '';
          return (
            <Tooltip title={`${broker} → ${topic}`}>
              <div className="iot-log-protocol-cell">
                <span className="iot-log-mqtt-target">{broker || '(未记录 Broker)'}</span>
                <span className="iot-log-mqtt-topic">{topic || '(未记录 Topic)'}</span>
              </div>
            </Tooltip>
          );
        }
        return (
          <Tooltip title={v || record.source_url}>
            <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{v || record.source_url}</span>
          </Tooltip>
        );
      },
    },
    {
      title: '结果', key: 'result', width: 90,
      render: (_: unknown, record: PushLogItem) => (
        <Space size={4}>
          <StatusTag log={record} />
          {record.mocked && <Tag color="gold">Mock</Tag>}
        </Space>
      ),
    },
    {
      title: '耗时', dataIndex: 'duration_ms', key: 'duration', width: 70,
      render: (v: number) => <span style={{ fontSize: 12 }}>{v}ms</span>,
    },
    {
      title: '时间', dataIndex: 'created_at', key: 'time', width: 150,
      render: (t: string) => <span style={{ fontSize: 12 }}>{formatLogTime(t)}</span>,
    },
    {
      title: '操作', key: 'action', width: 170,
      render: (_: unknown, record: PushLogItem) => (
        <Space size={4}>
          <Button type="link" size="small" onClick={() => openDetail(record)}>详情</Button>
          <Tooltip
            title={
              isMqttLog(record)
                ? '复制为 mosquitto_pub 命令'
                : isWsLog(record)
                  ? '复制为 websocat 命令'
                  : '复制为 cURL（解析后请求）'
            }
            overlayClassName="iot-tooltip"
          >
            <Button
              type="link"
              size="small"
              icon={<CopyOutlined />}
              onClick={() => copyCurl(record, true)}
            >
              {isMqttLog(record) || isWsLog(record) ? '命令' : 'cURL'}
            </Button>
          </Tooltip>
          <Popconfirm title="删除这条记录？" onConfirm={() => handleDelete(record.id)}>
            <Button type="link" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // MQTT 记录：只展示 Broker/Topic/QoS/Retain/ClientID/Payload，隐藏 HTTP 专属块
  const mqttRequestTab = (log: PushLogItem, parsed: boolean) => {
    const payload = parsed ? (log.final_body ?? log.request_body) : log.request_body;
    return (
      <div>
        <Descriptions bordered size="small" column={2} style={{ marginBottom: 12 }}>
          <Descriptions.Item label="Broker" span={2}>
            <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{log.mqtt_broker || '(空)'}</span>
          </Descriptions.Item>
          <Descriptions.Item label="Topic" span={2}>
            <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{log.mqtt_topic || '(空)'}</span>
          </Descriptions.Item>
          <Descriptions.Item label="QoS">{log.mqtt_qos ?? 0}</Descriptions.Item>
          <Descriptions.Item label="Retain">{log.mqtt_retain ? '是' : '否'}</Descriptions.Item>
          <Descriptions.Item label="Client ID" span={2}>
            <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{log.mqtt_client_id || '(自动分配)'}</span>
          </Descriptions.Item>
        </Descriptions>
        {renderBodyPre('Payload', payload)}
      </div>
    );
  };

  // WebSocket 记录：URL / 请求头 / 最后一条消息，没有 HTTP 响应
  const wsRequestTab = (log: PushLogItem, parsed: boolean) => {
    const url = parsed ? log.final_url : log.source_url;
    const headers = parsed ? log.final_headers : log.request_headers;
    return (
      <div>
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontWeight: 600, marginBottom: 4, fontSize: 13 }}>WebSocket URL</div>
          <pre className="iot-log-pre" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
            {url || '(空)'}
          </pre>
        </div>
        {renderKvPre('请求头', headers)}
        {renderBodyPre('最后一条消息', log.final_body)}
      </div>
    );
  };

  const requestTab = (log: PushLogItem, parsed: boolean) => {
    if (isMqttLog(log)) return mqttRequestTab(log, parsed);
    if (isWsLog(log)) return wsRequestTab(log, parsed);
    const url = parsed ? log.final_url : log.source_url;
    const headers = parsed ? log.final_headers : log.request_headers;
    const params = parsed ? log.final_params : log.request_params;
    const body = parsed ? log.final_body : log.request_body;
    return (
      <div>
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontWeight: 600, marginBottom: 4, fontSize: 13 }}>URL</div>
          <pre className="iot-log-pre" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
            {url || '(空)'}
          </pre>
        </div>
        {renderKvPre('请求头', headers)}
        {renderKvPre('查询参数', params)}
        {renderBodyPre('请求体', body)}
      </div>
    );
  };

  return (
    <>
      <Drawer
        title={
          <Space>
            <HistoryOutlined />
            <span>推送请求记录</span>
          </Space>
        }
        extra={
          <Button type="text" size="small" icon={<ReloadOutlined />} onClick={fetchLogs} title="刷新" />
        }
        placement="right"
        width={760}
        open={open}
        onClose={onClose}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12, marginBottom: 14 }}>
          <Space wrap size={10}>
            <Segmented
              value={protocol}
              options={PROTOCOL_OPTIONS}
              onChange={(v) => { setProtocol(v as 'http' | 'mqtt' | 'websocket' | 'all'); setPage(1); }}
            />
            <Select
              value={method}
              allowClear
              placeholder="方法"
              style={{ width: 96 }}
              disabled={protocol === 'mqtt' || protocol === 'websocket'}
              options={METHOD_OPTIONS}
              onChange={(v) => { setMethod(v); setPage(1); }}
            />
            <Input.Search
              value={searchInput}
              placeholder="搜索 URL"
              style={{ width: 200 }}
              allowClear
              onSearch={(v) => { setSearch(v); setPage(1); }}
              onChange={(e) => setSearchInput(e.target.value)}
            />
          </Space>
          <Popconfirm title="确定清空全部推送记录？" onConfirm={handleClear}>
            <Button size="small" icon={<DeleteOutlined />}>清空</Button>
          </Popconfirm>
        </div>

        <Table
          rowKey="id"
          size="small"
          columns={columns}
          dataSource={logs}
          loading={loading}
          pagination={{
            current: page,
            pageSize: LOG_PAGE_SIZE,
            total,
            size: 'small',
            showSizeChanger: false,
            onChange: (p) => setPage(p),
          }}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="暂无推送记录，发送一次请求后即可在此查看"
              />
            ),
          }}
        />
      </Drawer>

      <Modal
        title={
          detailLog && (
            <Space size={8} wrap>
              <ProtocolTag log={detailLog} />
              <span style={{ fontFamily: 'monospace', fontSize: 13 }}>
                {isMqttLog(detailLog)
                  ? `${detailLog.mqtt_broker || '-'} → ${detailLog.mqtt_topic || '-'}`
                  : (detailLog.final_url || detailLog.source_url)}
              </span>
            </Space>
          )
        }
        open={!!detailLog}
        onCancel={() => setDetailLog(null)}
        width={880}
        className="iot-modal"
        footer={
          detailLog && (
            <Space>
              <Button
                type="primary"
                icon={<CopyOutlined />}
                className="iot-btn-primary"
                onClick={() => copyCurl(detailLog, detailTab === 'parsed')}
              >
                {isMqttLog(detailLog) ? '复制为 mosquitto_pub' : isWsLog(detailLog) ? '复制为 websocat' : '复制为 cURL'}
              </Button>
              <Button
                onClick={() => copyText(
                  JSON.stringify(detailLog, null, 2),
                  '记录详情 JSON 已复制',
                )}
              >
                复制记录 JSON
              </Button>
              <Button onClick={() => setDetailLog(null)}>关闭</Button>
            </Space>
          )
        }
      >
        {detailLog && (
          <>
            <Descriptions bordered size="small" column={3} style={{ marginBottom: 14 }}>
              <Descriptions.Item label="状态">
                <StatusTag log={detailLog} />
              </Descriptions.Item>
              <Descriptions.Item label="耗时">{detailLog.duration_ms}ms</Descriptions.Item>
              <Descriptions.Item label="来源">
                {isMqttLog(detailLog)
                  ? <Tag color="purple">MQTT 发布</Tag>
                  : isWsLog(detailLog)
                    ? <Tag color="cyan">WebSocket 连接</Tag>
                    : (detailLog.mocked ? <Tag color="gold">Mock</Tag> : <Tag color="blue">真实转发</Tag>)}
              </Descriptions.Item>
              <Descriptions.Item label="时间" span={2}>
                {formatLogTime(detailLog.created_at)}
              </Descriptions.Item>
              <Descriptions.Item label="环境ID">
                {detailLog.environment_id ?? '-'}
              </Descriptions.Item>
              {detailLog.error && (
                <Descriptions.Item label="错误" span={3}>
                  <span style={{ color: 'var(--color-error, #ff4d4f)' }}>{detailLog.error}</span>
                </Descriptions.Item>
              )}
            </Descriptions>

            <Tabs
              activeKey={detailTab}
              onChange={setDetailTab}
              size="small"
              items={[
                {
                  key: 'parsed',
                  label: '解析后请求',
                  children: requestTab(detailLog, true),
                },
                {
                  key: 'raw',
                  label: '原始请求',
                  children: requestTab(detailLog, false),
                },
                // 前置步骤执行记录（仅 HTTP 推送可能存在）
                ...(isMqttLog(detailLog) || isWsLog(detailLog) ? [] : [{
                  key: 'pre_steps',
                  label: '前置步骤' + ((detailLog.pre_steps?.length || 0) > 0 ? ' (' + detailLog.pre_steps!.length + ')' : ''),
                  children: renderPreSteps(detailLog.pre_steps),
                }]),
                // MQTT / WebSocket 没有 HTTP 响应，隐藏响应 Tab（结果已在状态/错误中体现）
                ...(isMqttLog(detailLog) || isWsLog(detailLog) ? [] : [{
                  key: 'response',
                  label: '响应',
                  children: (
                    <div>
                      <Descriptions bordered size="small" column={2} style={{ marginBottom: 12 }}>
                        <Descriptions.Item label="状态码">
                          <StatusTag log={detailLog} />
                        </Descriptions.Item>
                        <Descriptions.Item label="耗时">
                          {detailLog.duration_ms}ms
                        </Descriptions.Item>
                      </Descriptions>
                      {renderKvPre('响应头', detailLog.response_headers)}
                      {renderBodyPre('响应体', detailLog.response_body)}
                    </div>
                  ),
                }]),
              ]}
            />
          </>
        )}
      </Modal>
    </>
  );
};

export default PushLogDrawer;

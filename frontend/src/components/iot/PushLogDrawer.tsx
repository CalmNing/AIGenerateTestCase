import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Button, Drawer, Empty, Input, Modal, Popconfirm, Select, Space, Table,
  Tabs, Tag, Tooltip, Descriptions, message,
} from 'antd';
import {
  CopyOutlined, DeleteOutlined, HistoryOutlined, ReloadOutlined,
} from '@ant-design/icons';
import { pushLogApi } from '../../services/api';
import { PushLogItem } from '../../types';
import './IoTDataPushPlatform.css';

const LOG_PAGE_SIZE = 20;

const METHOD_OPTIONS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']
  .map((m) => ({ value: m, label: m }));

const MethodTag: React.FC<{ method: string }> = ({ method }) => {
  const colorMap: Record<string, string> = {
    GET: 'green', POST: 'blue', PUT: 'orange', PATCH: 'purple', DELETE: 'red',
  };
  return <Tag color={colorMap[method?.toUpperCase()] || 'default'}>{method}</Tag>;
};

const StatusTag: React.FC<{ log: PushLogItem }> = ({ log }) => {
  if (log.error) return <Tag color="red">失败</Tag>;
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

async function copyText(text: string, tip: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    message.success(tip);
  } catch {
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
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [detailLog, setDetailLog] = useState<PushLogItem | null>(null);
  const [detailTab, setDetailTab] = useState('parsed');
  const queryRef = useRef({ page, method, search });
  queryRef.current = { page, method, search };

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const q = queryRef.current;
      const res = await pushLogApi.getLogs({
        page: q.page,
        page_size: LOG_PAGE_SIZE,
        method: q.method,
        search: q.search.trim() || undefined,
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
  }, [open, page, method, search, fetchLogs]);

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
    copyText(buildCurlCommand(log, useParsed), 'cURL 已复制到剪贴板，可直接分享');

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

  const columns = [
    {
      title: '方法', dataIndex: 'request_method', key: 'method', width: 80,
      render: (m: string) => <MethodTag method={m} />,
    },
    {
      title: '请求 URL（解析后）', dataIndex: 'final_url', key: 'url', ellipsis: true,
      render: (v: string, record: PushLogItem) => (
        <Tooltip title={v || record.source_url}>
          <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{v || record.source_url}</span>
        </Tooltip>
      ),
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
          <Tooltip title="复制为 cURL（解析后请求）" overlayClassName="iot-tooltip">
            <Button
              type="link"
              size="small"
              icon={<CopyOutlined />}
              onClick={() => copyCurl(record, true)}
            >
              cURL
            </Button>
          </Tooltip>
          <Popconfirm title="删除这条记录？" onConfirm={() => handleDelete(record.id)}>
            <Button type="link" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const requestTab = (log: PushLogItem, parsed: boolean) => {
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
            <Select
              value={method}
              allowClear
              placeholder="方法"
              style={{ width: 96 }}
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
              <MethodTag method={detailLog.request_method} />
              <span style={{ fontFamily: 'monospace', fontSize: 13 }}>
                {detailLog.final_url || detailLog.source_url}
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
                复制为 cURL
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
                {detailLog.mocked ? <Tag color="gold">Mock</Tag> : <Tag color="blue">真实转发</Tag>}
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
                {
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
                },
              ]}
            />
          </>
        )}
      </Modal>
    </>
  );
};

export default PushLogDrawer;

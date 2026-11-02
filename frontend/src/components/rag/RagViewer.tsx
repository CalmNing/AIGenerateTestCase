import React, { useEffect, useState } from 'react';
import { Card, Input, Button, Table, Tag, Statistic, Row, Col, Space, message, Modal, Popconfirm, Typography } from 'antd';
import { ReloadOutlined, SearchOutlined, SyncOutlined, PlusOutlined } from '@ant-design/icons';
import { ragApi, RagStats, RagHit } from '../../services/api';

const { Text } = Typography;

/**
 * RAG 检索观测页：索引状态、召回试查、同义词管理、手动重建。
 * 与生成链路共用同一套检索（utils/retrieval.py），用于调参/验证/巡检。
 */
const RagViewer: React.FC = () => {
  const [stats, setStats] = useState<RagStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<RagHit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [termsModalOpen, setTermsModalOpen] = useState(false);
  const [termsText, setTermsText] = useState('');
  const [reindexing, setReindexing] = useState(false);

  const loadStats = async () => {
    setLoading(true);
    try {
      const res: any = await ragApi.stats();
      if (res?.code === 200) setStats(res.data);
      else message.error(res?.message || '加载失败');
    } catch (e: any) {
      message.error('加载失败: ' + (e?.message || '未知错误'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadStats(); }, []);

  const doSearch = async () => {
    if (!query.trim()) return;
    setSearching(true);
    try {
      const res: any = await ragApi.search({ query: query.trim(), top_k: 10 });
      if (res?.code === 200) {
        setHits(res.data.hits);
        if (!res.data.count) message.info('无召回——检查索引或换关键词');
      } else {
        message.error(res?.message || '搜索失败');
      }
    } catch (e: any) {
      message.error('搜索失败: ' + (e?.message || '未知错误'));
    } finally {
      setSearching(false);
    }
  };

  const openTerms = () => {
    if (!stats) return;
    setTermsText(
      Object.entries(stats.terms || {})
        .map(([k, v]) => `${k}: ${v.join(', ')}`)
        .join('\n'),
    );
    setTermsModalOpen(true);
  };

  const saveTerms = async () => {
    const terms: Record<string, string[]> = {};
    for (const line of termsText.split('\n')) {
      const idx = line.indexOf(':');
      if (idx < 0) continue;
      const key = line.slice(0, idx).trim();
      const vals = line.slice(idx + 1).split(/[,，]/).map((s) => s.trim()).filter(Boolean);
      if (key && vals.length) terms[key] = vals;
    }
    try {
      const res: any = await ragApi.updateTerms(terms);
      if (res?.code === 200) {
        message.success('同义词已更新');
        setTermsModalOpen(false);
        loadStats();
      } else {
        message.error(res?.message || '保存失败');
      }
    } catch (e: any) {
      message.error('保存失败: ' + (e?.message || '未知错误'));
    }
  };

  const doReindex = async () => {
    setReindexing(true);
    try {
      const res: any = await ragApi.reindex();
      if (res?.code === 200) {
        message.success(`已重建，索引 ${res.data.indexed_count} 条`);
        loadStats();
      } else {
        message.error(res?.message || '重建失败');
      }
    } catch (e: any) {
      message.error('重建失败: ' + (e?.message || '未知错误'));
    } finally {
      setReindexing(false);
    }
  };

  const hitColumns = [
    { title: '得分', dataIndex: 'score', key: 'score', width: 90, render: (v: number) => <Tag color={v < -1 ? 'green' : 'default'}>{v.toFixed(2)}</Tag> },
    { title: 'rowid', dataIndex: 'rowid', key: 'rowid', width: 90 },
    { title: '内容', dataIndex: 'content', key: 'content' },
  ];

  return (
    <div style={{ padding: 16, maxWidth: 1000, margin: '0 auto' }}>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}>
          <Card size="small">
            <Statistic title="索引文档数" value={stats?.indexed_count ?? '-'} />
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small">
            <Statistic title="同义词条目" value={Object.keys(stats?.terms ?? {}).length} />
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small">
            <Statistic title="索引文件" value={stats?.db_path?.split('\\').pop() || '-'} valueStyle={{ fontSize: 16 }} />
          </Card>
        </Col>
      </Row>

      <Space style={{ marginBottom: 16 }}>
        <Button icon={<ReloadOutlined />} onClick={loadStats} loading={loading}>刷新</Button>
        <Button icon={<PlusOutlined />} onClick={openTerms}>同义词管理</Button>
        <Popconfirm title="全量重建索引（历史需求）？" onConfirm={doReindex}>
          <Button icon={<SyncOutlined />} loading={reindexing}>重建索引</Button>
        </Popconfirm>
      </Space>

      <Card size="small" title="召回试查（与生成链路同一套检索逻辑）" style={{ marginBottom: 16 }}>
        <Space.Compact style={{ width: '100%' }}>
          <Input
            placeholder="输入需求关键词，如：账号密码登录"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onPressEnter={doSearch}
          />
          <Button type="primary" icon={<SearchOutlined />} onClick={doSearch} loading={searching}>
            搜索
          </Button>
        </Space.Compact>
        {hits !== null && (
          <Table
            size="small"
            style={{ marginTop: 12 }}
            rowKey="rowid"
            columns={hitColumns}
            dataSource={hits}
            pagination={{ pageSize: 5 }}
          />
        )}
      </Card>

      <Card
        size="small"
        title={`最近索引的文档（${stats?.recent_docs?.length ?? 0}）`}
        extra={<Text type="secondary" style={{ fontSize: 12 }}>按写入/更新时间排序；搜索不会新增文档</Text>}
      >
        {(stats?.recent_docs || []).map((d) => (
          <div key={d.rowid} style={{ padding: '4px 0', borderBottom: '1px solid var(--color-border-secondary)' }}>
            <Tag color={d.kind === 'example' ? 'blue' : 'default'} style={{ marginRight: 6 }}>
              {d.kind === 'example' ? '示例' : '历史'}
            </Tag>
            <Text type="secondary">#{d.rowid}</Text>{' '}
            {d.created_at && (
              <Text type="secondary" style={{ fontSize: 11, marginRight: 6 }}>
                {String(d.created_at).slice(0, 16)}
              </Text>
            )}
            {d.content}
          </div>
        ))}
        {!stats?.recent_docs?.length && <Text type="secondary">（空）</Text>}
      </Card>

      <Modal
        title="同义词管理（每行一条：标准词: 同义词1, 同义词2）"
        open={termsModalOpen}
        onOk={saveTerms}
        onCancel={() => setTermsModalOpen(false)}
        width={520}
      >
        <Input.TextArea rows={8} value={termsText} onChange={(e) => setTermsText(e.target.value)} />
        <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>
          例：登录: 登入, 账号登录 —— 召回时会自动扩展这些同义词
        </Text>
      </Modal>
    </div>
  );
};

export default RagViewer;
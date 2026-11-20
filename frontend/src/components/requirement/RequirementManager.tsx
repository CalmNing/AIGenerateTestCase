import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Button, Collapse, Empty, Form, Input, Modal, Popconfirm, Space,
  Spin, Table, Tag, Tooltip, Typography, message,
} from 'antd';
import {
  DeleteOutlined, EditOutlined, FileAddOutlined, FileTextOutlined,
  HistoryOutlined, LinkOutlined, MessageOutlined, PlusOutlined,
  ReloadOutlined, SyncOutlined,
} from '@ant-design/icons';
import {
  RequirementDoc, RequirementDiffEntry, RequirementRevision,
} from '../../types';
import { requirementDocApi } from '../../services/api';
import MarkdownView from './MarkdownView';
import ChatAssistant from '../chat/ChatAssistant';
import './RequirementManager.css';

const { Text } = Typography;

// 文档陈旧判定阈值：超过该时长未同步显示过期提示
const STALE_THRESHOLD_MS = 24 * 60 * 60 * 1000;

interface RequirementManagerProps {
  /** 当前选中的会话（登记需求时默认关联；列表按其过滤） */
  selectedSession?: { id: number; name?: string } | null;
  /** 当前选中的模块（登记需求时默认关联；0=全部模块） */
  selectedModule?: number | string | null;
}

const DIFF_TYPE_META: Record<string, { label: string; color: string }> = {
  added: { label: '新增', color: 'green' },
  modified: { label: '修改', color: 'orange' },
  removed: { label: '删除', color: 'red' },
};

const SOURCE_META: Record<string, { label: string; color: string }> = {
  feishu: { label: '需求文档', color: 'blue' },
  manual: { label: '手动录入', color: 'purple' },
};

const formatTime = (iso: string | null): string => {
  if (!iso) return '-';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '-' : d.toLocaleString('zh-CN', { hour12: false });
};

const diffSummary = (diffs: RequirementDiffEntry[]): string =>
  '新增 ' + diffs.filter((d) => d.type === 'added').length
  + '、修改 ' + diffs.filter((d) => d.type === 'modified').length
  + '、删除 ' + diffs.filter((d) => d.type === 'removed').length;

/** 解析大纲：章节路径按标题等级展开为缩进层级 */
interface OutlineNode {
  path: string;
  depth: number;
  label: string;
  count: number;
}

/** 解析结构大纲：按章节路径聚合（自动补齐父级章节），与正文 Markdown 标题层级一致 */
const buildOutline = (items: RequirementItemLike[]): OutlineNode[] => {
  const nodes: OutlineNode[] = [];
  const known = new Map<string, OutlineNode>();
  const add = (sec: string, count: number) => {
    const parts = sec.split(' / ').filter(Boolean);
    for (let i = 1; i <= parts.length; i += 1) {
      const path = parts.slice(0, i).join(' / ');
      let node = known.get(path);
      if (!node) {
        node = { path, depth: i, label: parts[i - 1], count: 0 };
        known.set(path, node);
        nodes.push(node);
      }
      if (i === parts.length) node.count += count;
    }
  };

  const sorted = [...(items || [])].sort((a, b) => a.order_index - b.order_index);
  const leading = sorted.filter((it) => !(it.section || '').trim());
  if (leading.length > 0) {
    nodes.push({ path: '', depth: 0, label: '（文档开头）', count: leading.length });
  }
  for (const it of sorted) {
    const sec = (it.section || '').trim();
    if (sec) add(sec, 1);
  }
  return nodes;
};

type RequirementItemLike = { key: string; section: string; title: string; content: string; order_index: number };

const RequirementManager: React.FC<RequirementManagerProps> = ({ selectedSession: currentSession, selectedModule: currentModule }) => {
  const [docs, setDocs] = useState<RequirementDoc[]>([]);
  const [docsLoading, setDocsLoading] = useState(false);
  const [selectedDocId, setSelectedDocId] = useState<number | null>(null);
  const [selectedDoc, setSelectedDoc] = useState<RequirementDoc | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [revisions, setRevisions] = useState<RequirementRevision[]>([]);

  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm();
  const [creating, setCreating] = useState(false);

  const [textOpen, setTextOpen] = useState(false);
  const [textForm] = Form.useForm();
  const [texting, setTexting] = useState(false);

  const [editOpen, setEditOpen] = useState(false);
  const [editContent, setEditContent] = useState('');
  const [savingContent, setSavingContent] = useState(false);

  const [syncing, setSyncing] = useState(false);
  const [syncAllRunning, setSyncAllRunning] = useState(false);

  const [titleEditing, setTitleEditing] = useState(false);
  const [titleDraft, setTitleDraft] = useState('');

  const [revisionsOpen, setRevisionsOpen] = useState(false);

  // 需求澄清对话：以选中文档为上下文的多轮 AI 对话
  const [chatOpen, setChatOpen] = useState(false);
  const [saveDocOpen, setSaveDocOpen] = useState(false);
  const [saveDocTitle, setSaveDocTitle] = useState('');
  const [saveDocContent, setSaveDocContent] = useState('');
  const [savingDoc, setSavingDoc] = useState(false);

  // ===== 数据加载 =====

  const loadDocs = useCallback(async () => {
    setDocsLoading(true);
    try {
      // 按当前会话/模块过滤：未选会话时显示全部（兼容无归属的历史数据）
      const filters: { session_id?: number; module_id?: number } = {};
      if (currentSession?.id) {
        filters.session_id = currentSession.id;
        if (currentModule && currentModule !== 0 && Number.isFinite(Number(currentModule))) {
          filters.module_id = Number(currentModule);
        }
      }
      const res = await requirementDocApi.list(Object.keys(filters).length > 0 ? filters : undefined);
      // 生成历史（source_type=generation）不属于需求解析页
      const all = res.code === 200 && res.data ? res.data : [];
      setDocs(all.filter((d) => d.source_type !== 'generation'));
    } catch (e) {
      console.error('加载需求文档失败', e);
    } finally {
      setDocsLoading(false);
    }
  }, [currentSession, currentModule]);

  const openDoc = useCallback(async (docId: number) => {
    setSelectedDocId(docId);
    setDetailLoading(true);
    try {
      const [docRes, revRes] = await Promise.all([
        requirementDocApi.get(docId),
        requirementDocApi.revisions(docId),
      ]);
      const doc = docRes.code === 200 && docRes.data ? docRes.data : null;
      setSelectedDoc(doc);
      setRevisions(revRes.code === 200 && revRes.data ? revRes.data : []);
    } catch (e) {
      console.error('加载文档详情失败', e);
      setSelectedDoc(null);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    loadDocs();
  }, [loadDocs]);

  // ===== 操作 =====

  const handleCreate = async () => {
    try {
      const values = await createForm.validateFields();
      setCreating(true);
      const res = await requirementDocApi.create({
        url: values.url,
        title: values.title || '',
        session_id: currentSession?.id ?? null,
        module_id: currentModule && currentModule !== 0 && Number.isFinite(Number(currentModule)) ? Number(currentModule) : null,
      });
      if (res.code === 200 && res.data) {
        message.success('需求文档已登记并完成首次同步解析');
        setCreateOpen(false);
        createForm.resetFields();
        await loadDocs();
        await openDoc(res.data.id);
      }
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } } };
      if (err.response?.data?.detail) message.error(err.response.data.detail);
    } finally {
      setCreating(false);
    }
  };

  const handleCreateText = async () => {
    try {
      const values = await textForm.validateFields();
      setTexting(true);
      const res = await requirementDocApi.createText({
        title: values.title || '',
        content: values.content,
        session_id: currentSession?.id ?? null,
        module_id: currentModule && currentModule !== 0 && Number.isFinite(Number(currentModule)) ? Number(currentModule) : null,
      });
      if (res.code === 200 && res.data) {
        message.success('需求文本已录入并解析');
        setTextOpen(false);
        textForm.resetFields();
        await loadDocs();
        await openDoc(res.data.id);
      }
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } } };
      if (err.response?.data?.detail) message.error(err.response.data.detail);
    } finally {
      setTexting(false);
    }
  };

  const handleSync = async (doc: RequirementDoc) => {
    setSyncing(true);
    try {
      const res = await requirementDocApi.sync(doc.id, true);
      if (res.code === 200 && res.data) {
        const diffs = res.data.differences || [];
        message.success(diffs.length === 0 ? '同步完成：内容无变化' : '同步完成：' + diffSummary(diffs));
        await loadDocs();
        await openDoc(doc.id);
      }
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || '同步失败');
    } finally {
      setSyncing(false);
    }
  };

  const handleSyncAll = async () => {
    if (docs.length === 0) return;
    setSyncAllRunning(true);
    let unchanged = 0;
    let changed = 0;
    let failed = 0;
    try {
      for (const doc of docs) {
        try {
          const res = await requirementDocApi.sync(doc.id, true);
          if (res.code === 200 && res.data) {
            if ((res.data.differences || []).length === 0) unchanged += 1;
            else changed += 1;
          } else {
            failed += 1;
          }
        } catch {
          failed += 1;
        }
      }
      message.success('同步完成：无变化 ' + unchanged + '、有差异 ' + changed + '、失败 ' + failed);
      await loadDocs();
      if (selectedDocId) await openDoc(selectedDocId);
    } finally {
      setSyncAllRunning(false);
    }
  };

  const handleSaveContent = async () => {
    if (!selectedDoc) return;
    setSavingContent(true);
    try {
      const res = await requirementDocApi.updateContent(selectedDoc.id, editContent);
      if (res.code === 200 && res.data) {
        message.success('正文已更新并重新解析：' + diffSummary(res.data.differences || []));
        setEditOpen(false);
        await loadDocs();
        await openDoc(selectedDoc.id);
      }
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || '保存失败');
    } finally {
      setSavingContent(false);
    }
  };

  const handleRename = async () => {
    if (!selectedDoc) return;
    const title = titleDraft.trim();
    if (!title) {
      message.warning('名称不能为空');
      return;
    }
    try {
      const res = await requirementDocApi.update(selectedDoc.id, { title });
      if (res.code === 200 && res.data) {
        setTitleEditing(false);
        message.success('名称已更新');
        await loadDocs();
        await openDoc(selectedDoc.id);
      }
    } catch (e) {
      message.error('更新失败');
    }
  };

  const handleDelete = async (doc: RequirementDoc) => {
    try {
      const res = await requirementDocApi.remove(doc.id);
      if (res.code === 200) {
        message.success('需求文档已删除');
        if (selectedDocId === doc.id) {
          setSelectedDocId(null);
          setSelectedDoc(null);
          setRevisions([]);
        }
        await loadDocs();
      }
    } catch (e) {
      message.error('删除失败');
    }
  };

  // ===== 需求澄清对话 =====

  // 对话上下文：源需求名称/来源/版本 + 正文全文（组件内由后端截断保护）
  const chatContext = useMemo(() => {
    if (!selectedDoc) return '';
    const lines = [
      '【源需求名称】' + (selectedDoc.title || '未命名需求'),
      '【来源】' + ((SOURCE_META[selectedDoc.source_type] || SOURCE_META.feishu).label) + ' · v' + selectedDoc.version,
      '',
      '【需求正文】',
      selectedDoc.content_snapshot || '（无正文）',
    ];
    return lines.join('\n');
  }, [selectedDoc]);

  // 打开保存预览：携带最后一条 AI 回复（整理出的需求清单）
  const handleOpenSaveDoc = (content: string) => {
    if (!content.trim()) {
      message.warning('没有可保存的内容');
      return;
    }
    setSaveDocTitle((selectedDoc?.title || '需求') + '（澄清清单）');
    setSaveDocContent(content);
    setSaveDocOpen(true);
  };

  // 保存为新需求文档（manual）；源文档不动
  const handleSaveDocConfirm = async () => {
    const content = saveDocContent.trim();
    if (!content) {
      message.warning('清单内容不能为空');
      return;
    }
    setSavingDoc(true);
    try {
      const res = await requirementDocApi.createText({
        title: saveDocTitle.trim() || '需求澄清清单',
        content,
        session_id: selectedDoc?.session_id ?? currentSession?.id ?? null,
        module_id: selectedDoc?.module_id ?? (currentModule && currentModule !== 0 && Number.isFinite(Number(currentModule)) ? Number(currentModule) : null),
      });
      if (res.code === 200 && res.data) {
        message.success('已保存为新需求文档，可在「生成测试用例」中引用');
        setSaveDocOpen(false);
        await loadDocs();
        await openDoc(res.data.id);
      } else {
        message.error(res.message || '保存失败');
      }
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || '保存失败');
    } finally {
      setSavingDoc(false);
    }
  };

  // ===== 派生数据 =====

  const outline = useMemo(
    () => (selectedDoc ? buildOutline(selectedDoc.structured || []) : []),
    [selectedDoc],
  );

  const isStale = useMemo(() => {
    if (!selectedDoc?.updated_at) return false;
    return Date.now() - new Date(selectedDoc.updated_at).getTime() > STALE_THRESHOLD_MS;
  }, [selectedDoc]);

  const latestRevision = revisions.length > 0 ? revisions[0] : null;
  const latestDiffs: RequirementDiffEntry[] = latestRevision?.differences || [];

  // ===== 渲染 =====

  const docColumns = [
    {
      title: '需求文档',
      dataIndex: 'title',
      render: (v: string, record: RequirementDoc) => (
        <div style={{ maxWidth: 240 }}>
          <div style={{ fontWeight: selectedDocId === record.id ? 600 : 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {v || '未命名需求'}
          </div>
          <Space size={4} style={{ marginTop: 2 }}>
            <Tag color={(SOURCE_META[record.source_type] || SOURCE_META.feishu).color} style={{ fontSize: 11, lineHeight: '16px' }}>
              {(SOURCE_META[record.source_type] || SOURCE_META.feishu).label}
            </Tag>
            <Text type="secondary" style={{ fontSize: 11 }}>v{record.version}</Text>
          </Space>
        </div>
      ),
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      width: 96,
      render: (v: string) => <Text type="secondary" style={{ fontSize: 11 }}>{formatTime(v)}</Text>,
    },
  ];

  const renderOutline = () => {
    if (outline.length === 0) return <Empty description="暂无解析结果，请先同步" />;
    return (
      <div style={{ padding: '4px 0' }}>
        {outline.map((n) => (
          <div key={n.path || '(root)'} style={{ paddingLeft: n.depth * 18, margin: '3px 0' }}>
            <Text style={{ fontSize: 13 }}>
              {n.depth > 0 && <Text type="secondary" style={{ fontSize: 11, marginRight: 6 }}>L{n.depth}</Text>}
              <Text strong={n.depth <= 1}>{n.label}</Text>
              {n.count > 0 && (
                <Text type="secondary" style={{ fontSize: 11, marginLeft: 6 }}>{n.count} 条</Text>
              )}
            </Text>
          </div>
        ))}
      </div>
    );
  };

  return (
    <div style={{ display: 'flex', gap: 12, height: '100%', minHeight: 0 }}>
      {/* 左侧：文档列表 */}
      <div style={{ width: 320, flexShrink: 0, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <div style={{ marginTop: 8, marginBottom: 10, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)} style={{ gridColumn: '1 / -1' }}>
            登记飞书文档
          </Button>
          <Button icon={<FileTextOutlined />} onClick={() => setTextOpen(true)}>手动录入</Button>
          <Tooltip title="同步列表内全部文档">
            <Button icon={<SyncOutlined spin={syncAllRunning} />} onClick={handleSyncAll} disabled={docs.length === 0 || syncAllRunning} style={{ width: '100%' }}>
              全部同步
            </Button>
          </Tooltip>
        </div>
        <Table
          size="small"
          rowKey="id"
          loading={docsLoading}
          dataSource={docs}
          columns={docColumns}
          pagination={false}
          rowClassName={(record) => (record.id === selectedDocId ? 'req-row-active' : '')}
          onRow={(record) => ({
            onClick: () => openDoc(record.id),
            style: { cursor: 'pointer' },
          })}
          style={{ background: '#fff', flex: 1, overflow: 'auto' }}
          locale={{ emptyText: <Empty description="暂无需求文档" /> }}
        />
      </div>

      {/* 右侧：文档详情（Markdown） */}
      <div style={{ flex: 1, minWidth: 0, background: '#fff', borderRadius: 8, padding: 16, overflow: 'auto' }}>
        {!selectedDoc ? (
          <Empty description="从左侧选择一个需求文档查看解析内容" style={{ marginTop: 80 }} />
        ) : (
          <Spin spinning={detailLoading}>
            <div style={{ marginBottom: 12 }}>
              {titleEditing ? (
                <Space>
                  <Input
                    value={titleDraft}
                    onChange={(e) => setTitleDraft(e.target.value)}
                    style={{ width: 360 }}
                    onPressEnter={handleRename}
                    autoFocus
                  />
                  <Button size="small" type="primary" onClick={handleRename}>保存</Button>
                  <Button size="small" onClick={() => setTitleEditing(false)}>取消</Button>
                </Space>
              ) : (
                <Space>
                  <Typography.Title level={4} style={{ margin: 0 }}>
                    {selectedDoc.title || '未命名需求'}
                  </Typography.Title>
                  <Button type="text" size="small" icon={<EditOutlined />} onClick={() => { setTitleDraft(selectedDoc.title); setTitleEditing(true); }} />
                </Space>
              )}
              <div style={{ marginTop: 6 }}>
                <Space size={8} wrap>
                  <Tag color={(SOURCE_META[selectedDoc.source_type] || SOURCE_META.feishu).color}>
                    {(SOURCE_META[selectedDoc.source_type] || SOURCE_META.feishu).label}
                  </Tag>
                  <Tag>v{selectedDoc.version}</Tag>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    更新于 {formatTime(selectedDoc.updated_at)}
                  </Text>
                  {isStale && <Tag color="orange">超过 24h 未同步</Tag>}
                  {selectedDoc.url && (
                    <a href={selectedDoc.url} target="_blank" rel="noreferrer" style={{ fontSize: 12 }}>
                      <LinkOutlined /> 打开飞书原文
                    </a>
                  )}
                </Space>
              </div>
              <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <Button icon={<SyncOutlined spin={syncing} />} onClick={() => handleSync(selectedDoc)} loading={syncing}>
                  同步飞书
                </Button>
                <Button icon={<EditOutlined />} onClick={() => { setEditContent(selectedDoc.content_snapshot || ''); setEditOpen(true); }}>
                  编辑正文
                </Button>
                <Button icon={<HistoryOutlined />} onClick={() => setRevisionsOpen(true)}>
                  变更历史
                </Button>
                <Button type="primary" ghost icon={<MessageOutlined />} onClick={() => setChatOpen(true)}>
                  需求澄清对话
                </Button>
                <div style={{ flex: 1 }} />
                <Popconfirm title="确认删除该需求文档？" onConfirm={() => handleDelete(selectedDoc)}>
                  <Button danger icon={<DeleteOutlined />}>删除</Button>
                </Popconfirm>
              </div>
            </div>

            {latestDiffs.length > 0 && (
              <div style={{ marginBottom: 10, fontSize: 12, color: '#888' }}>
                最近变更 v{latestRevision?.version}：{diffSummary(latestDiffs)}
              </div>
            )}

            <Collapse
              size="small"
              style={{ marginBottom: 12, background: '#fafafa' }}
              items={[{
                key: 'outline',
                label: '解析结构（严格按标题等级分类，共 ' + outline.length + ' 条）',
                children: renderOutline(),
              }]}
            />

            <div style={{ border: '1px solid #f0f0f0', borderRadius: 8, padding: '8px 20px' }}>
              <MarkdownView content={selectedDoc.content_snapshot || ''} />
            </div>
          </Spin>
        )}
      </div>

      {/* 登记飞书文档 */}
      <Modal
        title="登记飞书需求文档"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={handleCreate}
        confirmLoading={creating}
        okText="登记并同步解析"
      >
        <Form form={createForm} layout="vertical">
          <Form.Item name="url" label="飞书文档链接" rules={[{ required: true, message: '请输入飞书文档链接' }]}>
            <Input placeholder="https://xxx.feishu.cn/wiki/..." />
          </Form.Item>
          <Form.Item name="title" label="需求名称（留空自动取文档标题）">
            <Input placeholder="可选" />
          </Form.Item>
          <div style={{ fontSize: 12, color: 'var(--color-text-tertiary)' }}>
            将自动关联到当前会话与模块
          </div>
        </Form>
      </Modal>

      {/* 手动录入 */}
      <Modal
        title="手动录入需求文本"
        open={textOpen}
        onCancel={() => setTextOpen(false)}
        onOk={handleCreateText}
        confirmLoading={texting}
        okText="录入并解析"
      >
        <Form form={textForm} layout="vertical">
          <Form.Item name="title" label="需求名称">
            <Input placeholder="可选，留空取首行" />
          </Form.Item>
          <Form.Item name="content" label="需求内容" rules={[{ required: true, message: '请输入需求内容' }]}>
            <Input.TextArea rows={10} placeholder="支持 Markdown（# 标题按等级解析）或纯文本" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 编辑正文 */}
      <Modal
        title="编辑正文（保存后重新解析并记录差异）"
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={handleSaveContent}
        confirmLoading={savingContent}
        okText="保存并重新解析"
        width={860}
      >
        <Input.TextArea
          rows={22}
          value={editContent}
          onChange={(e) => setEditContent(e.target.value)}
          style={{ fontFamily: 'Consolas, monospace', fontSize: 12 }}
        />
      </Modal>

      {/* 需求澄清对话：以选中文档为上下文 */}
      <ChatAssistant
        open={chatOpen}
        onClose={() => setChatOpen(false)}
        title="需求澄清对话"
        mode="requirement_clarify"
        context={chatContext}
        scopeKey={selectedDoc ? 'req-clarify-' + selectedDoc.id : undefined}
        welcomeHint={
          selectedDoc
            ? '我会基于当前选中的需求「' + (selectedDoc.title || '未命名需求') + '」与你多轮澄清需求细节；确认清楚后点击「整理需求清单」，我会按模板输出完整需求清单，可保存为新需求文档（源需求不会改动）。'
            : '请先在左侧选择一个需求文档，再开始澄清对话。'
        }
        quickActions={[
          { label: '开始澄清提问', prompt: '请基于这份源需求，从输入字段和业务规则开始，逐组提出需要澄清的问题。' },
          { label: '整理需求清单', prompt: '请基于以上讨论和源需求，按需求清单模板整理一份完整的需求清单；未澄清到的信息如实写入「不确定事项」，不要自行补充。' },
        ]}
        onSaveLast={handleOpenSaveDoc}
        saveLabel="保存为新需求文档"
        width="66vw"
      />

      {/* 保存澄清清单为新需求文档 */}
      <Modal
        title="保存为新需求文档"
        open={saveDocOpen}
        onCancel={() => setSaveDocOpen(false)}
        onOk={handleSaveDocConfirm}
        confirmLoading={savingDoc}
        okText="保存"
        cancelText="取消"
        width={860}
      >
        <div style={{ marginBottom: 8, fontSize: 12, color: 'var(--color-text-tertiary)' }}>
          将保存为手动录入需求文档（不影响源需求）；保存后可在「生成测试用例」页签引用。
        </div>
        <div style={{ marginBottom: 8 }}>
          <Input
            value={saveDocTitle}
            onChange={(e) => setSaveDocTitle(e.target.value)}
            placeholder="需求名称"
          />
        </div>
        <Input.TextArea
          rows={22}
          value={saveDocContent}
          onChange={(e) => setSaveDocContent(e.target.value)}
          style={{ fontFamily: 'Consolas, monospace', fontSize: 12 }}
        />
      </Modal>

      {/* 变更历史 */}
      <Modal
        title="变更历史"
        open={revisionsOpen}
        onCancel={() => setRevisionsOpen(false)}
        footer={null}
        width={720}
      >
        {revisions.length === 0 ? (
          <Empty description="暂无变更记录" />
        ) : (
          <div style={{ maxHeight: 480, overflow: 'auto' }}>
            {revisions.map((rev) => (
              <div key={rev.id} style={{ borderBottom: '1px solid #f0f0f0', padding: '10px 0' }}>
                <Space>
                  <Tag color="blue">v{rev.version}</Tag>
                  <Tag>{rev.source === 'sync' ? '飞书同步' : rev.source === 'manual' ? '手动编辑' : rev.source}</Tag>
                  <Text type="secondary" style={{ fontSize: 12 }}>{formatTime(rev.created_at)}</Text>
                </Space>
                <div style={{ marginTop: 4, fontSize: 13 }}>{rev.summary}</div>
                {(rev.differences || []).length > 0 && (
                  <div style={{ marginTop: 6 }}>
                    {(rev.differences || []).slice(0, 8).map((d, i) => (
                      <Tag key={i} color={(DIFF_TYPE_META[d.type] || { color: 'default' }).color} style={{ fontSize: 11 }}>
                        {(DIFF_TYPE_META[d.type] || { label: d.type }).label}：{(d.section ? d.section + ' · ' : '') + (d.after || d.before || '').slice(0, 24)}
                      </Tag>
                    ))}
                    {(rev.differences || []).length > 8 && (
                      <Text type="secondary" style={{ fontSize: 11 }}>等 {(rev.differences || []).length} 条</Text>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Modal>
    </div>
  );
};

export default RequirementManager;

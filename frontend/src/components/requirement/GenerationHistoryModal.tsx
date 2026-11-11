import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, Empty, Input, List, Modal, Popconfirm, Spin, Tag, Typography, message } from 'antd';
import { DeleteOutlined, SearchOutlined } from '@ant-design/icons';
import { RequirementDoc } from '../../types';
import { requirementDocApi } from '../../services/api';

const { Text, Paragraph } = Typography;

interface GenerationHistoryModalProps {
  visible: boolean;
  moduleId?: number | string | null;
  onCancel: () => void;
  /** 点击条目回填需求输入框 */
  onSelectPrompt: (content: string) => void;
  /** 数据变化回调（父组件可刷新相关状态） */
  onDocsChange?: () => void;
}

const formatTime = (iso: string | null): string => {
  if (!iso) return '-';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '-' : d.toLocaleString('zh-CN', { hour12: false });
};

/** 最近生成的需求：生成时自动保存的需求输入日志（generation 来源），可回填复用、可清理。 */
const GenerationHistoryModal: React.FC<GenerationHistoryModalProps> = ({
  visible,
  moduleId,
  onCancel,
  onSelectPrompt,
  onDocsChange,
}) => {
  const [docs, setDocs] = useState<RequirementDoc[]>([]);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState('');

  const loadDocs = useCallback(async () => {
    setLoading(true);
    try {
      const filters: { source_type: string; module_id?: number } = { source_type: 'generation' };
      if (moduleId && moduleId !== 0 && moduleId !== 'all') {
        filters.module_id = Number(moduleId);
      }
      const res = await requirementDocApi.list(filters);
      setDocs(res.code === 200 && res.data ? res.data : []);
    } catch (e) {
      console.error('加载生成历史失败', e);
    } finally {
      setLoading(false);
    }
  }, [moduleId]);

  useEffect(() => {
    if (visible) loadDocs();
  }, [visible, loadDocs]);

  const filtered = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    if (!kw) return docs;
    return docs.filter((d) => d.content_snapshot.toLowerCase().includes(kw));
  }, [docs, keyword]);

  const handleSelect = (doc: RequirementDoc) => {
    onSelectPrompt(doc.content_snapshot);
    message.success('已填入需求描述');
    onCancel();
  };

  const handleDelete = async (doc: RequirementDoc) => {
    try {
      await requirementDocApi.remove(doc.id);
      message.success('已删除该条生成历史');
      await loadDocs();
      onDocsChange?.();
    } catch (e) {
      console.error('删除失败', e);
    }
  };

  return (
    <Modal
      title="最近生成的需求"
      open={visible}
      width={680}
      footer={null}
      onCancel={onCancel}
      destroyOnClose
    >
      <Input
        allowClear
        prefix={<SearchOutlined />}
        placeholder="搜索历史需求内容"
        value={keyword}
        onChange={(e) => setKeyword(e.target.value)}
        style={{ marginBottom: 12 }}
      />
      <div style={{ maxHeight: 480, overflowY: 'auto' }}>
        <Spin spinning={loading}>
          {filtered.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={keyword ? '未找到匹配内容' : '暂无生成历史：每次生成测试用例后，当次需求会自动保存在这里'}
            />
          ) : (
            <List
              dataSource={filtered}
              renderItem={(doc) => (
                <List.Item
                  style={{ cursor: 'pointer', padding: '10px 12px' }}
                  onClick={() => handleSelect(doc)}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                      <Text strong style={{ fontSize: 13 }}>{doc.title}</Text>
                      <Tag style={{ fontSize: 11 }}>{'模块 #' + (doc.module_id ?? '-')}</Tag>
                    </div>
                    <Paragraph
                      type="secondary"
                      style={{ marginBottom: 2, fontSize: 12, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
                    >
                      {doc.content_snapshot.slice(0, 80)}
                    </Paragraph>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      {formatTime(doc.updated_at || doc.created_at)}
                      {doc.session_id ? ' · 会话 #' + doc.session_id : ''}
                    </Text>
                  </div>
                  <Popconfirm
                    title="删除该条生成历史？"
                    description="仅清理这条记录，不影响已生成的用例"
                    onConfirm={(e) => { e?.stopPropagation(); handleDelete(doc); }}
                    onCancel={(e) => e?.stopPropagation()}
                    okText="删除"
                    cancelText="取消"
                  >
                    <Button
                      type="text"
                      size="small"
                      danger
                      icon={<DeleteOutlined />}
                      onClick={(e) => e.stopPropagation()}
                    />
                  </Popconfirm>
                </List.Item>
              )}
            />
          )}
        </Spin>
      </div>
      <div style={{ paddingTop: 10, borderTop: '1px solid #f0f0f0', fontSize: 12, color: '#999' }}>
        点击条目填入需求描述框；删除仅清理历史记录。需求文档与版本管理请到「需求管理」。
      </div>
    </Modal>
  );
};

export default GenerationHistoryModal;

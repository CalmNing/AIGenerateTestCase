import React, { useState, useEffect } from 'react';
import { List, Button, Empty, Tooltip, Typography, Modal } from 'antd';
import { DeleteOutlined, ExpandOutlined, CompressOutlined, ExclamationCircleOutlined } from '@ant-design/icons';
import { HistoryPrompt } from '../types';
import { historyPromptApi } from '../services/api';

const { Text } = Typography;

interface HistoryPromptSidebarProps {
  moduleId: number | string | null;
  sessionId: number | null;
  onSelectPrompt: (content: string) => void;
  onPromptsChange?: () => void;
  refreshKey?: number; // 用于触发刷新的key
}

const HistoryPromptSidebar: React.FC<HistoryPromptSidebarProps> = ({
  moduleId,
  sessionId,
  onSelectPrompt,
  onPromptsChange,
  refreshKey
}) => {
  const [prompts, setPrompts] = useState<HistoryPrompt[]>([]);
  const [loading, setLoading] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  // 加载历史提示词
  const loadPrompts = async () => {
    if (!moduleId || moduleId === 0 || moduleId === 'all') {
      setPrompts([]);
      return;
    }

    setLoading(true);
    try {
      const response = await historyPromptApi.getPrompts(Number(moduleId));
      if (response.code === 200 && response.data) {
        setPrompts(response.data);
      }
    } catch (error) {
      console.error('加载历史提示词失败:', error);
    } finally {
      setLoading(false);
    }
  };

  // 当模块变化时重新加载
  useEffect(() => {
    loadPrompts();
  }, [moduleId, refreshKey]); // 添加 refreshKey 依赖

  // 删除提示词
  const handleDelete = async (e: React.MouseEvent, prompt: HistoryPrompt) => {
    e.stopPropagation();
    
    // 显示确认对话框
    Modal.confirm({
      title: '确认删除',
      icon: <ExclamationCircleOutlined />,
      content: (
        <div>
          <p>确定要删除这条历史需求描述吗？</p>
          <div style={{ 
            marginTop: '8px', 
            padding: '8px', 
            background: '#f5f5f5', 
            borderRadius: '4px',
            fontSize: '12px',
            color: '#666'
          }}>
            {getPreview(prompt.content, 50)}
          </div>
        </div>
      ),
      okText: '确定',
      okType: 'danger',
      cancelText: '取消',
      centered: true,
      onOk: async () => {
        try {
          const response = await historyPromptApi.deletePrompt(prompt.id);
          if (response.code === 200) {
            setPrompts(prompts.filter(p => p.id !== prompt.id));
            onPromptsChange?.();
          }
        } catch (error) {
          console.error('删除历史提示词失败:', error);
        }
      }
    });
  };

  // 处理提示词点击
  const handleSelect = (prompt: HistoryPrompt) => {
    onSelectPrompt(prompt.content);
  };

  // 格式化日期
  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    return date.toLocaleDateString('zh-CN', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  // 截取内容预览
  const getPreview = (content: string, maxLength: number = 50) => {
    if (content.length <= maxLength) return content;
    return content.substring(0, maxLength) + '...';
  };

  if (collapsed) {
    return (
      <div style={{
        width: '40px',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        paddingTop: '16px',
        borderLeft: '1px solid #f0f0f0',
        background: '#fafafa'
      }}>
        <Tooltip title="展开历史提示词">
          <Button
            type="text"
            icon={<ExpandOutlined />}
            onClick={() => setCollapsed(false)}
            style={{ marginBottom: '8px' }}
          />
        </Tooltip>
        <Tooltip title={`历史提示词 (${prompts.length})`}>
          <div style={{
            writingMode: 'vertical-rl',
            textOrientation: 'mixed',
            fontSize: '12px',
            color: '#666',
            marginTop: '16px'
          }}>
            历史提示词
          </div>
        </Tooltip>
      </div>
    );
  }

  return (
    <div style={{
      width: '280px',
      borderLeft: '1px solid #f0f0f0',
      background: '#fafafa',
      display: 'flex',
      flexDirection: 'column',
      height: '100%'
    }}>
      {/* 头部 */}
      <div style={{
        padding: '12px 16px',
        borderBottom: '1px solid #f0f0f0',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        background: '#fff'
      }}>
        <Text strong>历史需求描述</Text>
        <Button
          type="text"
          size="small"
          icon={<CompressOutlined />}
          onClick={() => setCollapsed(true)}
        />
      </div>

      {/* 列表内容 */}
      <div style={{ flex: 1, overflow: 'auto', padding: '8px' }}>
        {!moduleId || moduleId === 0 || moduleId === 'all' ? (
          <Empty
            description="请先选择模块"
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            style={{ marginTop: '40px' }}
          />
        ) : prompts.length === 0 ? (
          <Empty
            description="暂无历史需求描述"
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            style={{ marginTop: '40px' }}
          />
        ) : (
          <List
            loading={loading}
            dataSource={prompts}
            renderItem={(prompt) => (
              <div
                style={{
                  padding: '8px 12px',
                  marginBottom: '8px',
                  background: '#fff',
                  borderRadius: '6px',
                  cursor: 'pointer',
                  border: '1px solid #e8e8e8',
                  transition: 'all 0.3s',
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: '8px'
                }}
                onClick={() => handleSelect(prompt)}
              >
                {/* 内容区域 */}
                <Tooltip title={prompt.content} placement="left">
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{
                      fontSize: '13px',
                      color: '#333',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      marginBottom: '4px'
                    }}>
                      {getPreview(prompt.content, 30)}
                    </div>
                    <div style={{
                      fontSize: '11px',
                      color: '#999'
                    }}>
                      {formatDate(prompt.created_at)}
                    </div>
                  </div>
                </Tooltip>
                
                {/* 删除按钮 */}
                <Tooltip title="删除">
                  <DeleteOutlined
                    onClick={(e) => handleDelete(e, prompt)}
                    style={{ 
                      color: '#ff4d4f', 
                      fontSize: '14px',
                      flexShrink: 0,
                      marginTop: '2px'
                    }}
                  />
                </Tooltip>
              </div>
            )}
          />
        )}
      </div>

      {/* 提示 */}
      <div style={{
        padding: '8px 16px',
        borderTop: '1px solid #f0f0f0',
        fontSize: '12px',
        color: '#999',
        background: '#fff'
      }}>
        点击填充需求描述
      </div>
    </div>
  );
};

export default HistoryPromptSidebar;

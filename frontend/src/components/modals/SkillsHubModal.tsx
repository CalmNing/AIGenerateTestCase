import React, { useState, useEffect } from 'react';
import { Modal, Switch, Typography, Empty, Spin, Tag, Collapse, Input, Button, message, Space, Popconfirm } from 'antd';
import { CheckOutlined, CloseOutlined, BookOutlined, DownloadOutlined, DeleteOutlined } from '@ant-design/icons';
import { skillsApi } from '../../services/api';
import { Skill } from '../../types';

const { Text, Paragraph } = Typography;

interface SkillsHubModalProps {
  visible: boolean;
  onCancel: () => void;
  onSave: (selectedNames: string[]) => void;
}

const SkillsHubModal: React.FC<SkillsHubModalProps> = ({ visible, onCancel, onSave }) => {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [installUrl, setInstallUrl] = useState('');
  const [installing, setInstalling] = useState(false);

  const loadSkills = () => {
    setLoading(true);
    skillsApi.list().then((res) => {
      if (res.code === 200 && res.data) {
        setSkills(res.data);
      }
    }).finally(() => setLoading(false));
  };

  useEffect(() => {
    if (visible) {
      loadSkills();

      const saved = localStorage.getItem('selectedSkills');
      if (saved) {
        try {
          setSelected(new Set(JSON.parse(saved)));
        } catch {}
      }
    }
  }, [visible]);

  const toggleSkill = (name: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const handleSave = () => {
    const names = Array.from(selected);
    localStorage.setItem('selectedSkills', JSON.stringify(names));
    onSave(names);
  };

  const handleDelete = async (name: string) => {
    try {
      const res = await skillsApi.delete(name);
      if (res.code === 200) {
        message.success(`技能 '${name}' 已删除`);
        // Also remove from selected set
        setSelected((prev) => {
          const next = new Set(prev);
          next.delete(name);
          return next;
        });
        loadSkills();
      } else {
        message.error(res.message || '删除失败');
      }
    } catch (err: any) {
      message.error(err?.response?.data?.detail || err?.message || '删除失败');
    }
  };

  const handleInstall = async () => {
    if (!installUrl.trim()) {
      message.warning('请输入 Skill Hub install URL');
      return;
    }
    setInstalling(true);
    try {
      const res = await skillsApi.install(installUrl.trim());
      if (res.code === 200) {
        message.success(res.message || '技能安装成功');
        setInstallUrl('');
        loadSkills();
      } else {
        message.error(res.message || '安装失败');
      }
    } catch (err: any) {
      message.error(err?.response?.data?.detail || err?.message || '安装失败，请检查 URL 是否正确');
    } finally {
      setInstalling(false);
    }
  };

  return (
    <Modal
      title={
        <span>
          <BookOutlined style={{ marginRight: 8 }} />
          Skills Hub
        </span>
      }
      open={visible}
      onCancel={onCancel}
      okText="保存"
      cancelText="取消"
      width={680}
      onOk={handleSave}
      bodyStyle={{ maxHeight: '65vh', overflowY: 'auto' }}
    >
      {/* Install section */}
      <div
        style={{
          background: '#fafafa',
          border: '1px solid #f0f0f0',
          borderRadius: 8,
          padding: '12px 16px',
          marginBottom: 16,
        }}
      >
        <Text strong style={{ fontSize: 13, display: 'block', marginBottom: 8 }}>
          <DownloadOutlined style={{ marginRight: 6 }} />
          从 Skill Hub 安装
        </Text>
        <Space.Compact style={{ width: '100%' }}>
          <Input
            placeholder="粘贴 Skill Hub install URL..."
            value={installUrl}
            onChange={(e) => setInstallUrl(e.target.value)}
            onPressEnter={handleInstall}
            style={{ fontSize: 12 }}
          />
          <Button
            type="primary"
            loading={installing}
            onClick={handleInstall}
            icon={<DownloadOutlined />}
          >
            安装
          </Button>
        </Space.Compact>
      </div>

      {/* Skill list */}
      <div style={{ marginBottom: 16, fontSize: 13, color: '#666' }}>
        选择要在测试用例生成时启用的测试方法论技能。选中技能的指导内容将注入到 AI 提示词中。
        已选 <Tag color="blue">{selected.size}</Tag> 个技能
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Spin tip="加载技能列表..." />
        </div>
      ) : skills.length === 0 ? (
        <Empty description="暂无可用 Skills，请从上方安装" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        skills.map((skill) => {
          const isSelected = selected.has(skill.name);
          return (
            <div
              key={skill.name}
              style={{
                border: `1px solid ${isSelected ? '#1677ff' : '#f0f0f0'}`,
                borderRadius: 8,
                padding: '12px 16px',
                marginBottom: 12,
                opacity: isSelected ? 1 : 0.55,
                transition: 'opacity 0.2s, border-color 0.2s',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
                <div style={{ flex: 1, marginRight: 16 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                    <Text strong style={{ fontSize: 14 }}>{skill.display_name}</Text>
                    <Text code style={{ fontSize: 11 }}>{skill.name}</Text>
                  </div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {skill.description}
                  </Text>
                </div>
                <Space>
                  <Popconfirm
                    title="确认删除"
                    description={`确定要删除技能 '${skill.name}' 吗？`}
                    onConfirm={() => handleDelete(skill.name)}
                    okText="删除"
                    cancelText="取消"
                    okButtonProps={{ danger: true }}
                  >
                    <Button
                      type="text"
                      size="small"
                      danger
                      icon={<DeleteOutlined />}
                      style={{ fontSize: 14 }}
                    />
                  </Popconfirm>
                  <Switch
                    checked={isSelected}
                    onChange={() => toggleSkill(skill.name)}
                    checkedChildren={<CheckOutlined />}
                    unCheckedChildren={<CloseOutlined />}
                  />
                </Space>
              </div>

              <Collapse
                ghost
                size="small"
                items={[{
                  key: 'body',
                  label: <Text type="secondary" style={{ fontSize: 12 }}>预览方法论文档</Text>,
                  children: (
                    <Paragraph
                      style={{
                        fontSize: 12,
                        whiteSpace: 'pre-wrap',
                        maxHeight: 200,
                        overflowY: 'auto',
                        background: '#fafafa',
                        padding: 12,
                        borderRadius: 4,
                        margin: 0,
                      }}
                    >
                      {skill.body}
                    </Paragraph>
                  ),
                }]}
              />
            </div>
          );
        })
      )}
    </Modal>
  );
};

export default SkillsHubModal;

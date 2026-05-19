import React, { useState, useEffect } from 'react';
import { Modal, Switch, Typography, Empty } from 'antd';
import { CheckOutlined, CloseOutlined } from '@ant-design/icons';

const { Text } = Typography;

interface SkillEntry {
  key: string;
  label: string;
  description: string;
  enabled: boolean;
}

const DEFAULT_SKILLS: SkillEntry[] = [
  { key: 'lanhu_get_pages', label: '获取页面列表', description: '获取蓝湖 Axure 原型页面列表（含页面名称和总数）', enabled: true },
  { key: 'lanhu_get_ai_analyze_page_result', label: '分析页面内容', description: '分析原型页面内容并生成详细需求文档', enabled: true },
  { key: 'lanhu_get_designs', label: '获取设计图', description: '获取蓝湖 UI 设计图列表', enabled: true },
  { key: 'lanhu_get_ai_analyze_design_result', label: '分析设计图', description: '分析 UI 设计图内容', enabled: true },
  { key: 'lanhu_get_design_slices', label: '设计切图', description: '获取设计图切片/图标资源下载信息', enabled: true },
  { key: 'lanhu_get_members', label: '项目成员', description: '查看项目协作者列表', enabled: false },
  { key: 'lanhu_say_list', label: '项目留言', description: '查看项目留言板消息列表', enabled: false },
  { key: 'lanhu_resolve_invite_link', label: '解析邀请链接', description: '解析蓝湖邀请链接为可用项目 URL', enabled: true },
];

interface SkillsConfigModalProps {
  visible: boolean;
  onCancel: () => void;
  onSave: (values: any) => void;
}

const SkillsConfigModal: React.FC<SkillsConfigModalProps> = ({ visible, onCancel, onSave }) => {
  const [skills, setSkills] = useState<SkillEntry[]>(DEFAULT_SKILLS);

  useEffect(() => {
    if (visible) {
      const saved = localStorage.getItem('skillsConfig');
      if (saved) {
        const savedMap = JSON.parse(saved);
        setSkills(DEFAULT_SKILLS.map(s => ({
          ...s,
          enabled: savedMap[s.key] !== undefined ? savedMap[s.key] : s.enabled,
        })));
      } else {
        setSkills(DEFAULT_SKILLS);
      }
    }
  }, [visible]);

  const toggleSkill = (key: string) => {
    setSkills(skills.map(s => s.key === key ? { ...s, enabled: !s.enabled } : s));
  };

  const handleSave = () => {
    const config = Object.fromEntries(skills.map(s => [s.key, s.enabled]));
    localStorage.setItem('skillsConfig', JSON.stringify(config));
    onSave(config);
  };

  return (
    <Modal
      title="Skills 配置"
      open={visible}
      onCancel={onCancel}
      okText="保存"
      cancelText="取消"
      width={560}
      onOk={handleSave}
    >
      <div style={{ marginBottom: 16, fontSize: 13, color: '#666' }}>
        启用或禁用蓝湖 MCP 工具（关闭后 agent 将无法调用该工具）
      </div>

      {skills.length === 0 ? (
        <Empty description="暂无可用 Skills" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        skills.map((skill) => (
          <div
            key={skill.key}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '10px 0',
              borderBottom: '1px solid #f0f0f0',
              opacity: skill.enabled ? 1 : 0.45,
              transition: 'opacity 0.2s',
            }}
          >
            <div style={{ flex: 1, marginRight: 16 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Text strong style={{ fontSize: 13 }}>{skill.label}</Text>
                <Text code style={{ fontSize: 11, color: '#999' }}>{skill.key}</Text>
              </div>
              <div style={{ fontSize: 12, color: '#888', marginTop: 2 }}>{skill.description}</div>
            </div>
            <Switch
              checked={skill.enabled}
              onChange={() => toggleSkill(skill.key)}
              checkedChildren={<CheckOutlined />}
              unCheckedChildren={<CloseOutlined />}
            />
          </div>
        ))
      )}
    </Modal>
  );
};

export default SkillsConfigModal;

import React, { useState, useRef, useEffect, useCallback } from 'react';
import {
  Drawer,
  Input,
  Button,
  Space,
  Typography,
  Switch,
  Tag,
  Tooltip,
  Spin,
} from 'antd';
import {
  SendOutlined,
  SettingOutlined,
  RobotOutlined,
} from '@ant-design/icons';
import { chatApi, skillsApi } from '../../services/api';
import ChatMessage from './ChatMessage';
import './ChatAssistant.css';

const { Text } = Typography;
const { TextArea } = Input;

interface Message {
  role: 'user' | 'assistant';
  content: string;
  timestamp?: number;
}

interface ChatAssistantProps {
  open: boolean;
  onClose: () => void;
}

const ChatAssistant: React.FC<ChatAssistantProps> = ({ open, onClose }) => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [loading, setLoading] = useState(false);
  const [selectedSkills, setSelectedSkills] = useState<string[]>([]);
  const [availableSkills, setAvailableSkills] = useState<any[]>([]);
  const [showSettings, setShowSettings] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<any>(null);
  // 会话标识：让后端复用同一条 LangGraph thread（服务端记忆），仅当有工具时生效
  const [clientSessionId, setClientSessionId] = useState<string>(
    () => `chat-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
  );

  // 加载可用 Skills
  useEffect(() => {
    if (open) {
      loadSkills();
      // 恢复保存的 Skills 选择
      const saved = localStorage.getItem('chatSelectedSkills');
      if (saved) {
        try {
          setSelectedSkills(JSON.parse(saved));
        } catch {
          // ignore
        }
      }
    }
  }, [open]);

  const loadSkills = async () => {
    try {
      const res = await skillsApi.list();
      if ((res as any)?.data?.code === 0 && (res as any)?.data?.data) {
        setAvailableSkills((res as any).data.data);
      } else if (Array.isArray((res as any)?.data)) {
        setAvailableSkills((res as any).data);
      }
    } catch {
      // ignore
    }
  };

  // 自动滚动到底部
  useEffect(() => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages]);

  // 聚焦输入框
  useEffect(() => {
    if (open && inputRef.current) {
      setTimeout(() => inputRef.current?.focus(), 300);
    }
  }, [open]);

  const handleSend = useCallback(async () => {
    const text = inputValue.trim();
    if (!text || loading) return;

    const userMessage: Message = {
      role: 'user',
      content: text,
      timestamp: Date.now(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInputValue('');
    setLoading(true);

    try {
      // 构建对话历史（只发送最近 20 条）
      const history = [...messages, userMessage].slice(-20).map((m) => ({
        role: m.role,
        content: m.content,
      }));

      const res = await chatApi.send(history, selectedSkills, clientSessionId);
      // axios 拦截器已解包 response.data，res 就是后端返回的 {code, message, data}
      const resAny = res as any;

      if ((resAny?.code === 0 || resAny?.code === 200) && resAny?.data?.reply) {
        const assistantMessage: Message = {
          role: 'assistant',
          content: resAny.data.reply,
          timestamp: Date.now(),
        };
        setMessages((prev) => [...prev, assistantMessage]);
      } else {
        const errorMessage: Message = {
          role: 'assistant',
          content: '抱歉，AI 回复失败，请稍后重试。',
          timestamp: Date.now(),
        };
        setMessages((prev) => [...prev, errorMessage]);
      }
    } catch (error: any) {
      const errorMessage: Message = {
        role: 'assistant',
        content: `请求失败: ${error.message || '未知错误'}`,
        timestamp: Date.now(),
      };
      setMessages((prev) => [...prev, errorMessage]);
    } finally {
      setLoading(false);
    }
  }, [inputValue, loading, messages, selectedSkills]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const _handleClear = () => {
    setMessages([]);
    setInputValue('');
    // P1 审查修复：清空对话必须同时换 clientSessionId，
    // 否则后端仍保留旧 thread 轨迹，"只发最新一条"的增量逻辑会把新对话
    // 接在被清掉的旧上下文后面（前后端记忆错位）。
    setClientSessionId(
      `chat-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
    );
  };

  const handleSkillToggle = (skillName: string, checked: boolean) => {
    setSelectedSkills((prev) => {
      const next = checked
        ? [...prev, skillName]
        : prev.filter((s) => s !== skillName);
      localStorage.setItem('chatSelectedSkills', JSON.stringify(next));
      return next;
    });
  };

  const footer = (
    <div className="chat-footer">
      <div className="chat-footer-settings">
        <Tooltip title="技能配置">
          <Button
            type="text"
            icon={<SettingOutlined />}
            onClick={() => setShowSettings(!showSettings)}
          />
        </Tooltip>
        {selectedSkills.length > 0 && (
          <Tag color="blue">{selectedSkills.length} 个技能已启用</Tag>
        )}
      </div>
      {showSettings && (
        <div className="chat-skills-panel">
          <Text type="secondary" style={{ fontSize: 12 }}>启用的技能方法论：</Text>
          <div className="chat-skills-list">
            {availableSkills.map((skill) => (
              <div key={skill.name} className="chat-skill-item">
                <Switch
                  size="small"
                  checked={selectedSkills.includes(skill.name)}
                  onChange={(checked) => handleSkillToggle(skill.name, checked)}
                />
                <Text style={{ fontSize: 12, marginLeft: 8 }}>{skill.display_name || skill.name}</Text>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="chat-input-area">
        <TextArea
          ref={inputRef}
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
          autoSize={{ minRows: 1, maxRows: 4 }}
          disabled={loading}
        />
        <Button
          type="primary"
          icon={<SendOutlined />}
          onClick={handleSend}
          loading={loading}
          disabled={!inputValue.trim()}
        />
      </div>
    </div>
  );

  return (
    <Drawer
      title={
        <Space>
          <RobotOutlined />
          <span>AI 助手</span>
        </Space>
      }
      placement="right"
      width={480}
      open={open}
      onClose={onClose}
      footer={footer}
      className="chat-drawer"
      styles={{ body: { padding: 0, display: 'flex', flexDirection: 'column' } }}
    >
      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="chat-welcome">
            <RobotOutlined style={{ fontSize: 48, color: '#1890ff', marginBottom: 16 }} />
            <Text type="secondary" style={{ fontSize: 16 }}>
              你好！我是 AI 助手，可以帮你回答问题、分析文档、讨论技术方案。
            </Text>
            <Text type="secondary" style={{ fontSize: 12, marginTop: 8 }}>
              {selectedSkills.length > 0
                ? `已启用 ${selectedSkills.length} 个技能方法论`
                : '点击右下角 ⚙️ 配置技能方法论'}
            </Text>
          </div>
        )}
        {messages.map((msg, index) => (
          <ChatMessage key={index} message={msg} />
        ))}
        {loading && (
          <div className="chat-loading">
            <Spin size="small" />
            <Text type="secondary" style={{ marginLeft: 8 }}>AI 正在思考...</Text>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>
    </Drawer>
  );
};

export default ChatAssistant;

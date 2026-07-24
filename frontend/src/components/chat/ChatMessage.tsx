import React from 'react';
import { Typography, Space } from 'antd';
import { UserOutlined, RobotOutlined } from '@ant-design/icons';
import './ChatAssistant.css';

const { Text } = Typography;

interface Message {
  role: 'user' | 'assistant';
  content: string;
  timestamp?: number;
}

interface ChatMessageProps {
  message: Message;
}

// 简单的 Markdown 渲染（支持代码块、粗体、列表）
const renderContent = (content: string) => {
  // 处理代码块
  const parts = content.split(/(```[\s\S]*?```)/g);

  return parts.map((part, index) => {
    if (part.startsWith('```') && part.endsWith('```')) {
      const lines = part.slice(3, -3);
      const firstNewline = lines.indexOf('\n');
      const language = firstNewline > 0 ? lines.slice(0, firstNewline).trim() : '';
      const code = firstNewline > 0 ? lines.slice(firstNewline + 1) : lines;
      return (
        <pre key={index} className="chat-code-block">
          {language && <div className="chat-code-lang">{language}</div>}
          <code>{code}</code>
        </pre>
      );
    }

    // 处理行内内容
    return part.split('\n').map((line, lineIndex) => {
      // 处理列表
      if (line.match(/^\s*[-*]\s/)) {
        return (
          <div key={`${index}-${lineIndex}`} className="chat-list-item">
            {line.replace(/^\s*[-*]\s/, '• ')}
          </div>
        );
      }
      if (line.match(/^\s*\d+\.\s/)) {
        return (
          <div key={`${index}-${lineIndex}`} className="chat-list-item">
            {line}
          </div>
        );
      }
      // 处理粗体
      const boldParts = line.split(/(\*\*.*?\*\*)/g);
      return (
        <div key={`${index}-${lineIndex}`}>
          {boldParts.map((bp, bpIndex) => {
            if (bp.startsWith('**') && bp.endsWith('**')) {
              return <strong key={bpIndex}>{bp.slice(2, -2)}</strong>;
            }
            return <span key={bpIndex}>{bp}</span>;
          })}
        </div>
      );
    });
  });
};

const ChatMessage: React.FC<ChatMessageProps> = ({ message }) => {
  const isUser = message.role === 'user';

  return (
    <div className={`chat-message ${isUser ? 'chat-message-user' : 'chat-message-assistant'}`}>
      <div className="chat-message-avatar">
        {isUser ? (
          <UserOutlined style={{ color: '#fff' }} />
        ) : (
          <RobotOutlined style={{ color: '#1890ff' }} />
        )}
      </div>
      <div className="chat-message-content">
        <div className={`chat-bubble ${isUser ? 'chat-bubble-user' : 'chat-bubble-assistant'}`}>
          {isUser ? (
            <Text style={{ color: '#fff', whiteSpace: 'pre-wrap' }}>{message.content}</Text>
          ) : (
            <div className="chat-markdown">{renderContent(message.content)}</div>
          )}
        </div>
        {message.timestamp && (
          <Text type="secondary" className="chat-time">
            {new Date(message.timestamp).toLocaleTimeString()}
          </Text>
        )}
      </div>
    </div>
  );
};

export default ChatMessage;

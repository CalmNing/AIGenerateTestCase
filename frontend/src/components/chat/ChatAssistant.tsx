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
  message as antMessage,
} from 'antd';
import {SendOutlined,
  SettingOutlined,
  RobotOutlined,
  CopyOutlined,
  SaveOutlined,
  PauseOutlined,
  ImportOutlined,
  HistoryOutlined,
  PlusOutlined,
  DeleteOutlined,
  MessageOutlined,
  CloseOutlined,
  PictureOutlined,
} from '@ant-design/icons';
import {
  chatApiStream,
  skillsApi,
  ChatStreamControl,
  chatHistoryApi,
  ChatConversationSummary,
} from '../../services/api';
import { parseSuggestedCases, ParsedSuggestedCase } from '../../utils/suggestedCases';
import { copyTextToClipboard } from '../../utils/clipboard';
import ChatMessage, { Message } from './ChatMessage';
import './ChatAssistant.css';

const { Text } = Typography;
const { TextArea } = Input;

export interface ChatQuickAction {
  label: string;
  /** 点击后作为用户消息发送的预设内容 */
  prompt: string;
  /** true 时点击仅把 prompt 填入输入框（可继续编辑），不直接发送 */
  fillOnly?: boolean;
}

interface ChatAssistantProps {
  open: boolean;
  onClose: () => void;
  /** 抽屉标题，默认"AI 助手" */
  title?: string;
  /** 对话模式：general（默认）/ requirement_clarify / testcase_optimize */
  mode?: 'general' | 'requirement_clarify' | 'testcase_optimize';
  /** 业务上下文（需求正文、用例清单摘要等），随每轮请求注入 */
  context?: string;
  /** 上下文身份标识：变化时清空对话并轮换服务端 thread，防止跨上下文串记忆 */
  scopeKey?: string;
  /** 欢迎语，默认通用文案 */
  welcomeHint?: string;
  /** 输入区上方的快捷操作（点击即发送预设消息） */
  quickActions?: ChatQuickAction[];
  /** 传入后显示"保存"按钮，携带最后一条 AI 回复文本回调 */
  onSaveLast?: (content: string) => void;
  /** 保存按钮文案，默认"保存" */
  saveLabel?: string;
  /** 抽屉宽度（像素或百分比字符串），默认 480 */
  width?: number | string;
  /** 传入后：AI 回复含结构化用例建议时显示"引入用例"按钮，解析结果回调 */
  onImportCases?: (cases: ParsedSuggestedCase[], sourceText: string) => void;
  /** 引入按钮文案，默认"引入用例" */
  importLabel?: string;
  /** 是否启用会话存储（存储会话/选择上次对话继续），默认启用 */
  persistSessions?: boolean;
  /** 附加请求体字段（如 testcase_session_id），随每轮对话请求发送 */
  extraBody?: Record<string, unknown>;
  /** 工具修改/删除用例后触发（用于刷新用例列表）；同一轮多次触发会防抖合并 */
  onCaseChanged?: () => void;
}

const newThreadId = (scope?: string) =>
  'chat-' + (scope || 'global') + '-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);

// 统一走 copyTextToClipboard：http/局域网环境下 navigator.clipboard 不可用，
// 由它降级到 execCommand 兜底。
const copyText = async (text: string) => copyTextToClipboard(text);

const ChatAssistant: React.FC<ChatAssistantProps> = ({
  open,
  onClose,
  title = 'AI 助手',
  mode = 'general',
  context,
  scopeKey,
  welcomeHint,
  quickActions,
  onSaveLast,
  saveLabel = '保存',
  width = 480,
  onImportCases,
  importLabel = '引入用例',
  persistSessions = true,
  extraBody,
  onCaseChanged,
}) => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [loading, setLoading] = useState(false);
  const [selectedSkills, setSelectedSkills] = useState<string[]>([]);
  const [availableSkills, setAvailableSkills] = useState<any[]>([]);
  const [showSettings, setShowSettings] = useState(false);
  // 思考过程开关（DeepSeek thinking），默认开启，记忆用户偏好
  // 待发送的截图附件（data URL，单张 ≤2MB，最多 4 张）
  const [pendingImages, setPendingImages] = useState<string[]>([]);
  const [showThinking, setShowThinking] = useState<boolean>(() => {
    const saved = localStorage.getItem('chatShowThinking');
    return saved === null ? true : saved === 'true';
  });
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<any>(null);
  const streamControlRef = useRef<ChatStreamControl | null>(null);
  // 会话标识：让后端复用同一条 LangGraph thread（服务端记忆），仅当有工具时生效。
  // 业务场景下 scopeKey 进入 thread id，切换文档/会话时轮换，防跨上下文串记忆。
  const [clientSessionId, setClientSessionId] = useState<string>(() => newThreadId(scopeKey));
  // 会话持久化：当前绑定的已存会话 + 会话列表面板
  const [activeConversationId, setActiveConversationId] = useState<number | null>(null);
  const [conversations, setConversations] = useState<ChatConversationSummary[]>([]);
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const [savingConversation, setSavingConversation] = useState(false);

  // scopeKey 变化：清空对话、解绑会话并轮换 thread
  useEffect(() => {
    setMessages([]);
    setInputValue('');
    setActiveConversationId(null);
    setClientSessionId(newThreadId(scopeKey));
  }, [scopeKey]);

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

  // 思考开关偏好持久化
  useEffect(() => {
    localStorage.setItem('chatShowThinking', String(showThinking));
  }, [showThinking]);

  // 会话自动保存：流结束后（存在未流式消息且没有 streaming 中）整体快照 upsert
  const persistSnapshot = useCallback(
    async (msgs: Message[]) => {
      if (!persistSessions) return;
      if (msgs.length === 0 || msgs.some((m) => m.streaming)) return;
      setSavingConversation(true);
      try {
        const payload = msgs.map((m) => ({
          role: m.role,
          content: m.content,
          reasoning: m.reasoning || '',
          toolEvents: m.toolEvents || [],
          ...(m.images && m.images.length > 0 ? { images: m.images } : {}),
        }));
        if (activeConversationId) {
          const res = await chatHistoryApi.update(activeConversationId, { messages: payload, thread_id: clientSessionId });
          if (res.code === 200 && res.data) {
            setConversations((prev) => {
              const idx = prev.findIndex((c) => c.id === activeConversationId);
              if (idx < 0) return prev;
              const next = [...prev];
              next[idx] = { ...next[idx], ...(res.data as ChatConversationSummary) };
              return next;
            });
          }
        } else {
          const res = await chatHistoryApi.save({
            mode,
            scope_key: scopeKey || '',
            thread_id: clientSessionId,
            messages: payload,
          });
          if (res.code === 200 && res.data) {
            setActiveConversationId(res.data.id);
            setConversations((prev) => [{ ...(res.data as ChatConversationSummary) }, ...prev]);
          }
        }
      } catch {
        // 会话保存失败不影响对话
      } finally {
        setSavingConversation(false);
      }
    },
    [persistSessions, activeConversationId, clientSessionId, mode, scopeKey]
  );

  // 检测流结束（此前有 streaming，现在没有）触发保存
  const prevStreamingRef = useRef(false);
  useEffect(() => {
    const anyStreaming = messages.some((m) => m.streaming);
    if (prevStreamingRef.current && !anyStreaming && messages.length > 0) {
      void persistSnapshot(messages);
    }
    prevStreamingRef.current = anyStreaming;
  }, [messages, persistSnapshot]);

  // 加载会话列表（打开面板时）
  const loadConversations = useCallback(async () => {
    if (!persistSessions) return;
    try {
      const res = await chatHistoryApi.list({
        mode,
        scope_key: scopeKey || undefined,
        limit: 50,
      });
      if (res.code === 200 && res.data) {
        setConversations(res.data);
      }
    } catch {
      // ignore
    }
  }, [persistSessions, mode, scopeKey]);

  useEffect(() => {
    if (sessionsOpen) void loadConversations();
  }, [sessionsOpen, loadConversations]);

  // 选择历史会话：载入消息 + 绑定 id/thread
  const handleSelectConversation = useCallback(async (id: number) => {
    try {
      const res = await chatHistoryApi.get(id);
      if (res.code !== 200 || !res.data) return;
      const detail = res.data;
      const restored: Message[] = (detail.messages || []).map((m, idx) => ({
        role: m.role === 'assistant' ? 'assistant' : 'user',
        content: m.content || '',
        reasoning: m.reasoning || '',
        toolEvents: m.toolEvents || [],
        ...(Array.isArray((m as any).images) && (m as any).images.length > 0 ? { images: (m as any).images } : {}),
        timestamp: detail.updated_at ? new Date(detail.updated_at).getTime() + idx : Date.now() + idx,
      }));
      setMessages(restored);
      setActiveConversationId(id);
      if (detail.thread_id) {
        setClientSessionId(detail.thread_id);
      } else {
        setClientSessionId(newThreadId(scopeKey));
      }
      setSessionsOpen(false);
    } catch {
      antMessage.error('载入会话失败');
    }
  }, [scopeKey]);

  // 新会话：清空消息 + 解绑会话 + 轮换 thread
  const handleNewConversation = useCallback(() => {
    streamControlRef.current?.abort();
    setMessages([]);
    setInputValue('');
    setActiveConversationId(null);
    setClientSessionId(newThreadId(scopeKey));
    setSessionsOpen(false);
  }, [scopeKey]);

  // 删除会话
  const handleDeleteConversation = useCallback(async (id: number) => {
    try {
      const res = await chatHistoryApi.remove(id);
      if (res.code === 200) {
        setConversations((prev) => prev.filter((c) => c.id !== id));
        if (activeConversationId === id) {
          setActiveConversationId(null);
        }
      }
    } catch {
      antMessage.error('删除会话失败');
    }
  }, [activeConversationId]);

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

  // 组件卸载/关闭抽屉时中断进行中的流
  useEffect(() => {
    return () => {
      streamControlRef.current?.abort();
    };
  }, []);

  // 更新流式消息（按 timestamp 定位，流式期间该消息内容持续追加）
  const patchStreaming = useCallback((timestamp: number, patch: (m: Message) => Partial<Message>) => {
    setMessages((prev) => prev.map((m) => (m.timestamp === timestamp ? { ...m, ...patch(m) } : m)));
  }, []);

  // 用例被 AI 工具修改/删除后的刷新回调：800ms 防抖合并一轮回复中的多次工具调用
  const caseChangedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const notifyCaseChanged = useCallback(() => {
    if (!onCaseChanged) return;
    if (caseChangedTimerRef.current) clearTimeout(caseChangedTimerRef.current);
    caseChangedTimerRef.current = setTimeout(() => {
      caseChangedTimerRef.current = null;
      onCaseChanged();
    }, 800);
  }, [onCaseChanged]);
  useEffect(() => () => {
    if (caseChangedTimerRef.current) clearTimeout(caseChangedTimerRef.current);
  }, []);

  // 共享流式执行：创建占位 assistant 消息并按 SSE 事件增量填充
  const runStream = useCallback(
    (history: Array<{ role: string; content: string; images?: string[] }>, assistantTimestamp: number) => {
      const finalize = (patch?: Partial<Message>) => {
        patchStreaming(assistantTimestamp, (m) => ({
          streaming: false,
          // 空回复兜底：错误时显示提示，中断时显示已停止
          content: m.content || patch?.content || (patch?.reasoning === undefined ? '（已停止）' : m.content),
          ...patch,
        }));
        setLoading(false);
        streamControlRef.current = null;
      };

      const control = chatApiStream(
        {
          messages: history,
          selected_skills: selectedSkills.length > 0 ? selectedSkills : undefined,
          client_session_id: clientSessionId,
          context,
          mode,
          show_thinking: showThinking,
          ...(extraBody as Record<string, unknown> | undefined),
        },
        {
          onReasoning: (delta) => patchStreaming(assistantTimestamp, (m) => ({ reasoning: (m.reasoning || '') + delta })),
          onContent: (delta) => patchStreaming(assistantTimestamp, (m) => ({ content: m.content + delta })),
          onTool: (toolName) => {
            patchStreaming(assistantTimestamp, (m) => ({ toolEvents: [...(m.toolEvents || []), toolName] }));
            if (onCaseChanged && /update_testcase|delete_testcases/.test(toolName)) notifyCaseChanged();
          },
          onError: (errorMsg) =>
            patchStreaming(assistantTimestamp, (m) => ({
              content: m.content
                ? m.content.includes('截断') && errorMsg.includes('截断')
                  ? m.content + '\n\n> ⚠️ ' + errorMsg
                  : m.content + '\n\n---\n⚠️ 生成中断：' + errorMsg
                : '请求失败: ' + errorMsg,
            })),
          onDone: () => finalize(),
        },
      );
      streamControlRef.current = control;
    },
    [patchStreaming, selectedSkills, clientSessionId, context, mode, showThinking, extraBody, onCaseChanged, notifyCaseChanged]
  );

  const handleSend = useCallback(async () => {
    const text = inputValue.trim();
    if ((!text && pendingImages.length === 0) || loading) return;

    const userMessage: Message = {
      role: 'user',
      content: text || '（见截图）',
      timestamp: Date.now(),
      ...(pendingImages.length > 0 ? { images: [...pendingImages] } : {}),
    };
    const assistantTimestamp = Date.now() + 1;
    const assistantMessage: Message = {
      role: 'assistant',
      content: '',
      timestamp: assistantTimestamp,
      streaming: true,
      reasoning: '',
      toolEvents: [],
    };

    setMessages((prev) => [...prev, userMessage, assistantMessage]);
    setInputValue('');
    setPendingImages([]);
    setLoading(true);

    // 构建对话历史（只发送最近 20 条；占位 assistant 消息不发送）；截图随消息发送
    const history = [...messages, userMessage].slice(-20).map((m) => ({
      role: m.role,
      content: m.content,
      ...(m.images && m.images.length > 0 ? { images: m.images } : {}),
    }));
    runStream(history as Array<{ role: string; content: string; images?: string[] }>, assistantTimestamp);
  }, [inputValue, loading, messages, pendingImages, runStream]);

  // 截图附件：文件转 data URL；超 2MB 拒绝（后端限制一致）
  const MAX_IMAGE_BYTES = 2 * 1024 * 1024;
  const addImageFiles = useCallback((files: File[]) => {
    const images = files.filter((f) => f.type.startsWith('image/'));
    if (images.length === 0) return;
    images.forEach((file) => {
      if (file.size > MAX_IMAGE_BYTES) {
        antMessage.error('截图 ' + file.name + ' 超过 2MB，请压缩后再发');
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        const dataUrl = String(reader.result || '');
        if (!dataUrl.startsWith('data:image/')) return;
        setPendingImages((prev) => {
          if (prev.length >= 4) {
            antMessage.warning('一次最多发 4 张截图');
            return prev;
          }
          return [...prev, dataUrl];
        });
      };
      reader.readAsDataURL(file);
    });
  }, []);

  // 输入区粘贴截图：clipboard 里有图片文件时拦截并转为附件
  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const files = Array.from(e.clipboardData?.files || []);
    if (files.length > 0) {
      e.preventDefault();
      addImageFiles(files);
    }
  }, [addImageFiles]);

  // 快捷操作（fillOnly）：把预设内容填入输入框，不直接发送；已有内容时换行追加
  const handleFillPrompt = useCallback((prompt: string) => {
    setInputValue((prev) => (prev.trim() ? prev.replace(/\s+$/, '') + '\n\n' + prompt : prompt));
    // 填入后聚焦输入框，方便用户继续编辑
    setTimeout(() => inputRef.current?.focus?.(), 0);
  }, []);

  // 快捷操作：点击即发送预设消息
  const handleQuickAction = useCallback(
    (action: ChatQuickAction) => {
      if (loading) return;
      const userMessage: Message = { role: 'user', content: action.prompt, timestamp: Date.now() };
      const assistantTimestamp = Date.now() + 1;
      const assistantMessage: Message = {
        role: 'assistant',
        content: '',
        timestamp: assistantTimestamp,
        streaming: true,
        reasoning: '',
        toolEvents: [],
      };
      setMessages((prev) => [...prev, userMessage, assistantMessage]);
      setInputValue('');
      setLoading(true);
      const history = [...messages, userMessage].slice(-20).map((m) => ({
        role: m.role,
        content: m.content,
      }));
      runStream(history, assistantTimestamp);
    },
    [loading, messages, runStream]
  );

  const handleStop = () => {
    streamControlRef.current?.abort();
  };

  const handleCopy = async (text: string) => {
    const ok = await copyText(text);
    if (ok) {
      antMessage.success('已复制');
    } else {
      antMessage.error('复制失败，请手动选择复制');
    }
  };

  const handleImportCases = () => {
    if (!onImportCases || !lastImportableAssistant) return;
    const parsed = parseSuggestedCases(lastImportableAssistant.content);
    if (parsed.length === 0) {
      antMessage.warning('未识别到可引入的用例建议（需要“### 建议：用例名称”格式的回复）');
      return;
    }
    onImportCases(parsed, lastImportableAssistant.content);
  };

  const handleSaveLast = () => {
    const lastAssistant = [...messages].reverse().find((m) => m.role === 'assistant' && m.content);
    if (!lastAssistant || !onSaveLast) return;
    onSaveLast(lastAssistant.content);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
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
        <Tooltip title="开启后展示 AI 思考过程（DeepSeek thinking），关闭则直接输出答案">
          <Space size={4} style={{ marginLeft: 'auto' }}>
            <Text type="secondary" style={{ fontSize: 12 }}>思考过程</Text>
            <Switch size="small" checked={showThinking} onChange={setShowThinking} />
          </Space>
        </Tooltip>
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
      {(quickActions?.length ?? 0) > 0 && (
        <div className="chat-quick-actions">
          {quickActions!.map((action) => (
            <Button
              key={action.label}
              size="small"
              disabled={loading}
              onClick={() =>
                action.fillOnly
                  ? handleFillPrompt(action.prompt)
                  : handleQuickAction(action)
              }
            >
              {action.label}
            </Button>
          ))}
        </div>
      )}
      {pendingImages.length > 0 && (
        <div className="chat-pending-images">
          {pendingImages.map((src, idx) => (
            <div key={idx} className="chat-pending-image">
              <img src={src} alt={'截图' + (idx + 1)} />
              <Button
                size="small"
                type="text"
                danger
                icon={<CloseOutlined />}
                onClick={() => setPendingImages((prev) => prev.filter((_, i) => i !== idx))}
              />
            </div>
          ))}
          <span className="chat-pending-images-hint">截图将随消息发送（每张 ≤2MB）</span>
        </div>
      )}
      <div className="chat-input-area">
        <TextArea
          ref={inputRef}
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          placeholder="输入消息... (Enter 发送, Shift+Enter 换行; 可直接粘贴截图)"
          autoSize={{ minRows: 3, maxRows: 10 }}
          disabled={loading}
        />
        {loading ? (
          <Tooltip title="停止生成">
            <Button danger icon={<PauseOutlined />} onClick={handleStop} />
          </Tooltip>
        ) : (
          <Tooltip title="插入截图（也可直接 Ctrl+V 粘贴）">
            <Button
              icon={<PictureOutlined />}
              onClick={() => document.getElementById('chat-image-input-' + (clientSessionId || 'x'))?.click()}
              disabled={loading}
            />
          </Tooltip>
        )}
        <Button
          type="primary"
          icon={<SendOutlined />}
          onClick={handleSend}
          disabled={!inputValue.trim() && pendingImages.length === 0}
        />
        <input
          type="file"
          accept="image/*"
          multiple
          style={{ display: 'none' }}
          id={'chat-image-input-' + (clientSessionId || 'x')}
          onChange={(e) => {
            addImageFiles(Array.from(e.target.files || []));
            e.target.value = '';
          }}
        />
      </div>
    </div>
  );

  const lastAssistantMessage = [...messages].reverse().find((m) => m.role === 'assistant' && m.content);
  // 最近一条含结构化建议的 AI 回复（供"引入用例"使用）
  const lastImportableAssistant = [...messages]
    .reverse()
    .find((m) => m.role === 'assistant' && m.content && parseSuggestedCases(m.content).length > 0);

  return (
    <Drawer
      title={
        <Space>
          <RobotOutlined />
          <span>{title}</span>
          {persistSessions && (
            <>
              <Tooltip title="历史会话">
                <Button
                  type="text"
                  size="small"
                  icon={<HistoryOutlined />}
                  onClick={() => setSessionsOpen(!sessionsOpen)}
                />
              </Tooltip>
              <Tooltip title="新会话">
                <Button
                  type="text"
                  size="small"
                  icon={<PlusOutlined />}
                  onClick={handleNewConversation}
                />
              </Tooltip>
              {savingConversation && <Text type="secondary" style={{ fontSize: 12 }}>保存中…</Text>}
            </>
          )}
        </Space>
      }
      placement="right"
      width={width}
      open={open}
      onClose={onClose}
      footer={footer}
      className="chat-drawer"
      styles={{ body: { padding: 0, display: 'flex', flexDirection: 'column' } }}
    >
      {sessionsOpen && persistSessions && (
        <div className="chat-sessions-panel">
          <div className="chat-sessions-header">
            <Text type="secondary" style={{ fontSize: 12 }}>历史会话（按更新时间）</Text>
            <Button size="small" type="text" onClick={() => setSessionsOpen(false)}>收起</Button>
          </div>
          {conversations.length === 0 ? (
            <Text type="secondary" style={{ fontSize: 12, padding: '8px 12px', display: 'block' }}>
              暂无历史会话；发送第一条消息后会自动保存。
            </Text>
          ) : (
            <div className="chat-sessions-list">
              {conversations.map((conv) => (
                <div
                  key={conv.id}
                  className={'chat-session-item' + (conv.id === activeConversationId ? ' is-active' : '')}
                  onClick={() => void handleSelectConversation(conv.id)}
                >
                  <MessageOutlined style={{ color: conv.id === activeConversationId ? '#1890ff' : '#999', marginTop: 2 }} />
                  <div className="chat-session-item__main">
                    <div className="chat-session-item__title">{conv.title || '未命名会话'}</div>
                    <div className="chat-session-item__meta">
                      {conv.last_message_preview || '（空会话）'}
                    </div>
                    <div className="chat-session-item__time">
                      {conv.updated_at ? new Date(conv.updated_at).toLocaleString('zh-CN', { hour12: false }) : ''}
                      {' · '}{conv.message_count} 条
                    </div>
                  </div>
                  <Button
                    type="text"
                    size="small"
                    icon={<DeleteOutlined />}
                    onClick={(e) => {
                      e.stopPropagation();
                      void handleDeleteConversation(conv.id);
                    }}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="chat-welcome">
            <RobotOutlined style={{ fontSize: 48, color: '#1890ff', marginBottom: 16 }} />
            <Text type="secondary" style={{ fontSize: 16 }}>
              {welcomeHint || '你好！我是 AI 助手，可以帮你回答问题、分析文档、讨论技术方案。'}
            </Text>
            <Text type="secondary" style={{ fontSize: 12, marginTop: 8 }}>
              {selectedSkills.length > 0
                ? '已启用 ' + selectedSkills.length + ' 个技能方法论'
                : '点击左下角 ⚙️ 配置技能方法论'}
            </Text>
          </div>
        )}
        {messages.map((msg, index) => (
          <div key={msg.timestamp ?? index}>
            <ChatMessage message={msg} />
            {msg.role === 'assistant' && !msg.streaming && msg.content && (
              <div className="chat-message-actions">
                <Button
                  type="text"
                  size="small"
                  icon={<CopyOutlined />}
                  onClick={() => handleCopy(msg.content)}
                >
                  复制
                </Button>
                {onSaveLast && msg === lastAssistantMessage && (
                  <Button
                    type="text"
                    size="small"
                    icon={<SaveOutlined />}
                    onClick={handleSaveLast}
                  >
                    {saveLabel}
                  </Button>
                )}
                {onImportCases && msg === lastImportableAssistant && (
                  <Button
                    type="text"
                    size="small"
                    icon={<ImportOutlined />}
                    onClick={handleImportCases}
                  >
                    {importLabel}
                  </Button>
                )}
              </div>
            )}
          </div>
        ))}
        {loading && messages.length === 0 && (
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
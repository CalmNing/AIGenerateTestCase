import React, { useState, useEffect } from 'react';
import {
  Modal, Input, Button, Typography, InputNumber,
  Spin, Tag, Switch, message, Tabs, ConfigProvider, Segmented, Tooltip,
  Badge, Alert
} from 'antd';
import {
  PlusOutlined, DeleteOutlined, PlusCircleOutlined, MinusCircleOutlined,
  ReloadOutlined, CheckCircleOutlined, CloseCircleOutlined,
  SettingOutlined, EnvironmentOutlined, ToolOutlined,
  CodeOutlined, AppstoreOutlined, ThunderboltOutlined,
  InfoCircleOutlined, LinkOutlined, NodeIndexOutlined,
  ClockCircleOutlined, KeyOutlined, SaveOutlined,
  ApiOutlined, SearchOutlined
} from '@ant-design/icons';
import CodeMirror from '@uiw/react-codemirror';
import { json } from '@codemirror/lang-json';
import { mcpApi, mcpServerApi } from '../../services/api';

const { Text } = Typography;

/* ============================================================
   Design Tokens — Clean Tech / Linear-inspired Premium Light
   ============================================================ */
const tokens = {
  bg: '#ffffff',
  surface: '#f8fafc',
  surfaceAlt: '#f1f5f9',
  border: '#e2e8f0',
  borderActive: '#cbd5e1',
  primary: '#6366f1',       // indigo-500
  primaryHover: '#4f46e5',  // indigo-600
  primaryGlow: '#eef2ff',   // indigo-50
  primarySubtle: '#e0e7ff', // indigo-100
  accent: '#06b6d4',        // cyan-500
  text: '#1e293b',           // slate-800
  textSecondary: '#64748b',  // slate-500
  textMuted: '#94a3b8',      // slate-400
  success: '#10b981',
  error: '#ef4444',
  warning: '#f59e0b',
  cardBg: '#ffffff',
  inputBg: '#ffffff',
  shadow: '0 1px 3px 0 rgb(0 0 0 / 0.05)',
  shadowMd: '0 4px 12px -2px rgb(0 0 0 / 0.06)',
  radius: 10,
  radiusSm: 6,
  radiusLg: 14,
};

/* ============================================================
   Types
   ============================================================ */
interface EnvEntry {
  key: string;
  value: string;
}

interface McpServerEntry {
  id?: number;
  name: string;
  type: string;
  enabled: boolean;
  url?: string;
  command?: string;
  args?: string[];
  timeout?: number;
  env?: EnvEntry[];
  headers?: EnvEntry[];
  enabledTools?: string[];
}

interface McpConfigModalProps {
  visible: boolean;
  onCancel: () => void;
  onSave: (values: any) => void;
}

const defaultServer: McpServerEntry = {
  name: '',
  type: 'http',
  enabled: true,
  url: '',
  command: '',
  args: [''],
  timeout: 60,
  env: [{ key: '', value: '' }],
  headers: [{ key: '', value: '' }],
  enabledTools: undefined,
};

/* ============================================================
   Helper functions (preserved from original)
   ============================================================ */
function serversToJson(servers: McpServerEntry[]): string {
  const obj: Record<string, any> = {};
  for (const s of servers) {
    if (!s.name.trim()) continue;
    const entry: Record<string, any> = { type: s.type, enabled: s.enabled, timeout: s.timeout ?? 60 };
    if (s.type === 'http' && s.url) entry.url = s.url;
    if (s.type === 'stdio') {
      if (s.command) entry.command = s.command;
      if (s.args && s.args.length > 0) entry.args = s.args.filter(a => a.trim());
    }
    if (s.env && s.env.length > 0) {
      const envObj: Record<string, string> = {};
      for (const e of s.env) {
        if (e.key.trim()) envObj[e.key] = e.value;
      }
      if (Object.keys(envObj).length > 0) entry.env = envObj;
    }
    if (s.enabledTools && s.enabledTools.length > 0) entry.enabled_tools = s.enabledTools;
    obj[s.name] = entry;
  }
  return JSON.stringify({ mcpServers: obj }, null, 2);
}

function parseJsonToEntries(text: string): McpServerEntry[] {
  const data = JSON.parse(text);
  const entries: any[] = [];
  if (Array.isArray(data)) {
    for (const item of data) {
      if (item && typeof item === 'object') entries.push(item);
    }
  } else if (data && typeof data === 'object') {
    const dict = data.mcpServers || data;
    if (typeof dict === 'object' && !Array.isArray(dict)) {
      for (const [name, cfg] of Object.entries(dict)) {
        if (cfg && typeof cfg === 'object') entries.push({ name, ...(cfg as any) });
      }
    } else if (Array.isArray(dict)) {
      for (const item of dict) {
        if (item && typeof item === 'object') entries.push(item);
      }
    }
  }
  return entries;
}

function normalizeEntry(item: any): McpServerEntry {
  const env: EnvEntry[] = [];
  const rawEnv = item.env;
  if (typeof rawEnv === 'object' && !Array.isArray(rawEnv)) {
    for (const [k, v] of Object.entries(rawEnv)) {
      env.push({ key: k, value: String(v ?? '') });
    }
  } else if (Array.isArray(rawEnv)) {
    for (const e of rawEnv) {
      if (e && typeof e === 'object') env.push({ key: e.key || '', value: e.value || '' });
    }
  }
  const type = item.type || (item.command ? 'stdio' : 'http');
  const entry: McpServerEntry = {
    name: item.name || '',
    type,
    enabled: item.enabled !== false,
    url: type === 'http' ? (item.url || '') : '',
    command: type === 'stdio' ? (item.command || '') : '',
    args: type === 'stdio' ? (item.args || []).filter((a: any) => String(a).trim()) : [''],
    timeout: item.timeout ?? 60,
    env: env.length > 0 ? env : [{ key: '', value: '' }],
    headers: [{ key: '', value: '' }],
    enabledTools: item.enabled_tools ?? undefined,
  };
  if (env.length === 0) entry.env = [{ key: '', value: '' }];
  if (!entry.args || entry.args.length === 0) entry.args = [''];
  return entry;
}

/* ============================================================
   Style helpers
   ============================================================ */
const cardStyle: React.CSSProperties = {
  background: tokens.cardBg,
  border: `1px solid ${tokens.border}`,
  borderRadius: tokens.radius,
  padding: 0,
  overflow: 'hidden',
  marginBottom: 14,
  boxShadow: tokens.shadow,
  transition: 'border-color 0.2s, box-shadow 0.25s ease',
};

const cardHeaderStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  padding: '12px 18px',
  borderBottom: `1px solid ${tokens.border}`,
  background: tokens.surface,
  minHeight: 52,
};

const _labelStyle: React.CSSProperties = {
  fontSize: 12,
  fontWeight: 500,
  color: tokens.textSecondary,
  letterSpacing: 0.3,
  textTransform: 'uppercase' as const,
  marginBottom: 6,
};

const inputStyle: React.CSSProperties = {
  background: tokens.inputBg,
  border: `1px solid ${tokens.border}`,
  borderRadius: tokens.radiusSm,
  color: tokens.text,
  height: 40,
};

const sectionStyle: React.CSSProperties = {
  padding: '16px 18px',
};

/* ============================================================
   Inline icon label component
   ============================================================ */
const FieldLabel: React.FC<{ icon: React.ReactNode; label: string; info?: string }> = ({ icon, label, info }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
    <span style={{ color: tokens.textSecondary, fontSize: 13, lineHeight: 1, opacity: 0.7 }}>{icon}</span>
    <span style={{ fontSize: 12, fontWeight: 600, color: tokens.textSecondary, letterSpacing: 0.2 }}>{label}</span>
    {info && (
      <Tooltip title={info}>
        <InfoCircleOutlined style={{ fontSize: 11, color: tokens.textMuted, cursor: 'help', opacity: 0.6 }} />
      </Tooltip>
    )}
  </div>
);

/* ============================================================
   Main Component
   ============================================================ */
const McpConfigModal: React.FC<McpConfigModalProps> = ({ visible, onCancel, onSave }) => {
  const [mode, setMode] = useState<'form' | 'json'>('form');
  const [servers, setServers] = useState<McpServerEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [toolResults, setToolResults] = useState<Record<number, { loading: boolean; data?: any[]; error?: string }>>({});
  const [jsonText, setJsonText] = useState('');
  const [jsonError, setJsonError] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (visible) {
      loadServers();
      setMode('form');
    }
  }, [visible]);

  const loadServers = async () => {
    setLoading(true);
    try {
      const res = await mcpServerApi.list();
      if (res.code === 200 && res.data) {
        const mapped = res.data.map((s: any) => ({
          id: s.id,
          name: s.name,
          type: s.type,
          enabled: s.enabled,
          url: s.url || '',
          command: s.command || '',
          args: (s.args && s.args.length > 0) ? s.args : [''],
          timeout: s.timeout ?? 60,
          env: (s.env && s.env.length > 0) ? s.env : [{ key: '', value: '' }],
          headers: [{ key: '', value: '' }],
          enabledTools: s.enabled_tools ?? undefined,
        }));
        setServers(mapped.length > 0 ? mapped : [{ ...defaultServer }]);
      } else {
        fallbackToLocal();
      }
    } catch {
      fallbackToLocal();
    } finally {
      setLoading(false);
    }
  };

  const fallbackToLocal = () => {
    const saved = localStorage.getItem('mcpServers');
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        setServers(Array.isArray(parsed) ? parsed : [{ ...defaultServer }]);
      } catch {
        setServers([{ ...defaultServer }]);
      }
    } else {
      setServers([{ ...defaultServer }]);
    }
  };

  const syncToLocalStorage = (updatedServers: McpServerEntry[]) => {
    const serversToSave = updatedServers.filter(s => s.name.trim() !== '');
    localStorage.setItem('mcpServers', JSON.stringify(serversToSave));
  };

  const switchToJson = () => {
    setJsonText(serversToJson(servers));
    setJsonError('');
    setMode('json');
  };

  const formatJson = () => {
    try {
      const parsed = JSON.parse(jsonText);
      setJsonText(JSON.stringify(parsed, null, 2));
      setJsonError('');
    } catch {
      message.warning('JSON 格式有误，无法格式化');
    }
  };

  const handleSave = async () => {
    if (mode === 'json') {
      try {
        const parsed = JSON.parse(jsonText);
        setSaving(true);
        const res = await mcpServerApi.importJson(parsed);
        if (res.code === 200) {
          message.success(res.message || 'JSON 配置已保存');
          syncToLocalStorage(
            (res.data || []).map((s: any) => ({
              id: s.id, name: s.name, type: s.type, enabled: s.enabled,
              url: s.url || '', command: s.command || '',
              args: (s.args && s.args.length > 0) ? s.args : [''],
              timeout: s.timeout ?? 60,
              env: (s.env && s.env.length > 0) ? s.env : [{ key: '', value: '' }],
              headers: [{ key: '', value: '' }],
              enabledTools: s.enabled_tools ?? undefined,
            }))
          );
          await loadServers();
          onSave(parsed);
        } else {
          message.error('保存失败: ' + (res.message || '未知错误'));
        }
      } catch (err: any) {
        message.error('JSON 解析失败: ' + (err.message || '格式错误'));
      } finally {
        setSaving(false);
      }
      return;
    }

    const validServers = servers.filter(s => s.name.trim() !== '');
    syncToLocalStorage(validServers);

    try {
      const res = await mcpServerApi.list();
      const serverIds = new Set<number>();
      if (res.code === 200 && res.data) {
        res.data.forEach((s: any) => serverIds.add(s.id));
      }

      for (const server of validServers) {
        const enabledTools = server.enabledTools && server.enabledTools.length > 0 ? server.enabledTools : undefined;
        const payload: any = {
          name: server.name,
          type: server.type,
          enabled: server.enabled,
          timeout: server.timeout ?? 60,
          url: server.type === 'http' ? server.url : '',
          command: server.type === 'stdio' ? server.command : '',
          args: server.type === 'stdio' ? (server.args || []).filter(a => a.trim() !== '') : [],
          env: (server.env || []).filter(e => e.key.trim() !== ''),
          ...(enabledTools !== undefined ? { enabled_tools: enabledTools } : {}),
        };

        if (server.id) {
          serverIds.delete(server.id);
          await mcpServerApi.update(server.id, payload);
        } else {
          await mcpServerApi.create(payload);
        }
      }

      for (const sid of serverIds) {
        await mcpServerApi.delete(sid);
      }

      message.success('MCP 配置已保存到服务端');
    } catch (e: any) {
      console.warn('同步到服务端失败，配置仅保存在本地:', e.message);
      message.warning('服务端保存失败，配置已保存在浏览器本地');
    }
    onSave(servers);
  };

  const updateServer = (index: number, field: string, value: any) => {
    const updated = [...servers];
    (updated[index] as any)[field] = value;
    setServers(updated);
  };

  const addServer = () => {
    setServers([...servers, {
      ...defaultServer,
      enabled: true,
      enabledTools: undefined,
      args: [''],
      env: [{ key: '', value: '' }],
      headers: [{ key: '', value: '' }],
    }]);
  };

  const removeServer = (index: number) => {
    setServers(servers.filter((_, i) => i !== index));
  };

  const addArg = (serverIndex: number) => {
    const updated = [...servers];
    updated[serverIndex].args = [...(updated[serverIndex].args || []), ''];
    setServers(updated);
  };

  const updateArg = (serverIndex: number, argIndex: number, value: string) => {
    const updated = [...servers];
    updated[serverIndex].args = updated[serverIndex].args || [];
    updated[serverIndex].args[argIndex] = value;
    setServers(updated);
  };

  const removeArg = (serverIndex: number, argIndex: number) => {
    const updated = [...servers];
    updated[serverIndex].args = (updated[serverIndex].args || []).filter((_, i) => i !== argIndex);
    setServers(updated);
  };

  const addEnv = (serverIndex: number) => {
    const updated = [...servers];
    updated[serverIndex].env = [...(updated[serverIndex].env || []), { key: '', value: '' }];
    setServers(updated);
  };

  const updateEnv = (serverIndex: number, envIndex: number, field: 'key' | 'value', val: string) => {
    const updated = [...servers];
    updated[serverIndex].env = updated[serverIndex].env || [];
    updated[serverIndex].env[envIndex][field] = val;
    setServers(updated);
  };

  const removeEnv = (serverIndex: number, envIndex: number) => {
    const updated = [...servers];
    updated[serverIndex].env = (updated[serverIndex].env || []).filter((_, i) => i !== envIndex);
    setServers(updated);
  };

  const fetchTools = async (si: number) => {
    const server = servers[si];
    setToolResults(prev => ({ ...prev, [si]: { loading: true } }));
    try {
      const payload: any = {
        name: server.name || 'server',
        type: server.type,
        enabled: server.enabled,
        timeout: server.timeout ?? 60,
      };
      if (server.type === 'http') {
        payload.url = server.url;
      } else {
        payload.command = server.command;
        payload.args = (server.args || []).filter(a => a.trim() !== '');
        payload.env = (server.env || []).filter(e => e.key.trim() !== '');
      }
      const res = await mcpApi.listTools([payload]);
      if (res.code === 200 && res.data) {
        setToolResults(prev => ({ ...prev, [si]: { loading: false, data: res.data } }));
        if (res.data[0]?.tools && (!servers[si].enabledTools || servers[si].enabledTools.length === 0)) {
          const allToolNames = res.data[0].tools.map((t: any) => t.name);
          updateServer(si, 'enabledTools', allToolNames);
        }
      } else {
        setToolResults(prev => ({ ...prev, [si]: { loading: false, error: res.message } }));
      }
    } catch (err: any) {
      setToolResults(prev => ({ ...prev, [si]: { loading: false, error: err.message } }));
    }
  };

  const toggleTool = (si: number, toolName: string) => {
    const server = servers[si];
    const current = server.enabledTools || [];
    const isEnabled = current.includes(toolName);
    updateServer(si, 'enabledTools',
      isEnabled ? current.filter(n => n !== toolName) : [...current, toolName]
    );
  };

  const getServerTools = (si: number) => {
    const result = toolResults[si];
    if (!result || !result.data || result.data.length === 0) return null;
    return result.data[0];
  };

  const handleJsonChange = (val: string) => {
    setJsonText(val);
    setJsonError('');
    try {
      JSON.parse(val);
    } catch (err: any) {
      setJsonError(`JSON 语法错误: ${err.message}`);
    }
  };

  const switchToForm = () => {
    try {
      const rawEntries = parseJsonToEntries(jsonText);
      if (rawEntries.length === 0) {
        setServers([{ ...defaultServer }]);
      } else {
        setServers(rawEntries.map(normalizeEntry));
      }
      setJsonError('');
      setMode('form');
    } catch (err: any) {
      message.error('无法切换到表单模式，请修正 JSON 错误后再试');
    }
  };

  /* ------------------------------------------------
     Sub-render: Single server card
     ------------------------------------------------ */
  const renderServerCard = (server: McpServerEntry, si: number) => {
    const toolsInfo = getServerTools(si);
    const enabledCount = (server.enabledTools || []).length;
    const totalTools = toolsInfo?.tools?.length || 0;

    return (
      <div
        key={si}
        style={{
          ...cardStyle,
          ...(server.enabled ? {} : { opacity: 0.55 }),
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.borderColor = tokens.primary;
          e.currentTarget.style.boxShadow = `0 0 0 1px ${tokens.primary}66, ${tokens.shadowMd}`;
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.borderColor = tokens.border;
          e.currentTarget.style.boxShadow = tokens.shadow;
        }}
      >
        {/* ── Card Header ── */}
        <div style={cardHeaderStyle}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 1, minWidth: 0 }}>
            <Switch
              checked={server.enabled}
              onChange={(v) => updateServer(si, 'enabled', v)}
              size="small"
              style={{ flexShrink: 0 }}
            />
            <Input
              placeholder="服务器名称，如 lanhu、playwright"
              value={server.name}
              onChange={(e) => updateServer(si, 'name', e.target.value)}
              variant="borderless"
              style={{
                flex: 1,
                minWidth: 120,
                fontWeight: 600,
                fontSize: 14,
                color: tokens.text,
                background: 'transparent',
              }}
            />
            <Badge
              count={server.type === 'http' ? 'HTTP' : 'STDIO'}
              style={{
                background: server.type === 'http'
                  ? 'linear-gradient(135deg, #6366f1, #818cf8)'
                  : 'linear-gradient(135deg, #10b981, #34d399)',
                border: 'none',
                fontSize: 10,
                fontWeight: 600,
                padding: '0 8px',
                lineHeight: '20px',
                height: 20,
                borderRadius: 4,
              }}
            />
            {server.id && (
              <Tag
                bordered={false}
                style={{
                  background: 'rgba(16, 185, 129, 0.12)',
                  color: tokens.success,
                  fontSize: 10,
                  fontWeight: 500,
                  borderRadius: 4,
                  padding: '0 6px',
                  lineHeight: '20px',
                  marginRight: 0,
                }}
              >
                <CheckCircleOutlined style={{ marginRight: 3 }} />已持久化
              </Tag>
            )}
          </div>
          <Tooltip title="删除此服务器">
            <Button
              type="text"
              size="small"
              icon={<DeleteOutlined />}
              onClick={() => removeServer(si)}
              style={{
                color: tokens.textMuted,
                flexShrink: 0,
                marginLeft: 8,
              }}
              onMouseEnter={(e) => { e.currentTarget.style.color = tokens.error; }}
              onMouseLeave={(e) => { e.currentTarget.style.color = tokens.textMuted; }}
            />
          </Tooltip>
        </div>

        {/* ── Card Body — Tabs ── */}
        <div style={{ padding: '0' }}>
          <Tabs
            size="small"
            style={{ margin: 0 }}
            tabBarStyle={{
              margin: 0,
              padding: '0 18px',
              background: tokens.surface,
              borderBottom: `1px solid ${tokens.border}`,
            }}
            tabBarExtraContent={null}
            items={[
              /* ⚙️ Basic Config */
              {
                key: 'basic',
                label: (
                  <span style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 4 }}>
                    <SettingOutlined />基础配置
                  </span>
                ),
                children: (
                  <div style={sectionStyle}>
                    {/* Type Select */}
                    <div style={{ marginBottom: 16 }}>
                      <FieldLabel icon={<ApiOutlined />} label="传输类型" info="HTTP 用于远程服务器，STDIO 用于本地命令行工具" />
                      <div style={{ background: tokens.inputBg, border: `1px solid ${tokens.border}`, borderRadius: tokens.radiusSm, padding: 3 }}>
                        <Segmented
                          value={server.type}
                          onChange={(v) => updateServer(si, 'type', v)}
                          options={[
                            { value: 'http', label: <span style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 4, padding: '0 4px' }}><LinkOutlined /> HTTP</span> },
                            { value: 'stdio', label: <span style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 4, padding: '0 4px' }}><NodeIndexOutlined /> STDIO</span> },
                          ]}
                          style={{ background: 'transparent' }}
                        />
                      </div>
                    </div>

                    {/* URL or Command + Args */}
                    {server.type === 'http' ? (
                      <div style={{ marginBottom: 16 }}>
                        <FieldLabel icon={<LinkOutlined />} label="服务器 URL" info="MCP 服务器的 HTTP 端点地址" />
                        <Input
                          placeholder="http://lanhu-mcp:8000/mcp"
                          value={server.url}
                          onChange={(e) => updateServer(si, 'url', e.target.value)}
                          style={{ ...inputStyle, width: '100%' }}
                          prefix={<ApiOutlined style={{ color: tokens.textMuted }} />}
                        />
                      </div>
                    ) : (
                      <>
                        <div style={{ marginBottom: 12 }}>
                          <FieldLabel icon={<NodeIndexOutlined />} label="启动命令" />
                          <Input
                            placeholder="npx"
                            value={server.command}
                            onChange={(e) => updateServer(si, 'command', e.target.value)}
                            style={{ ...inputStyle, width: '100%' }}
                            prefix={<CodeOutlined style={{ color: tokens.textMuted }} />}
                          />
                        </div>
                        <div>
                          <FieldLabel icon={<CodeOutlined />} label="参数" />
                          {(server.args || []).map((arg, ai) => (
                            <div key={ai} style={{ display: 'flex', gap: 6, marginBottom: 6, alignItems: 'center' }}>
                              <Input
                                placeholder={`参数 #${ai + 1}`}
                                value={arg}
                                onChange={(e) => updateArg(si, ai, e.target.value)}
                                style={{ ...inputStyle, flex: 1 }}
                              />
                              <Button
                                type="text"
                                size="small"
                                icon={<MinusCircleOutlined />}
                                onClick={() => removeArg(si, ai)}
                                style={{ color: tokens.textMuted, flexShrink: 0 }}
                              />
                            </div>
                          ))}
                          <Button
                            type="dashed"
                            size="small"
                            icon={<PlusCircleOutlined />}
                            onClick={() => addArg(si)}
                            style={{
                              color: tokens.primary,
                              borderColor: tokens.primarySubtle,
                              background: tokens.primaryGlow,
                              marginTop: 4,
                              fontSize: 12,
                              height: 30,
                              borderRadius: tokens.radiusSm,
                            }}
                          >
                            添加参数
                          </Button>
                        </div>
                      </>
                    )}

                    {/* Timeout */}
                    <div style={{ marginTop: 16 }}>
                      <FieldLabel icon={<ClockCircleOutlined />} label="超时时间" info="MCP 服务器响应超时（秒）" />
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <InputNumber
                          min={1}
                          max={3600}
                          value={server.timeout ?? 60}
                          onChange={(v) => updateServer(si, 'timeout', v)}
                          style={{
                            ...inputStyle,
                            width: 120,
                          }}
                          controls
                        />
                        <Text style={{ color: tokens.textMuted, fontSize: 12 }}>秒</Text>
                      </div>
                    </div>
                  </div>
                ),
              },

              /* 🌐 Environment */
              {
                key: 'env',
                label: (
                  <span style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 4 }}>
                    <EnvironmentOutlined />环境变量
                  </span>
                ),
                children: (
                  <div style={sectionStyle}>
                    {(server.env || []).map((env, ei) => (
                      <div key={ei} style={{ display: 'flex', gap: 8, marginBottom: 8, alignItems: 'center' }}>
                        <Input
                          placeholder="变量名"
                          value={env.key}
                          onChange={(e) => updateEnv(si, ei, 'key', e.target.value)}
                          style={{ ...inputStyle, width: '45%' }}
                          prefix={<KeyOutlined style={{ color: tokens.textMuted }} />}
                        />
                        <Input
                          placeholder="变量值"
                          value={env.value}
                          onChange={(e) => updateEnv(si, ei, 'value', e.target.value)}
                          style={{ ...inputStyle, flex: 1 }}
                        />
                        <Button
                          type="text"
                          size="small"
                          icon={<MinusCircleOutlined />}
                          onClick={() => removeEnv(si, ei)}
                          style={{ color: tokens.textMuted, flexShrink: 0 }}
                        />
                      </div>
                    ))}
                    <Button
                      type="dashed"
                      size="small"
                      icon={<PlusCircleOutlined />}
                      onClick={() => addEnv(si)}
                      style={{
                        color: tokens.primary,
                        borderColor: tokens.primarySubtle,
                        background: tokens.primaryGlow,
                        fontSize: 12,
                        marginTop: 2,
                        height: 30,
                        borderRadius: tokens.radiusSm,
                      }}
                    >
                      添加环境变量
                    </Button>
                  </div>
                ),
              },

              /* 🔧 Tools */
              {
                key: 'tools',
                label: (
                  <span style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 4 }}>
                    <ToolOutlined />可用方法
                    {totalTools > 0 && (
                      <span style={{
                        background: tokens.primaryGlow,
                        color: tokens.primary,
                        fontSize: 10,
                        fontWeight: 600,
                        padding: '0 6px',
                        borderRadius: 8,
                        lineHeight: '16px',
                        height: 16,
                        marginLeft: 2,
                      }}>
                        {enabledCount}/{totalTools}
                      </span>
                    )}
                  </span>
                ),
                children: (
                  <div style={sectionStyle}>
                    {/* Fetch button */}
                    <div style={{ marginBottom: 12, display: 'flex', justifyContent: 'flex-end' }}>
                      <Button
                        size="small"
                        icon={<ReloadOutlined />}
                        onClick={(e) => { e.stopPropagation(); fetchTools(si); }}
                        loading={toolResults[si]?.loading}
                        style={{
                          background: tokens.primaryGlow,
                          border: `1px solid ${tokens.primary}44`,
                          color: tokens.primary,
                          fontSize: 11,
                          height: 28,
                          borderRadius: tokens.radiusSm,
                        }}
                      >
                        获取方法列表
                      </Button>
                    </div>

                    {/* Loading with skeleton feel */}
                    {toolResults[si]?.loading && (
                      <div style={{ textAlign: 'center', padding: '28px 0' }}>
                        <Spin size="small" style={{ color: tokens.primary }} />
                        <div style={{ color: tokens.textSecondary, fontSize: 12, marginTop: 10 }}>
                          <span style={{ display: 'inline-block', animation: 'pulse 1.5s infinite' }}>正在连接服务器获取方法列表...</span>
                        </div>
                        <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 6, maxWidth: 300, margin: '16px auto 0' }}>
                          {[1, 2, 3].map(i => (
                            <div key={i} style={{
                              height: 32,
                              borderRadius: 6,
                              background: `linear-gradient(90deg, ${tokens.surfaceAlt} 25%, ${tokens.surface} 50%, ${tokens.surfaceAlt} 75%)`,
                              backgroundSize: '200% 100%',
                              animation: 'shimmer 1.5s infinite',
                              opacity: 1 - i * 0.15,
                            }} />
                          ))}
                        </div>
                        <style>{`
                          @keyframes shimmer {
                            0% { background-position: -200% 0; }
                            100% { background-position: 200% 0; }
                          }
                        `}</style>
                      </div>
                    )}

                    {/* Error */}
                    {toolResults[si]?.error && (
                      <Alert
                        type="error"
                        message={<span style={{ fontSize: 12 }}>连接失败: {toolResults[si].error}</span>}
                        style={{
                          background: 'rgba(239,68,68,0.08)',
                          border: '1px solid rgba(239,68,68,0.2)',
                          borderRadius: tokens.radiusSm,
                          padding: '8px 12px',
                        }}
                        icon={<CloseCircleOutlined style={{ color: tokens.error }} />}
                        showIcon
                      />
                    )}

                    {/* Tool list */}
                    {toolsInfo && (
                      <>
                        {toolsInfo.available ? (
                          <div style={{
                            background: tokens.inputBg,
                            border: `1px solid ${tokens.border}`,
                            borderRadius: tokens.radiusSm,
                            overflow: 'hidden',
                          }}>
                            {toolsInfo.tools.length === 0 ? (
                              <div style={{ textAlign: 'center', padding: '16px', color: tokens.textMuted, fontSize: 12 }}>
                                该服务器无可用方法
                              </div>
                            ) : (
                              <div style={{ maxHeight: 240, overflowY: 'auto' }}>
                                {toolsInfo.tools.map((t: any, ti: number) => {
                                  const enabledList = servers[si].enabledTools || [];
                                  const isToolEnabled = enabledList.includes(t.name);
                                  return (
                                    <div
                                      key={ti}
                                      style={{
                                        display: 'flex',
                                        alignItems: 'flex-start',
                                        gap: 10,
                                        padding: '8px 14px',
                                        borderBottom: ti < toolsInfo.tools.length - 1 ? `1px solid ${tokens.border}` : 'none',
                                        cursor: 'pointer',
                                        transition: 'background 0.15s',
                                      }}
                                      onClick={() => toggleTool(si, t.name)}
                                      onMouseEnter={(e) => { e.currentTarget.style.background = tokens.primaryGlow; }}
                                      onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
                                    >
                                      <div style={{
                                        width: 18,
                                        height: 18,
                                        borderRadius: 4,
                                        border: `1.5px solid ${isToolEnabled ? tokens.primary : tokens.borderActive}`,
                                        background: isToolEnabled ? tokens.primary : tokens.inputBg,
                                        display: 'flex',
                                        alignItems: 'center',
                                        justifyContent: 'center',
                                        flexShrink: 0,
                                        marginTop: 2,
                                        transition: 'all 0.18s ease',
                                        transform: isToolEnabled ? 'scale(1.05)' : 'scale(1)',
                                      }}>
                                        {isToolEnabled && (
                                          <CheckCircleOutlined style={{ color: '#fff', fontSize: 11 }} />
                                        )}
                                      </div>
                                      <div style={{ flex: 1, minWidth: 0 }}>
                                        <code style={{
                                          color: tokens.text,
                                          fontSize: 12,
                                          fontWeight: 500,
                                          fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
                                        }}>
                                          {t.name}
                                        </code>
                                        {t.description && (
                                          <div style={{
                                            color: tokens.textMuted,
                                            fontSize: 11,
                                            marginTop: 2,
                                            lineHeight: 1.4,
                                            overflow: 'hidden',
                                            textOverflow: 'ellipsis',
                                            whiteSpace: 'nowrap',
                                          }}>
                                            {t.description}
                                          </div>
                                        )}
                                      </div>
                                      {t.inputSchema && (
                                        <Tooltip title="此方法包含输入参数">
                                          <CodeOutlined style={{ color: tokens.textMuted, fontSize: 11, flexShrink: 0 }} />
                                        </Tooltip>
                                      )}
                                    </div>
                                  );
                                })}
                              </div>
                            )}
                            <div style={{
                              padding: '8px 14px',
                              borderTop: `1px solid ${tokens.border}`,
                              display: 'flex',
                              alignItems: 'center',
                              gap: 6,
                              fontSize: 11,
                              color: tokens.textMuted,
                            }}>
                              <CheckCircleOutlined style={{ color: tokens.success, fontSize: 11 }} />
                              共 {toolsInfo.tools.length} 个方法 · 已启用 {enabledCount} 个
                              {toolsInfo.tools.length > 0 && (
                                <span style={{ marginLeft: 'auto' }}>
                                  <Button
                                    type="link"
                                    size="small"
                                    style={{ fontSize: 11, color: tokens.primary, padding: '0 4px', fontWeight: 500 }}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      if (enabledCount === toolsInfo.tools.length) {
                                        updateServer(si, 'enabledTools', []);
                                      } else {
                                        updateServer(si, 'enabledTools', toolsInfo.tools.map((t: any) => t.name));
                                      }
                                    }}
                                  >
                                    {enabledCount === toolsInfo.tools.length ? '全部禁用' : '全部启用'}
                                  </Button>
                                </span>
                              )}
                            </div>
                          </div>
                        ) : (
                          <Alert
                            type="error"
                            message={<span style={{ fontSize: 12 }}>连接失败{toolsInfo.error ? `: ${toolsInfo.error}` : ''}</span>}
                            style={{
                              background: 'rgba(239,68,68,0.08)',
                              border: '1px solid rgba(239,68,68,0.2)',
                              borderRadius: tokens.radiusSm,
                              padding: '8px 12px',
                            }}
                            icon={<CloseCircleOutlined style={{ color: tokens.error }} />}
                            showIcon
                          />
                        )}
                      </>
                    )}

                    {!toolResults[si] && !toolsInfo && (
                      <div style={{
                        textAlign: 'center',
                        padding: '24px 0',
                        color: tokens.textMuted,
                        fontSize: 12,
                      }}>
                        <SearchOutlined style={{ fontSize: 20, display: 'block', marginBottom: 8, opacity: 0.5 }} />
                        点击上方「获取方法列表」按钮<br />连接并查看可用方法
                      </div>
                    )}
                  </div>
                ),
              },
            ]}
          />
        </div>
      </div>
    );
  };

  /* ------------------------------------------------
     Render
     ------------------------------------------------ */
  return (
    <ConfigProvider
      theme={{
        token: {
          colorPrimary: tokens.primary,
          colorBgContainer: tokens.surface,
          colorBgElevated: tokens.surface,
          colorBorder: tokens.border,
          colorText: tokens.text,
          colorTextSecondary: tokens.textSecondary,
          borderRadius: tokens.radiusSm,
          fontSize: 13,
          controlHeight: 36,
          controlItemBgHover: tokens.surfaceAlt,
        },
        components: {
          Modal: {
            contentBg: tokens.bg,
            headerBg: tokens.bg,
            titleColor: tokens.text,
            titleFontSize: 16,
          },
          Input: {
            colorBgContainer: tokens.inputBg,
            colorBorder: tokens.border,
            activeBorderColor: tokens.primary,
            hoverBorderColor: tokens.borderActive,
            colorText: tokens.text,
            colorTextPlaceholder: tokens.textMuted,
            borderRadius: tokens.radiusSm,
          },
          Select: {
            colorBgContainer: tokens.inputBg,
            colorBorder: tokens.border,
            activeBorderColor: tokens.primary,
            colorText: tokens.text,
            colorTextPlaceholder: tokens.textMuted,
            borderRadius: tokens.radiusSm,
          },
          InputNumber: {
            colorBgContainer: tokens.inputBg,
            colorBorder: tokens.border,
            activeBorderColor: tokens.primary,
            colorText: tokens.text,
            borderRadius: tokens.radiusSm,
          },
          Button: {
            colorPrimary: tokens.primary,
            colorPrimaryHover: tokens.primaryHover,
            borderRadius: tokens.radiusSm,
            controlHeight: 36,
          },
          Tabs: {
            colorText: tokens.textSecondary,
            colorPrimary: tokens.primary,
            inkBarColor: tokens.primary,
            itemColor: tokens.textSecondary,
            itemHoverColor: tokens.text,
            itemSelectedColor: tokens.text,
          },
          Segmented: {
            colorBgContainer: tokens.surfaceAlt,
            colorText: tokens.textSecondary,
            colorTextLabel: tokens.textSecondary,
            itemSelectedBg: tokens.bg,
            itemSelectedColor: tokens.text,
            borderRadius: tokens.radiusSm,
            controlHeight: 40,
          },
          Switch: {
            colorPrimary: tokens.primary,
          },
          Badge: {
            fontSize: 10,
          },
          Tag: {
            colorText: tokens.success,
          },
          Message: {},
          Alert: {
            colorErrorBg: 'rgba(239,68,68,0.08)',
            colorErrorBorder: 'rgba(239,68,68,0.2)',
          },
        },
      }}
    >
      <Modal
        title={
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{
              width: 34,
              height: 34,
              borderRadius: 10,
              background: `linear-gradient(135deg, ${tokens.primary}, #818cf8)`,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 16,
              boxShadow: `0 2px 8px ${tokens.primaryGlow}`,
            }}>
              <ThunderboltOutlined style={{ color: '#fff' }} />
            </div>
            <div>
              <div style={{ fontSize: 16, fontWeight: 600, lineHeight: 1.3 }}>MCP 服务器配置</div>
              <div style={{ fontSize: 11, color: tokens.textMuted, fontWeight: 400, lineHeight: 1.3 }}>
                配置 AI 助手的 MCP 工具服务器连接
              </div>
            </div>
          </div>
        }
        open={visible}
        onCancel={onCancel}
        width={800}
        footer={
          <div style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            borderTop: `1px solid ${tokens.border}`,
            paddingTop: 16,
            marginTop: 0,
          }}>
            <Text style={{ fontSize: 11, color: tokens.textMuted }}>
              修改后需保存配置才能生效
            </Text>
            <div style={{ display: 'flex', gap: 10 }}>
              <Button
                onClick={onCancel}
                style={{
                  color: tokens.textSecondary,
                  borderColor: tokens.border,
                  background: tokens.inputBg,
                  height: 40,
                  borderRadius: tokens.radiusSm,
                  fontSize: 13,
                  padding: '0 20px',
                }}
              >
                取消
              </Button>
              <Button
                type="primary"
                onClick={handleSave}
                loading={saving}
                icon={<SaveOutlined />}
                style={{
                  height: 40,
                  borderRadius: tokens.radiusSm,
                  fontSize: 13,
                  fontWeight: 600,
                  padding: '0 24px',
                  background: `linear-gradient(135deg, ${tokens.primary}, ${tokens.primaryHover})`,
                  border: 'none',
                  boxShadow: `0 2px 8px ${tokens.primaryGlow}`,
                  letterSpacing: 0.3,
                }}
              >
                保存配置
              </Button>
            </div>
          </div>
        }
        styles={{
          body: {
            padding: '20px 0 0',
          },
        }}
        destroyOnClose
        centered
      >
        {/* ── Mode Switch ── */}
        <div style={{
          padding: '0 24px 16px',
        }}>
          <div style={{
            background: tokens.surface,
            border: `1px solid ${tokens.border}`,
            borderRadius: tokens.radiusSm,
            padding: 4,
          }}>
            <Segmented
              value={mode}
              block
              onChange={(key) => {
                if (key === 'json') {
                  switchToJson();
                } else {
                  switchToForm();
                }
              }}
              options={[
                {
                  value: 'form',
                  label: (
                    <span style={{ fontSize: 14, display: 'flex', alignItems: 'center', gap: 6, justifyContent: 'center', padding: '3px 0' }}>
                      <AppstoreOutlined /> 页面配置
                    </span>
                  ),
                },
                {
                  value: 'json',
                  label: (
                    <span style={{ fontSize: 14, display: 'flex', alignItems: 'center', gap: 6, justifyContent: 'center', padding: '3px 0' }}>
                      <CodeOutlined /> JSON 配置
                    </span>
                  ),
                },
              ]}
            />
          </div>
        </div>

        {/* ── Scrollable Content ── */}
        <div style={{ maxHeight: '58vh', overflow: 'auto', padding: '0 24px' }}>
          {mode === 'form' ? (
            /* ========== FORM MODE ========== */
            <>
              {loading ? (
                <div style={{ textAlign: 'center', padding: '60px 0' }}>
                  <Spin size="large" style={{ color: tokens.primary }} />
                  <div style={{ color: tokens.textMuted, fontSize: 13, marginTop: 12 }}>加载配置中...</div>
                </div>
              ) : servers.length === 0 ? (
                <div style={{
                  textAlign: 'center',
                  padding: '64px 20px',
                  border: `1px dashed ${tokens.border}`,
                  borderRadius: tokens.radius,
                  background: tokens.surface,
                }}>
                  <div style={{
                    width: 56,
                    height: 56,
                    borderRadius: 16,
                    background: tokens.primaryGlow,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    margin: '0 auto',
                  }}>
                    <ApiOutlined style={{ fontSize: 24, color: tokens.primary }} />
                  </div>
                  <div style={{ color: tokens.text, fontSize: 15, fontWeight: 600, marginTop: 16 }}>
                    暂无 MCP 服务器配置
                  </div>
                  <div style={{ color: tokens.textSecondary, fontSize: 13, marginTop: 6, lineHeight: 1.5 }}>
                    添加 MCP 服务器后，AI 助手即可通过工具调用<br />获取外部数据或执行操作
                  </div>
                  <Button
                    type="primary"
                    onClick={addServer}
                    icon={<PlusOutlined />}
                    style={{ marginTop: 20, borderRadius: tokens.radiusSm, height: 40, padding: '0 24px' }}
                  >
                    添加 MCP 服务器
                  </Button>
                </div>
              ) : (
                <>
                  {servers.map((server, si) => renderServerCard(server, si))}

                  <Button
                    type="dashed"
                    onClick={addServer}
                    block
                    icon={<PlusOutlined />}
                    style={{
                      height: 46,
                      color: tokens.primary,
                      borderColor: tokens.primarySubtle,
                      background: tokens.primaryGlow,
                      borderStyle: 'dashed',
                      fontSize: 14,
                      fontWeight: 500,
                      borderRadius: tokens.radiusSm,
                      marginBottom: 8,
                    }}
                  >
                    添加 MCP 服务器
                  </Button>
                </>
              )}
            </>
          ) : (
            /* ========== JSON MODE ========== */
            <div>
              <div style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: 12,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <InfoCircleOutlined style={{ color: tokens.textMuted, fontSize: 12 }} />
                  <Text style={{ fontSize: 12, color: tokens.textMuted }}>
                    以标准 <Text code >mcpServers</Text> JSON 格式编辑
                  </Text>
                </div>
                <Button
                  size="small"
                  onClick={formatJson}
                  icon={<CodeOutlined />}
                  style={{
                    color: tokens.textSecondary,
                    borderColor: tokens.border,
                    borderRadius: tokens.radiusSm,
                    fontSize: 12,
                    height: 28,
                  }}
                >
                  格式化
                </Button>
              </div>

              {/* JSON Editor */}
              <div style={{
                border: `1px solid ${jsonError ? tokens.error : tokens.border}`,
                borderRadius: tokens.radiusSm,
                overflow: 'hidden',
                transition: 'border-color 0.2s',
              }}>
                <CodeMirror
                  value={jsonText}
                  onChange={(value) => handleJsonChange(value)}
                  extensions={[json()]}
                  height="380px"
                  basicSetup={{
                    lineNumbers: true,
                    foldGutter: true,
                    bracketMatching: true,
                    closeBrackets: true,
                    tabSize: 2,
                    highlightActiveLine: true,
                  }}
                />
              </div>

              {/* Status indicators */}
              <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
                {jsonError ? (
                  <div style={{ fontSize: 12, color: tokens.error, display: 'flex', alignItems: 'center', gap: 4 }}>
                    <CloseCircleOutlined />{jsonError}
                  </div>
                ) : jsonText.trim() ? (
                  <div style={{ fontSize: 12, color: tokens.success, display: 'flex', alignItems: 'center', gap: 4 }}>
                    <CheckCircleOutlined />JSON 语法正确
                  </div>
                ) : (
                  <span style={{ fontSize: 12, color: tokens.textMuted }}>请输入 JSON 配置</span>
                )}
              </div>
            </div>
          )}
        </div>
      </Modal>
    </ConfigProvider>
  );
};

export default McpConfigModal;

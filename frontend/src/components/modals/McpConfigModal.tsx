import React, { useState, useEffect } from 'react';
import { Modal, Input, Select, Button, Space, Typography, Empty, InputNumber, Spin, Tag, Switch, message, Collapse } from 'antd';
import { PlusOutlined, DeleteOutlined, PlusCircleOutlined, MinusCircleOutlined, ReloadOutlined, CheckCircleOutlined, CloseCircleOutlined, SettingOutlined, EnvironmentOutlined, ToolOutlined } from '@ant-design/icons';
import { mcpApi, mcpServerApi } from '../../services/api';

const { Text } = Typography;

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

const McpConfigModal: React.FC<McpConfigModalProps> = ({ visible, onCancel, onSave }) => {
  const [servers, setServers] = useState<McpServerEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [toolResults, setToolResults] = useState<Record<number, { loading: boolean; data?: any[]; error?: string }>>({});

  useEffect(() => {
    if (visible) {
      loadServers();
    }
  }, [visible]);

  const loadServers = async () => {
    setLoading(true);
    try {
      // 从服务端加载持久化配置
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
        // 服务端无数据，回退到 localStorage
        fallbackToLocal();
      }
    } catch {
      // 服务端不可用，回退到 localStorage
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

  const handleSave = async () => {
    const validServers = servers.filter(s => s.name.trim() !== '');

    // 同步到 localStorage（兼容现有生成流程）
    syncToLocalStorage(validServers);

    // 批量同步到服务端
    try {
      // 获取当前服务端列表
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
          // 更新已存在的配置
          serverIds.delete(server.id);
          await mcpServerApi.update(server.id, payload);
        } else {
          // 创建新配置
          await mcpServerApi.create(payload);
        }
      }

      // 删除在服务端存在，但在前端已被移除的配置
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
    setServers([...servers, { ...defaultServer, enabled: true, enabledTools: undefined, args: [''], env: [{ key: '', value: '' }], headers: [{ key: '', value: '' }] }]);
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
        // 首次获取工具时，自动启用所有工具（如果之前未设置过）
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

  return (
    <Modal
      title="MCP 配置"
      open={visible}
      onCancel={onCancel}
      okText="保存"
      cancelText="取消"
      width={720}
      onOk={handleSave}
    >
      <div style={{ marginBottom: 16, fontSize: 13, color: '#666' }}>
        配置 MCP 服务器，格式参考 <Text code>.mcp.json</Text>
        <span style={{ marginLeft: 8, color: '#52c41a' }}>配置将持久化保存到服务端</span>
      </div>

      <div style={{ maxHeight: '40vh', overflow: 'auto' }}>
      {loading ? (
        <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
      ) : servers.length === 0 ? (
        <Empty description="暂无 MCP 服务器配置" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        servers.map((server, si) => (
          <div
            key={si}
            style={{
              padding: 16,
              marginBottom: 16,
              border: '1px solid #e8e8e8',
              borderRadius: 8,
              background: '#fafafa',
            }}
          >
            {/* 头部：服务器名称 + 开关 + 删除 */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <Space>
                <Switch
                  checked={server.enabled}
                  onChange={(v) => updateServer(si, 'enabled', v)}
                  size="small"
                />
                <Input
                  placeholder="服务器名称，如 lanhu、playwright"
                  value={server.name}
                  onChange={(e) => updateServer(si, 'name', e.target.value)}
                  style={{ width: 280, fontWeight: 600 }}
                  variant="borderless"
                />
                {server.id && (
                  <Tag color="green" style={{ fontSize: 10 }}>已持久化</Tag>
                )}
              </Space>
              <Button type="text" danger icon={<DeleteOutlined />} onClick={() => removeServer(si)} />
            </div>

            {/* 可折叠配置区域 */}
            <Collapse
              ghost
              size="small"
              expandIconPosition="end"
              defaultActiveKey={['basic']}
              items={[
                {
                  key: 'basic',
                  label: <span style={{ fontSize: 13, fontWeight: 500 }}><SettingOutlined style={{ marginRight: 6 }} />基础配置</span>,
                  children: (
                    <Space direction="vertical" style={{ width: '100%' }} size={10}>
                      <Space size={12} style={{ width: '100%' }}>
                        <div style={{ width: 100, fontSize: 12, color: '#666' }}>传输类型</div>
                        <Select
                          value={server.type}
                          onChange={(v) => updateServer(si, 'type', v)}
                          style={{ width: 200 }}
                          options={[
                            { value: 'http', label: 'HTTP (Streamable HTTP)' },
                            { value: 'stdio', label: 'STDIO (命令行)' },
                          ]}
                        />
                      </Space>

                      {server.type === 'stdio' ? (
                        <>
                          <Space size={12} style={{ width: '100%' }}>
                            <div style={{ width: 100, fontSize: 12, color: '#666' }}>命令</div>
                            <Input
                              placeholder="npx"
                              value={server.command}
                              onChange={(e) => updateServer(si, 'command', e.target.value)}
                              style={{ flex: 1 }}
                            />
                          </Space>

                          <div style={{ paddingLeft: 112 }}>
                            <div style={{ fontSize: 12, color: '#666', marginBottom: 6 }}>参数</div>
                            {(server.args || []).map((arg, ai) => (
                              <Space key={ai} style={{ marginBottom: 6, width: '100%' }}>
                                <Input
                                  placeholder={'参数 #' + (ai + 1)}
                                  value={arg}
                                  onChange={(e) => updateArg(si, ai, e.target.value)}
                                  style={{ flex: 1 }}
                                />
                                <Button type="text" size="small" danger icon={<MinusCircleOutlined />} onClick={() => removeArg(si, ai)} />
                              </Space>
                            ))}
                            <Button type="dashed" size="small" icon={<PlusCircleOutlined />} onClick={() => addArg(si)}>
                              添加参数
                            </Button>
                          </div>
                        </>
                      ) : (
                        <Space size={12} style={{ width: '100%' }}>
                          <div style={{ width: 100, fontSize: 12, color: '#666' }}>URL</div>
                          <Input
                            placeholder="http://lanhu-mcp:8000/mcp"
                            value={server.url}
                            onChange={(e) => updateServer(si, 'url', e.target.value)}
                            style={{ flex: 1 }}
                          />
                        </Space>
                      )}

                      <Space size={12} style={{ width: '100%' }}>
                        <div style={{ width: 100, fontSize: 12, color: '#666' }}>超时 (秒)</div>
                        <InputNumber
                          min={1}
                          max={3600}
                          value={server.timeout ?? 60}
                          onChange={(v) => updateServer(si, 'timeout', v)}
                          style={{ width: 120 }}
                        />
                      </Space>
                    </Space>
                  ),
                },
                {
                  key: 'env',
                  label: <span style={{ fontSize: 13, fontWeight: 500 }}><EnvironmentOutlined style={{ marginRight: 6 }} />环境变量</span>,
                  children: (
                    <div>
                      {(server.env || []).map((env, ei) => (
                        <Space key={ei} style={{ marginBottom: 6, width: '100%' }}>
                          <Input
                            placeholder="变量名"
                            value={env.key}
                            onChange={(e) => updateEnv(si, ei, 'key', e.target.value)}
                            style={{ width: 180 }}
                          />
                          <Input.Password
                            placeholder="变量值"
                            value={env.value}
                            onChange={(e) => updateEnv(si, ei, 'value', e.target.value)}
                            style={{ flex: 1 }}
                          />
                          <Button type="text" size="small" danger icon={<MinusCircleOutlined />} onClick={() => removeEnv(si, ei)} />
                        </Space>
                      ))}
                      <div style={{ marginTop: 6 }}>
                        <Button type="dashed" size="small" icon={<PlusCircleOutlined />} onClick={() => addEnv(si)}>
                          添加环境变量
                        </Button>
                      </div>
                    </div>
                  ),
                },
                {
                  key: 'tools',
                  label: <span style={{ fontSize: 13, fontWeight: 500 }}><ToolOutlined style={{ marginRight: 6 }} />可用方法</span>,
                  extra: (
                    <Button
                      size="small"
                      icon={<ReloadOutlined />}
                      onClick={(e) => { e.stopPropagation(); fetchTools(si); }}
                      loading={toolResults[si]?.loading}
                    >
                      获取方法列表
                    </Button>
                  ),
                  children: (
                    <div>
                      {toolResults[si]?.loading && (
                        <div style={{ textAlign: 'center', padding: '12px 0' }}>
                          <Spin size="small" /> <span style={{ fontSize: 12, color: '#999', marginLeft: 8 }}>连接中...</span>
                        </div>
                      )}
                      {toolResults[si]?.error && (
                        <div style={{ fontSize: 12, color: '#ff4d4f', padding: '4px 0' }}>
                          <CloseCircleOutlined style={{ marginRight: 4 }} />{toolResults[si].error}
                        </div>
                      )}
                      {getServerTools(si) && (
                        <>
                          {getServerTools(si).available ? (
                            <div style={{ background: '#fff', borderRadius: 4, padding: '8px 12px' }}>
                              {getServerTools(si).tools.length === 0 ? (
                                <div style={{ fontSize: 12, color: '#999' }}>该服务器无可用方法</div>
                              ) : (
                                getServerTools(si).tools.map((t: any, ti: number) => {
                                  const enabledList = servers[si].enabledTools || [];
                                  const isToolEnabled = enabledList.includes(t.name);
                                  return (
                                    <div key={ti} style={{ padding: '6px 0', borderBottom: ti < getServerTools(si).tools.length - 1 ? '1px solid #f0f0f0' : 'none' }}>
                                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                        <Switch
                                          checked={isToolEnabled}
                                          onChange={() => toggleTool(si, t.name)}
                                          size="small"
                                          style={{ marginRight: 4 }}
                                        />
                                        <Tag color="blue" style={{ fontSize: 11 }}>{t.name}</Tag>
                                      </div>
                                      {t.description && (
                                        <div style={{ fontSize: 12, color: '#888', marginTop: 2, marginLeft: 24 }}>{t.description}</div>
                                      )}
                                    </div>
                                  );
                                })
                              )}
                              <div style={{ fontSize: 11, color: '#aaa', marginTop: 4 }}>
                                <CheckCircleOutlined style={{ color: '#52c41a', marginRight: 4 }} />共 {getServerTools(si).tools.length} 个方法，已启用 {(servers[si].enabledTools || []).length} 个
                              </div>
                            </div>
                          ) : (
                            <div style={{ fontSize: 12, color: '#ff4d4f', padding: '4px 0' }}>
                              <CloseCircleOutlined style={{ marginRight: 4 }} />连接失败{getServerTools(si).error ? (': ' + getServerTools(si).error) : ''}
                            </div>
                          )}
                        </>
                      )}
                      {!toolResults[si] && (
                        <div style={{ fontSize: 12, color: '#999', padding: '8px 0', textAlign: 'center' }}>
                          点击上方按钮获取服务器可用方法列表
                        </div>
                      )}
                    </div>
                  ),
                },
              ]}
            />
          </div>
        ))
      )}

      <Button type="dashed" onClick={addServer} block icon={<PlusOutlined />} style={{ marginTop: 8 }}>
        添加 MCP 服务器
      </Button>
      </div>
    </Modal>
  );
};

export default McpConfigModal;

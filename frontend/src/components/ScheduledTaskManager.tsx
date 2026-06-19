import React, { useState, useEffect } from 'react';
import {
  Table,
  Button,
  message,
  Modal,
  Card,
  Tag,
  Space,
  Typography,
  Popconfirm,
  Form,
  Input,
  Select,
  InputNumber,
  Radio
} from 'antd';
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons';
import { scheduledTaskApi, savedRequestApi, globalParameterApi } from '../services/api';
import type { ScheduledTask } from '../types';
import FileUpload, { UploadedFileResult } from './FileUpload';
import './ScheduledTaskManager.css';


const { Text } = Typography;

const ScheduledTaskManager: React.FC = () => {
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchTask, setSearchTask] = useState('');
  const [logModalVisible, setLogModalVisible] = useState(false);
  const [selectedTask, setSelectedTask] = useState<ScheduledTask | null>(null);
  const [parsedLog, setParsedLog] = useState<any[]>([]);
  const [isFormVisible, setIsFormVisible] = useState(false);
  const [editingTask, setEditingTask] = useState<ScheduledTask | null>(null);
  const [savedRequests, setSavedRequests] = useState<any[]>([]);
  const [environments, setEnvironments] = useState<any[]>([]);
  const [selectedRequestToAdd, setSelectedRequestToAdd] = useState<number | undefined>(undefined);
  const [requestIds, setRequestIds] = useState<number[]>([]);
  const [taskParameters, setTaskParameters] = useState<Array<{ key: string; value: string; type?: 'text' | 'file'; file?: UploadedFileResult }>>([]);

  // 切换参数类型
  const handleTaskParamTypeChange = (index: number, type: 'text' | 'file') => {
    const newParams = [...taskParameters];
    newParams[index] = { ...newParams[index], type };
    if (type === 'text') {
      delete newParams[index].file;
      newParams[index].value = '';
    }
    setTaskParameters(newParams);
  };

  // 文件上传/删除回调
  const handleTaskParamFileChange = (index: number, fileResult?: UploadedFileResult) => {
    const newParams = [...taskParameters];
    if (fileResult) {
      newParams[index] = { ...newParams[index], file: fileResult, value: fileResult.fileId };
    } else {
      // 文件被删除，清空文件信息
      newParams[index] = { ...newParams[index], file: undefined, value: '' };
    }
    setTaskParameters(newParams);
  };

  const [form] = Form.useForm();

  // 获取当前选中环境的参数（用于 FileUpload 的 baseUrl 和 accessToken）
  const currentEnvironmentId = Form.useWatch('environment_id', form);
  const currentEnv = environments.find((e: any) => e.id === currentEnvironmentId);
  const envBaseUrl = currentEnv?.parameters?.find((p: any) => p.key === 'baseUrl')?.value;
  const envAccessToken = currentEnv?.parameters?.find((p: any) => p.key === 'access-token')?.value;

  const fetchTasks = async () => {
    setLoading(true);
    try {
      const response = await scheduledTaskApi.getTasks();
      if (response.code === 200) {
        setTasks(response.data || []);
      } else {
        message.error('获取定时任务失败');
      }
    } catch (error) {
      message.error('获取定时任务失败');
    } finally {
      setLoading(false);
    }
  };

  const fetchSavedRequests = async () => {
    try {
      const response = await savedRequestApi.getRequests();
      if (response.code === 200 && response.data) {
        setSavedRequests(response.data);
      }
    } catch (error) {
      console.error('Failed to fetch saved requests:', error);
    }
  };

  const fetchEnvironments = async () => {
    try {
      const response = await globalParameterApi.getEnvironments();
      if (response.code === 200 && response.data) {
        setEnvironments(response.data);
      }
    } catch (error) {
      console.error('Failed to fetch environments:', error);
    }
  };

  useEffect(() => {
    fetchTasks();
    fetchSavedRequests();
    fetchEnvironments();
  }, []);

  const handleRunTask = async (taskId: number) => {
    try {
      const response = await scheduledTaskApi.runTask(taskId);
      if (response.code === 200) {
        message.success('任务已触发执行');
        // 延迟刷新以等待任务执行完成
        setTimeout(fetchTasks, 2000);
      } else {
        message.error('触发任务失败');
      }
    } catch (error) {
      message.error('触发任务失败');
    }
  };

  const handleToggleTask = async (task: ScheduledTask) => {
    try {
      const response = await scheduledTaskApi.updateTask(task.id, {
        ...task,
        enabled: !task.enabled
      });
      if (response.code === 200) {
        message.success(task.enabled ? '任务已禁用' : '任务已启用');
        fetchTasks();
      } else {
        message.error('更新任务失败');
      }
    } catch (error) {
      message.error('更新任务失败');
    }
  };

  const handleDeleteTask = async (taskId: number) => {
    try {
      const response = await scheduledTaskApi.deleteTask(taskId);
      if (response.code === 200) {
        message.success('任务已删除');
        fetchTasks();
      } else {
        message.error('删除任务失败');
      }
    } catch (error) {
      message.error('删除任务失败');
    }
  };

  const handleCopyTask = (task: ScheduledTask) => {
    // 创建任务的副本
    const copiedTask = {
      ...task,
      id: undefined, // 移除ID，使其成为新任务
      name: `${task.name} (复制)`, // 修改名称
      enabled: false, // 默认为禁用状态
      last_run_at: null,
      last_run_result: null
    };

    // 打开编辑表单，预填充复制的数据
    setEditingTask(null); // 确保是创建新任务
    form.setFieldsValue({
      name: copiedTask.name,
      schedule_type: copiedTask.schedule_type,
      interval_seconds: copiedTask.interval_seconds,
      cron_expression: copiedTask.cron_expression,
      request_ids: copiedTask.request_ids,
      environment_id: copiedTask.environment_id,
    });
    setRequestIds(copiedTask.request_ids || []);
    setTaskParameters(copiedTask.parameters || []);
    setSelectedRequestToAdd(undefined);
    setIsFormVisible(true);
    message.info('已复制任务，请修改并保存');
  };

  const handleOpenCreate = () => {
    setEditingTask(null);
    form.resetFields();
    form.setFieldsValue({ name: '', schedule_type: 'interval', interval_seconds: 60, request_ids: [], environment_id: undefined });
    setRequestIds([]);
    setTaskParameters([]);
    setSelectedRequestToAdd(undefined);
    setIsFormVisible(true);
  };

  const handleOpenEdit = (task: ScheduledTask) => {
    setEditingTask(task);
    form.setFieldsValue({
      name: task.name,
      schedule_type: task.schedule_type,
      interval_seconds: task.interval_seconds,
      cron_expression: task.cron_expression,
      request_ids: task.request_ids,
      environment_id: task.environment_id,
    });
    setRequestIds(task.request_ids || []);
    setTaskParameters(task.parameters || []);
    setSelectedRequestToAdd(undefined);
    setIsFormVisible(true);
  };

  const handleFormSubmit = async () => {
    try {
      // 确保 request_ids 字段的值与 requestIds 状态同步
      form.setFieldsValue({ request_ids: requestIds });
      const values = await form.validateFields();
      // 附上任务参数（文件类型保留 fileId 作为 value）
      values.parameters = taskParameters.filter(p => p.key && (p.value || p.file));
      if (editingTask) {
        await scheduledTaskApi.updateTask(editingTask.id, values);
        message.success('定时任务已更新');
      } else {
        await scheduledTaskApi.createTask(values);
        message.success('定时任务已创建');
      }
      setIsFormVisible(false);
      fetchTasks();
    } catch (error: any) {
      if (error.response) {
        const msg = error.response.data?.message || String(error.response.status);
        message.error('操作失败: ' + msg);
      } else if (!error.errorFields) {
        message.error('操作失败');
      }
    }
  };

  const handleViewLog = async (task: ScheduledTask) => {
    // 重新获取任务最新数据（避免显示缓存的旧日志）
    try {
      const response = await scheduledTaskApi.getTasks();
      if (response.code === 200 && response.data) {
        const latest = response.data.find((t: ScheduledTask) => t.id === task.id);
        setSelectedTask(latest || task);
        const resultToParse = (latest || task).last_run_result;
        if (resultToParse) {
          setParsedLog(JSON.parse(resultToParse));
        } else {
          setParsedLog([]);
        }
      }
    } catch (error) {
      // 回退到本地数据
      setSelectedTask(task);
      if (task.last_run_result) {
        try {
          setParsedLog(JSON.parse(task.last_run_result));
        } catch {
          setParsedLog([]);
        }
      } else {
        setParsedLog([]);
      }
    }
    setLogModalVisible(true);
  };

  const getNextExecutionTime = (task: ScheduledTask): string => {
    if (!task.enabled) {
      return '已禁用';
    }

    if (task.schedule_type === 'interval') {
      if (task.last_run_at) {
        const lastRun = new Date(task.last_run_at);
        const nextRun = new Date(lastRun.getTime() + task.interval_seconds * 1000);
        return nextRun.toLocaleString();
      } else {
        // 如果没有上次执行时间，显示"立即执行"
        return '立即执行';
      }
    } else if (task.schedule_type === 'cron') {
      // 对于 Cron 表达式，这里简化处理，实际项目中可能需要使用 Cron 解析库
      return '按 Cron 表达式';
    }

    return '-';
  };

  const columns = [
    {
      title: '任务名称',
      dataIndex: 'name',
      key: 'name',
      render: (text: string, record: ScheduledTask) => (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className={`stm-status-dot ${record.enabled ? 'is-running' : 'is-disabled'}`} />
          <Text strong ellipsis style={{ color: record.enabled ? 'var(--color-text)' : 'var(--color-text-tertiary)' }}>{text}</Text>
        </div>
      ),
      ellipsis: true,
    },
    {
      title: '调度规则',
      key: 'schedule',
      render: (_: any, record: ScheduledTask) => (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className={`stm-schedule-badge stm-schedule-badge--${record.schedule_type}`}>
            {record.schedule_type === 'interval' ? '间隔' : 'Cron'}
          </span>
          <span style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>
            {record.schedule_type === 'interval'
              ? `${record.interval_seconds}s`
              : (record.cron_expression || '-')}
          </span>
        </div>
      ),
      width: 160,
    },
    {
      title: '执行环境',
      key: 'environment',
      render: (_: any, record: ScheduledTask) => {
        const env = environments.find(e => e.id === record.environment_id);
        return <span style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>{env ? env.name : '-'}</span>;
      },
      width: 100,
      ellipsis: true,
    },
    {
      title: '上次执行',
      dataIndex: 'last_run_at',
      key: 'last_run_at',
      render: (last_run_at: string | null) => {
        if (last_run_at) {
          const d = new Date(last_run_at);
          return (
            <div>
              <div className="stm-time">{d.toLocaleDateString()} {d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</div>
            </div>
          );
        }
        return <span style={{ fontSize: 12, color: 'var(--color-text-disabled)' }}>从未执行</span>;
      },
      width: 130,
    },
    {
      title: '操作',
      key: 'action',
      render: (_: any, record: ScheduledTask) => (
        <div className="stm-actions">
          <button className="stm-action-btn stm-action-btn--primary" onClick={() => handleRunTask(record.id)}>执行</button>
          <button className={`stm-action-btn stm-action-btn--toggle ${record.enabled ? 'is-enabled' : 'is-disabled'}`} onClick={() => handleToggleTask(record)}>
            {record.enabled ? '禁用' : '启用'}
          </button>
          <span className="stm-action-divider" />
          <button className="stm-action-btn stm-action-btn--info" onClick={() => handleViewLog(record)}>日志</button>
          <button className="stm-action-btn stm-action-btn--warning" onClick={() => handleOpenEdit(record)}>编辑</button>
          <button className="stm-action-btn stm-action-btn--success" onClick={() => handleCopyTask(record)}>复制</button>
          <Popconfirm
            title="确定要删除这个任务吗？"
            onConfirm={() => handleDeleteTask(record.id)}
            okText="确定"
            cancelText="取消"
          >
            <button className="stm-action-btn stm-action-btn--danger">删除</button>
          </Popconfirm>
        </div>
      ),
      width: 280,
      fixed: 'right' as const,
    }
  ];

  return (
    <div className="stm-container">
      <Card title="定时任务管理" bordered={false} className="stm-card"
        bodyStyle={{ flex: 1, display: 'flex', flexDirection: 'column', padding: '12px 16px', overflow: 'hidden' }}>
        <div className="stm-filter-bar">
          <label>任务名称</label>
          <Input.Search
            placeholder="搜索任务名称..."
            allowClear
            size="small"
            onChange={(e) => setSearchTask(e.target.value)}
            onSearch={(val) => setSearchTask(val)}
          />
          <div style={{ flex: 1 }} />
          <span style={{ fontSize: 12, color: 'var(--color-text-tertiary)' }}>
            {tasks.filter(t => t.enabled).length}/{tasks.length} 运行中
          </span>
          <Button type="primary" size="small" icon={<PlusOutlined />} onClick={handleOpenCreate}>创建定时任务</Button>
        </div>

        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }} className="stm-table">
          <Table
            columns={columns}
            dataSource={tasks.filter(t => !searchTask || t.name.toLowerCase().includes(searchTask.toLowerCase()))}
            rowKey="id"
            loading={loading}
            size="small"
            rowClassName={(record) => record.enabled ? '' : 'stm-row-disabled'}
            pagination={{
              pageSize: 15,
              size: 'small',
              showTotal: (total) => <span style={{ color: 'var(--color-text-tertiary)', fontSize: 12 }}>共 {total} 条</span>,
            }}
            scroll={{ y: 'calc(100vh - 320px)' }}
          />
        </div>

      <Modal
        title={
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span>执行日志</span>
            {selectedTask && (
              <span style={{ fontSize: 13, fontWeight: 400, color: 'var(--color-text-secondary)' }}>
                {selectedTask.name}
              </span>
            )}
          </div>
        }
        open={logModalVisible}
        onCancel={() => setLogModalVisible(false)}
        footer={null}
        width={840}
        styles={{ body: { maxHeight: 'calc(100vh - 180px)', overflowY: 'auto', padding: '12px 20px' } }}
      >
        {parsedLog.length > 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {parsedLog.map((entry, index) => {
              const isSuccess = entry.status === 'success';
              const statusCode = entry.response?.status_code || entry.status_code;
              const method = entry.request?.method;
              const url = entry.request?.url;

              return (
                <div key={index} className={`stm-log-entry ${isSuccess ? 'is-success' : 'is-error'}`}>
                  {/* Compact summary line */}
                  <div className="stm-log-summary">
                    <span className={`stm-log-status-dot ${isSuccess ? 'is-success' : 'is-error'}`} />
                    <span className="stm-log-summary-name">{entry.request_name || `请求 #${entry.request_id}`}</span>
                    {method && <span className="stm-log-method">{method}</span>}
                    {statusCode && (
                      <span className={`stm-log-status-code ${isSuccess ? 'is-success' : 'is-error'}`}>
                        {statusCode}
                      </span>
                    )}
                    {!isSuccess && entry.detail && (
                      <span className="stm-log-error-hint">{entry.detail.slice(0, 60)}{entry.detail.length > 60 ? '...' : ''}</span>
                    )}
                  </div>

                  {/* Request URL — always visible, compact */}
                  {url && (
                    <div className="stm-log-url">
                      <span className="stm-log-url-text">{url}</span>
                    </div>
                  )}

                  {/* Collapsible details */}
                  {(entry.request?.headers || entry.request?.body || entry.request?.params || entry.response || entry.extracted) && (
                    <details className="stm-log-details">
                      <summary className="stm-log-details-toggle">查看详情</summary>
                      <div className="stm-log-details-body">
                        {/* Headers */}
                        {entry.request?.headers && Object.keys(entry.request.headers).length > 0 && (
                          <div className="stm-log-detail-section">
                            <div className="stm-log-detail-label">请求头</div>
                            <div className="stm-log-kv-list">
                              {Object.entries(entry.request.headers).map(([key, value], idx) => (
                                <div key={idx} className="stm-log-kv-item">
                                  <span className="stm-log-kv-key">{key}</span>
                                  <span className="stm-log-kv-value">{String(value)}</span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* Params */}
                        {entry.request?.params && Object.keys(entry.request.params).length > 0 && (
                          <div className="stm-log-detail-section">
                            <div className="stm-log-detail-label">请求参数</div>
                            <div className="stm-log-kv-list">
                              {Object.entries(entry.request.params).map(([key, value], idx) => (
                                <div key={idx} className="stm-log-kv-item">
                                  <span className="stm-log-kv-key">{key}</span>
                                  <span className="stm-log-kv-value">{String(value)}</span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* Request Body */}
                        {entry.request?.body && (
                          <div className="stm-log-detail-section">
                            <div className="stm-log-detail-label">请求体</div>
                            <pre className="stm-log-code-block">
                              {typeof entry.request.body === 'object' ? JSON.stringify(entry.request.body, null, 2) : entry.request.body}
                            </pre>
                          </div>
                        )}

                        {/* Response */}
                        {entry.response && (
                          <div className="stm-log-detail-section">
                            <div className="stm-log-detail-label" style={{ color: isSuccess ? 'var(--color-success)' : 'var(--color-danger)' }}>
                              响应体 {statusCode && `(${statusCode})`}
                            </div>
                            {entry.response.body !== undefined && entry.response.body !== null && (
                              <pre className="stm-log-code-block">
                                {typeof entry.response.body === 'object'
                                  ? JSON.stringify(entry.response.body, null, 2)
                                  : String(entry.response.body)}
                              </pre>
                            )}
                          </div>
                        )}

                        {/* Extracted vars */}
                        {entry.extracted && Object.keys(entry.extracted).length > 0 && (
                          <div className="stm-log-detail-section">
                            <div className="stm-log-detail-label">提取的变量</div>
                            <div className="stm-log-kv-list">
                              {Object.entries(entry.extracted).map(([key, value], idx) => (
                                <div key={idx} className="stm-log-kv-item">
                                  <span className="stm-log-kv-key">{key}</span>
                                  <span className="stm-log-kv-value" style={{ color: 'var(--color-success)' }}>{String(value)}</span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* Error detail */}
                        {entry.detail && (
                          <div className="stm-log-detail-section">
                            <div className="stm-log-detail-label" style={{ color: 'var(--color-danger)' }}>错误详情</div>
                            <pre className="stm-log-code-block" style={{ borderColor: 'var(--color-danger)', background: 'var(--color-danger-bg)' }}>
                              {entry.detail}
                            </pre>
                          </div>
                        )}
                      </div>
                    </details>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <div style={{ textAlign: 'center', padding: '48px 0', color: 'var(--color-text-disabled)' }}>
            <div style={{ fontSize: 32, marginBottom: 12, opacity: 0.3 }}>📋</div>
            <div>暂无执行日志</div>
          </div>
        )}
      </Modal>

      <Modal
        title={editingTask ? '编辑定时任务' : '创建定时任务'}
        open={isFormVisible}
        onOk={handleFormSubmit}
        onCancel={() => setIsFormVisible(false)}
        width={640}
        styles={{ body: { maxHeight: 'calc(100vh - 200px)', overflowY: 'auto', padding: '16px 20px' } }}
        destroyOnClose
      >
        <Form form={form} layout="vertical" style={{ marginBottom: 0 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 16px' }}>
            <Form.Item name="name" label="任务名称" rules={[{ required: true, message: '请输入任务名称' }]}
              style={{ gridColumn: '1 / -1' }} >
              <Input placeholder="请输入任务名称" style={{ width: '100%' }} />
            </Form.Item>

            <Form.Item name="schedule_type" label="调度类型">
              <Select style={{ width: '100%' }}>
                <Select.Option value="interval">间隔执行</Select.Option>
                <Select.Option value="cron">Cron 表达式</Select.Option>
              </Select>
            </Form.Item>

          <Form.Item noStyle shouldUpdate={(prev, cur) => prev.schedule_type !== cur.schedule_type}>
            {({ getFieldValue }) =>
              getFieldValue('schedule_type') === 'cron' ? (
                <Form.Item name="cron_expression" label="Cron 表达式" rules={[{ required: true, message: '请输入 Cron 表达式' }]}
                  extra="格式: 分 时 日 月 星期，如 0 8 * * * 表示每天8点"
                >
                  <Input placeholder="0 8 * * *" style={{ width: '100%' }} />
                </Form.Item>
              ) : (
                <Form.Item name="interval_seconds" label="执行间隔（秒）" rules={[{ required: true }]}>
                  <InputNumber min={1} max={86400} style={{ width: '100%' }} />
                </Form.Item>
              )
            }
          </Form.Item>
          </div>

          <Form.Item label="关联请求" extra="请按顺序添加请求，执行时将按添加顺序串行执行" style={{ marginBottom: 12 }}>
            <div>
              {/* 已添加的请求列表 */}
              <Form.Item noStyle name="request_ids" rules={[{ required: true, message: '请选择至少一个请求' }]}>
                <div>
                  {requestIds.map((requestId: number, index: number) => {
                    const request = savedRequests.find(r => r.id === requestId);

                    const moveUp = () => {
                      if (index > 0) {
                        const newIds = [...requestIds];
                        [newIds[index], newIds[index - 1]] = [newIds[index - 1], newIds[index]];
                        setRequestIds(newIds);
                        form.setFieldsValue({ request_ids: newIds });
                      }
                    };

                    const moveDown = () => {
                      if (index < requestIds.length - 1) {
                        const newIds = [...requestIds];
                        [newIds[index], newIds[index + 1]] = [newIds[index + 1], newIds[index]];
                        setRequestIds(newIds);
                        form.setFieldsValue({ request_ids: newIds });
                      }
                    };

                    const remove = () => {
                      const newIds = requestIds.filter(id => id !== requestId);
                      setRequestIds(newIds);
                      form.setFieldsValue({ request_ids: newIds });
                    };

                    return (
                      <div key={requestId} className="stm-request-item">
                        <span className="stm-request-item-index">{index + 1}</span>
                        <span className="stm-request-item-name">{request?.name || `请求 ${requestId}`}</span>
                        <div className="stm-request-item-actions">
                          {index > 0 && <Button size="small" type="text" onClick={moveUp}>↑</Button>}
                          {index < requestIds.length - 1 && <Button size="small" type="text" onClick={moveDown}>↓</Button>}
                          <Button size="small" type="text" danger onClick={remove}>×</Button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </Form.Item>

              {/* 添加请求的选择器 */}
              <div style={{ display: 'flex', marginTop: 8, gap: 8 }}>
                <Select
                  placeholder="选择要添加的请求"
                  style={{ flex: 1 }}
                  value={selectedRequestToAdd}
                  onChange={setSelectedRequestToAdd}
                  showSearch
                  optionFilterProp="label"
                  filterOption={(input, option) =>
                    (option?.label || '').toLowerCase().includes(input.toLowerCase())
                  }
                  options={savedRequests
                    .filter(r => !requestIds.includes(r.id))
                    .map(r => ({ label: r.name, value: r.id }))}
                />
                <Button
                  type="primary"
                  onClick={() => {
                    if (selectedRequestToAdd) {
                      if (!requestIds.includes(selectedRequestToAdd)) {
                        const newIds = [...requestIds, selectedRequestToAdd];
                        setRequestIds(newIds);
                        form.setFieldsValue({ request_ids: newIds });
                        setSelectedRequestToAdd(undefined);
                      }
                    }
                  }}
                >
                  添加
                </Button>
              </div>
            </div>
          </Form.Item>

          <Form.Item name="environment_id" label="执行环境"
            extra="执行时使用的环境参数，用于变量替换和后置提取"
            style={{ marginBottom: 12 }}
          >
            <Select placeholder="选择环境" allowClear style={{ width: '100%' }}
              options={environments.map(e => ({ label: e.name, value: e.id || undefined }))}
            />
          </Form.Item>

          <Form.Item
            label="任务参数"
            extra="配置任务级参数，优先级高于执行环境中的同名参数"
            style={{ marginBottom: 0 }}
          >
            <div style={{ background: 'var(--color-bg)', padding: 10, borderRadius: 'var(--radius-sm)', border: '1px solid var(--color-border-light)' }}>
              {taskParameters.map((param, index) => (
                <div key={index} style={{ display: 'flex', gap: 6, alignItems: 'flex-start', marginBottom: 6 }}>
                  <Radio.Group
                    value={param.type || 'text'}
                    size="small"
                    optionType="button"
                    buttonStyle="solid"
                    options={[
                      { label: '文本', value: 'text' },
                      { label: '文件', value: 'file' },
                    ]}
                    onChange={(e) => handleTaskParamTypeChange(index, e.target.value as 'text' | 'file')}
                  />
                  <Input
                    placeholder="参数名"
                    value={param.key}
                    onChange={e => {
                      const newParams = [...taskParameters];
                      newParams[index] = { ...newParams[index], key: e.target.value };
                      setTaskParameters(newParams);
                    }}
                    style={{ width: 140 }}
                  />
                  {param.type === 'file' ? (
                    <div style={{ flex: 1, minWidth: 240 }}>
                      <FileUpload
                        value={param.file ? [param.file] : []}
                        onChange={(files) => handleTaskParamFileChange(index, files?.[0])}
                        maxSize={50}
                        onUploadSuccess={(result) => {
                          const newParams = [...taskParameters];
                          newParams[index] = { ...newParams[index], file: result, value: result.fileId };
                          setTaskParameters(newParams);
                        }}
                        baseUrl={envBaseUrl}
                        accessToken={envAccessToken}
                      />
                    </div>
                  ) : (
                    <Input
                      placeholder="参数值"
                      value={param.value}
                      onChange={e => {
                        const newParams = [...taskParameters];
                        newParams[index] = { ...newParams[index], value: e.target.value };
                        setTaskParameters(newParams);
                      }}
                      style={{ flex: 1 }}
                    />
                  )}
                  <Button
                    danger
                    icon={<DeleteOutlined />}
                    onClick={() => setTaskParameters(taskParameters.filter((_, i) => i !== index))}
                  />
                </div>
              ))}
              <Button
                type="dashed"
                block
                icon={<PlusOutlined />}
                onClick={() => setTaskParameters([...taskParameters, { key: '', value: '' }])}
                style={{ marginTop: taskParameters.length > 0 ? 4 : 0 }}
              >
                新增参数
              </Button>
            </div>
          </Form.Item>
        </Form>
      </Modal>
    </Card>
    </div>
  );
};

export default ScheduledTaskManager;
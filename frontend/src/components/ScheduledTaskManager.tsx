import React, { useState, useEffect } from 'react';
import {
  Table,
  Button,
  message,
  Modal,
  Card,
  Descriptions,
  Tag,
  Space,
  Typography,
  Divider,
  Popconfirm,
  Form,
  Input,
  Select,
  InputNumber
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { scheduledTaskApi } from '../services/api';
import type { ScheduledTask } from '../types';
import axios from 'axios';

const { Text, Paragraph } = Typography;

const ScheduledTaskManager: React.FC = () => {
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [logModalVisible, setLogModalVisible] = useState(false);
  const [selectedTask, setSelectedTask] = useState<ScheduledTask | null>(null);
  const [parsedLog, setParsedLog] = useState<any[]>([]);
  const [isFormVisible, setIsFormVisible] = useState(false);
  const [editingTask, setEditingTask] = useState<ScheduledTask | null>(null);
  const [savedRequests, setSavedRequests] = useState<any[]>([]);
  const [environments, setEnvironments] = useState<any[]>([]);
  const [selectedRequestToAdd, setSelectedRequestToAdd] = useState<number | undefined>(undefined);
  const [requestIds, setRequestIds] = useState<number[]>([]);
  const [form] = Form.useForm();

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
      const response = await axios.get('/api/saved-requests');
      if (response.data.code === 200 && response.data.data) {
        setSavedRequests(response.data.data);
      }
    } catch (error) {
      console.error('Failed to fetch saved requests:', error);
    }
  };

  const fetchEnvironments = async () => {
    try {
      const response = await axios.get('/api/global-parameters');
      if (response.data.code === 200 && response.data.data) {
        setEnvironments(response.data.data);
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
    setSelectedRequestToAdd(undefined);
    setIsFormVisible(true);
    message.info('已复制任务，请修改并保存');
  };

  const handleOpenCreate = () => {
    setEditingTask(null);
    form.resetFields();
    form.setFieldsValue({ name: '', schedule_type: 'interval', interval_seconds: 60, request_ids: [], environment_id: undefined });
    setRequestIds([]);
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
    setSelectedRequestToAdd(undefined);
    setIsFormVisible(true);
  };

  const handleFormSubmit = async () => {
    try {
      // 确保 request_ids 字段的值与 requestIds 状态同步
      form.setFieldsValue({ request_ids: requestIds });
      const values = await form.validateFields();
      if (editingTask) {
        await axios.put(`/api/scheduled-tasks/${editingTask.id}`, values);
        message.success('定时任务已更新');
      } else {
        await axios.post('/api/scheduled-tasks', values);
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

  const handleViewLog = (task: ScheduledTask) => {
    setSelectedTask(task);
    if (task.last_run_result) {
      try {
        setParsedLog(JSON.parse(task.last_run_result));
      } catch (error) {
        setParsedLog([]);
      }
    } else {
      setParsedLog([]);
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
      render: (text: string) => (
        <Text strong ellipsis>{text}</Text>
      ),
      width: 120,
      ellipsis: true
    },
    {
      title: '调度类型',
      dataIndex: 'schedule_type',
      key: 'schedule_type',
      render: (text: string) => (
        <Tag color={text === 'interval' ? 'blue' : 'green'}>
          {text === 'interval' ? '间隔' : 'Cron'}
        </Tag>
      ),
      width: 80
    },
    {
      title: '执行频率',
      dataIndex: ['schedule_type', 'interval_seconds', 'cron_expression'],
      key: 'schedule',
      render: (_:any, record: ScheduledTask) => {
        if (record.schedule_type === 'interval') {
          return `${record.interval_seconds}秒`;
        } else {
          return record.cron_expression || '-';
        }
      },
      width: 100,
      ellipsis: true
    },
    {
      title: '执行环境',
      key: 'environment',
      render: (_:any, record: ScheduledTask) => {
        const environment = environments.find(env => env.id === record.environment_id);
        return environment ? environment.name : '-';
      },
      width: 90,
      ellipsis: true
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      key: 'enabled',
      render: (enabled: boolean) => (
        <Tag color={enabled ? 'success' : 'default'}>
          {enabled ? '运行中' : '已禁用'}
        </Tag>
      ),
      width: 70
    },
    {
      title: '上次执行',
      dataIndex: 'last_run_at',
      key: 'last_run_at',
      render: (last_run_at: string | null) => {
        if (last_run_at) {
          return <Text ellipsis>{new Date(last_run_at).toLocaleString()}</Text>;
        }
        return <Text type="secondary">从未执行</Text>;
      },
      width: 120,
      ellipsis: true
    },
    {
      title: '下次执行',
      key: 'next_execution',
      render: (_:any, record: ScheduledTask) => {
        const nextExecution = getNextExecutionTime(record);
        return <Text ellipsis>{nextExecution}</Text>;
      },
      width: 120,
      ellipsis: true
    },
    {
      title: '操作',
      key: 'action',
      render: (_:any, record: ScheduledTask) => (
        <Space size="small">
          <Button
            type="text"
            onClick={() => handleRunTask(record.id)}
            size="small"
            style={{ textDecoration: 'underline', color: '#1890ff' }}
          >
            执行
          </Button>
          <Button
            type="text"
            onClick={() => handleToggleTask(record)}
            size="small"
            style={{ textDecoration: 'underline', color: record.enabled ? '#ff4d4f' : '#52c41a' }}
          >
            {record.enabled ? '禁用' : '启用'}
          </Button>
          <Button
            type="text"
            onClick={() => handleViewLog(record)}
            size="small"
            style={{ textDecoration: 'underline', color: '#1890ff' }}
          >
            日志
          </Button>
          <Button
            type="text"
            onClick={() => handleOpenEdit(record)}
            size="small"
            style={{ textDecoration: 'underline', color: '#1890ff' }}
          >
            编辑
          </Button>
          <Button
            type="text"
            onClick={() => handleCopyTask(record)}
            size="small"
            style={{ textDecoration: 'underline', color: '#1890ff' }}
          >
            复制
          </Button>
          <Popconfirm
            title="确定要删除这个任务吗？"
            onConfirm={() => handleDeleteTask(record.id)}
            okText="确定"
            cancelText="取消"
          >
            <Button
              type="text"
              size="small"
              style={{ textDecoration: 'underline', color: '#ff4d4f' }}
            >
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
      width: 180
    }
  ];

  return (
    <Card title="定时任务管理" bordered={false}>
      <div style={{ marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleOpenCreate}>创建定时任务</Button>
      </div>
      <Table
        columns={columns}
        dataSource={tasks}
        rowKey="id"
        loading={loading}
        pagination={{ pageSize: 10 }}
      // size="small"
      />

      <Modal
        title={`${selectedTask?.name || ''} - 执行日志`}
        open={logModalVisible}
        onCancel={() => setLogModalVisible(false)}
        footer={[
          <Button key="close" onClick={() => setLogModalVisible(false)}>
            关闭
          </Button>
        ]}
        width={800}
        bodyStyle={{ maxHeight: 600, overflowY: 'auto', paddingRight: '16px' }}
      >
        {parsedLog.length > 0 ? (
          <div>
            {parsedLog.map((entry, index) => (
              <div key={index} style={{ marginBottom: 20 }}>
                <Descriptions bordered size="small">
                  <Descriptions.Item label="请求ID">{entry.request_id}</Descriptions.Item>
                  <Descriptions.Item label="请求名称">{entry.request_name}</Descriptions.Item>
                  <Descriptions.Item label="状态">
                    <Tag color={entry.status === 'success' ? 'success' : 'error'}>
                      {entry.status}
                    </Tag>
                  </Descriptions.Item>
                  {entry.status_code && (
                    <Descriptions.Item label="状态码">{entry.status_code}</Descriptions.Item>
                  )}
                </Descriptions>

                {entry.request && (
                  <div style={{ marginTop: 16, borderRadius: 6, border: '1px solid #e8e8e8', padding: 12, backgroundColor: '#f9f9f9' }}>
                    <Text strong style={{ fontSize: '14px', color: '#333' }}>请求详情</Text>
                    <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
                      <div style={{ display: 'flex', alignItems: 'flex-start' }}>
                        <Text style={{ width: 80, color: '#666', fontSize: '13px', fontWeight: 500 }}>方法:</Text>
                        <Text style={{ flex: 1, fontSize: '13px' }}>{entry.request.method}</Text>
                      </div>
                      <div style={{ display: 'flex', alignItems: 'flex-start' }}>
                        <Text style={{ width: 80, color: '#666', fontSize: '13px', fontWeight: 500 }}>URL:</Text>
                        <Text style={{ flex: 1, fontSize: '13px', wordBreak: 'break-all' }}>{entry.request.url}</Text>
                      </div>
                      {entry.request.headers && Object.keys(entry.request.headers).length > 0 && (
                        <div style={{ display: 'flex', alignItems: 'flex-start' }}>
                          <Text style={{ width: 80, color: '#666', fontSize: '13px', fontWeight: 500 }}>Headers:</Text>
                          <div style={{ flex: 1, fontSize: '12px' }}>
                            {Object.entries(entry.request.headers).map(([key, value], idx) => (
                              <div key={idx} style={{ padding: '2px 0', borderBottom: '1px solid #f0f0f0' }}>
                                <Text style={{ color: '#888' }}>{key}:</Text> <Text>{String(value)}</Text>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                      {entry.request.params && Object.keys(entry.request.params).length > 0 && (
                        <div style={{ display: 'flex', alignItems: 'flex-start' }}>
                          <Text style={{ width: 80, color: '#666', fontSize: '13px', fontWeight: 500 }}>Params:</Text>
                          <div style={{ flex: 1, fontSize: '12px' }}>
                            {Object.entries(entry.request.params).map(([key, value], idx) => (
                              <div key={idx} style={{ padding: '2px 0', borderBottom: '1px solid #f0f0f0' }}>
                                <Text style={{ color: '#888' }}>{key}:</Text> <Text>{String(value)}</Text>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                      {entry.request.body && (
                        <div style={{ display: 'flex', alignItems: 'flex-start' }}>
                          <Text style={{ width: 80, color: '#666', fontSize: '13px', fontWeight: 500 }}>Body:</Text>
                          <div style={{ flex: 1, fontSize: '12px', wordBreak: 'break-all', backgroundColor: '#f0f0f0', padding: 8, borderRadius: 4, border: '1px solid #e0e0e0' }}>
                            {typeof entry.request.body === 'object' ? JSON.stringify(entry.request.body, null, 2) : entry.request.body}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {entry.response && (
                  <div style={{ marginTop: 16, borderRadius: 6, border: '1px solid #b7eb8f', padding: 12, backgroundColor: '#f6ffed' }}>
                    <Text strong style={{ fontSize: '14px', color: '#333' }}>响应详情</Text>
                    <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
                      <div style={{ display: 'flex', alignItems: 'flex-start' }}>
                        <Text style={{ width: 80, color: '#666', fontSize: '13px', fontWeight: 500 }}>状态码:</Text>
                        <Text style={{ fontSize: '13px' }}>{entry.response.status_code}</Text>
                      </div>
                      {entry.response.body !== undefined && entry.response.body !== null && (
                        <div style={{ display: 'flex', alignItems: 'flex-start' }}>
                          <Text style={{ width: 80, color: '#666', fontSize: '13px', fontWeight: 500, flexShrink: 0 }}>响应体:</Text>
                          <div style={{ flex: 1, fontSize: '12px', wordBreak: 'break-all', backgroundColor: '#f0f0f0', padding: 8, borderRadius: 4, border: '1px solid #e0e0e0', maxHeight: 300, overflow: 'auto' }}>
                            <Paragraph style={{ margin: 0, fontSize: '12px', whiteSpace: 'pre-wrap' }}>
                              {typeof entry.response.body === 'object'
                                ? JSON.stringify(entry.response.body, null, 2)
                                : String(entry.response.body)}
                            </Paragraph>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {entry.extracted && Object.keys(entry.extracted).length > 0 && (
                  <div style={{ marginTop: 16, borderRadius: 6, border: '1px solid #e8e8e8', padding: 12, backgroundColor: '#f9f9f9' }}>
                    <Text strong style={{ fontSize: '14px', color: '#333' }}>提取的变量</Text>
                    <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 4 }}>
                      {Object.entries(entry.extracted).map(([key, value], idx) => (
                        <div key={idx} style={{ display: 'flex', alignItems: 'flex-start', padding: '2px 0', borderBottom: '1px solid #f0f0f0' }}>
                          <Text style={{ width: 120, color: '#666', fontSize: '13px', fontWeight: 500 }}>{key}:</Text>
                          <Text style={{ flex: 1, fontSize: '13px' }}>{String(value)}</Text>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {entry.detail && (
                  <div style={{ marginTop: 16, borderRadius: 6, border: '1px solid #ffccc7', padding: 12, backgroundColor: '#fff1f0' }}>
                    <Text strong style={{ fontSize: '14px', color: '#cf1322' }}>错误详情</Text>
                    <div style={{ marginTop: 8 }}>
                      <Paragraph style={{ margin: 0, color: '#cf1322', fontSize: '13px' }}>{entry.detail}</Paragraph>
                    </div>
                  </div>
                )}

                {index < parsedLog.length - 1 && <Divider />}
              </div>
            ))}
          </div>
        ) : (
          <Text type="secondary">暂无执行日志</Text>
        )}
      </Modal>

      <Modal
        title={editingTask ? '编辑定时任务' : '创建定时任务'}
        open={isFormVisible}
        onOk={handleFormSubmit}
        onCancel={() => setIsFormVisible(false)}
        width={500}
        bodyStyle={{ maxHeight: 600, overflowY: 'auto', paddingRight: '16px' }}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="任务名称" rules={[{ required: true, message: '请输入任务名称' }]}>
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

          <Form.Item label="关联请求" extra="请按顺序添加请求，执行时将按添加顺序串行执行">
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
                      <div key={requestId} style={{ display: 'flex', alignItems: 'center', marginBottom: 8, padding: 8, border: '1px solid #e8e8e8', borderRadius: 4, backgroundColor: '#f9f9f9' }}>
                        <span style={{ marginRight: 12, fontWeight: 500, width: 24, textAlign: 'center' }}>{index + 1}</span>
                        <span style={{ flex: 1 }}>{request?.name || `请求 ${requestId}`}</span>
                        <Space size="small">
                          {index > 0 && <Button size="small" onClick={moveUp}>↑</Button>}
                          {index < requestIds.length - 1 && <Button size="small" onClick={moveDown}>↓</Button>}
                          <Button danger size="small" onClick={remove}>删除</Button>
                        </Space>
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
          >
            <Select placeholder="选择环境" allowClear style={{ width: '100%' }}
              options={environments.map(e => ({ label: e.name, value: e.id || undefined }))}
            />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
};

export default ScheduledTaskManager;
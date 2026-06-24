import React, { useEffect, useState } from 'react';
import { Modal, Form, Input, Select, Tabs, Button, Space, Card, Switch } from 'antd';
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons';
import { TestCase, TestCaseStatus, GlobalParameter } from '../../types';
import { globalParameterApi, apiTestApi } from '../../services/api';

interface HeaderItem {
  key: string;
  value: string;
}

interface EditTestcaseModalProps {
  visible: boolean;
  selectedTestcase: TestCase | null;
  form: any;
  loading: boolean;
  onCancel: () => void;
  onFinish: (values: any) => void;
}

const EditTestcaseModal: React.FC<EditTestcaseModalProps> = ({
  visible,
  selectedTestcase,
  form,
  loading,
  onCancel,
  onFinish
}) => {
  const [apiCallEnabled, setApiCallEnabled] = useState(false);
  const [headers, setHeaders] = useState<HeaderItem[]>([{ key: '', value: '' }]);
  const [environmentId, setEnvironmentId] = useState<number | undefined>(undefined);
  const [environments, setEnvironments] = useState<GlobalParameter[]>([]);
  const [body, setBody] = useState<string>('');

  const firstEndpointId = (value: TestCase['api_endpoint_id']): number | null => {
    if (value === null || value === undefined) return null;
    const first = String(value).split(',')[0]?.trim();
    const parsed = Number(first);
    return Number.isFinite(parsed) ? parsed : null;
  };

  // 加载环境列表
  useEffect(() => {
    if (visible) {
      loadEnvironments();
    }
  }, [visible]);

  const loadEnvironments = async () => {
    try {
      const response = await globalParameterApi.getEnvironments();
      if (response.code === 200 && response.data) {
        setEnvironments(response.data);
      }
    } catch (error) {
      console.error('加载环境列表失败:', error);
    }
  };

  // 加载关联接口的 body
  const loadEndpointBody = async (projectId: number, endpointId: number) => {
    try {
      const response = await apiTestApi.getEndpoints(projectId);
      if (response.code === 200 && response.data) {
        const endpoint = response.data.find((ep: any) => ep.id === endpointId);
        if (endpoint?.body) {
          setBody(endpoint.body);
        }
      }
    } catch (error) {
      console.error('加载接口 body 失败:', error);
    }
  };

  useEffect(() => {
    if (selectedTestcase && visible) {
      // 检查是否有api_call配置
      const apiCallStep = selectedTestcase.steps.find(
        (step: any) => typeof step === 'object' && step.type === 'api_call'
      );

      if (apiCallStep && typeof apiCallStep === 'object') {
        setApiCallEnabled(true);
        setHeaders((apiCallStep as any).headers || [{ key: '', value: '' }]);
        setEnvironmentId((apiCallStep as any).environment_id);
        // 优先使用 api_call step 中的 body，否则从接口加载
        if ((apiCallStep as any).body) {
          setBody((apiCallStep as any).body);
        } else if (selectedTestcase.api_project_id && selectedTestcase.api_endpoint_id) {
          const endpointId = firstEndpointId(selectedTestcase.api_endpoint_id);
          if (endpointId) loadEndpointBody(selectedTestcase.api_project_id, endpointId);
        } else {
          setBody('');
        }
      } else {
        setApiCallEnabled(false);
        setHeaders([{ key: '', value: '' }]);
        setEnvironmentId(undefined);
        // 从关联接口加载 body
        if (selectedTestcase.api_project_id && selectedTestcase.api_endpoint_id) {
          const endpointId = firstEndpointId(selectedTestcase.api_endpoint_id);
          if (endpointId) loadEndpointBody(selectedTestcase.api_project_id, endpointId);
        } else {
          setBody('');
        }
      }

      form.setFieldsValue({
        case_name: selectedTestcase.case_name,
        case_level: selectedTestcase.case_level,
        status: selectedTestcase.status || TestCaseStatus.NOT_RUN,
        preset_conditions: selectedTestcase.preset_conditions
          .filter((s: any) => typeof s === 'string')
          .join('\n'),
        steps: selectedTestcase.steps
          .filter((step: any) => typeof step === 'string')
          .join('\n'),
        expected_results: selectedTestcase.expected_results.join('\n')
      });
    }
  }, [selectedTestcase, form, visible]);

  const handleAddHeader = () => {
    setHeaders([...headers, { key: '', value: '' }]);
  };

  const handleRemoveHeader = (index: number) => {
    const newHeaders = headers.filter((_, i) => i !== index);
    setHeaders(newHeaders.length > 0 ? newHeaders : [{ key: '', value: '' }]);
  };

  const handleHeaderChange = (index: number, field: 'key' | 'value', value: string) => {
    const newHeaders = [...headers];
    newHeaders[index] = { ...newHeaders[index], [field]: value };
    setHeaders(newHeaders);
  };

  const handleOk = async () => {
    try {
      const values = await form.validateFields();
      handleFinish(values);
    } catch (error) {
      // 表单验证失败
      console.error('表单验证失败:', error);
    }
  };

  const handleFinish = (values: any) => {
    // 构建steps数组
    const steps: any[] = [];

    // 添加文本步骤
    if (values.steps) {
      const textSteps = values.steps.split('\n').filter((s: string) => s.trim());
      steps.push(...textSteps);
    }

    // 添加api_call步骤（如果启用）
    if (apiCallEnabled && selectedTestcase?.api_endpoint_id) {
      const validHeaders = headers.filter(h => h.key.trim() !== '');
      // 从现有 api_call 步骤中提取 endpoint_id，或从 api_endpoint_id 取第一个
      const existingApiCall = Array.isArray(selectedTestcase.steps)
        ? selectedTestcase.steps.find((s: any) => typeof s === 'object' && s.type === 'api_call')
        : undefined;
      const existingEndpointId = existingApiCall
        ? (existingApiCall as any).endpoint_id
        : firstEndpointId(selectedTestcase.api_endpoint_id);
      steps.push({
        type: 'api_call',
        endpoint_id: existingEndpointId,
        headers: validHeaders,
        body: body || undefined,
        environment_id: environmentId
      });
    }

    // 处理前置条件：合并 textarea 文本 + 保留现有的 api_call 对象
    const textPresets = (values.preset_conditions || '')
      .split('\n')
      .filter((s: string) => s.trim());
    const objectPresets = (selectedTestcase?.preset_conditions || [])
      .filter((s: any) => typeof s !== 'string');

    // 调用原始onFinish，传递修改后的数据
    onFinish({
      ...values,
      preset_conditions: [...textPresets, ...objectPresets],
      steps
    });
  };

  return (
    <Modal
      title="编辑测试用例"
      open={visible}
      onCancel={onCancel}
      okText="保存"
      cancelText="取消"
      width={720}
      styles={{
        body: { padding: '16px 24px' },
      }}
      destroyOnClose
      confirmLoading={loading}
      onOk={handleOk}
    >
      {selectedTestcase && (
        <Form
          form={form}
          layout="vertical"
        >
          <Tabs
            defaultActiveKey="basic"
            items={[
              {
                key: 'basic',
                label: '基本信息',
                children: (
                  <>
                    <Form.Item
                      name="case_name"
                      label="用例名称"
                      rules={[{ required: true, message: '请输入用例名称' }]}
                    >
                      <Input placeholder="请输入用例名称" />
                    </Form.Item>

                    <Form.Item
                      name="case_level"
                      label="用例级别"
                      rules={[{ required: true, message: '请选择用例级别' }]}
                    >
                      <Select>
                        <Select.Option value={1}>P0</Select.Option>
                        <Select.Option value={2}>P1</Select.Option>
                        <Select.Option value={3}>P2</Select.Option>
                        <Select.Option value={4}>P3</Select.Option>
                      </Select>
                    </Form.Item>

                    <Form.Item
                      name="status"
                      label="测试状态"
                      rules={[{ required: true, message: '请选择测试状态' }]}
                    >
                      <Select disabled={selectedTestcase.status !== TestCaseStatus.NOT_RUN}>
                        <Select.Option value={TestCaseStatus.NOT_RUN}>未执行</Select.Option>
                        <Select.Option value={TestCaseStatus.PASSED}>已通过</Select.Option>
                        <Select.Option value={TestCaseStatus.FAILED}>未通过</Select.Option>
                      </Select>
                    </Form.Item>

                    <Form.Item
                      name="preset_conditions"
                      label="前置条件"
                      rules={[{ required: true, message: '请输入前置条件' }]}
                    >
                      <Input.TextArea
                        placeholder="请输入前置条件，每行一个条件"
                        rows={3}
                      />
                    </Form.Item>

                    <Form.Item
                      name="steps"
                      label="测试步骤"
                      rules={[{ required: true, message: '请输入测试步骤' }]}
                    >
                      <Input.TextArea
                        placeholder="请输入测试步骤，每行一个步骤"
                        rows={3}
                      />
                    </Form.Item>

                    <Form.Item
                      name="expected_results"
                      label="预期结果"
                      rules={[{ required: true, message: '请输入预期结果' }]}
                    >
                      <Input.TextArea
                        placeholder="请输入预期结果，每行一个结果"
                        rows={3}
                      />
                    </Form.Item>
                  </>
                )
              },
              {
                key: 'api',
                label: 'API调用配置',
                disabled: !selectedTestcase.api_endpoint_id,
                children: (
                  <div>
                    {!selectedTestcase.api_endpoint_id ? (
                      <div style={{ textAlign: 'center', padding: '40px 20px', color: 'var(--color-text-disabled)', fontSize: 13 }}>
                        该测试用例未关联 API 接口，无法配置 API 调用参数
                      </div>
                    ) : (
                      <>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16, padding: '10px 14px', background: 'var(--color-bg)', borderRadius: 'var(--radius-md)', border: '1px solid var(--color-border-light)' }}>
                          <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--color-text-secondary)' }}>启用 API 调用</span>
                          <Switch
                            checked={apiCallEnabled}
                            onChange={setApiCallEnabled}
                            size="small"
                          />
                        </div>

                        {apiCallEnabled && (
                          <>
                            {/* Headers */}
                            <div style={{ marginBottom: 16, padding: '12px 14px', background: 'var(--color-bg)', borderRadius: 'var(--radius-md)', border: '1px solid var(--color-border-light)' }}>
                              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                                <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text)' }}>请求 Headers</span>
                                <button
                                  type="button"
                                  onClick={handleAddHeader}
                                  style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 10px', border: '1px dashed var(--color-border)', borderRadius: 'var(--radius-sm)', background: 'transparent', color: 'var(--color-text-tertiary)', fontSize: 12, cursor: 'pointer' }}
                                >
                                  <PlusOutlined style={{ fontSize: 10 }} /> 添加
                                </button>
                              </div>
                              <div style={{ fontSize: 12, color: 'var(--color-text-tertiary)', marginBottom: 8 }}>
                                添加认证信息和其他请求头，如 Authorization: Bearer token
                              </div>
                              {headers.map((header, index) => (
                                <div key={index} style={{ display: 'flex', gap: 8, marginBottom: 6, alignItems: 'center' }}>
                                  <Input
                                    placeholder="Key"
                                    value={header.key}
                                    onChange={(e) => handleHeaderChange(index, 'key', e.target.value)}
                                    style={{ flex: 1, fontFamily: 'Consolas, Monaco, monospace', fontSize: 12 }}
                                  />
                                  <Input
                                    placeholder="Value"
                                    value={header.value}
                                    onChange={(e) => handleHeaderChange(index, 'value', e.target.value)}
                                    style={{ flex: 2, fontFamily: 'Consolas, Monaco, monospace', fontSize: 12 }}
                                  />
                                  <Button
                                    type="text"
                                    danger
                                    size="small"
                                    icon={<DeleteOutlined />}
                                    onClick={() => handleRemoveHeader(index)}
                                    style={{ flexShrink: 0 }}
                                  />
                                </div>
                              ))}

                              <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
                                <span style={{ fontSize: 12, color: 'var(--color-text-secondary)', whiteSpace: 'nowrap' }}>环境变量</span>
                                <Select
                                  placeholder="选择环境（可选）"
                                  value={environmentId}
                                  onChange={setEnvironmentId}
                                  allowClear
                                  size="small"
                                  style={{ width: 200 }}
                                >
                                  {environments.map(env => (
                                    <Select.Option key={env.id} value={env.id}>
                                      {env.name}
                                    </Select.Option>
                                  ))}
                                </Select>
                              </div>
                              {environmentId && (
                                <div style={{ marginTop: 6, fontSize: 12, color: 'var(--color-text-tertiary)' }}>
                                  选择环境后，可在 Headers 中使用 {'{{变量名}}'} 格式引用环境变量
                                </div>
                              )}
                            </div>

                            {/* Body */}
                            <div style={{ padding: '12px 14px', background: 'var(--color-bg)', borderRadius: 'var(--radius-md)', border: '1px solid var(--color-border-light)' }}>
                              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text)', marginBottom: 8 }}>请求 Body</div>
                              <div style={{ fontSize: 12, color: 'var(--color-text-tertiary)', marginBottom: 8 }}>
                                JSON 格式，留空则使用接口默认 Body
                              </div>
                              <Input.TextArea
                                value={body}
                                onChange={(e) => setBody(e.target.value)}
                                placeholder='{"key": "value"}'
                                rows={6}
                                style={{ fontFamily: 'Consolas, Monaco, monospace', fontSize: 13 }}
                              />
                            </div>
                          </>
                        )}
                      </>
                    )}
                  </div>
                )
              }
            ]}
          />
        </Form>
      )}
    </Modal>
  );
};

export default EditTestcaseModal;

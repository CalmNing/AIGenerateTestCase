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
          loadEndpointBody(selectedTestcase.api_project_id, selectedTestcase.api_endpoint_id);
        } else {
          setBody('');
        }
      } else {
        setApiCallEnabled(false);
        setHeaders([{ key: '', value: '' }]);
        setEnvironmentId(undefined);
        // 从关联接口加载 body
        if (selectedTestcase.api_project_id && selectedTestcase.api_endpoint_id) {
          loadEndpointBody(selectedTestcase.api_project_id, selectedTestcase.api_endpoint_id);
        } else {
          setBody('');
        }
      }

      form.setFieldsValue({
        case_name: selectedTestcase.case_name,
        case_level: selectedTestcase.case_level,
        status: selectedTestcase.status || TestCaseStatus.NOT_RUN,
        preset_conditions: selectedTestcase.preset_conditions.join('\n'),
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
      steps.push({
        type: 'api_call',
        headers: validHeaders,
        body: body || undefined,
        environment_id: environmentId
      });
    }

    // 调用原始onFinish，传递修改后的数据
    onFinish({
      ...values,
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
      width={800}
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
                      <div style={{ textAlign: 'center', padding: '20px', color: '#999' }}>
                        该测试用例未关联API接口，无法配置API调用参数
                      </div>
                    ) : (
                      <>
                        <div style={{ marginBottom: 16 }}>
                          <Space>
                            <span>启用API调用:</span>
                            <Switch
                              checked={apiCallEnabled}
                              onChange={setApiCallEnabled}
                            />
                          </Space>
                        </div>

                        {apiCallEnabled && (
                          <>
                          <Card title="请求Headers" size="small">
                            <div style={{ marginBottom: 8, color: '#666', fontSize: 12 }}>
                              添加认证信息和其他请求头，例如: Authorization: Bearer your_token
                            </div>
                            {headers.map((header, index) => (
                              <Space key={index} style={{ marginBottom: 8, display: 'flex' }}>
                                <Input
                                  placeholder="Header名称"
                                  value={header.key}
                                  onChange={(e) => handleHeaderChange(index, 'key', e.target.value)}
                                  style={{ width: 200 }}
                                />
                                <Input
                                  placeholder="Header值"
                                  value={header.value}
                                  onChange={(e) => handleHeaderChange(index, 'value', e.target.value)}
                                  style={{ width: 300 }}
                                />
                                <Button
                                  type="text"
                                  danger
                                  icon={<DeleteOutlined />}
                                  onClick={() => handleRemoveHeader(index)}
                                />
                              </Space>
                            ))}
                            <Button
                              type="dashed"
                              onClick={handleAddHeader}
                              icon={<PlusOutlined />}
                              style={{ width: '100%' }}
                            >
                              添加Header
                            </Button>

                            <div style={{ marginTop: 16 }}>
                              <Space>
                                <span>使用环境变量:</span>
                                <Select
                                  placeholder="选择环境（可选）"
                                  value={environmentId}
                                  onChange={setEnvironmentId}
                                  allowClear
                                  style={{ width: 200 }}
                                >
                                  {environments.map(env => (
                                    <Select.Option key={env.id} value={env.id}>
                                      {env.name}
                                    </Select.Option>
                                  ))}
                                </Select>
                              </Space>
                              {environmentId && (
                                <div style={{ marginTop: 8, color: '#666', fontSize: 12 }}>
                                  提示：选择环境后，可以在Headers中使用 {'{{变量名}}'} 格式引用环境变量
                                </div>
                              )}
                            </div>
                          </Card>

                          <Card title="请求Body" size="small" style={{ marginTop: 16 }}>
                            <div style={{ marginBottom: 8, color: '#666', fontSize: 12 }}>
                              请求体内容（JSON 格式），留空则使用接口默认 Body
                            </div>
                            <Input.TextArea
                              value={body}
                              onChange={(e) => setBody(e.target.value)}
                              placeholder='{"key": "value"}'
                              rows={6}
                              style={{ fontFamily: 'monospace' }}
                            />
                          </Card>
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

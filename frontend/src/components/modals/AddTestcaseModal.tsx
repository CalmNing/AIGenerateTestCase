import React from 'react';
import { Modal, Form, Input, Select, notification } from 'antd';
import { Session, Module } from '../../types';
import { testcaseApi } from '../../services/api';

const { TextArea } = Input;

interface AddTestcaseModalProps {
  visible: boolean;
  selectedSession: Session | null;
  modules: Module[];
  onCancel: () => void;
  onAddSuccess: () => void;
}

const AddTestcaseModal: React.FC<AddTestcaseModalProps> = ({
  visible,
  selectedSession,
  modules,
  onCancel,
  onAddSuccess
}) => {
  const [form] = Form.useForm();
  const [loading, setLoading] = React.useState(false);

  const handleOk = async () => {
    if (!selectedSession) return;

    try {
      setLoading(true);
      const values = await form.validateFields();
      
      // 处理数组字段
      const testcaseData = {
        case_name: values.case_name,
        case_level: values.case_level,
        preset_conditions: values.preset_conditions.split('\n').filter((item: string) => item.trim()),
        steps: values.steps.split('\n').filter((item: string) => item.trim()),
        expected_results: values.expected_results.split('\n').filter((item: string) => item.trim()),
        module_id: values.module_id || undefined,
        session_id: selectedSession.id
      };
      
      const response = await testcaseApi.createTestcase(
        selectedSession.id,
        testcaseData
      );
      
      if (response.code === 200) {
        notification.success({
          message: '创建成功',
          description: '测试用例已成功创建',
          placement: 'topRight'
        });
        form.resetFields();
        onAddSuccess();
        onCancel();
      } else {
        notification.error({
          message: '创建失败',
          description: response.message || '创建测试用例失败，请重试',
          placement: 'topRight'
        });
      }
    } catch (error: any) {
      console.error('创建测试用例失败:', error);
      notification.error({
        message: '创建失败',
        description: error.response?.data?.detail || '创建测试用例时发生错误，请重试',
        placement: 'topRight'
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal
      title="新增测试用例"
      open={visible}
      onOk={handleOk}
      onCancel={onCancel}
      confirmLoading={loading}
      okText="确认创建"
      cancelText="取消"
      width={600}
    >
      {!selectedSession && (
        <div style={{ marginBottom: 16, color: '#ff4d4f' }}>
          请先选择一个会话
        </div>
      )}
      
      <Form form={form} layout="vertical" disabled={!selectedSession}>
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
          initialValue={4}
        >
          <Select placeholder="请选择用例级别">
            <Select.Option value={1}>P1</Select.Option>
            <Select.Option value={2}>P2</Select.Option>
            <Select.Option value={3}>P3</Select.Option>
            <Select.Option value={4}>P4</Select.Option>
          </Select>
        </Form.Item>
        
        <Form.Item
          name="module_id"
          label="模块"
          rules={[{ required: true, message: '请选择模块' }]}
        >
          <Select placeholder="请选择模块">
            {modules.map(module => (
              <Select.Option key={module.id} value={module.id}>
                {module.module_name}
              </Select.Option>
            ))}
          </Select>
        </Form.Item>
        
        <Form.Item
          name="preset_conditions"
          label="前置条件"
          rules={[{ required: true, message: '请输入前置条件' }]}
        >
          <TextArea 
            placeholder="请输入前置条件，每行一个"
            rows={3}
          />
        </Form.Item>
        
        <Form.Item
          name="steps"
          label="测试步骤"
          rules={[{ required: true, message: '请输入测试步骤' }]}
        >
          <TextArea 
            placeholder="请输入测试步骤，每行一个"
            rows={4}
          />
        </Form.Item>
        
        <Form.Item
          name="expected_results"
          label="预期结果"
          rules={[{ required: true, message: '请输入预期结果' }]}
        >
          <TextArea 
            placeholder="请输入预期结果，每行一个"
            rows={3}
          />
        </Form.Item>
      </Form>
    </Modal>
  );
};

export default AddTestcaseModal;
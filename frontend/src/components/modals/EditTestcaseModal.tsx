import React from 'react';
import { Modal, Form, Input, Select } from 'antd';
import { TestCase } from '../../types';

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
  return (
    <Modal
      title="编辑测试用例"
      open={visible}
      onCancel={onCancel}
      okText="保存"
      cancelText="取消"
      width={800}
      confirmLoading={loading}
      onOk={() => form.submit()}
    >
      {selectedTestcase && (
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            case_name: selectedTestcase.case_name,
            case_level: selectedTestcase.case_level,
            status: selectedTestcase.status || 'pending',
            preset_conditions: selectedTestcase.preset_conditions.join('\n'),
            steps: selectedTestcase.steps.join('\n'),
            expected_results: selectedTestcase.expected_results.join('\n')
          }}
          onFinish={onFinish}
        >
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
            <Select>
              <Select.Option value="pending">待执行</Select.Option>
              <Select.Option value="completed">已执行</Select.Option>
            </Select>
          </Form.Item>

          <Form.Item
            name="preset_conditions"
            label="前置条件"
            rules={[{ required: true, message: '请输入前置条件' }]}
          >
            <Input.TextArea
              placeholder="请输入前置条件，每行一个条件"
              rows={4}
            />
          </Form.Item>

          <Form.Item
            name="steps"
            label="测试步骤"
            rules={[{ required: true, message: '请输入测试步骤' }]}
          >
            <Input.TextArea
              placeholder="请输入测试步骤，每行一个步骤"
              rows={6}
            />
          </Form.Item>

          <Form.Item
            name="expected_results"
            label="预期结果"
            rules={[{ required: true, message: '请输入预期结果' }]}
          >
            <Input.TextArea
              placeholder="请输入预期结果，每行一个结果"
              rows={4}
            />
          </Form.Item>
        </Form>
      )}
    </Modal>
  );
};

export default EditTestcaseModal;
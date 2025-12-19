import React from 'react';
import { Modal, Form, Input, Radio } from 'antd';

interface SettingsModalProps {
  visible: boolean;
  settingForm: any;
  settingType: 'api' | 'ollama';
  loading: boolean;
  onCancel: () => void;
  onFinish: (values: any) => void;
  onSettingTypeChange: (e: any) => void;
}

const SettingsModal: React.FC<SettingsModalProps> = ({
  visible,
  settingForm,
  settingType,
  loading,
  onCancel,
  onFinish,
  onSettingTypeChange
}) => {
  return (
    <Modal
      title="设置"
      open={visible}
      onCancel={onCancel}
      okText="保存"
      cancelText="取消"
      width={600}
      confirmLoading={loading}
      onOk={() => settingForm.submit()}
    >
      <Form
        form={settingForm}
        layout="vertical"
        onFinish={onFinish}
      >
        {/* 设置类型单选 */}
        <Form.Item
          name="setting_type"
          label="设置类型"
          rules={[{ required: true, message: '请选择设置类型' }]}
        >
          <Radio.Group onChange={onSettingTypeChange} defaultValue="api">
            <Radio value="api">使用API密钥</Radio>
            <Radio value="ollama">使用Ollama</Radio>
          </Radio.Group>
        </Form.Item>

        {/* API密钥设置 */}
        {settingType === 'api' && (
          <Form.Item
            name="api_key"
            label="DeepSeek API密钥"
            rules={[{ required: true, message: '请输入 DeepSeek API密钥' }]}
          >
            <Input.Password placeholder="请输入 DeepSeek API密钥" />
          </Form.Item>
        )}

        {/* Ollama设置 */}
        {settingType === 'ollama' && (
          <>
            <Form.Item
              name="ollama_url"
              label="Ollama URL"
              rules={[{ required: true, message: '请输入Ollama URL' }]}
            >
              <Input placeholder="请输入Ollama URL，如 http://localhost:11434" defaultValue={"http://localhost:11434"} />
            </Form.Item>

            <Form.Item
              name="ollama_model"
              label="Ollama模型"
              rules={[{ required: true, message: '请输入Ollama模型名称' }]}
            >
              <Input placeholder="请输入Ollama模型名称，如 llama3" defaultValue={"gpt-oss:120b-cloud"} />
            </Form.Item>
          </>
        )}
      </Form>
    </Modal>
  );
};

export default SettingsModal;
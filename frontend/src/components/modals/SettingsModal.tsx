import React, {useEffect} from 'react';
import { Modal, Form, Input, Radio, Divider, AutoComplete, Button, Space } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';

interface SettingsModalProps {
  visible: boolean;
  settingForm: any;
  settingType: 'api' | 'ollama';
  loading: boolean;
  onCancel: () => void;
  onFinish: (values: any) => void;
  onSettingTypeChange: (e: any) => void;
  modelList: string[];
  modelListLoading: boolean;
  onFetchModels: () => void;
}

const SettingsModal: React.FC<SettingsModalProps> = ({
  visible,
  settingForm,
  settingType,
  loading,
  onCancel,
  onFinish,
  onSettingTypeChange,
  modelList,
  modelListLoading,
  onFetchModels,
}) => {
  useEffect(() => {
    settingForm.setFieldsValue({
      api_base_url: settingForm.getFieldValue('api_base_url') || 'https://api.deepseek.com',
      api_model: settingForm.getFieldValue('api_model') || 'deepseek-v4-flash',
      ollama_url: settingForm.getFieldValue('ollama_url') || 'http://localhost:11434',
      ollama_model: settingForm.getFieldValue('ollama_model') || 'gpt-oss:120b-cloud'
    });
  }, []);
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
          <>
            <Form.Item
              name="api_key"
              label="API KEY"
              rules={[{ required: true, message: '请输入 API KEY' }]}
            >
              <Input.Password placeholder="请输入 API KEY" />
            </Form.Item>

            <Form.Item
              name="api_base_url"
              label="API Base URL"
              tooltip="OpenAI-compatible API 地址。默认直连 DeepSeek；也可填写 OneAPI/New API/LiteLLM 等兼容网关地址。"
            >
              <Input placeholder="https://api.deepseek.com" />
            </Form.Item>

            <Form.Item
              name="api_model"
              label="模型名称"
              rules={[{ required: true, message: '请输入模型名称' }]}
              tooltip="选择或输入模型名称。支持 OpenAI 兼容接口（DeepSeek、OpenAI、OneAPI 等）。点击右侧按钮获取可用模型列表。"
            >
              <Space.Compact style={{ width: '100%' }}>
                <AutoComplete
                  style={{ flex: 1 }}
                  placeholder="deepseek-v4-flash"
                  options={modelList.map(m => ({ label: m, value: m }))}
                  filterOption={(inputValue, option) =>
                    option?.value?.toLowerCase().includes(inputValue.toLowerCase()) ?? false
                  }
                  disabled={modelListLoading}
                  onSelect={(value) => {
                    settingForm.setFieldsValue({ api_model: value });
                  }}
                  onChange={(value) => {
                    settingForm.setFieldsValue({ api_model: value });
                  }}
                />
                <Button
                  icon={<ReloadOutlined spin={modelListLoading} />}
                  onClick={(e) => {
                    e.preventDefault();
                    onFetchModels();
                  }}
                  loading={modelListLoading}
                  title="获取模型列表"
                />
              </Space.Compact>
            </Form.Item>

            <Form.Item
              name="api_proxy_url"
              label="API Proxy URL"
              tooltip="HTTP/HTTPS 代理地址。Docker 容器访问宿主机代理通常使用 host.docker.internal，例如 http://host.docker.internal:7890。"
            >
              <Input placeholder="http://host.docker.internal:7890" />
            </Form.Item>
          </>
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

        <Divider />

        {/* 蓝湖配置 */}
        <Form.Item
          name="lanhu_cookie"
          label="蓝湖 Cookie"
          tooltip="登录蓝湖网页版后，从浏览器开发者工具中复制 Cookie 值。留空则使用服务器原有配置。"
        >
          <Input.Password placeholder="粘贴完整的蓝湖 Cookie" />
        </Form.Item>
      </Form>
    </Modal>
  );
};

export default SettingsModal;

import React, { useEffect, useState } from 'react';
import { Card, Form, Input, Button, Space, Switch, Select, message, Spin } from 'antd';
import { PlusOutlined, DeleteOutlined, SaveOutlined } from '@ant-design/icons';
import { Session, GlobalParameter } from '../../types';
import { sessionApi, globalParameterApi } from '../../services/api';

interface HeaderItem {
  key: string;
  value: string;
}

interface SessionApiConfigProps {
  selectedSession: Session | null;
  onConfigUpdated?: () => void;
}

const SessionApiConfig: React.FC<SessionApiConfigProps> = ({
  selectedSession,
  onConfigUpdated
}) => {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [apiConfigEnabled, setApiConfigEnabled] = useState(false);
  const [headers, setHeaders] = useState<HeaderItem[]>([{ key: '', value: '' }]);
  const [environmentId, setEnvironmentId] = useState<number | undefined>(undefined);
  const [environments, setEnvironments] = useState<GlobalParameter[]>([]);

  // 加载环境列表和会话配置
  useEffect(() => {
    if (selectedSession) {
      loadData();
    }
  }, [selectedSession]);

  const loadData = async () => {
    setLoading(true);
    try {
      // 并行加载环境列表和会话详情
      const [envResponse, sessionResponse] = await Promise.all([
        globalParameterApi.getEnvironments(),
        sessionApi.getSession(selectedSession!.id)
      ]);

      if (envResponse.code === 200 && envResponse.data) {
        setEnvironments(envResponse.data);
      }

      if (sessionResponse.code === 200 && sessionResponse.data) {
        const session = sessionResponse.data as Session;
        const apiConfig = (session as any).api_config;

        if (apiConfig) {
          setApiConfigEnabled(true);
          setHeaders(apiConfig.headers || [{ key: '', value: '' }]);
          setEnvironmentId(apiConfig.environment_id);
          form.setFieldsValue({
            apiConfigEnabled: true
          });
        } else {
          setApiConfigEnabled(false);
          setHeaders([{ key: '', value: '' }]);
          setEnvironmentId(undefined);
          form.setFieldsValue({
            apiConfigEnabled: false
          });
        }
      }
    } catch (error) {
      console.error('加载配置失败:', error);
    } finally {
      setLoading(false);
    }
  };

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

  const handleSave = async () => {
    if (!selectedSession) return;

    setSaving(true);
    try {
      const apiConfig = apiConfigEnabled
        ? {
            headers: headers.filter(h => h.key.trim() !== ''),
            environment_id: environmentId
          }
        : null;

      const response = await sessionApi.updateSession(selectedSession.id, {
        name: selectedSession.name,
        api_config: apiConfig
      });

      if (response.code === 200) {
        message.success('API调用配置已保存');
        onConfigUpdated?.();
      } else {
        message.error(response.message || '保存失败');
      }
    } catch (error) {
      console.error('保存配置失败:', error);
      message.error('保存失败，请重试');
    } finally {
      setSaving(false);
    }
  };

  if (!selectedSession) {
    return (
      <Card title="API调用配置">
        <div style={{ textAlign: 'center', padding: '20px', color: '#999' }}>
          请先选择一个会话
        </div>
      </Card>
    );
  }

  return (
    <Spin spinning={loading}>
      <Card
        title="API调用配置"
        extra={
          <span style={{ fontSize: 12, color: '#666' }}>
            当前会话: {selectedSession.name}
          </span>
        }
      >
        <div style={{ marginBottom: 16 }}>
          <div style={{ marginBottom: 8, color: '#666', fontSize: 12 }}>
            配置会话级别的API调用参数，该配置将应用于当前会话下所有未单独配置的测试用例
          </div>
          <Space>
            <span>启用会话级API配置:</span>
            <Switch
              checked={apiConfigEnabled}
              onChange={setApiConfigEnabled}
            />
          </Space>
        </div>

        {apiConfigEnabled && (
          <>
            <Card title="环境变量" size="small" style={{ marginBottom: 16 }}>
              <Space>
                <span>使用环境变量:</span>
                <Select
                  placeholder="选择环境（可选）"
                  value={environmentId}
                  onChange={setEnvironmentId}
                  allowClear
                  style={{ width: 300 }}
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
            </Card>

            <Card title="请求Headers" size="small" style={{ marginBottom: 16 }}>
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
            </Card>
          </>
        )}

        <div style={{ textAlign: 'right' }}>
          <Button
            type="primary"
            icon={<SaveOutlined />}
            onClick={handleSave}
            loading={saving}
          >
            保存配置
          </Button>
        </div>
      </Card>
    </Spin>
  );
};

export default SessionApiConfig;

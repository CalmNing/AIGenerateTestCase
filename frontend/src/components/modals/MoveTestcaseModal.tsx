import React, { useState, useEffect } from 'react';
import { Modal, Select, Form, notification } from 'antd';
import { Session, TestCase, Module } from '../../types';
import { sessionApi, moduleApi, testcaseApi } from '../../services/api';

interface MoveTestcaseModalProps {
  visible: boolean;
  testcase: TestCase | null;
  testcaseIds?: number[];
  onCancel: () => void;
  onMoveSuccess: () => void;
}

const MoveTestcaseModal: React.FC<MoveTestcaseModalProps> = ({
  visible,
  testcase,
  testcaseIds = [],
  onCancel,
  onMoveSuccess
}) => {
  const [form] = Form.useForm();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [modules, setModules] = useState<Module[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedSession, setSelectedSession] = useState<number>(0);

  // 加载会话列表
  useEffect(() => {
    if (visible) {
      loadSessions();
    }
  }, [visible]);

  // 加载模块列表
  useEffect(() => {
    if (selectedSession > 0) {
      loadModules(selectedSession);
    } else {
      setModules([]);
    }
  }, [selectedSession]);

  const loadSessions = async () => {
    try {
      const response = await sessionApi.getSessions();
      if (response.code === 200 && response.data) {
        setSessions(response.data);
        if (testcase && response.data.length > 0) {
          // 默认选中当前测试用例所在的会话
          const currentSession = response.data.find((s: Session) => s.id === testcase.session_id);
          if (currentSession) {
            setSelectedSession(currentSession.id);
            form.setFieldsValue({ session_id: currentSession.id });
          } else if (response.data.length > 0) {
            setSelectedSession(response.data[0].id);
            form.setFieldsValue({ session_id: response.data[0].id });
          }
        } else if (response.data.length > 0) {
          setSelectedSession(response.data[0].id);
          form.setFieldsValue({ session_id: response.data[0].id });
        }
      }
    } catch (error) {
      console.error('加载会话失败:', error);
      notification.error({
        message: '加载失败',
        description: '加载会话列表失败，请重试',
        placement: 'topRight'
      });
    }
  };

  const loadModules = async (sessionId: number) => {
    try {
      const response = await moduleApi.getModules(sessionId);
      if (response.code === 200 && response.data) {
        setModules(response.data);
        // 重置模块选择
        form.setFieldsValue({ module_id: undefined });
      }
    } catch (error) {
      console.error('加载模块失败:', error);
      notification.error({
        message: '加载失败',
        description: '加载模块列表失败，请重试',
        placement: 'topRight'
      });
    }
  };

  const handleSessionChange = (value: number) => {
    setSelectedSession(value);
  };

  const handleOk = async () => {
    if (!testcase && testcaseIds.length === 0) return;

    try {
      setLoading(true);
      const values = await form.validateFields();
      
      // 确保module_id正确处理，空字符串或undefined都转换为null
      const moduleId = values.module_id === '' || values.module_id === undefined ? null : values.module_id;
      
      let response;
      if (testcaseIds.length > 0) {
        // 批量移动
        response = await testcaseApi.batchMoveTestcase(
          testcaseIds,
          values.session_id,
          moduleId
        );
      } else if (testcase) {
        // 单个移动
        response = await testcaseApi.moveTestcase(
          testcase.id,
          values.session_id,
          moduleId
        );
      } else {
        throw new Error('请选择要移动的测试用例');
      }
      
      if (response.code === 200) {
        notification.success({
          message: '移动成功',
          description: response.message || '测试用例已成功移动',
          placement: 'topRight'
        });
        onMoveSuccess();
        onCancel();
      } else {
        notification.error({
          message: '移动失败',
          description: response.message || '移动测试用例失败，请重试',
          placement: 'topRight'
        });
      }
    } catch (error: any) {
      console.error('移动测试用例失败:', error);
      notification.error({
        message: '移动失败',
        description: error.response?.data?.detail || '移动测试用例时发生错误，请重试',
        placement: 'topRight'
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal
      title={testcaseIds.length > 0 ? `批量移动测试用例 (${testcaseIds.length}个)` : "移动测试用例"}
      open={visible}
      onOk={handleOk}
      onCancel={onCancel}
      confirmLoading={loading}
      okText="确认移动"
      cancelText="取消"
    >
      {testcaseIds.length > 0 ? (
        <div style={{ marginBottom: 16 }}>
          <p><strong>批量移动:</strong> 共 {testcaseIds.length} 个测试用例</p>
        </div>
      ) : testcase ? (
        <div style={{ marginBottom: 16 }}>
          <p><strong>当前测试用例:</strong> {testcase.case_name}</p>
          <p><strong>当前会话:</strong> {sessions.find(s => s.id === testcase.session_id)?.name || '未知'}</p>
        </div>
      ) : null}
      
      <Form form={form} layout="vertical">
        <Form.Item
          name="session_id"
          label="目标会话"
          rules={[{ required: true, message: '请选择目标会话' }]}
        >
          <Select
            placeholder="请选择目标会话"
            onChange={handleSessionChange}
            style={{ width: '100%' }}
          >
            {sessions.map(session => (
              <Select.Option key={session.id} value={session.id}>
                {session.name}
              </Select.Option>
    ))}
          </Select>
        </Form.Item>
        
        <Form.Item
          name="module_id"
          label="目标模块"
        >
          <Select
            placeholder="请选择目标模块（可选）"
            style={{ width: '100%' }}
          >
            <Select.Option value={undefined}>无模块</Select.Option>
            {modules.map(module => (
              <Select.Option key={module.id} value={module.id}>
                {module.module_name}
              </Select.Option>
            ))}
          </Select>
        </Form.Item>
      </Form>
    </Modal>
  );
};

export default MoveTestcaseModal;
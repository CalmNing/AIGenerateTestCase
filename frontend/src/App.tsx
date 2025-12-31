import React, { useState, useEffect } from 'react';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { Layout, notification, Form, Tabs } from 'antd';
import { ApiResponse, Session, TestCase, TestCaseResponse, TestCaseStatus } from './types';
import { sessionApi, testcaseApi } from './services/api';
import HeaderComponent from './components/HeaderComponent';
import SessionSidebar from './components/SessionSidebar';
import TestCaseGenerator from './components/TestCaseGenerator';
import TestCaseManager from './components/TestCaseManager';
import DeleteSessionModal from './components/modals/DeleteSessionModal';
import DeleteTestcaseModal from './components/modals/DeleteTestcaseModal';
import CompleteTestcaseModal from './components/modals/CompleteTestcaseModal';
import ViewTestcaseModal from './components/modals/ViewTestcaseModal';
import EditTestcaseModal from './components/modals/EditTestcaseModal';
import SettingsModal from './components/modals/SettingsModal';

const { Content } = Layout;

const App: React.FC = () => {

  // 状态管理
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selectedSession, setSelectedSession] = useState<Session | null>(null);
  const [testcases, setTestcases] = useState<TestCase[]>([]);
  const [testcasesResponse, setTestcasesResponse] = useState<TestCaseResponse>({ items: [], passed: 0, failed: 0, not_run: 0, totalNumber: 0 });
  const [newSessionName, setNewSessionName] = useState('');
  const [requirement, setRequirement] = useState('');
  const [loading, setLoading] = useState(false);
  const [selectedTestcase, setSelectedTestcase] = useState<TestCase | null>(null);
  const [isViewModalVisible, setIsViewModalVisible] = useState(false);
  const [isEditModalVisible, setIsEditModalVisible] = useState(false);
  const [activeTab, setActiveTab] = useState('generate');

  // 创建表单实例
  const [form] = Form.useForm();
  // 删除确认状态
  const [confirmDeleteVisible, setConfirmDeleteVisible] = useState(false);
  const [sessionToDelete, setSessionToDelete] = useState<number | null>(null);

  // 初始化数据
  useEffect(() => {
    loadSessions();
    setSelectedSession(selectedSession?.id ? selectedSession : null);
    // 从localStorage加载设置
    const savedSettings = localStorage.getItem('appSettings');
    if (savedSettings) {
      const settings = JSON.parse(savedSettings);
      settingForm.setFieldsValue(settings);
      setSettingType(settings.setting_type || 'api');
    }
  }, []);

  // 加载会话列表
  const loadSessions = async () => {
    try {
      const response: ApiResponse<Session[]> | any = await sessionApi.getSessions();
      if (response.code === 200 && response.data) {
        console.log('Setting sessions:', response.data);
        setSessions(response.data);
        if (response.data.length > 0) {
          setSelectedSession(response.data[0]);
          loadTestcases(response.data[0].id);
        }
      }
    } catch (error) {
      console.error('加载会话失败:', error);
    }
  };

  // 加载测试用例
  const loadTestcases = async (sessionId: number | any, filters?: { case_name?: string; status?: string }) => {
    try {
      const response: ApiResponse<TestCaseResponse> | any = await testcaseApi.getTestcases(sessionId, filters);
      if (response.code === 200 && response.data) {
        setTestcases(response.data.items);
        setTestcasesResponse(response.data);
      }
    } catch (error) {
      console.error('加载测试用例失败:', error);
    }
  };

  // 创建会话
  const handleCreateSession = async () => {
    if (!newSessionName.trim()) return;

    try {
      const response: ApiResponse<Session> | any = await sessionApi.createSession(newSessionName.trim());
      if (response.code === 200 && response.data) {
        setSessions([...sessions, response.data]);
        setSelectedSession(response.data);
        setNewSessionName('');
        loadTestcases(response.data.id);
        notification.success({
          message: '创建成功',
          description: '会话已成功创建',
          placement: 'topRight'
        });
      }
    } catch (error) {
      console.error('创建会话失败:', error);
      notification.error({
        message: '创建失败',
        description: '会话创建失败，请重试',
        placement: 'topRight'
      });
    }
  };

  // 删除会话 - 显示确认对话框
  const handleDeleteSession = (id: number) => {
    setSessionToDelete(id);
    setConfirmDeleteVisible(true);
  };

  // 确认删除会话
  const handleConfirmDeleteSession = async () => {
    if (!sessionToDelete) return;

    try {
      const response: ApiResponse<Session> | any = await sessionApi.deleteSession(sessionToDelete);
      if (response.code === 200) {
        setSessions(sessions.filter(session => session.id !== sessionToDelete));
        if (selectedSession?.id === sessionToDelete) {
          const sessionsId = sessions.length > 0 ? sessions[0] : null;
          setSelectedSession(sessionsId);
          loadTestcases(sessionsId?.id);
        }
      }
    } catch (error) {
      console.error('删除会话失败:', error);
    } finally {
      // 关闭确认对话框
      setConfirmDeleteVisible(false);
      setSessionToDelete(null);
    }
  };

  // 取消删除会话
  const handleCancelDeleteSession = () => {
    setConfirmDeleteVisible(false);
    setSessionToDelete(null);
  };

  // 选择会话
  const handleSelectSession = (session: Session) => {
    setSelectedSession(session);
    loadTestcases(session.id, filters);
  };

  // 查看测试用例
  const handleViewTestcase = (testcase: TestCase) => {
    setSelectedTestcase(testcase);
    setIsViewModalVisible(true);
  };

  // 编辑测试用例
  const handleEditTestcase = (testcase: TestCase) => {
    setSelectedTestcase(testcase);
    setIsEditModalVisible(true);
    // 重置表单
    form.resetFields();
  };

  // 表单提交处理
  const handleEditSubmit = async (values: any) => {
    if (!selectedTestcase || !selectedSession) return;

    try {
      setLoading(true);

      // 处理表单数据
      const updatedTestcase = {
        ...selectedTestcase,
        case_name: values.case_name,
        case_level: values.case_level,
        status: values.status as TestCaseStatus,
        preset_conditions: values.preset_conditions.split('\n').filter((item: string) => item.trim()),
        steps: values.steps.split('\n').filter((item: string) => item.trim()),
        expected_results: values.expected_results.split('\n').filter((item: string) => item.trim())
      };

      // 调用后端API更新测试用例
      const response: ApiResponse | any = await testcaseApi.updateTestcase(
        selectedSession.id,
        selectedTestcase.id,
        updatedTestcase
      );

      if (response.code === 200) {
        // 更新本地状态
        setTestcases(testcases.map(tc =>
          tc.id === selectedTestcase.id ? updatedTestcase : tc
        ));

        // 关闭模态框
        setIsEditModalVisible(false);

        // 显示成功通知
        notification.success({
          message: '更新成功',
          description: '测试用例已成功更新',
          placement: 'topRight'
        });
      }
    } catch (error) {
      console.error('更新测试用例失败:', error);
      notification.error({
        message: '更新失败',
        description: '测试用例更新失败，请重试',
        placement: 'topRight'
      });
    } finally {
      setLoading(false);
    }
  };

  // 测试用例删除状态
  const [confirmDeleteTestcaseVisible, setConfirmDeleteTestcaseVisible] = useState(false);
  const [testcaseToDelete, setTestcaseToDelete] = useState<number | null>(null);

  // 测试用例执行状态
  const [confirmCompleteTestcaseVisible, setConfirmCompleteTestcaseVisible] = useState(false);
  const [testcaseToComplete, setTestcaseToComplete] = useState<TestCase | null>(null);

  // 测试用例筛选条件
  const [filters, setFilters] = useState({
    case_name: '',
    status: ''
  });

  // 删除测试用例 - 显示确认对话框
  const handleDeleteTestcase = (id: number) => {
    setTestcaseToDelete(id);
    setConfirmDeleteTestcaseVisible(true);
  };

  // 确认删除测试用例
  const handleConfirmDeleteTestcase = async () => {
    if (!testcaseToDelete || !selectedSession) return;

    try {
      const response: any = await testcaseApi.deleteTestcase(selectedSession.id, testcaseToDelete);
      if (response.code === 200) {
        setTestcases(testcases.filter(testcase => testcase.id !== testcaseToDelete));
        loadTestcases(selectedSession.id);

        notification.success({
          message: '删除成功',
          description: '测试用例已成功删除',
          placement: 'topRight'
        });
      }
    } catch (error) {
      console.error('删除测试用例失败:', error);
      notification.error({
        message: '删除失败',
        description: '测试用例删除失败，请重试',
        placement: 'topRight'
      });
    } finally {
      setConfirmDeleteTestcaseVisible(false);
      setTestcaseToDelete(null);
    }
  };

  // 取消删除测试用例
  const handleCancelDeleteTestcase = () => {
    setConfirmDeleteTestcaseVisible(false);
    setTestcaseToDelete(null);
  };

  // 执行测试用例 - 显示确认对话框
  const handleCompleteTestcase = (testcase: TestCase) => {
    setTestcaseToComplete(testcase);
    setConfirmCompleteTestcaseVisible(true);
  };

  // 确认执行测试用例
  const handleConfirmCompleteTestcase = async (status: TestCaseStatus, bugId?: number) => {
    if (!testcaseToComplete || !selectedSession) return;

    try {
      // 更新测试用例状态
      const updatedTestcase = {
        ...testcaseToComplete,
        status: status,
        bug_id: bugId
      };

      // 调用后端API更新测试用例
      const response: any = await testcaseApi.updateTestcase(
        selectedSession.id,
        testcaseToComplete.id,
        updatedTestcase
      );

      if (response.code === 200) {
        // 更新本地状态
        setTestcases(testcases.map(tc =>
          tc.id === testcaseToComplete.id ? updatedTestcase : tc
        ));
        loadTestcases(selectedSession.id);
        // 显示成功通知
        notification.success({
          message: '执行成功',
          description: `测试用例已成功标记为${status === TestCaseStatus.PASSED ? '通过' : status === TestCaseStatus.FAILED ? '未通过' : '未运行'}`,
          placement: 'topRight'
        });
      }
    } catch (error) {
      console.error('执行测试用例失败:', error);
      notification.error({
        message: '执行失败',
        description: '测试用例标记执行失败，请重试',
        placement: 'topRight'
      });
    } finally {
      setConfirmCompleteTestcaseVisible(false);
      setTestcaseToComplete(null);
    }
  };

  // 取消执行测试用例
  const handleCancelCompleteTestcase = () => {
    setConfirmCompleteTestcaseVisible(false);
    setTestcaseToComplete(null);
  };

  // 设置功能
  const [isSettingModalVisible, setIsSettingModalVisible] = useState(false);
  const [settingForm] = Form.useForm();
  // 设置类型：api或ollama，单选
  const [settingType, setSettingType] = useState<'api' | 'ollama'>('api');

  // 打开设置模态框
  const handleOpenSettingModal = () => {
    setIsSettingModalVisible(true);
    // 如果没有保存的设置，设置默认值
    const savedSettings = localStorage.getItem('appSettings');
    if (!savedSettings) {
      settingForm.setFieldsValue({
        setting_type: 'api',
        api_key: '',
        ollama_url: 'http://localhost:11434',
        ollama_model: 'gpt-oss:120b-cloud'
      });
      setSettingType('api');
    }
  };

  // 设置类型改变处理
  const handleSettingTypeChange = (e: any) => {
    const value = e.target.value;
    setSettingType(value);
    // 重置表单对应的字段
    if (value === 'api') {
      settingForm.setFieldsValue({
        ollama_url: '',
        ollama_model: ''
      });
    } else {
      settingForm.setFieldsValue({
        api_key: ''
      });
    }
  };

  // 保存设置
  const handleSaveSetting = async (values: any) => {
    try {
      setLoading(true);

      // 保存到localStorage
      localStorage.setItem('appSettings', JSON.stringify(values));

      // 关闭模态框
      setIsSettingModalVisible(false);

      // 显示成功通知
      notification.success({
        message: '保存成功',
        description: '配置已成功保存到本地',
        placement: 'topRight'
      });
    } catch (error) {
      console.error('保存配置失败:', error);
      notification.error({
        message: '保存失败',
        description: '保存配置时发生错误，请重试',
        placement: 'topRight'
      });
    } finally {
      setLoading(false);
    }
  };

  // 生成测试用例
  const handleGenerateTestcases = async () => {
    if (!selectedSession || !requirement.trim()) return;

    try {
      setLoading(true);

      // 从localStorage获取模型配置
      const savedSettings = localStorage.getItem('appSettings');
      let modelConfig = undefined;
      if (!savedSettings) {
        handleOpenSettingModal();
      }
      else {
        const settings = JSON.parse(savedSettings);
        if (settings) {
          // if (settings.api_key == undefined && settings.api_key == '') {
          //   handleOpenSettingModal();
          // }
          modelConfig = {
            model_type: settings.setting_type,
            api_key: settings.api_key || '',
            ollama_url: settings.ollama_url || '',
            ollama_model: settings.ollama_model || ''
          };
        }

        const response: ApiResponse<TestCase> | any = await testcaseApi.generateTestcases(selectedSession.id, requirement.trim(), modelConfig);
        if (response.code === 200 && response.data) {
          loadTestcases(selectedSession.id);
          setRequirement('');
          notification.success({
            message: '生成成功',
            description: '测试用例已成功生成',
            placement: 'topRight'
          });
        }
      }

    } catch (error: any) {
      console.error('生成测试用例失败:', error);
      notification.error({
        message: '生成失败',
        description: `${error.response.data.detail || '生成测试用例时发生错误，请重试'}`,
        placement: 'topRight'
      });
    } finally {
      setLoading(false);
    }
  };



  return (
    <ConfigProvider locale={zhCN}>
      <Layout style={{ minHeight: '100vh' }}>
        <HeaderComponent onSettingsOpen={handleOpenSettingModal} />
        <Layout>
          <SessionSidebar
            sessions={sessions}
            selectedSession={selectedSession}
            newSessionName={newSessionName}
            onNewSessionNameChange={setNewSessionName}
            onCreateSession={handleCreateSession}
            onSelectSession={handleSelectSession}
            onDeleteSession={handleDeleteSession}
          />
          <Layout style={{ padding: '16px' }}>
            <Content style={{ background: '#fff', padding: '24px', margin: 0 }}>
              <Tabs
                activeKey={activeTab}
                onChange={setActiveTab}
                items={[
                  {
                    key: 'generate',
                    label: '生成测试用例',
                    children: (
                      <TestCaseGenerator
                        selectedSession={selectedSession}
                        requirement={requirement}
                        loading={loading}
                        onRequirementChange={setRequirement}
                        onGenerate={handleGenerateTestcases}
                      />
                    ),
                  },
                  {
                    key: 'manage',
                    label: '管理测试用例',
                    children: (
                      <TestCaseManager
                        testcasesResponse={testcasesResponse}
                        selectedSession={selectedSession}
                        testcases={testcases}
                        filters={filters}
                        onFiltersChange={setFilters}
                        onLoadTestcases={loadTestcases}
                        onView={handleViewTestcase}
                        onEdit={handleEditTestcase}
                        onComplete={handleCompleteTestcase}
                        onDelete={handleDeleteTestcase}
                      />
                    ),
                  },
                ]}
              />
            </Content>
          </Layout>
        </Layout>
      </Layout>

      <DeleteSessionModal
        visible={confirmDeleteVisible}
        onOk={handleConfirmDeleteSession}
        onCancel={handleCancelDeleteSession}
      />

      <DeleteTestcaseModal
        visible={confirmDeleteTestcaseVisible}
        onOk={handleConfirmDeleteTestcase}
        onCancel={handleCancelDeleteTestcase}
      />

      <CompleteTestcaseModal
        visible={confirmCompleteTestcaseVisible}
        onOk={handleConfirmCompleteTestcase}
        onCancel={handleCancelCompleteTestcase}
      />

      <ViewTestcaseModal
        visible={isViewModalVisible}
        selectedTestcase={selectedTestcase}
        onCancel={() => setIsViewModalVisible(false)}
        onComplete={handleCompleteTestcase}
      />

      <EditTestcaseModal
        visible={isEditModalVisible}
        selectedTestcase={selectedTestcase}
        form={form}
        loading={loading}
        onCancel={() => setIsEditModalVisible(false)}
        onFinish={handleEditSubmit}
      />

      <SettingsModal
        visible={isSettingModalVisible}
        settingForm={settingForm}
        settingType={settingType}
        loading={loading}
        onCancel={() => setIsSettingModalVisible(false)}
        onFinish={handleSaveSetting}
        onSettingTypeChange={handleSettingTypeChange}
      />

    </ConfigProvider>
  );
};

export default App;

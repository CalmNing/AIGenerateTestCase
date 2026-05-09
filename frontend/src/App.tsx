import React, { useState, useEffect } from 'react';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { Layout, notification, Form, Tabs, Modal, Input, Button, Select, Space, Popconfirm } from 'antd';
import { PlusOutlined, MinusOutlined, EditOutlined } from '@ant-design/icons';
import SubPlatformHeader from './components/SubPlatformHeader';
import { ApiResponse, Session, TestCase, TestCaseResponse, TestCaseStatus, Module, TestCaseFilters } from './types';
import { sessionApi, testcaseApi, moduleApi, historyPromptApi, globalParameterApi } from './services/api';

// 环境类型定义
interface Environment {
  id: string;
  name: string;
  parameters: Array<{ key: string; value: string }>;
  is_default?: boolean;
}
import HomePage from './components/HomePage';
import IoTDataPushPlatform from './components/IoTDataPushPlatform';
import IoTMockPlatform from './components/IoTMockPlatform';
import HeaderComponent from './components/HeaderComponent';
import SessionSidebar from './components/SessionSidebar';
import TestCaseGenerator from './components/TestCaseGenerator';
import TestCaseManager from './components/TestCaseManager';
import ScheduledTaskManager from './components/ScheduledTaskManager';
import DeleteSessionModal from './components/modals/DeleteSessionModal';
import DeleteTestcaseModal from './components/modals/DeleteTestcaseModal';
import CompleteTestcaseModal from './components/modals/CompleteTestcaseModal';
import ViewTestcaseModal from './components/modals/ViewTestcaseModal';
import EditTestcaseModal from './components/modals/EditTestcaseModal';
import SettingsModal from './components/modals/SettingsModal';
import MoveTestcaseModal from './components/modals/MoveTestcaseModal';
import AddTestcaseModal from './components/modals/AddTestcaseModal';
import ModuleSidebar from './components/ModuleSidebar';

const { Content } = Layout;

const App: React.FC = () => {

  // 导航状态管理
  const [currentPlatform, setCurrentPlatform] = useState<'home' | 'ai-testcase' | 'iot-mock' | 'scheduled-task' | 'mock-api'>(() => {
    // 从localStorage加载上次的平台状态
    const savedPlatform = localStorage.getItem('currentPlatform');
    return (savedPlatform as 'home' | 'ai-testcase' | 'iot-mock' | 'scheduled-task' | 'mock-api') || 'home';
  });

  // 状态管理
  const [sessions, setSessions] = useState<Session[]>([]);
  const [modules, setModules] = useState<Module[]>([]);
  const [selectedSession, setSelectedSession] = useState<Session | null>(null);
  const [selectedModule, setSelectedModule] = useState<number|string>(0);
  const [testcases, setTestcases] = useState<TestCase[]>([]);
  const [testcasesResponse, setTestcasesResponse] = useState<TestCaseResponse>({ items: [], passed: 0, failed: 0, not_run: 0, totalNumber: 0, totalBugs: 0 });
  const [newSessionName, setNewSessionName] = useState('');
  const [editSessionName, setEditSessionName] = useState('');
  const [newModuleName, setNewModuleName] = useState('');
  const [editModuleName, setEditModuleName] = useState('');
  const [newModuleParentId, setNewModuleParentId] = useState<number | null>(null);
  const [editModuleParentId, setEditModuleParentId] = useState<number | null>(null);
  const [requirement, setRequirement] = useState('');
  const [loading, setLoading] = useState(false);
  
  // 全局参数管理
  const [environments, setEnvironments] = useState<Environment[]>([{ id: 'env-1', name: '默认环境', parameters: [] }]);
  const [currentEnvironmentId, setCurrentEnvironmentId] = useState<string>(() => {
    return localStorage.getItem('currentEnvironmentId') || 'env-1';
  });
  const [isGlobalParamsModalVisible, setIsGlobalParamsModalVisible] = useState(false);
  const [newEnvironmentName, setNewEnvironmentName] = useState('');
  const [isAddEnvModalVisible, setIsAddEnvModalVisible] = useState(false);
  const [isEditEnvModalVisible, setIsEditEnvModalVisible] = useState(false);
  const [editingEnvironment, setEditingEnvironment] = useState<Environment | null>(null);
  const [editEnvironmentName, setEditEnvironmentName] = useState('');
  const [selectedTestcase, setSelectedTestcase] = useState<TestCase | null>(null);
  const [isViewModalVisible, setIsViewModalVisible] = useState(false);
  const [isEditModalVisible, setIsEditModalVisible] = useState(false);
  const [isEditSessionModalVisible, setIsEditSessionModalVisible] = useState(false);
  const [isEditModuleModalVisible, setIsEditModuleModalVisible] = useState(false);
  const [sessionToEdit, setSessionToEdit] = useState<Session | null>(null);
  const [moduleToEdit, setModuleToEdit] = useState<Module | null>(null);
  const [isAddModuleModalVisible, setIsAddModuleModalVisible] = useState(false);
  const [activeTab, setActiveTab] = useState('generate');
  // 图片状态管理
  const [imageBase64, setImageBase64] = useState<string | null>(null);
  // 历史提示词刷新key
  const [historyPromptRefreshKey, setHistoryPromptRefreshKey] = useState(0);

  // 创建表单实例
  const [form] = Form.useForm();
  // 删除确认状态
  const [confirmDeleteVisible, setConfirmDeleteVisible] = useState(false);
  const [sessionToDelete, setSessionToDelete] = useState<number | null>(null);

  // 初始化数据
  useEffect(() => {
    loadSessions();
    setSelectedSession(selectedSession?.id ? selectedSession : null);
    setSelectedModule(selectedModule ? selectedModule : 0);
    // 从localStorage加载设置
    const savedSettings = localStorage.getItem('appSettings');
    if (savedSettings) {
      const settings = JSON.parse(savedSettings);
      settingForm.setFieldsValue(settings);
      setSettingType(settings.setting_type || 'api');
    }
  }, []);

  // 当选择"全部"节点时，如果当前在"生成测试用例"页签，自动切换到"管理测试用例"
  useEffect(() => {
    if (selectedModule === 0 && activeTab === 'generate') {
      setActiveTab('manage');
    }
  }, [selectedModule]);

  // 加载会话列表
  const loadSessions = async () => {
    try {
      const response: ApiResponse<Session[]> | any = await sessionApi.getSessions();
      if (response.code === 200 && response.data) {
        console.log('Setting sessions:', response.data);
        setSessions(response.data);
        if (response.data.length > 0) {
          setSelectedSession(response.data[0]);
          loadModules(response.data[0].id);
          loadTestcases(response.data[0].id);
        }
      }
    } catch (error) {
      console.error('加载会话失败:', error);
    }
  };

  // 加载测试用例
  const loadTestcases = async (sessionId: number | any, filters?: TestCaseFilters) => {
    try {
      const response: ApiResponse<TestCaseResponse> | any = await testcaseApi.getTestcases(sessionId, filters);
      if (response.code === 200 && response.data) {
        setTestcases(response.data.items || []);
        setTestcasesResponse(response.data || {});
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

  // 打开新增模块对话框
  const handleOpenAddModuleModal = (parentId?: number) => {
    setNewModuleParentId(parentId || null);
    setIsAddModuleModalVisible(true);
  };

  // 创建模块
  const handleCreateModule = async () => {
    if (!selectedSession || !newModuleName.trim()) return;

    try {
      const response: ApiResponse<Module> | any = await moduleApi.createModule({
        module_name: newModuleName.trim(),
        session_id: selectedSession.id,
        parent_id: newModuleParentId
      });

      if (response.code === 200 && response.data) {
        setModules([...modules, response.data]);
        setNewModuleName('');
        setNewModuleParentId(null);
        setIsAddModuleModalVisible(false);
        notification.success({
          message: '创建成功',
          description: '模块已成功创建',
          placement: 'topRight'
        });
      }
    } catch (error) {
      console.error('创建模块失败:', error);
      notification.error({
        message: '创建失败',
        description: '模块创建失败，请重试',
        placement: 'topRight'
      });
    }
  };

  // 加载模块列表
  const loadModules = async (sessionId: number | any) => {
    try {
      const response: ApiResponse<Module[]> | any = await moduleApi.getModules(sessionId);
      if (response.code === 200 && response.data) {
        setModules(response.data);
        setSelectedModule('all'); // 重置选中的模块
      }
    } catch (error) {
      console.error('加载模块失败:', error);
    }
  };

  // 选择模块
  const handleSelectModule = (module: Module) => {
    setSelectedModule(module.id!);
    // 可以在这里添加根据模块筛选测试用例的逻辑
    loadTestcases(selectedSession?.id ?? undefined, { module_id: module.id || 'all' });
    setFilters({ case_name: '', bug_id: '', exist_bug: false, status: undefined, module_id: module.id || 'all' });
  };

  // 选择全部模块
  const handleSelectAllModules = () => {
    setSelectedModule(0);
    // 清空模块筛选
    loadTestcases(selectedSession?.id ?? undefined, { ...filters, module_id: undefined });
  };

  // 打开编辑模块对话框
  const handleEditModule = (module: Module) => {
    setModuleToEdit(module);
    setEditModuleName(module.module_name);
    setEditModuleParentId(module.parent_id);
    setIsEditModuleModalVisible(true);
  };

  // 更新模块
  const handleUpdateModule = async () => {
    if (!moduleToEdit || !editModuleName.trim()) return;

    try {
      const response: ApiResponse<Module> | any = await moduleApi.updateModule(moduleToEdit.id!, {
        ...moduleToEdit,
        module_name: editModuleName.trim(),
        parent_id: editModuleParentId
      });

      if (response.code === 200 && response.data) {
        // 更新本地状态
        setModules(modules.map(m => m.id === moduleToEdit.id ? response.data : m));
        if (selectedModule === moduleToEdit.id) {
          setSelectedModule(response.data.id!);
        }
        setIsEditModuleModalVisible(false);
        notification.success({
          message: '更新成功',
          description: response.message || '模块已成功更新',
          placement: 'topRight'
        });
      } else {
        notification.error({
          message: '更新失败',
          description: response.message || '模块更新失败，请重试',
          placement: 'topRight'
        });
      }
    } catch (error) {
      console.error('更新模块失败:', error);
      notification.error({
        message: '更新失败',
        description: '模块更新失败，请重试',
        placement: 'topRight'
      });
    }
  };

  // 删除模块
  const handleDeleteModule = async (moduleId: number) => {
    try {
      const response: ApiResponse | any = await moduleApi.deleteModule(moduleId);
      if (response.code === 200) {
        setModules(modules.filter(module => module.id !== moduleId));
        if (selectedModule === moduleId) {
          setSelectedModule(0);
        }
        notification.success({
          message: '删除成功',
          description: response.message || '模块已成功删除',
          placement: 'topRight'
        });
      } else {
        notification.error({
          message: '删除失败',
          description: response.message || '模块删除失败，请重试',
          placement: 'topRight'
        });
      }
    } catch (error) {
      console.error('删除模块失败:', error);
      notification.error({
        message: '删除失败',
        description: '模块删除失败，请重试',
        placement: 'topRight'
      });
    }
  };

  // 打开编辑会话对话框
  const handleOpenEditSessionModal = (session: Session) => {
    setSessionToEdit(session);
    setEditSessionName(session.name);
    setIsEditSessionModalVisible(true);
  };

  // 编辑会话
  const handleEditSession = async () => {
    if (!sessionToEdit || !editSessionName.trim()) return;

    try {
      // 调用更新会话API
      const response: ApiResponse<Session> | any = await sessionApi.updateSession(sessionToEdit.id, { name: editSessionName.trim() });
      if (response.code === 200 && response.data) {
        // 更新本地状态
        setSessions(sessions.map(s => s.id === sessionToEdit.id ? response.data : s));
        if (selectedSession?.id === sessionToEdit.id) {
          setSelectedSession(response.data);
        }
        setIsEditSessionModalVisible(false);
        notification.success({
          message: '更新成功',
          description: response.message || '会话已成功更新',
          placement: 'topRight'
        });
      } else {
        notification.error({
          message: '更新失败',
          description: response.message || '会话更新失败，请重试',
          placement: 'topRight'
        });
      }
    } catch (error) {
      console.error('更新会话失败:', error);
      notification.error({
        message: '更新失败',
        description: '会话更新失败，请重试',
        placement: 'topRight'
      });
    } finally {
      // 刷新测试用例列表
      loadSessions();
      loadTestcases(selectedSession?.id);
      loadModules(selectedSession?.id);
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
        notification.success({
          message: '删除成功',
          description: response.message || '会话已成功删除',
          placement: 'topRight'
        });
        setSessions(sessions.filter(session => session.id !== sessionToDelete));
        if (selectedSession?.id === sessionToDelete) {
          const sessionsId = sessions.length > 0 ? sessions[0] : null;
          setSelectedSession(sessionsId);
          loadTestcases(sessionsId?.id);
        }
      } else {
        notification.error({
          message: '删除失败',
          description: response.message || '会话删除失败，请重试',
          placement: 'topRight'
        });
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
    // const NewFilters = {
    //   case_name: '',
    //   status: undefined,
    //   bug_id: '',
    //   exist_bug: false,
    //   module_id: undefined
    // };
    // setFilters({ ...NewFilters });
    loadTestcases(session.id,);
    loadModules(session.id);
  };

  // 查看测试用例
  const handleViewTestcase = (testcase: TestCase) => {
    setSelectedTestcase(testcase);

    setIsViewModalVisible(true);
    const index_demo = testcases.findIndex(tc => tc.id === testcase?.id);
    // 重置索引
    setCurrentIndex(index_demo);
    // 重置按钮状态
    if (index_demo === 0) {
      setPrevButtonDisabled(true);
    } else {
      setPrevButtonDisabled(false);
    }
    if (index_demo === testcases.length - 1) {
      setNextButtonDisabled(true);
    } else {
      setNextButtonDisabled(false);
    }
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
          description: response.message || '测试用例已成功更新',
          placement: 'topRight'
        });
      } else {
        notification.error({
          message: '更新失败',
          description: response.message || '测试用例更新失败，请重试',
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

  // 移动测试用例状态
  const [isMoveModalVisible, setIsMoveModalVisible] = useState(false);
  const [testcaseToMove, setTestcaseToMove] = useState<TestCase | null>(null);
  const [selectedTestcaseIds, setSelectedTestcaseIds] = useState<number[]>([]);

  // 新增测试用例状态
  const [isAddModalVisible, setIsAddModalVisible] = useState(false);

  // 测试用例筛选条件
  const [filters, setFilters] = useState<TestCaseFilters>();

  // 测试用例筛选条件变更处理
  // useEffect(() => {
  //   loadTestcases(selectedSession?.id ?? undefined, { ...filters, module_id: selectedModule === 0 ? undefined : selectedModule });
  // }, [selectedModule]);
  // 删除测试用例 - 显示确认对话框
  const handleDeleteTestcase = (id: number) => {
    setTestcaseToDelete(id);
    setConfirmDeleteTestcaseVisible(true);
  };

  // 批量删除测试用例
  const handleBatchDeleteTestcases = async (ids: number[]) => {
    if (!selectedSession) return;

    try {
      // 循环删除每个测试用例
      // for (const id of ids) {
      //   await testcaseApi.deleteTestcase(selectedSession.id, id);
      // }

      const response: ApiResponse | any = await testcaseApi.deleteTestcase(selectedSession.id, ids);
      if (response.code === 200) {
        // 更新本地状态
        // setTestcases(testcases.filter(testcase => !ids.includes(testcase.id)));
        // 重新加载测试用例
        loadTestcases(selectedSession.id, filters);
        // 显示成功通知
        notification.success({
          message: '删除成功',
          description: response.message || `已成功删除 ${ids.length} 个测试用例`,
          placement: 'topRight'
        });
      } else {
        notification.error({
          message: '删除失败',
          description: response.message || `删除 ${ids.length} 个测试用例失败，请重试`,
          placement: 'topRight'
        });
      }
    } catch (error) {
      console.error('批量删除测试用例失败:', error);
      notification.error({
        message: '删除失败',
        description: '批量删除测试用例失败，请重试',
        placement: 'topRight'
      });
    }
  };

  // 确认删除测试用例
  const handleConfirmDeleteTestcase = async () => {
    if (!testcaseToDelete || !selectedSession) return;

    try {
      const response: any = await testcaseApi.deleteTestcase(selectedSession.id, [testcaseToDelete]);
      if (response.code === 200) {
        // setTestcases(testcases.filter(testcase => testcase.id !== testcaseToDelete));
        loadTestcases(selectedSession.id, { ...filters, module_id: selectedModule === 0 ? undefined : Number(selectedModule) });

        notification.success({
          message: '删除成功',
          description: response.message || '测试用例已成功删除',
          placement: 'topRight'
        });
      } else {
        notification.error({
          message: '删除失败',
          description: response.message || '测试用例删除失败，请重试',
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
  const handleCompleteTestcase = (testcase: TestCase | null) => {
    if (!testcase) return;
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
        // loadTestcases(selectedSession.id, filters);
        setSelectedTestcase(updatedTestcase);
        setCurrentIndex(testcases.findIndex(tc => tc.id === updatedTestcase.id));
        // 显示成功通知
        notification.success({
          message: '执行成功',
          description: `测试用例已成功标记为${status === TestCaseStatus.PASSED ? '通过' : status === TestCaseStatus.FAILED ? '未通过' : '未运行'}`,
          placement: 'topRight'
        });
      } else {
        notification.error({
          message: '执行失败',
          description: response.message || '测试用例标记执行失败，请重试',
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

  // 打开移动测试用例对话框（单个）
  const handleMoveTestcase = (testcase: TestCase | null) => {
    if (!testcase) return;
    setTestcaseToMove(testcase);
    setSelectedTestcaseIds([]);
    setIsMoveModalVisible(true);
  };

  // 打开批量移动测试用例对话框
  const handleBatchMoveTestcase = (ids: number[]) => {
    if (ids.length === 0) return;
    setSelectedTestcaseIds(ids);
    setTestcaseToMove(null);
    setIsMoveModalVisible(true);
  };

  // 移动测试用例成功处理
  const handleMoveTestcaseSuccess = () => {
    // 重新加载当前会话的测试用例
    if (selectedSession) {
      loadTestcases(selectedSession.id, filters);
    }
    // 清空选中的测试用例ID
    setSelectedTestcaseIds([]);
  };

  // 打开新增测试用例对话框
  const handleOpenAddTestcaseModal = () => {
    setIsAddModalVisible(true);
  };

  // 新增测试用例成功处理
  const handleAddTestcaseSuccess = () => {
    // 重新加载当前会话的测试用例
    if (selectedSession) {
      loadTestcases(selectedSession.id, filters);
    }
    setIsAddModalVisible(false);
  };

  // 设置功能
  const [isSettingModalVisible, setIsSettingModalVisible] = useState(false);
  const [settingButtonStatus, setSettingButtonStatus] = useState(false);

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
    if (!selectedSession || (!requirement.trim()) && !imageBase64) return;

    try {
      setLoading(true);

      // 从localStorage获取模型配置
      const savedSettings = localStorage.getItem('appSettings');
      if (!savedSettings) {
        handleOpenSettingModal();
        notification.error({
          message: '配置缺失',
          description: '请先配置模型设置',
          placement: 'topRight'
        });
        return;
      }

      const settings = JSON.parse(savedSettings);
      const modelConfig = {
        model_type: settings.setting_type,
        api_key: settings.api_key || '',
        ollama_url: settings.ollama_url || '',
        ollama_model: settings.ollama_model || ''
      };

      setSettingButtonStatus(true); // 点击生成按钮设置按钮置灰

      // 只有当requirement非空或imageBase64非空时才调用API
      if (requirement.trim() || imageBase64) {
        console.log('调用API前的状态:', {
          sessionId: selectedSession.id,
          requirement: requirement.trim(),
          hasModelConfig: !!modelConfig,
          hasImageBase64: !!imageBase64,
          imageBase64Length: imageBase64?.length || 0
        });

        // 【第一步】先保存历史提示词 - 只要有需求描述就保存
        if (requirement.trim()) {
          console.log('=== 开始保存历史提示词 ===');
          try {
            const promptData: { content: string; session_id: number; module_id?: number } = {
              content: requirement.trim(),
              session_id: selectedSession.id
            };
            // 只有选择了具体模块时才关联模块
            if (selectedModule && selectedModule !== 0 && selectedModule !== 'all') {
              promptData.module_id = Number(selectedModule);
            }
            console.log('准备保存提示词数据:', promptData);

            // 调用创建历史提示词接口
            const promptResponse = await historyPromptApi.createPrompt(promptData);
            console.log('历史提示词API响应:', promptResponse);

            if (promptResponse.code === 200) {
              console.log('✓ 历史提示词已成功保存到数据库');
            } else {
              console.error('历史提示词保存失败，响应码:', promptResponse.code);
            }
          } catch (error: any) {
            console.error('保存历史提示词失败:', error);
            // 不阻断流程，继续生成测试用例
          }
        }

        // 【第二步】调用生成测试用例API
        const response: ApiResponse<TestCase> | any = await testcaseApi.generateTestcases(
          selectedSession.id,
          requirement.trim(),
          modelConfig,
          imageBase64, // 传递图片base64数据
          selectedModule === 0 ? undefined : Number(selectedModule) // 传递模块ID
        );
        console.log('生成测试用例API响应:', response);

        if (response.code === 200) {
          loadTestcases(selectedSession.id);
          setRequirement('');
          setImageBase64(null); // 清空图片数据
          // 刷新历史提示词列表
          setHistoryPromptRefreshKey(prev => prev + 1);
          notification.success({
            message: '生成成功',
            description: '测试用例已成功生成',
            placement: 'topRight'
          });
        } else {
          notification.error({
            message: '生成失败',
            description: response.message || '生成测试用例失败，请重试',
            placement: 'topRight'
          });
        }
      } else {
        notification.error({
          message: '生成失败',
          description: '请上传图片文件或输入需求描述',
          placement: 'topRight'
        });
      }

    } catch (error: any) {
      console.error('生成测试用例失败:', error);
      notification.error({
        message: '生成失败',
        description: `${error.response?.data?.detail || '生成测试用例时发生错误，请重试'}`,
        placement: 'topRight'
      });
    } finally {
      setLoading(false);
      setSettingButtonStatus(false); // 最终总会恢复按钮状态
    }
  };
  const [nextButtonDisabled, setNextButtonDisabled] = useState(false);
  const [prevButtonDisabled, setPrevButtonDisabled] = useState(false);
  const [currentIndex, setCurrentIndex] = useState<number>(0);
  const handleNextCase = async () => {
    if (!selectedSession || !selectedTestcase) return;

    if (currentIndex === testcases.length - 2) {
      setNextButtonDisabled(true);
    };
    const nextIndex = currentIndex + 1;
    const nextTestcase = testcases[nextIndex];
    setSelectedTestcase(nextTestcase);
    setCurrentIndex(nextIndex);
    setPrevButtonDisabled(false);
  };
  const handlePrevCase = async () => {
    if (!selectedSession || !selectedTestcase) return;
    setNextButtonDisabled(false);
    if (currentIndex === 1) {
      setPrevButtonDisabled(true);
    }

    const prevIndex = currentIndex - 1;
    const prevTestcase = testcases[prevIndex];
    setSelectedTestcase(prevTestcase);
    setCurrentIndex(prevIndex);
    setNextButtonDisabled(false);
  };

  // 导航函数
  const navigateToHome = () => {
    setCurrentPlatform('home');
    localStorage.setItem('currentPlatform', 'home');
  };

  const navigateToAITestcase = () => {
    setCurrentPlatform('ai-testcase');
    localStorage.setItem('currentPlatform', 'ai-testcase');
  };

  const navigateToIoTMock = () => {
    setCurrentPlatform('iot-mock');
    localStorage.setItem('currentPlatform', 'iot-mock');
  };

  const navigateToScheduledTask = () => {
    setCurrentPlatform('scheduled-task');
    localStorage.setItem('currentPlatform', 'scheduled-task');
  };

  const navigateToMock = () => {
    setCurrentPlatform('mock-api');
    localStorage.setItem('currentPlatform', 'mock-api');
  };
  
  // 全局参数管理函数
  const fetchGlobalParameters = async (envId?: string | null) => {
    try {
      const response = await globalParameterApi.getEnvironments();
      if (response.code === 200 && response.data) {
        const backendEnvironments = response.data.map((env: any) => ({
          id: env.id.toString(),
          name: env.name,
          parameters: env.parameters || [],
          is_default: env.is_default
        }));
        if (backendEnvironments.length > 0) {
          setEnvironments(backendEnvironments);
          if (envId) {
            setCurrentEnvironmentId(envId);
            return;
          }
          // 优先使用 localStorage 中保存的环境，若不存在则使用默认环境
          const savedEnvId = localStorage.getItem('currentEnvironmentId');
          if (savedEnvId && backendEnvironments.find((env: any) => env.id === savedEnvId)) {
            setCurrentEnvironmentId(savedEnvId);
          } else {
            const defaultEnv = backendEnvironments.find((env: any) => env.is_default) || backendEnvironments[0];
            setCurrentEnvironmentId(defaultEnv.id);
          }
        }
      }
    } catch (error) {
      console.error('Failed to fetch global parameters:', error);
    }
  };
  
  // 获取当前环境
  const getCurrentEnvironment = (): Environment => {
    return environments.find(env => env.id === currentEnvironmentId) || environments[0];
  };
  
  // 切换环境
  const handleSwitchEnvironment = (envId: string) => {
    setCurrentEnvironmentId(envId);
    localStorage.setItem('currentEnvironmentId', envId);
  };
  
  // 保存环境到后端
  const saveEnvironmentToBackend = async (environment: Environment) => {
    try {
      if (environment.id === 'env-1') {
        // 创建新环境
        const response = await globalParameterApi.createEnvironment({
          name: environment.name,
          parameters: environment.parameters,
          is_default: environment.is_default || false
        });
        if (response.code === 200 && response.data) {
          fetchGlobalParameters(response.data.id.toString());
        }
      } else {
        // 更新现有环境
        const response = await globalParameterApi.updateEnvironment(Number(environment.id), {
          name: environment.name,
          parameters: environment.parameters,
          is_default: environment.is_default || false
        });
        if (response.code === 200) {
          fetchGlobalParameters(environment.id);
        }
      }
    } catch (error) {
      console.error('Failed to save environment:', error);
    }
  };
  
  // 添加环境参数
  const handleAddEnvironmentParameter = async () => {
    const newEnvironments = [...environments];
    const envIndex = newEnvironments.findIndex(env => env.id === currentEnvironmentId);
    if (envIndex !== -1) {
      const newParameters = [...newEnvironments[envIndex].parameters, { key: '', value: '' }];
      newEnvironments[envIndex] = { ...newEnvironments[envIndex], parameters: newParameters };
      setEnvironments(newEnvironments);
      // 保存到后端
      await saveEnvironmentToBackend(newEnvironments[envIndex]);
    }
  };
  
  // 删除环境参数
  const handleRemoveEnvironmentParameter = async (index: number) => {
    const newEnvironments = [...environments];
    const envIndex = newEnvironments.findIndex(env => env.id === currentEnvironmentId);
    if (envIndex !== -1) {
      const newParameters = newEnvironments[envIndex].parameters.filter((_, i) => i !== index);
      newEnvironments[envIndex] = { ...newEnvironments[envIndex], parameters: newParameters };
      setEnvironments(newEnvironments);
      // 保存到后端
      await saveEnvironmentToBackend(newEnvironments[envIndex]);
    }
  };
  
  // 处理环境参数变更
  const handleEnvironmentParameterChange = async (index: number, field: 'key' | 'value', value: string) => {
    const newEnvironments = [...environments];
    const envIndex = newEnvironments.findIndex(env => env.id === currentEnvironmentId);
    if (envIndex !== -1) {
      const newParameters = [...newEnvironments[envIndex].parameters];
      newParameters[index] = { ...newParameters[index], [field]: value };
      newEnvironments[envIndex] = { ...newEnvironments[envIndex], parameters: newParameters };
      setEnvironments(newEnvironments);
      // 保存到后端
      await saveEnvironmentToBackend(newEnvironments[envIndex]);
    }
  };
  
  // 打开添加环境模态框
  const handleAddEnvironment = () => {
    setNewEnvironmentName('');
    setIsAddEnvModalVisible(true);
  };
  
  // 确认添加环境
  const handleConfirmAddEnvironment = async () => {
    if (newEnvironmentName) {
      try {
        const response = await globalParameterApi.createEnvironment({
          name: newEnvironmentName,
          parameters: [],
          is_default: environments.length === 0
        });
        if (response.code === 200 && response.data) {
          fetchGlobalParameters(response.data.id.toString());
          setIsAddEnvModalVisible(false);
        }
      } catch (error) {
        console.error('Failed to add environment:', error);
      }
    }
  };
  
  // 删除环境
  const handleRemoveEnvironment = async (envId: string) => {
    try {
      const response = await globalParameterApi.deleteEnvironment(Number(envId));
      if (response.code === 200) {
        fetchGlobalParameters();
      }
    } catch (error) {
      console.error('Failed to remove environment:', error);
    }
  };

  // 打开编辑环境模态框
  const handleOpenEditEnvironment = (env: Environment) => {
    setEditingEnvironment(env);
    setEditEnvironmentName(env.name);
    setIsEditEnvModalVisible(true);
  };

  // 确认编辑环境
  const handleConfirmEditEnvironment = async () => {
    if (!editingEnvironment || !editEnvironmentName.trim()) return;
    try {
      const response = await globalParameterApi.updateEnvironment(Number(editingEnvironment.id), {
        name: editEnvironmentName.trim(),
        parameters: editingEnvironment.parameters,
        is_default: editingEnvironment.is_default || false
      });
        if (response.code === 200 && response.data) {
          fetchGlobalParameters(editingEnvironment.id);
          setIsEditEnvModalVisible(false);
        }
    } catch (error) {
      console.error('Failed to update environment:', error);
    }
  };
  
  // 加载全局参数
  useEffect(() => {
    fetchGlobalParameters();
  }, []);

  return (
    <ConfigProvider locale={zhCN}>
      {currentPlatform === 'home' ? (
        <HomePage
          onNavigateToAI={navigateToAITestcase}
          onNavigateToIoT={navigateToIoTMock}
          onNavigateToScheduledTask={navigateToScheduledTask}
          onNavigateToMock={navigateToMock}
        />
      ) : currentPlatform === 'iot-mock' ? (
        <div>
          <SubPlatformHeader
            title="测试工具平台"
            onBackToHome={navigateToHome}
            environmentName={getCurrentEnvironment()?.name}
            onGlobalParamsOpen={() => setIsGlobalParamsModalVisible(true)}
          />
          <IoTDataPushPlatform
            currentEnvironmentId={currentEnvironmentId}
          />
        </div>
      ) : currentPlatform === 'scheduled-task' ? (
        <div>
          <SubPlatformHeader
            title="测试工具平台"
            onBackToHome={navigateToHome}
            environmentName={getCurrentEnvironment()?.name}
            onGlobalParamsOpen={() => setIsGlobalParamsModalVisible(true)}
          />
          <div style={{ padding: '16px' }}>
            <ScheduledTaskManager />
          </div>
        </div>
      ) : currentPlatform === 'mock-api' ? (
        <div>
          <SubPlatformHeader
            title="测试工具平台"
            onBackToHome={navigateToHome}
            environmentName={getCurrentEnvironment()?.name}
            onGlobalParamsOpen={() => setIsGlobalParamsModalVisible(true)}
          />
          <IoTMockPlatform />
        </div>
      ) : (
        <Layout style={{ minHeight: '100vh' }}>
          <HeaderComponent 
            onSettingsOpen={handleOpenSettingModal} 
            settingButtonStatus={settingButtonStatus} 
            onBackToHome={navigateToHome}
          />
          <Layout>
            <SessionSidebar
              testcases={testcases}
              sessions={sessions}
              selectedSession={selectedSession}
              newSessionName={newSessionName}
              onNewSessionNameChange={setNewSessionName}
              onCreateSession={handleCreateSession}
              onSelectSession={handleSelectSession}
              onDeleteSession={handleDeleteSession}
              onOpenAddModuleModal={handleOpenAddModuleModal}
              onOpenEditSessionModal={handleOpenEditSessionModal}
            />
            <ModuleSidebar
              modules={modules}
              selectedModule={selectedModule}
              onSelectModule={handleSelectModule}
              onSelectAllModules={handleSelectAllModules}
              onDeleteModule={handleDeleteModule}
              onEditModule={handleEditModule}
              onOpenAddModuleModal={handleOpenAddModuleModal}
            />
            <Layout style={{ padding: '4px' }}>
              <Content style={{ background: '#fff', padding: '10px', margin: 0 }}>
                <Tabs
                  activeKey={activeTab}
                  onChange={(key) => {
                    setActiveTab(key);
                    // 切换到"管理测试用例"页签时，加载测试用例列表
                    if (key === 'manage' && selectedSession) {
                      loadTestcases(selectedSession.id, filters);
                    }
                  }}
                  items={
                    selectedModule === 0
                      ? [
                          // 只显示"管理测试用例"页签
                          {
                            key: 'manage',
                            label: '管理测试用例',
                            children: (
                              <TestCaseManager
                                testcasesResponse={testcasesResponse}
                                modules={modules}
                                selectedSession={selectedSession}
                                selectedModule={selectedModule}
                                testcases={testcases}
                                filters={filters ?? undefined}
                                onFiltersChange={(newFilters) => {
                                  // 合并当前选中的模块ID到过滤器中
                                  const mergedFilters = {
                                    ...newFilters,
                                    module_id: selectedModule === 0 ? undefined : Number(selectedModule)
                                  };
                                  setFilters(mergedFilters);
                                  // 重新加载测试用例
                                  loadTestcases(selectedSession?.id ?? undefined, mergedFilters);
                                }}
                                onLoadTestcases={loadTestcases}
                                onView={handleViewTestcase}
                                onEdit={handleEditTestcase}
                                onComplete={handleCompleteTestcase}
                                onDelete={handleDeleteTestcase}
                                onBatchDelete={handleBatchDeleteTestcases}
                                onBatchMove={handleBatchMoveTestcase}
                                onAdd={handleOpenAddTestcaseModal}
                                onMove={handleMoveTestcase}
                              />
                            ),
                          },
                        ]
                      : [
                          // 显示两个页签
                          {
                            key: 'generate',
                            label: '生成测试用例',
                            children: (
                              <TestCaseGenerator
                                selectedSession={selectedSession}
                                modules={modules}
                                selectedModule={selectedModule}
                                requirement={requirement}
                                loading={loading}
                                onRequirementChange={setRequirement}
                                onGenerate={handleGenerateTestcases}
                                // imageBase64={imageBase64}
                                // onImageChange={setImageBase64}
                                historyPromptRefreshKey={historyPromptRefreshKey}
                              />
                            ),
                          },
                          {
                            key: 'manage',
                            label: '管理测试用例',
                            children: (
                              <TestCaseManager
                                testcasesResponse={testcasesResponse}
                                modules={modules}
                                selectedSession={selectedSession}
                                selectedModule={selectedModule}
                                testcases={testcases}
                                filters={filters ?? undefined}
                                onFiltersChange={(newFilters) => {
                                  // 合并当前选中的模块ID到过滤器中
                                  const mergedFilters = {
                                    ...newFilters,
                                    module_id: selectedModule === 0 ? undefined : Number(selectedModule)
                                  };
                                  setFilters(mergedFilters);
                                  // 重新加载测试用例
                                  loadTestcases(selectedSession?.id ?? undefined, mergedFilters);
                                }}
                                onLoadTestcases={loadTestcases}
                                onView={handleViewTestcase}
                                onEdit={handleEditTestcase}
                                onComplete={handleCompleteTestcase}
                                onDelete={handleDeleteTestcase}
                                onBatchDelete={handleBatchDeleteTestcases}
                                onBatchMove={handleBatchMoveTestcase}
                                onAdd={handleOpenAddTestcaseModal}
                                onMove={handleMoveTestcase}
                              />
                            ),
                          },
                        ]
                  }
                />
              </Content>
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
          <ViewTestcaseModal
            visible={isViewModalVisible}
            selectedTestcase={selectedTestcase}
            nextButtonDisabled={nextButtonDisabled}
            prevButtonDisabled={prevButtonDisabled}
            onNext={handleNextCase}
            onPrev={handlePrevCase}
            onCancel={() => setIsViewModalVisible(false)}
            onComplete={handleCompleteTestcase}
          />
          <CompleteTestcaseModal
            visible={confirmCompleteTestcaseVisible}
            onOk={handleConfirmCompleteTestcase}
            onCancel={handleCancelCompleteTestcase}
          />

          {
            isEditModalVisible && (
              <EditTestcaseModal
                visible={isEditModalVisible}
                selectedTestcase={selectedTestcase}
                form={form}
                loading={loading}
                onCancel={() => setIsEditModalVisible(false)}
                onFinish={handleEditSubmit}
              />
            )
          }

          <Modal
            title="新增模块"
            open={isAddModuleModalVisible}
            onOk={handleCreateModule}
            onCancel={() => {
              setIsAddModuleModalVisible(false);
              setNewModuleParentId(null);
            }}
            confirmLoading={loading}
          >
            <div style={{ marginBottom: '12px' }}>
              <label style={{ display: 'block', marginBottom: '4px' }}>父模块：</label>
              <Select
                placeholder="请选择父模块（可选）"
                value={newModuleParentId}
                onChange={(value) => setNewModuleParentId(value)}
                style={{ width: '100%' }}
                allowClear
                showSearch
                optionFilterProp="children"
              >
                {modules.map(m => (
                  <Select.Option key={m.id} value={m.id!}>
                    {m.module_name}
                  </Select.Option>
                ))}
              </Select>
            </div>
            <div>
              <label style={{ display: 'block', marginBottom: '4px' }}>模块名称：</label>
              <Input
                placeholder="请输入模块名称"
                value={newModuleName}
                onChange={(e) => setNewModuleName(e.target.value)}
                onPressEnter={handleCreateModule}
              />
            </div>
          </Modal>

          <Modal
            title="编辑模块"
            open={isEditModuleModalVisible}
            onOk={handleUpdateModule}
            onCancel={() => setIsEditModuleModalVisible(false)}
            confirmLoading={loading}
          >
            <div style={{ marginBottom: '12px' }}>
              <label style={{ display: 'block', marginBottom: '4px' }}>父模块：</label>
              <Select
                placeholder="请选择父模块（可选）"
                value={editModuleParentId}
                onChange={(value) => setEditModuleParentId(value)}
                style={{ width: '100%' }}
                allowClear
                showSearch
                optionFilterProp="children"
              >
                {/* 排除当前编辑的模块及其子模块 */}
                {modules
                  .filter(m => m.id !== moduleToEdit?.id)
                  .map(m => (
                    <Select.Option key={m.id} value={m.id!}>
                      {m.module_name}
                    </Select.Option>
                  ))}
              </Select>
            </div>
            <div>
              <label style={{ display: 'block', marginBottom: '4px' }}>模块名称：</label>
              <Input
                placeholder="请输入模块名称"
                value={editModuleName}
                onChange={(e) => setEditModuleName(e.target.value)}
                onPressEnter={handleUpdateModule}
              />
            </div>
          </Modal>

          <Modal
            title="编辑会话"
            open={isEditSessionModalVisible}
            onOk={handleEditSession}
            onCancel={() => setIsEditSessionModalVisible(false)}
            confirmLoading={loading}
          >
            <Input
              placeholder="请输入会话名称"
              value={editSessionName}
              onChange={(e) => setEditSessionName(e.target.value)}
              onPressEnter={handleEditSession}
              style={{ marginBottom: '8px' }}
            />
          </Modal>

          <SettingsModal
            visible={isSettingModalVisible}
            settingForm={settingForm}
            settingType={settingType}
            loading={loading}
            onCancel={() => setIsSettingModalVisible(false)}
            onFinish={handleSaveSetting}
            onSettingTypeChange={handleSettingTypeChange}
          />

          <MoveTestcaseModal
            visible={isMoveModalVisible}
            testcase={testcaseToMove}
            testcaseIds={selectedTestcaseIds}
            onCancel={() => setIsMoveModalVisible(false)}
            onMoveSuccess={handleMoveTestcaseSuccess}
          />

          <AddTestcaseModal
            visible={isAddModalVisible}
            selectedSession={selectedSession}
            modules={modules}
            onCancel={() => setIsAddModalVisible(false)}
            onAddSuccess={handleAddTestcaseSuccess}
          />
        </Layout>
      )}

      {/* 全局参数模态框 - 全局渲染 */}
      <Modal
        title="全局参数管理"
        open={isGlobalParamsModalVisible}
        onOk={() => setIsGlobalParamsModalVisible(false)}
        onCancel={() => setIsGlobalParamsModalVisible(false)}
        width={600}
      >
        <div style={{ marginBottom: '16px' }}>
          {/* 环境选择和管理 */}
          <div style={{ marginBottom: '20px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
              <h3 style={{ margin: 0, fontSize: '14px', fontWeight: 'bold' }}>环境管理</h3>
              <Button
                type="primary"
                size="small"
                onClick={handleAddEnvironment}
              >
                添加环境
              </Button>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginBottom: '12px' }}>
              {environments.map(env => (
                <div
                  key={env.id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    padding: '4px 12px',
                    borderRadius: '16px',
                    background: currentEnvironmentId === env.id ? '#1890ff' : '#f0f0f0',
                    color: currentEnvironmentId === env.id ? '#fff' : '#333',
                    cursor: 'pointer',
                    fontSize: '12px',
                    gap: '4px'
                  }}
                  onClick={() => handleSwitchEnvironment(env.id)}
                >
                  {env.name}
                  {env.is_default && (
                    <span style={{ fontSize: '10px', opacity: 0.8 }}>(默认)</span>
                  )}
                  <Button
                    type="text"
                    size="small"
                    icon={<EditOutlined />}
                    style={{ color: currentEnvironmentId === env.id ? '#fff' : '#999', padding: 0, fontSize: '12px' }}
                    onClick={(e) => {
                      e.stopPropagation();
                      handleOpenEditEnvironment(env);
                    }}
                  >
                    {/* 编辑 */}
                  </Button>
                  <Popconfirm
                    title="确定删除该环境？"
                    description="删除后该环境下的所有参数将丢失"
                    onConfirm={(e) => {
                      e?.stopPropagation();
                      handleRemoveEnvironment(env.id);
                    }}
                    onCancel={(e) => e?.stopPropagation()}
                    okText="确定"
                    cancelText="取消"
                  >
                    <Button
                      type="text"
                      size="small"
                      style={{ color: currentEnvironmentId === env.id ? '#fff' : '#ff4d4f', padding: 0, fontSize: '14px' }}
                      onClick={(e) => e.stopPropagation()}
                    >
                      ×
                    </Button>
                  </Popconfirm>
                </div>
              ))}
            </div>
          </div>

          {/* 当前环境的参数 */}
          <p style={{ color: '#666', marginBottom: '12px' }}>全局参数将应用于所有请求，可在URL、请求头和请求体中使用 &#123;&#123;variable&#125;&#125; 或 $&#123;variable&#125; 语法引用</p>

          {getCurrentEnvironment().parameters.map((param, index) => (
            <Space
              key={index}
              style={{ width: '100%', marginBottom: '12px' }}
              align="center"
            >
              <Input
                placeholder="参数名"
                value={param.key}
                onChange={(e) => handleEnvironmentParameterChange(index, 'key', e.target.value)}
                style={{ width: 150 }}
                size="middle"
              />
              <Input
                placeholder="参数值"
                value={param.value}
                onChange={(e) => handleEnvironmentParameterChange(index, 'value', e.target.value)}
                style={{ flex: 1, width: 330 }}
                size="middle"
              />
              <Button
                icon={<MinusOutlined />}
                danger
                onClick={() => handleRemoveEnvironmentParameter(index)}
                size="small"
              />
            </Space>
          ))}

          <Button
            type="dashed"
            icon={<PlusOutlined />}
            onClick={handleAddEnvironmentParameter}
            style={{ marginTop: '8px', width: '100%' }}
            size="middle"
          >
            添加全局参数
          </Button>
        </div>
      </Modal>

      {/* 添加环境模态框 - 全局渲染 */}
      <Modal
        title="创建新环境"
        open={isAddEnvModalVisible}
        onOk={handleConfirmAddEnvironment}
        onCancel={() => setIsAddEnvModalVisible(false)}
        width={400}
      >
        <Input
          placeholder="请输入环境名称"
          value={newEnvironmentName}
          onChange={(e) => setNewEnvironmentName(e.target.value)}
          style={{ marginBottom: '16px' }}
        />
        <div style={{ color: '#999', fontSize: '12px' }}>
          环境名称用于区分不同的参数配置集
        </div>
      </Modal>

      {/* 编辑环境模态框 - 全局渲染 */}
      <Modal
        title="编辑环境"
        open={isEditEnvModalVisible}
        onOk={handleConfirmEditEnvironment}
        onCancel={() => setIsEditEnvModalVisible(false)}
        width={400}
      >
        <Input
          placeholder="请输入环境名称"
          value={editEnvironmentName}
          onChange={(e) => setEditEnvironmentName(e.target.value)}
          onPressEnter={handleConfirmEditEnvironment}
          style={{ marginBottom: '16px' }}
        />
        <div style={{ color: '#999', fontSize: '12px' }}>
          环境名称用于区分不同的参数配置集
        </div>
      </Modal>
    </ConfigProvider>
  );
};

export default App;

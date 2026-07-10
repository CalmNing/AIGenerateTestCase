import React, { useState, useEffect } from 'react';
import { ConfigProvider, theme } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { Layout, Form, Tabs, Modal, Input, Button, Select, Space, Popconfirm, message } from 'antd';
import { PlusOutlined, MinusOutlined, EditOutlined } from '@ant-design/icons';
import SubPlatformHeader from './components/SubPlatformHeader';
import { ApiResponse, Session, TestCase, TestCaseResponse, TestCaseStatus, Module, TestCaseFilters, TestCaseExecutionLog } from './types';
import { sessionApi, testcaseApi, moduleApi, globalParameterApi, configApi } from './services/api';
import { useEnvironments, Environment } from './hooks/useEnvironments';
import HomePage from './components/HomePage';
import IoTDataPushPlatform from './components/iot/IoTDataPushPlatform';
import IoTMockPlatform from './components/iot/IoTMockPlatform';
import HeaderComponent from './components/HeaderComponent';
import SessionSidebar from './components/SessionSidebar';
import TestCaseGenerator from './components/test-case/TestCaseGenerator';
import TestCaseManager from './components/test-case/TestCaseManager';
import RequirementTestPointManager from './components/test-case/RequirementTestPointManager';
import SessionApiConfig from './components/api/SessionApiConfig';
import ScheduledTaskManager from './components/ScheduledTaskManager';
import ApiScenarioTestTool from './components/api/ApiScenarioTestTool';
import VariableAssistant from './components/VariableAssistant';
import DeleteSessionModal from './components/modals/DeleteSessionModal';
import DeleteTestcaseModal from './components/modals/DeleteTestcaseModal';
import CompleteTestcaseModal from './components/modals/CompleteTestcaseModal';
import ViewTestcaseModal from './components/modals/ViewTestcaseModal';
import EditTestcaseModal from './components/modals/EditTestcaseModal';
import SettingsModal from './components/modals/SettingsModal';
import McpConfigModal from './components/modals/McpConfigModal';
import SkillsHubModal from './components/modals/SkillsHubModal';
import MoveTestcaseModal from './components/modals/MoveTestcaseModal';
import AddTestcaseModal from './components/modals/AddTestcaseModal';
import TestCaseExecutionLogModal from './components/modals/TestCaseExecutionLogModal';
import ModuleSidebar from './components/ModuleSidebar';
import { Permission, hasPermission } from './services/permissions';
import { useRef } from 'react';

const { Content } = Layout;

const STORAGE_KEYS = {
  selectedSessionId: 'ai-testcase:selectedSessionId',
  selectedModuleId: 'ai-testcase:selectedModuleId',
  activeTab: 'ai-testcase:activeTab',
} as const;

const getStoredNumber = (key: string): number | null => {
  const raw = localStorage.getItem(key);
  if (raw === null || raw === '') return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
};

const INITIAL_PERSISTED_STATE = {
  selectedSessionId: getStoredNumber(STORAGE_KEYS.selectedSessionId),
  selectedModuleId: getStoredNumber(STORAGE_KEYS.selectedModuleId),
  activeTab: localStorage.getItem(STORAGE_KEYS.activeTab) || 'generate',
};

const showMessage = (
  type: 'success' | 'error' | 'warning' | 'info',
  title: string,
  description?: string,
) => {
  message.open({
    type,
    content: description ? `${title}: ${description}` : title,
  });
};

const App: React.FC = () => {

  // 导航状态管理
  const [currentPlatform, setCurrentPlatform] = useState<'home' | 'ai-testcase' | 'iot-mock' | 'api-test' | 'scheduled-task' | 'mock-api'>(() => {
    // 从localStorage加载上次的平台状态
    const savedPlatform = localStorage.getItem('currentPlatform');
    return (savedPlatform as 'home' | 'ai-testcase' | 'iot-mock' | 'api-test' | 'scheduled-task' | 'mock-api') || 'home';
  });
  const canManageSettings = hasPermission(Permission.CONFIG_MANAGE);
  const canManageMcp = hasPermission(Permission.MCP_MANAGE);
  const canManageSkills = hasPermission(Permission.SKILLS_MANAGE);
  const canManageGlobalParams = hasPermission(Permission.GLOBAL_PARAMETER_MANAGE);
  const canAccessScheduledTask = hasPermission(Permission.SCHEDULED_TASK_MANAGE);
  const canAccessMock = hasPermission(Permission.MOCK_MANAGE);

  // 状态管理
  const [sessions, setSessions] = useState<Session[]>([]);
  const [modules, setModules] = useState<Module[]>([]);
  const [selectedSession, setSelectedSession] = useState<Session | null>(null);
  const [selectedModule, setSelectedModule] = useState<number|string>(INITIAL_PERSISTED_STATE.selectedModuleId ?? 0);
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
  
  // 全局参数管理（迁移到 useEnvironments hook）
  const {
    environments,
    currentEnvironmentId,
    isGlobalParamsModalVisible,
    setIsGlobalParamsModalVisible,
    newEnvironmentName,
    setNewEnvironmentName,
    isAddEnvModalVisible,
    setIsAddEnvModalVisible,
    isEditEnvModalVisible,
    setIsEditEnvModalVisible,
    editingEnvironment,
    editEnvironmentName,
    setEditEnvironmentName,
    getCurrentEnvironment,
    handleSwitchEnvironment,
    handleAddEnvironmentParameter,
    handleRemoveEnvironmentParameter,
    handleEnvironmentParameterChange,
    handleAddEnvironment,
    handleConfirmAddEnvironment,
    handleRemoveEnvironment,
    handleOpenEditEnvironment,
    handleConfirmEditEnvironment,
  } = useEnvironments(canManageGlobalParams);
  const [selectedTestcase, setSelectedTestcase] = useState<TestCase | null>(null);
  const [executionLogTestcase, setExecutionLogTestcase] = useState<TestCase | null>(null);
  const [executionLogs, setExecutionLogs] = useState<TestCaseExecutionLog[]>([]);
  const [executionLogsLoading, setExecutionLogsLoading] = useState(false);
  const [isExecutionLogModalVisible, setIsExecutionLogModalVisible] = useState(false);
  const [isViewModalVisible, setIsViewModalVisible] = useState(false);
  const [isEditModalVisible, setIsEditModalVisible] = useState(false);
  const [returnToViewAfterEdit, setReturnToViewAfterEdit] = useState(false);
  const [isEditSessionModalVisible, setIsEditSessionModalVisible] = useState(false);
  const [isEditModuleModalVisible, setIsEditModuleModalVisible] = useState(false);
  const [sessionToEdit, setSessionToEdit] = useState<Session | null>(null);
  const [moduleToEdit, setModuleToEdit] = useState<Module | null>(null);
  const [isAddModuleModalVisible, setIsAddModuleModalVisible] = useState(false);
  const [activeTab, setActiveTab] = useState(INITIAL_PERSISTED_STATE.activeTab);
  // 图片状态管理
  const [imageBase64, setImageBase64] = useState<string | null>(null);
  // 历史提示词刷新key
  const [historyPromptRefreshKey, setHistoryPromptRefreshKey] = useState(0);

  // 创建表单实例
  const [form] = Form.useForm();
  // 删除确认状态
  const [confirmDeleteVisible, setConfirmDeleteVisible] = useState(false);
  const [sessionToDelete, setSessionToDelete] = useState<number | null>(null);
  const canPersistSelectionRef = useRef(false);
  const hasRestoredModuleRef = useRef(false);

  // 初始化数据
  useEffect(() => {
    loadSessions();
    // 从后端加载配置
    const loadConfig = async () => {
      try {
        const res = await configApi.getConfig();
        // console.log('初始化加载配置结果:', res);
        if (res.code === 200 && res.data) {
          const config = res.data;
          // console.log('配置数据:', config);
          // 延迟设置值，确保表单已渲染
          setTimeout(() => {
            const values = {
              setting_type: config.model_type || 'api',
              api_key: config.api_key || '',
              api_base_url: config.api_base_url || 'https://api.deepseek.com',
              api_model: config.api_model || 'deepseek-v4-flash',
              api_proxy_url: config.api_proxy_url || '',
              ollama_url: config.ollama_url || 'http://localhost:11434',
              ollama_model: config.ollama_model || 'llama3',
            };
            // console.log('设置表单值:', values);
            settingForm.setFieldsValue(values);
            setSettingType((config.model_type as 'api' | 'ollama') || 'api');
            // console.log('设置后的表单值:', settingForm.getFieldsValue());
          }, 100);
        }
      } catch (error) {
        console.error('加载配置失败:', error);
      }
    };
    loadConfig();
  }, []);

  useEffect(() => {
    if (!canPersistSelectionRef.current) return;
    if (selectedSession?.id) {
      localStorage.setItem(STORAGE_KEYS.selectedSessionId, String(selectedSession.id));
    } else {
      localStorage.removeItem(STORAGE_KEYS.selectedSessionId);
    }
  }, [selectedSession?.id]);

  useEffect(() => {
    if (!canPersistSelectionRef.current) return;
    localStorage.setItem(STORAGE_KEYS.selectedModuleId, String(selectedModule || 0));
  }, [selectedModule]);

  useEffect(() => {
    if (!canPersistSelectionRef.current) return;
    localStorage.setItem(STORAGE_KEYS.activeTab, activeTab);
  }, [activeTab]);

  useEffect(() => {
    const blocked =
      (currentPlatform === 'scheduled-task' && !canAccessScheduledTask) ||
      (currentPlatform === 'mock-api' && !canAccessMock);

    if (blocked) {
      setCurrentPlatform('home');
      localStorage.setItem('currentPlatform', 'home');
    }
  }, [currentPlatform, canAccessScheduledTask, canAccessMock]);

  // 当选择"全部"节点时，如果当前在"生成测试用例"页签，自动切换到"管理测试用例"
  useEffect(() => {
    if (!hasRestoredModuleRef.current) return;
    if (selectedModule === 0 && activeTab === 'generate') {
      setActiveTab('manage');
    }
  }, [selectedModule]);

  // 加载会话列表
  const loadSessions = async () => {
    try {
      const response: ApiResponse<Session[]> | any = await sessionApi.getSessions();
      if (response.code === 200 && response.data) {
        setSessions(response.data);
        if (response.data.length > 0) {
          const storedSessionId = INITIAL_PERSISTED_STATE.selectedSessionId;
          const initialSession = response.data.find((session: Session) => session.id === storedSessionId) || response.data[0];
          setSelectedSession(initialSession);
          loadModules(initialSession.id);
          loadTestcases(initialSession.id);
        } else {
          setSelectedSession(null);
          setModules([]);
          setSelectedModule(0);
          hasRestoredModuleRef.current = true;
          canPersistSelectionRef.current = true;
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
        showMessage('success', '创建成功', '会话已成功创建');
      }
    } catch (error) {
      console.error('创建会话失败:', error);
      showMessage('error', '创建失败', '会话创建失败，请重试');
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
        showMessage('success', '创建成功', '模块已成功创建');
      }
    } catch (error) {
      console.error('创建模块失败:', error);
      showMessage('error', '创建失败', '模块创建失败，请重试');
    }
  };

  // 加载模块列表
  const loadModules = async (sessionId: number | any) => {
    try {
      const response: ApiResponse<Module[]> | any = await moduleApi.getModules(sessionId);
      if (response.code === 200 && response.data) {
        setModules(response.data);
        const storedModuleId = INITIAL_PERSISTED_STATE.selectedModuleId;
        const hasStoredModule = storedModuleId !== null && response.data.some((module: Module) => module.id === storedModuleId);
        setSelectedModule(hasStoredModule ? storedModuleId! : 0);
        hasRestoredModuleRef.current = true;
        canPersistSelectionRef.current = true;
      } else {
        setModules([]);
        setSelectedModule(0);
        hasRestoredModuleRef.current = true;
        canPersistSelectionRef.current = true;
      }
    } catch (error) {
      console.error('加载模块失败:', error);
    }
  };

  // 选择模块
  const handleSelectModule = (module: Module) => {
    canPersistSelectionRef.current = true;
    setSelectedModule(module.id!);
    // 可以在这里添加根据模块筛选测试用例的逻辑
    loadTestcases(selectedSession?.id ?? undefined, { module_id: module.id || 'all' });
    setFilters({ case_name: '', bug_id: '', exist_bug: false, status: undefined, module_id: module.id || 'all' });
  };

  // 选择全部模块
  const handleSelectAllModules = () => {
    canPersistSelectionRef.current = true;
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
        showMessage('success', '更新成功', response.message || '模块已成功更新');
      } else {
        showMessage('error', '更新失败', response.message || '模块更新失败，请重试');
      }
    } catch (error) {
      console.error('更新模块失败:', error);
      showMessage('error', '更新失败', '模块更新失败，请重试');
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
        showMessage('success', '删除成功', response.message || '模块已成功删除');
      } else {
        showMessage('error', '删除失败', response.message || '模块删除失败，请重试');
      }
    } catch (error) {
      console.error('删除模块失败:', error);
      showMessage('error', '删除失败', '模块删除失败，请重试');
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
        showMessage('success', '更新成功', response.message || '会话已成功更新');
      } else {
        showMessage('error', '更新失败', response.message || '会话更新失败，请重试');
      }
    } catch (error) {
      console.error('更新会话失败:', error);
      showMessage('error', '更新失败', '会话更新失败，请重试');
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
        showMessage('success', '删除成功', response.message || '会话已成功删除');
        setSessions(sessions.filter(session => session.id !== sessionToDelete));
        if (selectedSession?.id === sessionToDelete) {
          const sessionsId = sessions.length > 0 ? sessions[0] : null;
          setSelectedSession(sessionsId);
          loadTestcases(sessionsId?.id);
        }
      } else {
        showMessage('error', '删除失败', response.message || '会话删除失败，请重试');
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
    canPersistSelectionRef.current = true;
    setSelectedSession(session);
    setSelectedModule(0);
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
    const testcaseIndex = testcases.findIndex(tc => tc.id === testcase.id);
    setSelectedTestcase(testcase);
    setIsViewModalVisible(true);
    setCurrentIndex(testcaseIndex >= 0 ? testcaseIndex : 0);
  };

  // 编辑测试用例
  const handleEditTestcase = (testcase: TestCase) => {
    // 先重置表单，再打开弹窗，避免 modal 内的 setFieldsValue 被随后 reset 清掉
    form.resetFields();
    setSelectedTestcase(testcase);
    setIsEditModalVisible(true);
  };

  const handleEditTestcaseFromView = (testcase: TestCase | null) => {
    if (!testcase) return;
    setReturnToViewAfterEdit(true);
    setIsViewModalVisible(false);
    handleEditTestcase(testcase);
  };

  const handleDeleteTestcaseFromView = async (testcase: TestCase | null) => {
    if (!testcase || !selectedSession) return;

    const testcaseIndex = testcases.findIndex((item) => item.id === testcase.id);

    try {
      const response: any = await testcaseApi.deleteTestcase(selectedSession.id, [testcase.id]);
      if (response.code !== 200) {
        throw new Error(response.message || '测试用例删除失败，请重试');
      }

      const remainingTestcases = testcases.filter((item) => item.id !== testcase.id);
      setTestcases(remainingTestcases);

      if (remainingTestcases.length === 0) {
        setSelectedTestcase(null);
        setIsViewModalVisible(false);
        setCurrentIndex(0);
      } else {
        const nextIndex = Math.min(testcaseIndex, remainingTestcases.length - 1);
        setSelectedTestcase(remainingTestcases[nextIndex]);
        setCurrentIndex(nextIndex);
      }

      showMessage('success', '删除成功', response.message || '测试用例已成功删除');

      loadTestcases(selectedSession.id, { ...filters, module_id: selectedModule === 0 ? undefined : Number(selectedModule) });
    } catch (error) {
      console.error('删除测试用例失败:', error);
      showMessage('error', '删除失败', error instanceof Error ? error.message : '测试用例删除失败，请重试');
      throw error;
    }
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
        preset_conditions: Array.isArray(values.preset_conditions)
          ? values.preset_conditions
          : (values.preset_conditions || '').split('\n').filter((item: string) => item.trim()),
        // steps可能是数组（从EditTestcaseModal传来）或字符串
        steps: Array.isArray(values.steps)
          ? values.steps
          : values.steps.split('\n').filter((item: string) => item.trim()),
        expected_results: Array.isArray(values.expected_results)
          ? values.expected_results
          : (values.expected_results || '').split('\n').filter((item: string) => item.trim())
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
        setSelectedTestcase(updatedTestcase);

        if (returnToViewAfterEdit) {
          setIsViewModalVisible(true);
          setReturnToViewAfterEdit(false);
        }

        // 显示成功通知
        showMessage('success', '更新成功', response.message || '测试用例已成功更新');
      } else {
        showMessage('error', '更新失败', response.message || '测试用例更新失败，请重试');
      }
    } catch (error) {
      console.error('更新测试用例失败:', error);
      showMessage('error', '更新失败', '测试用例更新失败，请重试');
    } finally {
      setLoading(false);
    }
  };

  const handleCancelEditTestcase = () => {
    setIsEditModalVisible(false);
    if (returnToViewAfterEdit && selectedTestcase) {
      setIsViewModalVisible(true);
      setReturnToViewAfterEdit(false);
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
        showMessage('success', '删除成功', response.message || `已成功删除 ${ids.length} 个测试用例`);
      } else {
        showMessage('error', '删除失败', response.message || `删除 ${ids.length} 个测试用例失败，请重试`);
      }
    } catch (error) {
      console.error('批量删除测试用例失败:', error);
      showMessage('error', '删除失败', '批量删除测试用例失败，请重试');
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

        showMessage('success', '删除成功', response.message || '测试用例已成功删除');
      } else {
        showMessage('error', '删除失败', response.message || '测试用例删除失败，请重试');
      }
    } catch (error) {
      console.error('删除测试用例失败:', error);
      showMessage('error', '删除失败', '测试用例删除失败，请重试');
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

  const updateTestcaseExecutionStatus = async (testcase: TestCase | null, status: TestCaseStatus, bugId?: number) => {
    if (!testcase || !selectedSession) return;

    const updatedTestcase = {
      ...testcase,
      status,
      bug_id: bugId,
    };

    const response: any = await testcaseApi.updateTestcase(
      selectedSession.id,
      testcase.id,
      updatedTestcase,
    );

    if (response.code !== 200) {
      throw new Error(response.message || '测试用例标记执行失败，请重试');
    }

    setTestcases(testcases.map((tc) => (
      tc.id === testcase.id ? updatedTestcase : tc
    )));
    setSelectedTestcase(updatedTestcase);
    setCurrentIndex(testcases.findIndex(tc => tc.id === updatedTestcase.id));
    message.success(`已标记为${status === TestCaseStatus.PASSED ? '通过' : '未通过'}`);
  };

  // 确认执行测试用例
  const handleConfirmCompleteTestcase = async (status: TestCaseStatus, bugId?: number) => {
    if (!testcaseToComplete || !selectedSession) return;

    try {
      await updateTestcaseExecutionStatus(testcaseToComplete, status, bugId);
    } catch (error) {
      console.error('执行测试用例失败:', error);
      showMessage('error', '执行失败', error instanceof Error ? error.message : '测试用例标记执行失败，请重试');
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

  const loadExecutionLogs = async (testcase: TestCase, openModal = true) => {
    if (!selectedSession) return;
    if (openModal) {
      setExecutionLogTestcase(testcase);
      setIsExecutionLogModalVisible(true);
    }
    setExecutionLogsLoading(true);
    try {
      const res = await testcaseApi.getExecutionLogs(selectedSession.id, testcase.id);
      if (res.code === 200 && res.data) {
        setExecutionLogs(res.data);
      } else {
        setExecutionLogs([]);
        showMessage('error', '加载执行日志失败', res.message || '加载执行日志失败');
      }
    } catch (error: any) {
      setExecutionLogs([]);
      showMessage('error', '加载执行日志失败', error?.response?.data?.message || error.message || '加载执行日志失败');
    } finally {
      setExecutionLogsLoading(false);
    }
  };

  const handleViewExecutionLogs = (testcase: TestCase) => {
    loadExecutionLogs(testcase, true);
  };

  const handleInferTestcaseDependencies = async (testcase: TestCase) => {
    if (!selectedSession) return;
    try {
      const res = await testcaseApi.inferDependencies(selectedSession.id, testcase.id);
      if (res.code === 200) {
        const data = res.data as { added_post_actions?: number; replaced_fields?: number; extra_endpoint_ids?: number[] } || {};
        showMessage('success', '依赖推断完成', `发现 ${data.extra_endpoint_ids?.length || 0} 个依赖接口`);
        loadTestcases(selectedSession.id, filters);
      } else {
        showMessage('error', '依赖推断失败', res.message || '依赖推断失败');
      }
    } catch (error: any) {
      showMessage('error', '依赖推断失败', error?.response?.data?.message || error.message || '依赖推断失败');
    }
  };

  // 执行测试用例 API 执行
  const handleApiExecuteTestcase = async (testcase: TestCase) => {
    if (!selectedSession) return;
    // Set executing state in the table through a callback approach
    showMessage('info', '正在执行...', `正在执行测试用例: ${testcase.case_name}`);
    try {
      const res = await testcaseApi.executeTestcase(selectedSession.id, testcase.id);
      if (res.code === 200 && res.data) {
        const result = res.data;
        if (result.passed) {
          showMessage('success', '执行成功', `测试用例 ${testcase.case_name} 已执行通过`);
        } else {
          // 构建详细的失败信息
          const stepResult = result.result?.step;
          let detailMessage = `测试用例 ${testcase.case_name} 执行未通过`;

          if (stepResult) {
            // 显示断言失败详情
            const failedAssertions = (stepResult.assertions || []).filter((a: any) => !a.passed);
            if (failedAssertions.length > 0) {
              const assertionDetails = failedAssertions.map((a: any) =>
                `${a.type}: 期望 ${a.expected}, 实际 ${a.actual}`
              ).join('\n');
              detailMessage += `\n\n断言失败:\n${assertionDetails}`;
            }

            // 显示请求和响应信息
            if (stepResult.request) {
              detailMessage += `\n\n请求: ${stepResult.request.method} ${stepResult.request.url}`;
              if (stepResult.request.body) {
                const reqBodyStr = typeof stepResult.request.body === 'string'
                  ? stepResult.request.body
                  : JSON.stringify(stepResult.request.body, null, 2);
                detailMessage += `\n请求体: ${reqBodyStr.substring(0, 500)}${reqBodyStr.length > 500 ? '...' : ''}`;
              }
            }
            if (stepResult.response) {
              detailMessage += `\n响应状态码: ${stepResult.response.status_code}`;
              if (stepResult.response.body) {
                const bodyStr = typeof stepResult.response.body === 'string'
                  ? stepResult.response.body
                  : JSON.stringify(stepResult.response.body, null, 2);
                detailMessage += `\n响应内容: ${bodyStr.substring(0, 500)}${bodyStr.length > 500 ? '...' : ''}`;
              }
            }

            // 显示错误详情
            if (stepResult.detail) {
              detailMessage += `\n\n错误详情: ${stepResult.detail}`;
            }
          }

          // 使用 Modal 显示详细错误信息
          Modal.error({
            title: '测试用例执行失败',
            content: (
              <div style={{ whiteSpace: 'pre-wrap', maxHeight: '60vh', overflow: 'auto' }}>
                {detailMessage}
              </div>
            ),
            width: 600,
            okText: '确定',
          });
          await loadExecutionLogs(testcase, true);
        }
        // 重新加载测试用例
        loadTestcases(selectedSession.id, filters);
        if (result.passed && isExecutionLogModalVisible && executionLogTestcase?.id === testcase.id) {
          await loadExecutionLogs(testcase, false);
        }
      } else {
        showMessage('error', '执行失败', res.message || '执行失败');
      }
    } catch (error: any) {
      showMessage('error', '执行失败', error?.response?.data?.message || error.message || '执行失败');
    }
  };
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
  const [isMcpConfigVisible, setIsMcpConfigVisible] = useState(false);
  const [isSkillsHubVisible, setIsSkillsHubVisible] = useState(false);
    const saved = localStorage.getItem('selectedSkills');
  const [selectedSkills, setSelectedSkills] = useState<string[]>(() => {
    return saved ? JSON.parse(saved) : [];
  });

  const [selectedApiEndpointId, setSelectedApiEndpointId] = useState<number[]>([]);
  const [selectedApiProjectId, setSelectedApiProjectId] = useState<number | null>(null);
  const [apiEndpointOverrides, setApiEndpointOverrides] = useState<Record<number, { body?: string; headers?: any[]; parameters?: any[] }>>({});
  const [settingForm] = Form.useForm();
  // 设置类型：api或ollama，单选
  const [settingType, setSettingType] = useState<'api' | 'ollama'>('api');
  const [modelList, setModelList] = useState<string[]>([]);
  const [modelListLoading, setModelListLoading] = useState(false);

  // 打开设置模态框
  const handleOpenSettingModal = async () => {
    if (!canManageSettings) return;

    try {
      // 先从后端加载配置
      const res = await configApi.getConfig();
      // console.log('打开弹窗加载配置结果:', res);
      if (res.code === 200 && res.data) {
        const config = res.data;
        // console.log('配置数据:', config);

        // 先设置 settingType 确保字段显示
        const newType = (config.model_type as 'api' | 'ollama') || 'api';
        setSettingType(newType);

        // 先打开弹窗
        setIsSettingModalVisible(true);

        // 延迟设置表单值，等待 settingType 更新和字段渲染
        setTimeout(() => {
          const values = {
            setting_type: newType,
            api_key: config.api_key || '',
            api_base_url: config.api_base_url || 'https://api.deepseek.com',
            api_model: config.api_model || 'deepseek-v4-flash',
            api_proxy_url: config.api_proxy_url || '',
            ollama_url: config.ollama_url || 'http://localhost:11434',
            ollama_model: config.ollama_model || 'llama3',
            lanhu_cookie: config.lanhu_cookie || '',
            bug_link_template: config.bug_link_template || '',
          };
          // console.log('设置表单值:', values);
          settingForm.setFieldsValue(values);
          // console.log('设置后的表单值:', settingForm.getFieldsValue());
        }, 300);
      }
    } catch (error) {
      console.error('加载配置失败:', error);
      // 如果加载失败，使用默认值
      setSettingType('api');
      setIsSettingModalVisible(true);
      setTimeout(() => {
        settingForm.setFieldsValue({
          setting_type: 'api',
          api_key: '',
          api_base_url: 'https://api.deepseek.com',
          api_model: 'deepseek-v4-flash',
          api_proxy_url: '',
          ollama_url: 'http://localhost:11434',
          ollama_model: 'llama3'
        });
      }, 300);
    }
  };

  // 设置类型改变处理
  const handleSettingTypeChange = (e: any) => {
    const value = e.target.value;
    setSettingType(value);
    // 重置表单对应的字段
    if (value === 'api') {
      settingForm.setFieldsValue({
        api_base_url: settingForm.getFieldValue('api_base_url') || 'https://api.deepseek.com',
        api_model: settingForm.getFieldValue('api_model') || 'deepseek-v4-flash',
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

      // 清理字段值
      const configData: any = {};
      if (typeof values.api_key === 'string') {
        configData.api_key = values.api_key.trim();
      }
      if (typeof values.api_base_url === 'string') {
        configData.api_base_url = values.api_base_url.trim();
      }
      if (typeof values.api_model === 'string') {
        configData.api_model = values.api_model.trim();
      }
      if (typeof values.api_proxy_url === 'string') {
        configData.api_proxy_url = values.api_proxy_url.trim();
      }
      if (typeof values.ollama_url === 'string') {
        configData.ollama_url = values.ollama_url.trim();
      }
      if (typeof values.ollama_model === 'string') {
        configData.ollama_model = values.ollama_model.trim();
      }

      // 设置模型类型
      configData.model_type = values.setting_type || 'api';

      // 保存蓝湖 Cookie
      if (typeof values.lanhu_cookie === 'string') {
        configData.lanhu_cookie = values.lanhu_cookie.trim();
      }

      // 保存 Bug 链接模板
      if (typeof values.bug_link_template === 'string') {
        configData.bug_link_template = values.bug_link_template.trim();
      }

      // 调用后端 API 保存配置
      await configApi.updateConfig(configData);

      // 关闭模态框
      setIsSettingModalVisible(false);

      // 显示成功通知
      showMessage('success', '保存成功', '配置已保存到服务器');
    } catch (error: any) {
      console.error('保存配置失败:', error);
      showMessage('error', '保存失败', error?.response?.data?.message || '保存配置时发生错误，请重试');
    } finally {
      setLoading(false);
    }
  };

  // 获取模型列表
  const fetchModelList = async () => {
    const fields = settingForm.getFieldsValue();
    const apiKey = (fields.api_key || '').trim();
    const apiBaseUrl = (fields.api_base_url || 'https://api.deepseek.com').trim();
    if (!apiKey) {
      showMessage('warning', '请先填写 API Key');
      return;
    }
    setModelListLoading(true);
    try {
      const res = await configApi.listModels(apiKey, apiBaseUrl);
      if (res.code === 200 && res.data) {
        const ids = (res.data as { id: string }[]).map(m => m.id);
        setModelList(ids);
        if (ids.length === 0) {
          showMessage('info', '该服务未返回模型列表，您可以手动输入模型名称');
        } else {
          showMessage('success', `已获取 ${ids.length} 个模型`);
        }
      } else {
        showMessage('error', '获取模型列表失败', (res as any).message);
      }
    } catch (e: any) {
      console.error('获取模型列表失败:', e);
      showMessage('error', '获取模型列表失败', e?.response?.data?.message || e?.message || '网络错误');
    } finally {
      setModelListLoading(false);
    }
  };

  // 生成测试用例
  const handleGenerateTestcases = async () => {
    if (!selectedSession || (!requirement.trim()) && !imageBase64) return;

    try {
      setLoading(true);

      // 从表单获取模型配置（已从后端加载）
      const formValues = settingForm.getFieldsValue();
      const modelConfig = {
        model_type: formValues.setting_type || 'api',
        api_key: (formValues.api_key || '').trim(),
        api_base_url: (formValues.api_base_url || '').trim(),
        api_proxy_url: (formValues.api_proxy_url || '').trim(),
        api_model: (formValues.api_model || 'deepseek-v4-flash').trim(),
        ollama_url: (formValues.ollama_url || '').trim(),
        ollama_model: (formValues.ollama_model || '').trim()
      };

      // 检查配置是否完整
      if (modelConfig.model_type === 'api' && !modelConfig.api_key) {
        handleOpenSettingModal();
        showMessage('error', '配置缺失', '请先配置 API Key');
        return;
      }

      setSettingButtonStatus(true); // 点击生成按钮设置按钮置灰

      // 只有当requirement非空或imageBase64非空时才调用API
      if (requirement.trim() || imageBase64) {
        // 调用生成测试用例API（后端会在生成成功后自动保存历史提示词）
        const response: ApiResponse<TestCase> | any = await testcaseApi.generateTestcases(
          selectedSession.id,
          requirement.trim(),
          modelConfig,
          imageBase64, // 传递图片base64数据
          selectedModule === 0 ? undefined : Number(selectedModule), // 传递模块ID
          selectedSkills.length > 0 ? selectedSkills : undefined,
          selectedApiEndpointId.length > 0 ? selectedApiEndpointId : undefined,
          selectedApiProjectId,
          Object.keys(apiEndpointOverrides).length > 0 ? apiEndpointOverrides : null
        );

        if (response.code === 200) {
          loadTestcases(selectedSession.id);
          setRequirement('');
          setImageBase64(null); // 清空图片数据
          // 刷新历史提示词列表
          setHistoryPromptRefreshKey(prev => prev + 1);
          showMessage('success', '生成成功', '测试用例已成功生成');
        } else {
          showMessage('error', '生成失败', response.message || '生成测试用例失败，请重试');
        }
      } else {
        showMessage('error', '生成失败', '请上传图片文件或输入需求描述');
      }

    } catch (error: any) {
      console.error('生成测试用例失败:', error);
      showMessage('error', '生成失败', `${error.response?.data?.detail || error.response?.data?.message || error.response?.data?.data || '生成测试用例时发生错误，请重试'}`);
    } finally {
      setLoading(false);
      setSettingButtonStatus(false); // 最终总会恢复按钮状态
    }
  };

  // 监听 4 阶段 Pipeline 完成事件，刷新测试用例列表
  useEffect(() => {
    const handlePipelineCompleted = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail?.sessionId && selectedSession && selectedSession.id === detail.sessionId) {
        loadTestcases(selectedSession.id, filters);
        setHistoryPromptRefreshKey(prev => prev + 1);
      }
    };

    window.addEventListener('pipeline-completed', handlePipelineCompleted);
    return () => window.removeEventListener('pipeline-completed', handlePipelineCompleted);
  }, [selectedSession?.id, filters]);

  const [nextButtonDisabled, setNextButtonDisabled] = useState(false);
  const [prevButtonDisabled, setPrevButtonDisabled] = useState(false);
  const [currentIndex, setCurrentIndex] = useState<number>(0);

  useEffect(() => {
    setPrevButtonDisabled(currentIndex <= 0);
    setNextButtonDisabled(testcases.length === 0 || currentIndex >= testcases.length - 1);
  }, [currentIndex, testcases.length]);

  const handleNextCase = async () => {
    if (!selectedSession || !selectedTestcase) return;

    const nextIndex = Math.min(currentIndex + 1, testcases.length - 1);
    const nextTestcase = testcases[nextIndex];
    if (!nextTestcase) return;
    setSelectedTestcase(nextTestcase);
    setCurrentIndex(nextIndex);
  };

  const handlePrevCase = async () => {
    if (!selectedSession || !selectedTestcase) return;

    const prevIndex = Math.max(currentIndex - 1, 0);
    const prevTestcase = testcases[prevIndex];
    if (!prevTestcase) return;
    setSelectedTestcase(prevTestcase);
    setCurrentIndex(prevIndex);
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

  const navigateToApiTest = () => {
    setCurrentPlatform('api-test');
    localStorage.setItem('currentPlatform', 'api-test');
  };

  const navigateToScheduledTask = () => {
    if (!canAccessScheduledTask) return;
    setCurrentPlatform('scheduled-task');
    localStorage.setItem('currentPlatform', 'scheduled-task');
  };

  const navigateToMock = () => {
    if (!canAccessMock) return;
    setCurrentPlatform('mock-api');
    localStorage.setItem('currentPlatform', 'mock-api');
  };
  

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: '#4f46e5',
          colorSuccess: '#10b981',
          colorWarning: '#f59e0b',
          colorError: '#f43f5e',
          colorInfo: '#4f46e5',
          colorText: '#0f172a',
          colorTextSecondary: '#475569',
          colorTextTertiary: '#94a3b8',
          colorBgContainer: '#ffffff',
          colorBgLayout: '#f8fafc',
          colorBgElevated: '#ffffff',
          colorBorder: '#e2e8f0',
          colorBorderSecondary: '#f1f5f9',
          borderRadius: 6,
          fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Oxygen', 'Ubuntu', 'Cantarell', 'Fira Sans', 'Droid Sans', 'Helvetica Neue', sans-serif",
          fontSize: 14,
          controlHeight: 36,
          colorLink: '#4f46e5',
          colorLinkHover: '#4338ca',
          colorLinkActive: '#3730a3',
        },
        algorithm: theme.defaultAlgorithm,
      }}
    >
      {currentPlatform === 'home' ? (
        <HomePage
          onNavigateToAI={navigateToAITestcase}
          onNavigateToIoT={navigateToIoTMock}
          onNavigateToApiTest={navigateToApiTest}
          onNavigateToScheduledTask={navigateToScheduledTask}
          onNavigateToMock={navigateToMock}
          canAccessScheduledTask={canAccessScheduledTask}
          canAccessMock={canAccessMock}
        />
      ) : currentPlatform === 'iot-mock' ? (
        <div>
          <SubPlatformHeader
            title="测试工具平台"
            onBackToHome={navigateToHome}
            environmentName={getCurrentEnvironment()?.name}
            onGlobalParamsOpen={() => canManageGlobalParams && setIsGlobalParamsModalVisible(true)}
            canManageGlobalParams={canManageGlobalParams}
          />
          <IoTDataPushPlatform
            currentEnvironmentId={currentEnvironmentId}
            canManageGlobalParams={canManageGlobalParams}
          />
        </div>
      ) : currentPlatform === 'api-test' ? (
        <div>
          <SubPlatformHeader
            title="接口场景测试工具"
            onBackToHome={navigateToHome}
            onGlobalParamsOpen={() => canManageGlobalParams && setIsGlobalParamsModalVisible(true)}
            canManageGlobalParams={canManageGlobalParams}
          />
          <ApiScenarioTestTool />
        </div>
      ) : currentPlatform === 'scheduled-task' ? (
        <div>
          <SubPlatformHeader
            title="测试工具平台"
            onBackToHome={navigateToHome}
            environmentName={getCurrentEnvironment()?.name}
            onGlobalParamsOpen={() => canManageGlobalParams && setIsGlobalParamsModalVisible(true)}
            canManageGlobalParams={canManageGlobalParams}
          />
          <div style={{ padding: 'var(--space-4)' }}>
            <ScheduledTaskManager />
          </div>
        </div>
      ) : currentPlatform === 'mock-api' ? (
        <div>
          <SubPlatformHeader
            title="测试工具平台"
            onBackToHome={navigateToHome}
            environmentName={getCurrentEnvironment()?.name}
            onGlobalParamsOpen={() => canManageGlobalParams && setIsGlobalParamsModalVisible(true)}
            canManageGlobalParams={canManageGlobalParams}
          />
          <IoTMockPlatform />
        </div>
      ) : (
        <Layout style={{ height: '100vh', overflow: 'hidden' }}>
          <HeaderComponent
            onSettingsOpen={handleOpenSettingModal}
            settingButtonStatus={settingButtonStatus}
            onBackToHome={navigateToHome}
            onMcpConfigOpen={() => canManageMcp && setIsMcpConfigVisible(true)}
            onSkillsHubOpen={() => canManageSkills && setIsSkillsHubVisible(true)}
            onGlobalParamsOpen={() => canManageGlobalParams && setIsGlobalParamsModalVisible(true)}
            canManageSettings={canManageSettings}
            canManageMcp={canManageMcp}
            canManageSkills={canManageSkills}
            canManageGlobalParams={canManageGlobalParams}
          />
          <Layout style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
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
            <Layout style={{ padding: 'var(--space-4)', minHeight: 0, overflow: 'hidden' }}>
              <Content style={{ background: 'var(--color-bg-elevated)', padding: 'var(--space-4)', margin: 0, borderRadius: 'var(--radius-lg)', border: '1px solid var(--color-border-light)', height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                <Tabs
                  className="rtp-tabs-host"
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
                          // 只显示"管理测试用例"和"API调用配置"页签
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
                                selectedTestcaseId={selectedTestcase?.id ?? null}
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
                                onApiExecute={handleApiExecuteTestcase}
                                onViewExecutionLogs={handleViewExecutionLogs}
                                onInferDependencies={handleInferTestcaseDependencies}
                              />
                            ),
                          },
                          {
                            key: 'api-config',
                            label: 'API调用配置',
                            children: (
                              <SessionApiConfig
                                selectedSession={selectedSession}
                                onConfigUpdated={() => {
                                  // 配置更新后的回调
                                  if (selectedSession) {
                                    loadTestcases(selectedSession.id, filters);
                                  }
                                }}
                              />
                            ),
                          },
                        ]
                      : [
                          // 显示三个页签
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
                                historyPromptRefreshKey={historyPromptRefreshKey}
                                selectedApiEndpointId={selectedApiEndpointId}
                                onApiEndpointChange={(ids: number[]) => setSelectedApiEndpointId(ids)}
                                selectedApiProjectId={selectedApiProjectId}
                                onApiProjectChange={setSelectedApiProjectId}
                                apiEndpointOverrides={apiEndpointOverrides}
                                onApiEndpointOverridesChange={setApiEndpointOverrides}
                              />
                            ),
                          },
                          {
                            key: 'structure',
                            label: '用例结构',
                            children: (
                              <RequirementTestPointManager
                                sessionId={selectedSession?.id ?? null}
                                modules={modules}
                                selectedModule={selectedModule}
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
                                selectedTestcaseId={selectedTestcase?.id ?? null}
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
                                onApiExecute={handleApiExecuteTestcase}
                                onViewExecutionLogs={handleViewExecutionLogs}
                                onInferDependencies={handleInferTestcaseDependencies}
                              />
                            ),
                          },
                          {
                            key: 'api-config',
                            label: 'API调用配置',
                            children: (
                              <SessionApiConfig
                                selectedSession={selectedSession}
                                onConfigUpdated={() => {
                                  // 配置更新后的回调
                                  if (selectedSession) {
                                    loadTestcases(selectedSession.id, filters);
                                  }
                                }}
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
            sessionId={selectedSession?.id}
            selectedTestcase={selectedTestcase}
            nextButtonDisabled={nextButtonDisabled}
            prevButtonDisabled={prevButtonDisabled}
            onNext={handleNextCase}
            onPrev={handlePrevCase}
            onCancel={() => setIsViewModalVisible(false)}
            onEdit={handleEditTestcaseFromView}
            onDelete={handleDeleteTestcaseFromView}
            onMarkPassed={(testcase) => updateTestcaseExecutionStatus(testcase, TestCaseStatus.PASSED)}
            onMarkFailed={(testcase, bugId) => updateTestcaseExecutionStatus(testcase, TestCaseStatus.FAILED, bugId)}
          />
          <TestCaseExecutionLogModal
            visible={isExecutionLogModalVisible}
            testcase={executionLogTestcase}
            logs={executionLogs}
            loading={executionLogsLoading}
            onCancel={() => {
              setIsExecutionLogModalVisible(false);
              setExecutionLogTestcase(null);
              setExecutionLogs([]);
            }}
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
                onCancel={handleCancelEditTestcase}
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
            <div style={{ marginBottom: 'var(--space-3)' }}>
              <label style={{ display: 'block', marginBottom: 'var(--space-1)', fontSize: 'var(--font-size-sm)', fontWeight: 'var(--font-weight-medium)' }}>父模块：</label>
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
              <label style={{ display: 'block', marginBottom: 'var(--space-1)', fontSize: 'var(--font-size-sm)', fontWeight: 'var(--font-weight-medium)' }}>模块名称：</label>
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
            <div style={{ marginBottom: 'var(--space-3)' }}>
              <label style={{ display: 'block', marginBottom: 'var(--space-1)', fontSize: 'var(--font-size-sm)', fontWeight: 'var(--font-weight-medium)' }}>父模块：</label>
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
              <label style={{ display: 'block', marginBottom: 'var(--space-1)', fontSize: 'var(--font-size-sm)', fontWeight: 'var(--font-weight-medium)' }}>模块名称：</label>
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
              style={{ marginBottom: 'var(--space-2)' }}
            />
          </Modal>

          {canManageSettings && (
            <SettingsModal
              visible={isSettingModalVisible}
              settingForm={settingForm}
              settingType={settingType}
              loading={loading}
              onCancel={() => setIsSettingModalVisible(false)}
              onFinish={handleSaveSetting}
              onSettingTypeChange={handleSettingTypeChange}
              modelList={modelList}
              modelListLoading={modelListLoading}
              onFetchModels={fetchModelList}
            />
          )}

          {canManageMcp && (
            <McpConfigModal
              visible={isMcpConfigVisible}
              onCancel={() => setIsMcpConfigVisible(false)}
              onSave={() => setIsMcpConfigVisible(false)}
            />
          )}

          {canManageSkills && (
            <SkillsHubModal
              visible={isSkillsHubVisible}
              onCancel={() => setIsSkillsHubVisible(false)}
              onSave={(names) => {
                setSelectedSkills(names);
                setIsSkillsHubVisible(false);
              }}
            />
          )}

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
        open={isGlobalParamsModalVisible && canManageGlobalParams}
        onOk={() => setIsGlobalParamsModalVisible(false)}
        onCancel={() => setIsGlobalParamsModalVisible(false)}
        width={600}
        styles={{ body: { padding: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column', maxHeight: '65vh' } }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden', height: '100%' }}>
          {/* 环境选择和管理 - 固定区域 */}
          <div style={{ padding: 'var(--space-4) var(--space-5)', borderBottom: '1px solid var(--color-border-light)', flexShrink: 0 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-3)' }}>
              <h3 style={{ margin: 0, fontSize: 'var(--font-size-base)', fontWeight: 'var(--font-weight-semibold)' }}>环境管理</h3>
              <Button
                type="primary"
                size="small"
                onClick={handleAddEnvironment}
              >
                添加环境
              </Button>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)' }}>
              {environments.map(env => (
                <div
                  key={env.id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    padding: 'var(--space-1) var(--space-3)',
                    borderRadius: 'var(--radius-full)',
                    background: currentEnvironmentId === env.id ? 'var(--color-primary)' : 'var(--color-border-light)',
                    color: currentEnvironmentId === env.id ? '#fff' : 'var(--color-text)',
                    cursor: 'pointer',
                    fontSize: 'var(--font-size-xs)',
                    gap: 'var(--space-1)',
                    transition: 'all 0.2s ease',
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
                    style={{ color: currentEnvironmentId === env.id ? '#fff' : 'var(--color-text-tertiary)', padding: 0, fontSize: 'var(--font-size-xs)' }}
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
                      style={{ color: currentEnvironmentId === env.id ? '#fff' : 'var(--color-danger)', padding: 0, fontSize: '14px' }}
                      onClick={(e) => e.stopPropagation()}
                    >
                      ×
                    </Button>
                  </Popconfirm>
                </div>
              ))}
            </div>
          </div>

          {/* 参数提示 - 固定区域 */}
          <p style={{ color: 'var(--color-text-secondary)', margin: 0, padding: 'var(--space-3) var(--space-5)', fontSize: 'var(--font-size-sm)', flexShrink: 0, borderBottom: '1px solid var(--color-border-light)', background: 'var(--color-bg-secondary)' }}>
            全局参数将应用于所有请求，可在URL、请求头和请求体中使用 &#123;&#123;variable&#125;&#125; 或 $&#123;variable&#125; 语法引用
          </p>

          {/* 参数列表 - 可滚动区域 */}
          <div style={{ flex: 1, overflowY: 'auto', padding: 'var(--space-3) var(--space-5)' }}>
            {getCurrentEnvironment().parameters.map((param, index) => (
              <Space
                key={index}
                style={{ width: '100%', marginBottom: 'var(--space-3)' }}
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
          </div>

          {/* 添加按钮 - 固定底部 */}
          <div style={{ padding: 'var(--space-2) var(--space-5) var(--space-4)', borderTop: '1px solid var(--color-border-light)', flexShrink: 0 }}>
            <Button
              type="dashed"
              icon={<PlusOutlined />}
              onClick={handleAddEnvironmentParameter}
              style={{ width: '100%' }}
              size="middle"
            >
              添加全局参数
            </Button>
          </div>
        </div>
      </Modal>

      {/* 添加环境模态框 - 全局渲染 */}
      <Modal
        title="创建新环境"
        open={isAddEnvModalVisible && canManageGlobalParams}
        onOk={handleConfirmAddEnvironment}
        onCancel={() => setIsAddEnvModalVisible(false)}
        width={400}
      >
        <Input
          placeholder="请输入环境名称"
          value={newEnvironmentName}
          onChange={(e) => setNewEnvironmentName(e.target.value)}
          style={{ marginBottom: 'var(--space-4)' }}
        />
        <div style={{ color: 'var(--color-text-tertiary)', fontSize: 'var(--font-size-xs)' }}>
          环境名称用于区分不同的参数配置集
        </div>
      </Modal>

      {/* 编辑环境模态框 - 全局渲染 */}
      <Modal
        title="编辑环境"
        open={isEditEnvModalVisible && canManageGlobalParams}
        onOk={handleConfirmEditEnvironment}
        onCancel={() => setIsEditEnvModalVisible(false)}
        width={400}
      >
        <Input
          placeholder="请输入环境名称"
          value={editEnvironmentName}
          onChange={(e) => setEditEnvironmentName(e.target.value)}
          onPressEnter={handleConfirmEditEnvironment}
          style={{ marginBottom: 'var(--space-4)' }}
        />
        <div style={{ color: 'var(--color-text-tertiary)', fontSize: 'var(--font-size-xs)' }}>
          环境名称用于区分不同的参数配置集
        </div>
      </Modal>

      {/* 变量助手 - 全局渲染 */}
      <VariableAssistant
        environmentId={currentEnvironmentId ? parseInt(currentEnvironmentId) || undefined : undefined}
        environmentVariables={getCurrentEnvironment().parameters.filter(p => p.key).map(p => ({ key: p.key, value: p.value }))}
      />
    </ConfigProvider>
  );
};

export default App;




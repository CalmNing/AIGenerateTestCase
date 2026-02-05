import React, { useState, useEffect } from 'react';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { Layout, notification, Form, Tabs, Modal, Input, Button } from 'antd';
import { ApiResponse, Session, TestCase, TestCaseResponse, TestCaseStatus, Module, TestCaseFilters } from './types';
import { sessionApi, testcaseApi, moduleApi } from './services/api';
import HomePage from './components/HomePage';
import IoTMockPlatform from './components/IoTMockPlatform';
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
import ModuleSidebar from './components/ModuleSidebar';

const { Content } = Layout;

const App: React.FC = () => {

  // 导航状态管理
  const [currentPlatform, setCurrentPlatform] = useState<'home' | 'ai-testcase' | 'iot-mock'>(() => {
    // 从localStorage加载上次的平台状态
    const savedPlatform = localStorage.getItem('currentPlatform');
    return (savedPlatform as 'home' | 'ai-testcase' | 'iot-mock') || 'home';
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
  const [requirement, setRequirement] = useState('');
  const [loading, setLoading] = useState(false);
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
  const handleOpenAddModuleModal = () => {
    setIsAddModuleModalVisible(true);
  };

  // 创建模块
  const handleCreateModule = async () => {
    if (!selectedSession || !newModuleName.trim()) return;

    try {
      const response: ApiResponse<Module> | any = await moduleApi.createModule({
        module_name: newModuleName.trim(),
        session_id: selectedSession.id
      });

      if (response.code === 200 && response.data) {
        setModules([...modules, response.data]);
        setNewModuleName('');
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
    setIsEditModuleModalVisible(true);
  };

  // 更新模块
  const handleUpdateModule = async () => {
    if (!moduleToEdit || !editModuleName.trim()) return;

    try {
      const response: ApiResponse<Module> | any = await moduleApi.updateModule(moduleToEdit.id!, {
        ...moduleToEdit,
        module_name: editModuleName.trim()
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
        loadTestcases(selectedSession.id, filters);
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

          // 调用API
          const response: ApiResponse<TestCase> | any = await testcaseApi.generateTestcases(
            selectedSession.id,
            requirement.trim(),
            modelConfig,
            imageBase64, // 传递图片base64数据
            selectedModule === 0 ? undefined : Number(selectedModule) // 传递模块ID
          );
          if (response.code === 200 && response.data) {
            loadTestcases(selectedSession.id);
            setRequirement('');
            setImageBase64(null); // 清空图片数据
            notification.success({
              message: '生成成功',
              description: '测试用例已成功生成',
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


  return (
    <ConfigProvider locale={zhCN}>
      {currentPlatform === 'home' ? (
        <HomePage 
          onNavigateToAI={navigateToAITestcase} 
          onNavigateToIoT={navigateToIoTMock} 
        />
      ) : currentPlatform === 'iot-mock' ? (
        <div>
          <div style={{ padding: '16px', background: '#fff', borderBottom: '1px solid #e8e8e8', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h1 style={{ margin: 0 }}>IoT Mock 平台</h1>
            <Button onClick={navigateToHome}>返回首页</Button>
          </div>
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
                  onChange={setActiveTab}
                  items={[
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
                          imageBase64={imageBase64}
                          onImageChange={setImageBase64}
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
                        />
                      ),
                    },
                  ]}
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
            onCancel={() => setIsAddModuleModalVisible(false)}
            confirmLoading={loading}
          >
            <Input
              placeholder="请输入模块名称"
              value={newModuleName}
              onChange={(e) => setNewModuleName(e.target.value)}
              onPressEnter={handleCreateModule}
              style={{ marginBottom: '8px' }}
            />
          </Modal>

          <Modal
            title="编辑模块"
            open={isEditModuleModalVisible}
            onOk={handleUpdateModule}
            onCancel={() => setIsEditModuleModalVisible(false)}
            confirmLoading={loading}
          >
            <Input
              placeholder="请输入模块名称"
              value={editModuleName}
              onChange={(e) => setEditModuleName(e.target.value)}
              onPressEnter={handleUpdateModule}
              style={{ marginBottom: '8px' }}
            />
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
        </Layout>
      )}
    </ConfigProvider>
  );
};

export default App;

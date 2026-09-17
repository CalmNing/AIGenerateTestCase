import { useState, useEffect, useCallback } from 'react';
import { globalParameterApi } from '../services/api';

// 环境类型定义
export interface Environment {
  id: string;
  name: string;
  parameters: Array<{ key: string; value: string; description?: string }>;
  is_default?: boolean;
}

/**
 * 全局参数 / 环境管理领域 hook。
 *
 * 行为保持：从 App.tsx 原样迁移，状态、处理函数与 localStorage key
 * ('currentEnvironmentId') 完全一致；以同名返回，便于 App.tsx 直接解构使用。
 */
export function useEnvironments(canManageGlobalParams: boolean) {
  const [environments, setEnvironments] = useState<Environment[]>([
    { id: 'env-1', name: '默认环境', parameters: [] },
  ]);
  const [currentEnvironmentId, setCurrentEnvironmentId] = useState<string>(() => {
    return localStorage.getItem('currentEnvironmentId') || 'env-1';
  });
  const [isGlobalParamsModalVisible, setIsGlobalParamsModalVisible] = useState(false);
  const [newEnvironmentName, setNewEnvironmentName] = useState('');
  const [isAddEnvModalVisible, setIsAddEnvModalVisible] = useState(false);
  const [isEditEnvModalVisible, setIsEditEnvModalVisible] = useState(false);
  const [editingEnvironment, setEditingEnvironment] = useState<Environment | null>(null);
  const [editEnvironmentName, setEditEnvironmentName] = useState('');

  const fetchGlobalParameters = useCallback(async (envId?: string | null) => {
    try {
      const response = await globalParameterApi.getEnvironments();
      if (response.code === 200 && response.data) {
        const backendEnvironments = response.data.map((env: any) => ({
          id: env.id.toString(),
          name: env.name,
          parameters: env.parameters || [],
          is_default: env.is_default,
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
  }, []);

  // 获取当前环境
  const getCurrentEnvironment = useCallback((): Environment => {
    return environments.find(env => env.id === currentEnvironmentId) || environments[0];
  }, [environments, currentEnvironmentId]);

  // 切换环境
  const handleSwitchEnvironment = useCallback((envId: string) => {
    setCurrentEnvironmentId(envId);
    localStorage.setItem('currentEnvironmentId', envId);
  }, []);

  // 保存环境到后端
  const saveEnvironmentToBackend = useCallback(async (environment: Environment) => {
    try {
      if (environment.id === 'env-1') {
        // 创建新环境
        const response = await globalParameterApi.createEnvironment({
          name: environment.name,
          parameters: environment.parameters,
          is_default: environment.is_default || false,
        });
        if (response.code === 200 && response.data) {
          fetchGlobalParameters(response.data.id.toString());
        }
      } else {
        // 更新现有环境
        const response = await globalParameterApi.updateEnvironment(Number(environment.id), {
          name: environment.name,
          parameters: environment.parameters,
          is_default: environment.is_default || false,
        });
        if (response.code === 200) {
          fetchGlobalParameters(environment.id);
        }
      }
    } catch (error) {
      console.error('Failed to save environment:', error);
    }
  }, [fetchGlobalParameters]);

  // 添加环境参数
  const handleAddEnvironmentParameter = useCallback(async () => {
    const newEnvironments = [...environments];
    const envIndex = newEnvironments.findIndex(env => env.id === currentEnvironmentId);
    if (envIndex !== -1) {
      const newParameters = [...newEnvironments[envIndex].parameters, { key: '', value: '', description: '' }];
      newEnvironments[envIndex] = { ...newEnvironments[envIndex], parameters: newParameters };
      setEnvironments(newEnvironments);
      // 保存到后端
      await saveEnvironmentToBackend(newEnvironments[envIndex]);
    }
  }, [environments, currentEnvironmentId, saveEnvironmentToBackend]);

  // 删除环境参数
  const handleRemoveEnvironmentParameter = useCallback(async (index: number) => {
    const newEnvironments = [...environments];
    const envIndex = newEnvironments.findIndex(env => env.id === currentEnvironmentId);
    if (envIndex !== -1) {
      const newParameters = newEnvironments[envIndex].parameters.filter((_, i) => i !== index);
      newEnvironments[envIndex] = { ...newEnvironments[envIndex], parameters: newParameters };
      setEnvironments(newEnvironments);
      // 保存到后端
      await saveEnvironmentToBackend(newEnvironments[envIndex]);
    }
  }, [environments, currentEnvironmentId, saveEnvironmentToBackend]);

  // 处理环境参数变更（仅更新本地状态，失焦时再保存）
  const handleEnvironmentParameterChange = useCallback((index: number, field: 'key' | 'value' | 'description', value: string) => {
    setEnvironments(prev => {
      const envIndex = prev.findIndex(env => env.id === currentEnvironmentId);
      if (envIndex === -1) return prev;
      const newParameters = [...prev[envIndex].parameters];
      newParameters[index] = { ...newParameters[index], [field]: value };
      const newEnvironments = [...prev];
      newEnvironments[envIndex] = { ...newEnvironments[envIndex], parameters: newParameters };
      return newEnvironments;
    });
  }, [currentEnvironmentId]);

  // 参数编辑失焦后保存当前环境
  const handleSaveEnvironmentParameter = useCallback(async () => {
    const env = environments.find(item => item.id === currentEnvironmentId);
    if (env) {
      await saveEnvironmentToBackend(env);
    }
  }, [environments, currentEnvironmentId, saveEnvironmentToBackend]);

  // 打开添加环境模态框
  const handleAddEnvironment = useCallback(() => {
    setNewEnvironmentName('');
    setIsAddEnvModalVisible(true);
  }, []);

  // 确认添加环境
  const handleConfirmAddEnvironment = useCallback(async () => {
    if (newEnvironmentName) {
      try {
        const response = await globalParameterApi.createEnvironment({
          name: newEnvironmentName,
          parameters: [],
          is_default: environments.length === 0,
        });
        if (response.code === 200 && response.data) {
          fetchGlobalParameters(response.data.id.toString());
          setIsAddEnvModalVisible(false);
        }
      } catch (error) {
        console.error('Failed to add environment:', error);
      }
    }
  }, [newEnvironmentName, environments.length, fetchGlobalParameters]);

  // 删除环境
  const handleRemoveEnvironment = useCallback(async (envId: string) => {
    try {
      const response = await globalParameterApi.deleteEnvironment(Number(envId));
      if (response.code === 200) {
        fetchGlobalParameters();
      }
    } catch (error) {
      console.error('Failed to remove environment:', error);
    }
  }, [fetchGlobalParameters]);

  // 打开编辑环境模态框
  const handleOpenEditEnvironment = useCallback((env: Environment) => {
    setEditingEnvironment(env);
    setEditEnvironmentName(env.name);
    setIsEditEnvModalVisible(true);
  }, []);

  // 确认编辑环境
  const handleConfirmEditEnvironment = useCallback(async () => {
    if (!editingEnvironment || !editEnvironmentName.trim()) return;
    try {
      const response = await globalParameterApi.updateEnvironment(Number(editingEnvironment.id), {
        name: editEnvironmentName.trim(),
        parameters: editingEnvironment.parameters,
        is_default: editingEnvironment.is_default || false,
      });
      if (response.code === 200 && response.data) {
        fetchGlobalParameters(editingEnvironment.id);
        setIsEditEnvModalVisible(false);
      }
    } catch (error) {
      console.error('Failed to update environment:', error);
    }
  }, [editingEnvironment, editEnvironmentName, fetchGlobalParameters]);

  // 加载全局参数
  useEffect(() => {
    if (canManageGlobalParams) {
      fetchGlobalParameters();
    }
  }, [canManageGlobalParams, fetchGlobalParameters]);

  return {
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
    fetchGlobalParameters,
    getCurrentEnvironment,
    handleSwitchEnvironment,
    saveEnvironmentToBackend,
    handleAddEnvironmentParameter,
    handleRemoveEnvironmentParameter,
    handleEnvironmentParameterChange,
    handleSaveEnvironmentParameter,
    handleAddEnvironment,
    handleConfirmAddEnvironment,
    handleRemoveEnvironment,
    handleOpenEditEnvironment,
    handleConfirmEditEnvironment,
  };
}

import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  AutoComplete,
  Button,
  Card,
  Checkbox,
  Collapse,
  Descriptions,
  Empty,
  Form,
  Input,
  List,
  message,
  Modal,
  Select,
  Space,
  Tabs,
  Table,
  Tag,
  Upload,
} from 'antd';
import {
  DeleteOutlined,
  DownloadOutlined,
  ExperimentOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  RobotOutlined,
  SaveOutlined,
  SyncOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import CodeMirror from '@uiw/react-codemirror';
import { json } from '@codemirror/lang-json';
import { apiTestApi, globalParameterApi } from '../services/api';
import { ApiEndpoint, ApiProject, ApiScenario, ApiScenarioResult, ApiScenarioStep, GlobalParameter } from '../types';

const { TextArea } = Input;
const REMOVED_FROM_SPEC_TAG = '__removed_from_spec__';
const MAX_SCENARIO_RESULT_RECORDS = 10;

const DEFAULT_ENDPOINT_GROUP = '未分组接口';
const projectUrlTextStyle: React.CSSProperties = {
  display: 'block',
  maxWidth: '100%',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
};
const fullHeightCardStyle: React.CSSProperties = {
  height: '100%',
  minHeight: 0,
  display: 'flex',
  flexDirection: 'column',
};
const scrollableCardBodyStyle: React.CSSProperties = {
  flex: 1,
  minHeight: 0,
  overflow: 'auto',
};
const fixedCardBodyStyle: React.CSSProperties = {
  ...scrollableCardBodyStyle,
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
};
const scenarioEditorFormStyle: React.CSSProperties = {
  flex: '1 1 0',
  minHeight: 0,
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
};
const endpointEditorFormStyle: React.CSSProperties = {
  ...scenarioEditorFormStyle,
};
const scenarioTabPaneStyle: React.CSSProperties = {
  height: '100%',
  minHeight: 0,
  overflow: 'auto',
  paddingRight: 4,
  paddingBottom: 24,
};
const endpointTabPaneStyle: React.CSSProperties = {
  ...scenarioTabPaneStyle,
};
const assetTabPaneStyle: React.CSSProperties = {
  height: '100%',
  minHeight: 0,
  display: 'flex',
  flexDirection: 'column',
  gap: 12,
  overflow: 'hidden',
};
const assetListScrollStyle: React.CSSProperties = {
  flex: '1 1 0',
  minHeight: 0,
  overflow: 'auto',
  paddingRight: 4,
  paddingBottom: 28,
};
const scenarioActionsStyle: React.CSSProperties = {
  flex: '0 0 auto',
  paddingTop: 12,
  paddingBottom: 2,
  borderTop: '1px solid #f0f0f0',
  background: '#fff',
};
const debugResultPanelStyle: React.CSSProperties = {
  border: '1px solid #e5e7eb',
  borderRadius: 8,
  background: '#fff',
  overflow: 'hidden',
};
const debugResultPanelHeaderStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  gap: 12,
  padding: '10px 12px',
  borderBottom: '1px solid #eef0f3',
  background: '#fafafa',
  fontWeight: 600,
};
const debugResultPanelBodyStyle: React.CSSProperties = {
  padding: 12,
};
const assertionTypeOptions = [
  { label: '状态码范围', value: 'status_code_range' },
  { label: '状态码等于', value: 'status_code' },
  { label: '响应时间小于', value: 'response_time_lt' },
  { label: '响应字段存在', value: 'jsonpath_exists' },
  { label: '响应字段等于', value: 'jsonpath_equals' },
];
const commonJsonPathOptions = [
  { label: '响应体', value: '$' },
  { label: '数据对象 data', value: '$.data' },
  { label: '数据列表 data.list', value: '$.data.list' },
  { label: '数据项 data.items', value: '$.data.items' },
  { label: '错误信息 message', value: '$.message' },
  { label: '业务编码 code', value: '$.code' },
];
const scenarioStepStatusLabels: Record<string, string> = {
  passed: '通过',
  failed: '失败',
  error: '错误',
};
const assertionTypeLabels = Object.fromEntries(assertionTypeOptions.map((item) => [item.value, item.label]));
type HeaderRow = ApiEndpoint['headers'][number];
type ParameterRow = ApiEndpoint['parameters'][number];
type BodyPreviewTarget = { type: 'endpoint' } | { type: 'scenario-step'; index: number };

const DEFAULT_ASSERTIONS: ApiEndpoint['assertions'] = [
  { type: 'jsonpath_equals', value: 200, jsonpath: '$.code' },
];

function cloneRows<T>(items: T[] | undefined): T[] {
  return JSON.parse(JSON.stringify(items || []));
}

function defaultAssertions(): ApiEndpoint['assertions'] {
  return cloneRows(DEFAULT_ASSERTIONS);
}

function endpointAssertions(endpoint: ApiEndpoint): ApiEndpoint['assertions'] {
  const assertions = normalizeRows(endpoint.assertions);
  return assertions.length > 0 ? cloneRows(assertions) : defaultAssertions();
}

function scenarioStepFromEndpoint(endpoint: ApiEndpoint): ApiScenarioStep {
  return {
    endpoint_id: endpoint.id,
    name: endpoint.name,
    method: endpoint.method,
    path: endpoint.path,
    url: endpoint.url || endpoint.path,
    body: endpoint.body || '',
    enabled: true,
    continue_on_failure: false,
    headers: cloneRows(endpoint.headers),
    parameters: cloneRows(endpoint.parameters),
    pre_actions: cloneRows(endpoint.pre_actions),
    post_actions: cloneRows(endpoint.post_actions),
    assertions: endpointAssertions(endpoint),
  };
}

function formatResultValue(value: unknown): string {
  if (value === undefined || value === null || value === '') return '空';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function formatFailedAssertion(assertion: any): string {
  const expected = formatResultValue(assertion?.expected);
  const actual = formatResultValue(assertion?.actual);
  switch (assertion?.type) {
    case 'status_code':
      return `状态码不等于预期值（期望：${expected}，实际：${actual}）`;
    case 'status_code_range':
      return `状态码不在预期范围内（期望：${expected}，实际：${actual}）`;
    case 'response_time_lt':
      return `响应时间超出限制（期望小于：${expected}ms，实际：${actual}ms）`;
    case 'jsonpath_exists':
      return `响应字段不存在（路径：${expected}，匹配数：${actual}）`;
    case 'jsonpath_equals':
      return `响应字段值不匹配（期望：${expected}，实际：${actual}）`;
    default:
      return `${assertionTypeLabels[assertion?.type] || '断言'}未通过（期望：${expected}，实际：${actual}）`;
  }
}

function formatScenarioFailureReason(row: any): string {
  if (row?.status === 'passed') return '-';
  if (row?.detail) return String(row.detail);

  const failedAssertion = row?.assertions?.find((item: any) => !item.passed);
  if (failedAssertion) return formatFailedAssertion(failedAssertion);

  const statusCode = row?.response?.status_code;
  if (typeof statusCode === 'number' && (statusCode < 200 || statusCode >= 400)) {
    return `HTTP 状态码异常（实际：${statusCode}）`;
  }
  return '执行失败';
}

function getScenarioResultSummary(result: any) {
  const steps = Array.isArray(result?.steps) ? result.steps : [];
  const passed = steps.filter((step: any) => step.status === 'passed').length;
  return { total: steps.length, passed };
}

function formatScenarioResultRecordLabel(record: ApiScenarioResult) {
  const { total, passed } = getScenarioResultSummary(record.result);
  return `${new Date(record.created_at).toLocaleString()} - ${passed}/${total}`;
}

function endpointLabel(endpoint: ApiEndpoint): string {
  return `${endpoint.name || '未命名接口'} · ${endpoint.method} ${endpoint.path || endpoint.url || ''}`;
}

function endpointSearchText(endpoint: ApiEndpoint): string {
  return [
    endpoint.name,
    endpoint.method,
    endpoint.path,
    endpoint.url,
    ...(endpoint.tags || []),
  ].filter(Boolean).join(' ').toLowerCase();
}

function collectJsonPathOptions(schema: Record<string, any> | undefined, prefix = '$', labelPrefix = '响应字段', depth = 0): Array<{ label: string; value: string }> {
  if (!schema || depth > 4) return [];
  const properties = schema.properties && typeof schema.properties === 'object' ? schema.properties : undefined;
  if (!properties) return [];
  const options: Array<{ label: string; value: string }> = [];
  Object.entries(properties).forEach(([key, value]) => {
    const path = `${prefix}.${key}`;
    const label = `${labelPrefix} ${path.replace('$.', '')}`;
    options.push({ label, value: path });
    if (value && typeof value === 'object') {
      options.push(...collectJsonPathOptions(value as Record<string, any>, path, labelPrefix, depth + 1));
      if ((value as Record<string, any>).type === 'array' && (value as Record<string, any>).items) {
        options.push(...collectJsonPathOptions((value as Record<string, any>).items, `${path}[0]`, labelPrefix, depth + 1));
      }
    }
  });
  return options;
}

function normalizeRows<T extends Record<string, any>>(items: T[] | undefined): T[] {
  return (items || []).filter((item) => item && typeof item === 'object');
}

function cleanKeyValueRows(items: Array<Partial<HeaderRow>> | undefined): HeaderRow[] {
  return (items || [])
    .map((item: any) => {
      const row: HeaderRow = { key: (item.key || '').trim(), value: item.value || '' };
      if (item.required !== undefined) row.required = Boolean(item.required);
      if (item.schema && typeof item.schema === 'object') row.schema = item.schema;
      return row;
    })
    .filter((item) => item.key);
}

function cleanParameterRows(items: ApiEndpoint['parameters'] | undefined): ParameterRow[] {
  return (items || [])
    .map((item) => {
      const row: ParameterRow = {
        key: (item.key || '').trim(),
        value: item.value || '',
        in: item.in || 'query',
        required: Boolean(item.required),
      };
      if (item.schema && typeof item.schema === 'object') row.schema = item.schema;
      return row;
    })
    .filter((item) => item.key);
}

function preservePairMetadata<T extends HeaderRow | ParameterRow>(
  rows: T[],
  source: T[] | undefined,
  includeLocation = false,
): T[] {
  return rows.map((row) => {
    const original = (source || []).find((item) => {
      if (!item || item.key !== row.key) return false;
      const itemLocation = 'in' in item ? item.in || '' : '';
      const rowLocation = 'in' in row ? row.in || '' : '';
      return !includeLocation || itemLocation === rowLocation;
    });
    if (!original) return row;
    return {
      ...row,
      required: row.required !== undefined ? row.required : original.required,
      schema: row.schema || original.schema,
    };
  });
}

function cleanPreActions(items: ApiEndpoint['pre_actions'] | undefined) {
  return (items || [])
    .map((item) => ({
      type: item.type || 'set_variable',
      key: (item.key || item.variable || '').trim(),
      value: item.value || '',
    }))
    .filter((item) => item.key);
}

function cleanPostActions(items: ApiEndpoint['post_actions'] | undefined) {
  return (items || [])
    .map((item) => ({
      type: item.type || 'extract_jsonpath',
      key: (item.key || item.variable || '').trim(),
      jsonpath: item.jsonpath || '',
    }))
    .filter((item) => item.key && item.jsonpath);
}

function cleanAssertions(items: ApiEndpoint['assertions'] | undefined) {
  return (items || [])
    .map((item) => ({
      type: item.type || 'status_code_range',
      value: item.value,
      min: item.min,
      max: item.max,
      jsonpath: item.jsonpath,
    }))
    .filter((item) => item.type);
}

function cleanScenarioVariables(items: Array<{ key?: string; value?: string }> | undefined) {
  return cleanKeyValueRows(items);
}

function normalizeEnvironmentId(value: unknown): number | null {
  if (value === undefined || value === null || value === '') return null;
  const id = Number(value);
  return Number.isFinite(id) ? id : null;
}

function hasStepField(step: ApiScenarioStep, field: keyof ApiScenarioStep): boolean {
  return Object.prototype.hasOwnProperty.call(step, field);
}

function cleanScenarioSteps(items: ApiScenarioStep[] | undefined) {
  return (items || [])
    .map((step) => {
      const cleaned: ApiScenarioStep = {
        endpoint_id: Number(step.endpoint_id),
        name: step.name || '',
        enabled: step.enabled !== false,
        continue_on_failure: Boolean(step.continue_on_failure),
      };
      const headers = cleanKeyValueRows(step.headers);
      const parameters = cleanParameterRows(step.parameters);
      const preActions = cleanPreActions(step.pre_actions);
      const postActions = cleanPostActions(step.post_actions);
      const assertions = cleanAssertions(step.assertions);
      if (step.method) cleaned.method = step.method;
      if (step.path) cleaned.path = step.path;
      if (step.url) cleaned.url = step.url;
      if (step.body) cleaned.body = step.body;
      if (headers.length > 0) cleaned.headers = headers;
      if (parameters.length > 0) cleaned.parameters = parameters;
      if (preActions.length > 0) cleaned.pre_actions = preActions;
      if (postActions.length > 0) cleaned.post_actions = postActions;
      if (assertions.length > 0) cleaned.assertions = assertions;
      return cleaned;
    })
    .filter((step) => step.endpoint_id);
}

function hydrateStepWithEndpoint(step: ApiScenarioStep, endpoints: ApiEndpoint[]) {
  const endpoint = endpoints.find((item) => item.id === step.endpoint_id);
  return {
    ...step,
    name: step.name || endpoint?.name || '',
    method: step.method || endpoint?.method,
    path: step.path || endpoint?.path,
    url: step.url || endpoint?.url || endpoint?.path,
    body: hasStepField(step, 'body') ? step.body : endpoint?.body || '',
    enabled: step.enabled !== false,
    continue_on_failure: Boolean(step.continue_on_failure),
    headers: hasStepField(step, 'headers') ? normalizeRows(step.headers) : cloneRows(endpoint?.headers),
    parameters: hasStepField(step, 'parameters') ? normalizeRows(step.parameters) : cloneRows(endpoint?.parameters),
    pre_actions: hasStepField(step, 'pre_actions') ? normalizeRows(step.pre_actions) : cloneRows(endpoint?.pre_actions),
    post_actions: hasStepField(step, 'post_actions') ? normalizeRows(step.post_actions) : cloneRows(endpoint?.post_actions),
    assertions: hasStepField(step, 'assertions') ? normalizeRows(step.assertions) : endpoint ? endpointAssertions(endpoint) : defaultAssertions(),
  };
}

function scenarioToFormValues(scenario: ApiScenario, endpoints: ApiEndpoint[] = []) {
  return {
    ...scenario,
    environment_id: scenario.environment_id ?? undefined,
    variables: normalizeRows(scenario.variables),
    steps: normalizeRows(scenario.steps).map((step) => hydrateStepWithEndpoint(step, endpoints)),
  };
}

function endpointToFormValues(endpoint: ApiEndpoint) {
  return {
    ...endpoint,
    headers: normalizeRows(endpoint.headers),
    parameters: normalizeRows(endpoint.parameters),
    environment_id: endpoint.environment_id ?? undefined,
    pre_actions: normalizeRows(endpoint.pre_actions),
    post_actions: normalizeRows(endpoint.post_actions),
    assertions: normalizeRows(endpoint.assertions),
  };
}

const ApiScenarioTestTool: React.FC = () => {
  const [projects, setProjects] = useState<ApiProject[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<number | null>(null);
  const [endpoints, setEndpoints] = useState<ApiEndpoint[]>([]);
  const [scenarios, setScenarios] = useState<ApiScenario[]>([]);
  const [environments, setEnvironments] = useState<GlobalParameter[]>([]);
  const [selectedEndpoint, setSelectedEndpoint] = useState<ApiEndpoint | null>(null);
  const [selectedScenario, setSelectedScenario] = useState<ApiScenario | null>(null);
  const [activeTab, setActiveTab] = useState('endpoints');
  const [loading, setLoading] = useState(false);
  const [syncingProjectId, setSyncingProjectId] = useState<number | null>(null);
  const [running, setRunning] = useState(false);
  const [endpointRunning, setEndpointRunning] = useState(false);
  const [bodyGenerating, setBodyGenerating] = useState(false);
  const [scenarioStepBodyGenerating, setScenarioStepBodyGenerating] = useState<number | null>(null);
  const [scenarioGenerating, setScenarioGenerating] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [scenarioResultHistory, setScenarioResultHistory] = useState<Record<number, ApiScenarioResult[]>>({});
  const [selectedScenarioResultRecordId, setSelectedScenarioResultRecordId] = useState<number | null>(null);
  const [endpointResult, setEndpointResult] = useState<any>(null);
  const [bodyPreview, setBodyPreview] = useState('');
  const [bodyPreviewMessage, setBodyPreviewMessage] = useState('');
  const [bodyPreviewVisible, setBodyPreviewVisible] = useState(false);
  const [bodyPreviewTarget, setBodyPreviewTarget] = useState<BodyPreviewTarget>({ type: 'endpoint' });
  const [importName, setImportName] = useState('');
  const [importUrl, setImportUrl] = useState('');
  const [importFile, setImportFile] = useState<File | null>(null);
  const [endpointSearch, setEndpointSearch] = useState('');

  const [projectForm] = Form.useForm();
  const [endpointForm] = Form.useForm();
  const [scenarioForm] = Form.useForm();
  const watchedScenarioSteps = Form.useWatch('steps', scenarioForm) || [];
  const jsonExtensions = useMemo(() => [json()], []);
  const selectedProject = projects.find((project) => project.id === selectedProjectId) || null;
  const selectedScenarioId = selectedScenario?.id ?? null;
  const selectedScenarioIdRef = useRef<number | null>(null);
  const environmentOptions = environments.map((environment) => ({
    label: `${environment.name}${environment.is_default ? '（默认）' : ''}`,
    value: environment.id,
  }));
  const endpointOptions = endpoints.map((endpoint) => ({ label: endpointLabel(endpoint), value: endpoint.id }));
  const selectedScenarioResultRecords = useMemo(() => {
    if (!selectedScenarioId) return [];
    return scenarioResultHistory[selectedScenarioId] || [];
  }, [scenarioResultHistory, selectedScenarioId]);

  const getStepJsonPathOptions = (stepIndex: number) => {
    const endpointId = scenarioForm.getFieldValue(['steps', stepIndex, 'endpoint_id']);
    const endpoint = endpoints.find((item) => item.id === endpointId);
    const schemaOptions = collectJsonPathOptions(endpoint?.response_schema);
    const optionMap = new Map<string, { label: string; value: string }>();
    [...commonJsonPathOptions, ...schemaOptions].forEach((option) => optionMap.set(option.value, option));
    return Array.from(optionMap.values());
  };

  const selectedEndpointJsonPathOptions = useMemo(() => {
    const schemaOptions = collectJsonPathOptions(selectedEndpoint?.response_schema);
    const optionMap = new Map<string, { label: string; value: string }>();
    [...commonJsonPathOptions, ...schemaOptions].forEach((option) => optionMap.set(option.value, option));
    return Array.from(optionMap.values());
  }, [selectedEndpoint?.response_schema]);

  const filteredEndpoints = useMemo(() => {
    const keyword = endpointSearch.trim().toLowerCase();
    if (!keyword) return endpoints;
    return endpoints.filter((endpoint) => {
      const name = (endpoint.name || '').toLowerCase();
      const path = (endpoint.path || endpoint.url || '').toLowerCase();
      return name.includes(keyword) || path.includes(keyword);
    });
  }, [endpoints, endpointSearch]);

  const endpointGroups = useMemo(() => {
    const groups: Array<{ name: string; endpoints: ApiEndpoint[] }> = [];
    const groupMap = new Map<string, ApiEndpoint[]>();

    filteredEndpoints.forEach((endpoint) => {
      const tags = Array.isArray(endpoint.tags) && endpoint.tags.length > 0 ? endpoint.tags : [DEFAULT_ENDPOINT_GROUP];
      Array.from(new Set(tags)).forEach((tag) => {
        const groupName = tag === REMOVED_FROM_SPEC_TAG
          ? '文档已移除'
          : (String(tag || DEFAULT_ENDPOINT_GROUP).trim() || DEFAULT_ENDPOINT_GROUP);
        if (!groupMap.has(groupName)) {
          groupMap.set(groupName, []);
          groups.push({ name: groupName, endpoints: groupMap.get(groupName)! });
        }
        groupMap.get(groupName)!.push(endpoint);
      });
    });

    return groups;
  }, [filteredEndpoints]);

  const loadProjects = async () => {
    const res = await apiTestApi.getProjects();
    if (res.code === 200 && res.data) {
      setProjects(res.data);
      if (!selectedProjectId && res.data.length > 0) {
        setSelectedProjectId(res.data[0].id);
      }
    }
  };

  const loadProjectData = async (projectId: number) => {
    const [endpointRes, scenarioRes] = await Promise.all([
      apiTestApi.getEndpoints(projectId),
      apiTestApi.getScenarios(projectId),
    ]);
    if (endpointRes.code === 200) setEndpoints(endpointRes.data || []);
    if (scenarioRes.code === 200) setScenarios(scenarioRes.data || []);
    setSelectedEndpoint(null);
    setSelectedScenario(null);
    selectedScenarioIdRef.current = null;
    setResult(null);
    setScenarioResultHistory({});
    setSelectedScenarioResultRecordId(null);
    setEndpointResult(null);
  };

  const loadScenarioResults = async (scenarioId: number, applySelection = false) => {
    const res = await apiTestApi.getScenarioResults(scenarioId);
    if (res.code !== 200) {
      message.error(res.message || '加载场景执行结果失败');
      return [];
    }

    const records = res.data || [];
    setScenarioResultHistory((history) => ({
      ...history,
      [scenarioId]: records,
    }));

    if (applySelection && selectedScenarioIdRef.current === scenarioId) {
      const latestRecord = records[0] || null;
      setSelectedScenarioResultRecordId(latestRecord?.id || null);
      setResult(latestRecord?.result || null);
    }

    return records;
  };

  const loadEnvironments = async () => {
    const res = await globalParameterApi.getEnvironments();
    if (res.code === 200 && res.data) {
      setEnvironments(res.data);
    }
  };

  const readModelConfig = () => {
    try {
      const raw = localStorage.getItem('appSettings');
      const settings = raw ? JSON.parse(raw) : {};
      return {
        model_type: settings.setting_type || 'api',
        api_key: (settings.api_key || '').trim(),
        api_base_url: (settings.api_base_url || '').trim(),
        api_proxy_url: (settings.api_proxy_url || '').trim(),
        ollama_url: (settings.ollama_url || '').trim(),
        ollama_model: (settings.ollama_model || '').trim(),
      };
    } catch {
      return { model_type: 'api' };
    }
  };

  useEffect(() => {
    loadProjects().catch(() => message.error('加载接口项目失败'));
    loadEnvironments().catch(() => message.error('加载环境失败'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (selectedProjectId) {
      loadProjectData(selectedProjectId).catch(() => message.error('加载接口数据失败'));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedProjectId]);

  useEffect(() => {
    if (!selectedEndpoint) return;
    const latestEndpoint = endpoints.find((endpoint) => endpoint.id === selectedEndpoint.id);
    if (latestEndpoint && latestEndpoint !== selectedEndpoint) {
      setSelectedEndpoint(latestEndpoint);
    }
  }, [endpoints, selectedEndpoint]);

  useEffect(() => {
    if (selectedProject) {
      projectForm.setFieldsValue({
        ...selectedProject,
        headers: normalizeRows(selectedProject.headers),
      });
    } else {
      projectForm.resetFields();
    }
  }, [selectedProject, projectForm]);

  useEffect(() => {
    if (selectedEndpoint) {
      endpointForm.resetFields();
      endpointForm.setFieldsValue(endpointToFormValues(selectedEndpoint));
    } else {
      endpointForm.resetFields();
    }
  }, [selectedEndpoint, endpointForm]);

  useEffect(() => {
    if (selectedScenario) {
      scenarioForm.setFieldsValue(scenarioToFormValues(selectedScenario, endpoints));
    } else {
      scenarioForm.resetFields();
    }
  }, [selectedScenario, scenarioForm, endpoints]);

  const selectScenario = (scenario: ApiScenario) => {
    const latestRecord = scenarioResultHistory[scenario.id]?.[0] || null;
    selectedScenarioIdRef.current = scenario.id;
    setSelectedEndpoint(null);
    setEndpointResult(null);
    setSelectedScenario(scenario);
    setSelectedScenarioResultRecordId(latestRecord?.id || null);
    setResult(latestRecord?.result || null);
    scenarioForm.setFieldsValue(scenarioToFormValues(scenario, endpoints));
    window.setTimeout(() => {
      scenarioForm.setFieldsValue(scenarioToFormValues(scenario, endpoints));
    }, 0);
    loadScenarioResults(scenario.id, true).catch(() => message.error('加载场景执行结果失败'));
  };

  const handleImport = async () => {
    if (!importFile && !importUrl.trim()) {
      message.warning('请上传 Swagger/OpenAPI 文件或填写 URL');
      return;
    }
    setLoading(true);
    try {
      const res = await apiTestApi.importOpenApi({
        name: importName,
        url: importUrl.trim(),
        file: importFile,
      });
      if (res.code === 200 && res.data) {
        message.success(`导入成功，解析 ${res.data.endpoints.length} 个接口`);
        setImportName('');
        setImportUrl('');
        setImportFile(null);
        await loadProjects();
        setSelectedProjectId(res.data.project.id);
      } else {
        message.error(res.message || '导入失败');
      }
    } catch (error: any) {
      message.error(error.response?.data?.message || error.message || '导入失败');
    } finally {
      setLoading(false);
    }
  };

  const handleSyncProject = async (project: ApiProject) => {
    setSyncingProjectId(project.id);
    try {
      const res = await apiTestApi.syncProject(project.id);
      if (res.code === 200 && res.data) {
        setProjects((items) => items.map((item) => item.id === project.id ? res.data!.project : item));
        if (selectedProjectId === project.id) {
          setEndpoints(res.data.endpoints || []);
          setSelectedEndpoint((current) => current ? (res.data!.endpoints.find((item) => item.id === current.id) || current) : current);
        }
        message.success(`同步完成：新增 ${res.data.created}，更新 ${res.data.updated}，标记移除 ${res.data.marked_removed}`);
      } else {
        message.error(res.message || '同步失败');
      }
    } catch (error: any) {
      message.error(error.response?.data?.message || error.message || '同步失败');
    } finally {
      setSyncingProjectId(null);
    }
  };

  const handleSaveProject = async () => {
    if (!selectedProject) return;
    const values = projectForm.getFieldsValue(true);
    const res = await apiTestApi.updateProject(selectedProject.id, {
      name: values.name || selectedProject.name,
      base_url: values.base_url || '',
      headers: cleanKeyValueRows(values.headers),
      source_url: values.source_url || null,
    });
    if (res.code === 200 && res.data) {
      setProjects(projects.map((project) => project.id === res.data!.id ? res.data! : project));
      projectForm.setFieldsValue({
        ...res.data,
        headers: normalizeRows(res.data.headers),
      });
      message.success('项目已保存');
    }
  };

  const handleSelectProject = (project: ApiProject) => {
    setSelectedProjectId(project.id);
    setSelectedEndpoint(null);
    setSelectedScenario(null);
    selectedScenarioIdRef.current = null;
    setResult(null);
    setSelectedScenarioResultRecordId(null);
    setEndpointResult(null);
  };

  const handleDeleteProject = (project: ApiProject) => {
    Modal.confirm({
      title: '删除接口项目',
      content: `确定删除「${project.name}」吗？项目下的接口和场景会一并删除。`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        const res = await apiTestApi.deleteProject(project.id);
        if (res.code !== 200) {
          message.error(res.message || '删除接口项目失败');
          return;
        }
        const nextProjects = projects.filter((item) => item.id !== project.id);
        setProjects(nextProjects);
        if (selectedProjectId === project.id) {
          const nextProject = nextProjects[0] || null;
          setSelectedProjectId(nextProject?.id || null);
          setEndpoints([]);
          setScenarios([]);
          setSelectedEndpoint(null);
          setSelectedScenario(null);
          selectedScenarioIdRef.current = null;
          setResult(null);
          setScenarioResultHistory({});
          setSelectedScenarioResultRecordId(null);
          setEndpointResult(null);
        }
        message.success('接口项目已删除');
      },
    });
  };

  const buildEndpointUpdatePayload = () => {
    if (!selectedEndpoint) throw new Error('未选择接口');
    const values = endpointForm.getFieldsValue(true);
    return {
      ...selectedEndpoint,
      method: values.method,
      name: values.name,
      path: values.path,
      url: values.url || null,
      environment_id: normalizeEnvironmentId(values.environment_id),
      body: values.body || '',
      headers: preservePairMetadata(cleanKeyValueRows(values.headers), selectedEndpoint.headers),
      parameters: preservePairMetadata(cleanParameterRows(values.parameters), selectedEndpoint.parameters, true),
      pre_actions: cleanPreActions(values.pre_actions),
      post_actions: cleanPostActions(values.post_actions),
      assertions: cleanAssertions(values.assertions),
    };
  };

  const saveEndpoint = async (quiet = false) => {
    if (!selectedEndpoint) return undefined;
    const payload = buildEndpointUpdatePayload();
    const res = await apiTestApi.updateEndpoint(selectedEndpoint.id, payload);
    if (res.code === 200 && res.data) {
      setEndpoints(endpoints.map((endpoint) => endpoint.id === res.data!.id ? res.data! : endpoint));
      setSelectedEndpoint(res.data);
      if (!quiet) message.success('接口已保存');
      return res.data;
    }
    message.error(res.message || '保存接口失败');
    return undefined;
  };

  const handleSaveEndpoint = async () => {
    try {
      await saveEndpoint(false);
    } catch {
      message.error('JSON 配置格式不正确');
    }
  };

  const buildEndpointPayloadFromForm = () => {
    const values = endpointForm.getFieldsValue();
    const projectValues = projectForm.getFieldsValue(true);
    return {
      ...buildEndpointUpdatePayload(),
      base_url: projectValues.base_url ?? selectedProject?.base_url,
      project_headers: cleanKeyValueRows(projectValues.headers ?? selectedProject?.headers),
      environment_id: normalizeEnvironmentId(values.environment_id),
    };
  };

  const handleRunEndpoint = async () => {
    if (!selectedEndpoint) return;
    setEndpointRunning(true);
    setEndpointResult(null);
    try {
      const payload = buildEndpointPayloadFromForm();
      const res = await apiTestApi.runEndpoint(selectedEndpoint.id, payload);
      if (res.code === 200) {
        setEndpointResult(res.data);
        message.success('接口调试完成');
      } else {
        message.error(res.message || '接口调试失败');
      }
    } catch (error: any) {
      if (error instanceof SyntaxError) {
        message.error('JSON 配置格式不正确');
      } else {
        message.error(error.response?.data?.message || error.message || '接口调试失败');
      }
    } finally {
      setEndpointRunning(false);
    }
  };

  const handleGenerateBody = async () => {
    if (!selectedEndpoint) return;
    if (!selectedEndpoint.request_schema || Object.keys(selectedEndpoint.request_schema).length === 0) {
      message.warning('当前接口没有可用的 request schema');
      return;
    }
    setBodyGenerating(true);
    try {
      const values = endpointForm.getFieldsValue(true);
      const res = await apiTestApi.generateEndpointBody(selectedEndpoint.id, {
        ...readModelConfig(),
        current_body: values.body || '',
        environment_id: normalizeEnvironmentId(values.environment_id),
        instruction: '',
      });
      if (res.code === 200 && res.data) {
        setBodyPreviewTarget({ type: 'endpoint' });
        setBodyPreview(res.data.body || '');
        setBodyPreviewMessage(res.data.message || res.message || '');
        setBodyPreviewVisible(true);
      } else {
        message.error(res.message || '生成请求体失败');
      }
    } catch (error: any) {
      message.error(error.response?.data?.message || error.message || '生成请求体失败');
    } finally {
      setBodyGenerating(false);
    }
  };

  const handleGenerateScenarioStepBody = async (stepIndex: number) => {
    const endpointId = scenarioForm.getFieldValue(['steps', stepIndex, 'endpoint_id']);
    const endpoint = endpoints.find((item) => item.id === endpointId);
    if (!endpoint) {
      message.warning('Please select an endpoint first');
      return;
    }
    if (!endpoint.request_schema || Object.keys(endpoint.request_schema).length === 0) {
      message.warning('Current endpoint has no request schema');
      return;
    }
    setScenarioStepBodyGenerating(stepIndex);
    try {
      const res = await apiTestApi.generateEndpointBody(endpoint.id, {
        ...readModelConfig(),
        current_body: scenarioForm.getFieldValue(['steps', stepIndex, 'body']) || '',
        environment_id: normalizeEnvironmentId(scenarioForm.getFieldValue('environment_id')),
        instruction: '',
      });
      if (res.code === 200 && res.data) {
        setBodyPreviewTarget({ type: 'scenario-step', index: stepIndex });
        setBodyPreview(res.data.body || '');
        setBodyPreviewMessage(res.data.message || res.message || '');
        setBodyPreviewVisible(true);
      } else {
        message.error(res.message || 'Failed to generate request body');
      }
    } catch (error: any) {
      message.error(error.response?.data?.message || error.message || 'Failed to generate request body');
    } finally {
      setScenarioStepBodyGenerating(null);
    }
  };

  const handleGenerateScenarioTests = async () => {
    if (!selectedEndpoint) return;
    setScenarioGenerating(true);
    try {
      const savedEndpoint = await saveEndpoint(true);
      if (!savedEndpoint) return;
      const res = await apiTestApi.generateEndpointScenarioTests(savedEndpoint.id);
      if (res.code === 200 && res.data) {
        setScenarios([res.data, ...scenarios]);
        selectScenario(res.data);
        setActiveTab('scenarios');
        message.success('接口单测场景已生成');
      } else {
        message.error(res.message || '生成接口单测失败');
      }
    } catch (error: any) {
      if (error instanceof SyntaxError) {
        message.error('JSON 配置格式不正确');
      } else {
        message.error(error.response?.data?.message || error.message || '生成接口单测失败');
      }
    } finally {
      setScenarioGenerating(false);
    }
  };

  const handleCreateScenario = async () => {
    if (!selectedProjectId) return;
    const res = await apiTestApi.createScenario(selectedProjectId, {
      name: '新接口场景',
      description: '',
      base_url: selectedProject?.base_url,
      variables: [],
      steps: [],
    });
    if (res.code === 200 && res.data) {
      setScenarios([res.data, ...scenarios]);
      selectScenario(res.data);
      setActiveTab('scenarios');
    }
  };

  const handleSaveScenario = async () => {
    if (!selectedScenario) return undefined;
    try {
      const values = scenarioForm.getFieldsValue(true);
      const payload = {
        ...selectedScenario,
        ...values,
        environment_id: normalizeEnvironmentId(values.environment_id),
        variables: cleanScenarioVariables(values.variables),
        steps: cleanScenarioSteps(values.steps),
      };
      const res = await apiTestApi.updateScenario(selectedScenario.id, payload);
      if (res.code === 200 && res.data) {
        setScenarios(scenarios.map((scenario) => scenario.id === res.data!.id ? res.data! : scenario));
        setSelectedScenario(res.data);
        message.success('场景已保存');
        return res.data;
      }
      message.error(res.message || '保存场景失败');
    } catch {
      message.error('场景变量或步骤 JSON 格式不正确');
    }
    return undefined;
  };

  const handleAddStep = (endpoint: ApiEndpoint) => {
    const current = normalizeRows<ApiScenarioStep>(scenarioForm.getFieldValue('steps') || []);
    const next = [...current, scenarioStepFromEndpoint(endpoint)];
    scenarioForm.setFieldValue('steps', next);
  };

  const handleRunScenario = async () => {
    if (!selectedScenario) return;
    const runningScenario = selectedScenario;
    const savedScenario = await handleSaveScenario();
    if (!savedScenario) return;
    setRunning(true);
    try {
      const res = await apiTestApi.runScenario(runningScenario.id);
      if (res.code === 200 && res.data) {
        const record = res.data;
        setScenarioResultHistory((history) => ({
          ...history,
          [runningScenario.id]: [record, ...(history[runningScenario.id] || [])].slice(0, MAX_SCENARIO_RESULT_RECORDS),
        }));
        setSelectedScenarioResultRecordId(record.id);
        setResult(record.result);
        message.success('场景执行完成');
      } else {
        message.error(res.message || '场景执行失败');
      }
    } finally {
      setRunning(false);
    }
  };

  const renderJsonBlock = (value: unknown) => (
    <pre style={{ maxHeight: 360, overflow: 'auto', background: '#f6f8fa', padding: 12, borderRadius: 6, fontSize: 12 }}>
      {JSON.stringify(value ?? {}, null, 2)}
    </pre>
  );

  const renderEndpointTabContent = (children: React.ReactNode) => (
    <div style={endpointTabPaneStyle}>{children}</div>
  );

  const renderCompactValue = (value: unknown) => {
    if (value === undefined || value === null || value === '') {
      return <span style={{ color: '#9ca3af' }}>暂无</span>;
    }
    if (typeof value === 'object') {
      return (
        <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 12 }}>
          {JSON.stringify(value, null, 2)}
        </pre>
      );
    }
    return <span style={{ wordBreak: 'break-word' }}>{String(value)}</span>;
  };

  const renderDebugPanel = (title: string, children: React.ReactNode, extra?: React.ReactNode) => (
    <div style={debugResultPanelStyle}>
      <div style={debugResultPanelHeaderStyle}>
        <span>{title}</span>
        {extra}
      </div>
      <div style={debugResultPanelBodyStyle}>{children}</div>
    </div>
  );

  const renderKeyValueResultTable = (value: unknown) => {
    const record = value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
    const rows = Object.entries(record).map(([key, item]) => ({ key, value: item }));
    if (rows.length === 0) {
      return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无数据" />;
    }
    return (
      <Table
        size="small"
        pagination={false}
        dataSource={rows}
        columns={[
          { title: '字段', dataIndex: 'key', width: 220 },
          { title: '值', dataIndex: 'value', render: renderCompactValue },
        ]}
      />
    );
  };

  const renderAssertionResultTable = (assertions: any[] | undefined) => {
    const rows = Array.isArray(assertions) ? assertions.map((item, index) => ({ key: index, ...item })) : [];
    if (rows.length === 0) {
      return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无断言结果" />;
    }
    return (
      <Table
        size="small"
        pagination={false}
        dataSource={rows}
        columns={[
          {
            title: '结果',
            dataIndex: 'passed',
            width: 90,
            render: (passed: boolean) => <Tag color={passed ? 'success' : 'error'}>{passed ? '通过' : '未通过'}</Tag>,
          },
          {
            title: '断言类型',
            dataIndex: 'type',
            width: 180,
            render: (type: string) => assertionTypeLabels[type] || type || '-',
          },
          { title: '期望', dataIndex: 'expected', render: renderCompactValue },
          { title: '实际', dataIndex: 'actual', render: renderCompactValue },
        ]}
      />
    );
  };

  const renderStepDebugResult = (step: any, options: { passed?: boolean; title?: string; raw?: unknown } = {}) => {
    const request = step.request || {};
    const response = step.response || {};
    const status = String(step.status || (options.passed ? 'passed' : 'failed'));
    const passed = options.passed ?? status === 'passed';
    const responseStatusCode = response.status_code;
    const method = request.method || selectedEndpoint?.method || '-';
    const statusColor = passed ? 'success' : status === 'error' ? 'error' : 'warning';

    return (
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Alert
          type={passed ? 'success' : 'error'}
          showIcon
          message={options.title || (passed ? '执行通过' : '执行未通过')}
          description={step.detail || `执行状态：${scenarioStepStatusLabels[status] || status}`}
        />

        {renderDebugPanel(
          '请求地址',
          <Descriptions size="small" column={1} bordered>
            <Descriptions.Item label="Method">
              <Tag color="blue">{method}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="URL">{renderCompactValue(request.url)}</Descriptions.Item>
            <Descriptions.Item label="响应状态">
              {responseStatusCode !== undefined ? <Tag color={responseStatusCode >= 200 && responseStatusCode < 400 ? 'success' : 'error'}>{responseStatusCode}</Tag> : '暂无响应'}
            </Descriptions.Item>
            <Descriptions.Item label="耗时">{response.elapsed_ms !== undefined ? `${response.elapsed_ms} ms` : '暂无'}</Descriptions.Item>
          </Descriptions>,
          <Tag color={statusColor}>{scenarioStepStatusLabels[status] || status}</Tag>,
        )}

        {renderDebugPanel('请求头', renderKeyValueResultTable(request.headers))}
        {renderDebugPanel('请求参数', renderKeyValueResultTable(request.params))}
        {renderDebugPanel('请求 Body', renderCompactValue(request.body))}
        {renderDebugPanel('前置变量', renderKeyValueResultTable(step.pre_updates))}
        {renderDebugPanel('后置提取', renderKeyValueResultTable(step.extracted))}
        {renderDebugPanel('断言结果', renderAssertionResultTable(step.assertions))}
        {renderDebugPanel('响应头', renderKeyValueResultTable(response.headers))}
        {renderDebugPanel('响应 Body', renderCompactValue(response.body))}

        {options.raw !== undefined && (
          <Collapse
            size="small"
            items={[
              {
                key: 'raw',
                label: '原始调试数据',
                children: renderJsonBlock(options.raw),
              },
            ]}
          />
        )}
      </Space>
    );
  };

  const renderEndpointDebugResult = (debugResult: any) => {
    const step = debugResult?.step || {};
    return renderStepDebugResult(step, {
      passed: Boolean(debugResult?.passed),
      title: debugResult?.passed ? '接口调试通过' : '接口调试未通过',
      raw: debugResult,
    });
  };

  const renderHeadersEditor = (valueFlex = 2) => (
    <Form.List name="headers">
      {(fields, { add, remove }) => (
        <Space direction="vertical" style={{ width: '100%' }}>
          {fields.map((field) => (
            <Space key={field.key} style={{ display: 'flex', width: '100%' }} align="baseline">
              <Form.Item name={[field.name, 'key']} style={{ flex: 1, marginBottom: 8 }}>
                <Input placeholder="Key" />
              </Form.Item>
              <Form.Item name={[field.name, 'value']} style={{ flex: valueFlex, marginBottom: 8 }}>
                <Input placeholder="Value" />
              </Form.Item>
              <Button danger type="text" icon={<DeleteOutlined />} onClick={() => remove(field.name)} />
            </Space>
          ))}
          <Button icon={<PlusOutlined />} onClick={() => add({ key: '', value: '' })}>添加 Header</Button>
        </Space>
      )}
    </Form.List>
  );

  const renderParamsEditor = () => (
    <Form.List name="parameters">
      {(fields, { add, remove }) => (
        <Space direction="vertical" style={{ width: '100%' }}>
          {fields.map((field) => (
            <Space key={field.key} style={{ display: 'flex', width: '100%' }} align="baseline">
              <Form.Item name={[field.name, 'key']} style={{ flex: 1, marginBottom: 8 }}>
                <Input placeholder="Key" />
              </Form.Item>
              <Form.Item name={[field.name, 'value']} style={{ flex: 1.4, marginBottom: 8 }}>
                <Input placeholder="Value" />
              </Form.Item>
              <Form.Item name={[field.name, 'in']} style={{ width: 110, marginBottom: 8 }}>
                <Select options={[
                  { label: 'query', value: 'query' },
                  { label: 'path', value: 'path' },
                ]} />
              </Form.Item>
              <Form.Item name={[field.name, 'required']} valuePropName="checked" style={{ width: 70, marginBottom: 8 }}>
                <Checkbox>必填</Checkbox>
              </Form.Item>
              <Button danger type="text" icon={<DeleteOutlined />} onClick={() => remove(field.name)} />
            </Space>
          ))}
          <Button icon={<PlusOutlined />} onClick={() => add({ key: '', value: '', in: 'query', required: false })}>添加 Param</Button>
        </Space>
      )}
    </Form.List>
  );

  const renderScenarioVariablesEditor = () => (
    <Form.List name="variables">
      {(fields, { add, remove }) => (
        <Space direction="vertical" style={{ width: '100%' }}>
          {fields.map((field) => (
            <Space key={field.key} style={{ display: 'flex', width: '100%' }} align="baseline">
              <Form.Item name={[field.name, 'key']} style={{ flex: 1, marginBottom: 8 }}>
                <Input placeholder="Key" />
              </Form.Item>
              <Form.Item name={[field.name, 'value']} style={{ flex: 2, marginBottom: 8 }}>
                <Input placeholder="Value" />
              </Form.Item>
              <Button danger type="text" icon={<DeleteOutlined />} onClick={() => remove(field.name)} />
            </Space>
          ))}
          <Button icon={<PlusOutlined />} onClick={() => add({ key: '', value: '' })}>Add variable</Button>
        </Space>
      )}
    </Form.List>
  );

  const renderNestedKeyValueList = (name: any, addLabel: string) => (
    <Form.List name={name}>
      {(fields, { add, remove }) => (
        <Space direction="vertical" style={{ width: '100%' }}>
          {fields.map((field) => (
            <Space key={field.key} style={{ display: 'flex', width: '100%' }} align="baseline">
              <Form.Item name={[field.name, 'key']} style={{ flex: 1, marginBottom: 8 }}>
                <Input placeholder="Key" />
              </Form.Item>
              <Form.Item name={[field.name, 'value']} style={{ flex: 2, marginBottom: 8 }}>
                <Input placeholder="Value" />
              </Form.Item>
              <Button danger type="text" icon={<DeleteOutlined />} onClick={() => remove(field.name)} />
            </Space>
          ))}
          <Button size="small" icon={<PlusOutlined />} onClick={() => add({ key: '', value: '' })}>{addLabel}</Button>
        </Space>
      )}
    </Form.List>
  );

  const renderNestedParamsList = (name: any) => (
    <Form.List name={name}>
      {(fields, { add, remove }) => (
        <Space direction="vertical" style={{ width: '100%' }}>
          {fields.map((field) => (
            <Space key={field.key} style={{ display: 'flex', width: '100%' }} align="baseline">
              <Form.Item name={[field.name, 'key']} style={{ flex: 1, marginBottom: 8 }}>
                <Input placeholder="Key" />
              </Form.Item>
              <Form.Item name={[field.name, 'value']} style={{ flex: 1.4, marginBottom: 8 }}>
                <Input placeholder="Value" />
              </Form.Item>
              <Form.Item name={[field.name, 'in']} style={{ width: 110, marginBottom: 8 }}>
                <Select options={[
                  { label: 'query', value: 'query' },
                  { label: 'path', value: 'path' },
                ]} />
              </Form.Item>
              <Form.Item name={[field.name, 'required']} valuePropName="checked" style={{ width: 70, marginBottom: 8 }}>
                <Checkbox>Required</Checkbox>
              </Form.Item>
              <Button danger type="text" icon={<DeleteOutlined />} onClick={() => remove(field.name)} />
            </Space>
          ))}
          <Button size="small" icon={<PlusOutlined />} onClick={() => add({ key: '', value: '', in: 'query', required: false })}>Add param</Button>
        </Space>
      )}
    </Form.List>
  );

  const renderPreActionsList = (name: any) => (
    <Form.List name={name}>
      {(fields, { add, remove }) => (
        <Space direction="vertical" style={{ width: '100%' }}>
          {fields.map((field) => (
            <Space key={field.key} style={{ display: 'flex', width: '100%' }} align="baseline">
              <Form.Item name={[field.name, 'key']} style={{ flex: 1, marginBottom: 8 }}>
                <Input placeholder="Variable" />
              </Form.Item>
              <Form.Item name={[field.name, 'value']} style={{ flex: 2, marginBottom: 8 }}>
                <Input placeholder="Value" />
              </Form.Item>
              <Button danger type="text" icon={<DeleteOutlined />} onClick={() => remove(field.name)} />
            </Space>
          ))}
          <Button size="small" icon={<PlusOutlined />} onClick={() => add({ type: 'set_variable', key: '', value: '' })}>Add pre action</Button>
        </Space>
      )}
    </Form.List>
  );

  const renderPostActionsList = (name: any, jsonPathOptions = commonJsonPathOptions) => (
    <Form.List name={name}>
      {(fields, { add, remove }) => (
        <Space direction="vertical" style={{ width: '100%' }}>
          {fields.map((field) => (
            <Space key={field.key} style={{ display: 'flex', width: '100%' }} align="baseline">
              <Form.Item name={[field.name, 'key']} style={{ flex: 1, marginBottom: 8 }}>
                <Input placeholder="Variable" />
              </Form.Item>
              <Form.Item name={[field.name, 'jsonpath']} style={{ flex: 2, marginBottom: 8 }}>
                <AutoComplete
                  options={jsonPathOptions}
                  filterOption={(input, option) => {
                    const label = String(option?.label || '').toLowerCase();
                    const value = String(option?.value || '').toLowerCase();
                    return label.includes(input.toLowerCase()) || value.includes(input.toLowerCase());
                  }}
                >
                  <Input placeholder="JSONPath, e.g. $.data.token" />
                </AutoComplete>
              </Form.Item>
              <Button danger type="text" icon={<DeleteOutlined />} onClick={() => remove(field.name)} />
            </Space>
          ))}
          <Button size="small" icon={<PlusOutlined />} onClick={() => add({ type: 'extract_jsonpath', key: '', jsonpath: '' })}>Add post action</Button>
        </Space>
      )}
    </Form.List>
  );

  const renderAssertionsList = (name: any, jsonPathOptions = commonJsonPathOptions) => (
    <Form.List name={name}>
      {(fields, { add, remove }) => (
        <Space direction="vertical" style={{ width: '100%' }}>
          {fields.map((field) => (
            <Space key={field.key} style={{ display: 'flex', width: '100%' }} align="baseline" wrap>
              <Form.Item name={[field.name, 'type']} style={{ width: 180, marginBottom: 8 }}>
                <Select options={assertionTypeOptions} />
              </Form.Item>
              <Form.Item name={[field.name, 'min']} style={{ width: 90, marginBottom: 8 }}>
                <Input placeholder="Min" />
              </Form.Item>
              <Form.Item name={[field.name, 'max']} style={{ width: 90, marginBottom: 8 }}>
                <Input placeholder="Max" />
              </Form.Item>
              <Form.Item name={[field.name, 'value']} style={{ width: 150, marginBottom: 8 }}>
                <Input placeholder="Value" />
              </Form.Item>
              <Form.Item name={[field.name, 'jsonpath']} style={{ flex: 1, minWidth: 180, marginBottom: 8 }}>
                <AutoComplete
                  options={jsonPathOptions}
                  filterOption={(input, option) => {
                    const label = String(option?.label || '').toLowerCase();
                    const value = String(option?.value || '').toLowerCase();
                    return label.includes(input.toLowerCase()) || value.includes(input.toLowerCase());
                  }}
                >
                  <Input placeholder="响应字段 / JSONPath" />
                </AutoComplete>
              </Form.Item>
              <Button danger type="text" icon={<DeleteOutlined />} onClick={() => remove(field.name)} />
            </Space>
          ))}
          <Button size="small" icon={<PlusOutlined />} onClick={() => add(defaultAssertions()[0])}>Add assertion</Button>
        </Space>
      )}
    </Form.List>
  );

  const renderScenarioStepsEditor = () => (
    <Form.List name="steps">
      {(fields, { add, remove, move }) => (
        <Space direction="vertical" style={{ width: '100%' }}>
          <Button
            icon={<PlusOutlined />}
            onClick={() => {
              const endpoint = endpoints[0];
              add(endpoint ? scenarioStepFromEndpoint(endpoint) : {
                enabled: true,
                continue_on_failure: false,
                headers: [],
                parameters: [],
                pre_actions: [],
                post_actions: [],
                assertions: defaultAssertions(),
              });
            }}
          >
            Add step
          </Button>
          {fields.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No steps" />
          ) : (
            <div>
              <Collapse
                size="small"
                defaultActiveKey={[]}
                items={fields.map((field, index) => ({
                  key: String(field.key),
                  label: (() => {
                    const step = watchedScenarioSteps[field.name] || {};
                    const endpoint = endpoints.find((item) => item.id === step.endpoint_id);
                    const name = String(step.name || endpoint?.name || '').trim();
                    return <Space><Tag>{index + 1}</Tag><span>{name || 'Step'}</span></Space>;
                  })(),
                  extra: (
                    <Space onClick={(event) => event.stopPropagation()}>
                      <Button size="small" disabled={index === 0} onClick={() => move(index, index - 1)}>Up</Button>
                      <Button size="small" disabled={index === fields.length - 1} onClick={() => move(index, index + 1)}>Down</Button>
                      <Button danger size="small" icon={<DeleteOutlined />} onClick={() => remove(field.name)} />
                    </Space>
                  ),
                  children: (
                    <>
              <Space style={{ width: '100%' }} align="start" wrap>
                <Form.Item name={[field.name, 'endpoint_id']} label="接口" style={{ minWidth: 320, flex: 1 }}>
                  <Select
                    showSearch
                    placeholder="按接口名称或路径搜索"
                    filterOption={(input, option) => {
                      const endpoint = endpoints.find((item) => item.id === option?.value);
                      return endpoint ? endpointSearchText(endpoint).includes(input.toLowerCase()) : false;
                    }}
                    options={endpointOptions}
                    onChange={(id) => {
                      const endpoint = endpoints.find((item) => item.id === id);
                      if (endpoint) {
                        const currentStep = scenarioForm.getFieldValue(['steps', field.name]) || {};
                        scenarioForm.setFieldValue(['steps', field.name], {
                          ...scenarioStepFromEndpoint(endpoint),
                          enabled: currentStep.enabled !== false,
                          continue_on_failure: Boolean(currentStep.continue_on_failure),
                        });
                      }
                    }}
                  />
                </Form.Item>
                <Form.Item name={[field.name, 'name']} label="Name" style={{ minWidth: 220, flex: 1 }}>
                  <Input />
                </Form.Item>
                <Form.Item name={[field.name, 'enabled']} valuePropName="checked" label=" ">
                  <Checkbox>Enabled</Checkbox>
                </Form.Item>
                <Form.Item name={[field.name, 'continue_on_failure']} valuePropName="checked" label=" ">
                  <Checkbox>Continue on failure</Checkbox>
                </Form.Item>
              </Space>
              <Tabs
                size="small"
                items={[
                  {
                    key: 'request',
                    label: 'Request',
                    children: (
                      <Space direction="vertical" style={{ width: '100%' }}>
                        <Space style={{ width: '100%' }} align="start">
                          <Form.Item name={[field.name, 'method']} label="Method" style={{ width: 130 }}>
                            <Select allowClear options={['GET', 'POST', 'PUT', 'DELETE', 'PATCH'].map((method) => ({ label: method, value: method }))} />
                          </Form.Item>
                          <Form.Item name={[field.name, 'url']} label="URL / Path" style={{ flex: 1 }}>
                            <Input />
                          </Form.Item>
                        </Space>
                        <Space>
                          <Button
                            size="small"
                            icon={<RobotOutlined />}
                            loading={scenarioStepBodyGenerating === field.name}
                            disabled={!endpoints.find((item) => item.id === scenarioForm.getFieldValue(['steps', field.name, 'endpoint_id']))?.request_schema}
                            onClick={() => handleGenerateScenarioStepBody(field.name)}
                          >
                            AI Body
                          </Button>
                        </Space>
                        <Form.Item name={[field.name, 'body']} label="Body">
                          <TextArea rows={5} />
                        </Form.Item>
                      </Space>
                    ),
                  },
                  { key: 'headers', label: 'Headers', children: renderNestedKeyValueList([field.name, 'headers'], 'Add header') },
                  { key: 'params', label: 'Params', children: renderNestedParamsList([field.name, 'parameters']) },
                  { key: 'pre', label: 'Pre', children: renderPreActionsList([field.name, 'pre_actions']) },
                  { key: 'post', label: 'Post', children: renderPostActionsList([field.name, 'post_actions'], getStepJsonPathOptions(field.name)) },
                  { key: 'assertions', label: 'Assertions', children: renderAssertionsList([field.name, 'assertions'], getStepJsonPathOptions(field.name)) },
                ]}
              />
                    </>
                  ),
                }))}
              />
            </div>
          )}
        </Space>
      )}
    </Form.List>
  );

  const handleDownloadCollection = () => {
    if (!selectedProject) return;
    const collection = {
      info: {
        name: selectedProject.name,
        schema: 'https://schema.getpostman.com/json/collection/v2.1.0/collection.json',
      },
      item: endpoints.map((endpoint) => ({
        name: endpoint.name,
        request: {
          method: endpoint.method,
          header: endpoint.headers,
          url: {
            raw: `{{baseUrl}}${endpoint.path}`,
            host: ['{{baseUrl}}'],
            path: endpoint.path.split('/').filter(Boolean),
            query: endpoint.parameters.filter((param) => param.in !== 'path').map((param) => ({ key: param.key, value: param.value })),
          },
          body: endpoint.body ? { mode: 'raw', raw: endpoint.body } : undefined,
        },
        event: [{
          listen: 'test',
          script: {
            type: 'text/javascript',
            exec: ['pm.test("status code is 2xx", function () { pm.expect(pm.response.code).to.be.within(200, 299); });'],
          },
        }],
      })),
      variable: [{ key: 'baseUrl', value: selectedProject.base_url }],
    };
    const blob = new Blob([JSON.stringify(collection, null, 2)], { type: 'application/json' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `${selectedProject.name || 'api-collection'}.postman_collection.json`;
    link.click();
    URL.revokeObjectURL(link.href);
  };

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '300px 380px 1fr', gap: 12, height: 'calc(100vh - 76px)', minHeight: 0, overflow: 'hidden', padding: 12, background: '#f5f7fb' }}>
      <Card
        title="接口项目"
        size="small"
        style={fullHeightCardStyle}
        styles={{ body: { ...scrollableCardBodyStyle, display: 'flex', flexDirection: 'column', gap: 12, overflow: 'hidden' } }}
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <Input placeholder="项目名称" value={importName} onChange={(event) => setImportName(event.target.value)} />
          <Input placeholder="OpenAPI URL" value={importUrl} onChange={(event) => setImportUrl(event.target.value)} />
          <Upload
            maxCount={1}
            beforeUpload={(file) => {
              setImportFile(file);
              return false;
            }}
            onRemove={() => setImportFile(null)}
          >
            <Button icon={<UploadOutlined />} block>选择 Swagger/OpenAPI 文件</Button>
          </Upload>
          <Button type="primary" loading={loading} onClick={handleImport} block>导入接口文档</Button>
        </Space>

        <List
          style={{ marginTop: 12, flex: 1, minHeight: 0, overflow: 'auto' }}
          dataSource={projects}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无接口项目" /> }}
          renderItem={(project) => (
            <List.Item
              actions={[
                project.source_type === 'url' && project.source_url ? (
                  <Button
                    key="sync"
                    type="text"
                    size="small"
                    icon={<SyncOutlined />}
                    loading={syncingProjectId === project.id}
                    onClick={(event) => {
                      event.stopPropagation();
                      handleSyncProject(project);
                    }}
                  />
                ) : null,
                <Button
                  key="delete"
                  type="text"
                  danger
                  size="small"
                  icon={<DeleteOutlined />}
                  onClick={(event) => {
                    event.stopPropagation();
                    handleDeleteProject(project);
                  }}
                />,
              ].filter(Boolean)}
              style={{
                cursor: 'pointer',
                padding: '8px 10px',
                background: project.id === selectedProjectId ? '#e6f4ff' : undefined,
                borderRadius: 6,
              }}
              onClick={() => handleSelectProject(project)}
            >
              <List.Item.Meta
                style={{ minWidth: 0 }}
                title={<Space><span>{project.name}</span>{project.source_type === 'url' && <Tag color="cyan">URL</Tag>}</Space>}
                description={(
                  <Space direction="vertical" size={2} style={{ width: '100%', minWidth: 0 }}>
                    <span title={project.base_url || '未设置 Base URL'} style={projectUrlTextStyle}>
                      {project.base_url || '未设置 Base URL'}
                    </span>
                    {project.source_url && (
                      <span title={project.source_url} style={{ ...projectUrlTextStyle, fontSize: 12, color: '#8c8c8c' }}>
                        {project.source_url}
                      </span>
                    )}
                  </Space>
                )}
              />
            </List.Item>
          )}
        />
      </Card>

      <Card
        size="small"
        title={selectedProject ? selectedProject.name : '接口资产'}
        extra={selectedProject && <Button size="small" icon={<DownloadOutlined />} onClick={handleDownloadCollection}>导出</Button>}
        style={fullHeightCardStyle}
        styles={{ body: fixedCardBodyStyle }}
      >
        {selectedProject ? (
          <div style={{ width: '100%', height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column', gap: 12, overflow: 'hidden' }}>
            <Tabs
              className="api-asset-tabs"
              style={{ flex: '1 1 0', minHeight: 0, width: '100%' }}
              activeKey={activeTab}
              onChange={setActiveTab}
              items={[
                {
                  key: 'endpoints',
                  label: `接口 ${endpoints.length}`,
                  children: (
                    <div style={assetTabPaneStyle}>
                      <Input.Search
                        allowClear
                        placeholder="搜索接口名称或路径"
                        value={endpointSearch}
                        onChange={(event) => setEndpointSearch(event.target.value)}
                        onSearch={setEndpointSearch}
                      />
                      {endpoints.length === 0 ? (
                        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无接口" />
                      ) : filteredEndpoints.length === 0 ? (
                        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未找到匹配接口" />
                      ) : (
                        <div style={assetListScrollStyle}>
                          <Collapse
                            key={`${endpointSearch.trim()}|${endpointGroups.map((group) => group.name).join('|')}`}
                            size="small"
                            defaultActiveKey={endpointSearch.trim() ? endpointGroups.map((group) => group.name) : []}
                            items={endpointGroups.map((group) => ({
                              key: group.name,
                              label: (
                                <Space>
                                  <span>{group.name}</span>
                                  <Tag>{group.endpoints.length}</Tag>
                                </Space>
                              ),
                              children: (
                                <List
                                  dataSource={group.endpoints}
                                  renderItem={(endpoint) => {
                                    const removed = endpoint.tags?.includes(REMOVED_FROM_SPEC_TAG);
                                    return (
                                      <List.Item
                                        style={{
                                          cursor: 'pointer',
                                          background: endpoint.id === selectedEndpoint?.id ? '#f0f5ff' : undefined,
                                          padding: '8px',
                                          borderRadius: 6,
                                        }}
                                        onClick={() => {
                                          setSelectedEndpoint(endpoint);
                                          setSelectedScenario(null);
                                          selectedScenarioIdRef.current = null;
                                          setResult(null);
                                          setSelectedScenarioResultRecordId(null);
                                          setEndpointResult(null);
                                        }}
                                      >
                                        <List.Item.Meta
                                          title={<Space><Tag color="blue">{endpoint.method}</Tag><span>{endpoint.name}</span>{removed && <Tag color="red">已移除</Tag>}</Space>}
                                          description={endpoint.path}
                                        />
                                      </List.Item>
                                    );
                                  }}
                                />
                              ),
                            }))}
                          />
                        </div>
                      )}
                    </div>
                  ),
                },
                {
                  key: 'scenarios',
                  label: `场景 ${scenarios.length}`,
                  children: (
                    <div style={assetTabPaneStyle}>
                      <Button icon={<PlusOutlined />} onClick={handleCreateScenario} block>新建场景</Button>
                      <List
                        style={assetListScrollStyle}
                        dataSource={scenarios}
                        renderItem={(scenario) => (
                          <List.Item
                            style={{ cursor: 'pointer', background: scenario.id === selectedScenario?.id ? '#f0f5ff' : undefined, padding: '8px', borderRadius: 6 }}
                            onClick={() => selectScenario(scenario)}
                          >
                            <List.Item.Meta title={scenario.name} description={`${scenario.steps?.length || 0} 个步骤`} />
                          </List.Item>
                        )}
                      />
                    </div>
                  ),
                },
              ]}
            />
          </div>
        ) : (
          <Empty description="请先导入或选择接口项目" />
        )}
      </Card>

      <Card
        size="small"
        title={selectedEndpoint ? '接口编辑' : selectedScenario ? '场景编排' : selectedProject ? '项目配置' : '详情'}
        style={fullHeightCardStyle}
        styles={{ body: selectedEndpoint || selectedScenario ? fixedCardBodyStyle : scrollableCardBodyStyle }}
      >
        {selectedEndpoint ? (
          <Form
            key={selectedEndpoint.id}
            form={endpointForm}
            layout="vertical"
            className="api-endpoint-editor-form"
            style={endpointEditorFormStyle}
            initialValues={endpointToFormValues(selectedEndpoint)}
          >
            <Space style={{ width: '100%' }} align="start">
              <Form.Item name="method" label="Method" style={{ width: 120 }}>
                <Select options={['GET', 'POST', 'PUT', 'DELETE', 'PATCH'].map((method) => ({ label: method, value: method }))} />
              </Form.Item>
              <Form.Item name="name" label="接口名称" style={{ flex: 1 }}>
                <Input />
              </Form.Item>
              <Form.Item name="environment_id" label="环境" style={{ width: 180 }}>
                <Select allowClear placeholder="可选" options={environmentOptions} />
              </Form.Item>
            </Space>
            <Form.Item name="path" label="Path">
              <Input />
            </Form.Item>
            <Form.Item name="url" label="完整 URL 覆盖">
              <Input placeholder="可选；填写后执行时不使用 BaseUrl + Path" />
            </Form.Item>
            <Tabs
              className="api-endpoint-editor-tabs"
              items={[
                {
                  key: 'body',
                  label: 'Body',
                  children: renderEndpointTabContent(
                    <Space direction="vertical" style={{ width: '100%' }}>
                      <Space>
                        <Button
                          icon={<RobotOutlined />}
                          loading={bodyGenerating}
                          disabled={!selectedEndpoint.request_schema || Object.keys(selectedEndpoint.request_schema).length === 0}
                          onClick={handleGenerateBody}
                        >
                          一键 AI
                        </Button>
                        <span style={{ color: '#999', fontSize: 12 }}>命中全局变量 key 时会写入 {'{{key}}'} 占位。</span>
                      </Space>
                      <Form.Item
                        name="body"
                        style={{ marginBottom: 0 }}
                        getValueProps={(value) => ({ value: value || '' })}
                        getValueFromEvent={(value: string) => value}
                      >
                        <CodeMirror
                          height="220px"
                          extensions={jsonExtensions}
                        />
                      </Form.Item>
                    </Space>,
                  ),
                },
                { key: 'headers', label: 'Headers', children: renderEndpointTabContent(renderHeadersEditor()) },
                { key: 'params', label: 'Params', children: renderEndpointTabContent(renderParamsEditor()) },
                {
                  key: 'schema',
                  label: 'Schema',
                  children: renderEndpointTabContent(
                    <Space direction="vertical" style={{ width: '100%' }}>
                      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                        <strong>Request Schema</strong>
                        <Button
                          size="small"
                          onClick={() => {
                            navigator.clipboard.writeText(JSON.stringify(selectedEndpoint.request_schema || {}, null, 2));
                            message.success('Request Schema 已复制');
                          }}
                        >
                          复制
                        </Button>
                      </Space>
                      {renderJsonBlock(selectedEndpoint.request_schema || {})}
                      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                        <strong>Response Schema</strong>
                        <Button
                          size="small"
                          onClick={() => {
                            navigator.clipboard.writeText(JSON.stringify(selectedEndpoint.response_schema || {}, null, 2));
                            message.success('Response Schema 已复制');
                          }}
                        >
                          复制
                        </Button>
                      </Space>
                      {renderJsonBlock(selectedEndpoint.response_schema || {})}
                    </Space>,
                  ),
                },
                { key: 'pre', label: '前置', children: renderEndpointTabContent(renderPreActionsList('pre_actions')) },
                { key: 'post', label: '后置', children: renderEndpointTabContent(renderPostActionsList('post_actions', selectedEndpointJsonPathOptions)) },
                { key: 'assertions', label: '断言', children: renderEndpointTabContent(renderAssertionsList('assertions', selectedEndpointJsonPathOptions)) },
                {
                  key: 'debug',
                  label: '调试结果',
                  children: renderEndpointTabContent(endpointResult ? renderEndpointDebugResult(endpointResult) : <Alert type="info" message="点击“调试接口”后在这里查看请求、响应、断言和提取变量。" />),
                },
              ]}
            />
            <Space wrap style={scenarioActionsStyle}>
              <Button type="primary" icon={<SaveOutlined />} onClick={handleSaveEndpoint}>保存接口</Button>
              <Button icon={<PlayCircleOutlined />} loading={endpointRunning} onClick={handleRunEndpoint}>调试接口</Button>
              <Button icon={<ExperimentOutlined />} loading={scenarioGenerating} onClick={handleGenerateScenarioTests}>生成接口单测</Button>
              {selectedScenario && <Button onClick={() => handleAddStep(selectedEndpoint)}>加入当前场景</Button>}
            </Space>
          </Form>
        ) : selectedScenario ? (
          <Form
            key={selectedScenario.id}
            form={scenarioForm}
            layout="vertical"
            initialValues={scenarioToFormValues(selectedScenario, endpoints)}
            className="api-scenario-editor-form"
            style={scenarioEditorFormStyle}
          >
            <Space style={{ width: '100%' }} align="start">
              <Form.Item name="name" label="场景名称" style={{ flex: 1 }}>
                <Input />
              </Form.Item>
              <Form.Item name="environment_id" label="环境" style={{ width: 180 }}>
                <Select allowClear placeholder="可选" options={environmentOptions} />
              </Form.Item>
            </Space>
            <Form.Item name="base_url" label="场景 BaseUrl">
              <Input placeholder="为空时使用项目 BaseUrl" />
            </Form.Item>
            <Form.Item label="添加接口步骤">
              <Select
                showSearch
                placeholder="选择接口加入场景"
                filterOption={(input, option) => {
                  const endpoint = endpoints.find((item) => item.id === option?.value);
                  return endpoint ? endpointSearchText(endpoint).includes(input.toLowerCase()) : false;
                }}
                onSelect={(id) => {
                  const endpoint = endpoints.find((item) => item.id === id);
                  if (endpoint) handleAddStep(endpoint);
                }}
                options={endpointOptions}
              />
            </Form.Item>
            <Tabs
              className="api-scenario-editor-tabs"
              items={[
                { key: 'steps', label: 'Steps', children: <div style={scenarioTabPaneStyle}>{renderScenarioStepsEditor()}</div> },
                { key: 'vars', label: 'Variables', children: <div style={scenarioTabPaneStyle}>{renderScenarioVariablesEditor()}</div> },
                {
                  key: 'result',
                  label: '执行结果',
                  children: (
                    <div style={scenarioTabPaneStyle}>
                      {result ? (
                    <Space direction="vertical" style={{ width: '100%' }}>
                      {selectedScenarioResultRecords.length > 0 && (
                        <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
                          <Select
                            size="small"
                            style={{ minWidth: 280 }}
                            value={selectedScenarioResultRecordId || selectedScenarioResultRecords[0]?.id}
                            options={selectedScenarioResultRecords.map((record) => ({
                              label: formatScenarioResultRecordLabel(record),
                              value: record.id,
                            }))}
                            onChange={(recordId) => {
                              const record = selectedScenarioResultRecords.find((item) => item.id === recordId);
                              setSelectedScenarioResultRecordId(recordId);
                              setResult(record?.result || null);
                            }}
                          />
                          <Tag>{selectedScenarioResultRecords.length}/{MAX_SCENARIO_RESULT_RECORDS}</Tag>
                        </Space>
                      )}
                    <Table<any>
                      size="small"
                      rowKey="index"
                      pagination={false}
                      dataSource={result.steps || []}
                      columns={[
                        { title: '#', dataIndex: 'index', width: 60 },
                        { title: '步骤', dataIndex: 'name' },
                        { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color={value === 'passed' ? 'green' : 'red'}>{scenarioStepStatusLabels[value] || value || '-'}</Tag> },
                        { title: '请求 URL', render: (_: unknown, row: any) => row.request?.url || '-' },
                        { title: '状态码', render: (_: unknown, row: any) => row.response?.status_code || '-' },
                        { title: '耗时', render: (_: unknown, row: any) => row.response?.elapsed_ms ? `${row.response.elapsed_ms}ms` : '-' },
                        { title: '失败原因', render: (_: unknown, row: any) => formatScenarioFailureReason(row) },
                      ]}
                      expandable={{
                        expandedRowRender: (row) => (
                          <div style={{ padding: 8, background: '#fbfcfe' }}>
                            {renderStepDebugResult(row, {
                              passed: row.status === 'passed',
                              title: row.status === 'passed' ? '步骤执行通过' : '步骤执行未通过',
                              raw: row,
                            })}
                          </div>
                        ),
                      }}
                    />
                    </Space>
                      ) : <Alert type="info" message="运行场景后在这里查看每一步请求、响应、断言和提取变量。" />}
                    </div>
                  ),
                },
              ]}
            />
            <Space wrap style={scenarioActionsStyle}>
              <Button type="primary" icon={<SaveOutlined />} onClick={handleSaveScenario}>保存场景</Button>
              <Button icon={<PlayCircleOutlined />} loading={running} onClick={handleRunScenario}>串行执行</Button>
              <Button
                danger
                icon={<DeleteOutlined />}
                onClick={() => Modal.confirm({
                  title: '删除场景',
                  content: `确定删除 ${selectedScenario.name}？`,
                  onOk: async () => {
                    const deletedScenarioId = selectedScenario.id;
                    await apiTestApi.deleteScenario(deletedScenarioId);
                    setScenarios(scenarios.filter((scenario) => scenario.id !== deletedScenarioId));
                    setSelectedScenario(null);
                    selectedScenarioIdRef.current = null;
                    setResult(null);
                    setSelectedScenarioResultRecordId(null);
                    setScenarioResultHistory((history) => {
                      const next = { ...history };
                      delete next[deletedScenarioId];
                      return next;
                    });
                  },
                })}
              >
                删除
              </Button>
            </Space>
          </Form>
        ) : selectedProject ? (
          <Form form={projectForm} layout="vertical">
            <Form.Item name="name" label="项目名称">
              <Input />
            </Form.Item>
            <Form.Item name="base_url" label="BaseUrl">
              <Input placeholder="https://api.example.com" />
            </Form.Item>
            <Form.Item name="source_url" label="OpenAPI URL">
              <Input disabled={selectedProject.source_type !== 'url'} />
            </Form.Item>
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontWeight: 500, marginBottom: 8 }}>项目 Headers</div>
              {renderHeadersEditor(4)}
            </div>
            <Button type="primary" icon={<SaveOutlined />} onClick={handleSaveProject}>保存项目配置</Button>
          </Form>
        ) : (
          <Empty description="选择接口项目、接口或场景开始编辑" />
        )}
      </Card>

      <Modal
        title="AI 生成请求体预览"
        open={bodyPreviewVisible}
        width={760}
        okText="应用到 Body"
        cancelText="取消"
        onOk={() => {
          if (bodyPreviewTarget.type === 'scenario-step') {
            scenarioForm.setFieldValue(['steps', bodyPreviewTarget.index, 'body'], bodyPreview);
          } else {
            endpointForm.setFieldValue('body', bodyPreview);
          }
          setBodyPreviewVisible(false);
          message.success('请求体已写入 Body');
        }}
        onCancel={() => setBodyPreviewVisible(false)}
      >
        {bodyPreviewMessage && <Alert type="info" showIcon message={bodyPreviewMessage} style={{ marginBottom: 12 }} />}
        <pre style={{ maxHeight: 520, overflow: 'auto', background: '#f6f8fa', padding: 12, borderRadius: 6, fontSize: 12 }}>
          {bodyPreview}
        </pre>
      </Modal>
    </div>
  );
};

export default ApiScenarioTestTool;

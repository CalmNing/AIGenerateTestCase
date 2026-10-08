import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Checkbox,
  Empty,
  Input,
  List,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Space,
  Switch,
  Table,
  TableProps,
  Tabs,
  Tag,
  Tooltip,
  TreeSelect,
  Drawer,
  message,
} from 'antd';
import {
  AppstoreOutlined,
  CodeOutlined,
  DeleteOutlined,
  DownloadOutlined,
  DownOutlined,
  EditOutlined,
  ExperimentOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  SaveOutlined,
  UpOutlined,
} from '@ant-design/icons';
import { PageElement, PageLocator, PageObject, UIAutomationRun, UIAutomationTaskStatus } from '../../types';
import { LubanMenuNode, LubanModule, globalParameterApi, uiAutomationApi } from '../../services/api';
import {
  addLocatorByKey,
  addChildByKey,
  flattenElementTree,
  nextElementKey,
  pathByKey,
  removeByKey,
  removeLocatorByKey,
  updateByKey,
  updateLocatorByKey,
} from '../../utils/elementTree';

const STRATEGY_OPTIONS = [
  'testid', 'role', 'label', 'aria-label', 'placeholder', 'name', 'id', 'text', 'css', 'xpath',
].map((value) => ({ value, label: value }));

// 与后端 element_type 归一化结果对齐(page_extractor.element_type / LLM prompt)
const ELEMENT_TYPE_OPTIONS = [
  { value: 'input', label: 'input 输入框' },
  { value: 'button', label: 'button 按钮' },
  { value: 'link', label: 'link 链接' },
  { value: 'select', label: 'select 下拉' },
  { value: 'textarea', label: 'textarea 文本域' },
  { value: 'checkbox', label: 'checkbox 复选' },
  { value: 'radio', label: 'radio 单选' },
  { value: 'upload', label: 'upload 上传' },
  { value: 'element', label: 'element 其他' },
];

const emptyLocator = (): PageLocator => ({ strategy: 'css', value: '', stability: 50 });

const ACTION_OPTIONS = [
  'fill', 'click', 'select_option', 'upload_file', 'check', 'uncheck', 'press',
  'assert_text', 'assert_visible', 'wait_for',
].map((value) => ({ value, label: value }));

// 动作 → 「值」列的填写提示(upload_file 的值是后端可访问的文件绝对路径)
const VALUE_PLACEHOLDER: Record<string, string> = {
  upload_file: '待上传文件的绝对路径(后端所在机器),多个用逗号分隔',
};

const RUN_STATUS_COLOR: Record<UIAutomationRun['status'], string> = {
  PASSED: 'success',
  FAILED: 'error',
  ERROR: 'warning',
  STOPPED: 'default',
  RUNNING: 'processing',
};

// ---- 元素分组(按探针触发词)----
const BASE_GROUP = '页面主体';
const groupOf = (el: { probeTrigger?: string }) => el.probeTrigger?.trim() || BASE_GROUP;
const GROUP_ORDER = [BASE_GROUP, '新增', '新建', '添加', '创建', '录入', '上传', '导入', '授权', '登记', '申请', '编辑', '修改', '删除', '移除'];
// 前缀匹配:触发词是「动词+宾语」形式(如「新增隐患」),按动词前缀归类颜色
const groupTagColor = (g: string) =>
  g === BASE_GROUP
    ? 'default'
    : /^(新增|新建|添加|创建|录入|上传|导入|授权|登记|申请)/.test(g)
      ? 'green'
      : /^(编辑|修改)/.test(g)
        ? 'blue'
        : /^(删除|移除)/.test(g)
          ? 'red'
          : 'purple';
const groupLabel = (g: string) => (g === BASE_GROUP ? '页面主体' : `${g} 弹窗`);
const groupSortIndex = (g: string) => {
  const verbIndex = GROUP_ORDER.findIndex(
    (k) => k !== BASE_GROUP && g.startsWith(k),
  );
  return g === BASE_GROUP ? 0 : verbIndex === -1 ? GROUP_ORDER.length : verbIndex + 1;
};

const formatTime = (value?: string | null) => {
  if (!value) return '-';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
};

/** localStorage 里的环境 id 是字符串(可能为 env-1 形态);仅数字才作为后端 environment_id */
const currentEnvId = (): number | null => {
  const raw = localStorage.getItem('currentEnvironmentId');
  if (!raw) return null;
  const id = Number(raw);
  return Number.isFinite(id) && id > 0 ? id : null;
};

/** localStorage 缺失/非数字(env-1)时,用后端列表解析真实 id 并写回,避免 environment_id 一直为 null */
const resolveAndPersistEnvId = (list: Array<{ id: number | string; is_default?: boolean }>): number | null => {
  const saved = currentEnvId();
  const matched = saved != null ? list.find((e) => Number(e.id) === saved) : undefined;
  const env = matched ?? list.find((e) => e.is_default) ?? list[0];
  if (!env) return saved;
  const id = Number(env.id);
  if (!Number.isFinite(id) || id <= 0) return null;
  localStorage.setItem('currentEnvironmentId', String(id));
  return id;
};

interface ExtractFormState {
  name: string;
  site_origin: string;
  page_path: string;
  use_llm_naming: boolean;
  /** 浮层递归深度:0=只采当前页,默认 3,上限 5(仅浮层不跟路由) */
  max_depth: number;
  /** 关联的登录页对象(0=不关联):有登录态直接复用,没有就先执行它的登录步骤 */
  login_page_object_id: number;
}

const EMPTY_EXTRACT: ExtractFormState = {
  name: '',
  site_origin: '',
  page_path: '',
  use_llm_naming: true,
  max_depth: 3,
  login_page_object_id: 0,
};

const UIAutomationPlatform: React.FC = () => {
  const [pageObjects, setPageObjects] = useState<PageObject[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selected, setSelected] = useState<PageObject | null>(null);
  const [activeTab, setActiveTab] = useState('elements');
  const [loading, setLoading] = useState(false);

  const [extractOpen, setExtractOpen] = useState(false);
  const [extractForm, setExtractForm] = useState<ExtractFormState>(EMPTY_EXTRACT);
  const [extractMode, setExtractMode] = useState<'create' | 'regenerate'>('create');
  const [mergeOnRegenerate, setMergeOnRegenerate] = useState(true);
  const [editOpen, setEditOpen] = useState(false);
  const [editForm, setEditForm] = useState({ name: '', site_origin: '', page_path: '', description: '', login_page_object_id: 0, is_login_page: false });
  const [extracting, setExtracting] = useState(false);
  const [task, setTask] = useState<UIAutomationTaskStatus | null>(null);
  // 侧栏搜索:全量扫描会创建大量页面对象,按名称/URL 过滤
  const [sidebarKeyword, setSidebarKeyword] = useState('');
  // 侧栏分页:列表可能上百条,按页渲染;搜索关键字变化时重置回第一页
  const [sidebarPage, setSidebarPage] = useState(1);
  useEffect(() => {
    setSidebarPage(1);
  }, [sidebarKeyword]);
  // 验证定位器的进行中状态:任务异步执行,按钮需要 loading 反馈
  const [validating, setValidating] = useState(false);
  // 默认/当前环境参数:仅用于展示时解析 {{web_url}} 等占位符,落库仍存原文
  const [displayParams, setDisplayParams] = useState<Record<string, string>>({});

  // 鲁班平台「按菜单生成 URL」:模块/菜单选择,access-token 仅内存使用不落库
  const [lubanOpen, setLubanOpen] = useState(false);
  const [lubanToken, setLubanToken] = useState('');
  const [lubanResId, setLubanResId] = useState('');
  const [lubanModules, setLubanModules] = useState<LubanModule[]>([]);
  const [lubanModuleCode, setLubanModuleCode] = useState('');
  const [lubanMenus, setLubanMenus] = useState<LubanMenuNode[]>([]);
  const [lubanMenuId, setLubanMenuId] = useState('');
  const [lubanLoading, setLubanLoading] = useState<'' | 'modules' | 'menus'>('');
  // 全量扫描:遍历全部模块菜单,批量创建页面对象
  const [lubanScanning, setLubanScanning] = useState(false);
  const [lubanScanProgress, setLubanScanProgress] = useState<{ done: number; total: number } | null>(null);
  const [lubanAutoExtract, setLubanAutoExtract] = useState(false);

  const [elements, setElements] = useState<PageElement[]>([]);
  const [expandedRowKeys, setExpandedRowKeys] = useState<React.Key[]>([]);
  const handleExpandedRowsChange = useCallback((keys: readonly React.Key[]) => {
    setExpandedRowKeys([...keys]);
  }, []);
  // 父节点(触发按钮)自身的定位器/说明编辑:展开只给子级,编辑走抽屉
  const [locatorEditKey, setLocatorEditKey] = useState<string | null>(null);
  /** 新增元素弹窗:空 parentKey = 顶层(页面主体),否则为父节点 key */
  const [addElementOpen, setAddElementOpen] = useState(false);
  const [addElementForm, setAddElementForm] = useState<{
    name: string;
    type: string;
    parentKey: string;
    locators: PageLocator[];
  }>({ name: '新元素', type: 'input', parentKey: '', locators: [] });
  const [validation, setValidation] = useState<Array<Record<string, any>> | null>(null);
  const [code, setCode] = useState('');
  const [steps, setSteps] = useState<Array<Record<string, any>>>([]);
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<{ trace: any[]; heal: any[]; status: string } | null>(null);
  const [runs, setRuns] = useState<UIAutomationRun[]>([]);

  const timersRef = useRef<Array<ReturnType<typeof setInterval>>>([]);

  const stopTimers = useCallback(() => {
    timersRef.current.forEach((timer) => clearInterval(timer));
    timersRef.current = [];
  }, []);

  useEffect(() => () => stopTimers(), [stopTimers]);

  const loadPageObjects = useCallback(async (selectId?: number) => {
    setLoading(true);
    try {
      const res = await uiAutomationApi.listPageObjects();
      const rows = res.data ?? [];
      setPageObjects(rows);
      if (selectId) {
        setSelectedId(selectId);
      } else if (rows.length > 0 && selectedId === null) {
        setSelectedId(rows[0].id);
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载页面对象失败');
    } finally {
      setLoading(false);
    }
  }, [selectedId]);

  useEffect(() => {
    loadPageObjects();
  }, []);

  useEffect(() => {
    globalParameterApi
      .getEnvironments()
      .then((res) => {
        const list = Array.isArray(res.data) ? res.data : [];
        const envId = resolveAndPersistEnvId(list);
        const env =
          (envId != null ? list.find((e) => Number(e.id) === envId) : undefined) ??
          list.find((e) => e.is_default) ??
          list[0];
        if (!env) return;
        const map: Record<string, string> = {};
        for (const p of env.parameters || []) {
          if (p?.key) map[p.key.trim()] = p.value ?? '';
        }
        setDisplayParams(map);
      })
      .catch((e) => {
        // 展示解析失败不打断页面,仅留日志便于排查(变量会保持原文 {{key}})
        console.warn('加载环境参数失败,{{变量}} 将按原文显示', e);
      });
  }, []);

  const resolveVars = useCallback(
    (text?: string | null) => {
      if (!text) return '';
      return text.replace(/\{\{([^}]+)\}\}/g, (raw, key: string) => {
        const value = displayParams[key.trim()];
        return value ? value : raw;
      });
    },
    [displayParams]
  );

  const loadRuns = useCallback(async (pageObjectId: number) => {
    try {
      const res = await uiAutomationApi.listRuns(pageObjectId);
      setRuns(res.data ?? []);
    } catch {
      setRuns([]);
    }
  }, []);

  const loadPageObject = useCallback(async (id: number) => {
    try {
      const res = await uiAutomationApi.getPageObject(id);
      if (!res.data) return;
      setSelected(res.data);
      setElements(res.data.elements ?? []);
      setExpandedRowKeys([]);
      // 步骤持久化在页面对象上:登录页对象的登录步骤就是在这里被保存/复用的
      setSteps(res.data.steps ?? []);
      loadRuns(res.data.id);
    } catch {
      setSelected(null);
    }
  }, [loadRuns]);

  useEffect(() => {
    if (selectedId === null) {
      setSelected(null);
      return;
    }
    setValidation(null);
    setCode('');
    setRunResult(null);
    setSteps([]);
    loadPageObject(selectedId);
  }, [selectedId, loadPageObject]);

  const watchTask = useCallback(
    (taskId: string, onDone: (t: UIAutomationTaskStatus) => void, onLost?: () => void) => {
      const timer = setInterval(async () => {
        try {
          const res = await uiAutomationApi.getTask(taskId);
          const current = res.data;
          if (!current) return;
          setTask(current);
          if (current.status === 'done' || current.result_status) {
            clearInterval(timer);
            onDone(current);
          }
        } catch (e: any) {
          if (e?.response?.status === 404) {
            clearInterval(timer);
            message.warning('任务状态已丢失(后端可能重启),请刷新后从运行历史查看结果');
            onLost?.();
          }
          /* 其他轮询失败等待下次 */
        }
      }, 2000);
      timersRef.current.push(timer);
    },
    []
  );

  // ---- 登录态:载体是页面对象(登录=一个配了执行步骤的页面对象) ----

  /** 侧栏过滤后的页面对象(按名称/URL 关键字) */
  const filteredPageObjects = useMemo(() => {
    const kw = sidebarKeyword.trim().toLowerCase();
    if (!kw) return pageObjects;
    return pageObjects.filter((item) => `${item.name} ${item.url ?? ''}`.toLowerCase().includes(kw));
  }, [pageObjects, sidebarKeyword]);

  /** 可作为登录页对象被关联的候选(标记了「作为登录页对象」的页面对象) */
  const loginPageItems = useMemo(
    () =>
      pageObjects
        .filter((item) => item.is_login_page)
        .map((item) => ({
          value: item.id,
          label: `${item.name} · ${
            item.session_status === 'READY' ? '已登录' : item.session_status === 'FAILED' ? '登录失败' : '未登录'
          }`,
        })),
    [pageObjects]
  );

  /** 编辑信息里的「关联登录页对象」下拉:0 = 解除关联 */
  const loginPageOptions = useMemo(
    () => [{ value: 0, label: '不关联登录页对象' }, ...loginPageItems],
    [loginPageItems]
  );

  /** 抽取/重新生成弹窗:0 = 本次不使用登录态(匿名访问,不读站点缓存、不触发登录) */
  const extractLoginPageOptions = useMemo(
    () => [{ value: 0, label: '不使用登录态(匿名访问,不读站点已缓存登录态)' }, ...loginPageItems],
    [loginPageItems]
  );

  /** 本次生效的登录页对象:自身关联的 > 自己就是登录页对象 */
  const activeLoginPage = useMemo(() => {
    if (!selected) return null;
    if (selected.login_page_object_id) {
      return pageObjects.find((item) => item.id === selected.login_page_object_id) ?? null;
    }
    return selected.is_login_page ? selected : null;
  }, [selected, pageObjects]);

  const renderSessionTag = (target?: PageObject | null) => {
    if (!target) return <Tag>未关联登录页对象</Tag>;
    if (target.session_status === 'READY') {
      return (
        <Tag color="green">
          已登录({target.session_cookie_count ?? 0} cookie / {target.session_origin_count ?? 0} 存储)
        </Tag>
      );
    }
    if (target.session_status === 'FAILED') {
      const err = target.last_login_error ?? '';
      const short = err.length > 24 ? `${err.slice(0, 24)}…` : err;
      return (
        <Tooltip title={err}>
          <Tag color="red">登录失败{short ? `:${short}` : ''}</Tag>
        </Tooltip>
      );
    }
    return <Tag>未登录(使用时自动执行登录步骤)</Tag>;
  };

  /** 刷新当前页面对象(拿最新登录态) */
  const refreshSelected = useCallback(
    async (id: number) => {
      try {
        const res = await uiAutomationApi.getPageObject(id);
        if (res.data) setSelected(res.data);
      } catch {
        /* 忽略:只是刷新展示 */
      }
    },
    []
  );

  /** 清除登录态:下次抽取/执行会重新执行登录页对象的步骤建立 */
  const clearPageObjectLogin = async (target: PageObject) => {
    try {
      const res = await uiAutomationApi.clearPageObjectSession(target.id);
      if (res.code === 200) {
        message.success('已清除登录态,下次使用会重新执行登录步骤');
        await loadPageObjects(selectedId ?? undefined);
        if (selectedId) await refreshSelected(selectedId);
      } else {
        message.error(res.message || '清除失败');
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '清除失败');
    }
  };

  /** 把执行(自愈)里的步骤保存到页面对象:登录页对象靠它被其他页面对象调用 */
  const saveSteps = async () => {
    if (!selected) return;
    try {
      const res = await uiAutomationApi.updatePageObject(selected.id, {
        name: selected.name,
        site_origin: selected.site_origin,
        page_path: selected.page_path,
        url: selected.url,
        description: selected.description,
        elements,
        steps,
        is_login_page: selected.is_login_page,
        login_page_object_id: selected.login_page_object_id ?? 0,
      });
      if (res.code === 200 && res.data) {
        message.success('步骤已保存到页面对象,其他页面对象关联它即可调用');
        setSelected(res.data);
        await loadPageObjects(selected.id);
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存步骤失败');
    }
  };

  const openCreateExtract = () => {
    setExtractMode('create');
    setExtractForm(EMPTY_EXTRACT);
    setTask(null);
    setExtractOpen(true);
  };

  const openRegenerate = () => {
    if (!selected) return;
    setExtractMode('regenerate');
    setExtractForm({
      name: selected.name,
      site_origin: selected.site_origin,
      page_path: selected.page_path,
      use_llm_naming: true,
      max_depth: 3,
      // 自己就是登录页对象时预选它自己(拿自己的登录态抽,否则会被重定向到登录页);
      // 未关联登录页对象的普通页面预选 0 = 本次以匿名身份抽取,不读已缓存登录态
      login_page_object_id:
        selected.login_page_object_id || (selected.is_login_page ? selected.id : 0),
    });
    setMergeOnRegenerate(true);
    setTask(null);
    setExtractOpen(true);
  };

  // ---- 鲁班平台「按菜单生成 URL」 ----

  const openLubanModal = () => {
    setLubanModuleCode('');
    setLubanMenus([]);
    setLubanMenuId('');
    setLubanOpen(true);
  };

  const loadLubanModules = async () => {
    const base = extractForm.site_origin.trim();
    const token = lubanToken.trim();
    if (!base) {
      message.warning('请先在抽取表单填写站点地址');
      return;
    }
    if (!token) {
      message.warning('请填写 access-token');
      return;
    }
    setLubanLoading('modules');
    try {
      const res = await uiAutomationApi.lubanModules({
        base_url: base,
        access_token: token,
        res_id: lubanResId.trim(),
        environment_id: currentEnvId(),
      });
      if (res.code !== 200) {
        message.error(res.message || '获取系统模块失败');
        return;
      }
      setLubanModules((res.data || []).filter((m) => m.url));
      setLubanMenus([]);
      setLubanMenuId('');
    } catch (err: any) {
      message.error(err?.response?.data?.detail || err?.message || '获取系统模块失败');
    } finally {
      setLubanLoading('');
    }
  };

  const loadLubanMenus = async (moduleCode: string) => {
    setLubanModuleCode(moduleCode);
    setLubanMenus([]);
    setLubanMenuId('');
    if (!moduleCode) return;
    const base = extractForm.site_origin.trim();
    setLubanLoading('menus');
    try {
      const res = await uiAutomationApi.lubanMenus({
        base_url: base,
        access_token: lubanToken.trim(),
        res_id: lubanResId.trim(),
        sys_module_code: moduleCode,
        environment_id: currentEnvId(),
      });
      if (res.code !== 200) {
        message.error(res.message || '获取菜单失败');
        return;
      }
      setLubanMenus(res.data || []);
    } catch (err: any) {
      message.error(err?.response?.data?.detail || err?.message || '获取菜单失败');
    } finally {
      setLubanLoading('');
    }
  };

  const lubanTreeData = useMemo(() => {
    const byParent = new Map<string, LubanMenuNode[]>();
    (lubanMenus || []).forEach((node) => {
      const key = node.parentId || '-1';
      byParent.set(key, [...(byParent.get(key) || []), node]);
    });
    const build = (node: LubanMenuNode): Record<string, any> => ({
      value: node.id,
      title: node.name,
      selectable: Boolean(node.url && node.url !== '/#' && node.type === 'menu' && !node.isLink),
      children: (byParent.get(node.id) || []).map(build),
    });
    return (byParent.get('-1') || []).map(build);
  }, [lubanMenus]);

  const applyLubanMenu = (menuId: string) => {
    setLubanMenuId(menuId);
    const menu = lubanMenus.find((n) => n.id === menuId);
    const mod = lubanModules.find((m) => m.code === lubanModuleCode);
    if (!menu || !mod) return;
    // 沿 parentId 向上收集目录 url 段(跳过无页面的分组,如 url 为空或 "/#")
    const byId = new Map(lubanMenus.map((n) => [n.id, n]));
    const segments: string[] = [];
    let current: LubanMenuNode | undefined = menu;
    while (current) {
      const seg = (current.url || '').trim();
      if (seg && seg !== '/#') segments.unshift(seg);
      current = current.parentId && current.parentId !== '-1' ? byId.get(current.parentId) : undefined;
    }
    const base = extractForm.site_origin.trim().replace(/\/+$/, '');
    const hash = `#/home${mod.url}${segments.join('')}`;
    setExtractForm((prev) => ({
      ...prev,
      site_origin: base,
      page_path: hash,
      name: prev.name.trim() || menu.name,
    }));
    message.success(`已生成页面路径: ${hash}`);
    setLubanOpen(false);
  };

  // 全量扫描:遍历全部模块的菜单树,批量创建页面对象(按 URL 去重)
  const runLubanScan = async () => {
    const base = extractForm.site_origin.trim().replace(/\/+$/, '');
    const token = lubanToken.trim();
    if (!base) {
      message.warning('请先在抽取表单填写站点地址');
      return;
    }
    if (!token) {
      message.warning('请填写 access-token');
      return;
    }
    setLubanScanning(true);
    try {
      const modRes = await uiAutomationApi.lubanModules({
        base_url: base,
        access_token: token,
        res_id: lubanResId.trim(),
        environment_id: currentEnvId(),
      });
      if (modRes.code !== 200) {
        message.error(modRes.message || '获取系统模块失败');
        return;
      }
      const modules = (modRes.data || []).filter((m) => m.url && !m.isLink);
      if (!modules.length) {
        message.info('没有可扫描的模块');
        return;
      }
      const existing = new Set(pageObjects.map((p) => p.url));
      const pages: Array<{ name: string; page_path: string; url: string }> = [];
      let done = 0;
      for (const mod of modules) {
        const menuRes = await uiAutomationApi.lubanMenus({
          base_url: base,
          access_token: token,
          res_id: lubanResId.trim(),
          sys_module_code: mod.code,
          environment_id: currentEnvId(),
        });
        done += 1;
        setLubanScanProgress({ done, total: modules.length });
        if (menuRes.code !== 200) continue;
        const menus = menuRes.data || [];
        const byId = new Map(menus.map((n) => [n.id, n]));
        for (const node of menus) {
          if (node.type !== 'menu' || !node.url || node.url === '/#' || node.isLink) continue;
          const segments: string[] = [];
          const dirNames: string[] = [];
          // 从菜单自身开始走 parentId 链:必须包含菜单自己的 url,
          // 否则同目录下所有菜单会坍缩成同一个「目录根 URL」(重复+缺失的根源)
          let current: LubanMenuNode | undefined = node;
          while (current) {
            const seg = (current.url || '').trim();
            if (seg && seg !== '/#') segments.unshift(seg);
            if (current.name) dirNames.unshift(current.name);
            current = current.parentId && current.parentId !== '-1' ? byId.get(current.parentId) : undefined;
          }
          const hash = `#/home${mod.url}${segments.join('')}`;
          const full = `${base}/?redirect=${hash}`;
          if (existing.has(full)) continue;
          existing.add(full);
          pages.push({
            name: dirNames.join('-').slice(0, 50),
            page_path: hash,
            url: full,
          });
        }
      }
      if (!pages.length) {
        message.info('没有发现可新增的页面(可能都已创建)');
        return;
      }
      const autoExtract = lubanAutoExtract;
      Modal.confirm({
        title: `发现 ${pages.length} 个可创建页面`,
        content: autoExtract
          ? '将批量创建页面对象,并自动排队抽取元素(后端串行执行,每页约 1-2 分钟)'
          : '仅批量创建页面对象(不抽取元素),之后可逐个或重新生成时抽取',
        okText: `创建 ${pages.length} 个`,
        cancelText: '取消',
        onOk: async () => {
          let created = 0;
          let failed = 0;
          for (const page of pages) {
            try {
              const res = await uiAutomationApi.createPageObject({
                name: page.name,
                site_origin: base,
                page_path: page.page_path,
                url: page.url,
              });
              if (res.code === 200) {
                created += 1;
                if (autoExtract && res.data?.id) {
                  await uiAutomationApi.extract({
                    name: page.name,
                    site_origin: base,
                    page_path: page.page_path,
                    url: page.url,
                    use_llm_naming: true,
                    environment_id: currentEnvId(),
                  });
                }
              } else {
                failed += 1;
              }
            } catch {
              failed += 1;
            }
          }
          await loadPageObjects();
          message.success(
            `已创建 ${created} 个页面对象${failed ? `,失败 ${failed} 个` : ''}` +
              (autoExtract ? ',元素抽取已排队,后端串行执行' : ''),
          );
          setLubanOpen(false);
        },
      });
    } catch (err: any) {
      message.error(err?.response?.data?.detail || err?.message || '全量扫描失败');
    } finally {
      setLubanScanning(false);
      setLubanScanProgress(null);
    }
  };

  const startExtract = async () => {
    if (extractMode === 'create' && !extractForm.site_origin.trim()) {
      message.warning('请填写站点地址');
      return;
    }
    setExtracting(true);
    setTask(null);
    try {
      const payload = {
        name: extractForm.name.trim(),
        site_origin: extractForm.site_origin.trim(),
        page_path: extractForm.page_path.trim(),
        use_llm_naming: extractForm.use_llm_naming,
        max_depth: extractForm.max_depth,
        login_page_object_id: extractForm.login_page_object_id || null,
        // 没选登录页对象 = 本次显式不使用登录态(匿名抽取,不读站点缓存、不登录)
        skip_login: !extractForm.login_page_object_id,
        environment_id: currentEnvId(),
      };
      const res =
        extractMode === 'regenerate' && selected
          ? await uiAutomationApi.regenerate(selected.id, {
              use_llm_naming: extractForm.use_llm_naming,
              merge: mergeOnRegenerate,
              max_depth: extractForm.max_depth,
              login_page_object_id: extractForm.login_page_object_id || null,
              // 没选登录页对象 = 本次显式不使用登录态(匿名抽取,不读站点缓存、不登录)
              skip_login: !extractForm.login_page_object_id,
              environment_id: currentEnvId(),
            })
          : await uiAutomationApi.extract(payload);
      if (res.code !== 200 || !res.data) {
        message.error(res.message || (extractMode === 'regenerate' ? '发起重新生成失败' : '发起抽取失败'));
        setExtracting(false);
        return;
      }
      watchTask(res.data.task_id, async (done) => {
        setExtracting(false);
        if (done.result_status === 'PASSED') {
          message.success(extractMode === 'regenerate' ? '页面对象已重新生成' : '页面元素抽取完成');
          setExtractOpen(false);
          setExtractForm(EMPTY_EXTRACT);
          if (extractMode === 'regenerate' && selected) {
            await loadPageObject(selected.id);
            await loadPageObjects(selected.id);
          } else {
            await loadPageObjects(done.page_object_id ?? undefined);
          }
        } else {
          message.error(done.error || (extractMode === 'regenerate' ? '重新生成失败' : '抽取失败'));
        }
      }, () => setExtracting(false));
    } catch (e: any) {
      setExtracting(false);
      message.error(e?.response?.data?.detail || '发起请求失败');
    }
  };

  const saveElements = async () => {
    if (!selected) return;
    try {
      const res = await uiAutomationApi.updatePageObject(selected.id, {
        name: selected.name,
        site_origin: selected.site_origin,
        page_path: selected.page_path,
        url: selected.url,
        description: selected.description,
        elements,
      });
      if (res.code === 200) {
        message.success('已保存');
        setSelected(res.data ?? selected);
        loadPageObjects(selected.id);
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存失败');
    }
  };

  // 清空元素:直接落库(不经过「保存元素」),所以必须二次确认。
  // 元素全没了以后,已配置步骤里的 element key 必然失效,一并清掉。
  const clearElements = () => {
    if (!selected) return;
    if (totalElementCount === 0) {
      message.info('当前没有元素可清空');
      return;
    }
    const count = totalElementCount;
    Modal.confirm({
      title: `清空「${selected.name}」的全部元素?`,
      content: (
        <div>
          将删除 {count} 个元素(含子节点)及其定位器、名称编辑与自愈记录,已配置的执行步骤同时清空。
          此操作不可撤销,之后需要重新抽取或手工新增。
        </div>
      ),
      okText: '清空',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        try {
          const res = await uiAutomationApi.updatePageObject(selected.id, {
            name: selected.name,
            site_origin: selected.site_origin,
            page_path: selected.page_path,
            url: selected.url,
            description: selected.description,
            elements: [],
          });
          if (res.code !== 200) {
            message.error(res.message || '清空失败');
            return;
          }
          setElements([]);
          setExpandedRowKeys([]);
          setSteps([]);
          setSelected(res.data ?? selected);
          await loadPageObjects(selected.id);
          message.success(`已清空 ${count} 个元素`);
        } catch (e: any) {
          message.error(e?.response?.data?.detail || '清空失败');
        }
      },
    });
  };

  const openEditPageObject = () => {
    if (!selected) return;
    setEditForm({
      name: selected.name,
      site_origin: selected.site_origin,
      page_path: selected.page_path,
      description: selected.description,
      login_page_object_id: selected.login_page_object_id ?? 0,
      is_login_page: selected.is_login_page,
    });
    setEditOpen(true);
  };

  const savePageObjectMeta = async () => {
    if (!selected) return;
    if (!editForm.name.trim()) {
      message.warning('名称不能为空');
      return;
    }
    const url = editForm.site_origin.trim()
      ? `${editForm.site_origin.trim().replace(/\/+$/, '')}${editForm.page_path.trim().startsWith('#') || editForm.page_path.trim().startsWith('/') ? '' : '/'}${editForm.page_path.trim()}`
      : selected.url;
    try {
      const res = await uiAutomationApi.updatePageObject(selected.id, {
        name: editForm.name.trim(),
        site_origin: editForm.site_origin.trim(),
        page_path: editForm.page_path.trim(),
        url,
        description: editForm.description.trim(),
        elements,
        steps,
        is_login_page: editForm.is_login_page,
        login_page_object_id: editForm.login_page_object_id || 0,
      });
      if (res.code === 200 && res.data) {
        message.success('已保存');
        setSelected(res.data);
        setElements(res.data.elements ?? []);
        setExpandedRowKeys([]);
        setEditOpen(false);
        loadPageObjects(selected.id);
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存失败');
    }
  };

  const deletePageObject = async (id: number) => {
    try {
      await uiAutomationApi.deletePageObject(id);
      message.success('已删除');
      if (selectedId === id) setSelectedId(null);
      await loadPageObjects();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '删除失败');
    }
  };

  const validateLocators = async () => {
    if (!selected) return;
    setValidation(null);
    setValidating(true);
    try {
      const res = await uiAutomationApi.validate(selected.id, {
        login_page_object_id: activeLoginPage?.id ?? selected.login_page_object_id ?? null,
        environment_id: currentEnvId(),
      });
      if (res.code !== 200 || !res.data) {
        message.error(res.message || '发起验证失败');
        setValidating(false);
        return;
      }
      watchTask(res.data.task_id, async (done) => {
        setValidating(false);
        if (done.result_status === 'PASSED' && done.record_id) {
          const runRes = await uiAutomationApi.getRun(done.record_id);
          setValidation((runRes.data?.result?.elements as Array<Record<string, any>>) ?? []);
          message.success(runRes.data?.summary || '验证完成');
        } else {
          message.error(done.error || '验证失败');
        }
      }, () => setValidating(false));
    } catch (e: any) {
      setValidating(false);
      message.error(e?.response?.data?.detail || '发起验证失败');
    }
  };

  const loadCode = async () => {
    if (!selected) return;
    try {
      const text = await uiAutomationApi.exportCode(selected.id);
      setCode(typeof text === 'string' ? text : String(text));
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '导出失败');
    }
  };

  /** 步骤将实际执行的列表(优先本次编辑,否则页面对象已保存步骤) */
  const activeRunSteps = () => (steps.length > 0 ? steps : (selected?.steps ?? []));

  /** 引用了 enabled===false 元素的步骤 → 无效元素 key(有序去重) */
  const invalidStepElementKeys = (runStepsList: Array<Record<string, any>>) => {
    const disabled = new Map<string, string>();
    flattenElementTree(elements).forEach(({ element }) => {
      if (element.enabled === false) disabled.set(element.key, element.name || element.key);
    });
    if (!disabled.size) return [];
    const keys: string[] = [];
    runStepsList.forEach((step) => {
      const key = step?.element;
      if (key && disabled.has(key) && !keys.includes(key)) keys.push(key);
    });
    return keys;
  };

  const runSteps = async () => {
    if (!selected || (steps.length === 0 && (selected.steps ?? []).length === 0)) {
      message.warning('请先添加执行步骤(或使用页面对象上已保存的步骤)');
      return;
    }
    const invalidKeys = invalidStepElementKeys(activeRunSteps());
    if (invalidKeys.length) {
      const disabledMap = new Map<string, string>();
      flattenElementTree(elements).forEach(({ element }) => {
        if (element.enabled === false) disabledMap.set(element.key, element.name || element.key);
      });
      const labels = invalidKeys.map((k) => {
        const name = disabledMap.get(k) || k;
        return name === k ? k : `${name}(${k})`;
      });
      message.error(`步骤中存在无效的元素: ${labels.join('、')}`);
      return;
    }
    setRunning(true);
    setRunResult(null);
    try {
      const res = await uiAutomationApi.run(selected.id, steps, {
        login_page_object_id: activeLoginPage?.id ?? selected.login_page_object_id ?? null,
        environment_id: currentEnvId(),
      });
      if (res.code !== 200 || !res.data) {
        message.error(res.message || '发起执行失败');
        setRunning(false);
        return;
      }
      watchTask(res.data.task_id, async (done) => {
        setRunning(false);
        if (done.record_id) {
          const runRes = await uiAutomationApi.getRun(done.record_id);
          const run = runRes.data;
          if (run) {
            setRunResult({ trace: run.steps_trace ?? [], heal: run.heal_events ?? [], status: run.status });
            setRuns((prev) => [run, ...prev]);
          }
        }
        if (done.result_status === 'PASSED') {
          message.success('执行通过');
        } else if (done.result_status === 'FAILED') {
          message.warning('执行未通过(部分步骤失败)');
        } else if (done.result_status === 'ERROR') {
          message.error(done.error || '执行出错');
        }
        // 执行中的登录动作会把最新登录态写回配置(自动抽取登录状态),刷新状态标记
      }, () => setRunning(false));
    } catch (e: any) {
      setRunning(false);
      message.error(e?.response?.data?.detail || '发起执行失败');
    }
  };

  // 元素编辑按 key 在树中深度定位(不再假设扁平数组下标)
  const updateElement = (key: string, patch: Partial<PageElement>) => {
    setElements((prev) => updateByKey(prev, key, patch));
  };

  /** 打开新增元素弹窗(parentKey 预选挂载位置) */
  const openAddElement = (parentKey = '') => {
    if (!selected) return;
    setAddElementForm({
      name: '新元素',
      type: 'input',
      parentKey,
      locators: [emptyLocator()],
    });
    setAddElementOpen(true);
  };

  const updateAddLocator = (index: number, patch: Partial<PageLocator>) => {
    setAddElementForm((prev) => ({
      ...prev,
      locators: prev.locators.map((loc, i) => (i === index ? { ...loc, ...patch } : loc)),
    }));
  };

  const addAddLocator = () => {
    setAddElementForm((prev) => ({ ...prev, locators: [...prev.locators, emptyLocator()] }));
  };

  const removeAddLocator = (index: number) => {
    setAddElementForm((prev) => ({
      ...prev,
      locators: prev.locators.filter((_, i) => i !== index),
    }));
  };

  /** 确认新增:插到目标位置最前;父级自动展开;丢弃空定位器 */
  const confirmAddElement = () => {
    if (!selected) return;
    const { name, type, parentKey, locators } = addElementForm;
    const keptLocators = locators
      .filter((loc) => loc.value.trim())
      .map((loc) => ({ ...loc, value: loc.value.trim() }));
    setElements((prev) => {
      const key = nextElementKey(prev);
      const child: PageElement = {
        key,
        name: name.trim() || '新元素',
        type: type || 'input',
        enabled: true,
        locators: keptLocators,
      };
      if (!parentKey) return [child, ...prev];
      return addChildByKey(prev, parentKey, child);
    });
    if (parentKey) {
      setExpandedRowKeys((prev) => (prev.includes(parentKey) ? prev : [...prev, parentKey]));
    }
    setAddElementOpen(false);
    message.success(keptLocators.length ? '已新增元素(含定位器)' : '已新增元素');
  };

  const updateLocator = (elementKey: string, locatorIndex: number, patch: Partial<PageLocator>) => {
    setElements((prev) => updateLocatorByKey(prev, elementKey, locatorIndex, patch));
  };

  const addLocator = (elementKey: string) => {
    setElements((prev) => addLocatorByKey(prev, elementKey));
  };

  const removeLocator = (elementKey: string, locatorIndex: number) => {
    setElements((prev) => removeLocatorByKey(prev, elementKey, locatorIndex));
  };

  // 树形:带 children 的触发按钮作顶层可展开行;无子节点的根归入「页面主体」。
  // 旧扁平数据(仅有 probeTrigger、无 children)仍按触发词归组为一层伪分组。
  const elementTreeRows = useMemo(() => {
    type Row = PageElement & { isGroup?: boolean; group?: string; count?: number };
    const hasTree = elements.some((el) => (el.children?.length ?? 0) > 0);
    if (hasTree) {
      const parents = elements.filter((el) => (el.children?.length ?? 0) > 0);
      const leaves = elements.filter((el) => (el.children?.length ?? 0) === 0);
      const rows: Row[] = [];
      if (leaves.length) {
        rows.push({
          key: `__group__::${BASE_GROUP}`,
          name: groupLabel(BASE_GROUP),
          type: '',
          locators: [],
          isGroup: true,
          group: BASE_GROUP,
          count: leaves.length,
          children: leaves,
        });
      }
      rows.push(...parents);
      return rows;
    }
    // 扁平:按 probeTrigger 分组成一层(保持旧 UI 分组语义)
    const groups = new Map<string, PageElement[]>();
    elements.forEach((el) => {
      const g = groupOf(el);
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g)!.push(el);
    });
    return [...groups.keys()]
      .sort((a, b) => groupSortIndex(a) - groupSortIndex(b))
      .map((g) => ({
        key: `__group__::${g}`,
        name: groupLabel(g),
        type: '',
        locators: [],
        isGroup: true as const,
        group: g,
        count: (groups.get(g) || []).length,
        children: groups.get(g) || [],
      }));
  }, [elements]);

  const totalElementCount = useMemo(() => flattenElementTree(elements).length, [elements]);

  // 层级序号:顶层 1,2…;子级 2.1、2.1.1(按当前树顺序,含分组行)
  const seqByKey = useMemo(() => {
    const map = new Map<string, string>();
    const walk = (nodes: Array<PageElement & { isGroup?: boolean }>, prefix?: string) => {
      let i = 0;
      for (const n of nodes || []) {
        i += 1;
        const seq = prefix ? `${prefix}.${i}` : String(i);
        map.set(n.key, seq);
        if (n.children?.length) {
          walk(n.children as Array<PageElement & { isGroup?: boolean }>, seq);
        }
      }
    };
    walk(elementTreeRows as Array<PageElement & { isGroup?: boolean }>);
    return map;
  }, [elementTreeRows]);

  // 可展开 key:仅有子级的行(分组/触发按钮);叶子不可展开,编辑走操作列抽屉
  const expandableKeys = useMemo(() => {
    const keys: React.Key[] = [];
    const walk = (nodes?: Array<PageElement & { isGroup?: boolean }>) => {
      for (const n of nodes || []) {
        if (n.isGroup || n.children?.length) {
          keys.push(n.key);
          if (n.children?.length) walk(n.children as Array<PageElement & { isGroup?: boolean }>);
        }
      }
    };
    walk(elementTreeRows as Array<PageElement & { isGroup?: boolean }>);
    return keys;
  }, [elementTreeRows]);

  const locatorEditNode = useMemo(() => {
    if (!locatorEditKey) return null;
    return flattenElementTree(elements).find((f) => f.element.key === locatorEditKey)?.element ?? null;
  }, [elements, locatorEditKey]);

  // 工具栏「挂到」下拉:顶层 + 所有可作父级的触发按钮(含嵌套)
  const parentAddOptions = useMemo(() => {
    const opts: Array<{ value: string; label: string }> = [
      { value: '', label: '页面主体(顶层)' },
    ];
    const walk = (nodes: PageElement[], prefix = '') => {
      for (const n of nodes) {
        if (n.children?.length) {
          opts.push({
            value: n.key,
            label: `${prefix}${n.name || n.key} (${n.children.length} 子级)`,
          });
          walk(n.children, `${prefix}${n.name || n.key} / `);
        }
      }
    };
    walk(elements);
    return opts;
  }, [elements]);

  const rowKindClass = (row: PageElement & { isGroup?: boolean }) => {
    if (row.isGroup) return 'ui-auto-tree-row ui-auto-tree-row--group';
    const base = row.children?.length ? 'ui-auto-tree-row ui-auto-tree-row--parent' : 'ui-auto-tree-row ui-auto-tree-row--leaf';
    return row.enabled === false ? `${base} ui-auto-tree-row--disabled` : base;
  };

  // 步骤下拉:树形拍平 + 按祖先路径分组
  const elementSelectOptions = useMemo(() => {
    const paths = pathByKey(elements);
    const flat = flattenElementTree(elements);
    const groups = new Map<string, PageElement[]>();
    flat.forEach(({ element, path }) => {
      const g = path.length ? path.join(' / ') : groupOf(element);
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g)!.push(element);
    });
    return [...groups.entries()].map(([group, items]) => ({
      label: `${group} (${items.length})`,
      title: group,
      options: items.map((el) => {
        const p = paths[el.key] ?? [];
        return { value: el.key, label: `${p.length ? `${p.join(' / ')} / ` : ''}${el.name} (${el.key})` };
      }),
    }));
  }, [elements]);

  const validationTreeRows = useMemo(() => {
    if (!validation) return [];
    const paths = pathByKey(elements);
    // 按祖先路径分组(与后端 ensure_overlay_path 分组一致)
    const groups = new Map<string, Array<Record<string, any>>>();
    validation.forEach((row) => {
      const path = paths[row.key] ?? [];
      const g = path.length
        ? path.join(' / ')
        : groupOf(elements.find((el) => el.key === row.key) ?? ({} as PageElement));
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g)!.push(row);
    });
    if (elements.some((el) => (el.children?.length ?? 0) > 0)) {
      // 树形:validation 行按 key 挂回对应节点的 children(展示层用 elements 结构)
      const attach = (nodes: PageElement[]): Array<Record<string, any>> =>
        nodes.flatMap((node) => {
          const own = validation.filter((r) => r.key === node.key);
          const kids = node.children?.length ? attach(node.children) : [];
          const row = own[0];
          if (!row && !kids.length) return [];
          return [
            {
              ...(row ?? { key: node.key, name: node.name, matched: false, locators: [] }),
              enabled: node.enabled,
              children: kids.length ? kids : undefined,
            },
          ];
        });
      const attached = attach(elements);
      const parents = attached.filter((r) => r.children?.length);
      const leaves = attached.filter((r) => !r.children?.length);
      const rows: Array<Record<string, any>> = [];
      if (leaves.length) {
        rows.push({
          key: `__vgroup__::${BASE_GROUP}`,
          isGroup: true,
          group: BASE_GROUP,
          count: leaves.length,
          children: leaves,
        });
      }
      rows.push(...parents);
      return rows;
    }
    return [...groups.keys()]
      .sort((a, b) => groupSortIndex(a) - groupSortIndex(b))
      .map((g) => ({
        key: `__vgroup__::${g}`,
        isGroup: true,
        group: g,
        count: (groups.get(g) || []).length,
        children: groups.get(g) || [],
      }));
  }, [validation, elements]);

  const validationSeqByKey = useMemo(() => {
    const map = new Map<string, string>();
    const walk = (nodes: Array<Record<string, any>>, prefix?: string) => {
      let i = 0;
      for (const n of nodes || []) {
        i += 1;
        const seq = prefix ? `${prefix}.${i}` : String(i);
        map.set(String(n.key), seq);
        if (n.children?.length) walk(n.children, seq);
      }
    };
    walk(validationTreeRows);
    return map;
  }, [validationTreeRows]);

  type ElementRow = PageElement & { isGroup?: boolean; group?: string; count?: number };

  const elementColumns: TableProps<ElementRow>['columns'] = [
    {
      title: '#',
      width: 64,
      render: (_: unknown, row: ElementRow) => (
        <span className="ui-auto-tree-seq" title={`序号 ${seqByKey.get(row.key) ?? ''}`}>
          {seqByKey.get(row.key) ?? '—'}
        </span>
      ),
    },
    {
      title: '名称 / 分组',
      dataIndex: 'name',
      width: 340,
      render: (value: string, row: ElementRow) =>
        row.isGroup ? (
          <Space size={8} className="ui-auto-tree-group-label">
            <Tag color={groupTagColor(row.group!)} style={{ marginInlineEnd: 0 }}>
              {row.group! === BASE_GROUP ? row.name : groupLabel(row.group!)}
            </Tag>
            <span style={{ color: 'var(--color-text-secondary)' }}>{row.count} 个元素</span>
          </Space>
        ) : (
          <span className="ui-auto-tree-name ui-auto-tree-name--static">
            <span className="ui-auto-tree-name__text" title={value}>
              {value || '—'}
            </span>
            {row.children?.length ? (
              <Tooltip title={`${row.children.length} 个弹窗子元素`}>
                <Tag color="geekblue" className="ui-auto-tree-count" style={{ marginInlineEnd: 0 }}>
                  {row.children.length}
                </Tag>
              </Tooltip>
            ) : null}
          </span>
        ),
    },
    {
      title: 'key',
      dataIndex: 'key',
      width: 130,
      render: (v: string, row: ElementRow) => (row.isGroup ? null : <Tag>{v}</Tag>),
    },
    {
      title: '类型',
      dataIndex: 'type',
      width: 110,
      render: (value: string, row: ElementRow) =>
        row.isGroup ? null : (value ? <Tag>{value}</Tag> : '—'),
    },
    {
      title: '定位器',
      width: 78,
      render: (_: unknown, row: ElementRow) => (row.isGroup ? null : `${(row.locators ?? []).length} 个`),
    },
    {
      title: '自愈',
      dataIndex: 'heal_count',
      width: 64,
      render: (v?: number, row?: ElementRow) => (row?.isGroup ? null : (v ?? 0) || '—'),
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      width: 72,
      render: (_: unknown, row: ElementRow) =>
        row.isGroup ? null : (
          <Tooltip title={row.enabled === false ? '无效:引用它的执行步骤会被拦截' : '有效'}>
            <Switch
              size="small"
              checked={row.enabled !== false}
              aria-label={`有效开关 ${row.key}`}
              onChange={(checked) => updateElement(row.key, { enabled: checked })}
            />
          </Tooltip>
        ),
    },
    {
      title: '操作',
      width: 120,
      render: (_: unknown, row: ElementRow) =>
        row.isGroup ? null : (
          <Space size={0}>
            {row.children?.length || parentAddOptions.some((o) => o.value === row.key) ? (
              <Tooltip title="在该节点下新增子元素">
                <Button
                  size="small"
                  type="text"
                  aria-label={`在 ${row.key} 下新增子元素`}
                  icon={<PlusOutlined />}
                  onClick={() => openAddElement(row.key)}
                />
              </Tooltip>
            ) : null}
            <Tooltip title="编辑名称、类型、说明与定位器">
              <Button
                size="small"
                type="text"
                aria-label={`编辑 ${row.key}`}
                icon={<EditOutlined />}
                onClick={() => setLocatorEditKey(row.key)}
              />
            </Tooltip>
            <Popconfirm
              title={row.children?.length ? `删除「${row.name}」及其 ${row.children.length} 个子节点?` : `删除「${row.name}」?`}
              description="将移除该元素及子树上的定位器与名称,需重新抽取或手工补回。"
              okText="删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              onConfirm={() => setElements((prev) => removeByKey(prev, row.key))}
            >
              <Button size="small" type="text" danger aria-label={`删除 ${row.key}`} icon={<DeleteOutlined />} />
            </Popconfirm>
          </Space>
        ),
    },
  ];

  const validationColumns: TableProps<Record<string, any>>['columns'] = [
    {
      title: '#',
      width: 64,
      render: (_: unknown, row: Record<string, any>) => (
        <span className="ui-auto-tree-seq">{validationSeqByKey.get(String(row.key)) ?? '—'}</span>
      ),
    },
    {
      title: '元素 / 分组',
      dataIndex: 'name',
      width: 180,
      render: (value: string, row: Record<string, any>) =>
        row.isGroup ? (
          <Space size={8}>
            <Tag color={groupTagColor(row.group)} style={{ marginInlineEnd: 0 }}>
              {groupLabel(row.group)}
            </Tag>
            <span style={{ color: 'var(--color-text-secondary)' }}>{row.count} 个元素</span>
          </Space>
        ) : (
          value
        ),
    },
    {
      title: '结果',
      dataIndex: 'matched',
      width: 100,
      render: (v: boolean, row: Record<string, any>) => (row.isGroup ? null : <Tag color={v ? 'success' : 'error'}>{v ? '可用' : '失效'}</Tag>),
    },
    {
      title: '定位器明细',
      render: (_: unknown, row: Record<string, any>) =>
        row.isGroup ? null : (
          <Space direction="vertical" size={2}>
            {(row.locators ?? []).map((loc: Record<string, any>, i: number) => (
              <span key={i} style={{ fontSize: 12 }}>
                <Tag color={loc.matched ? 'success' : 'error'}>{loc.strategy}</Tag>
                <code>{loc.value}</code> · 命中 {loc.count}
              </span>
            ))}
          </Space>
        ),
    },
  ];

  const runColumns: TableProps<UIAutomationRun>['columns'] = [
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (s: UIAutomationRun['status']) => <Tag color={RUN_STATUS_COLOR[s]}>{s}</Tag>,
    },
    { title: '类型', dataIndex: 'run_type', width: 90 },
    { title: '摘要', dataIndex: 'summary', ellipsis: true },
    { title: '时间', dataIndex: 'finished_at', width: 170, render: (v?: string) => formatTime(v) },
  ];

  /** 名称/类型/说明 + 定位器编辑面板(操作列抽屉统一入口;台账表只读) */
  const locatorEditor = (row: PageElement, opts?: { inDrawer?: boolean }) => (
    <div className={opts?.inDrawer ? 'ui-auto-locator-panel ui-auto-locator-panel--drawer' : 'ui-auto-locator-panel'}>
      <div className="ui-auto-locator-panel__section">
        <div className="ui-auto-locator-panel__label">名称</div>
        <Input
          size="small"
          value={row.name ?? ''}
          placeholder="元素业务名称"
          aria-label={`名称 ${row.key}`}
          onChange={(e) => updateElement(row.key, { name: e.target.value })}
        />
      </div>
      <div className="ui-auto-locator-panel__section">
        <div className="ui-auto-locator-panel__label">类型</div>
        <Select
          size="small"
          style={{ width: '100%' }}
          value={row.type || 'input'}
          options={
            row.type && !ELEMENT_TYPE_OPTIONS.some((o) => o.value === row.type)
              ? [{ value: row.type, label: row.type }, ...ELEMENT_TYPE_OPTIONS]
              : ELEMENT_TYPE_OPTIONS
          }
          aria-label={`类型 ${row.key}`}
          onChange={(value) => updateElement(row.key, { type: value })}
        />
      </div>
      <div className="ui-auto-locator-panel__section">
        <div className="ui-auto-locator-panel__label">说明</div>
        <Input
          size="small"
          value={row.description ?? ''}
          placeholder="元素用途说明,便于写用例时理解"
          aria-label={`说明 ${row.key}`}
          onChange={(e) => updateElement(row.key, { description: e.target.value })}
        />
      </div>
      <div className="ui-auto-locator-panel__section">
        <div className="ui-auto-locator-panel__label">定位器</div>
        <div className="ui-auto-locator-panel__list">
          {(row.locators ?? []).length === 0 ? (
            <span className="ui-auto-locator-panel__empty">暂无定位器,点下方添加</span>
          ) : null}
          {(row.locators ?? []).map((loc, locIndex) => (
            <div key={locIndex} className="ui-auto-locator-panel__row">
              <Select
                size="small"
                style={{ width: 120 }}
                value={loc.strategy}
                options={STRATEGY_OPTIONS}
                aria-label={`策略 ${loc.strategy}`}
                onChange={(value) => updateLocator(row.key, locIndex, { strategy: value })}
              />
              <Input
                size="small"
                className="ui-auto-locator-panel__value"
                value={loc.value}
                placeholder="选择器 / 表达式"
                aria-label={`定位器值 ${locIndex + 1}`}
                onChange={(e) => updateLocator(row.key, locIndex, { value: e.target.value })}
              />
              <Tooltip title={`稳定性评分 ${loc.stability ?? 0}`}>
                <Tag style={{ marginInlineEnd: 0 }}>稳 {loc.stability ?? 0}</Tag>
              </Tooltip>
              <Button
                size="small"
                danger
                type="text"
                aria-label={`删除定位器 ${locIndex + 1}`}
                onClick={() => removeLocator(row.key, locIndex)}
              >
                删除
              </Button>
            </div>
          ))}
          <Button size="small" icon={<PlusOutlined />} onClick={() => addLocator(row.key)}>
            添加定位器
          </Button>
        </div>
      </div>
    </div>
  );

  const elementsTab = (
    <div className="ui-auto-tab-body">
      <div className="ui-auto-toolbar">
        <Space wrap size={8}>
          <Button type="primary" icon={<SaveOutlined />} onClick={saveElements} disabled={!selected}>
            保存元素
          </Button>
          <Button
            type="default"
            icon={<PlusOutlined />}
            disabled={!selected}
            onClick={() => openAddElement()}
          >
            新增元素
          </Button>
          <Button
            danger
            type="default"
            icon={<DeleteOutlined />}
            disabled={!selected || totalElementCount === 0}
            onClick={clearElements}
          >
            清空元素
          </Button>
        </Space>
        <Space wrap size={6}>
          <Button
            size="small"
            type="text"
            icon={<DownOutlined />}
            disabled={!selected || expandableKeys.length === 0}
            onClick={() => setExpandedRowKeys(expandableKeys)}
          >
            全部展开
          </Button>
          <Button
            size="small"
            type="text"
            icon={<UpOutlined />}
            disabled={expandedRowKeys.length === 0}
            onClick={() => setExpandedRowKeys([])}
          >
            全部收起
          </Button>
          <span className="ui-auto-toolbar__meta">{totalElementCount} 个元素</span>
        </Space>
      </div>
      {task?.uncollected_count ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 8 }}
          message={`抽取完成,但有 ${task.uncollected_count} 个元素疑似未被采集`}
          description={
            <div style={{ fontSize: 12 }}>
              <div style={{ marginBottom: 4, color: 'var(--color-text-tertiary)' }}>
                下面这些元素在页面上可见、看着像控件,却没有被采集到。请对照界面核对,确认缺失就把这几行反馈出来:
              </div>
              {(task.uncollected ?? []).map((item, i) => (
                <div key={i}>
                  <code>{item.tag}</code> {item.class || item.name || '-'}
                  {item.text ? ` · ${item.text}` : ''}
                </div>
              ))}
            </div>
          }
        />
      ) : null}
      <Table
        className="ui-auto-elements-table"
        size="small"
        rowKey="key"
        dataSource={elementTreeRows as unknown as ElementRow[]}
        columns={elementColumns}
        pagination={false}
        scroll={{ x: 'max-content', y: 'max(240px, calc(100vh - 360px))' }}
        childrenColumnName="children"
        rowClassName={(row) => rowKindClass(row as ElementRow)}
        expandable={{
          expandedRowKeys,
          onExpandedRowsChange: handleExpandedRowsChange,
          indentSize: 20,
          // 只有分组/有 children 的父级可展开;叶子无展开箭头,编辑走操作列抽屉
          rowExpandable: (row: ElementRow) => !!row.isGroup || !!row.children?.length,
        }}
      />
      <Drawer
        title={
          locatorEditNode ? (
            <Space size={8}>
              <span>编辑「{locatorEditNode.name}」</span>
              <Tag>{locatorEditNode.key}</Tag>
              {locatorEditNode.children?.length ? (
                <Tag color="geekblue">{locatorEditNode.children.length} 子节点</Tag>
              ) : null}
            </Space>
          ) : (
            '编辑元素'
          )
        }
        open={!!locatorEditKey}
        width={480}
        onClose={() => setLocatorEditKey(null)}
        destroyOnClose
      >
        {locatorEditNode ? locatorEditor(locatorEditNode, { inDrawer: true }) : null}
      </Drawer>
    </div>
  );

  const validateTab = (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Alert
        type="info"
        showIcon
        message="需要登录的页面请先关联登录页对象;验证会复用其已保存的登录态。"
        description={
          selected?.login_page_object_id && activeLoginPage ? (
            <Space size={6}>
              <span>登录页对象:{activeLoginPage.name}</span>
              {renderSessionTag(activeLoginPage)}
            </Space>
          ) : (
            '当前页面对象未关联登录页对象:可在「编辑信息」里关联;未关联时按全局参数的登录配置登录,都没有则匿名访问。'
          )
        }
      />
      <Button icon={<ExperimentOutlined />} onClick={validateLocators} loading={validating} disabled={!selected}>
        验证定位器
      </Button>
      {validation ? (
        <Table
          className="ui-auto-elements-table"
          size="small"
          rowKey="key"
          dataSource={validationTreeRows}
          columns={validationColumns}
          pagination={false}
          scroll={{ x: 'max-content' }}
          childrenColumnName="children"
          rowClassName={(row) => rowKindClass(row as ElementRow)}
          expandable={{
            expandedRowKeys,
            onExpandedRowsChange: handleExpandedRowsChange,
            indentSize: 20,
          }}
        />
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未验证" />
      )}
    </div>
  );

  const exportTab = (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Space>
        <Button icon={<CodeOutlined />} onClick={loadCode} disabled={!selected}>
          生成代码
        </Button>
        <Button
          icon={<DownloadOutlined />}
          disabled={!code}
          onClick={() => {
            const blob = new Blob([code], { type: 'text/x-python;charset=utf-8' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${selected?.name || 'page_object'}.py`;
            a.click();
            URL.revokeObjectURL(url);
          }}
        >
          下载
        </Button>
      </Space>
      {code ? (
        <Input.TextArea value={code} readOnly autoSize={{ minRows: 12, maxRows: 24 }} style={{ fontFamily: 'monospace' }} />
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="点击生成代码" />
      )}
    </div>
  );

  const executeTab = (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Alert
        type="info"
        showIcon
        message="步骤执行前会自动确保登录态可用"
        description={
          activeLoginPage
            ? `已关联登录页对象「${activeLoginPage.name}」:有登录态直接复用,没有就先执行它的登录步骤;执行过程中的会话变化会保存回它,供其他页面对象复用。`
            : '当前页面对象未关联登录页对象:可在「编辑信息」里关联;未关联时按全局参数的登录配置登录,都没有则匿名访问。'
        }
      />
      <Space>
        <Button
          type="primary"
          icon={<PlayCircleOutlined />}
          loading={running}
          disabled={!selected || (steps.length === 0 && (selected.steps ?? []).length === 0)}
          onClick={runSteps}
        >
          执行步骤
        </Button>
        <Button
          icon={<PlusOutlined />}
          disabled={!selected}
          onClick={() =>
            setSteps((prev) => [
              ...prev,
              { element: elements[0]?.key ?? '', action: 'fill', value: '' },
            ])
          }
        >
          添加步骤
        </Button>
        <Button icon={<SaveOutlined />} disabled={!selected} onClick={saveSteps}>
          保存步骤到页面对象
        </Button>
        {selected?.is_login_page ? (
          <Tooltip title="登录页对象:保存后,其他页面对象关联它时就会自动执行这些步骤完成登录">
            <Tag color="blue">登录页对象</Tag>
          </Tooltip>
        ) : null}
      </Space>
      <Table
        size="small"
        rowKey={(_, index) => String(index)}
        dataSource={steps}
        pagination={false}
        locale={{ emptyText: '暂无步骤' }}
        columns={[
          {
            title: '元素',
            width: 220,
            render: (_: unknown, row: Record<string, any>, index: number) => (
              <Select
                style={{ width: '100%' }}
                value={row.element}
                options={elementSelectOptions}
                showSearch
                placeholder="搜索元素名称或 key"
                optionFilterProp="label"
                filterOption={(input, option) => {
                  const q = input.trim().toLowerCase();
                  if (!q) return true;
                  const opt = option as { label?: unknown; value?: unknown; options?: Array<{ label?: unknown; value?: unknown }> };
                  const label = String(opt?.label ?? '').toLowerCase();
                  const value = String(opt?.value ?? '').toLowerCase();
                  const nested = (opt?.options ?? [])
                    .map((o) => `${o.label ?? ''} ${o.value ?? ''}`)
                    .join(' ')
                    .toLowerCase();
                  return label.includes(q) || value.includes(q) || nested.includes(q);
                }}
                onChange={(value) =>
                  setSteps((prev) => prev.map((s, i) => (i === index ? { ...s, element: value } : s)))
                }
              />
            ),
          },
          {
            title: '动作',
            width: 150,
            render: (_: unknown, row: Record<string, any>, index: number) => (
              <Select
                style={{ width: '100%' }}
                value={row.action}
                options={ACTION_OPTIONS}
                onChange={(value) =>
                  setSteps((prev) => prev.map((s, i) => (i === index ? { ...s, action: value } : s)))
                }
              />
            ),
          },
          {
            title: '值',
            render: (_: unknown, row: Record<string, any>, index: number) => (
              <Input
                value={row.value}
                placeholder={VALUE_PLACEHOLDER[row.action]}
                onChange={(e) =>
                  setSteps((prev) => prev.map((s, i) => (i === index ? { ...s, value: e.target.value } : s)))
                }
              />
            ),
          },
          {
            title: '操作',
            width: 80,
            render: (_: unknown, __: Record<string, any>, index: number) => (
              <Button size="small" danger type="text" onClick={() => setSteps((prev) => prev.filter((_, i) => i !== index))}>
                删除
              </Button>
            ),
          },
        ]}
      />
      {running && task ? (
        <Progress percent={task.progress} status="active" />
      ) : null}
      {task && task.message && running ? (
        <div style={{ color: 'var(--color-text-tertiary)', fontSize: 12 }}>{task.message}</div>
      ) : null}
      {runResult ? (
        <Space direction="vertical" style={{ width: '100%' }}>
          <Alert
            type={runResult.status === 'PASSED' ? 'success' : runResult.status === 'FAILED' ? 'error' : 'warning'}
            showIcon
            message={`执行结果: ${runResult.status}`}
            description={`自愈 ${runResult.heal.length} 次`}
          />
          {runResult.heal.length > 0 ? (
            <Alert
              type="info"
              showIcon
              message="定位器自愈记录"
              description={
                <div>
                  {runResult.heal.map((event, i) => (
                    <div key={i} style={{ fontSize: 12 }}>
                      步骤 {event.step ?? '-'} · {event.element_name || event.element_key} · {event.reason}
                      {' → '}
                      <code>{event.to}</code>
                      {event.similarity != null ? ` (相似度 ${event.similarity})` : ''}
                    </div>
                  ))}
                </div>
              }
            />
          ) : null}
          {runResult.trace.map((step, i) => (
            <div key={i} style={{ fontSize: 12 }}>
              <Tag color={step.status === 'passed' ? 'success' : 'error'}>#{step.step_no}</Tag>
              {step.action} {step.element} {step.value ? `= ${step.value}` : ''}
              {step.error ? <span style={{ color: 'var(--color-danger)' }}> · {step.error}</span> : null}
            </div>
          ))}
        </Space>
      ) : null}
    </div>
  );

  const historyTab = (
    <Table
      size="small"
      rowKey="id"
      dataSource={runs}
      columns={runColumns}
      pagination={{ pageSize: 5, showSizeChanger: false }}
      locale={{ emptyText: '暂无运行记录' }}
    />
  );

  return (
    <div className="ui-auto-layout">
      <aside className="ui-auto-sidebar">
        <div className="ui-auto-sidebar-head">
          <span>页面对象</span>
          <Button size="small" icon={<ReloadOutlined />} onClick={() => loadPageObjects()} loading={loading} />
        </div>
        <Input.Search
          allowClear
          size="small"
          placeholder="搜索名称或 URL"
          value={sidebarKeyword}
          onChange={(e) => setSidebarKeyword(e.target.value)}
        />
        <Button type="primary" icon={<PlusOutlined />} block onClick={openCreateExtract}>
          抽取页面元素
        </Button>
        <List
          size="small"
          className="ui-auto-pageobj-list"
          dataSource={filteredPageObjects}
          pagination={{
            // 侧栏窄,用 simple 模式保证分页器单行不换行(仅箭头 + 页码指示)
            simple: true,
            current: sidebarPage,
            pageSize: 20,
            onChange: (page) => setSidebarPage(page),
            hideOnSinglePage: true,
          }}
          locale={{
            emptyText:
              pageObjects.length > 0 && filteredPageObjects.length === 0 ? '无匹配的页面对象' : '暂无页面对象',
          }}
          renderItem={(item) => (
            <List.Item
              className={item.id === selectedId ? 'ui-auto-list-item is-active' : 'ui-auto-list-item'}
              onClick={() => setSelectedId(item.id)}
              actions={[
                <Popconfirm
                  key="del"
                  title="删除该页面对象?"
                  onConfirm={(e) => {
                    e?.stopPropagation();
                    deletePageObject(item.id);
                  }}
                  onCancel={(e) => e?.stopPropagation()}
                >
                  <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={(e) => e.stopPropagation()} />
                </Popconfirm>,
              ]}
            >
              <List.Item.Meta
                title={item.name}
                description={`${flattenElementTree(item.elements).length} 个元素`}
              />
            </List.Item>
          )}
        />
      </aside>

      <section className="ui-auto-detail">
        {selected ? (
          <>
            <div className="ui-auto-detail-head">
              <div className="ui-auto-detail-head__main">
                <div className="ui-auto-detail-head__title-row">
                  <span className="ui-auto-detail-title">{selected.name}</span>
                  <span className="ui-auto-detail-head__actions">
                    <Button size="small" icon={<EditOutlined />} onClick={openEditPageObject}>
                      编辑信息
                    </Button>
                    <Button size="small" icon={<ReloadOutlined />} onClick={openRegenerate}>
                      重新生成
                    </Button>
                  </span>
                </div>
                <Tooltip title={resolveVars(selected.url)} placement="bottomLeft">
                  <span className="ui-auto-detail-url">{resolveVars(selected.url)}</span>
                </Tooltip>
                {selected.description ? (
                  <span className="ui-auto-detail-desc">{selected.description}</span>
                ) : null}
                <div className="ui-auto-detail-meta">
                  <span className="ui-auto-detail-meta__label">
                    登录页对象:{activeLoginPage ? activeLoginPage.name : '未关联'}
                  </span>
                  {renderSessionTag(activeLoginPage)}
                  {activeLoginPage ? (
                    <Popconfirm
                      title="清除该登录态?"
                      description="清除后下次抽取/执行会重新执行登录步骤建立。"
                      onConfirm={() => clearPageObjectLogin(activeLoginPage)}
                    >
                      <Button size="small" type="link" className="ui-auto-detail-meta__link">
                        清除登录态
                      </Button>
                    </Popconfirm>
                  ) : null}
                </div>
              </div>
            </div>
            <Tabs
              className="ui-auto-detail-tabs"
              activeKey={activeTab}
              onChange={setActiveTab}
              items={[
                { key: 'elements', label: '元素', children: elementsTab },
                { key: 'validate', label: '定位器验证', children: validateTab },
                { key: 'export', label: '代码导出', children: exportTab },
                { key: 'execute', label: '执行(自愈)', children: executeTab },
                { key: 'history', label: '运行历史', children: historyTab },
              ]}
            />
          </>
        ) : (
          <Empty description="请选择或抽取一个页面对象" style={{ marginTop: 80 }} />
        )}
      </section>

      <Modal
        title="新增元素"
        open={addElementOpen}
        onCancel={() => setAddElementOpen(false)}
        onOk={confirmAddElement}
        okText="新增"
        width={560}
        className="ui-auto-edit-modal"
      >
        <div className="ui-auto-edit-form">
          <label className="ui-auto-edit-form__field">
            <span className="ui-auto-edit-form__label">名称</span>
            <Input
              value={addElementForm.name}
              onChange={(e) => setAddElementForm({ ...addElementForm, name: e.target.value })}
              placeholder="如 提交按钮"
              maxLength={64}
              autoFocus
            />
          </label>
          <div className="ui-auto-edit-form__field">
            <span className="ui-auto-edit-form__label">类型</span>
            <Select
              style={{ width: '100%' }}
              value={addElementForm.type}
              aria-label="元素类型"
              options={ELEMENT_TYPE_OPTIONS}
              onChange={(value) => setAddElementForm({ ...addElementForm, type: value })}
            />
          </div>
          <div className="ui-auto-edit-form__field">
            <span className="ui-auto-edit-form__label">挂载位置</span>
            <Select
              style={{ width: '100%' }}
              value={addElementForm.parentKey}
              aria-label="新元素挂载位置"
              options={parentAddOptions}
              onChange={(value) => setAddElementForm({ ...addElementForm, parentKey: value })}
              showSearch
              optionFilterProp="label"
            />
            <div className="ui-auto-edit-form__hint">
              新元素会插到所选位置最上方;选「页面主体(顶层)」则作为根节点展示
            </div>
          </div>

          <div className="ui-auto-edit-form__divider" />

          <div className="ui-auto-edit-form__field">
            <span className="ui-auto-edit-form__label">
              定位器
              <span className="ui-auto-edit-form__optional">可选</span>
            </span>
            <div className="ui-auto-locator-panel ui-auto-locator-panel--modal">
              <div className="ui-auto-locator-panel__list">
                {addElementForm.locators.length === 0 ? (
                  <span className="ui-auto-locator-panel__empty">
                    暂无定位器,可稍后在表格里补,或点下方添加
                  </span>
                ) : null}
                {addElementForm.locators.map((loc, locIndex) => (
                  <div key={locIndex} className="ui-auto-locator-panel__row">
                    <Select
                      size="small"
                      style={{ width: 120 }}
                      value={loc.strategy}
                      options={STRATEGY_OPTIONS}
                      aria-label={`新增定位器策略 ${locIndex + 1}`}
                      onChange={(value) => updateAddLocator(locIndex, { strategy: value })}
                    />
                    <Input
                      size="small"
                      className="ui-auto-locator-panel__value"
                      value={loc.value}
                      placeholder="选择器 / 表达式"
                      aria-label={`新增定位器值 ${locIndex + 1}`}
                      onChange={(e) => updateAddLocator(locIndex, { value: e.target.value })}
                    />
                    <Tooltip title={`稳定性评分 ${loc.stability ?? 50}`}>
                      <Tag style={{ marginInlineEnd: 0 }}>稳 {loc.stability ?? 50}</Tag>
                    </Tooltip>
                    <Button
                      size="small"
                      danger
                      type="text"
                      aria-label={`移除新增定位器 ${locIndex + 1}`}
                      onClick={() => removeAddLocator(locIndex)}
                    >
                      删除
                    </Button>
                  </div>
                ))}
                <Button size="small" icon={<PlusOutlined />} onClick={addAddLocator}>
                  添加定位器
                </Button>
              </div>
            </div>
            <div className="ui-auto-edit-form__hint">
              仅保存值非空的定位器;新增后也可在表格操作列继续编辑
            </div>
          </div>

          <div className="ui-auto-edit-form__tips">
            新增后点「保存元素」落库
          </div>
        </div>
      </Modal>

      <Modal
        title={extractMode === 'regenerate' ? `重新生成页面对象 - ${selected?.name ?? ''}` : '抽取页面元素'}
        open={extractOpen}
        onCancel={() => setExtractOpen(false)}
        onOk={startExtract}
        okText={extractMode === 'regenerate' ? '重新生成' : '开始抽取'}
        confirmLoading={extracting}
        width={520}
        className="ui-auto-edit-modal"
      >
        <div className="ui-auto-edit-form">
          {extractMode === 'regenerate' ? (
            <div className="ui-auto-edit-form__target">
              <div className="ui-auto-edit-form__target-label">目标地址</div>
              <div className="ui-auto-edit-form__target-url">{resolveVars(selected?.url) || '-'}</div>
              <div className="ui-auto-edit-form__target-desc">
                将重新打开该页面抽取元素;勾选「合并」可保留已编辑的元素名称与自愈历史。
              </div>
            </div>
          ) : null}

          {extractMode === 'create' ? (
            <>
              <label className="ui-auto-edit-form__field">
                <span className="ui-auto-edit-form__label">
                  名称
                  <span className="ui-auto-edit-form__optional">可选</span>
                </span>
                <Input
                  value={extractForm.name}
                  onChange={(e) => setExtractForm({ ...extractForm, name: e.target.value })}
                  placeholder="如 LoginPage,留空自动命名"
                />
              </label>
              <label className="ui-auto-edit-form__field">
                <span className="ui-auto-edit-form__label">站点地址</span>
                <Input
                  value={extractForm.site_origin}
                  onChange={(e) => setExtractForm({ ...extractForm, site_origin: e.target.value })}
                  placeholder="http://192.168.13.178:8182 或 {{web_url}}"
                />
              </label>
              <label className="ui-auto-edit-form__field">
                <span className="ui-auto-edit-form__label">页面路径</span>
                <Input
                  value={extractForm.page_path}
                  onChange={(e) => setExtractForm({ ...extractForm, page_path: e.target.value })}
                  placeholder="#/login 或 /login,可留空"
                />
              </label>
              <div>
                <Button icon={<AppstoreOutlined />} onClick={openLubanModal}>
                  按菜单生成 URL(鲁班平台)
                </Button>
              </div>
            </>
          ) : null}

          <div className="ui-auto-edit-form__field">
            <span className="ui-auto-edit-form__label">登录页对象</span>
            <Select
              style={{ width: '100%' }}
              value={extractForm.login_page_object_id}
              onChange={(value) => setExtractForm({ ...extractForm, login_page_object_id: Number(value) })}
              options={extractLoginPageOptions}
              notFoundContent="暂无登录页对象,可在该页面对象的「编辑信息」里勾选「作为登录页对象」"
            />
            <div className="ui-auto-edit-form__session">
              {extractForm.login_page_object_id ? (
                <Space size={8} wrap>
                  {renderSessionTag(pageObjects.find((item) => item.id === extractForm.login_page_object_id))}
                  <span className="ui-auto-edit-form__hint">
                    有登录态直接复用;没有就先执行它的登录步骤,再抽取本页面
                  </span>
                </Space>
              ) : (
                <span className="ui-auto-edit-form__hint">
                  不选 = 本次不使用登录态:不读站点缓存、不触发登录,直接匿名访问
                </span>
              )}
            </div>
          </div>

          <div className="ui-auto-edit-form__divider" />

          <Checkbox
            className="ui-auto-edit-form__checkbox"
            checked={extractForm.use_llm_naming}
            onChange={(e) => setExtractForm({ ...extractForm, use_llm_naming: e.target.checked })}
          >
            <span className="ui-auto-edit-form__checkbox-title">使用 AI 语义命名元素</span>
            <span className="ui-auto-edit-form__checkbox-desc">失败时自动回退规则命名</span>
          </Checkbox>

          <div className="ui-auto-edit-form__field">
            <span className="ui-auto-edit-form__label">
              浮层深度
              <span className="ui-auto-edit-form__hint">0=不点触发按钮 · 默认 3 · 上限 5</span>
            </span>
            <Select
              style={{ width: '100%' }}
              value={extractForm.max_depth}
              onChange={(value) => setExtractForm({ ...extractForm, max_depth: value })}
              options={[0, 1, 2, 3, 4, 5].map((d) => ({
                value: d,
                label: d === 0 ? '0(仅当前页)' : `${d} 层`,
              }))}
            />
            <div className="ui-auto-edit-form__hint">
              触发按钮(如「新增」)下的弹窗元素会挂在其子节点;只跟浮层不跳路由
            </div>
          </div>

          {extractMode === 'regenerate' ? (
            <>
              <div className="ui-auto-edit-form__divider" />
              <Checkbox
                className="ui-auto-edit-form__checkbox"
                checked={mergeOnRegenerate}
                onChange={(e) => setMergeOnRegenerate(e.target.checked)}
              >
                <span className="ui-auto-edit-form__checkbox-title">合并到现有元素</span>
                <span className="ui-auto-edit-form__checkbox-desc">保留已编辑的名称/描述与自愈历史</span>
              </Checkbox>
            </>
          ) : null}

          {extracting && task ? <Progress percent={task.progress} status="active" /> : null}
          {extracting && task?.message ? (
            <span className="ui-auto-edit-form__hint">{task.message}</span>
          ) : null}
        </div>
      </Modal>

      <Modal
        title="按菜单生成 URL(鲁班平台)"
        open={lubanOpen}
        onCancel={() => setLubanOpen(false)}
        footer={null}
        width={520}
        className="ui-auto-edit-modal"
      >
        <div className="ui-auto-edit-form">
          <div className="ui-auto-edit-form__tips">
            URL 拼接规则:站点地址 + /?redirect=#/home + 模块 url + 目录 url + 菜单 url
          </div>

          <label className="ui-auto-edit-form__field">
            <span className="ui-auto-edit-form__label">access-token</span>
            <Input
              value={lubanToken}
              onChange={(e) => setLubanToken(e.target.value)}
              placeholder="鲁班平台 access-token,仅内存使用不落库"
            />
          </label>

          <label className="ui-auto-edit-form__field">
            <span className="ui-auto-edit-form__label">
              resId
              <span className="ui-auto-edit-form__optional">可选</span>
            </span>
            <Input
              value={lubanResId}
              onChange={(e) => setLubanResId(e.target.value)}
              placeholder="部分环境菜单接口需要"
            />
          </label>

          <div className="ui-auto-edit-form__field">
            <span className="ui-auto-edit-form__label">系统模块</span>
            <div className="ui-auto-edit-form__inline">
              <Select
                style={{ flex: 1, minWidth: 0 }}
                placeholder="选择系统模块(如 项目管理)"
                value={lubanModuleCode || undefined}
                onChange={loadLubanMenus}
                loading={lubanLoading === 'modules'}
                options={lubanModules.map((m) => ({ value: m.code, label: `${m.name} (${m.url})` }))}
                notFoundContent={lubanLoading === 'modules' ? '加载中...' : '无数据,请先加载模块'}
              />
              <Button onClick={loadLubanModules} loading={lubanLoading === 'modules'}>
                加载模块
              </Button>
            </div>
          </div>

          {lubanModuleCode ? (
            <div className="ui-auto-edit-form__field">
              <span className="ui-auto-edit-form__label">菜单页面</span>
              <TreeSelect
                style={{ width: '100%' }}
                placeholder="选择菜单页面(如 WBS模板)"
                value={lubanMenuId || undefined}
                treeData={lubanTreeData}
                treeDefaultExpandAll={false}
                showSearch
                treeNodeFilterProp="title"
                loading={lubanLoading === 'menus'}
                onChange={(value) => applyLubanMenu(String(value))}
                notFoundContent={lubanLoading === 'menus' ? '加载中...' : '无菜单数据'}
              />
            </div>
          ) : null}

          <div className="ui-auto-edit-form__divider" />

          <Checkbox
            className="ui-auto-edit-form__checkbox"
            checked={lubanAutoExtract}
            onChange={(e) => setLubanAutoExtract(e.target.checked)}
          >
            <span className="ui-auto-edit-form__checkbox-title">创建后自动排队抽取元素</span>
            <span className="ui-auto-edit-form__checkbox-desc">后端串行执行,每页约 1-2 分钟</span>
          </Checkbox>

          <Button
            block
            type="primary"
            ghost={false}
            className="ui-auto-edit-form__submit"
            onClick={runLubanScan}
            loading={lubanScanning}
          >
            全量扫描全部模块并批量创建页面对象
          </Button>

          {lubanScanning && lubanScanProgress ? (
            <div className="ui-auto-edit-form__field">
              <Progress
                percent={Math.round((lubanScanProgress.done / Math.max(lubanScanProgress.total, 1)) * 100)}
                size="small"
                status="active"
              />
              <span className="ui-auto-edit-form__hint">
                正在扫描模块 {lubanScanProgress.done}/{lubanScanProgress.total}...
              </span>
            </div>
          ) : null}
        </div>
      </Modal>

      <Modal
        title="编辑页面对象"
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={savePageObjectMeta}
        okText="保存"
        width={520}
        className="ui-auto-edit-modal"
      >
        <div className="ui-auto-edit-form">
          <label className="ui-auto-edit-form__field">
            <span className="ui-auto-edit-form__label">名称</span>
            <Input
              value={editForm.name}
              onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
              placeholder="页面对象名称"
            />
          </label>

          <label className="ui-auto-edit-form__field">
            <span className="ui-auto-edit-form__label">
              站点地址
              <span className="ui-auto-edit-form__hint">支持 {'{{变量}}'}(如 {'{{web_url}}'})</span>
            </span>
            <Input
              value={editForm.site_origin}
              onChange={(e) => setEditForm({ ...editForm, site_origin: e.target.value })}
              placeholder="http://host:port 或 {{web_url}}"
            />
          </label>

          <label className="ui-auto-edit-form__field">
            <span className="ui-auto-edit-form__label">页面路径</span>
            <Input
              value={editForm.page_path}
              onChange={(e) => setEditForm({ ...editForm, page_path: e.target.value })}
              placeholder="#/login 或 /login"
            />
          </label>

          <label className="ui-auto-edit-form__field">
            <span className="ui-auto-edit-form__label">
              页面说明
              <span className="ui-auto-edit-form__optional">可选</span>
            </span>
            <Input.TextArea
              value={editForm.description}
              onChange={(e) => setEditForm({ ...editForm, description: e.target.value })}
              placeholder="这个页面是做什么的,便于团队理解"
              autoSize={{ minRows: 2, maxRows: 4 }}
            />
          </label>

          <div className="ui-auto-edit-form__field">
            <span className="ui-auto-edit-form__label">登录页对象</span>
            <Select
              style={{ width: '100%' }}
              value={editForm.login_page_object_id}
              onChange={(value) => setEditForm({ ...editForm, login_page_object_id: Number(value) })}
              options={loginPageOptions}
              notFoundContent="暂无登录页对象"
            />
            <div className="ui-auto-edit-form__session">
              {renderSessionTag(
                pageObjects.find((item) => item.id === editForm.login_page_object_id) ?? null
              )}
            </div>
          </div>

          <div className="ui-auto-edit-form__divider" />

          <Checkbox
            className="ui-auto-edit-form__checkbox"
            checked={editForm.is_login_page}
            onChange={(e) => setEditForm({ ...editForm, is_login_page: e.target.checked })}
          >
            <span className="ui-auto-edit-form__checkbox-title">作为登录页对象</span>
            <span className="ui-auto-edit-form__checkbox-desc">
              在「执行(自愈)」里配置登录步骤,其他页面对象关联它即可复用登录态
            </span>
          </Checkbox>

          <div className="ui-auto-edit-form__tips">
            修改站点地址/页面路径会同步更新目标 URL,元素需点击「重新生成」刷新。执行/验证/抽取时按默认环境参数解析变量。
          </div>
        </div>
      </Modal>

    </div>
  );
};

export default UIAutomationPlatform;

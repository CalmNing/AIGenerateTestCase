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
  Table,
  TableProps,
  Tabs,
  Tag,
  Tooltip,
  TreeSelect,
  message,
} from 'antd';
import {
  AppstoreOutlined,
  CodeOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  ExperimentOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  SaveOutlined,
} from '@ant-design/icons';
import { PageElement, PageLocator, PageObject, UIAutomationRun, UIAutomationTaskStatus } from '../../types';
import { LubanMenuNode, LubanModule, globalParameterApi, uiAutomationApi } from '../../services/api';

const STRATEGY_OPTIONS = [
  'testid', 'role', 'label', 'aria-label', 'placeholder', 'name', 'id', 'text', 'css', 'xpath',
].map((value) => ({ value, label: value }));

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

interface ExtractFormState {
  name: string;
  site_origin: string;
  page_path: string;
  use_llm_naming: boolean;
  /** 关联的登录页对象(0=不关联):有登录态直接复用,没有就先执行它的登录步骤 */
  login_page_object_id: number;
}

const EMPTY_EXTRACT: ExtractFormState = {
  name: '',
  site_origin: '',
  page_path: '',
  use_llm_naming: true,
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
  // 默认环境参数:仅用于展示时解析 {{web_url}} 等占位符,落库仍存原文
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
        const env = list.find((e) => e.is_default) ?? list[0];
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
      const res = await uiAutomationApi.lubanModules({ base_url: base, access_token: token, res_id: lubanResId.trim() });
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
      const modRes = await uiAutomationApi.lubanModules({ base_url: base, access_token: token, res_id: lubanResId.trim() });
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
        login_page_object_id: extractForm.login_page_object_id || null,
        // 没选登录页对象 = 本次显式不使用登录态(匿名抽取,不读站点缓存、不登录)
        skip_login: !extractForm.login_page_object_id,
      };
      const res =
        extractMode === 'regenerate' && selected
          ? await uiAutomationApi.regenerate(selected.id, {
              use_llm_naming: extractForm.use_llm_naming,
              merge: mergeOnRegenerate,
              login_page_object_id: extractForm.login_page_object_id || null,
              // 没选登录页对象 = 本次显式不使用登录态(匿名抽取,不读站点缓存、不登录)
              skip_login: !extractForm.login_page_object_id,
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
    if (elements.length === 0) {
      message.info('当前没有元素可清空');
      return;
    }
    const count = elements.length;
    Modal.confirm({
      title: `清空「${selected.name}」的全部元素?`,
      content: (
        <div>
          将删除 {count} 个元素及其定位器、名称编辑与自愈记录,已配置的执行步骤同时清空。
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

  const runSteps = async () => {
    if (!selected || (steps.length === 0 && (selected.steps ?? []).length === 0)) {
      message.warning('请先添加执行步骤(或使用页面对象上已保存的步骤)');
      return;
    }
    setRunning(true);
    setRunResult(null);
    try {
      const res = await uiAutomationApi.run(selected.id, steps, {
        login_page_object_id: activeLoginPage?.id ?? selected.login_page_object_id ?? null,
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

  // 元素编辑统一按 key 定位(树形表格里 index 不再等于扁平数组下标)
  const updateElement = (key: string, patch: Partial<PageElement>) => {
    setElements((prev) => prev.map((item) => (item.key === key ? { ...item, ...patch } : item)));
  };

  const updateLocator = (elementKey: string, locatorIndex: number, patch: Partial<PageLocator>) => {
    setElements((prev) =>
      prev.map((item) => {
        if (item.key !== elementKey) return item;
        const locators = (item.locators ?? []).map((loc, j) => (j === locatorIndex ? { ...loc, ...patch } : loc));
        return { ...item, locators };
      })
    );
  };

  const addLocator = (elementKey: string) => {
    setElements((prev) =>
      prev.map((item) =>
        item.key === elementKey
          ? { ...item, locators: [...(item.locators ?? []), { strategy: 'css', value: '', stability: 50 }] }
          : item
      )
    );
  };

  const removeLocator = (elementKey: string, locatorIndex: number) => {
    setElements((prev) =>
      prev.map((item) =>
        item.key === elementKey ? { ...item, locators: (item.locators ?? []).filter((_, j) => j !== locatorIndex) } : item
      )
    );
  };

  // 分组:probeTrigger 相同的元素归入同一弹窗组,排序 = 页面主体 → 新增类 → 编辑类 → 删除类 → 其他
  const groupElements = useCallback((items: PageElement[]) => {
    const groups = new Map<string, PageElement[]>();
    items.forEach((el) => {
      const g = groupOf(el);
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g)!.push(el);
    });
    return [...groups.keys()]
      .sort((a, b) => groupSortIndex(a) - groupSortIndex(b))
      .map((g) => ({ group: g, items: groups.get(g) || [] }));
  }, []);

  const elementTreeRows = useMemo(
    () =>
      groupElements(elements).map(({ group, items }) => ({
        key: `__group__::${group}`,
        isGroup: true as const,
        group,
        count: items.length,
        children: items,
      })),
    [elements, groupElements],
  );

  const elementSelectOptions = useMemo(
    () =>
      groupElements(elements).map(({ group, items }) => ({
        label: `${groupLabel(group)} (${items.length})`,
        title: group,
        options: items.map((el) => ({ value: el.key, label: `${el.name} (${el.key})` })),
      })),
    [elements, groupElements],
  );

  const validationTreeRows = useMemo(() => {
    if (!validation) return [];
    const triggerByKey = new Map(elements.map((el) => [el.key, groupOf(el)]));
    const groups = new Map<string, Array<Record<string, any>>>();
    validation.forEach((row) => {
      const g = triggerByKey.get(row.key) || BASE_GROUP;
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g)!.push(row);
    });
    return [...groups.keys()]
      .sort((a, b) => groupSortIndex(a) - groupSortIndex(b))
      .map((g) => ({
        key: `__vgroup__::${g}`,
        isGroup: true as const,
        group: g,
        count: (groups.get(g) || []).length,
        children: groups.get(g) || [],
      }));
  }, [validation, elements]);

  type ElementRow = PageElement & { isGroup?: boolean; group?: string; count?: number };
  const elementColumns: TableProps<ElementRow>['columns'] = [
    {
      title: '名称 / 分组',
      dataIndex: 'name',
      render: (value: string, row: ElementRow) =>
        row.isGroup ? (
          <Space size={8}>
            <Tag color={groupTagColor(row.group!)} style={{ marginInlineEnd: 0 }}>
              {groupLabel(row.group!)}
            </Tag>
            <span style={{ color: 'var(--color-text-secondary)' }}>{row.count} 个元素</span>
          </Space>
        ) : (
          <Input value={value} onChange={(e) => updateElement(row.key, { name: e.target.value })} />
        ),
    },
    {
      title: 'key',
      dataIndex: 'key',
      width: 140,
      render: (v: string, row: ElementRow) => (row.isGroup ? null : <Tag>{v}</Tag>),
    },
    {
      title: '类型',
      dataIndex: 'type',
      width: 120,
      render: (value: string, row: ElementRow) =>
        row.isGroup ? null : (
          <Input value={value} onChange={(e) => updateElement(row.key, { type: e.target.value })} />
        ),
    },
    {
      title: '定位器',
      width: 90,
      render: (_: unknown, row: ElementRow) => (row.isGroup ? null : `${(row.locators ?? []).length} 个`),
    },
    {
      title: '自愈次数',
      dataIndex: 'heal_count',
      width: 90,
      render: (v?: number, row?: ElementRow) => (row?.isGroup ? null : v ?? 0),
    },
    {
      title: '操作',
      width: 80,
      render: (_: unknown, row: ElementRow) =>
        row.isGroup ? null : (
          <Button
            size="small"
            danger
            type="text"
            onClick={() => setElements((prev) => prev.filter((item) => item.key !== row.key))}
          >
            删除
          </Button>
        ),
    },
  ];

  const validationColumns: TableProps<Record<string, any>>['columns'] = [
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

  const elementsTab = (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Space>
        <Button type="primary" icon={<SaveOutlined />} onClick={saveElements} disabled={!selected}>
          保存元素
        </Button>
        <Button
          icon={<PlusOutlined />}
          disabled={!selected}
          onClick={() =>
            setElements((prev) => {
              // 清空/删除后 prev.length 会与已有 key 撞车(如残留 element_2),按占用情况取号
              const used = new Set(prev.map((item) => item.key));
              let index = prev.length + 1;
              while (used.has(`element_${index}`)) index += 1;
              return [...prev, { key: `element_${index}`, name: '新元素', type: 'input', locators: [] }];
            })
          }
        >
          新增元素
        </Button>
        <Button
          danger
          icon={<DeleteOutlined />}
          disabled={!selected || elements.length === 0}
          onClick={clearElements}
        >
          清空元素
        </Button>
      </Space>
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
        size="small"
        rowKey="key"
        dataSource={elementTreeRows as unknown as ElementRow[]}
        columns={elementColumns}
        pagination={false}
        locale={{ emptyText: '暂无元素,请先抽取' }}
        childrenColumnName="children"
        expandable={{
          expandedRowRender: (row: ElementRow) =>
            row.isGroup ? null : (
            <Space direction="vertical" style={{ width: '100%' }} size={6}>
              <Space align="baseline">
                <span style={{ width: 56, display: 'inline-block', color: 'var(--color-text-tertiary)' }}>说明</span>
                <Input
                  style={{ width: 452, maxWidth: '100%' }}
                  value={row.description ?? ''}
                  placeholder="元素用途说明"
                  onChange={(e) => updateElement(row.key, { description: e.target.value })}
                />
              </Space>
              {(row.locators ?? []).map((loc, locIndex) => (
                <Space key={locIndex} align="baseline">
                  <Select
                    style={{ width: 130 }}
                    value={loc.strategy}
                    options={STRATEGY_OPTIONS}
                    onChange={(value) => updateLocator(row.key, locIndex, { strategy: value })}
                  />
                  <Input
                    style={{ width: 320 }}
                    value={loc.value}
                    onChange={(e) => updateLocator(row.key, locIndex, { value: e.target.value })}
                  />
                  <Tag>稳定性 {loc.stability}</Tag>
                  <Button size="small" danger type="text" onClick={() => removeLocator(row.key, locIndex)}>
                    删除
                  </Button>
                </Space>
              ))}
              <Button size="small" icon={<PlusOutlined />} onClick={() => addLocator(row.key)}>
                添加定位器
              </Button>
            </Space>
            )
        }}
      />
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
          size="small"
          rowKey="key"
          dataSource={validationTreeRows}
          columns={validationColumns}
          pagination={false}
          childrenColumnName="children"
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
              <List.Item.Meta title={item.name} description={`${(item.elements ?? []).length} 个元素`} />
            </List.Item>
          )}
        />
      </aside>

      <section className="ui-auto-detail">
        {selected ? (
          <>
            <div className="ui-auto-detail-head">
              <Space direction="vertical" size={2} style={{ flex: 1 }}>
                <Space>
                  <span style={{ fontWeight: 600, fontSize: 16 }}>{selected.name}</span>
                  <Button size="small" icon={<EditOutlined />} onClick={openEditPageObject}>
                    编辑信息
                  </Button>
                  <Button size="small" icon={<ReloadOutlined />} onClick={openRegenerate}>
                    重新生成
                  </Button>
                </Space>
                <Tooltip title={resolveVars(selected.url)} placement="bottomLeft">
                  <span className="ui-auto-detail-url">{resolveVars(selected.url)}</span>
                </Tooltip>
                {selected.description ? (
                  <span style={{ fontSize: 12, color: 'var(--color-text-tertiary)' }}>{selected.description}</span>
                ) : null}
                <Space size={6} wrap>
                  <span style={{ fontSize: 12, color: 'var(--color-text-tertiary)' }}>
                    登录页对象:{activeLoginPage ? activeLoginPage.name : '未关联'}
                  </span>
                  {renderSessionTag(activeLoginPage)}
                  {activeLoginPage ? (
                    <Popconfirm
                      title="清除该登录态?"
                      description="清除后下次抽取/执行会重新执行登录步骤建立。"
                      onConfirm={() => clearPageObjectLogin(activeLoginPage)}
                    >
                      <Button size="small" type="link" style={{ padding: 0 }}>
                        清除登录态
                      </Button>
                    </Popconfirm>
                  ) : null}
                </Space>
              </Space>
              <Tag>{elements.length} 个元素</Tag>
            </div>
            <Tabs
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
        title={extractMode === 'regenerate' ? `重新生成页面对象 - ${selected?.name ?? ''}` : '抽取页面元素'}
        open={extractOpen}
        onCancel={() => setExtractOpen(false)}
        onOk={startExtract}
        okText={extractMode === 'regenerate' ? '重新生成' : '开始抽取'}
        confirmLoading={extracting}
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          {extractMode === 'regenerate' ? (
            <Alert
              type="info"
              showIcon
              message={`目标地址: ${resolveVars(selected?.url) || '-'}`}
              description="将重新打开该页面抽取元素;勾选「合并」可保留已编辑的元素名称与自愈历史。"
            />
          ) : null}
          {extractMode === 'create' ? (
            <>
              <Input
                addonBefore="名称"
                value={extractForm.name}
                onChange={(e) => setExtractForm({ ...extractForm, name: e.target.value })}
                placeholder="如 LoginPage,留空自动命名"
              />
              <Input
                addonBefore="站点地址"
                value={extractForm.site_origin}
                onChange={(e) => setExtractForm({ ...extractForm, site_origin: e.target.value })}
                placeholder="http://192.168.13.178:8182 或 {{web_url}}"
              />
              <Input
                addonBefore="页面路径"
                value={extractForm.page_path}
                onChange={(e) => setExtractForm({ ...extractForm, page_path: e.target.value })}
                placeholder="#/login 或 /login,可留空"
              />
              <Button icon={<AppstoreOutlined />} onClick={openLubanModal}>
                按菜单生成 URL(鲁班平台)
              </Button>
            </>
          ) : null}
          <Space.Compact style={{ width: '100%' }}>
            <span className="ui-auto-addon">登录页对象</span>
            <Select
              style={{ width: '100%' }}
              value={extractForm.login_page_object_id}
              onChange={(value) => setExtractForm({ ...extractForm, login_page_object_id: Number(value) })}
              options={extractLoginPageOptions}
              notFoundContent="暂无登录页对象,可在该页面对象的「编辑信息」里勾选「作为登录页对象」"
            />
          </Space.Compact>
          {extractForm.login_page_object_id ? (
            <Space size={8} wrap>
              {renderSessionTag(pageObjects.find((item) => item.id === extractForm.login_page_object_id))}
              <span style={{ fontSize: 12, color: 'var(--color-text-tertiary)' }}>
                有登录态直接复用;没有就先执行它的登录步骤,再抽取本页面
              </span>
            </Space>
          ) : (
            <span style={{ fontSize: 12, color: 'var(--color-text-tertiary)' }}>
              不选登录页对象 = 本次不使用登录态:不读取该站点已缓存的登录态,也不触发登录,直接匿名访问
            </span>
          )}
          <Checkbox
            checked={extractForm.use_llm_naming}
            onChange={(e) => setExtractForm({ ...extractForm, use_llm_naming: e.target.checked })}
          >
            使用 AI 语义命名元素(失败自动回退规则命名)
          </Checkbox>
          {extractMode === 'regenerate' ? (
            <Checkbox checked={mergeOnRegenerate} onChange={(e) => setMergeOnRegenerate(e.target.checked)}>
              合并到现有元素(保留已编辑的名称/描述与自愈历史)
            </Checkbox>
          ) : null}
          {extracting && task ? <Progress percent={task.progress} status="active" /> : null}
          {extracting && task?.message ? (
            <span style={{ fontSize: 12, color: 'var(--color-text-tertiary)' }}>{task.message}</span>
          ) : null}
        </Space>
      </Modal>

      <Modal
        title="按菜单生成 URL(鲁班平台)"
        open={lubanOpen}
        onCancel={() => setLubanOpen(false)}
        footer={null}
        width={520}
      >
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <Alert
            type="info"
            showIcon
            message="URL 拼接规则: 站点地址 + /?redirect=#/home + 模块 url + 目录 url + 菜单 url"
          />
          <Input
            addonBefore="access-token"
            value={lubanToken}
            onChange={(e) => setLubanToken(e.target.value)}
            placeholder="鲁班平台 access-token,仅内存使用不落库"
          />
          <Input
            addonBefore="resId"
            value={lubanResId}
            onChange={(e) => setLubanResId(e.target.value)}
            placeholder="可选,部分环境菜单接口需要"
          />
          <Space.Compact style={{ width: '100%' }}>
            <Select
              style={{ width: '100%' }}
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
          </Space.Compact>
          {lubanModuleCode ? (
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
          ) : null}
          <Checkbox checked={lubanAutoExtract} onChange={(e) => setLubanAutoExtract(e.target.checked)}>
            创建后自动排队抽取元素(后端串行执行,每页约 1-2 分钟)
          </Checkbox>
          <Button block onClick={runLubanScan} loading={lubanScanning}>
            全量扫描全部模块并批量创建页面对象
          </Button>
          {lubanScanning && lubanScanProgress ? (
            <>
              <Progress
                percent={Math.round((lubanScanProgress.done / Math.max(lubanScanProgress.total, 1)) * 100)}
                size="small"
                status="active"
              />
              <span style={{ fontSize: 12, color: 'var(--color-text-tertiary)' }}>
                正在扫描模块 {lubanScanProgress.done}/{lubanScanProgress.total}...
              </span>
            </>
          ) : null}
        </Space>
      </Modal>

      <Modal
        title="编辑页面对象"
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={savePageObjectMeta}
        okText="保存"
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <Input
            addonBefore="名称"
            value={editForm.name}
            onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
            placeholder="页面对象名称"
          />
          <Input
            addonBefore="站点地址"
            value={editForm.site_origin}
            onChange={(e) => setEditForm({ ...editForm, site_origin: e.target.value })}
            placeholder="http://host:port 或 {{web_url}}"
          />
          <Input
            addonBefore="页面路径"
            value={editForm.page_path}
            onChange={(e) => setEditForm({ ...editForm, page_path: e.target.value })}
            placeholder="#/login 或 /login"
          />
          <Input.TextArea
            value={editForm.description}
            onChange={(e) => setEditForm({ ...editForm, description: e.target.value })}
            placeholder="页面说明(可选)"
            autoSize={{ minRows: 2, maxRows: 4 }}
          />
          <div>
            <Space size={8}>
              <span style={{ fontSize: 14 }}>登录页对象</span>
              <Select
                style={{ width: 280 }}
                value={editForm.login_page_object_id}
                onChange={(value) => setEditForm({ ...editForm, login_page_object_id: Number(value) })}
                options={loginPageOptions}
                notFoundContent="暂无登录页对象"
              />
            </Space>
            <div style={{ marginTop: 4 }}>
              {renderSessionTag(
                pageObjects.find((item) => item.id === editForm.login_page_object_id) ?? null
              )}
            </div>
          </div>
          <Checkbox
            checked={editForm.is_login_page}
            onChange={(e) => setEditForm({ ...editForm, is_login_page: e.target.checked })}
          >
            作为登录页对象(登录 = 在「执行(自愈)」里配置登录步骤,其他页面对象关联它即可复用登录态)
          </Checkbox>
          <span style={{ fontSize: 12, color: 'var(--color-text-tertiary)' }}>
            修改站点地址/页面路径会同步更新目标 URL,元素需点击「重新生成」刷新。
            站点地址支持 {'{{变量}}'}(如 {'{{web_url}}'}),执行/验证/抽取时按默认环境参数解析。
          </span>
        </Space>
      </Modal>

    </div>
  );
};

export default UIAutomationPlatform;

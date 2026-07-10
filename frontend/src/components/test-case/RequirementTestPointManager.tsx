import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  Button, Tag, Modal, Input, InputNumber, Select, Space, Spin, Empty, Pagination,
  message as antMessage, Tooltip, Popconfirm,
} from 'antd';
import {
  PlusOutlined, ReloadOutlined, EditOutlined, DeleteOutlined,
  ApartmentOutlined, NodeIndexOutlined, FileTextOutlined, ApiOutlined,
} from '@ant-design/icons';
import { testcaseApi } from '../../services/api';
import { TestCase, Module } from '../../types';
import './RequirementTestPointManager.css';

interface ReqPoint {
  id: number;
  content: string;
  module_id?: number | null;
  module: string;
  priority: string;
  order_index: number;
  test_point_count: number;
  created_at: string;
  updated_at?: string | null;
  api_project_id?: number | null;
  api_endpoint_ids?: string | null;
}

interface TPoint {
  id: number;
  requirement_point_id: number;
  content: string;
  test_type: string;
  design_method: string;
  priority: string;
  case_count: number;
  created_at: string;
  updated_at?: string | null;
  api_project_id?: number | null;
  api_endpoint_ids?: string | null;
}

type Draft = {
  kind: 'req' | 'tp';
  mode: 'add' | 'edit';
  rpId?: number;
  id?: number;
  content: string;
  module_id?: number | null;
  priority: string;
  test_type: string;
  design_method: string;
  api_project_id: number | null;
  api_endpoint_ids: string | null;
} | null;

const PRIORITY_COLOR: Record<string, string> = {
  high: 'red',
  medium: 'gold',
  low: 'green',
};

const PRIORITY_LABEL: Record<string, string> = {
  high: '高',
  medium: '中',
  low: '低',
};

const TEST_TYPE_COLOR: Record<string, string> = {
  functional: 'blue',
  performance: 'orange',
  security: 'red',
  compatibility: 'green',
};

const TEST_TYPE_LABEL: Record<string, string> = {
  functional: '功能',
  performance: '性能',
  security: '安全',
  compatibility: '兼容',
};

const DESIGN_METHOD_LABEL: Record<string, string> = {
  scenario: '场景法',
  equivalence_partitioning: '等价类划分',
  boundary_value: '边界值分析',
  error_guessing: '错误推测法',
  decision_table: '判定表法',
  orthogonal: '正交试验法',
  state_transition: '状态迁移法',
  causality: '因果图法',
};

const REQ_PAGE_SIZE = 20;
const TP_PAGE_SIZE = 20;
const CASE_PAGE_SIZE = 20;

const priorityTag = (p: string) => (
  <Tag color={PRIORITY_COLOR[p] || 'default'}>{PRIORITY_LABEL[p] || p || '中'}</Tag>
);

/** 将 ISO 时间字符串格式化为 YYYY-MM-DD HH:mm */
const formatTime = (iso?: string | null): string => {
  if (!iso) return '-';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

/** 卡片底部的时间信息（创建 + 更新；更新与创建相同或缺失时只显示创建） */
const timeFooter = (created?: string | null, updated?: string | null) => {
  const createdStr = formatTime(created);
  const updatedStr = formatTime(updated);
  if (updated && updated !== created) {
    return (
      <div className="rtp-card__time">
        <span title={created ?? ''}>创建：{createdStr}</span>
        <span title={updated ?? ''}>更新：{updatedStr}</span>
      </div>
    );
  }
  return <div className="rtp-card__time"><span title={created ?? ''}>创建：{createdStr}</span></div>;
};

const RequirementTestPointManager: React.FC<{
  sessionId?: number | null;
  modules?: Module[];
  selectedModule?: number | string;
}> = ({ sessionId, modules = [], selectedModule }) => {
  const [reqPoints, setReqPoints] = useState<ReqPoint[]>([]);
  const [reqTotal, setReqTotal] = useState(0);
  const [reqPage, setReqPage] = useState(1);
  const [reqPageSize, setReqPageSize] = useState(REQ_PAGE_SIZE);
  // 过滤使用的模块 id；初始值取父级选中的模块（0 视为全部）
  const [filterModule, setFilterModule] = useState<number | undefined>(
    selectedModule && Number(selectedModule) > 0 ? Number(selectedModule) : undefined,
  );

  const [testPoints, setTestPoints] = useState<TPoint[]>([]);
  const [tpTotal, setTpTotal] = useState(0);
  const [tpPage, setTpPage] = useState(1);
  const [tpPageSize, setTpPageSize] = useState(TP_PAGE_SIZE);

  const [cases, setCases] = useState<TestCase[]>([]);
  const [caseTotal, setCaseTotal] = useState(0);
  const [casePage, setCasePage] = useState(1);
  const [casePageSize, setCasePageSize] = useState(CASE_PAGE_SIZE);

  const [selectedReqId, setSelectedReqId] = useState<number | null>(null);
  const [selectedTpId, setSelectedTpId] = useState<number | null>(null);

  const [loading, setLoading] = useState(false);
  const [tpLoading, setTpLoading] = useState(false);
  const [caseLoading, setCaseLoading] = useState(false);
  const [polling, setPolling] = useState(false);
  const [draft, setDraft] = useState<Draft>(null);
  const [saving, setSaving] = useState(false);

  // 用 ref 保存最新选中态，避免重生成/轮询回调闭包里的陈旧值
  const selectedReqIdRef = useRef<number | null>(null);
  const selectedTpIdRef = useRef<number | null>(null);
  useEffect(() => { selectedReqIdRef.current = selectedReqId; }, [selectedReqId]);
  useEffect(() => { selectedTpIdRef.current = selectedTpId; }, [selectedTpId]);

  // 模块 id -> 名称 映射
  const moduleMap = useMemo(() => {
    const m = new Map<number, string>();
    modules.forEach(mod => { if (mod.id != null) m.set(mod.id, mod.module_name); });
    return m;
  }, [modules]);

  // 模块下拉选项（按层级缩进）
  const moduleOptions = useMemo(() => {
    const byId = new Map(modules.map(mod => [mod.id, mod]));
    const depthOf = (mod: Module): number => {
      let d = 0;
      let cur: Module | undefined = mod;
      while (cur && cur.parent_id != null && byId.has(cur.parent_id)) {
        d += 1;
        cur = byId.get(cur.parent_id);
        if (d > 10) break;
      }
      return d;
    };
    return modules
      .filter(mod => mod.id != null)
      .map(mod => ({
        value: mod.id as number,
        label: `${'　'.repeat(depthOf(mod))}${mod.module_name}`,
      }));
  }, [modules]);

  // 用例列：按选中测试点分页拉取
  const loadCases = useCallback(async (tpId: number | null, page = 1, pageSize = CASE_PAGE_SIZE) => {
    if (!sessionId || tpId == null) {
      setCases([]);
      setCaseTotal(0);
      return;
    }
    setCaseLoading(true);
    try {
      const res = await testcaseApi.getTestcases(
        sessionId,
        { test_point_id: tpId },
        { offset: (page - 1) * pageSize, limit: pageSize },
      );
      if (res.code === 200 && res.data) {
        setCases(res.data.items || []);
        setCaseTotal(res.data.totalNumber || 0);
        setCasePage(page);
        setCasePageSize(pageSize);
      }
    } catch (e) {
      console.error('Load cases failed:', e);
    } finally {
      setCaseLoading(false);
    }
  }, [sessionId]);

  // 测试点列：按选中需求点分页拉取（点击需求点才加载）
  const loadTestPoints = useCallback(async (
    reqId: number | null,
    page = 1,
    pageSize = TP_PAGE_SIZE,
    autoSelectFirstTp = false,
  ) => {
    if (!sessionId || reqId == null) {
      setTestPoints([]);
      setTpTotal(0);
      setSelectedTpId(null);
      setCases([]);
      setCaseTotal(0);
      return;
    }
    setTpLoading(true);
    try {
      const res = await testcaseApi.getTestPoints(sessionId, {
        requirement_point_id: reqId,
        offset: (page - 1) * pageSize,
        limit: pageSize,
      });
      const items: TPoint[] = (res.code === 200 && res.data?.items) ? res.data.items : [];
      setTestPoints(items);
      setTpTotal(res.data?.totalNumber || 0);
      setTpPage(page);
      setTpPageSize(pageSize);

      if (autoSelectFirstTp && items.length > 0) {
        const first = items[0];
        setSelectedTpId(first.id);
        await loadCases(first.id, 1, CASE_PAGE_SIZE);
      } else if (items.length === 0) {
        setSelectedTpId(null);
        setCases([]);
        setCaseTotal(0);
      } else if (selectedTpIdRef.current != null) {
        // 保持已选中的测试点，刷新其用例
        await loadCases(selectedTpIdRef.current, 1, CASE_PAGE_SIZE);
      }
    } catch (e) {
      console.error('Load test points failed:', e);
    } finally {
      setTpLoading(false);
    }
  }, [sessionId, loadCases]);

  // 需求点列：分页拉取，首次进入自动选中第一个
  // moduleId 显式传入，避免依赖 filterModule 闭包导致的陈旧值
  const loadReqPoints = useCallback(async (
    page = 1,
    pageSize = REQ_PAGE_SIZE,
    autoSelectFirst = false,
    moduleId: number | undefined = filterModule,
  ) => {
    if (!sessionId) {
      setReqPoints([]);
      setReqTotal(0);
      setSelectedReqId(null);
      return;
    }
    setLoading(true);
    try {
      const res = await testcaseApi.getRequirementPoints(sessionId, {
        offset: (page - 1) * pageSize,
        limit: pageSize,
        module_id: moduleId,
      });
      const items: ReqPoint[] = (res.code === 200 && res.data?.items) ? res.data.items : [];
      setReqPoints(items);
      setReqTotal(res.data?.totalNumber || 0);
      setReqPage(page);
      setReqPageSize(pageSize);

      if (autoSelectFirst && items.length > 0) {
        const first = items[0];
        setSelectedReqId(first.id);
        await loadTestPoints(first.id, 1, TP_PAGE_SIZE, true);
      }
    } catch (e) {
      console.error('Load requirement points failed:', e);
      antMessage.error('加载需求点失败');
    } finally {
      setLoading(false);
    }
  }, [sessionId, loadTestPoints, filterModule]);

  // 进入会话：加载需求点第 1 页并自动选中第一个
  useEffect(() => {
    if (!sessionId) {
      setReqPoints([]);
      setReqTotal(0);
      setTestPoints([]);
      setTpTotal(0);
      setCases([]);
      setCaseTotal(0);
      setSelectedReqId(null);
      setSelectedTpId(null);
      return;
    }
    loadReqPoints(1, REQ_PAGE_SIZE, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // 父级全局选中的模块变化时，同步到用例结构过滤条件并刷新
  const selectedModuleRef = useRef<number | string | undefined>(selectedModule);
  useEffect(() => {
    if (selectedModuleRef.current === selectedModule) return;
    selectedModuleRef.current = selectedModule;
    const modId = selectedModule && Number(selectedModule) > 0 ? Number(selectedModule) : undefined;
    // 切换模块时清空下游选中态，让结构完整刷新
    setSelectedReqId(null);
    setSelectedTpId(null);
    setTestPoints([]);
    setTpTotal(0);
    setCases([]);
    setCaseTotal(0);
    setFilterModule(modId);
    // 用最新的 modId 直接触发重新拉取，避免依赖 filterModule 闭包导致的陈旧值
    if (sessionId) loadReqPoints(1, reqPageSize, false, modId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedModule, sessionId, loadReqPoints, reqPageSize]);

  // 重生成/增删改后刷新当前视图（回到选中需求点的测试点第 1 页、选中测试点的用例第 1 页）
  const refreshAll = useCallback(async () => {
    await loadReqPoints(reqPage, reqPageSize, false);
    const rid = selectedReqIdRef.current;
    if (rid != null) {
      await loadTestPoints(rid, 1, TP_PAGE_SIZE, false);
      const tid = selectedTpIdRef.current;
      if (tid != null) await loadCases(tid, 1, CASE_PAGE_SIZE);
    }
  }, [loadReqPoints, loadTestPoints, loadCases, reqPage, reqPageSize]);

  const selectReq = (id: number) => {
    setSelectedReqId(id);
    loadTestPoints(id, 1, TP_PAGE_SIZE, true);
  };

  const selectTp = (id: number) => {
    setSelectedTpId(id);
    loadCases(id, 1, CASE_PAGE_SIZE);
  };

  const startPolling = useCallback((taskId: string, onDone: () => void) => {
    setPolling(true);
    const timer = setInterval(async () => {
      try {
        const res = await testcaseApi.getPipelineStatus(taskId);
        if (res.code === 200 && res.data) {
          if (res.data.status === 'completed') {
            clearInterval(timer);
            setPolling(false);
            antMessage.success('重生成完成！');
            onDone();
          } else if (res.data.status === 'failed') {
            clearInterval(timer);
            setPolling(false);
            antMessage.error('重生成失败：' + (res.data.error || res.data.message));
          }
        }
      } catch (e) {
        console.error('Poll status failed:', e);
      }
    }, 2000);
  }, []);

  const handleRegenReq = async (rpId: number) => {
    try {
      const res = await testcaseApi.regenerateRequirementPoint(rpId);
      if (res.code === 200 && res.data?.task_id) {
        antMessage.info('需求点重生成已启动，请稍候...');
        startPolling(res.data.task_id, refreshAll);
      } else {
        antMessage.error(res.message || '启动失败');
      }
    } catch (e: any) {
      antMessage.error('启动重生成失败：' + (e?.message || '未知错误'));
    }
  };

  const handleRegenTp = async (tpId: number) => {
    try {
      const res = await testcaseApi.regenerateTestPoint(tpId);
      if (res.code === 200 && res.data?.task_id) {
        antMessage.info('测试点用例重生成已启动，请稍候...');
        startPolling(res.data.task_id, refreshAll);
      } else {
        antMessage.error(res.message || '启动失败');
      }
    } catch (e: any) {
      antMessage.error('启动重生成失败：' + (e?.message || '未知错误'));
    }
  };

  const handleDeleteReq = async (rpId: number) => {
    try {
      const res = await testcaseApi.deleteRequirementPoint(rpId);
      if (res.code === 200) {
        antMessage.success('需求点已删除');
        if (selectedReqId === rpId) {
          setSelectedReqId(null);
          setSelectedTpId(null);
          setTestPoints([]);
          setTpTotal(0);
          setCases([]);
          setCaseTotal(0);
        }
        loadReqPoints(1, REQ_PAGE_SIZE, false);
      } else {
        antMessage.error(res.message || '删除失败');
      }
    } catch (e: any) {
      antMessage.error('删除失败：' + (e?.message || '未知错误'));
    }
  };

  const handleDeleteTp = async (tpId: number) => {
    try {
      const res = await testcaseApi.deleteTestPoint(tpId);
      if (res.code === 200) {
        antMessage.success('测试点已删除');
        if (selectedTpId === tpId) {
          setSelectedTpId(null);
          setCases([]);
          setCaseTotal(0);
        }
        if (selectedReqId != null) loadTestPoints(selectedReqId, 1, TP_PAGE_SIZE, false);
      } else {
        antMessage.error(res.message || '删除失败');
      }
    } catch (e: any) {
      antMessage.error('删除失败：' + (e?.message || '未知错误'));
    }
  };

  const openAddReq = () => {
    const defaultModule = selectedModule !== undefined && Number(selectedModule) > 0 ? Number(selectedModule) : undefined;
    setDraft({ kind: 'req', mode: 'add', content: '', module_id: defaultModule, priority: 'medium', test_type: 'functional', design_method: '', api_project_id: null, api_endpoint_ids: null });
  };

  const openAddTp = (rpId: number) =>
    setDraft({ kind: 'tp', mode: 'add', rpId, content: '', module_id: undefined, priority: 'medium', test_type: 'functional', design_method: '', api_project_id: null, api_endpoint_ids: null });

  const openEditReq = (rp: ReqPoint) =>
    setDraft({ kind: 'req', mode: 'edit', id: rp.id, content: rp.content, module_id: rp.module_id ?? undefined, priority: rp.priority, test_type: 'functional', design_method: '', api_project_id: rp.api_project_id ?? null, api_endpoint_ids: rp.api_endpoint_ids ?? null });

  const openEditTp = (tp: TPoint) =>
    setDraft({ kind: 'tp', mode: 'edit', id: tp.id, content: tp.content, module_id: undefined, priority: tp.priority, test_type: tp.test_type, design_method: tp.design_method, api_project_id: tp.api_project_id ?? null, api_endpoint_ids: tp.api_endpoint_ids ?? null });

  const saveDraft = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      if (draft.kind === 'req') {
        if (draft.mode === 'add') {
          const res = await testcaseApi.createRequirementPoint({
            session_id: sessionId as number,
            content: draft.content,
            module_id: draft.module_id ?? null,
            priority: draft.priority,
            api_project_id: draft.api_project_id,
            api_endpoint_ids: draft.api_endpoint_ids,
          });
          if (res.code !== 200) throw new Error(res.message);
        } else {
          const res = await testcaseApi.updateRequirementPoint(draft.id as number, {
            content: draft.content, module_id: draft.module_id ?? null, priority: draft.priority,
          });
          if (res.code !== 200) throw new Error(res.message);
        }
      } else {
        if (draft.mode === 'add') {
          const res = await testcaseApi.createTestPoint({
            session_id: sessionId as number,
            requirement_point_id: draft.rpId as number,
            content: draft.content,
            test_type: draft.test_type,
            design_method: draft.design_method,
            priority: draft.priority,
            api_project_id: draft.api_project_id,
            api_endpoint_ids: draft.api_endpoint_ids,
          });
          if (res.code !== 200) throw new Error(res.message);
        } else {
          const res = await testcaseApi.updateTestPoint(draft.id as number, {
            content: draft.content, test_type: draft.test_type, design_method: draft.design_method, priority: draft.priority,
          });
          if (res.code !== 200) throw new Error(res.message);
        }
      }
      antMessage.success(draft.mode === 'add' ? '已创建' : '已更新');
      setDraft(null);
      refreshAll();
    } catch (e: any) {
      antMessage.error('保存失败：' + (e?.message || '未知错误'));
    } finally {
      setSaving(false);
    }
  };

  const caseStatusTag = (status?: string) => {
    if (status === 'PASSED') return <Tag color="green">通过</Tag>;
    if (status === 'FAILED') return <Tag color="red">失败</Tag>;
    return <Tag>未执行</Tag>;
  };

  if (!sessionId) {
    return (
      <div className="rtp-board">
        <Empty description="请先选择会话" />
      </div>
    );
  }

  return (
    <div className="rtp-board">
      <div className="rtp-toolbar">
        <div className="rtp-toolbar__title">
          <ApartmentOutlined style={{ color: 'var(--color-primary)' }} />
          用例结构
          <span className="rtp-toolbar__sub">需求点 → 测试点 → 测试用例</span>
        </div>
        <div className="rtp-toolbar__right">
          <div className="rtp-stats">
            <span className="rtp-stats-chip">{reqTotal} 需求点</span>
            <span className="rtp-stats-chip">{tpTotal} 测试点</span>
            <span className="rtp-stats-chip">{caseTotal} 用例</span>
          </div>
          <Space>
            <Button size="small" icon={<ReloadOutlined />} onClick={() => refreshAll()} loading={loading || polling}>刷新</Button>
            <Button size="small" type="primary" icon={<PlusOutlined />} onClick={openAddReq}>新增需求点</Button>
          </Space>
        </div>
      </div>

      <div className="rtp-columns">
        {/* 列 1：需求点（分页） */}
        <section className="rtp-col">
          <header className="rtp-col__head">
            <span className="rtp-col__title">
              <ApartmentOutlined /> 需求点
              <span className="rtp-count">{reqTotal}</span>
            </span>
            <Tooltip title="新增需求点">
              <Button size="small" type="text" icon={<PlusOutlined />} onClick={openAddReq} />
            </Tooltip>
          </header>
          <div className="rtp-col__body">
            {loading ? (
              <div className="rtp-loading"><Spin /></div>
            ) : reqPoints.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无需求点，点击右上角新增" />
            ) : (
              reqPoints.map(rp => {
                const selected = selectedReqId === rp.id;
                return (
                  <div
                    key={rp.id}
                    className={`rtp-card ${selected ? 'is-selected' : ''}`}
                    onClick={() => selectReq(rp.id)}
                  >
                    <div className="rtp-card__head">
                      <div className="rtp-card__title">
                        <ApartmentOutlined className="rtp-card__icon rtp-card__icon--req" />
                        <span className="rtp-card__text">{rp.content || '(空)'}</span>
                      </div>
                      <div className="rtp-card__actions" onClick={e => e.stopPropagation()}>
                        <Tooltip title="新增测试点">
                          <Button size="small" type="text" icon={<PlusOutlined />} onClick={() => openAddTp(rp.id)} />
                        </Tooltip>
                        <Tooltip title="重生成测试点（级联到用例）">
                          <Button size="small" type="text" icon={<ReloadOutlined />} onClick={() => handleRegenReq(rp.id)} disabled={polling} />
                        </Tooltip>
                        <Tooltip title="编辑">
                          <Button size="small" type="text" icon={<EditOutlined />} onClick={() => openEditReq(rp)} />
                        </Tooltip>
                        <Popconfirm title="确认删除该需求点及其下所有测试点/用例？" onConfirm={() => handleDeleteReq(rp.id)}>
                          <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                        </Popconfirm>
                      </div>
                    </div>
                    <div className="rtp-card__meta">
                      {rp.module_id ? <Tag>{moduleMap.get(rp.module_id) || rp.module || '未命名模块'}</Tag> : null}
                      {priorityTag(rp.priority)}
                      {rp.api_project_id ? <Tag icon={<ApiOutlined />} color="blue">API</Tag> : null}
                    </div>
                    <div className="rtp-card__foot">{rp.test_point_count} 个测试点</div>
                    {timeFooter(rp.created_at, rp.updated_at)}
                  </div>
                );
              })
            )}
          </div>
          {reqTotal > reqPageSize && (
            <div className="rtp-col__pager">
              <Pagination
                size="small"
                current={reqPage}
                pageSize={reqPageSize}
                total={reqTotal}
                showSizeChanger
                hideOnSinglePage
                onChange={(p, ps) => loadReqPoints(p, ps, false)}
              />
            </div>
          )}
        </section>

        {/* 列 2：测试点（按选中需求点分页加载） */}
        <section className="rtp-col">
          <header className="rtp-col__head">
            <span className="rtp-col__title">
              <NodeIndexOutlined /> 测试点
              <span className="rtp-count">{tpTotal}</span>
            </span>
            <Tooltip title={selectedReqId == null ? '请先选择需求点' : '新增测试点'}>
              <Button
                size="small"
                type="text"
                icon={<PlusOutlined />}
                disabled={selectedReqId == null}
                onClick={() => selectedReqId != null && openAddTp(selectedReqId)}
              />
            </Tooltip>
          </header>
          <div className="rtp-col__body">
            {selectedReqId == null ? (
              <div className="rtp-placeholder">请选择左侧需求点</div>
            ) : tpLoading ? (
              <div className="rtp-loading"><Spin /></div>
            ) : testPoints.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该需求点暂无测试点" />
            ) : (
              testPoints.map(tp => {
                const selected = selectedTpId === tp.id;
                return (
                  <div
                    key={tp.id}
                    className={`rtp-card ${selected ? 'is-selected' : ''}`}
                    onClick={() => selectTp(tp.id)}
                  >
                    <div className="rtp-card__head">
                      <div className="rtp-card__title">
                        <NodeIndexOutlined className="rtp-card__icon rtp-card__icon--tp" />
                        <span className="rtp-card__text">{tp.content || '(空)'}</span>
                      </div>
                      <div className="rtp-card__actions" onClick={e => e.stopPropagation()}>
                        <Tooltip title="重生成用例">
                          <Button size="small" type="text" icon={<ReloadOutlined />} onClick={() => handleRegenTp(tp.id)} disabled={polling} />
                        </Tooltip>
                        <Tooltip title="编辑">
                          <Button size="small" type="text" icon={<EditOutlined />} onClick={() => openEditTp(tp)} />
                        </Tooltip>
                        <Popconfirm title="确认删除该测试点及其下所有用例？" onConfirm={() => handleDeleteTp(tp.id)}>
                          <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                        </Popconfirm>
                      </div>
                    </div>
                    <div className="rtp-card__meta">
                      <Tag color={TEST_TYPE_COLOR[tp.test_type] || 'default'}>{TEST_TYPE_LABEL[tp.test_type] || tp.test_type}</Tag>
                      {tp.design_method ? <Tag color="purple">{DESIGN_METHOD_LABEL[tp.design_method] || tp.design_method}</Tag> : null}
                      {priorityTag(tp.priority)}
                      {tp.api_project_id ? <Tag icon={<ApiOutlined />} color="blue">API</Tag> : null}
                    </div>
                    <div className="rtp-card__foot">{tp.case_count} 个用例</div>
                    {timeFooter(tp.created_at, tp.updated_at)}
                  </div>
                );
              })
            )}
          </div>
          {selectedReqId != null && tpTotal > tpPageSize && (
            <div className="rtp-col__pager">
              <Pagination
                size="small"
                current={tpPage}
                pageSize={tpPageSize}
                total={tpTotal}
                showSizeChanger
                hideOnSinglePage
                onChange={(p, ps) => loadTestPoints(selectedReqId, p, ps, false)}
              />
            </div>
          )}
        </section>

        {/* 列 3：测试用例（按选中测试点分页加载） */}
        <section className="rtp-col">
          <header className="rtp-col__head">
            <span className="rtp-col__title">
              <FileTextOutlined /> 测试用例
              <span className="rtp-count">{caseTotal}</span>
            </span>
          </header>
          <div className="rtp-col__body">
            {selectedTpId == null ? (
              <div className="rtp-placeholder">请选择左侧测试点</div>
            ) : caseLoading ? (
              <div className="rtp-loading"><Spin /></div>
            ) : cases.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该测试点暂无用例" />
            ) : (
              cases.map(c => (
                <div key={c.id} className="rtp-case">
                  <FileTextOutlined className="rtp-case__icon" />
                  <span className="rtp-case__name">{c.case_name}</span>
                  {caseStatusTag(c.status)}
                  <span className="rtp-case__time" title={c.updated_at && c.updated_at !== c.created_at ? `创建：${formatTime(c.created_at)}　更新：${formatTime(c.updated_at)}` : `创建：${formatTime(c.created_at)}`}>
                    {c.updated_at && c.updated_at !== c.created_at
                      ? `${formatTime(c.created_at)} / ${formatTime(c.updated_at)}`
                      : formatTime(c.created_at)}
                  </span>
                </div>
              ))
            )}
          </div>
          {selectedTpId != null && caseTotal > casePageSize && (
            <div className="rtp-col__pager">
              <Pagination
                size="small"
                current={casePage}
                pageSize={casePageSize}
                total={caseTotal}
                showSizeChanger
                hideOnSinglePage
                onChange={(p, ps) => loadCases(selectedTpId, p, ps)}
              />
            </div>
          )}
        </section>
      </div>

      <Modal
        title={draft ? `${draft.mode === 'add' ? '新增' : '编辑'}${draft.kind === 'req' ? '需求点' : '测试点'}` : ''}
        open={!!draft}
        onCancel={() => setDraft(null)}
        confirmLoading={saving}
        onOk={saveDraft}
        destroyOnClose
      >
        {draft && (
          <Space direction="vertical" style={{ width: '100%' }} size={12}>
            <div>
              <div style={{ marginBottom: 4 }}>内容</div>
              <Input.TextArea
                rows={3}
                value={draft.content}
                onChange={(e) => setDraft({ ...draft, content: e.target.value })}
                placeholder={draft.kind === 'req' ? '需求点描述' : '测试点描述'}
              />
            </div>
            {draft.kind === 'req' && (
              <div>
                <div style={{ marginBottom: 4 }}>模块</div>
                <Select
                  allowClear
                  placeholder="选择模块（可选）"
                  style={{ width: '100%' }}
                  value={draft.module_id ?? undefined}
                  onChange={(v) => setDraft({ ...draft, module_id: v ?? null })}
                  options={moduleOptions}
                />
              </div>
            )}
            <div>
              <div style={{ marginBottom: 4 }}>优先级</div>
              <Select
                style={{ width: '100%' }}
                value={draft.priority}
                onChange={(v) => setDraft({ ...draft, priority: v })}
                options={[
                  { label: '高 (high)', value: 'high' },
                  { label: '中 (medium)', value: 'medium' },
                  { label: '低 (low)', value: 'low' },
                ]}
              />
            </div>
            {draft.kind === 'tp' && (
              <>
                <div>
                  <div style={{ marginBottom: 4 }}>测试类别</div>
                  <Select
                    style={{ width: '100%' }}
                    value={draft.test_type}
                    onChange={(v) => setDraft({ ...draft, test_type: v })}
                    options={[
                      { label: '功能 (functional)', value: 'functional' },
                      { label: '性能 (performance)', value: 'performance' },
                      { label: '安全 (security)', value: 'security' },
                      { label: '兼容 (compatibility)', value: 'compatibility' },
                    ]}
                  />
                </div>
                <div>
                  <div style={{ marginBottom: 4 }}>设计方法</div>
                  <Input
                    value={draft.design_method}
                    onChange={(e) => setDraft({ ...draft, design_method: e.target.value })}
                    placeholder="如 equivalence_partitioning / boundary_value"
                  />
                </div>
              </>
            )}
            <div>
              <div style={{ marginBottom: 4 }}>API 项目ID（可选，接口测试用）</div>
              <InputNumber
                style={{ width: '100%' }}
                value={draft.api_project_id ?? null}
                onChange={(v) => setDraft({ ...draft, api_project_id: v ?? null })}
                placeholder="留空表示功能测试"
              />
            </div>
            <div>
              <div style={{ marginBottom: 4 }}>API Endpoint ID 列表（可选，逗号分隔）</div>
              <Input
                value={draft.api_endpoint_ids ?? ''}
                onChange={(e) => setDraft({ ...draft, api_endpoint_ids: e.target.value ? e.target.value : null })}
                placeholder="如 12,13,14"
              />
            </div>
          </Space>
        )}
      </Modal>
    </div>
  );
};

export default RequirementTestPointManager;

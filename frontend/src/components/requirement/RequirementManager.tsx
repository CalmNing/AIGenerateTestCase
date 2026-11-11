import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, Button, Collapse, Empty, Form, Input, Modal, Popconfirm, Select, Space,
  Spin, Table, Tabs, Tag, Tooltip, Typography, message,
} from 'antd';
import {
  CheckCircleOutlined, CloudDownloadOutlined, DeleteOutlined, EditOutlined,
  FileTextOutlined, LinkOutlined, PlusOutlined, ReloadOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import {
  AffectedCase, CaseUpdateResult, CoverageSummary, DocLinkEntry, GenerateFromItemsResult, Module, RequirementDoc,
  RequirementDiffEntry, RequirementItem, RequirementRevision, Session, TestCase,
} from '../../types';
import { moduleApi, requirementDocApi, sessionApi, testcaseApi } from '../../services/api';
import './RequirementManager.css';

const { Text, Paragraph } = Typography;

// 文档陈旧判定阈值：超过该时长未同步显示过期提示
const STALE_THRESHOLD_MS = 24 * 60 * 60 * 1000;

type ItemCoverageLike = CoverageSummary['items'][number];

interface RequirementManagerProps {
  sessions?: Session[];
  /** 直达 AI 用例平台：选中会话并打开「用例结构」页签 */
  onNavigateToStructure?: (sessionId: number) => void;
}

interface SectionGroup {
  section: string;
  items: RequirementItem[];
}

const DIFF_TYPE_META: Record<string, { label: string; color: string }> = {
  added: { label: '新增', color: 'green' },
  modified: { label: '修改', color: 'orange' },
  removed: { label: '删除', color: 'red' },
};

const SOURCE_META: Record<string, { label: string; color: string }> = {
  sync: { label: '飞书同步', color: 'blue' },
  manual: { label: '手动编辑', color: 'purple' },
  apply_cases: { label: '用例联动', color: 'cyan' },
};

const SOURCE_CHIP_LABEL: Record<string, string> = {
  feishu: '需求文档',
  generation: '生成历史',
  manual: '手动录入',
};

const formatTime = (iso: string | null): string => {
  if (!iso) return '-';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '-' : d.toLocaleString('zh-CN', { hour12: false });
};

const diffSummary = (diffs: RequirementDiffEntry[]): string =>
  '新增 ' + diffs.filter((d) => d.type === 'added').length
  + '、修改 ' + diffs.filter((d) => d.type === 'modified').length
  + '、删除 ' + diffs.filter((d) => d.type === 'removed').length;

const groupItems = (items: RequirementItem[]): SectionGroup[] => {
  const groups: SectionGroup[] = [];
  for (const it of [...(items || [])].sort((a, b) => a.order_index - b.order_index)) {
    const section = it.section || '未分类';
    const last = groups[groups.length - 1];
    if (last && last.section === section) {
      last.items.push(it);
    } else {
      groups.push({ section, items: [it] });
    }
  }
  return groups;
};

const RequirementManager: React.FC<RequirementManagerProps> = ({ sessions: sessionProp, onNavigateToStructure }) => {
  const [docs, setDocs] = useState<RequirementDoc[]>([]);
  const [docsLoading, setDocsLoading] = useState(false);
  const [selectedDocId, setSelectedDocId] = useState<number | null>(null);
  const [selectedDoc, setSelectedDoc] = useState<RequirementDoc | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [sessions, setSessions] = useState<Session[]>(sessionProp ?? []);
  const [testcases, setTestcases] = useState<TestCase[]>([]);
  const [revisions, setRevisions] = useState<RequirementRevision[]>([]);
  const [links, setLinks] = useState<DocLinkEntry[]>([]);
  const [activeTab, setActiveTab] = useState<string>('content');

  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm();
  const [creating, setCreating] = useState(false);

  const [editOpen, setEditOpen] = useState(false);
  const [editContent, setEditContent] = useState('');
  const [savingContent, setSavingContent] = useState(false);

  const [syncing, setSyncing] = useState(false);

  const [titleEditing, setTitleEditing] = useState(false);
  const [titleDraft, setTitleDraft] = useState('');

  const [impactOpen, setImpactOpen] = useState(false);
  const [impactLoading, setImpactLoading] = useState(false);
  const [affected, setAffected] = useState<AffectedCase[]>([]);
  const [selectedCaseIds, setSelectedCaseIds] = useState<number[]>([]);
  const [applyResults, setApplyResults] = useState<CaseUpdateResult[] | null>(null);
  const [applyingId, setApplyingId] = useState<number | null>(null);

  const [linkItemKey, setLinkItemKey] = useState<string | undefined>(undefined);
  const [linkCaseId, setLinkCaseId] = useState<number | undefined>(undefined);

  const [coverage, setCoverage] = useState<CoverageSummary | null>(null);
  const [selectedItemKeys, setSelectedItemKeys] = useState<string[]>([]);
  const [onlyUncovered, setOnlyUncovered] = useState(false);
  const [rpOpen, setRpOpen] = useState(false);
  const [rpForm] = Form.useForm();
  const [rpSubmitting, setRpSubmitting] = useState(false);
  const [modules, setModules] = useState<Module[]>([]);
  const [generating, setGenerating] = useState(false);
  const [syncAllRunning, setSyncAllRunning] = useState(false);
  const [sourceFilter, setSourceFilter] = useState<'all' | 'feishu' | 'generation' | 'manual'>('all');

  // 用例明细弹窗 / 生成结果弹窗 / 关键字过滤
  const [casesModalItem, setCasesModalItem] = useState<ItemCoverageLike | null>(null);
  const [generateResult, setGenerateResult] = useState<GenerateFromItemsResult | null>(null);
  const [itemKeyword, setItemKeyword] = useState('');

  // ===== 数据加载 =====

  const loadDocs = useCallback(async () => {
    setDocsLoading(true);
    try {
      const res = await requirementDocApi.list(sourceFilter === 'all' ? undefined : { source_type: sourceFilter });
      setDocs(res.code === 200 && res.data ? res.data : []);
    } catch (e) {
      console.error('加载需求文档失败', e);
    } finally {
      setDocsLoading(false);
    }
  }, [sourceFilter]);

  const loadSessions = useCallback(async () => {
    if (sessionProp && sessionProp.length > 0) {
      setSessions(sessionProp);
      return;
    }
    try {
      const res = await sessionApi.getSessions();
      if (res.code === 200 && res.data) setSessions(res.data);
    } catch (e) {
      console.error('加载会话失败', e);
    }
  }, [sessionProp]);

  const loadTestcases = useCallback(async (sessionId: number | null) => {
    if (!sessionId) {
      setTestcases([]);
      return;
    }
    try {
      const res = await testcaseApi.getTestcases(sessionId);
      setTestcases(res.code === 200 && res.data ? res.data.items : []);
    } catch (e) {
      console.error('加载测试用例失败', e);
      setTestcases([]);
    }
  }, []);

  const loadCoverage = useCallback(async (docId: number) => {
    try {
      const res = await requirementDocApi.coverage(docId);
      setCoverage(res.code === 200 && res.data ? res.data : null);
    } catch (e) {
      console.error('加载覆盖视图失败', e);
      setCoverage(null);
    }
  }, []);

  const loadModules = useCallback(async (sessionId: number | null) => {
    if (!sessionId) {
      setModules([]);
      return;
    }
    try {
      const res = await moduleApi.getModules(sessionId);
      if (res.code === 200 && res.data) setModules(res.data);
    } catch (e) {
      console.error('加载模块失败', e);
      setModules([]);
    }
  }, []);

  const openDoc = useCallback(async (docId: number) => {
    setSelectedDocId(docId);
    setDetailLoading(true);
    setApplyResults(null);
    setAffected([]);
    setSelectedCaseIds([]);
    try {
      const [docRes, revRes, linkRes] = await Promise.all([
        requirementDocApi.get(docId),
        requirementDocApi.revisions(docId),
        requirementDocApi.links(docId),
      ]);
      const doc = docRes.code === 200 && docRes.data ? docRes.data : null;
      setSelectedDoc(doc);
      setRevisions(revRes.code === 200 && revRes.data ? revRes.data : []);
      setLinks(linkRes.code === 200 && linkRes.data ? linkRes.data : []);
      setActiveTab('content');
      await loadTestcases(doc?.session_id ?? null);
      await loadCoverage(docId);
    } catch (e) {
      console.error('加载文档详情失败', e);
      setSelectedDoc(null);
    } finally {
      setDetailLoading(false);
    }
  }, [loadTestcases, loadCoverage]);

  useEffect(() => {
    loadDocs();
    loadSessions();
  }, [loadDocs, loadSessions]);

  const sectionGroups = useMemo(
    () => (selectedDoc ? groupItems(selectedDoc.structured || []) : []),
    [selectedDoc],
  );

  const sectionOptions = useMemo(
    () => (selectedDoc?.structured || []).map((it) => ({
      key: it.key,
      label: (it.section ? it.section + ' · ' : '') + it.title.slice(0, 30),
    })),
    [selectedDoc],
  );

  const latestRevision = revisions.length > 0 ? revisions[0] : null;
  const latestDiffs: RequirementDiffEntry[] = latestRevision?.differences || [];

  const coverageByKey = useMemo(() => {
    const map = new Map<string, ItemCoverageLike>();
    for (const x of coverage?.items || []) map.set(x.item_key, x);
    return map;
  }, [coverage]);

  // ===== 圈选辅助：全选可见/清空/章节全选/关键字过滤 =====

  const matchKeyword = useCallback((content: string, section: string): boolean => {
    const kw = itemKeyword.trim().toLowerCase();
    if (!kw) return true;
    return (content || '').toLowerCase().includes(kw) || (section || '').toLowerCase().includes(kw);
  }, [itemKeyword]);

  const visibleItemKeys = useMemo(() => {
    const keys: string[] = [];
    for (const g of sectionGroups) {
      for (const it of g.items) {
        if (!matchKeyword(it.content, it.section)) continue;
        if (onlyUncovered && (coverageByKey.get(it.key)?.coverage || 'uncovered') !== 'uncovered') continue;
        keys.push(it.key);
      }
    }
    return keys;
  }, [sectionGroups, matchKeyword, onlyUncovered, coverageByKey]);

  const selectAllVisible = () => {
    setSelectedItemKeys((prev) => Array.from(new Set([...prev, ...visibleItemKeys])));
  };

  const clearSelection = () => setSelectedItemKeys([]);

  const selectSection = (section: string) => {
    const group = sectionGroups.find((g) => g.section === section);
    if (!group) return;
    const keys = group.items
      .filter((it) => matchKeyword(it.content, it.section))
      .filter((it) => !onlyUncovered || (coverageByKey.get(it.key)?.coverage || 'uncovered') === 'uncovered')
      .map((it) => it.key);
    setSelectedItemKeys((prev) => Array.from(new Set([...prev, ...keys])));
  };

  const isDocStale = (d: RequirementDoc): boolean => {
    if (!d.updated_at) return false;
    return Date.now() - new Date(d.updated_at).getTime() > STALE_THRESHOLD_MS;
  };

  const staleDays = (d: RequirementDoc): number => {
    if (!d.updated_at) return 0;
    return Math.floor((Date.now() - new Date(d.updated_at).getTime()) / (24 * 60 * 60 * 1000));
  };

  const docHasUnhandledDiff = (docId: number): boolean => {
    if (selectedDocId !== docId) return false;
    return latestDiffs.length > 0;
  };

  // ===== 交互动作 =====

  const handleCreate = async () => {
    try {
      const values = await createForm.validateFields();
      setCreating(true);
      const res = await requirementDocApi.create({
        url: values.url.trim(),
        title: (values.title || '').trim(),
        session_id: values.session_id ?? null,
      });
      if (res.code === 200 && res.data) {
        message.success('需求文档已登记并同步');
        setCreateOpen(false);
        createForm.resetFields();
        await loadDocs();
        await openDoc(res.data.id);
      }
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } } };
      const detail = err.response?.data?.detail;
      if (detail) message.error(detail);
    } finally {
      setCreating(false);
    }
  };

  const handleSync = async (fetchFeishu: boolean) => {
    if (!selectedDoc) return;
    setSyncing(true);
    try {
      const res = await requirementDocApi.sync(selectedDoc.id, fetchFeishu);
      if (res.code === 200 && res.data) {
        const diffs = res.data.differences || [];
        if (diffs.length === 0) {
          message.success('同步完成：内容无变化');
        } else {
          message.success('同步完成：' + diffSummary(diffs) + '，版本 v' + res.data.version);
          await runImpact(res.data.id);
        }
        await loadDocs();
        await openDoc(selectedDoc.id);
      }
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || '同步失败');
    } finally {
      setSyncing(false);
    }
  };

  const handleSaveContent = async () => {
    if (!selectedDoc) return;
    if (!editContent.trim()) {
      message.warning('文档内容不能为空');
      return;
    }
    setSavingContent(true);
    try {
      const res = await requirementDocApi.updateContent(selectedDoc.id, editContent);
      if (res.code === 200 && res.data) {
        const diffs = res.data.differences || [];
        message.success(diffs.length === 0
          ? '内容已保存：无结构变化'
          : '内容已保存：' + diffSummary(diffs));
        setEditOpen(false);
        await loadDocs();
        await openDoc(selectedDoc.id);
      }
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || '保存失败');
    } finally {
      setSavingContent(false);
    }
  };

  const handleSaveTitle = async () => {
    setTitleEditing(false);
    if (!selectedDoc || !titleDraft.trim() || titleDraft.trim() === selectedDoc.title) return;
    try {
      const res = await requirementDocApi.update(selectedDoc.id, { title: titleDraft.trim() });
      if (res.code === 200 && res.data) {
        setSelectedDoc(res.data ?? null);
        await loadDocs();
        message.success('名称已更新');
      }
    } catch (e) {
      console.error('更新名称失败', e);
    }
  };

  const handleSessionChange = async (sessionId: number | undefined) => {
    if (!selectedDoc) return;
    try {
      const res = await requirementDocApi.update(selectedDoc.id, { session_id: sessionId ?? null });
      if (res.code === 200 && res.data) {
        setSelectedDoc(res.data ?? null);
        await loadDocs();
        await loadTestcases(sessionId ?? null);
        await loadModules(sessionId ?? null);
      }
    } catch (e) {
      console.error('更新关联会话失败', e);
    }
  };

  const handleDeleteDoc = async (docId: number) => {
    try {
      await requirementDocApi.remove(docId);
      message.success('需求文档已删除');
      if (selectedDocId === docId) {
        setSelectedDocId(null);
        setSelectedDoc(null);
        setRevisions([]);
        setLinks([]);
      }
      await loadDocs();
    } catch (e) {
      console.error('删除文档失败', e);
    }
  };

  // ===== 影响分析与联动更新 =====

  const runImpact = async (docId: number) => {
    setImpactOpen(true);
    setImpactLoading(true);
    setApplyResults(null);
    try {
      const res = await requirementDocApi.impact(docId);
      if (res.code === 200 && res.data) {
        setAffected(res.data.affected || []);
        setSelectedCaseIds((res.data.affected || []).map((a) => a.testcase_id));
        if ((res.data.affected || []).length === 0) {
          message.info('未识别到受影响的测试用例');
        }
      }
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || '影响分析失败');
    } finally {
      setImpactLoading(false);
    }
  };

  const suggestUpdates = async () => {
    if (!selectedDoc || selectedCaseIds.length === 0) {
      message.warning('请先选择要更新的测试用例');
      return;
    }
    setImpactLoading(true);
    try {
      const res = await requirementDocApi.applyCases(selectedDoc.id, {
        case_ids: selectedCaseIds,
        apply: false,
      });
      if (res.code === 200 && res.data) {
        setApplyResults(res.data.results || []);
        const failed = (res.data.results || []).filter((r) => r.status === 'failed').length;
        if (failed > 0) message.warning(failed + ' 条用例建议生成失败');
        else message.success('修改建议已生成，请展开用例确认后应用');
      }
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || '生成建议失败');
    } finally {
      setImpactLoading(false);
    }
  };

  const applyOne = async (result: CaseUpdateResult) => {
    if (!selectedDoc) return;
    setApplyingId(result.testcase_id);
    try {
      const res = await requirementDocApi.applyCases(selectedDoc.id, {
        case_ids: [result.testcase_id],
        apply: true,
      });
      if (res.code === 200 && res.data) {
        const r0 = (res.data.results || [])[0];
        if (r0 && r0.status === 'updated') {
          message.success('用例「' + (result.case_name || result.testcase_id) + '」已按需求更新');
          setApplyResults((prev) => (prev || []).map((r) => (
            r.testcase_id === result.testcase_id ? { ...r, status: 'updated' } : r
          )));
          const revRes = await requirementDocApi.revisions(selectedDoc.id);
          if (revRes.code === 200 && revRes.data) setRevisions(revRes.data);
        } else {
          message.error(r0?.error || '应用失败');
        }
      }
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || '应用失败');
    } finally {
      setApplyingId(null);
    }
  };

  // ===== 关联映射 =====

  const handleLink = async () => {
    if (!selectedDoc || !linkItemKey || !linkCaseId) {
      message.warning('请选择需求条目与测试用例');
      return;
    }
    try {
      await requirementDocApi.upsertLink(selectedDoc.id, {
        item_key: linkItemKey,
        testcase_id: linkCaseId,
        session_id: selectedDoc.session_id,
      });
      message.success('关联已建立');
      setLinkItemKey(undefined);
      setLinkCaseId(undefined);
      const res = await requirementDocApi.links(selectedDoc.id);
      if (res.code === 200 && res.data) setLinks(res.data);
    } catch (e) {
      console.error('建立关联失败', e);
    }
  };

  const handleUnlink = async (entry: DocLinkEntry) => {
    if (!selectedDoc) return;
    try {
      await requirementDocApi.upsertLink(selectedDoc.id, {
        item_key: entry.item_key,
        testcase_id: 0,
      });
      const res = await requirementDocApi.links(selectedDoc.id);
      if (res.code === 200 && res.data) setLinks(res.data);
    } catch (e) {
      console.error('解除关联失败', e);
    }
  };

  // ===== 条目圈选 / 覆盖 / 条目直生用例 =====

  const openRpModal = () => {
    if (!selectedDoc) return;
    if (selectedItemKeys.length === 0) {
      message.warning('请先勾选需求条目');
      return;
    }
    rpForm.setFieldsValue({ session_id: selectedDoc.session_id ?? undefined, module_id: undefined });
    if (selectedDoc.session_id) loadModules(selectedDoc.session_id);
    setRpOpen(true);
  };

  const handleToRequirementPoints = async () => {
    if (!selectedDoc) return;
    try {
      const values = await rpForm.validateFields();
      setRpSubmitting(true);
      const res = await requirementDocApi.toRequirementPoints(selectedDoc.id, {
        session_id: values.session_id,
        module_id: values.module_id ?? null,
        item_keys: selectedItemKeys,
      });
      if (res.code === 200 && res.data) {
        const skippedNote = res.data.skipped > 0 ? '，跳过重复 ' + res.data.skipped + ' 条' : '';
        message.success({ content: '已圈选 ' + res.data.created + ' 条需求点' + skippedNote, duration: 6 });
        setRpOpen(false);
        if (onNavigateToStructure && res.data.created > 0) {
          setTimeout(() => {
            Modal.confirm({
              title: '已圈选为需求点',
              content: '是否立即前往 AI 测试用例平台「用例结构」页签查看并继续生成？',
              okText: '去用例结构',
              cancelText: '留在本页',
              onOk: () => onNavigateToStructure(values.session_id),
            });
          }, 300);
        }
      }
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } } };
      if (err.response?.data?.detail) message.error(err.response.data.detail);
    } finally {
      setRpSubmitting(false);
    }
  };

  const handleGenerateFromItems = async () => {
    if (!selectedDoc) return;
    if (selectedItemKeys.length === 0) {
      message.warning('请先勾选需求条目');
      return;
    }
    if (!selectedDoc.session_id) {
      message.warning('请先在右上角选择关联会话');
      return;
    }
    setGenerating(true);
    try {
      const res = await requirementDocApi.generateFromItems(selectedDoc.id, {
        session_id: selectedDoc.session_id,
        item_keys: selectedItemKeys,
      });
      if (res.code === 200 && res.data) {
        setGenerateResult(res.data);
        message.success('已生成 ' + res.data.generated + ' 条测试用例，自动关联 ' + res.data.linked + ' 条');
        await loadTestcases(selectedDoc.session_id);
        await loadCoverage(selectedDoc.id);
        const revRes = await requirementDocApi.revisions(selectedDoc.id);
        if (revRes.code === 200 && revRes.data) setRevisions(revRes.data);
      }
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || '生成失败');
    } finally {
      setGenerating(false);
    }
  };

  const handleSyncAll = async () => {
    if (docs.length === 0) return;
    setSyncAllRunning(true);
    let unchanged = 0;
    let changed = 0;
    let failed = 0;
    try {
      for (const doc of docs) {
        try {
          const res = await requirementDocApi.sync(doc.id, true);
          if (res.code === 200 && res.data) {
            if ((res.data.differences || []).length === 0) unchanged += 1;
            else changed += 1;
          } else {
            failed += 1;
          }
        } catch {
          failed += 1;
        }
      }
      message.success('同步完成：无变化 ' + unchanged + '、有差异 ' + changed + '、失败 ' + failed);
      await loadDocs();
      if (selectedDocId) await openDoc(selectedDocId);
    } finally {
      setSyncAllRunning(false);
    }
  };

  // ===== 渲染辅助 =====

  const itemKeyTitle = useCallback((key: string): string => {
    const it = (selectedDoc?.structured || []).find((x) => x.key === key);
    return it ? (it.section ? it.section + ' · ' : '') + it.title.slice(0, 24) : key;
  }, [selectedDoc]);

  const caseNameById = useMemo(() => {
    const map = new Map<number, string>();
    for (const tc of testcases) map.set(tc.id as number, tc.case_name);
    return map;
  }, [testcases]);

  const linkRows = useMemo(() => links.map((l) => ({
    ...l,
    item_label: itemKeyTitle(l.item_key),
    case_label: caseNameById.get(l.testcase_id) || ('用例 #' + l.testcase_id),
  })), [links, caseNameById, itemKeyTitle]);

  const renderDiffTable = (diffs: RequirementDiffEntry[]) => (
    <Table
      size="small"
      rowKey={(d) => d.type + '-' + d.key}
      dataSource={diffs}
      pagination={false}
      columns={[
        {
          title: '类型',
          dataIndex: 'type',
          width: 70,
          render: (t: string) => {
            const meta = DIFF_TYPE_META[t] || { label: t, color: 'default' };
            return <Tag color={meta.color}>{meta.label}</Tag>;
          },
        },
        { title: '章节', dataIndex: 'section', width: 180, ellipsis: true },
        {
          title: '变更前',
          dataIndex: 'before',
          render: (v: string) => (v
            ? <Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0, fontSize: 12 }}>{v}</Paragraph>
            : <Text type="secondary">-</Text>),
        },
        {
          title: '变更后',
          dataIndex: 'after',
          render: (v: string) => (v
            ? <Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0, fontSize: 12 }}>{v}</Paragraph>
            : <Text type="secondary" delete>已删除</Text>),
        },
      ]}
    />
  );

  // ===== 组件渲染 =====

  return (
    <div className="req-mgr">
      <div className="req-mgr-sidebar">
        <div className="req-mgr-sidebar-head">
          <span>需求文档</span>
          <Space size={4}>
            {docs.length > 1 && (
              <Tooltip title="逐个从飞书同步全部文档">
                <Button
                  size="small"
                  icon={<CloudDownloadOutlined />}
                  loading={syncAllRunning}
                  onClick={handleSyncAll}
                >
                  全部
                </Button>
              </Tooltip>
            )}
            <Button
              type="primary"
              size="small"
              icon={<PlusOutlined />}
              onClick={() => setCreateOpen(true)}
            >
              登记
            </Button>
          </Space>
        </div>
        <div className="req-mgr-source-filter">
          {[
            { key: 'all', label: '全部' },
            { key: 'feishu', label: '需求文档' },
            { key: 'generation', label: '生成历史' },
            { key: 'manual', label: '手动录入' },
          ].map((s) => (
            <button
              key={s.key}
              type="button"
              className={'req-mgr-source-chip' + (sourceFilter === s.key ? ' active' : '')}
              onClick={() => setSourceFilter(s.key as 'all' | 'feishu' | 'generation' | 'manual')}
            >
              {s.label}
            </button>
          ))}
        </div>
        <Spin spinning={docsLoading}>
          {docs.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={sourceFilter === 'all' ? '暂无需求文档，请登记飞书文档链接' : '该来源下暂无条目'}
              style={{ marginTop: 40 }}
            />
          ) : (
            <div className="req-mgr-doc-list">
              {docs.map((doc) => (
                <div
                  key={doc.id}
                  className={'req-mgr-doc-item' + (selectedDocId === doc.id ? ' active' : '')}
                  onClick={() => openDoc(doc.id)}
                >
                  <div className="req-mgr-doc-item-title">
                    <FileTextOutlined />
                    <span className="req-mgr-doc-name">{doc.title}</span>
                    {doc.source_type !== 'feishu' && (
                      <Tag color={doc.source_type === 'generation' ? 'cyan' : 'purple'} style={{ fontSize: 10, marginRight: 4 }}>
                        {SOURCE_CHIP_LABEL[doc.source_type] || doc.source_type}
                      </Tag>
                    )}
                    {docHasUnhandledDiff(doc.id) && <span className="req-mgr-dot" title="有未处理的变更差异" />}
                    <Tag style={{ marginLeft: 'auto' }}>{'v' + doc.version}</Tag>
                  </div>
                  <div className="req-mgr-doc-item-meta">
                    <span>
                      {formatTime(doc.updated_at)}
                      {isDocStale(doc) && <Tag color="orange" style={{ marginLeft: 6, fontSize: 10 }}>距上次同步 {staleDays(doc)} 天</Tag>}
                    </span>
                    <Popconfirm
                      title="删除该需求文档？"
                      description="变更历史与关联映射将一并删除"
                      onConfirm={(e) => { e?.stopPropagation(); handleDeleteDoc(doc.id); }}
                      onCancel={(e) => e?.stopPropagation()}
                      okText="删除"
                      cancelText="取消"
                    >
                      <Button
                        type="text"
                        size="small"
                        danger
                        icon={<DeleteOutlined />}
                        onClick={(e) => e.stopPropagation()}
                      />
                    </Popconfirm>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Spin>
      </div>

      <div className="req-mgr-detail">
        {!selectedDoc ? (
          <Empty
            description={docs.length > 0 ? '请选择左侧需求条目' : '登记飞书需求文档后，可在此查看结构化需求、变更历史并联动测试用例'}
            style={{ marginTop: 100 }}
          />
        ) : (
          <Spin spinning={detailLoading}>
            <div className="req-mgr-doc-header">
              <div className="req-mgr-doc-header-main">
                {titleEditing ? (
                  <Input
                    value={titleDraft}
                    onChange={(e) => setTitleDraft(e.target.value)}
                    onPressEnter={handleSaveTitle}
                    onBlur={handleSaveTitle}
                    autoFocus
                    size="small"
                    style={{ maxWidth: 360 }}
                  />
                ) : (
                  <h2
                    className="req-mgr-doc-title"
                    onClick={() => { setTitleDraft(selectedDoc.title); setTitleEditing(true); }}
                    title="点击编辑名称"
                  >
                    {selectedDoc.title}
                    <EditOutlined style={{ fontSize: 12, marginLeft: 8, opacity: 0.5 }} />
                  </h2>
                )}
                <div className="req-mgr-doc-header-meta">
                  <Space size="small" wrap>
                    <Tag color="blue">{'v' + selectedDoc.version}</Tag>
                    {selectedDoc.source_type !== 'feishu' && (
                      <Tag color={selectedDoc.source_type === 'generation' ? 'cyan' : 'purple'}>
                        {SOURCE_CHIP_LABEL[selectedDoc.source_type] || selectedDoc.source_type}
                      </Tag>
                    )}
                    {selectedDoc.source_type === 'feishu' && (
                      <Tooltip title={selectedDoc.url}>
                        <a href={selectedDoc.url} target="_blank" rel="noopener noreferrer">
                          <LinkOutlined /> 打开飞书文档
                        </a>
                      </Tooltip>
                    )}
                    {latestRevision && (
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        最近变更：{(SOURCE_META[latestRevision.source] || { label: latestRevision.source }).label}
                        {' · '}
                        {formatTime(latestRevision.created_at)}
                      </Text>
                    )}
                  </Space>
                </div>
              </div>
              <div className="req-mgr-doc-header-actions">
                <Select
                  placeholder="关联会话（用于定位测试用例）"
                  value={selectedDoc.session_id ?? undefined}
                  onChange={handleSessionChange}
                  allowClear
                  showSearch
                  optionFilterProp="label"
                  style={{ width: 200 }}
                  options={sessions.map((s) => ({ value: s.id as number, label: s.name }))}
                />
                {selectedDoc.source_type === 'feishu' && (
                  <>
                    <Tooltip title="从飞书拉取最新内容">
                      <Button icon={<CloudDownloadOutlined />} loading={syncing} onClick={() => handleSync(true)}>
                        同步飞书
                      </Button>
                    </Tooltip>
                    <Tooltip title="不访问飞书，按本地快照重新解析">
                      <Button icon={<ReloadOutlined />} loading={syncing} onClick={() => handleSync(false)}>
                        重新解析
                      </Button>
                    </Tooltip>
                  </>
                )}
                <Button
                  icon={<EditOutlined />}
                  onClick={() => { setEditContent(selectedDoc.content_snapshot); setEditOpen(true); }}
                >
                  编辑正文
                </Button>
                <Tooltip title={latestDiffs.length === 0 ? '最近一次同步无差异' : '基于最近一次变更识别受影响的测试用例'}>
                  <Button
                    type="primary"
                    icon={<ThunderboltOutlined />}
                    disabled={latestDiffs.length === 0}
                    onClick={() => selectedDoc && runImpact(selectedDoc.id)}
                  >
                    影响分析
                  </Button>
                </Tooltip>
              </div>
            </div>

            {latestDiffs.length > 0 && (
              <Alert
                type="warning"
                showIcon
                style={{ margin: '0 16px' }}
                message={'最近一次变更（v' + selectedDoc.version + '）有 ' + latestDiffs.length + ' 处差异：' + diffSummary(latestDiffs)}
                action={(
                  <Button size="small" type="primary" onClick={() => runImpact(selectedDoc.id)}>
                    检查影响
                  </Button>
                )}
              />
            )}

            <Tabs
              activeKey={activeTab}
              onChange={setActiveTab}
              style={{ padding: '0 16px 16px', flex: 1, minHeight: 0 }}
              items={[
                {
                  key: 'content',
                  label: '需求内容',
                  children: (
                    <div className="req-mgr-content">
                      {sectionGroups.length === 0 ? (
                        <Empty description="暂无结构化内容，请先同步或重新解析" />
                      ) : (
                        <>
                          <div className="req-mgr-coverage-bar">
                            <Space size="small" wrap>
                              {coverage && (
                                <>
                                  <Tag color="green">已覆盖 {coverage.covered}/{coverage.total}</Tag>
                                  {coverage.pending > 0 && <Tag color="orange">待生成 {coverage.pending}</Tag>}
                                  {coverage.uncovered > 0 && <Tag color="red">未覆盖 {coverage.uncovered}</Tag>}
                                </>
                              )}
                              <Button
                                size="small"
                                type={onlyUncovered ? 'primary' : 'default'}
                                onClick={() => setOnlyUncovered(!onlyUncovered)}
                              >
                                只看未覆盖
                              </Button>
                              <Input
                                allowClear
                                size="small"
                                placeholder="搜索条目内容/章节"
                                value={itemKeyword}
                                onChange={(e) => setItemKeyword(e.target.value)}
                                style={{ width: 180 }}
                              />
                            </Space>
                            <Space size="small" wrap>
                              <Button
                                size="small"
                                onClick={selectAllVisible}
                                disabled={visibleItemKeys.length === 0}
                              >
                                全选可见 ({visibleItemKeys.length})
                              </Button>
                              <Button size="small" onClick={clearSelection} disabled={selectedItemKeys.length === 0}>
                                清空
                              </Button>
                              <Button
                                size="small"
                                icon={<PlusOutlined />}
                                onClick={openRpModal}
                                disabled={selectedItemKeys.length === 0}
                              >
                                转为需求点 ({selectedItemKeys.length})
                              </Button>
                              <Tooltip title="按勾选条目生成测试用例，并自动建立关联">
                                <Button
                                  size="small"
                                  type="primary"
                                  icon={<ThunderboltOutlined />}
                                  loading={generating}
                                  disabled={selectedItemKeys.length === 0 || !selectedDoc.session_id}
                                  onClick={handleGenerateFromItems}
                                >
                                  生成用例
                                </Button>
                              </Tooltip>
                            </Space>
                          </div>
                          {!selectedDoc.session_id && (
                            <Alert type="info" showIcon style={{ marginBottom: 8 }} message="提示：先在右上角选择关联会话，才能圈选需求点或生成用例" />
                          )}
                          <Collapse
                            defaultActiveKey={sectionGroups.map((_, i) => String(i))}
                            items={sectionGroups.map((g, i) => {
                              const visibleItems = g.items
                                .filter((it) => matchKeyword(it.content, it.section))
                                .filter((it) => !onlyUncovered || (coverageByKey.get(it.key)?.coverage || 'uncovered') === 'uncovered');
                              return {
                                key: String(i),
                                label: (
                                  <Space size="small">
                                    <strong>{g.section}</strong>
                                    <Button
                                      size="small"
                                      type="link"
                                      style={{ padding: 0, height: 'auto', fontSize: 12 }}
                                      onClick={(e) => { e.stopPropagation(); selectSection(g.section); }}
                                    >
                                      全选本章节
                                    </Button>
                                  </Space>
                                ),
                                children: visibleItems.length === 0
                                  ? <Text type="secondary">{onlyUncovered && g.items.length > 0 ? '该章节条目均已覆盖' : (itemKeyword ? '无匹配条目' : '该章节暂无条目')}</Text>
                                  : (
                                    <div className="req-mgr-items">
                                      {visibleItems.map((it) => {
                                        const cov = coverageByKey.get(it.key);
                                        const checked = selectedItemKeys.includes(it.key);
                                        return (
                                          <div key={it.key} className={'req-mgr-item' + (checked ? ' checked' : '')}>
                                            <div className="req-mgr-item-head">
                                              <label className="req-mgr-item-check">
                                                <input
                                                  type="checkbox"
                                                  checked={checked}
                                                  onChange={(e) => {
                                                    setSelectedItemKeys((prev) => (
                                                      e.target.checked ? [...prev, it.key] : prev.filter((k) => k !== it.key)
                                                    ));
                                                  }}
                                                />
                                                <span>圈选</span>
                                              </label>
                                              {cov?.coverage === 'covered' && (
                                                <Tooltip title="点击查看关联用例">
                                                  <Tag
                                                    color="green"
                                                    style={{ cursor: 'pointer' }}
                                                    onClick={() => setCasesModalItem(cov ?? null)}
                                                  >
                                                    {cov.case_status.passed > 0 && '通过 ' + cov.case_status.passed}
                                                    {cov.case_status.failed > 0 && ' 未通过 ' + cov.case_status.failed}
                                                    {cov.case_status.not_run > 0 && ' 未执行 ' + cov.case_status.not_run}
                                                    {' · ' + cov.case_details.length + ' 用例'}
                                                  </Tag>
                                                </Tooltip>
                                              )}
                                              {cov?.coverage === 'pending' && <Tag color="orange">待生成</Tag>}
                                              {(!cov || cov.coverage === 'uncovered') && <Tag color="red">未覆盖</Tag>}
                                            </div>
                                            <div className="req-mgr-item-text">{it.content}</div>
                                            <div className="req-mgr-item-foot">
                                              <Text type="secondary" style={{ fontSize: 12 }}>{it.key}</Text>
                                              {cov && cov.requirement_point_ids.length > 0 && (
                                                <Tag style={{ fontSize: 11 }}>需求点 ×{cov.requirement_point_ids.length}</Tag>
                                              )}
                                            </div>
                                          </div>
                                        );
                                      })}
                                    </div>
                                  ),
                              };
                            })}
                          />
                        </>
                      )}
                    </div>
                  ),
                },
                {
                  key: 'revisions',
                  label: '变更历史 (' + revisions.length + ')',
                  children: (
                    <div className="req-mgr-revisions">
                      {revisions.length === 0 ? (
                        <Empty description="暂无变更记录" />
                      ) : (
                        revisions.map((rev, idx) => {
                          const meta = SOURCE_META[rev.source] || { label: rev.source, color: 'default' };
                          return (
                            <Collapse
                              key={rev.id}
                              defaultActiveKey={idx === 0 ? [String(rev.id)] : []}
                              items={[{
                                key: String(rev.id),
                                label: (
                                  <Space size="small" wrap>
                                    <Tag color={meta.color}>{meta.label}</Tag>
                                    <strong>{'v' + rev.version}</strong>
                                    <span>{rev.summary}</span>
                                    <Text type="secondary" style={{ fontSize: 12 }}>{formatTime(rev.created_at)}</Text>
                                  </Space>
                                ),
                                children: rev.differences.length === 0
                                  ? <Text type="secondary">无正文差异{rev.affected_case_ids.length > 0 ? '；联动更新了 ' + rev.affected_case_ids.length + ' 条用例' : ''}</Text>
                                  : (
                                    <div>
                                      {renderDiffTable(rev.differences)}
                                      {idx === 0 && selectedDoc && (
                                        <Button
                                          size="small"
                                          type="primary"
                                          icon={<ThunderboltOutlined />}
                                          style={{ marginTop: 8 }}
                                          onClick={() => runImpact(selectedDoc.id)}
                                        >
                                          检查影响
                                        </Button>
                                      )}
                                      {rev.affected_case_ids.length > 0 && (
                                        <div style={{ marginTop: 8 }}>
                                          <Text type="secondary">受影响用例：</Text>
                                          {rev.affected_case_ids.map((cid) => (
                                            <Tag key={cid}>{caseNameById.get(cid) || ('#' + cid)}</Tag>
                                          ))}
                                        </div>
                                      )}
                                    </div>
                                  ),
                              }]}
                            />
                          );
                        })
                      )}
                    </div>
                  ),
                },
                {
                  key: 'links',
                  label: '关联映射 (' + links.length + ')',
                  children: (
                    <div className="req-mgr-links">
                      <div className="req-mgr-link-form">
                        <Select
                          placeholder="选择需求条目"
                          style={{ minWidth: 260 }}
                          value={linkItemKey}
                          onChange={setLinkItemKey}
                          showSearch
                          optionFilterProp="label"
                          options={sectionOptions}
                        />
                        <Select
                          placeholder="选择测试用例（按关联会话加载）"
                          style={{ minWidth: 240 }}
                          value={linkCaseId}
                          onChange={setLinkCaseId}
                          showSearch
                          optionFilterProp="label"
                          options={testcases.map((tc) => ({ value: tc.id as number, label: tc.case_name }))}
                        />
                        <Button type="primary" icon={<LinkOutlined />} onClick={handleLink}>
                          建立关联
                        </Button>
                      </div>
                      {!selectedDoc.session_id && (
                        <Alert type="info" showIcon message="提示：先在右上角选择关联会话，才能加载该会话下的测试用例" style={{ marginBottom: 12 }} />
                      )}
                      <Table
                        size="small"
                        rowKey="id"
                        dataSource={linkRows}
                        pagination={false}
                        locale={{ emptyText: '暂无关联映射，可通过上方表单建立需求条目与测试用例的关联' }}
                        columns={[
                          { title: '需求条目', dataIndex: 'item_label', ellipsis: true },
                          { title: '测试用例', dataIndex: 'case_label', ellipsis: true },
                          {
                            title: '操作',
                            width: 90,
                            render: (_: unknown, record: DocLinkEntry) => (
                              <Popconfirm title="解除该关联？" onConfirm={() => handleUnlink(record)} okText="解除" cancelText="取消">
                                <Button size="small" danger type="text">解除</Button>
                              </Popconfirm>
                            ),
                          },
                        ]}
                      />
                    </div>
                  ),
                },
              ]}
            />
          </Spin>
        )}
      </div>

      <Modal
        title="登记飞书需求文档"
        open={createOpen}
        onOk={handleCreate}
        onCancel={() => setCreateOpen(false)}
        confirmLoading={creating}
        okText="登记并同步"
        destroyOnClose
      >
        <Form form={createForm} layout="vertical">
          <Form.Item
            name="url"
            label="飞书文档链接"
            rules={[
              { required: true, message: '请输入飞书文档链接' },
              { type: 'url', message: '请输入合法的 URL' },
            ]}
            extra="支持 wiki / docx 链接，需已在 MCP 中配置飞书应用"
          >
            <Input placeholder="https://xxx.feishu.cn/wiki/xxxx" />
          </Form.Item>
          <Form.Item name="title" label="需求名称（可选，留空自动取文档标题）">
            <Input placeholder="如：会员中心改版需求" />
          </Form.Item>
          <Form.Item name="session_id" label="关联测试用例会话（可选）">
            <Select
              placeholder="选择会话，便于后续影响分析定位用例"
              allowClear
              showSearch
              optionFilterProp="label"
              options={sessions.map((s) => ({ value: s.id as number, label: s.name }))}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="编辑文档正文"
        open={editOpen}
        width={860}
        onOk={handleSaveContent}
        onCancel={() => setEditOpen(false)}
        confirmLoading={savingContent}
        okText="保存并记录差异"
        destroyOnClose
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="保存后将自动解析结构、生成差异明细（新增/修改/删除）并保留历史版本"
        />
        <Input.TextArea
          value={editContent}
          onChange={(e) => setEditContent(e.target.value)}
          rows={18}
          style={{ fontFamily: 'monospace' }}
        />
      </Modal>

      <Modal
        title="变更影响分析"
        open={impactOpen}
        width={920}
        footer={[
          <Button key="close" onClick={() => setImpactOpen(false)}>关闭</Button>,
          <Button
            key="suggest"
            type="primary"
            loading={impactLoading}
            disabled={selectedCaseIds.length === 0}
            onClick={suggestUpdates}
          >
            {'生成修改建议 (' + selectedCaseIds.length + ')'}
          </Button>,
        ]}
        onCancel={() => setImpactOpen(false)}
      >
        <Spin spinning={impactLoading}>
          {affected.length === 0 && !impactLoading ? (
            <Empty description="未识别到受影响的测试用例" />
          ) : (
            <>
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 12 }}
                message="基于最近一次需求变更的差异，通过关联映射与关键词匹配识别受影响的测试用例；勾选后可生成基于最新需求内容的修改建议。"
              />
              <Table
                size="small"
                rowKey="testcase_id"
                dataSource={affected}
                pagination={false}
                rowSelection={{ selectedRowKeys: selectedCaseIds, onChange: (keys) => setSelectedCaseIds(keys as number[]) }}
                columns={[
                  {
                    title: '测试用例',
                    dataIndex: 'case_name',
                    width: 200,
                    ellipsis: true,
                    render: (v: string, record: AffectedCase) => {
                      const done = applyResults?.find((r) => r.testcase_id === record.testcase_id && r.status === 'updated');
                      return done ? <span>{v} <Tag color="green" icon={<CheckCircleOutlined />}>已更新</Tag></span> : v;
                    },
                  },
                  {
                    title: '匹配关键词',
                    dataIndex: 'keywords',
                    width: 200,
                    render: (kw: string[]) => (kw || []).slice(0, 6).map((k) => <Tag key={k} style={{ fontSize: 11 }}>{k}</Tag>),
                  },
                  {
                    title: '建议',
                    dataIndex: 'suggestion',
                    render: (v: string, record: AffectedCase) => (
                      <Space direction="vertical" size={2} style={{ width: '100%' }}>
                        <Text style={{ fontSize: 12 }}>{v}</Text>
                        {record.suggestion_source === 'llm' && <Tag color="purple" style={{ fontSize: 11 }}>AI 生成</Tag>}
                      </Space>
                    ),
                  },
                  {
                    title: '操作',
                    width: 120,
                    render: (_: unknown, record: AffectedCase) => {
                      const result = applyResults?.find((r) => r.testcase_id === record.testcase_id);
                      if (result && result.status === 'updated') return null;
                      if (result && result.updated) {
                        return (
                          <Button
                            size="small"
                            type="primary"
                            loading={applyingId === record.testcase_id}
                            onClick={() => applyOne(result)}
                          >
                            应用修改
                          </Button>
                        );
                      }
                      return null;
                    },
                  },
                ]}
                expandable={{
                  rowExpandable: (record: AffectedCase) => {
                    const result = applyResults?.find((r) => r.testcase_id === record.testcase_id);
                    return !!result && !!result.updated;
                  },
                  expandedRowRender: (record: AffectedCase) => {
                    const result = applyResults?.find((r) => r.testcase_id === record.testcase_id);
                    if (!result?.updated) return null;
                    return (
                      <div className="req-mgr-apply-preview">
                        <p><strong>修改后用例名：</strong>{result.updated.case_name}</p>
                        <p><strong>预置条件：</strong>{result.updated.preset_conditions.join('；') || '-'}</p>
                        <p><strong>测试步骤：</strong></p>
                        <ol>
                          {result.updated.steps.map((s, i) => (
                            <li key={i}>{typeof s === 'string' ? s : JSON.stringify(s)}</li>
                          ))}
                        </ol>
                        <p><strong>预期结果：</strong>{result.updated.expected_results.join('；') || '-'}</p>
                      </div>
                    );
                  },
                }}
              />
              {applyResults && applyResults.some((r) => r.status === 'failed') && (
                <Alert
                  type="error"
                  showIcon
                  style={{ marginTop: 12 }}
                  message={'部分用例处理失败：' + applyResults.filter((r) => r.status === 'failed').map((r) => r.error || r.testcase_id).join('；')}
                />
              )}
            </>
          )}
        </Spin>
      </Modal>

      <Modal
        title={'圈选 ' + selectedItemKeys.length + ' 条需求条目为需求点'}
        open={rpOpen}
        onOk={handleToRequirementPoints}
        onCancel={() => setRpOpen(false)}
        confirmLoading={rpSubmitting}
        okText="圈选"
        destroyOnClose
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="圈选后进入所选会话的需求点体系，可在 AI 测试用例平台「用例结构」页签查看并走四阶段深度生成；重复圈选自动跳过。"
        />
        <Form form={rpForm} layout="vertical">
          <Form.Item
            name="session_id"
            label="目标会话"
            rules={[{ required: true, message: '请选择目标会话' }]}
          >
            <Select
              placeholder="选择测试用例会话"
              showSearch
              optionFilterProp="label"
              onChange={(v: number) => loadModules(v ?? null)}
              options={sessions.map((s) => ({ value: s.id as number, label: s.name }))}
            />
          </Form.Item>
          <Form.Item name="module_id" label="目标模块（可选）">
            <Select
              placeholder="选择模块（可留空）"
              allowClear
              showSearch
              optionFilterProp="children"
              disabled={modules.length === 0}
              options={modules.map((m) => ({ value: m.id as number, label: m.module_name }))}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={'关联用例' + (casesModalItem ? '（' + casesModalItem.section + '）' : '')}
        open={!!casesModalItem}
        width={680}
        footer={[
          <Button key="close" onClick={() => setCasesModalItem(null)}>关闭</Button>,
          casesModalItem && casesModalItem.case_details.length > 0 && onNavigateToStructure && selectedDoc?.session_id && (
            <Button
              key="goto"
              type="primary"
              onClick={() => {
                setCasesModalItem(null);
                onNavigateToStructure(selectedDoc.session_id as number);
              }}
            >
              去用例结构查看
            </Button>
          ),
        ].filter(Boolean)}
        onCancel={() => setCasesModalItem(null)}
      >
        {casesModalItem && casesModalItem.case_details.length === 0 ? (
          <Empty description="该条目暂无关联用例" />
        ) : (
          <Table
            size="small"
            rowKey="id"
            dataSource={casesModalItem?.case_details || []}
            pagination={false}
            columns={[
              { title: '用例名称', dataIndex: 'case_name', ellipsis: true },
              {
                title: '状态',
                dataIndex: 'status',
                width: 90,
                render: (s: string) => (
                  <Tag color={s === 'PASSED' ? 'green' : s === 'FAILED' ? 'red' : 'default'}>
                    {s === 'PASSED' ? '通过' : s === 'FAILED' ? '未通过' : '未执行'}
                  </Tag>
                ),
              },
            ]}
          />
        )}
      </Modal>

      <Modal
        title="用例生成完成"
        open={!!generateResult}
        width={680}
        footer={[
          <Button key="close" onClick={() => setGenerateResult(null)}>关闭</Button>,
          generateResult && onNavigateToStructure && (
            <Button
              key="goto"
              type="primary"
              onClick={() => {
                const sid = selectedDoc?.session_id;
                setGenerateResult(null);
                if (sid) onNavigateToStructure(sid);
              }}
            >
              去用例结构查看
            </Button>
          ),
        ]}
        onCancel={() => setGenerateResult(null)}
      >
        {generateResult && (
          <div>
            <Alert
              type="success"
              showIcon
              message={'生成 ' + generateResult.generated + ' 条测试用例，自动关联 ' + generateResult.linked + ' 条到需求条目'}
              style={{ marginBottom: 12 }}
            />
            {generateResult.warnings && generateResult.warnings.length > 0 && (
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 12 }}
                message={'降级提示：' + generateResult.warnings.join('；')}
              />
            )}
            <div>
              {generateResult.case_names.map((name, i) => (
                <Tag key={i} style={{ marginBottom: 4 }}>{name}</Tag>
              ))}
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
};

export default RequirementManager;

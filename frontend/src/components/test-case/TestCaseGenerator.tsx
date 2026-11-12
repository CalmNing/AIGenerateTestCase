import React, { useRef, useState, useEffect, useCallback, useMemo } from 'react';
import { Button, Checkbox, Input, TreeSelect, Spin, Tag, message as antMessage, Modal, Progress, Steps } from 'antd';
import {
  MessageOutlined, BulbOutlined,
  FileTextOutlined, ExperimentOutlined, UploadOutlined,
  CheckCircleOutlined, LoadingOutlined, CloseCircleOutlined, HistoryOutlined,
} from '@ant-design/icons';
import { Session, Module, RequirementDoc, RequirementItem } from '../../types';
import GenerationHistoryModal from '../requirement/GenerationHistoryModal';
import './TestCaseGenerator.css';

/** P0 失败可见性：后端返回的降级警告 / 质量提示 */
export interface PipelineWarning {
  phase: number;
  phase_name?: string;
  code: string;
  message: string;
  detail?: string;
  /** warning=结果确实缺内容（任务状态 partial）；quality=仅质量提示 */
  severity?: 'warning' | 'quality';
}

/** 从 pipeline 状态里取出"真正导致结果缺失"的降级项（不含 quality 提示）。 */
const getDegradationWarnings = (status: {
  warnings?: PipelineWarning[];
  result?: any;
}): PipelineWarning[] => {
  const fromStatus = status.warnings || [];
  const fromResult = (status.result?.warnings || []) as PipelineWarning[];
  const merged = [...fromStatus, ...fromResult];
  const seen = new Set<string>();
  return merged.filter((w) => {
    if (!w || w.severity === 'quality') return false;
    const key = `${w.phase}-${w.code}-${w.message}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
};

/** 质量提示（severity=quality）：不改变任务状态，只做提醒。 */
const getQualityWarnings = (status: {
  warnings?: PipelineWarning[];
  result?: any;
}): PipelineWarning[] => {
  const merged = [...(status.warnings || []), ...((status.result?.warnings || []) as PipelineWarning[])];
  const seen = new Set<string>();
  return merged.filter((w) => {
    if (!w || w.severity !== 'quality') return false;
    const key = `${w.phase}-${w.code}-${w.message}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
};

/** P5：上下文裁剪类提示（只取 code 表明"上下文被裁剪"的条目）。 */
const getContextWarnings = (status: {
  warnings?: PipelineWarning[];
  result?: any;
}): string[] => {
  const merged = [...(status.warnings || []), ...((status.result?.warnings || []) as PipelineWarning[])];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const w of merged) {
    if (!w) continue;
    if (w.code !== 'context_truncated' && w.code !== 'document_compacted') continue;
    if (seen.has(w.code)) continue;
    seen.add(w.code);
    out.push(w.message);
  }
  return out;
};

const PIPELINE_STAGES = [
  { stage: 1, label: '需求拆分' },
  { stage: 2, label: '测试点生成' },
  { stage: 3, label: '用例设计' },
  { stage: 4, label: '用例优化' },
] as const;

interface TestCaseGeneratorProps {
  selectedSession: Session | null;
  modules: Module[];
  selectedModule: number | string;
  requirement: string;
  loading: boolean;
  onRequirementChange: (value: string) => void;
  historyPromptRefreshKey?: number;
  /** 当检测到未配置 API Key 或模型时触发，用于弹出设置窗口 */
  onRequireConfig?: () => void;
}

const TestCaseGenerator: React.FC<TestCaseGeneratorProps> = ({
  selectedSession,
  modules,
  selectedModule,
  requirement,
  loading,
  onRequirementChange,
  onRequireConfig,
}) => {
  const textAreaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  // 4阶段 Pipeline 状态
  const [pipelineTaskId, setPipelineTaskId] = useState<string | null>(null);
  const [pipelineStatus, setPipelineStatus] = useState<{
    status: string;
    current_phase: number;
    phase_name: string;
    progress: number;
    message: string;
    result: any;
    error: string | null;
    /** P0 失败可见性：可继续的降级（批次失败/去重降级等） */
    warnings?: PipelineWarning[];
  } | null>(null);
  const [pipelinePolling, setPipelinePolling] = useState(false);
  // Pipeline 阶段按顺序依赖，复选框始终表示从第 1 阶段执行到所选阶段。
  const [selectedStages, setSelectedStages] = useState<number[]>([1, 2, 3, 4]);

  const handleStageToggle = (stage: number, checked: boolean) => {
    setSelectedStages(previous => checked
      ? PIPELINE_STAGES.filter(item => item.stage <= stage).map(item => item.stage)
      : previous.filter(item => item < stage));
  };

  const [historyOpen, setHistoryOpen] = useState(false);

  const handleSelectPrompt = (content: string) => {
    onRequirementChange(content);
  };

  const handlePromptsChange = () => {};

  // Pipeline 进度轮询
  useEffect(() => {
    if (!pipelineTaskId || !pipelinePolling) return;

    const pollInterval = setInterval(async () => {
      try {
        const { testcaseApi } = await import('../../services/api');
        const res = await testcaseApi.getPipelineStatus(pipelineTaskId);
        if (res.code === 200 && res.data) {
          setPipelineStatus(res.data);

          if (res.data.status === 'completed' || res.data.status === 'partial' || res.data.status === 'failed') {
            setPipelinePolling(false);
            clearInterval(pollInterval);

            if (res.data.status === 'completed' || res.data.status === 'partial') {
              // P0 失败可见性：partial 表示"生成完成但有降级"，必须如实提示用户
              const degradations = getDegradationWarnings(res.data);
              if (degradations.length > 0) {
                antMessage.warning(`深度生成完成，但有 ${degradations.length} 项降级（部分结果可能缺失），详见面板`);
              } else {
                antMessage.success('深度生成完成！');
              }
              // 通过自定义事件通知父组件刷新测试用例列表
              window.dispatchEvent(new CustomEvent('pipeline-completed', {
                detail: { sessionId: selectedSession?.id, result: res.data.result }
              }));
              // 生成成功后清空已选文件，避免重复上传
              setSelectedFile(null);
              if (fileInputRef.current) fileInputRef.current.value = '';
            } else if (res.data.status === 'failed') {
              antMessage.error(`深度生成失败：${res.data.error || res.data.message}`);
            }
          }
        }
      } catch (e) {
        console.error('Poll pipeline status failed:', e);
      }
    }, 2000);

    return () => clearInterval(pollInterval);
  }, [pipelineTaskId, pipelinePolling, selectedSession?.id]);

  const handleRequirementInputChange = (value: string) => {
    onRequirementChange(value);
  };

  // ===== 引用需求（按需求文档的解析结构选择章节，正文填入输入框）=====
  const [requirementDocs, setRequirementDocs] = useState<RequirementDoc[]>([]);
  const [filledKey, setFilledKey] = useState<string | null>(null);
  const [loadingReqDocs, setLoadingReqDocs] = useState(false);

  // 引用需求只取当前会话 + 当前模块的需求文档，不跨会话/模块
  const loadRequirementDocs = useCallback(async () => {
    if (!selectedSession?.id) {
      setRequirementDocs([]);
      return;
    }
    setLoadingReqDocs(true);
    try {
      const { requirementDocApi } = await import('../../services/api');
      const filters: { session_id: number; module_id?: number } = { session_id: selectedSession.id };
      if (selectedModule && selectedModule !== 0 && Number.isFinite(Number(selectedModule))) {
        filters.module_id = Number(selectedModule);
      }
      const res = await requirementDocApi.list(filters);
      if (res.code === 200 && res.data) {
        // 只列需求文档，排除生成历史（source_type=generation）
        setRequirementDocs(res.data.filter((d) => d.source_type !== 'generation'));
      }
    } catch (e) {
      console.error('加载需求文档失败', e);
    } finally {
      setLoadingReqDocs(false);
    }
  }, [selectedSession?.id, selectedModule]);

  useEffect(() => {
    loadRequirementDocs();
    setFilledKey(null); // 会话/模块切换后清空已选引用，避免指向不存在的节点
  }, [loadRequirementDocs]);

  // 解析结构章节填充：value = `${docId}::${sectionPath}`（sectionPath 为 '__all__' 时填整篇）
  const handleFillRequirement = (val?: string) => {
    setFilledKey(val ?? null);
    if (!val) return;
    const sep = val.indexOf('::');
    if (sep < 0) return;
    const docId = Number(val.slice(0, sep));
    const secPath = val.slice(sep + 2);
    const doc = requirementDocs.find((d) => d.id === docId);
    if (!doc) return;
    if (secPath === '__all__') {
      onRequirementChange((doc.content_snapshot || '').trim());
      return;
    }
    const sorted = [...(doc.structured || [])].sort((a, b) => a.order_index - b.order_index);
    const sectionOf = (it: RequirementItem) => (it.section || '').trim();
    // 选中的章节 + 其全部子章节，按文档顺序重建带子标题的正文
    const descendants: string[] = [];
    for (const it of sorted) {
      const sPath = sectionOf(it);
      if (sPath.startsWith(secPath + ' / ') && !descendants.includes(sPath)) descendants.push(sPath);
    }
    const bodyOf = (path: string) => sorted
      .filter((it) => sectionOf(it) === path)
      .map((it) => (it.content || '').trim())
      .filter(Boolean)
      .join('\n\n');
    let text = bodyOf(secPath);
    for (const dPath of descendants) {
      const body = bodyOf(dPath);
      if (!body) continue;
      const rel = dPath.slice(secPath.length + 3); // 去掉 "父路径 / " 前缀
      text += (text ? '\n\n' : '') + `#### ${rel}\n\n${body}`;
    }
    onRequirementChange((`## ${secPath}\n\n${text}`).trim());
  };

  // 下拉树：文档根节点（默认收起）→ 解析结构层级章节，与需求管理大纲一致
  const refTreeData = useMemo(() => requirementDocs.map((d) => {
    interface RefTreeNode {
      title: string;
      value: string;
      selectable: boolean;
      children?: RefTreeNode[];
    }
    const nodeByPath = new Map<string, RefTreeNode>();
    const roots: RefTreeNode[] = [];
    const ensureNode = (path: string, parts: string[]): RefTreeNode => {
      const existing = nodeByPath.get(path);
      if (existing) return existing;
      const depth = parts.length;
      const parentPath = depth > 1 ? parts.slice(0, depth - 1).join(' / ') : '';
      const parent = depth > 1 ? ensureNode(parentPath, parts.slice(0, depth - 1)) : null;
      const node: RefTreeNode = { title: parts[depth - 1], value: `${d.id}::${path}`, selectable: true, children: [] };
      nodeByPath.set(path, node);
      if (parent && parent.children) parent.children.push(node);
      else roots.push(node);
      return node;
    };
    const sorted = [...(d.structured || [])].sort((a, b) => a.order_index - b.order_index);
    for (const it of sorted) {
      const sec = (it.section || '').trim();
      if (!sec) continue;
      ensureNode(sec, sec.split(' / ').filter(Boolean));
    }
    // 空子级置空，避免出现无内容却带展开箭头的节点
    const prune = (nodes: RefTreeNode[]): RefTreeNode[] =>
      nodes.map((n) => (n.children && n.children.length > 0
        ? { title: n.title, value: n.value, selectable: n.selectable, children: prune(n.children) }
        : { title: n.title, value: n.value, selectable: n.selectable }));
    return {
      title: `${d.title || '未命名需求'}（v${d.version}）`,
      value: `${d.id}::__doc__`,
      selectable: false,
      children: [
        { title: '整篇文档', value: `${d.id}::__all__`, selectable: true },
        ...prune(roots),
      ],
    };
  }), [requirementDocs]);

  const handleDeepGenerate = async () => {
    if (!selectedSession || (!requirement.trim() && !selectedFile)) return;
    if (selectedStages.length === 0) {
      antMessage.warning('请至少选择一个生成阶段');
      return;
    }

    setPipelinePolling(true);
    setPipelineStatus({
      status: 'running',
      current_phase: 0,
      phase_name: '初始化',
      progress: 0,
      message: '正在启动深度生成...',
      result: null,
      error: null,
    });

    try {
      const { testcaseApi } = await import('../../services/api');
      const res = await testcaseApi.generateV2(selectedSession.id, requirement.trim(), {
        moduleId: selectedModule,
        file: selectedFile,
        pipelineMode: selectedStages.length === 4 ? 'auto' : 'manual',
        stopAfterStage: Math.max(...selectedStages) as 1 | 2 | 3 | 4,
      });
      if (res.code === 200 && res.data?.task_id) {
        setPipelineTaskId(res.data.task_id);
      } else {
        throw new Error(res.message || '启动失败');
      }
    } catch (e: any) {
      console.error('Deep generate failed:', e);
      antMessage.error('深度生成启动失败：' + (e?.message || '未知错误'));
      setPipelinePolling(false);
      setPipelineStatus(null);
    }
  };

  const handleGenerateWithPreview = async () => {
    // 检查是否已配置 API Key 和模型
    try {
      const { configApi } = await import('../../services/api');
      const configRes = await configApi.getConfig();
      if (configRes.code === 200 && configRes.data) {
        const cfg = configRes.data;
        const modelType = cfg.model_type || 'api';
        let configValid = true;
        if (modelType === 'api') {
          if (!cfg.api_key || !cfg.api_key.trim()) {
            configValid = false;
          }
          if (!cfg.api_model || !cfg.api_model.trim()) {
            configValid = false;
          }
        } else if (modelType === 'ollama') {
          if (!cfg.ollama_url || !cfg.ollama_url.trim()) {
            configValid = false;
          }
          if (!cfg.ollama_model || !cfg.ollama_model.trim()) {
            configValid = false;
          }
        }
        if (!configValid) {
          antMessage.warning('请先配置 API Key 和模型');
          onRequireConfig?.();
          return;
        }
      } else {
        antMessage.warning('请先配置 API Key 和模型');
        onRequireConfig?.();
        return;
      }
    } catch (e) {
      console.error('Check config failed:', e);
      antMessage.warning('请先配置 API Key 和模型');
      onRequireConfig?.();
      return;
    }
    handleDeepGenerate();
  };

  const moduleName = selectedModule
    ? modules.find(m => m.id === selectedModule)?.module_name || '无'
    : '无';


  return (
    <div className="tcg-container">
      <div className="tcg-main">
        {!selectedSession ? (
          <div className="tcg-card">
            <div className="tcg-empty">
              <MessageOutlined className="tcg-empty-icon" />
              <span className="tcg-empty-text">请先选择或创建会话</span>
            </div>
          </div>
        ) : (
          <div className="tcg-card">
            <div className="tcg-card-header">
              <BulbOutlined style={{ color: 'var(--color-primary)' }} />
              生成测试用例
              <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--color-text-tertiary)', fontWeight: 400 }}>
                4阶段深度生成：需求拆分→测试点→用例设计→优化
              </span>
            </div>
            <div className="tcg-card-body" style={{ display: 'flex', flexDirection: 'column' }}>
              {/* Context Info */}
              <div className="tcg-context-bar">
                <div className="tcg-context-item">
                  <span className="tcg-context-label">会话</span>
                  <span className="tcg-context-value">{selectedSession.name}</span>
                </div>
                <div className="tcg-context-divider" />
                <div className="tcg-context-item">
                  <span className="tcg-context-label">模块</span>
                  <span className="tcg-context-value">{moduleName}</span>
                </div>
              </div>

              {/* Requirement Input */}
              <div className="tcg-requirement-section">
                <div className="tcg-requirement-label">
                  <FileTextOutlined style={{ color: 'var(--color-text-tertiary)' }} />
                  需求描述
                </div>
                <div className="tcg-file-upload-row">
                  <input
                    type="file"
                    accept=".docx,.pdf,.txt,.md"
                    style={{ display: 'none' }}
                    ref={fileInputRef}
                    onChange={(e) => {
                      const f = e.target.files && e.target.files[0];
                      setSelectedFile(f || null);
                    }}
                  />
                  <button
                    type="button"
                    className="tcg-smart-btn"
                    onClick={() => fileInputRef.current?.click()}
                  >
                    <UploadOutlined style={{ fontSize: 12 }} />
                    上传需求文件
                  </button>
                  {selectedFile && (
                    <span className="tcg-file-chip">
                      <FileTextOutlined />
                      <span className="tcg-file-name" title={selectedFile.name}>{selectedFile.name}</span>
                      <button
                        type="button"
                        className="tcg-file-remove"
                        onClick={() => {
                          setSelectedFile(null);
                          if (fileInputRef.current) fileInputRef.current.value = '';
                        }}
                        title="移除文件"
                      >
                        <CloseCircleOutlined />
                      </button>
                    </span>
                  )}
                  <span className="tcg-file-hint">支持 .docx / .txt / .md，文件内容作为生成主体（文本框文字作补充）</span>
                </div>
                <div style={{ display: 'flex', gap: 8, marginTop: 8, marginBottom: 12, alignItems: 'flex-start' }}>
                  {/* Chrome 会把此搜索框误判为登录用户名框并弹密码填充提示；
                      放一对屏幕外的诱饵输入框吸收该判定（业界通用做法） */}
                  <input
                    type="text"
                    name="tcg-decoy-username"
                    autoComplete="off"
                    tabIndex={-1}
                    aria-hidden="true"
                    style={{ position: 'absolute', left: -9999, top: 0, width: 1, height: 1, opacity: 0, border: 'none' }}
                  />
                  <input
                    type="password"
                    name="tcg-decoy-password"
                    autoComplete="new-password"
                    tabIndex={-1}
                    aria-hidden="true"
                    style={{ position: 'absolute', left: -9999, top: 0, width: 1, height: 1, opacity: 0, border: 'none' }}
                  />
                  <TreeSelect
                    showSearch
                    allowClear
                    style={{ flex: 1, minWidth: 0 }}
                    value={filledKey ?? undefined}
                    treeData={refTreeData}
                    treeNodeFilterProp="title"
                    placeholder="引用需求：展开文档按解析结构选章节，正文填入下方输入框（可选）"
                    onChange={(v) => handleFillRequirement(v as string | undefined)}
                    disabled={pipelinePolling || loading || loadingReqDocs}
                    loading={loadingReqDocs}
                    treeDefaultExpandAll={false}
                    notFoundContent={loadingReqDocs ? <Spin size="small" /> : '暂无可引用的需求文档（先在需求管理登记）'}
                  />
                </div>
                <div className="tcg-textarea-wrapper">
                  <Input.TextArea
                    ref={textAreaRef}
                    placeholder="请输入需求描述... 已关联的 API 接口将作为参考，生成包含可执行 api_call 步骤的测试用例。"
                    value={requirement}
                    onChange={(e) => handleRequirementInputChange(e.target.value)}
                    className="tcg-textarea"
                  />
                </div>
              </div>
              <div className="tcg-mode-switch">
                <span className="tcg-mode-switch-label">
                  <ExperimentOutlined style={{ color: 'var(--color-primary)' }} />
                  生成阶段
                </span>
                <div className="tcg-stage-checkboxes">
                  {PIPELINE_STAGES.map(({ stage, label }) => (
                    <Checkbox
                      key={stage}
                      checked={selectedStages.includes(stage)}
                      onChange={(event) => handleStageToggle(stage, event.target.checked)}
                      disabled={pipelinePolling || loading || (stage > 1 && !selectedStages.includes(stage - 1))}
                    >
                      {label}
                    </Checkbox>
                  ))}
                </div>
                <Tag color={selectedStages.length === 4 ? 'green' : 'blue'} style={{ margin: 0 }}>
                  {selectedStages.length ? `执行至阶段 ${Math.max(...selectedStages)}` : '未选择阶段'}
                </Tag>
              </div>

              {/* Generate Button */}
              <button
                className={`tcg-generate-btn ${loading || pipelinePolling ? 'is-loading' : ''}`}
                onClick={handleGenerateWithPreview}
                disabled={(!requirement.trim() && !selectedFile) || selectedStages.length === 0 || loading || pipelinePolling}
              >
                {loading || pipelinePolling ? (
                  <>
                    <Spin size="small" style={{ color: 'var(--color-primary)' }} />
                    {pipelinePolling ? `深度生成中... ${pipelineStatus?.progress || 0}%` : 'AI 正在生成中...'}
                  </>
                ) : (
                  <>
                    <ExperimentOutlined />
                    {selectedStages.length === 0
                      ? '请选择生成阶段'
                      : selectedStages.length < 4
                        ? `深度生成（执行至阶段 ${Math.max(...selectedStages)}）`
                        : '深度生成（4阶段）'}
                  </>
                )}
              </button>

              {/* Pipeline 进度面板
                  P0 修复：partial 状态在轮询结束后仍保留展示，
                  否则 setPipelinePolling(false) 会卸载面板，降级明细永远不可见 */}
              {pipelineStatus && (pipelinePolling || pipelineStatus.status === 'partial') && (
                <div className="tcg-pipeline-panel" style={{
                  marginTop: 12,
                  padding: '12px 16px',
                  background: 'var(--color-bg-elevated)',
                  borderRadius: 8,
                  border: '1px solid var(--color-border-secondary)',
                }}>
                  {/* 横向紧凑四阶段，避免卡片高度被撑高出现滚动条 */}
                  <Steps
                    current={pipelineStatus.current_phase - 1}
                    size="small"
                    direction="horizontal"
                    style={{ marginBottom: 4 }}
                    items={[
                      {
                        title: '需求拆分',
                        icon: pipelineStatus.current_phase === 1
                          ? <LoadingOutlined />
                          : pipelineStatus.current_phase > 1
                            ? <CheckCircleOutlined style={{ color: '#52c41a' }} />
                            : undefined,
                      },
                      {
                        title: '测试点生成',
                        icon: pipelineStatus.current_phase === 2
                          ? <LoadingOutlined />
                          : pipelineStatus.current_phase > 2
                            ? <CheckCircleOutlined style={{ color: '#52c41a' }} />
                            : undefined,
                      },
                      {
                        title: '用例设计',
                        icon: pipelineStatus.current_phase === 3
                          ? <LoadingOutlined />
                          : pipelineStatus.current_phase > 3
                            ? <CheckCircleOutlined style={{ color: '#52c41a' }} />
                            : undefined,
                      },
                      {
                        title: '用例优化',
                        icon: pipelineStatus.current_phase === 4
                          ? <LoadingOutlined />
                          : pipelineStatus.current_phase > 4
                            ? <CheckCircleOutlined style={{ color: '#52c41a' }} />
                            : undefined,
                      },
                    ]}
                  />
                  {/* 当前阶段提示信息，单行展示 */}
                  <div className="tcg-pipeline-msg">
                    {pipelineStatus.message}
                  </div>
                  <Progress
                    percent={pipelineStatus.progress}
                    size="small"
                    status={pipelineStatus.status === 'failed' ? 'exception' : 'active'}
                    strokeColor={pipelineStatus.status === 'failed' ? '#ff4d4f' : undefined}
                  />
                  {pipelineStatus.status === 'failed' && pipelineStatus.error && (
                    <div style={{ marginTop: 8, color: '#ff4d4f', fontSize: 12 }}>
                      <CloseCircleOutlined /> {pipelineStatus.error}
                    </div>
                  )}
                  {(pipelineStatus.status === 'completed' || pipelineStatus.status === 'partial') && pipelineStatus.result && (
                    <div style={{ marginTop: 8, fontSize: 12, color: 'var(--color-text-tertiary)' }}>
                      {pipelineStatus.status === 'partial'
                        ? <CloseCircleOutlined style={{ color: '#fa8c16', marginRight: 4 }} />
                        : <CheckCircleOutlined style={{ color: '#52c41a', marginRight: 4 }} />}
                      生成完成：{pipelineStatus.result.requirement_points_count} 个需求点，
                      {pipelineStatus.result.test_points_count} 个测试点，
                      {pipelineStatus.result.test_cases_count} 个测试用例
                    </div>
                  )}
                  {/* B6-C：覆盖审计（链路完整率 + 缺口） */}
                  {pipelineStatus.result?.coverage && (
                    <div style={{ marginTop: 6, fontSize: 12, color: 'var(--color-text-tertiary)' }}>
                      <CheckCircleOutlined style={{ marginRight: 4 }} />
                      覆盖：链路完整率 {(pipelineStatus.result.coverage.link_completeness * 100).toFixed(0)}%
                      {pipelineStatus.result.coverage.gap_counts?.no_testpoints > 0 &&
                        `，${pipelineStatus.result.coverage.gap_counts.no_testpoints} 个需求点无测试点`}
                      {pipelineStatus.result.coverage.gap_counts?.no_testcases > 0 &&
                        `，${pipelineStatus.result.coverage.gap_counts.no_testcases} 个测试点无用例`}
                    </div>
                  )}
                  {/* B6：自动补齐必填字段的提示 */}
                  {pipelineStatus.result?.required_fields_autofilled > 0 && (
                    <div style={{ marginTop: 6, fontSize: 12, color: 'var(--color-text-tertiary)' }}>
                      <CheckCircleOutlined style={{ marginRight: 4 }} />
                      自动补齐了 {pipelineStatus.result.required_fields_autofilled} 处缺失的接口必填字段（占位值，建议核对）
                    </div>
                  )}
                  {/* B6：本次生成的质量指标（纯规则计算） */}
                  {pipelineStatus.result?.quality && (
                    <div style={{ marginTop: 6, fontSize: 12, color: 'var(--color-text-tertiary)' }}>
                      <CheckCircleOutlined style={{ marginRight: 4 }} />
                      质量：完整率 {(pipelineStatus.result.quality.completeness_rate * 100).toFixed(0)}%、结构
                      {(pipelineStatus.result.quality.structure_ok_rate * 100).toFixed(0)}%、断言
                      {(pipelineStatus.result.quality.assertion_coverage_rate * 100).toFixed(0)}%、重复
                      {(pipelineStatus.result.quality.duplicate_rate * 100).toFixed(1)}%
                      {pipelineStatus.result.quality.consistency_checked > 0 &&
                        `、Schema 一致性 ${(pipelineStatus.result.quality.consistency_pass_rate * 100).toFixed(0)}%`}
                    </div>
                  )}
                  {/* P5：上下文裁剪提示（内容可能被截断） */}
                  {getContextWarnings(pipelineStatus).length > 0 && (
                    <div style={{ marginTop: 6, fontSize: 12, color: '#fa8c16' }}>
                      <BulbOutlined style={{ marginRight: 4 }} />
                      上下文已裁剪：{getContextWarnings(pipelineStatus).join('；')}
                    </div>
                  )}
                  {/* P3：执行验证结果（生成后真实执行接口用例的通过率） */}
                  {pipelineStatus.result?.verification && pipelineStatus.result.verification.executed > 0 && (
                    <div style={{ marginTop: 6, fontSize: 12, color: 'var(--color-text-tertiary)' }}>
                      <ExperimentOutlined style={{ marginRight: 4 }} />
                      执行验证：通过 {pipelineStatus.result.verification.passed} / 执行 {pipelineStatus.result.verification.executed}
                      {pipelineStatus.result.verification.failed > 0 &&
                        `（失败 ${pipelineStatus.result.verification.failed}）`}
                      {pipelineStatus.result.verification.after_repair &&
                        ` | 重修后通过 ${pipelineStatus.result.verification.after_repair.passed}`}
                    </div>
                  )}
                  {/* P0 失败可见性：降级明细（批次失败/去重降级等）必须让用户看见 */}
                  {getDegradationWarnings(pipelineStatus).length > 0 && (
                    <div
                      className="tcg-pipeline-warnings"
                      style={{
                        marginTop: 8,
                        padding: '8px 10px',
                        borderRadius: 6,
                        background: 'rgba(250, 140, 22, 0.08)',
                        border: '1px solid rgba(250, 140, 22, 0.35)',
                        maxHeight: 150,
                        overflowY: 'auto',
                      }}
                    >
                      <div style={{ fontSize: 12, color: '#fa8c16', fontWeight: 600, marginBottom: 4 }}>
                        <CloseCircleOutlined style={{ marginRight: 4 }} />
                        {getDegradationWarnings(pipelineStatus).length} 项降级（结果可能不完整）
                      </div>
                      {getDegradationWarnings(pipelineStatus).map((w, i) => (
                        <div key={`${w.code}-${i}`} style={{ fontSize: 12, color: 'var(--color-text-secondary)', lineHeight: 1.6 }}>
                          · {w.phase_name ? `[阶段${w.phase} ${w.phase_name}]` : ''} {w.message}
                        </div>
                      ))}
                    </div>
                  )}
                  {/* P0：质量提示（不改变任务状态，仅提醒） */}
                  {getQualityWarnings(pipelineStatus).length > 0 && (
                    <div style={{ marginTop: 8, fontSize: 12, color: 'var(--color-text-tertiary)' }}>
                      <BulbOutlined style={{ marginRight: 4 }} />
                      质量提示：{getQualityWarnings(pipelineStatus).map((w) => w.message).join('；')}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}


      </div>

      <Button
        style={{ position: 'absolute', top: 12, right: 16, zIndex: 10 }}
        icon={<HistoryOutlined />}
        onClick={() => setHistoryOpen(true)}
      >
        历史
      </Button>

      <GenerationHistoryModal
        visible={historyOpen}
        moduleId={selectedModule}
        onCancel={() => setHistoryOpen(false)}
        onSelectPrompt={handleSelectPrompt}
        onDocsChange={handlePromptsChange}
      />
    </div>
  );
};

export default TestCaseGenerator;
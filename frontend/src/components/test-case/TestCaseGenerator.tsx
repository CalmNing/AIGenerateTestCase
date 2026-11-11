import React, { useRef, useState, useEffect } from 'react';
import { Button, Checkbox, Input, Select, Spin, Tag, message as antMessage, Modal, Progress, Steps } from 'antd';
import {
  MessageOutlined, BulbOutlined,
  FileTextOutlined, ExperimentOutlined, UploadOutlined,
  CheckCircleOutlined, LoadingOutlined, CloseCircleOutlined, FolderOpenOutlined, HistoryOutlined,
} from '@ant-design/icons';
import { Session, Module } from '../../types';
import GenerationHistoryModal from '../requirement/GenerationHistoryModal';
import './TestCaseGenerator.css';

const FEISHU_URL_RE = /https?:\/\/[a-zA-Z0-9-]+\.feishu\.cn\/(?:wiki|docx)\/[\w-]+/i;

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

const extractFeishuSections = (content: string): { label: string; value: string }[] => {
  const sections = new Map<string, { label: string; value: string }>();
  content.split(/\r?\n/).forEach((rawLine) => {
    const line = rawLine.trim();
    if (!line) return;

    const markdown = line.match(/^(#{1,6})\s+(.+?)\s*#*$/);
    const numbered = line.match(/^([一二三四五六七八九十百]+[、.．]\s*.+|\d+(?:\.\d+)*(?:[、.．]|\s+)\s*.+)$/);
    const chapter = line.match(/^第[一二三四五六七八九十百\d]+(?:章|节|部分)\s*.+$/);
    if (!markdown && !numbered && !chapter) return;

    const value = line;
    const level = markdown ? markdown[1].length :
      (/^[一二三四五六七八九十百]+[、.．]/.test(line) || /^第[一二三四五六七八九十百\d]+(?:章|部分)/.test(line)
        ? 1
        : (line.match(/^\d+(?:\.\d+)*/)?.[0].match(/\./g)?.length || 0) + 2);
    sections.set(value, { label: `${'　'.repeat(Math.max(0, level - 1))}${value}`, value });
  });
  return Array.from(sections.values());
};

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

const getFeishuHeading = (line: string): { level: number; title: string } | null => {
  const value = line.trim();
  const markdown = value.match(/^(#{1,6})\s+(.+?)\s*#*$/);
  if (markdown) return { level: markdown[1].length, title: markdown[2].trim() };

  const chineseNumber = value.match(/^([一二三四五六七八九十百]+)[、.．]\s*(.+?)\s*$/);
  if (chineseNumber) return { level: 1, title: chineseNumber[2].trim() };

  const arabicNumber = value.match(/^(\d+(?:\.\d+)*)(?:[、.．]|\s+)\s*(.+?)\s*$/);
  if (arabicNumber) return { level: arabicNumber[1].split('.').length + 1, title: arabicNumber[2].trim() };

  const chapter = value.match(/^第[一二三四五六七八九十百\d]+(章|节|部分)\s*(.+?)\s*$/);
  if (chapter) return { level: chapter[1] === '节' ? 2 : 1, title: chapter[2].trim() };
  return null;
};

const extractPreviewSection = (content: string, selectedHeading: string): string => {
  if (!selectedHeading) return content;
  const lines = content.split(/\r?\n/);
  const start = lines.findIndex(line => line.trim() === selectedHeading.trim());
  if (start < 0) return content;

  const level = getFeishuHeading(lines[start])?.level;
  if (level === undefined) return content;
  let end = lines.length;
  for (let index = start + 1; index < lines.length; index += 1) {
    const heading = getFeishuHeading(lines[index]);
    if (heading && heading.level <= level) {
      end = index;
      break;
    }
  }
  return lines.slice(start, end).join('\n').trim();
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
  const [feishuSection, setFeishuSection] = useState('');
  const [feishuSections, setFeishuSections] = useState<{ label: string; value: string }[]>([]);
  const [loadingFeishuSections, setLoadingFeishuSections] = useState(false);
  const [loadedFeishuUrl, setLoadedFeishuUrl] = useState('');
  const [feishuPreviewContent, setFeishuPreviewContent] = useState('');
  const [feishuPreviewVisible, setFeishuPreviewVisible] = useState(false);
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
    const nextUrl = value.match(FEISHU_URL_RE)?.[0] || '';
    if (loadedFeishuUrl && nextUrl !== loadedFeishuUrl) {
      setFeishuSections([]);
      setFeishuSection('');
      setFeishuPreviewContent('');
      setFeishuPreviewVisible(false);
      setLoadedFeishuUrl('');
    }
    onRequirementChange(value);
  };

  const handleLoadFeishuSections = async () => {
    const url = requirement.match(FEISHU_URL_RE)?.[0];
    if (!url) {
      antMessage.warning('请先在需求描述中填写飞书文档链接');
      return;
    }

    setLoadingFeishuSections(true);
    try {
      const { documentApi } = await import('../../services/api');
      const res = await documentApi.getFeishuContent(url);
      if (res.code !== 200 || !res.data?.text) {
        throw new Error(res.message || '读取飞书文档失败');
      }
      const sections = extractFeishuSections(res.data.text);
      setFeishuSections(sections);
      setFeishuPreviewContent(res.data.text);
      setFeishuPreviewVisible(true);
      setLoadedFeishuUrl(url);
      setFeishuSection('');
      if (!sections.length) {
        antMessage.info('已解析文档，但未识别到编号或 Markdown 标题');
      }
    } catch (e: any) {
      console.error('Load Feishu sections failed:', e);
      antMessage.error('读取飞书目录失败：' + (e?.response?.data?.message || e?.message || '未知错误'));
    } finally {
      setLoadingFeishuSections(false);
    }
  };

  const handleDeepGenerate = async () => {
    if (!selectedSession || (!requirement.trim() && !selectedFile)) return;
    if (selectedStages.length === 0) {
      antMessage.warning('请至少选择一个生成阶段');
      return;
    }
    if (feishuSection.trim() && !/https?:\/\/[a-zA-Z0-9-]+\.feishu\.cn\/(?:wiki|docx)\/[\w-]+/i.test(requirement)) {
      antMessage.warning('填写飞书目录标题时，请在需求描述中提供对应的飞书文档链接');
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
        feishuSection,
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

  const previewContent = extractPreviewSection(feishuPreviewContent, feishuSection);

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
                <div className="tcg-textarea-wrapper">
                  <Input.TextArea
                    ref={textAreaRef}
                    placeholder="请输入需求描述... 已关联的 API 接口将作为参考，生成包含可执行 api_call 步骤的测试用例。"
                    value={requirement}
                    onChange={(e) => handleRequirementInputChange(e.target.value)}
                    className="tcg-textarea"
                  />
                </div>
                <div style={{ display: 'flex', gap: 8, marginTop: 8, alignItems: 'flex-start' }}>
                  <Select
                    showSearch
                    allowClear
                    style={{ flex: 1, minWidth: 0 }}
                    value={feishuSection || undefined}
                    placeholder="从飞书文档选择目录（可不选）"
                    options={feishuSections.map(section => ({ label: section.label, value: section.value }))}
                    optionFilterProp="label"
                    onChange={(value) => setFeishuSection(value || '')}
                    disabled={pipelinePolling || loading || loadingFeishuSections}
                    notFoundContent={feishuSections.length ? '无匹配目录' : '请先读取目录'}
                  />
                  <Button
                    icon={<FolderOpenOutlined />}
                    onClick={handleLoadFeishuSections}
                    loading={loadingFeishuSections}
                    disabled={pipelinePolling || loading}
                  >
                    解析并预览
                  </Button>
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

        {/* Feishu parsed document preview */}
        <Modal
          title="飞书文档解析预览"
          open={feishuPreviewVisible}
          onCancel={() => setFeishuPreviewVisible(false)}
          width={900}
          footer={[
            <Button key="close" onClick={() => setFeishuPreviewVisible(false)}>
              关闭
            </Button>,
            <Button key="use-section" type="primary" onClick={() => setFeishuPreviewVisible(false)}>
              使用所选目录
            </Button>,
          ]}
        >
          <div style={{ marginBottom: 12, color: 'var(--color-text-secondary)', fontSize: 13 }}>
            {feishuSection
              ? `当前预览目录：${feishuSection}，显示 ${previewContent.length.toLocaleString()} / ${feishuPreviewContent.length.toLocaleString()} 个字符。`
              : `已解析 ${feishuPreviewContent.length.toLocaleString()} 个字符，识别到 ${feishuSections.length} 个目录。`}
            此操作仅读取文档，不会生成用例。
          </div>
          {feishuSections.length > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
              <span style={{ flexShrink: 0, fontWeight: 500 }}>生成目录</span>
              <Select
                showSearch
                allowClear
                style={{ flex: 1 }}
                value={feishuSection || undefined}
                placeholder="可选：选择章节作为生成范围"
                options={feishuSections.map(section => ({ label: section.label, value: section.value }))}
                optionFilterProp="label"
                onChange={(value) => setFeishuSection(value || '')}
              />
            </div>
          )}
          <pre style={{
            margin: 0,
            padding: 14,
            maxHeight: '55vh',
            overflow: 'auto',
            whiteSpace: 'pre-wrap',
            overflowWrap: 'anywhere',
            border: '1px solid var(--color-border-secondary)',
            borderRadius: 4,
            background: 'var(--color-bg-layout)',
            color: 'var(--color-text)',
            fontFamily: 'inherit',
            fontSize: 13,
            lineHeight: 1.6,
          }}>
            {previewContent}
          </pre>
        </Modal>

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
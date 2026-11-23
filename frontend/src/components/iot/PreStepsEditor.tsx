import React, { useRef, useState } from 'react';
import { Button, Checkbox, Input, Select, Space, Switch, Tooltip, Empty, message } from 'antd';
import { PlusOutlined, MinusOutlined, QuestionCircleOutlined, PictureOutlined } from '@ant-design/icons';
import type { PreStep, PreStepKv, PreStepExtract } from '../../types';
import { imageBase64Api } from '../../services/api';

/**
 * 前置步骤配置面板。
 *
 * 两种步骤类型：
 * - 关联已有请求：从已保存的请求里选一个，执行时后端按 id 现取最新配置，
 *   那边改了这里不用同步；同样支持从响应提取变量；
 * - 自己配置：内联配置方法、URL、请求头、查询参数、请求体与提取规则。
 *
 * 参数统一在前置参数（页签）中设置，前置步骤不再单独配置参数注入。
 *
 * 产出的变量只在本次请求链路内生效（不写入环境参数），后续步骤与主请求
 * 可以用 {{var}} 引用。
 */
interface PreStepsEditorProps {
  steps: PreStep[];
  /** 已保存的请求（用于「关联已有请求」选择器） */
  savedRequests: Array<{ id: number; name: string; method: string; url: string; protocol?: string }>;
  onChange: (index: number, updates: Partial<PreStep>) => void;
  onRemove: (index: number) => void;
  onAdd: () => void;
  onAddExtract: (stepIndex: number) => void;
  onRemoveExtract: (stepIndex: number, extractIndex: number) => void;
  onExtractChange: (stepIndex: number, extractIndex: number, field: 'key' | 'jsonpath', value: string) => void;
}

const METHODS = ['GET', 'POST', 'PUT', 'DELETE', 'PATCH'];

/** 步骤右上角的小标签文案 */
const sourceLabel = (step: PreStep): string => {
  if (step.type === 'set_variable') return '设置变量';
  if (step.source === 'saved') return '关联请求';
  return '自定义';
};

const PreStepsEditor: React.FC<PreStepsEditorProps> = ({
  steps,
  savedRequests,
  onChange,
  onRemove,
  onAdd,
  onAddExtract,
  onRemoveExtract,
  onExtractChange,
}) => {
  const fileInputRef = useRef<HTMLInputElement>(null);
  /** 待转换图片所属的步骤下标（-1 表示没有） */
  const pendingStepRef = useRef<number>(-1);
  /** 正在上传图片的步骤下标（-1 表示没有） */
  const [uploadingStep, setUploadingStep] = useState<number>(-1);

  /** 选择本地图片 -> 转 base64 -> 填入该步骤的变量值（旧版 set_variable 兼容） */
  const handlePickImage = (stepIndex: number) => {
    const step = steps[stepIndex];
    if (!step) return;
    if (step.type !== 'set_variable') {
      message.warning('只有「设置变量」类型的步骤支持从图片生成 base64');
      return;
    }
    // 步骤下标记在 ref 里：file input 的 value 是文件路径，不能用来推步骤
    pendingStepRef.current = stepIndex;
    fileInputRef.current?.click();
  };

  /** 上传选中的图片并把 base64 写进变量值 */
  const handleImageSelected = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    const stepIndex = pendingStepRef.current;
    // 清空以便再次选择同一文件
    e.target.value = '';
    pendingStepRef.current = -1;
    if (!file || stepIndex < 0) return;

    setUploadingStep(stepIndex);
    try {
      const res = await imageBase64Api.toBase64(file, { compress: true, format: 'raw' });
      if (res.code === 200 && res.data?.base64) {
        onChange(stepIndex, { value: res.data.base64 });
        message.success('图片已转为 base64（' + res.data.mime + '，' + Math.round(res.data.fileSize / 1024) + 'KB）');
      } else {
        message.error(res.message || '图片转 base64 失败');
      }
    } catch (err: any) {
      console.error('Image to base64 failed:', err);
      message.error(err?.response?.data?.detail || '图片转 base64 失败');
    } finally {
      setUploadingStep(-1);
    }
  };

  /** 修改提取规则的非文本字段（类型 / 压缩 / 格式） */
  const handleExtractOptionChange = (
    stepIndex: number,
    extractIndex: number,
    updates: Record<string, unknown>
  ) => {
    const step = steps[stepIndex];
    if (!step) return;
    const extracts = [...(step.extracts || [])];
    extracts[extractIndex] = { ...extracts[extractIndex], ...updates };
    onChange(stepIndex, { extracts } as Partial<PreStep>);
  };

  /** 修改自定义请求的请求头 / 查询参数条目 */
  const handleKvChange = (
    stepIndex: number,
    field: 'headers' | 'params',
    kvIndex: number,
    kvField: 'key' | 'value' | 'description',
    value: string
  ) => {
    const step = steps[stepIndex];
    if (!step) return;
    const list = [...(step[field] || [])];
    list[kvIndex] = { ...list[kvIndex], [kvField]: value };
    onChange(stepIndex, { [field]: list } as Partial<PreStep>);
  };

  const handleKvRemove = (stepIndex: number, field: 'headers' | 'params', kvIndex: number) => {
    const step = steps[stepIndex];
    if (!step) return;
    const list = (step[field] || []).filter((_, i) => i !== kvIndex);
    onChange(stepIndex, { [field]: list } as Partial<PreStep>);
  };

  const handleKvAdd = (stepIndex: number, field: 'headers' | 'params') => {
    const step = steps[stepIndex];
    if (!step) return;
    const list = [...(step[field] || []), { key: '', value: '', enabled: true }];
    onChange(stepIndex, { [field]: list } as Partial<PreStep>);
  };

  const handleKvToggle = (stepIndex: number, field: 'headers' | 'params', kvIndex: number, enabled: boolean) => {
    const step = steps[stepIndex];
    if (!step) return;
    const list = [...(step[field] || [])];
    list[kvIndex] = { ...list[kvIndex], enabled };
    onChange(stepIndex, { [field]: list } as Partial<PreStep>);
  };

  /** 切换 request 步骤来源：清掉另一来源的配置 */
  const handleSourceChange = (stepIndex: number, source: 'custom' | 'saved') => {
    const step = steps[stepIndex];
    if (!step) return;
    if (source === 'saved') {
      onChange(stepIndex, {
        source,
        saved_request_id: undefined,
        method: undefined,
        url: undefined,
        headers: undefined,
        params: undefined,
        body: undefined,
      });
    } else {
      onChange(stepIndex, {
        source,
        saved_request_id: undefined,
        method: step.method || 'GET',
        url: step.url || '',
        headers: step.headers || [],
        params: step.params || [],
        body: step.body || '',
      });
    }
  };

  /** 请求头 / 查询参数 KV 编辑表（iot-kv-table 样式与主请求一致） */
  const renderKvEditor = (stepIndex: number, field: 'headers' | 'params', keyPlaceholder: string, valuePlaceholder: string) => {
    const step = steps[stepIndex];
    const items: PreStepKv[] = step?.[field] || [];
    const allEnabled = items.length > 0 && items.every(item => item.enabled !== false);
    return (
      <div className="iot-kv-table" style={{ marginTop: 4 }}>
        <div className="iot-kv-table-head">
          <Checkbox
            checked={allEnabled}
            indeterminate={!allEnabled && items.some(item => item.enabled !== false)}
            onChange={(e) => {
              const enabled = e.target.checked;
              const list = items.map(item => ({ ...item, enabled }));
              onChange(stepIndex, { [field]: list } as Partial<PreStep>);
            }}
          />
          <span>Key</span>
          <span>Value</span>
          <span />
        </div>
        {items.length === 0 && (
          <div className="iot-kv-table-empty">暂无内容，点击下方按钮添加</div>
        )}
        {items.map((item, kvIndex) => (
          <div className={'iot-kv-table-row' + (item.enabled === false ? ' is-disabled' : '')} key={kvIndex}>
            <Checkbox
              checked={item.enabled !== false}
              onChange={(e) => handleKvToggle(stepIndex, field, kvIndex, e.target.checked)}
            />
            <Input
              size="small"
              placeholder={keyPlaceholder}
              value={item.key}
              onChange={(e) => handleKvChange(stepIndex, field, kvIndex, 'key', e.target.value)}
              style={{ width: 180, flex: 1, minWidth: 0 }}
            />
            <div className="iot-kv-table-value">
              <Input
                size="small"
                placeholder={valuePlaceholder}
                value={item.value}
                onChange={(e) => handleKvChange(stepIndex, field, kvIndex, 'value', e.target.value)}
              />
            </div>
            <Button
              type="text"
              size="small"
              danger
              icon={<MinusOutlined />}
              onClick={() => handleKvRemove(stepIndex, field, kvIndex)}
            />
          </div>
        ))}
        <Button type="dashed" size="small" icon={<PlusOutlined />} onClick={() => handleKvAdd(stepIndex, field)}>
          添加
        </Button>
      </div>
    );
  };

  /** 请求体编辑（JSON 文本域） */
  const renderBodyEditor = (stepIndex: number) => {
    const step = steps[stepIndex];
    const value = typeof step?.body === 'string' ? step.body : step?.body != null ? JSON.stringify(step.body, null, 2) : '';
    return (
      <Input.TextArea
        size="small"
        autoSize={{ minRows: 3, maxRows: 10 }}
        placeholder={'请输入请求体（JSON 格式），支持 {{variable}} 变量'}
        value={value}
        onChange={(e) => onChange(stepIndex, { body: e.target.value })}
        style={{ fontFamily: 'monospace' }}
      />
    );
  };

  /** 提取规则编辑区（request 步骤共用） */
  const renderExtracts = (stepIndex: number) => {
    const step = steps[stepIndex];
    const extracts: PreStepExtract[] = step?.extracts || [];
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-tertiary)' }}>
          提取变量（从响应 JSON 中取值）
        </div>
        {extracts.length === 0 ? (
          <div style={{ fontSize: 12, color: 'var(--color-text-tertiary)' }}>
            未配置提取规则，该请求仅用于产生副作用
          </div>
        ) : (
          extracts.map((rule, ri) => {
            const isImage = rule.type === 'image_base64';
            return (
              <div key={ri} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <Space.Compact style={{ width: '100%' }}>
                  <Select
                    size="small"
                    value={rule.type || 'json'}
                    style={{ width: 130 }}
                    onChange={(v) => handleExtractOptionChange(stepIndex, ri, { type: v })}
                    options={[
                      { label: '普通值', value: 'json' },
                      { label: '图片 base64', value: 'image_base64' },
                    ]}
                  />
                  <Input
                    size="small"
                    placeholder="变量名"
                    value={rule.key}
                    onChange={(e) => onExtractChange(stepIndex, ri, 'key', e.target.value)}
                    style={{ width: '30%' }}
                  />
                  <Input
                    size="small"
                    placeholder="JSONPath，如 $.data.token"
                    value={rule.jsonpath}
                    onChange={(e) => onExtractChange(stepIndex, ri, 'jsonpath', e.target.value)}
                  />
                  <Button
                    size="small"
                    danger
                    icon={<MinusOutlined />}
                    onClick={() => onRemoveExtract(stepIndex, ri)}
                  />
                </Space.Compact>
                {isImage && (
                  <Space size="small" wrap>
                    <Select
                      size="small"
                      value={rule.format || 'raw'}
                      style={{ width: 150 }}
                      onChange={(v) => handleExtractOptionChange(stepIndex, ri, { format: v })}
                      options={[
                        { label: '输出：纯 base64', value: 'raw' },
                        { label: '输出：data URL', value: 'data_url' },
                      ]}
                    />
                    <Select
                      size="small"
                      value={rule.compress === false ? 'raw' : 'compress'}
                      style={{ width: 170 }}
                      onChange={(v) =>
                        handleExtractOptionChange(stepIndex, ri, { compress: v === 'compress' })
                      }
                      options={[
                        { label: '压缩并转 JPEG', value: 'compress' },
                        { label: '保持原图字节', value: 'raw' },
                      ]}
                    />
                    <span style={{ fontSize: 11, color: 'var(--color-text-tertiary)' }}>
                      从响应里取到的值按图片解析，取不到就跳过该变量
                    </span>
                  </Space>
                )}
              </div>
            );
          })
        )}
        <Button type="dashed" size="small" icon={<PlusOutlined />} onClick={() => onAddExtract(stepIndex)}>
          添加提取规则
        </Button>
      </div>
    );
  };

  const tooltipHelp = (
    <div style={{ fontSize: 12, lineHeight: 1.8 }}>
      <div>发主请求前先执行的步骤，用于准备变量（如登录取 token）</div>
      <div style={{ marginTop: 4, fontWeight: 600 }}>两种来源：</div>
      <div><code>关联请求</code> —— 选择一条已保存的请求，执行时取它的最新配置</div>
      <div><code>自己配置</code> —— 内联配置方法、地址、请求头、参数与请求体</div>
      <div style={{ marginTop: 4, fontWeight: 600 }}>JSONPath 示例：</div>
      <div><code>$.data.token</code> — 提取 data.token</div>
      <div><code>$.data.list[0].id</code> — 提取数组第一项的 id</div>
      <div style={{ marginTop: 4 }}>变量只在本次请求链路内生效，不会写入环境参数。</div>
      <div>参数的循环取值请在前置参数页签配置。</div>
    </div>
  );

  if (steps.length === 0) {
    return (
      <div className="iot-config-section">
        <div className="iot-config-section-hint">
          发主请求前先执行的前置步骤，支持关联已有请求或自己配置请求
          <Tooltip title={tooltipHelp} overlayClassName="iot-tooltip">
            <QuestionCircleOutlined style={{ color: 'var(--color-text-tertiary)', fontSize: 13 }} />
          </Tooltip>
        </div>
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <span>
              暂无前置步骤，<Button type="link" size="small" onClick={onAdd}>添加</Button>
            </span>
          }
        />
      </div>
    );
  }

  return (
    <div className="iot-config-section">
      <div className="iot-config-section-hint">
        发主请求前先执行的前置步骤，支持关联已有请求或自己配置请求
        <Tooltip title={tooltipHelp} overlayClassName="iot-tooltip">
          <QuestionCircleOutlined style={{ color: 'var(--color-text-tertiary)', fontSize: 13 }} />
        </Tooltip>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {steps.map((step, index) => {
          const isRequest = step.type === 'request';
          const isSaved = isRequest && step.source === 'saved';
          return (
            <div
              key={index}
              style={{
                border: '1px solid var(--color-border-light)',
                borderRadius: 8,
                padding: 12,
                background: 'var(--color-bg-container, #fff)',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-tertiary)' }}>
                  步骤 {index + 1}
                </span>
                {isRequest && (
                  <Select
                    size="small"
                    value={step.source || 'custom'}
                    style={{ width: 130 }}
                    onChange={(v) => handleSourceChange(index, v as 'custom' | 'saved')}
                    options={[
                      { label: '自己配置', value: 'custom' },
                      { label: '关联请求', value: 'saved' },
                    ]}
                  />
                )}
                <Switch
                  size="small"
                  checked={step.enabled !== false}
                  onChange={(checked) => onChange(index, { enabled: checked })}
                />
                <span style={{ fontSize: 11, color: 'var(--color-text-tertiary)' }}>
                  {step.enabled === false ? '已跳过' : '启用'}
                </span>
                <span style={{ fontSize: 11, color: 'var(--color-text-tertiary)' }}>
                  {sourceLabel(step)}
                </span>
                <div style={{ flex: 1 }} />
                <Button
                  type="text"
                  size="small"
                  danger
                  icon={<MinusOutlined />}
                  onClick={() => onRemove(index)}
                  title="删除该步骤"
                />
              </div>

              {step.type === 'set_variable' ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <div style={{ fontSize: 11, color: 'var(--color-text-tertiary)' }}>
                    旧版「设置变量」步骤：参数现已统一在前置参数页签中配置，建议删除本步骤并迁移
                  </div>
                  <Space.Compact style={{ width: '100%' }}>
                    <Input
                      size="small"
                      placeholder="变量名"
                      value={step.key || ''}
                      onChange={(e) => onChange(index, { key: e.target.value })}
                      style={{ width: '40%' }}
                    />
                    <Input
                      size="small"
                      placeholder="变量值，支持 {{variable}}"
                      value={step.value || ''}
                      onChange={(e) => onChange(index, { value: e.target.value })}
                    />
                    <Tooltip title="选择本地图片，转成 base64 填入变量值">
                      <Button
                        size="small"
                        icon={<PictureOutlined />}
                        loading={uploadingStep === index}
                        onClick={() => handlePickImage(index)}
                      >
                        图片
                      </Button>
                    </Tooltip>
                  </Space.Compact>
                  {step.value && step.value.length > 80 && (
                    <div style={{ fontSize: 11, color: 'var(--color-text-tertiary)' }}>
                      当前值长度 {step.value.length} 字符
                    </div>
                  )}
                </div>
              ) : isSaved ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <Select
                    size="small"
                    showSearch
                    optionFilterProp="label"
                    placeholder="选择要关联的已保存请求（HTTP）"
                    style={{ width: '100%' }}
                    value={step.saved_request_id}
                    onChange={(v) => onChange(index, { saved_request_id: v as number })}
                    options={savedRequests.map(r => ({
                      label: r.method + ' ' + r.name + ' — ' + r.url,
                      value: r.id,
                    }))}
                    notFoundContent="暂无已保存的 HTTP 请求"
                  />
                  <div style={{ fontSize: 11, color: 'var(--color-text-tertiary)' }}>
                    执行时按该请求保存的最新配置发送（含请求头、参数、请求体），此处无需重复维护
                  </div>
                  {renderExtracts(index)}
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <Space.Compact style={{ width: '100%' }}>
                    <Select
                      size="small"
                      value={step.method || 'GET'}
                      style={{ width: 100 }}
                      onChange={(v) => onChange(index, { method: v })}
                      options={METHODS.map((m) => ({ label: m, value: m }))}
                    />
                    <Input
                      size="small"
                      placeholder="请求地址，支持 {{variable}}"
                      value={step.url || ''}
                      onChange={(e) => onChange(index, { url: e.target.value })}
                    />
                  </Space.Compact>

                  <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-tertiary)', marginTop: 4 }}>
                    请求头
                  </div>
                  {renderKvEditor(index, 'headers', 'Header 名称', 'Header 值，支持 {{variable}}')}

                  <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-tertiary)', marginTop: 4 }}>
                    查询参数
                  </div>
                  {renderKvEditor(index, 'params', '参数名', '参数值，支持 {{variable}}')}

                  <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-tertiary)', marginTop: 4 }}>
                    请求体
                  </div>
                  {renderBodyEditor(index)}

                  {renderExtracts(index)}
                </div>
              )}
            </div>
          );
        })}

        <Button type="dashed" icon={<PlusOutlined />} onClick={onAdd}>
          添加前置步骤
        </Button>
      </div>

      <input ref={fileInputRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={handleImageSelected} />
    </div>
  );
};

export default PreStepsEditor;

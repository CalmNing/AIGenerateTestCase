import React from 'react';
import { Button, Input, Segmented, Select } from 'antd';
import { CopyOutlined, DeleteOutlined, DownOutlined, HolderOutlined, PlusOutlined, UpOutlined } from '@ant-design/icons';
import './TestcaseStructuredSections.css';

export interface StepRow {
  id: string;
  action: string;
  expected: string;
}

export const createStepRow = (action = '', expected = ''): StepRow => ({
  id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
  action,
  expected,
});

export const expectedTemplateGroups = [
  {
    label: '页面交互',
    options: [
      { label: '操作成功提示', value: '显示操作成功提示，且状态更新正确。' },
      { label: '页面跳转成功', value: '页面成功跳转至目标页面，且内容展示正确。' },
      { label: '字段校验提示', value: '页面显示对应字段校验提示，且不允许继续提交。' },
    ],
  },
  {
    label: '数据与提交',
    options: [
      { label: '数据保存成功', value: '数据保存成功，并在列表中正确展示最新内容。' },
      { label: '列表刷新正确', value: '列表数据刷新成功，新增或修改内容展示正确。' },
      { label: '状态更新成功', value: '状态更新成功，页面展示与后端数据保持一致。' },
    ],
  },
  {
    label: '接口结果',
    options: [
      { label: '接口返回成功', value: '接口返回成功状态，响应内容与预期一致。' },
      { label: '接口返回失败', value: '接口返回失败信息，错误提示与预期一致。' },
      { label: '错误码校验', value: '接口返回指定错误码，错误原因与预期一致。' },
    ],
  },
];

export const presetTemplates = [
  '用户已登录系统',
  '系统网络连接正常',
  '存在可用测试数据',
  '当前页面已打开',
  '接口服务已启动并可访问',
  '相关权限已提前配置完成',
];

interface StructuredTestcaseSectionsProps {
  presetRows: string[];
  stepRows: StepRow[];
  activeTemplateGroup: string;
  draggingStepId: string | null;
  dragOverStepId: string | null;
  onAddPreset: () => void;
  onAppendPresetTemplate: (template: string) => void;
  onUpdatePreset: (index: number, value: string) => void;
  onRemovePreset: (index: number) => void;
  onAddStep: () => void;
  onFillExpectedIfEmpty: (template: string) => void;
  onChangeTemplateGroup: (value: string) => void;
  onStepDragStart: (stepId: string) => void;
  onStepDragOver: (stepId: string) => void;
  onStepDrop: (stepId: string) => void;
  onStepDragEnd: () => void;
  onUpdateStep: (id: string, field: 'action' | 'expected', value: string) => void;
  onApplyExpectedTemplate: (id: string, template: string) => void;
  onMoveStep: (id: string, direction: 'up' | 'down') => void;
  onDuplicateStep: (id: string) => void;
  onRemoveStep: (id: string) => void;
}

const StructuredTestcaseSections: React.FC<StructuredTestcaseSectionsProps> = ({
  presetRows,
  stepRows,
  activeTemplateGroup,
  draggingStepId,
  dragOverStepId,
  onAddPreset,
  onAppendPresetTemplate,
  onUpdatePreset,
  onRemovePreset,
  onAddStep,
  onFillExpectedIfEmpty,
  onChangeTemplateGroup,
  onStepDragStart,
  onStepDragOver,
  onStepDrop,
  onStepDragEnd,
  onUpdateStep,
  onApplyExpectedTemplate,
  onMoveStep,
  onDuplicateStep,
  onRemoveStep,
}) => {
  const activeExpectedTemplates = expectedTemplateGroups.find((group) => group.label === activeTemplateGroup)?.options
    || expectedTemplateGroups[0].options;

  return (
    <>
      <section className="tts-section">
        <div className="tts-section__header">
          <div>
            <h3>前置条件</h3>
            <p>可直接插入常用前置条件，再按需微调。</p>
          </div>
          <Button size="small" icon={<PlusOutlined />} onClick={onAddPreset}>
            添加条件
          </Button>
        </div>
        <div className="tts-template-chips">
          {presetTemplates.map((template) => (
            <button
              key={template}
              type="button"
              className="tts-template-chip"
              onClick={() => onAppendPresetTemplate(template)}
            >
              {template}
            </button>
          ))}
        </div>
        <div className="tts-stack">
          {presetRows.map((row, index) => (
            <div key={`preset-${index}`} className="tts-inline-row">
              <Input
                value={row}
                placeholder={`前置条件 ${index + 1}`}
                onChange={(event) => onUpdatePreset(index, event.target.value)}
              />
              <Button danger type="text" icon={<DeleteOutlined />} onClick={() => onRemovePreset(index)} />
            </div>
          ))}
        </div>
      </section>

      <section className="tts-section">
        <div className="tts-section__header">
          <div>
            <h3>步骤设计</h3>
            <p>按行编辑测试步骤与预期结果，支持排序和模板快捷插入。</p>
          </div>
          <Button size="small" icon={<PlusOutlined />} onClick={onAddStep}>
            添加步骤
          </Button>
        </div>
        <div className="tts-template-bar">
          <span className="tts-template-bar__label">快速预期模板</span>
          <Segmented
            size="small"
            value={activeTemplateGroup}
            onChange={(value) => onChangeTemplateGroup(String(value))}
            options={expectedTemplateGroups.map((group) => ({ label: group.label, value: group.label }))}
          />
          <div className="tts-template-bar__chips">
            {activeExpectedTemplates.map((template) => (
              <button
                key={`${template.label}-${template.value}`}
                type="button"
                className="tts-template-chip"
                onClick={() => onFillExpectedIfEmpty(template.value)}
              >
                {template.label}
              </button>
            ))}
          </div>
        </div>

        <div className="tts-steps-board">
          <div className="tts-steps-board__header">
            <div className="tts-steps-board__index">#</div>
            <div className="tts-steps-board__heading">测试步骤</div>
            <div className="tts-steps-board__heading">预期结果</div>
            <div className="tts-steps-board__actions">操作</div>
          </div>

          <div className="tts-steps-board__body">
            {stepRows.map((row, index) => (
              <div
                key={row.id}
                className={`tts-step-row${dragOverStepId === row.id ? ' is-drag-over' : ''}${draggingStepId === row.id ? ' is-dragging' : ''}`}
                draggable
                onDragStart={() => onStepDragStart(row.id)}
                onDragOver={(event) => {
                  event.preventDefault();
                  onStepDragOver(row.id);
                }}
                onDrop={() => onStepDrop(row.id)}
                onDragEnd={onStepDragEnd}
              >
                <div className="tts-step-row__index">{index + 1}</div>
                <Input.TextArea
                  value={row.action}
                  placeholder="输入测试步骤"
                  autoSize={{ minRows: 2, maxRows: 6 }}
                  onChange={(event) => onUpdateStep(row.id, 'action', event.target.value)}
                />
                <div className="tts-expected-cell">
                  <Select
                    size="small"
                    className="tts-expected-template"
                    placeholder="插入模板"
                    value={undefined}
                    onChange={(value) => {
                      if (typeof value === 'string') {
                        onApplyExpectedTemplate(row.id, value);
                      }
                    }}
                    options={expectedTemplateGroups}
                  />
                  <Input.TextArea
                    value={row.expected}
                    placeholder="输入预期结果"
                    autoSize={{ minRows: 2, maxRows: 6 }}
                    onChange={(event) => onUpdateStep(row.id, 'expected', event.target.value)}
                  />
                </div>
                <div className="tts-step-row__actions">
                  <Button type="text" icon={<HolderOutlined />} className="tts-drag-handle" title="拖拽排序" />
                  <Button type="text" icon={<UpOutlined />} disabled={index === 0} onClick={() => onMoveStep(row.id, 'up')} />
                  <Button type="text" icon={<DownOutlined />} disabled={index === stepRows.length - 1} onClick={() => onMoveStep(row.id, 'down')} />
                  <Button type="text" icon={<CopyOutlined />} onClick={() => onDuplicateStep(row.id)} />
                  <Button danger type="text" icon={<DeleteOutlined />} onClick={() => onRemoveStep(row.id)} />
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>
    </>
  );
};

export default StructuredTestcaseSections;

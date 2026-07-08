import React, { useState } from 'react';
import { Button, Drawer, Form, Input, message, Select, Tag } from 'antd';
import { Module, Session } from '../../types';
import { testcaseApi } from '../../services/api';
import StructuredTestcaseSections, { createStepRow, expectedTemplateGroups, StepRow } from './TestcaseStructuredSections';
import './TestcaseDrawerShared.css';
import './AddTestcaseModal.css';

interface AddTestcaseModalProps {
  visible: boolean;
  selectedSession: Session | null;
  modules: Module[];
  onCancel: () => void;
  onAddSuccess: () => void;
}

const showMessage = (
  type: 'success' | 'error',
  title: string,
  description?: string,
) => {
  message.open({
    type,
    content: description ? `${title}: ${description}` : title,
  });
};

const AddTestcaseModal: React.FC<AddTestcaseModalProps> = ({
  visible,
  selectedSession,
  modules,
  onCancel,
  onAddSuccess,
}) => {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [presetRows, setPresetRows] = useState<string[]>(['']);
  const [stepRows, setStepRows] = useState<StepRow[]>([createStepRow()]);
  const [activeTemplateGroup, setActiveTemplateGroup] = useState(expectedTemplateGroups[0].label);
  const [draggingStepId, setDraggingStepId] = useState<string | null>(null);
  const [dragOverStepId, setDragOverStepId] = useState<string | null>(null);

  const resetEditor = () => {
    form.resetFields();
    setPresetRows(['']);
    setStepRows([createStepRow()]);
    setActiveTemplateGroup(expectedTemplateGroups[0].label);
    setDraggingStepId(null);
    setDragOverStepId(null);
  };

  const updatePresetRow = (index: number, value: string) => {
    const nextRows = [...presetRows];
    nextRows[index] = value;
    setPresetRows(nextRows);
  };

  const addPresetRow = () => setPresetRows([...presetRows, '']);

  const appendPresetTemplate = (template: string) => {
    setPresetRows((rows) => {
      const lastIndex = rows.length - 1;
      if (lastIndex >= 0 && rows[lastIndex].trim() === '') {
        const nextRows = [...rows];
        nextRows[lastIndex] = template;
        return nextRows;
      }
      return [...rows, template];
    });
  };

  const removePresetRow = (index: number) => {
    const nextRows = presetRows.filter((_, rowIndex) => rowIndex !== index);
    setPresetRows(nextRows.length > 0 ? nextRows : ['']);
  };

  const updateStepRow = (id: string, field: 'action' | 'expected', value: string) => {
    setStepRows((rows) => rows.map((row) => (
      row.id === id ? { ...row, [field]: value } : row
    )));
  };

  const addStepRow = () => setStepRows((rows) => [...rows, createStepRow()]);

  const removeStepRow = (id: string) => {
    const nextRows = stepRows.filter((row) => row.id !== id);
    setStepRows(nextRows.length > 0 ? nextRows : [createStepRow()]);
  };

  const duplicateStepRow = (id: string) => {
    setStepRows((rows) => {
      const index = rows.findIndex((row) => row.id === id);
      if (index < 0) return rows;
      const source = rows[index];
      const cloned = createStepRow(source.action, source.expected);
      const nextRows = [...rows];
      nextRows.splice(index + 1, 0, cloned);
      return nextRows;
    });
  };

  const moveStepRow = (id: string, direction: 'up' | 'down') => {
    setStepRows((rows) => {
      const index = rows.findIndex((row) => row.id === id);
      if (index < 0) return rows;
      const targetIndex = direction === 'up' ? index - 1 : index + 1;
      if (targetIndex < 0 || targetIndex >= rows.length) return rows;
      const nextRows = [...rows];
      [nextRows[index], nextRows[targetIndex]] = [nextRows[targetIndex], nextRows[index]];
      return nextRows;
    });
  };

  const reorderStepRows = (sourceId: string, targetId: string) => {
    if (sourceId === targetId) return;
    setStepRows((rows) => {
      const sourceIndex = rows.findIndex((row) => row.id === sourceId);
      const targetIndex = rows.findIndex((row) => row.id === targetId);
      if (sourceIndex < 0 || targetIndex < 0) return rows;
      const nextRows = [...rows];
      const [moved] = nextRows.splice(sourceIndex, 1);
      nextRows.splice(targetIndex, 0, moved);
      return nextRows;
    });
  };

  const applyExpectedTemplate = (id: string, template: string) => {
    setStepRows((rows) => rows.map((row) => (
      row.id === id ? { ...row, expected: row.expected ? `${row.expected}\n${template}` : template } : row
    )));
  };

  const fillExpectedIfEmpty = (template: string) => {
    setStepRows((rows) => {
      const firstEmptyIndex = rows.findIndex((row) => !row.expected.trim());
      if (firstEmptyIndex < 0) {
        return [...rows, createStepRow('', template)];
      }
      return rows.map((row, index) => (
        index === firstEmptyIndex ? { ...row, expected: template } : row
      ));
    });
  };

  const handleStepDragStart = (stepId: string) => {
    setDraggingStepId(stepId);
    setDragOverStepId(stepId);
  };

  const handleStepDrop = (targetStepId: string) => {
    if (draggingStepId) reorderStepRows(draggingStepId, targetStepId);
    setDraggingStepId(null);
    setDragOverStepId(null);
  };

  const handleStepDragEnd = () => {
    setDraggingStepId(null);
    setDragOverStepId(null);
  };

  const handleOk = async () => {
    if (!selectedSession) return;

    try {
      setLoading(true);
      const values = await form.validateFields();
      const cleanedPresetRows = presetRows.map((item) => item.trim()).filter(Boolean);
      const cleanedStepRows = stepRows
        .map((row) => ({ action: row.action.trim(), expected: row.expected.trim() }))
        .filter((row) => row.action || row.expected);

      const testcaseData = {
        case_name: values.case_name,
        case_level: values.case_level,
        preset_conditions: cleanedPresetRows,
        steps: cleanedStepRows.map((row) => row.action),
        expected_results: cleanedStepRows.map((row) => row.expected),
        module_id: values.module_id || undefined,
        session_id: selectedSession.id,
      };

      const response = await testcaseApi.createTestcase(selectedSession.id, testcaseData);

      if (response.code === 200) {
        showMessage('success', '创建成功', '测试用例已成功创建');
        resetEditor();
        onAddSuccess();
        onCancel();
      } else {
        showMessage('error', '创建失败', response.message || '创建测试用例失败，请重试');
      }
    } catch (error: any) {
      console.error('创建测试用例失败:', error);
      showMessage('error', '创建失败', error.response?.data?.detail || '创建测试用例时发生错误，请重试');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Drawer
      title="新增测试用例"
      open={visible}
      onClose={onCancel}
      placement="right"
      width={820}
      destroyOnHidden
      className="atm-drawer"
      styles={{
        header: { padding: '16px 20px 12px' },
        body: { padding: '0 20px 20px' },
        footer: { padding: '12px 20px', borderTop: '1px solid var(--color-border-light)' },
      }}
      footer={(
        <div className="atm-footer">
          <Button onClick={onCancel}>取消</Button>
          <Button type="primary" loading={loading} onClick={() => void handleOk()} disabled={!selectedSession}>
            创建
          </Button>
        </div>
      )}
    >
      {!selectedSession && <div className="atm-empty-hint">请先选择一个会话。</div>}

      <Form form={form} layout="vertical" disabled={!selectedSession}>
        {selectedSession && (
          <div className="atm-summary">
            <Tag color="blue">{selectedSession.name}</Tag>
            <Tag color="default">新增模式</Tag>
          </div>
        )}

        <section className="atm-section">
          <div className="atm-section__header">
            <h3>基本信息</h3>
          </div>
          <div className="atm-grid">
            <Form.Item
              className="atm-grid__wide"
              name="case_name"
              label="用例名称"
              rules={[{ required: true, message: '请输入用例名称' }]}
            >
              <Input placeholder="输入用例名称" />
            </Form.Item>

            <Form.Item
              name="case_level"
              label="用例级别"
              rules={[{ required: true, message: '请选择用例级别' }]}
              initialValue={4}
            >
              <Select
                options={[
                  { label: 'P0', value: 1 },
                  { label: 'P1', value: 2 },
                  { label: 'P2', value: 3 },
                  { label: 'P3', value: 4 },
                ]}
              />
            </Form.Item>

            <Form.Item
              name="module_id"
              label="模块"
              rules={[{ required: true, message: '请选择模块' }]}
            >
              <Select
                placeholder="选择模块"
                options={modules.map((module) => ({ label: module.module_name, value: module.id }))}
              />
            </Form.Item>
          </div>
        </section>

        <StructuredTestcaseSections
          presetRows={presetRows}
          stepRows={stepRows}
          activeTemplateGroup={activeTemplateGroup}
          draggingStepId={draggingStepId}
          dragOverStepId={dragOverStepId}
          onAddPreset={addPresetRow}
          onAppendPresetTemplate={appendPresetTemplate}
          onUpdatePreset={updatePresetRow}
          onRemovePreset={removePresetRow}
          onAddStep={addStepRow}
          onFillExpectedIfEmpty={fillExpectedIfEmpty}
          onChangeTemplateGroup={setActiveTemplateGroup}
          onStepDragStart={handleStepDragStart}
          onStepDragOver={setDragOverStepId}
          onStepDrop={handleStepDrop}
          onStepDragEnd={handleStepDragEnd}
          onUpdateStep={updateStepRow}
          onApplyExpectedTemplate={applyExpectedTemplate}
          onMoveStep={moveStepRow}
          onDuplicateStep={duplicateStepRow}
          onRemoveStep={removeStepRow}
        />
      </Form>
    </Drawer>
  );
};

export default AddTestcaseModal;

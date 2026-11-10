import React, { useEffect, useMemo, useState } from 'react';
import { Button, Collapse, Drawer, Form, Input, Select, Switch, Tag } from 'antd';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { GlobalParameter, TestCase, TestCaseStatus } from '../../types';
import { apiTestApi, globalParameterApi } from '../../services/api';
import { formatTestCaseLevel, TEST_CASE_LEVEL_OPTIONS } from '../../utils/testCaseLevel';
import StructuredTestcaseSections, { createStepRow, expectedTemplateGroups, StepRow } from './TestcaseStructuredSections';
import './TestcaseDrawerShared.css';
import './EditTestcaseModal.css';

interface HeaderItem {
  key: string;
  value: string;
}

interface EditTestcaseModalProps {
  visible: boolean;
  selectedTestcase: TestCase | null;
  form: any;
  loading: boolean;
  onCancel: () => void;
  onFinish: (values: any) => void;
}

const EditTestcaseModal: React.FC<EditTestcaseModalProps> = ({
  visible,
  selectedTestcase,
  form,
  loading,
  onCancel,
  onFinish,
}) => {
  const [apiCallEnabled, setApiCallEnabled] = useState(false);
  const [headers, setHeaders] = useState<HeaderItem[]>([{ key: '', value: '' }]);
  const [environmentId, setEnvironmentId] = useState<number | undefined>(undefined);
  const [environments, setEnvironments] = useState<GlobalParameter[]>([]);
  const [body, setBody] = useState('');
  const [presetRows, setPresetRows] = useState<string[]>(['']);
  const [stepRows, setStepRows] = useState<StepRow[]>([createStepRow()]);
  const [activeTemplateGroup, setActiveTemplateGroup] = useState(expectedTemplateGroups[0].label);
  const [draggingStepId, setDraggingStepId] = useState<string | null>(null);
  const [dragOverStepId, setDragOverStepId] = useState<string | null>(null);

  const firstEndpointId = (value: TestCase['api_endpoint_id']): number | null => {
    if (value === null || value === undefined) return null;
    const first = String(value).split(',')[0]?.trim();
    const parsed = Number(first);
    return Number.isFinite(parsed) ? parsed : null;
  };

  const headerSummary = useMemo(
    () => headers.filter((item) => item.key.trim()).length,
    [headers],
  );

  useEffect(() => {
    if (!visible) return;
    void loadEnvironments();
  }, [visible]);

  useEffect(() => {
    if (!selectedTestcase || !visible) return;

    const apiCallStep = selectedTestcase.steps.find(
      (step: any) => typeof step === 'object' && step.type === 'api_call',
    );

    if (apiCallStep && typeof apiCallStep === 'object') {
      setApiCallEnabled(true);
      setHeaders((apiCallStep as any).headers?.length ? (apiCallStep as any).headers : [{ key: '', value: '' }]);
      setEnvironmentId((apiCallStep as any).environment_id);
      if ((apiCallStep as any).body) {
        setBody((apiCallStep as any).body);
      } else if (selectedTestcase.api_project_id && selectedTestcase.api_endpoint_id) {
        const endpointId = firstEndpointId(selectedTestcase.api_endpoint_id);
        if (endpointId) void loadEndpointBody(selectedTestcase.api_project_id, endpointId);
      } else {
        setBody('');
      }
    } else {
      setApiCallEnabled(false);
      setHeaders([{ key: '', value: '' }]);
      setEnvironmentId(undefined);
      if (selectedTestcase.api_project_id && selectedTestcase.api_endpoint_id) {
        const endpointId = firstEndpointId(selectedTestcase.api_endpoint_id);
        if (endpointId) void loadEndpointBody(selectedTestcase.api_project_id, endpointId);
      } else {
        setBody('');
      }
    }

    const textPresets = selectedTestcase.preset_conditions
      .filter((item: any) => typeof item === 'string')
      .map((item: any) => String(item));
    setPresetRows(textPresets.length > 0 ? textPresets : ['']);

    const textSteps = selectedTestcase.steps.filter(
      (step: any) => typeof step === 'string' || (typeof step === 'object' && step?.type !== 'api_call' && typeof step?.action === 'string'),
    );
    const expectedResults = selectedTestcase.expected_results.map((item: any) =>
      (typeof item === 'string' ? item : item?.expected || ''));
    const mappedRows = textSteps.map((step: any, index: number) => createStepRow(
      typeof step === 'string' ? step : step.action,
      expectedResults[index] || (typeof step === 'object' ? (step as any).expected || '' : ''),
    ));
    setStepRows(mappedRows.length > 0 ? mappedRows : [createStepRow()]);
    setActiveTemplateGroup(expectedTemplateGroups[0].label);
    setDraggingStepId(null);
    setDragOverStepId(null);

    form.setFieldsValue({
      case_name: selectedTestcase.case_name,
      case_level: selectedTestcase.case_level,
      status: selectedTestcase.status || TestCaseStatus.NOT_RUN,
    });
  }, [selectedTestcase, form, visible]);

  const loadEnvironments = async () => {
    try {
      const response = await globalParameterApi.getEnvironments();
      if (response.code === 200 && response.data) {
        setEnvironments(response.data);
      }
    } catch (error) {
      console.error('加载环境列表失败:', error);
    }
  };

  const loadEndpointBody = async (projectId: number, endpointId: number) => {
    try {
      const response = await apiTestApi.getEndpoints(projectId);
      if (response.code === 200 && response.data) {
        const endpoint = response.data.find((ep: any) => ep.id === endpointId);
        setBody(endpoint?.body || '');
      }
    } catch (error) {
      console.error('加载接口 body 失败:', error);
    }
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

  const handleHeaderChange = (index: number, field: 'key' | 'value', value: string) => {
    const nextHeaders = [...headers];
    nextHeaders[index] = { ...nextHeaders[index], [field]: value };
    setHeaders(nextHeaders);
  };

  const handleAddHeader = () => setHeaders([...headers, { key: '', value: '' }]);

  const handleRemoveHeader = (index: number) => {
    const nextHeaders = headers.filter((_, itemIndex) => itemIndex !== index);
    setHeaders(nextHeaders.length > 0 ? nextHeaders : [{ key: '', value: '' }]);
  };

  const handleOk = async () => {
    try {
      const values = await form.validateFields();
      handleFinish(values);
    } catch (error) {
      console.error('表单校验失败:', error);
    }
  };

  const handleFinish = (values: any) => {
    const cleanedPresetRows = presetRows.map((item) => item.trim()).filter(Boolean);
    const cleanedStepRows = stepRows
      .map((row) => ({ action: row.action.trim(), expected: row.expected.trim() }))
      .filter((row) => row.action || row.expected);

    const steps: any[] = [];
    const expectedResults = cleanedStepRows.map((row) => row.expected);
    const originalStructuredSteps = ((selectedTestcase?.steps || []) as any[]).filter(
      (step: any) => typeof step === 'object' && step?.type !== 'api_call' && typeof step?.action === 'string',
    );

    if (originalStructuredSteps.length > 0) {
      cleanedStepRows.forEach((row, index) => {
        const originalStep = originalStructuredSteps[index];
        const selectedExpected = selectedTestcase?.expected_results?.[index];
        const originalExpected = typeof selectedExpected === 'string'
          ? selectedExpected
          : originalStep?.expected;

        steps.push({
          step: index + 1,
          action: row.action,
          expected: row.expected || originalExpected || '',
        });
      });
    } else {
      steps.push(...cleanedStepRows.map((row) => row.action));
    }

    if (apiCallEnabled && selectedTestcase?.api_endpoint_id) {
      const validHeaders = headers.filter((item) => item.key.trim() !== '');
      const existingApiCall = Array.isArray(selectedTestcase.steps)
        ? selectedTestcase.steps.find((step: any) => typeof step === 'object' && step.type === 'api_call')
        : undefined;
      const existingEndpointId = existingApiCall
        ? (existingApiCall as any).endpoint_id
        : firstEndpointId(selectedTestcase.api_endpoint_id);

      steps.push({
        type: 'api_call',
        endpoint_id: existingEndpointId,
        headers: validHeaders,
        body: body || undefined,
        environment_id: environmentId,
      });
    }

    const objectPresets = (selectedTestcase?.preset_conditions || []).filter((item: any) => typeof item !== 'string');

    onFinish({
      ...values,
      preset_conditions: [...cleanedPresetRows, ...objectPresets],
      steps,
      expected_results: expectedResults,
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

  return (
    <Drawer
      title="编辑测试用例"
      open={visible}
      onClose={onCancel}
      placement="right"
      width={820}
      destroyOnHidden
      className="etm-drawer"
      styles={{
        header: { padding: '16px 20px 12px' },
        body: { padding: '0 20px 20px' },
        footer: { padding: '12px 20px', borderTop: '1px solid var(--color-border-light)' },
      }}
      footer={(
        <div className="etm-footer">
          <Button onClick={onCancel}>取消</Button>
          <Button type="primary" loading={loading} onClick={() => void handleOk()}>
            保存
          </Button>
        </div>
      )}
    >
      {selectedTestcase && (
        <Form form={form} layout="vertical">
          <div className="etm-summary">
            <Tag color="blue">#{selectedTestcase.id}</Tag>
            <Tag color="default">{formatTestCaseLevel(selectedTestcase.case_level)}</Tag>
            {selectedTestcase.api_endpoint_id && <Tag color="purple">已关联 API</Tag>}
          </div>

          <section className="etm-section">
            <div className="etm-section__header">
              <h3>基本信息</h3>
            </div>
            <div className="etm-grid">
              <Form.Item
                className="etm-grid__wide"
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
              >
                <Select options={TEST_CASE_LEVEL_OPTIONS} />
              </Form.Item>

              <Form.Item
                name="status"
                label="测试状态"
                rules={[{ required: true, message: '请选择测试状态' }]}
              >
                <Select
                  disabled={selectedTestcase.status !== TestCaseStatus.NOT_RUN}
                  options={[
                    { label: '未执行', value: TestCaseStatus.NOT_RUN },
                    { label: '已通过', value: TestCaseStatus.PASSED },
                    { label: '未通过', value: TestCaseStatus.FAILED },
                  ]}
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

          <section className="etm-section">
            <Collapse
              className="etm-collapse"
              items={[
                {
                  key: 'api',
                  label: (
                    <div className="etm-collapse__label">
                      <span>API 调用配置</span>
                      <span className="etm-collapse__meta">
                        {selectedTestcase.api_endpoint_id ? `${headerSummary} 个 Header` : '未关联 API'}
                      </span>
                    </div>
                  ),
                  children: !selectedTestcase.api_endpoint_id ? (
                    <div className="etm-empty-hint">该测试用例未关联 API 接口，无法配置 API 调用参数。</div>
                  ) : (
                    <div className="etm-api">
                      <div className="etm-api__toggle">
                        <div>
                          <div className="etm-api__toggle-title">启用 API 调用</div>
                          <div className="etm-api__toggle-help">开启后可覆盖请求头、环境变量和请求体。</div>
                        </div>
                        <Switch checked={apiCallEnabled} onChange={setApiCallEnabled} />
                      </div>

                      {apiCallEnabled && (
                        <>
                          <div className="etm-api__block">
                            <div className="etm-section__header">
                              <h4>请求 Headers</h4>
                              <Button size="small" icon={<PlusOutlined />} onClick={handleAddHeader}>
                                添加 Header
                              </Button>
                            </div>
                            <div className="etm-stack">
                              {headers.map((header, index) => (
                                <div key={`header-${index}`} className="etm-inline-row etm-inline-row--triple">
                                  <Input
                                    placeholder="Key"
                                    value={header.key}
                                    onChange={(event) => handleHeaderChange(index, 'key', event.target.value)}
                                  />
                                  <Input
                                    placeholder="Value"
                                    value={header.value}
                                    onChange={(event) => handleHeaderChange(index, 'value', event.target.value)}
                                  />
                                  <Button danger type="text" icon={<DeleteOutlined />} onClick={() => handleRemoveHeader(index)} />
                                </div>
                              ))}
                            </div>
                          </div>

                          <div className="etm-api__block">
                            <div className="etm-field-label">环境变量</div>
                            <Select
                              placeholder="选择环境（可选）"
                              value={environmentId}
                              onChange={setEnvironmentId}
                              allowClear
                              options={environments.map((env) => ({ label: env.name, value: env.id }))}
                            />
                            {environmentId && (
                              <div className="etm-field-help">选择环境后，可在 Headers 中使用 {'{{变量名}}'} 引用环境变量。</div>
                            )}
                          </div>

                          <div className="etm-api__block">
                            <div className="etm-field-label">请求 Body</div>
                            <div className="etm-field-help">JSON 格式，留空则使用接口默认 Body。</div>
                            <Input.TextArea
                              value={body}
                              onChange={(event) => setBody(event.target.value)}
                              placeholder='{"key": "value"}'
                              rows={8}
                              style={{ fontFamily: 'Consolas, Monaco, monospace', fontSize: 13 }}
                            />
                          </div>
                        </>
                      )}
                    </div>
                  ),
                },
              ]}
            />
          </section>
        </Form>
      )}
    </Drawer>
  );
};

export default EditTestcaseModal;

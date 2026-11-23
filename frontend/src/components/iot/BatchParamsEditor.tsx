import React, { useMemo, useRef, useState } from 'react';
import { Button, Input, Modal, Select, Tooltip, Empty, message } from 'antd';
import {
  PlusOutlined,
  MinusOutlined,
  QuestionCircleOutlined,
  ImportOutlined,
  PictureOutlined,
} from '@ant-design/icons';
import type { BatchParams, BatchParamsColumn } from '../../types';
import { imageBase64Api } from '../../services/api';

/**
 * 前置参数列表（批量）配置面板。
 *
 * 交互：**先定义列（变量名固定），再逐行填值**。
 * - 列：每一列是一个变量名，主请求里用 {{列名}} 引用；
 * - 行：每一行是一组值，发一次请求；
 * - 行内变量只在该次循环内生效，不跨行泄漏。
 *
 * 列类型：文本 / 数值 / 布尔 / JSON；数值、布尔、JSON 在执行时校验，
 * 非法值该变量不注入（旧数据里的图片列仍由后端兼容处理）。
 *
 * 支持从 JSON 数组或 CSV/TSV 文本粘贴导入。
 */
interface BatchParamsEditorProps {
  value?: BatchParams;
  onChange: (next: BatchParams) => void;
}


const COLUMN_TYPES = [
  { label: '文本', value: 'text' },
  { label: '数值', value: 'number' },
  { label: '布尔', value: 'boolean' },
  { label: 'JSON', value: 'json' },
  { label: '图片base64', value: 'image_base64' },
];

/** 把列定义统一成对象形式（兼容旧的字符串列名） */
function toColumn(col: string | BatchParamsColumn | null | undefined): BatchParamsColumn {
  if (typeof col === 'string') {
    return { name: col, type: 'text', compress: true, format: 'raw' };
  }
  if (!col || typeof col !== 'object') {
    return { name: '', type: 'text', compress: true, format: 'raw' };
  }
  return {
    name: col.name || '',
    type: col.type || 'text',
    compress: col.compress !== false,
    format: col.format || 'raw',
  };
}

/** 解析粘贴内容：优先 JSON 数组，其次 CSV/TSV（首行作为列名）。 */
function parseImportText(text: string): { columns: BatchParamsColumn[]; rows: string[][] } | null {
  const trimmed = (text || '').trim();
  if (!trimmed) return null;

  // JSON：数组的每项是对象（取 key 作为列），或数组的每项是数组（首行为列名）
  if (trimmed.startsWith('[') || trimmed.startsWith('{')) {
    try {
      const parsed = JSON.parse(trimmed);
      const arr = Array.isArray(parsed) ? parsed : [parsed];
      if (!arr.length) return null;

      if (Array.isArray(arr[0])) {
        // 首行是列名
        const columns: BatchParamsColumn[] = (arr[0] as unknown[]).map((v, i) => ({
          name: String(v ?? '').trim() || 'col' + (i + 1),
          type: 'text',
          compress: true,
          format: 'raw',
        }));
        const rows = arr.slice(1).map((r) => {
          const vals = Array.isArray(r) ? (r as unknown[]) : [];
          return columns.map((_c, i) => (vals[i] == null ? '' : String(vals[i])));
        });
        return { columns, rows };
      }

      // 对象数组：取所有 key 的并集作为列
      const keys: string[] = [];
      arr.forEach((item) => {
        if (item && typeof item === 'object') {
          Object.keys(item as Record<string, unknown>).forEach((k) => {
            if (!keys.includes(k)) keys.push(k);
          });
        }
      });
      if (!keys.length) return null;
      const columns: BatchParamsColumn[] = keys.map((k) => ({
        name: k,
        type: 'text',
        compress: true,
        format: 'raw',
      }));
      const rows = arr.map((item) => {
        const obj = (item && typeof item === 'object' ? item : {}) as Record<string, unknown>;
        return columns.map((c) => (obj[c.name] == null ? '' : String(obj[c.name])));
      });
      return { columns, rows };
    } catch {
      return null;
    }
  }

  // CSV / TSV：首行为列名
  const lines = trimmed.split(/\r?\n/).filter((l) => l.trim() !== '');
  if (lines.length < 2) return null;
  const delim = lines[0].includes('\t') ? '\t' : ',';
  const split = (line: string) => line.split(delim).map((c) => c.trim());
  const columns: BatchParamsColumn[] = split(lines[0]).map((c, i) => ({
    name: c || 'col' + (i + 1),
    type: 'text',
    compress: true,
    format: 'raw',
  }));
  const rows = lines.slice(1).map((line) => {
    const vals = split(line);
    return columns.map((c, i) => (vals[i] == null ? '' : vals[i]));
  });
  return { columns, rows };
}

const BatchParamsEditor: React.FC<BatchParamsEditorProps> = ({ value, onChange }) => {
  // 后端旧数据可能给出数组/缺字段形状，这里只取认得的字段，避免渲染期报错
  const params: BatchParams = {
    columns: Array.isArray(value?.columns) ? value.columns : [],
    rows: Array.isArray(value?.rows) ? value.rows : [],
    on_error: value?.on_error === 'stop' ? 'stop' : 'continue',
  };
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState('');
  // 图片base64 列：选图转 base64 的隐藏 file input（待转换的单元格位置存在 ref 里）
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pendingCellRef = useRef<{ row: number; col: number } | null>(null);
  const [convertingCell, setConvertingCell] = useState<string>('');

  const columns = useMemo(() => (params.columns || []).map(toColumn), [params.columns]);
  const rows = params.rows || [];

  const totalCells = useMemo(() => columns.length * rows.length, [columns.length, rows.length]);

  const emit = (next: Partial<BatchParams>) => {
    onChange({ columns, rows, on_error: params.on_error || 'continue', ...next });
  };

  const addColumn = () => {
    let n = columns.length + 1;
    let name = 'var' + n;
    while (columns.some((c) => c.name === name)) {
      n += 1;
      name = 'var' + n;
    }
    emit({
      columns: [...columns, { name, type: 'text', compress: true, format: 'raw' }],
      rows: rows.map((r) => [...r, '']),
    });
  };

  const removeColumn = (colIndex: number) => {
    emit({
      columns: columns.filter((_, i) => i !== colIndex),
      rows: rows.map((r) => r.filter((_, i) => i !== colIndex)),
    });
  };

  const updateColumn = (colIndex: number, updates: Partial<BatchParamsColumn>) => {
    const next = [...columns];
    next[colIndex] = { ...next[colIndex], ...updates };
    emit({ columns: next });
  };

  const addRow = () => {
    emit({ rows: [...rows, columns.map(() => '')] });
  };

  const removeRow = (rowIndex: number) => {
    emit({ rows: rows.filter((_, i) => i !== rowIndex) });
  };

  const setCell = (rowIndex: number, colIndex: number, val: string) => {
    const next = rows.map((r, i) =>
      i === rowIndex ? r.map((c, j) => (j === colIndex ? val : c)) : r
    );
    emit({ rows: next });
  };

  /** 图片base64 列：选本地图片 -> 转 base64 -> 填入单元格 */
  const handlePickImage = (rowIndex: number, colIndex: number) => {
    pendingCellRef.current = { row: rowIndex, col: colIndex };
    fileInputRef.current?.click();
  };

  const handleImageSelected = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    const cell = pendingCellRef.current;
    e.target.value = '';
    pendingCellRef.current = null;
    if (!file || !cell) return;

    setConvertingCell(cell.row + '-' + cell.col);
    try {
      const res = await imageBase64Api.toBase64(file, { compress: true, format: 'raw' });
      if (res.code === 200 && res.data?.base64) {
        setCell(cell.row, cell.col, res.data.base64);
        message.success(
          '图片已转为 base64（' + res.data.mime + '，' + Math.round(res.data.fileSize / 1024) + 'KB）'
        );
      } else {
        message.error(res.message || '图片转 base64 失败');
      }
    } catch (err: any) {
      console.error('Image to base64 failed:', err);
      message.error(err?.response?.data?.detail || '图片转 base64 失败');
    } finally {
      setConvertingCell('');
    }
  };

  const handleImport = () => {
    const parsed = parseImportText(importText);
    if (!parsed) {
      message.error('无法解析内容：需要 JSON 数组，或首行是列名的 CSV/TSV');
      return;
    }
    if (!parsed.columns.length || !parsed.rows.length) {
      message.error('解析结果为空');
      return;
    }
    onChange({
      columns: parsed.columns,
      rows: parsed.rows,
      on_error: params.on_error || 'continue',
    });
    setImportOpen(false);
    setImportText('');
    message.success('已导入 ' + parsed.columns.length + ' 列 / ' + parsed.rows.length + ' 行');
  };

  const thStyle: React.CSSProperties = {
    border: '1px solid var(--color-border-light)',
    background: 'var(--color-bg)',
    padding: '4px 6px',
    fontWeight: 600,
  };

  const tdStyle: React.CSSProperties = {
    border: '1px solid var(--color-border-light)',
    padding: 2,
  };

  return (
    <div className="iot-config-section">
      <div className="iot-config-section-hint">
        按列表循环执行主请求：先定义列（变量名），再逐行填值
        <Tooltip
          title={
            <div style={{ fontSize: 12, lineHeight: 1.8 }}>
              <div>按列表循环执行主请求</div>
              <div style={{ marginTop: 4, fontWeight: 600 }}>用法：</div>
              <div>1. 定义列（列名即变量名）</div>
              <div>2. 逐行填值</div>
              <div>3. 主请求里用 {'{{列名}}'} 引用</div>
              <div style={{ marginTop: 4, fontWeight: 600 }}>列类型：</div>
              <div><code>数值</code> —— 校验为合法数字，非法值该变量不注入</div>
              <div><code>布尔</code> —— true/false/1/0/yes/no 归一化为 true/false</div>
              <div><code>JSON</code> —— 校验合法并压缩为单行</div>
              <div><code>图片base64</code> —— 该列值是 base64 图片串，执行时校验并规范化；</div>
              <div>可粘贴 base64 / data:image URL，或点单元格里的图片按钮选本地图片</div>
              <div style={{ marginTop: 4 }}>每行变量只在该次循环内生效，不跨行泄漏。</div>
            </div>
          }
          overlayClassName="iot-tooltip"
        >
          <QuestionCircleOutlined style={{ color: 'var(--color-text-tertiary)', fontSize: 13 }} />
        </Tooltip>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
        <Button size="small" icon={<PlusOutlined />} onClick={addColumn}>
          添加列
        </Button>
        <Button size="small" icon={<PlusOutlined />} onClick={addRow} disabled={!columns.length}>
          添加行
        </Button>
        <Button size="small" icon={<ImportOutlined />} onClick={() => setImportOpen(true)}>
          粘贴导入
        </Button>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 12, color: 'var(--color-text-tertiary)' }}>失败时：</span>
        <Select
          size="small"
          value={params.on_error || 'continue'}
          style={{ width: 130 }}
          onChange={(v) => emit({ on_error: v })}
          options={[
            { label: '继续执行其余项', value: 'continue' },
            { label: '遇错即停', value: 'stop' },
          ]}
        />
      </div>

      {columns.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <span>
              尚未定义列，
              <Button type="link" size="small" onClick={addColumn}>
                添加一列
              </Button>
            </span>
          }
        />
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr>
                <th
                  style={{
                    ...thStyle,
                    width: 46,
                    textAlign: 'center',
                    fontSize: 11,
                    fontWeight: 600,
                    color: 'var(--color-text-tertiary)',
                  }}
                >
                  #
                </th>
                {columns.map((col, ci) => (
                  <th key={ci} style={thStyle}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 4 }}>
                      <Input
                        size="small"
                        value={col.name}
                        placeholder="变量名"
                        onChange={(e) => updateColumn(ci, { name: e.target.value })}
                        style={{ fontWeight: 600 }}
                      />
                      <Button
                        type="text"
                        size="small"
                        danger
                        icon={<MinusOutlined />}
                        onClick={() => removeColumn(ci)}
                        title="删除该列"
                      />
                    </div>
                    <Select
                      size="small"
                      value={col.type || 'text'}
                      style={{ width: '100%' }}
                      onChange={(v) => updateColumn(ci, { type: v as BatchParamsColumn['type'] })}
                      options={COLUMN_TYPES}
                    />
                  </th>
                ))}
                <th style={{ ...thStyle, width: 40 }} />
              </tr>
            </thead>
            <tbody>
              {rows.map((row, ri) => (
                <tr key={ri}>
                  <td
                    style={{
                      ...tdStyle,
                      textAlign: 'center',
                      color: 'var(--color-text-tertiary)',
                      fontSize: 11,
                    }}
                  >
                    {ri + 1}
                  </td>
                  {columns.map((col, ci) => {
                    const raw = row[ci] || '';
                    const isB64 = col.type === 'image_base64';
                    const cellKey = ri + '-' + ci;
                    const display = isB64 && raw.length > 30 ? raw.slice(0, 30) + '...' : raw;
                    return (
                      <td key={ci} style={tdStyle}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                          <Input
                            size="small"
                            value={display}
                            placeholder={
                              col.type === 'json'
                                ? '{"key": "value"}'
                                : col.type === 'boolean'
                                  ? 'true / false'
                                  : isB64
                                    ? 'base64 / data:image URL'
                                    : ''
                            }
                            title={isB64 ? raw : undefined}
                            onChange={(e) => setCell(ri, ci, e.target.value)}
                            variant="borderless"
                          />
                          {isB64 && (
                            <Tooltip title="选择本地图片，转成 base64 填入">
                              <Button
                                type="text"
                                size="small"
                                icon={<PictureOutlined />}
                                loading={convertingCell === cellKey}
                                onClick={() => handlePickImage(ri, ci)}
                              />
                            </Tooltip>
                          )}
                        </div>
                      </td>
                    );
                  })}
                  <td style={{ ...tdStyle, textAlign: 'center' }}>
                    <Button
                      type="text"
                      size="small"
                      danger
                      icon={<MinusOutlined />}
                      onClick={() => removeRow(ri)}
                      title="删除该行"
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 && (
            <div
              style={{
                textAlign: 'center',
                padding: '16px 0',
                fontSize: 12,
                color: 'var(--color-text-tertiary)',
              }}
            >
              还没有数据行，点「添加行」或「粘贴导入」
            </div>
          )}
        </div>
      )}

      {rows.length > 0 && (
        <div style={{ marginTop: 8, fontSize: 12, color: 'var(--color-text-tertiary)' }}>
          将执行 {rows.length} 次请求（{columns.length} 个变量 / 共 {totalCells} 个单元格）
          ；数值/布尔/JSON 列的值会在执行时校验，非法值该变量不注入
        </div>
      )}

      <Modal
        title="粘贴导入前置参数"
        open={importOpen}
        onOk={handleImport}
        onCancel={() => setImportOpen(false)}
        okText="导入"
        cancelText="取消"
        width={560}
      >
        <div style={{ marginBottom: 8, fontSize: 12, color: 'var(--color-text-tertiary)' }}>支持两种格式：</div>
        <ul
          style={{
            margin: '0 0 10px',
            paddingLeft: 18,
            fontSize: 12,
            color: 'var(--color-text-tertiary)',
            lineHeight: 1.8,
          }}
        >
          <li>
            <code>JSON 数组</code>：<code>[{'{"deviceId":"d1"}'}, ...]</code>（对象的 key 即列名）
          </li>
          <li>
            <code>CSV / TSV</code>：首行是列名，下面每行是一条数据
          </li>
        </ul>
        <Input.TextArea
          rows={10}
          value={importText}
          onChange={(e) => setImportText(e.target.value)}
          placeholder={'deviceId,tenant\nd1,a\nd2,b'}
          style={{ fontFamily: 'monospace', fontSize: 12 }}
        />
      </Modal>

      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        style={{ display: 'none' }}
        onChange={handleImageSelected}
      />
    </div>
  );
};

export default BatchParamsEditor;

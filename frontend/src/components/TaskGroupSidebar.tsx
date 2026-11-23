import React, { useMemo, useState } from 'react';
import { Button, Empty, Input, Modal, Popconfirm, Select, Space, Tooltip, message } from 'antd';
import {
  AppstoreOutlined,
  DeleteOutlined,
  FolderAddOutlined,
  FolderOutlined,
  PlusOutlined,
  SettingOutlined,
} from '@ant-design/icons';
import type { TaskGroup, GlobalParameter } from '../types';
import { taskGroupApi } from '../services/api';
import './TaskGroupSidebar.css';

/**
 * 定时任务分组侧栏。
 *
 * - 树形展示，支持多级嵌套；
 * - 可新建分组 / 子分组 / 重命名 / 删除；
 * - 分组可绑定执行环境并配置分组级环境变量（组内任务共享）。
 */
interface TaskGroupSidebarProps {
  groups: TaskGroup[];
  environments: GlobalParameter[];
  /** 当前选中的分组 id；null 表示"全部任务" */
  selectedGroupId: number | null;
  onSelectGroup: (groupId: number | null) => void;
  onGroupsChanged: () => void;
}

interface GroupFormState {
  id?: number;
  name: string;
  parentId: number | null;
  environmentId: number | null;
  variables: Array<{ key: string; value: string }>;
}

const EMPTY_FORM: GroupFormState = {
  name: '',
  parentId: null,
  environmentId: null,
  variables: [],
};

/** 把分组树拍平成带层级的列表，方便渲染 */
function flatten(groups: TaskGroup[], depth = 0): Array<{ group: TaskGroup; depth: number }> {
  const out: Array<{ group: TaskGroup; depth: number }> = [];
  (groups || []).forEach((g) => {
    out.push({ group: g, depth });
    if (g.children?.length) {
      out.push(...flatten(g.children, depth + 1));
    }
  });
  return out;
}

const TaskGroupSidebar: React.FC<TaskGroupSidebarProps> = ({
  groups,
  environments,
  selectedGroupId,
  onSelectGroup,
  onGroupsChanged,
}) => {
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState<GroupFormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);

  const flat = useMemo(() => flatten(groups), [groups]);

  const openCreate = (parentId: number | null) => {
    setForm({ ...EMPTY_FORM, parentId });
    setFormOpen(true);
  };

  const openEdit = (group: TaskGroup) => {
    setForm({
      id: group.id,
      name: group.name,
      parentId: group.parent_id ?? null,
      environmentId: group.environment_id ?? null,
      variables: (group.variables || []).map((v) => ({ key: v.key, value: v.value })),
    });
    setFormOpen(true);
  };

  const handleSave = async () => {
    if (!form.name.trim()) {
      message.error('分组名称不能为空');
      return;
    }
    setSaving(true);
    try {
      const payload = {
        name: form.name.trim(),
        parent_id: form.parentId,
        environment_id: form.environmentId,
        variables: form.variables.filter((v) => v.key),
      };
      if (form.id) {
        const res = await taskGroupApi.updateGroup(form.id, payload);
        if (res.code !== 200) {
          message.error(res.message || '分组更新失败');
          return;
        }
        message.success('分组已更新');
      } else {
        const res = await taskGroupApi.createGroup(payload);
        if (res.code !== 200) {
          message.error(res.message || '分组创建失败');
          return;
        }
        message.success('分组已创建');
      }
      setFormOpen(false);
      onGroupsChanged();
    } catch (err: any) {
      console.error('Save group failed:', err);
      message.error(err?.response?.data?.detail || err?.response?.data?.message || '保存分组失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (group: TaskGroup) => {
    try {
      const res = await taskGroupApi.deleteGroup(group.id);
      if (res.code === 200) {
        message.success('分组已删除');
        if (selectedGroupId === group.id) {
          onSelectGroup(null);
        }
        onGroupsChanged();
      } else {
        message.error(res.message || '分组删除失败');
      }
    } catch (err: any) {
      console.error('Delete group failed:', err);
      message.error(err?.response?.data?.detail || '分组删除失败');
    }
  };

  const envOptions = (environments || []).map((e) => ({ label: e.name, value: e.id }));

  return (
    <div className="stm-group-sidebar">
      <div className="stm-group-sidebar-head">
        <span className="stm-group-sidebar-title">
          <AppstoreOutlined style={{ fontSize: 13, color: 'var(--color-text-tertiary)' }} />
          分组
        </span>
        <Tooltip title="新建分组">
          <Button
            type="text"
            size="small"
            icon={<PlusOutlined />}
            onClick={() => openCreate(null)}
          />
        </Tooltip>
      </div>

      <div className="stm-group-list">
        <div
          className={`stm-group-item ${selectedGroupId === null ? 'is-active' : ''}`}
          onClick={() => onSelectGroup(null)}
        >
          <span className="stm-group-name">全部任务</span>
        </div>

        {flat.length === 0 && (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无分组" />
        )}

        {flat.map(({ group, depth }) => (
          <div
            key={group.id}
            className={`stm-group-item ${selectedGroupId === group.id ? 'is-active' : ''}`}
            style={{ paddingLeft: 8 + depth * 14 }}
            onClick={() => onSelectGroup(group.id)}
          >
            <FolderOutlined
              className="stm-group-icon"
              style={{ marginLeft: depth * 14 }}
            />
            <span className="stm-group-name" title={group.name}>
              {group.name}
            </span>
            {group.task_count ? <span className="stm-group-count">{group.task_count}</span> : null}
            <span className="stm-group-actions">
              <Tooltip title="新建子分组">
                <Button
                  type="text"
                  size="small"
                  icon={<FolderAddOutlined />}
                  onClick={(e) => {
                    e.stopPropagation();
                    openCreate(group.id);
                  }}
                />
              </Tooltip>
              <Tooltip title="编辑分组">
                <Button
                  type="text"
                  size="small"
                  icon={<SettingOutlined />}
                  onClick={(e) => {
                    e.stopPropagation();
                    openEdit(group);
                  }}
                />
              </Tooltip>
              <Popconfirm
                title="确定删除该分组？"
                description="子分组会一并删除，组内任务会移出分组（不会被删除）"
                onConfirm={(e) => {
                  e?.stopPropagation();
                  handleDelete(group);
                }}
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
            </span>
          </div>
        ))}
      </div>

      <Modal
        title={form.id ? '编辑分组' : '新建分组'}
        open={formOpen}
        onOk={handleSave}
        onCancel={() => setFormOpen(false)}
        okText="保存"
        cancelText="取消"
        confirmLoading={saving}
        width={560}
      >
        <div style={{ marginBottom: 12 }}>
          <div style={{ marginBottom: 6, fontSize: 12, fontWeight: 600, color: 'var(--color-text-secondary)' }}>
            分组名称
          </div>
          <Input
            placeholder="如「运维任务」「每日巡检」"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        </div>

        <div style={{ marginBottom: 12 }}>
          <div style={{ marginBottom: 6, fontSize: 12, fontWeight: 600, color: 'var(--color-text-secondary)' }}>
            上级分组
          </div>
          <Select
            style={{ width: '100%' }}
            placeholder="不选则创建为顶级分组"
            value={form.parentId}
            allowClear
            onChange={(v) => setForm({ ...form, parentId: v ?? null })}
            options={flat
              .filter(({ group }) => group.id !== form.id)
              .map(({ group, depth }) => ({
                label: `${'　'.repeat(depth)}${group.name}`,
                value: group.id,
              }))}
          />
        </div>

        <div style={{ marginBottom: 12 }}>
          <div style={{ marginBottom: 6, fontSize: 12, fontWeight: 600, color: 'var(--color-text-secondary)' }}>
            绑定执行环境
          </div>
          <Select
            style={{ width: '100%' }}
            placeholder="组内任务默认使用该环境（任务自身设置优先）"
            value={form.environmentId}
            allowClear
            onChange={(v) => setForm({ ...form, environmentId: v ?? null })}
            options={envOptions}
          />
        </div>

        <div>
          <div
            style={{
              marginBottom: 6,
              fontSize: 12,
              fontWeight: 600,
              color: 'var(--color-text-secondary)',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
            }}
          >
            <span>分组环境变量</span>
            <span style={{ fontWeight: 400, color: 'var(--color-text-tertiary)' }}>
              组内任务共享；优先级：分组变量 &lt; 环境变量 &lt; 任务参数
            </span>
          </div>
          {form.variables.length === 0 && (
            <div style={{ fontSize: 12, color: 'var(--color-text-tertiary)', marginBottom: 6 }}>
              暂无变量
            </div>
          )}
          {form.variables.map((v, idx) => (
            <Space.Compact key={idx} style={{ width: '100%', marginBottom: 6 }}>
              <Input
                placeholder="变量名"
                value={v.key}
                onChange={(e) => {
                  const next = [...form.variables];
                  next[idx] = { ...next[idx], key: e.target.value };
                  setForm({ ...form, variables: next });
                }}
                style={{ width: '35%' }}
              />
              <Input
                placeholder="变量值"
                value={v.value}
                onChange={(e) => {
                  const next = [...form.variables];
                  next[idx] = { ...next[idx], value: e.target.value };
                  setForm({ ...form, variables: next });
                }}
              />
              <Button
                danger
                icon={<DeleteOutlined />}
                onClick={() =>
                  setForm({ ...form, variables: form.variables.filter((_, i) => i !== idx) })
                }
              />
            </Space.Compact>
          ))}
          <Button
            type="dashed"
            size="small"
            icon={<PlusOutlined />}
            onClick={() =>
              setForm({ ...form, variables: [...form.variables, { key: '', value: '' }] })
            }
          >
            添加变量
          </Button>
        </div>
      </Modal>
    </div>
  );
};

export default TaskGroupSidebar;

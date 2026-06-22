import React, { useMemo, useState } from 'react';
import { Layout, Typography, Button, Popover, Space, Empty, Badge } from 'antd';
import { PlusOutlined, AppstoreOutlined, EllipsisOutlined, CaretDownFilled, CaretRightFilled, FolderFilled, FileFilled, EditOutlined, FolderAddOutlined, DeleteOutlined } from '@ant-design/icons';
import { Module } from '../types';

const { Sider } = Layout;
const { Text } = Typography;

interface ModuleSidebarProps {
  modules: Module[];
  selectedModule: number | string;
  onSelectModule: (module: Module) => void;
  onSelectAllModules: () => void;
  onDeleteModule: (id: number) => void;
  onEditModule: (module: Module) => void;
  onOpenAddModuleModal: (parentId?: number) => void;
}

const ModuleSidebar: React.FC<ModuleSidebarProps> = ({
  modules,
  selectedModule,
  onSelectModule,
  onSelectAllModules,
  onDeleteModule,
  onEditModule,
  onOpenAddModuleModal
}) => {
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());

  // Build parent -> children map
  const childrenMap = useMemo(() => {
    const map = new Map<number | null, Module[]>();
    modules.forEach(m => {
      const key = m.parent_id ?? null;
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(m);
    });
    return map;
  }, [modules]);

  const rootModules = childrenMap.get(null) || [];

  const toggleExpand = (id: number) => {
    setExpandedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const renderModule = (module: Module, depth: number) => {
    const children = childrenMap.get(module.id!) || [];
    const hasChildren = children.length > 0;
    const isExpanded = expandedIds.has(module.id!);
    const isSelected = selectedModule === module.id;

    return (
      <div key={module.id}>
        <div
          className="module-item"
          onClick={() => onSelectModule(module)}
          style={{
            display: 'flex',
            alignItems: 'center',
            height: 34,
            paddingLeft: 12 + depth * 20,
            paddingRight: 8,
            margin: '1px 6px',
            borderRadius: 'var(--radius-md)',
            cursor: 'pointer',
            transition: 'all 180ms ease',
            background: isSelected ? 'var(--color-primary-bg)' : 'transparent',
            boxShadow: isSelected ? 'inset 3px 0 0 0 var(--color-primary)' : 'none',
            position: 'relative',
          }}
          onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.background = 'var(--color-border-light)'; }}
          onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.background = 'transparent'; }}
        >
          {/* Expand/collapse toggle */}
          <span
            style={{
              width: 18,
              height: 18,
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              flex: '0 0 auto',
              cursor: hasChildren ? 'pointer' : 'default',
              color: hasChildren ? 'var(--color-text-tertiary)' : 'transparent',
              fontSize: 10,
              transition: 'color 150ms ease, transform 200ms ease',
              transform: isExpanded ? 'rotate(0deg)' : 'rotate(0deg)',
              marginRight: 2,
            }}
            onClick={(e) => {
              if (hasChildren) {
                e.stopPropagation();
                toggleExpand(module.id!);
              }
            }}
          >
            {hasChildren ? (
              isExpanded ? <CaretDownFilled /> : <CaretRightFilled />
            ) : (
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--color-border)', display: 'block' }} />
            )}
          </span>

          {/* Icon */}
          <span style={{
            flex: '0 0 auto',
            fontSize: 14,
            marginRight: 8,
            color: hasChildren ? (isSelected ? 'var(--color-primary)' : '#d4a843') : (isSelected ? 'var(--color-primary)' : 'var(--color-text-disabled)'),
            transition: 'color 180ms ease',
            display: 'inline-flex',
          }}>
            {hasChildren ? <FolderFilled /> : <FileFilled />}
          </span>

          {/* Name */}
          <span style={{
            flex: '1 1 0',
            minWidth: 0,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            fontSize: 'var(--font-size-sm)',
            color: isSelected ? 'var(--color-primary)' : 'var(--color-text)',
            fontWeight: isSelected ? 'var(--font-weight-medium)' : 'var(--font-weight-normal)',
            transition: 'color 180ms ease',
            lineHeight: '34px',
          }}>
            {module.module_name}
          </span>

          {/* Actions — hover only */}
          <div className="module-item-actions" style={{ flex: '0 0 auto', opacity: 0, transition: 'opacity 120ms ease' }}>
            <Popover
              content={(
                <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                  <Button type="text" size="small" block icon={<FolderAddOutlined />} onClick={(e) => { e.stopPropagation(); onOpenAddModuleModal(module.id!); }} style={{ justifyContent: 'flex-start' }}>新增子模块</Button>
                  <Button type="text" size="small" block icon={<EditOutlined />} onClick={(e) => { e.stopPropagation(); onEditModule(module); }} style={{ justifyContent: 'flex-start' }}>编辑</Button>
                  <Button type="text" danger size="small" block icon={<DeleteOutlined />} onClick={(e) => { e.stopPropagation(); onDeleteModule(module.id!); }} style={{ justifyContent: 'flex-start' }}>删除</Button>
                </div>
              )}
              trigger="click"
              placement="right"
            >
              <Button
                type="text"
                icon={<EllipsisOutlined />}
                size="small"
                onClick={(e) => e.stopPropagation()}
                style={{ padding: 0, height: 20, width: 20, display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: 'var(--radius-sm)' }}
              />
            </Popover>
          </div>
        </div>

        {/* Children */}
        {hasChildren && isExpanded && (
          <div style={{ position: 'relative' }}>
            {/* Vertical guide line */}
            <div style={{
              position: 'absolute',
              left: 12 + depth * 20 + 8,
              top: 0,
              bottom: 8,
              width: 1,
              background: 'var(--color-border)',
            }} />
            {children.map(child => renderModule(child, depth + 1))}
          </div>
        )}
      </div>
    );
  };

  const isAllSelected = selectedModule === 0;

  return (
    <Sider width={240} style={{ background: 'var(--color-bg-elevated)', borderRight: '1px solid var(--color-border)' }}>
      <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--color-border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Space size={8} align="center">
          <Text strong style={{ fontSize: 'var(--font-size-base)' }}>模块</Text>
          <Badge count={modules.length} style={{ backgroundColor: 'var(--color-border)', color: 'var(--color-text-secondary)', boxShadow: 'none', fontSize: 11, fontWeight: 600 }} />
        </Space>
        <Button type="text" size="small" icon={<PlusOutlined />} onClick={() => onOpenAddModuleModal()} style={{ color: 'var(--color-primary)' }} />
      </div>

      <div style={{ maxHeight: 'calc(100vh - 150px)', overflowY: 'auto', padding: '6px 0' }}>
        {/* 全部模块 */}
        <div
          onClick={onSelectAllModules}
          style={{
            padding: '0 12px 0 16px',
            cursor: 'pointer',
            background: isAllSelected ? 'var(--color-primary-bg)' : 'transparent',
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            height: 36,
            transition: 'all 180ms ease',
            color: isAllSelected ? 'var(--color-primary)' : 'var(--color-text)',
            fontWeight: isAllSelected ? 'var(--font-weight-medium)' : 'var(--font-weight-normal)',
            borderRadius: 'var(--radius-md)',
            margin: '0 6px',
            boxShadow: isAllSelected ? 'inset 3px 0 0 0 var(--color-primary)' : 'none',
          }}
          onMouseEnter={(e) => { if (!isAllSelected) e.currentTarget.style.background = 'var(--color-border-light)'; }}
          onMouseLeave={(e) => { if (!isAllSelected) e.currentTarget.style.background = 'transparent'; }}
        >
          <AppstoreOutlined style={{ color: isAllSelected ? 'var(--color-primary)' : 'var(--color-text-tertiary)', fontSize: 15 }} />
          <span style={{ flex: 1, fontSize: 'var(--font-size-sm)' }}>全部模块</span>
          <Text type="secondary" style={{ fontSize: 12 }}>{modules.length}</Text>
        </div>

        {modules.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="暂无模块"
            style={{ padding: '40px 0', margin: 0 }}
          >
            <Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => onOpenAddModuleModal()}>
              新增模块
            </Button>
          </Empty>
        ) : (
          <div style={{ padding: '2px 0' }}>
            {rootModules.map(m => renderModule(m, 0))}
          </div>
        )}
      </div>
    </Sider>
  );
};

export default ModuleSidebar;

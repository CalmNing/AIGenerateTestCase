import React, { useMemo, useState, useEffect } from 'react';
import { Layout, Typography, Button, Space, Popover, Tree, ConfigProvider } from 'antd';
import { PlusOutlined, ProjectTwoTone, EllipsisOutlined, FolderOutlined, FileOutlined, DownOutlined } from '@ant-design/icons';
import { Module } from '../types';
import type { TreeDataNode, TreeProps } from 'antd';

const { Sider } = Layout;
const { Title } = Typography;

interface ModuleSidebarProps {
  modules: Module[];
  selectedModule: number | string; // 当前选中的模块
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
  // 构建树形数据
  const buildTreeData = (modules: Module[]): TreeDataNode[] => {
    // 创建 id -> module 映射
    const moduleMap = new Map<number, Module>();
    modules.forEach(m => {
      if (m.id) moduleMap.set(m.id, m);
    });

    // 递归构建子树
    const buildNode = (module: Module): TreeDataNode => {
      const children = modules.filter(m => m.parent_id === module.id);
      return {
        key: String(module.id),
        title: (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            maxWidth: '160px',
            minWidth: '100%',
            // height: '36px',
            // fontSize: '15px',
            overflow: 'hidden',
          }}>
            <span style={{
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              flex: 1,
              minWidth: 0,
            }}>{module.module_name}</span>


            <Popover
              content={(
                <Space direction="vertical" size="small">
                  <Button
                    type="text"
                    size="small"
                    onClick={(e) => {
                      e.stopPropagation();
                      onOpenAddModuleModal(module.id!);
                    }}
                  >
                    新增子模块
                  </Button>
                  <Button
                    type="text"
                    size="small"
                    onClick={(e) => {
                      e.stopPropagation();
                      onEditModule(module);
                    }}
                  >
                    编辑模块
                  </Button>
                  <Button
                    type="text"
                    danger
                    size="small"
                    onClick={(e) => {
                      e.stopPropagation();
                      onDeleteModule(module.id!);
                    }}
                  >
                    删除模块
                  </Button>
                </Space>
              )}
              trigger="click"
              placement="right"
            >
              <Button
                type="text"
                icon={<EllipsisOutlined />}
                size="small"
                onClick={(e) => e.stopPropagation()}
                style={{ padding: '0 4px' }}
              />
            </Popover>
          </div>
        ),
        icon: children.length > 0 ? <FolderOutlined /> : <FileOutlined />,
        children: children.length > 0 ? children.map(buildNode) : undefined,
      };
    };

    // 找出顶层模块（parent_id 为 null）
    const rootModules = modules.filter(m => m.parent_id === null);
    return rootModules.map(buildNode);
  };

  const treeData = useMemo(() => buildTreeData(modules), [modules]);

  // 获取所有模块的 key（用于展开）
  const allModuleKeys = useMemo(() => modules.map(m => String(m.id)), [modules]);

  // 展开状态管理
  const [expandedKeys, setExpandedKeys] = useState<React.Key[]>([]);

  // 模块变化时默认展开所有节点
  useEffect(() => {
    setExpandedKeys(allModuleKeys);
  }, [allModuleKeys.join(',')]);

  // 展开/收缩处理
  const handleExpand: TreeProps['onExpand'] = (keys) => {
    setExpandedKeys(keys);
  };

  // 选择模块
  const handleSelect: TreeProps['onSelect'] = (selectedKeys) => {
    if (selectedKeys.length > 0 && selectedKeys[0] !== 'all') {
      const moduleId = Number(selectedKeys[0]);
      const module = modules.find(m => m.id === moduleId);
      if (module) {
        onSelectModule(module);
      }
    }
  };

  return (
    <Sider width={240} style={{ background: '#fff', borderRight: '1px solid #f0f0f0', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '16px', borderBottom: '1px solid #f0f0f0' }}>
        <Space style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%' }}>
          <Title level={5} style={{ margin: 0 }}>模块</Title>
          <Button
            type="primary"
            size="middle"
            icon={<PlusOutlined />}
            onClick={() => onOpenAddModuleModal()}
          >
            新增模块
          </Button>
        </Space>
      </div>

      {/* 全部选项 */}


      {/* 模块树 */}
      <div style={{ flex: 1, overflow: 'auto', padding: '1px 0' }}>
        <div
          onClick={onSelectAllModules}
          style={{
            padding: '0 16px',
            cursor: 'pointer',
            background: selectedModule === 0 ? '#e6f7ff' : 'transparent',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            height: '36px',
            fontSize: '15px',
          }}
        >
          <ProjectTwoTone />
          <span>全部</span>
        </div>

        <ConfigProvider

          theme={{
            components: {
              Tree: {
                titleHeight: 36,
              },
            },
          }}
        >
          <Tree
            blockNode
            selectedKeys={selectedModule && selectedModule !== 0 ? [String(selectedModule)] : []}
            expandedKeys={expandedKeys}
            onExpand={handleExpand}
            onSelect={handleSelect}
            treeData={treeData}
            style={{ background: 'transparent', fontSize: '15px' }}
          />
        </ConfigProvider>
      </div>
    </Sider>
  );
};

export default ModuleSidebar;

import React from 'react';
import { Layout, Menu, Typography, Button, Space, Popover } from 'antd';
import { PlusOutlined, ProjectTwoTone, EllipsisOutlined } from '@ant-design/icons';
import { Module } from '../types';

const { Sider } = Layout;
const { Title } = Typography;

interface ModuleSidebarProps {
  modules: Module[];
  selectedModule: number|string; // 当前选中的模块
  onSelectModule: (module: Module) => void;
  onSelectAllModules: () => void;
  onDeleteModule: (id: number) => void;
  onEditModule: (module: Module) => void;
  onOpenAddModuleModal: () => void;
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
  const menuItems = [
    {
      key: 'all',
      label: (
        <Space>
          <span style={{
            fontSize: "16px",
            lineHeight: "1.5",
            width: "140px",
            height: "40px",
            display: "flex",
            alignItems: "center",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}>全部</span>
        </Space>
      ),
      icon: <ProjectTwoTone />,
      onClick: () => onSelectAllModules()
    },
    ...modules.map(module => ({
      key: String(module.id),
      label: (
        <Space>
          <span style={{
            fontSize: "16px",
            lineHeight: "1.5",
            width: "140px",
            height: "40px",
            display: "flex",
            alignItems: "center",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}>{module.module_name}</span>

          <Popover
            content={(
              <Space direction="vertical" size="small">
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
            // title="操作"
            trigger="click"
            placement="bottom"
          >
            <Button
              type="text"
              icon={<EllipsisOutlined />}
              size="small"
              onClick={(e) => e.stopPropagation()}
            />
          </Popover>
        </Space>
      ),
      icon: <ProjectTwoTone />,
      onClick: () => onSelectModule(module)
    }))
  ];

  return (
    <Sider width={240} style={{ background: '#fff', borderRight: '1px solid #f0f0f0', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '16px', borderBottom: '1px solid #f0f0f0' }}>
        <Space style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%' }}>
          <Title level={5} style={{ margin: 0 }}>模块</Title>
          <Button
            type="primary"
            size="middle"
            icon={<PlusOutlined />}
            onClick={onOpenAddModuleModal}
          >
            新增模块
          </Button>
        </Space>
      </div>
      <Menu
        mode="inline"
        selectedKeys={selectedModule ? [String(selectedModule)] : ['all']}
        style={{ borderRight: 0, flex: 1, overflow: 'auto' }}
        items={menuItems}
      />
    </Sider>
  );
};

export default ModuleSidebar;
import React, { useEffect, useState } from 'react';
import {
  Modal,
  Button,
  Form,
  Input,
  InputNumber,
  Select,
  Popconfirm,
  Space,
  message,
  Empty,
  Divider,
  Spin,
  Tooltip,
} from 'antd';
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  LinkOutlined,
  AppstoreOutlined,
} from '@ant-design/icons';
import { navLinkApi } from '../services/api';
import { NavLink, NavTreeNode } from '../types';

interface NavLinkManagerModalProps {
  visible: boolean;
  onCancel: () => void;
  onSaved: () => void;
}

const openTypeOptions = [
  { value: 'blank', label: '新标签页打开' },
  { value: 'self', label: '当前页打开' },
];

const NavLinkManagerModal: React.FC<NavLinkManagerModalProps> = ({ visible, onCancel, onSaved }) => {
  const [tree, setTree] = useState<NavTreeNode[]>([]);
  const [loading, setLoading] = useState(false);

  const [catModalVisible, setCatModalVisible] = useState(false);
  const [editingCategory, setEditingCategory] = useState<NavTreeNode['category'] | null>(null);
  const [catForm] = Form.useForm();

  const [linkModalVisible, setLinkModalVisible] = useState(false);
  const [editingLink, setEditingLink] = useState<NavLink | null>(null);
  const [linkDefaultCategoryId, setLinkDefaultCategoryId] = useState<number | undefined>(undefined);
  const [linkForm] = Form.useForm();

  const loadTree = async () => {
    setLoading(true);
    try {
      const res = await navLinkApi.getTree();
      if (res.code === 200 && res.data) {
        setTree(res.data);
      } else {
        setTree([]);
      }
    } catch (e) {
      console.error('加载导航树失败', e);
      setTree([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (visible) {
      loadTree();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible]);

  const refresh = () => {
    loadTree();
    onSaved();
  };

  // ===== 分类 =====
  const openCreateCategory = () => {
    setEditingCategory(null);
    catForm.setFieldsValue({ name: '', icon: '', sort_order: tree.length });
    setCatModalVisible(true);
  };

  const openEditCategory = (node: NavTreeNode) => {
    setEditingCategory(node.category);
    catForm.setFieldsValue({
      name: node.category.name,
      icon: node.category.icon ?? '',
      sort_order: node.category.sort_order,
    });
    setCatModalVisible(true);
  };

  const handleSaveCategory = async () => {
    const values = await catForm.validateFields();
    try {
      if (editingCategory) {
        await navLinkApi.updateCategory(editingCategory.id, values);
        message.success('分类已更新');
      } else {
        await navLinkApi.createCategory(values);
        message.success('分类已创建');
      }
      setCatModalVisible(false);
      refresh();
    } catch (e: any) {
      message.error(e?.response?.data?.message || '保存失败');
    }
  };

  const handleDeleteCategory = async (id: number) => {
    try {
      await navLinkApi.deleteCategory(id);
      message.success('分类已删除');
      refresh();
    } catch (e: any) {
      message.error(e?.response?.data?.message || '删除失败');
    }
  };

  // ===== 链接 =====
  const openCreateLink = (categoryId?: number) => {
    setEditingLink(null);
    setLinkDefaultCategoryId(categoryId ?? tree[0]?.category.id);
    linkForm.setFieldsValue({
      category_id: categoryId ?? tree[0]?.category.id,
      title: '',
      url: '',
      description: '',
      icon: '',
      open_type: 'blank',
      sort_order: 0,
    });
    setLinkModalVisible(true);
  };

  const openEditLink = (link: NavLink) => {
    setEditingLink(link);
    linkForm.setFieldsValue({
      category_id: link.category_id,
      title: link.title,
      url: link.url,
      description: link.description ?? '',
      icon: link.icon ?? '',
      open_type: link.open_type,
      sort_order: link.sort_order,
    });
    setLinkModalVisible(true);
  };

  const handleSaveLink = async () => {
    const values = await linkForm.validateFields();
    try {
      if (editingLink) {
        await navLinkApi.updateLink(editingLink.id, values);
        message.success('链接已更新');
      } else {
        await navLinkApi.createLink(values);
        message.success('链接已创建');
      }
      setLinkModalVisible(false);
      refresh();
    } catch (e: any) {
      message.error(e?.response?.data?.message || '保存失败');
    }
  };

  const handleDeleteLink = async (id: number) => {
    try {
      await navLinkApi.deleteLink(id);
      message.success('链接已删除');
      refresh();
    } catch (e: any) {
      message.error(e?.response?.data?.message || '删除失败');
    }
  };

  return (
    <Modal
      title={
        <Space>
          <AppstoreOutlined style={{ color: 'var(--color-primary)' }} />
          <span>资源导航管理</span>
        </Space>
      }
      open={visible}
      onCancel={onCancel}
      width={760}
      footer={
        <Button type="primary" onClick={onCancel}>
          完成
        </Button>
      }
      destroyOnClose
    >
      <div style={{ marginBottom: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ color: 'var(--color-text-tertiary)', fontSize: 13 }}>分类与链接全员可见，仅配置管理员可编辑</span>
        <Button type="primary" size="small" icon={<PlusOutlined />} onClick={openCreateCategory}>
          新增分类
        </Button>
      </div>

      <Spin spinning={loading}>
        {tree.length === 0 ? (
          <Empty description="暂无分类，点击右上角新增" style={{ padding: '32px 0' }} />
        ) : (
          tree.map((node) => (
            <div
              key={node.category.id}
              style={{
                border: '1px solid var(--color-border)',
                borderRadius: 'var(--radius-lg)',
                padding: 12,
                marginBottom: 12,
                background: 'var(--color-bg)',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                <Space>
                  <span style={{ fontSize: 18 }}>{node.category.icon || '📁'}</span>
                  <strong style={{ fontSize: 15 }}>{node.category.name}</strong>
                  <span style={{ color: 'var(--color-text-tertiary)', fontSize: 12 }}>排序 {node.category.sort_order}</span>
                </Space>
                <Space size={4}>
                  <Tooltip title="新增链接">
                    <Button size="small" type="text" icon={<LinkOutlined />} onClick={() => openCreateLink(node.category.id)} />
                  </Tooltip>
                  <Tooltip title="编辑分类">
                    <Button size="small" type="text" icon={<EditOutlined />} onClick={() => openEditCategory(node)} />
                  </Tooltip>
                  <Popconfirm
                    title="删除该分类？"
                    description="分类下的所有链接也会被删除"
                    onConfirm={() => handleDeleteCategory(node.category.id)}
                    okText="删除"
                    cancelText="取消"
                  >
                    <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </Space>
              </div>

              <Divider style={{ margin: '4px 0 8px' }} />

              {node.links.length === 0 ? (
                <div style={{ color: 'var(--color-text-tertiary)', fontSize: 13, padding: '4px 0' }}>暂无链接</div>
              ) : (
                node.links.map((link) => (
                  <div
                    key={link.id}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '6px 8px',
                      borderRadius: 'var(--radius-md)',
                      border: '1px solid var(--color-border-light)',
                      marginBottom: 6,
                      background: 'var(--color-bg-elevated)',
                    }}
                  >
                    <Space>
                      <span>{link.icon || '🔗'}</span>
                      <div style={{ minWidth: 0 }}>
                        <div style={{ fontSize: 13, fontWeight: 500 }}>{link.title}</div>
                        <div
                          style={{
                            fontSize: 12,
                            color: 'var(--color-text-tertiary)',
                            maxWidth: 420,
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {link.url}
                        </div>
                      </div>
                    </Space>
                    <Space size={4}>
                      <Tooltip title="编辑链接">
                        <Button size="small" type="text" icon={<EditOutlined />} onClick={() => openEditLink(link)} />
                      </Tooltip>
                      <Popconfirm title="删除该链接？" onConfirm={() => handleDeleteLink(link.id)} okText="删除" cancelText="取消">
                        <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                      </Popconfirm>
                    </Space>
                  </div>
                ))
              )}
            </div>
          ))
        )}
      </Spin>

      {/* 分类编辑弹窗 */}
      <Modal
        title={editingCategory ? '编辑分类' : '新增分类'}
        open={catModalVisible}
        onOk={handleSaveCategory}
        onCancel={() => setCatModalVisible(false)}
        destroyOnClose
        okText="保存"
        cancelText="取消"
      >
        <Form form={catForm} layout="vertical">
          <Form.Item name="name" label="分类名称" rules={[{ required: true, message: '请输入分类名称' }]}>
            <Input placeholder="如：接口文档、快捷工具" maxLength={64} />
          </Form.Item>
          <Form.Item name="icon" label="图标（emoji 或文字）">
            <Input placeholder="如：📚 或 api" maxLength={32} />
          </Form.Item>
          <Form.Item name="sort_order" label="排序权重（越小越靠前）">
            <InputNumber min={0} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 链接编辑弹窗 */}
      <Modal
        title={editingLink ? '编辑链接' : '新增链接'}
        open={linkModalVisible}
        onOk={handleSaveLink}
        onCancel={() => setLinkModalVisible(false)}
        destroyOnClose
        okText="保存"
        cancelText="取消"
      >
        <Form form={linkForm} layout="vertical">
          <Form.Item name="category_id" label="所属分类" rules={[{ required: true, message: '请选择分类' }]}>
            <Select
              placeholder="选择分类"
              options={tree.map((n) => ({ value: n.category.id, label: `${n.category.icon || '📁'} ${n.category.name}` }))}
            />
          </Form.Item>
          <Form.Item name="title" label="标题" rules={[{ required: true, message: '请输入标题' }]}>
            <Input placeholder="如：订单服务 Swagger" maxLength={128} />
          </Form.Item>
          <Form.Item name="url" label="地址" rules={[{ required: true, message: '请输入链接地址' }, { type: 'url', message: '请输入合法 URL' }]}>
            <Input placeholder="https://..." maxLength={1024} />
          </Form.Item>
          <Form.Item name="description" label="描述（可选）">
            <Input.TextArea placeholder="一句话说明这个链接的用途" rows={2} maxLength={512} />
          </Form.Item>
          <Form.Item name="icon" label="图标（emoji 或文字，可选）">
            <Input placeholder="如：📘" maxLength={32} />
          </Form.Item>
          <Space style={{ display: 'flex' }} size="large">
            <Form.Item name="open_type" label="打开方式">
              <Select options={openTypeOptions} style={{ width: 160 }} />
            </Form.Item>
            <Form.Item name="sort_order" label="排序权重">
              <InputNumber min={0} style={{ width: 160 }} />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
    </Modal>
  );
};

export default NavLinkManagerModal;

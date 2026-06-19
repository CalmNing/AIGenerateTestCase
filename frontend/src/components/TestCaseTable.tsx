import React, { useState } from 'react';
import { Table, Button, PaginationProps, TableProps, message, Modal, Tooltip, Typography } from 'antd';
import { EyeOutlined, EditOutlined, CheckCircleOutlined, ApiOutlined, DragOutlined, ExclamationCircleOutlined } from '@ant-design/icons';
import { TestCase, TestCaseStatus } from '../types';

const { Text } = Typography;

interface TestCaseTableProps {
  testcases: TestCase[];
  onView: (testcase: TestCase) => void;
  onEdit: (testcase: TestCase) => void;
  onComplete: (testcase: TestCase) => void;
  onDelete: (id: number) => void;
  onBatchDelete: (ids: number[]) => void;
  onMove: (testcase: TestCase) => void;
  onApiExecute?: (testcase: TestCase) => void;
  onBatchMove: (ids: number[]) => void;
}

const levelConfig: Record<number, { label: string; color: string; bg: string }> = {
  1: { label: 'P1', color: '#f43f5e', bg: '#fff1f2' },
  2: { label: 'P2', color: '#f59e0b', bg: '#fffbeb' },
  3: { label: 'P3', color: '#4f46e5', bg: '#eef2ff' },
  4: { label: 'P4', color: '#94a3b8', bg: '#f8fafc' },
};

const statusConfig: Record<string, { label: string; color: string; dot: string }> = {
  PASSED: { label: '已通过', color: '#10b981', dot: '#10b981' },
  FAILED: { label: '未通过', color: '#f43f5e', dot: '#f43f5e' },
  NOT_RUN: { label: '未执行', color: '#94a3b8', dot: '#cbd5e1' },
};

const TestCaseTable: React.FC<TestCaseTableProps> = ({
  testcases,
  onView,
  onEdit,
  onComplete,
  onDelete,
  onBatchDelete,
  onMove,
  onApiExecute,
  onBatchMove
}) => {
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [isBatchDeleteModalVisible, setIsBatchDeleteModalVisible] = useState(false);

  const testcaseColumns = [
    {
      title: '#',
      dataIndex: 'index',
      key: 'index',
      width: 48,
      align: 'center' as const,
      render: (_: any, __: any, idx: number) => (
        <Text type="secondary" style={{ fontSize: 12 }}>{idx + 1}</Text>
      ),
    },
    {
      title: '用例名称',
      dataIndex: 'case_name',
      key: 'case_name',
      ellipsis: true,
      render: (name: string) => (
        <Text strong style={{ fontSize: 13 }}>{name}</Text>
      ),
    },
    {
      title: '级别',
      dataIndex: 'case_level',
      key: 'case_level',
      width: 64,
      align: 'center' as const,
      render: (level: number) => {
        const cfg = levelConfig[level] || levelConfig[4];
        return (
          <span style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            height: 22,
            padding: '0 8px',
            borderRadius: 'var(--radius-full)',
            fontSize: 12,
            fontWeight: 600,
            color: cfg.color,
            background: cfg.bg,
            letterSpacing: '0.02em',
          }}>
            {cfg.label}
          </span>
        );
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      align: 'center' as const,
      render: (status: TestCaseStatus) => {
        const cfg = statusConfig[status] || statusConfig.NOT_RUN;
        return (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
            <span style={{
              width: 7,
              height: 7,
              borderRadius: '50%',
              background: cfg.dot,
              flexShrink: 0,
            }} />
            <span style={{ color: cfg.color }}>{cfg.label}</span>
          </span>
        );
      },
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 110,
      align: 'center' as const,
      render: (time: string) => {
        const date = new Date(time);
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const day = String(date.getDate()).padStart(2, '0');
        const hours = String(date.getHours()).padStart(2, '0');
        const minutes = String(date.getMinutes()).padStart(2, '0');
        return <Text type="secondary" style={{ fontSize: 13 }}>{month}-{day} {hours}:{minutes}</Text>;
      },
    },
    {
      title: 'Bug',
      dataIndex: 'bug_id',
      key: 'bug_id',
      width: 64,
      align: 'center' as const,
      render: (bugId: number | undefined) => {
        if (bugId) {
          return (
            <a
              href={`http://zt.luban.fit/index.php?m=bug&f=view&bugID=${bugId}`}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                height: 22,
                padding: '0 6px',
                borderRadius: 'var(--radius-sm)',
                fontSize: 12,
                fontWeight: 500,
                color: 'var(--color-danger)',
                background: 'var(--color-danger-bg)',
                textDecoration: 'none',
                transition: 'opacity 150ms ease',
              }}
              onMouseEnter={(e) => { e.currentTarget.style.opacity = '0.8'; }}
              onMouseLeave={(e) => { e.currentTarget.style.opacity = '1'; }}
            >
              #{bugId}
            </a>
          );
        }
        return <span style={{ color: 'var(--color-text-disabled)', fontSize: 12 }}>—</span>;
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 160,
      align: 'center' as const,
      render: (_: any, record: TestCase) => (
        <div className="table-action-group" style={{ display: 'inline-flex', gap: 2 }}>
          <Tooltip title="查看" placement="top">
            <Button size="small" type="text" icon={<EyeOutlined />} onClick={() => onView(record)} className="table-action-btn" />
          </Tooltip>
          <Tooltip title="编辑" placement="top">
            <Button size="small" type="text" icon={<EditOutlined />} onClick={() => onEdit(record)} className="table-action-btn" />
          </Tooltip>
          <Tooltip title="执行" placement="top">
            <Button size="small" type="text" icon={<CheckCircleOutlined />} onClick={() => onComplete(record)} className="table-action-btn table-action-btn-success" />
          </Tooltip>
          {onApiExecute && record.api_endpoint_id && (
            <Tooltip title="API 执行" placement="top">
              <Button size="small" type="text" icon={<ApiOutlined />} onClick={() => onApiExecute(record)} className="table-action-btn table-action-btn-primary" />
            </Tooltip>
          )}
          <Tooltip title="移动" placement="top">
            <Button size="small" type="text" icon={<DragOutlined />} onClick={() => onMove(record)} className="table-action-btn" />
          </Tooltip>
        </div>
      ),
    },
  ];

  const rowSelection: TableProps<TestCase>['rowSelection'] = {
    selectedRowKeys,
    onChange: (keys: React.Key[]) => setSelectedRowKeys(keys),
  };

  const handleBatchDelete = () => {
    if (selectedRowKeys.length === 0) {
      message.warning('请选择要删除的测试用例');
      return;
    }
    setIsBatchDeleteModalVisible(true);
  };

  const handleConfirmBatchDelete = () => {
    onBatchDelete(selectedRowKeys.map(Number));
    setSelectedRowKeys([]);
    setIsBatchDeleteModalVisible(false);
  };

  return (
    <>
      {selectedRowKeys.length > 0 && (
        <div className="batch-action-bar">
          <span style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>
            已选 <Text strong style={{ color: 'var(--color-primary)' }}>{selectedRowKeys.length}</Text> 项
          </span>
          <Button size="small" danger icon={<ExclamationCircleOutlined />} onClick={handleBatchDelete}>
            批量删除
          </Button>
          <Button size="small" onClick={() => onBatchMove(selectedRowKeys.map(Number))}>
            批量移动
          </Button>
        </div>
      )}
      <Table
        rowSelection={{ type: 'checkbox', ...rowSelection }}
        columns={testcaseColumns}
        dataSource={testcases}
        rowKey="id"
        pagination={{
          position: ['bottomRight'],
          defaultPageSize: 20,
          size: 'small',
          showTotal: (total) => <Text type="secondary" style={{ fontSize: 12 }}>共 {total} 条</Text>,
        }}
        scroll={{ x: 700, y: '47vh' }}
        tableLayout="fixed"
        size="small"
        locale={{ emptyText: '暂无测试用例' }}
      />
      <Modal
        title="批量删除确认"
        open={isBatchDeleteModalVisible}
        onOk={handleConfirmBatchDelete}
        onCancel={() => setIsBatchDeleteModalVisible(false)}
        okText="确认删除"
        cancelText="取消"
        okType="danger"
      >
        <p>确定要删除选中的 {selectedRowKeys.length} 个测试用例吗？</p>
        <p style={{ color: 'var(--color-danger)', fontSize: 13 }}>此操作不可恢复</p>
      </Modal>
    </>
  );
};

export default TestCaseTable;

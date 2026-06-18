import React, { useState } from 'react';
import { Table, Space, Button, PaginationProps, TableProps, message, Modal, Tag, Tooltip } from 'antd';
import { EyeOutlined, EditOutlined, CheckCircleOutlined, ApiOutlined, DragOutlined, DeleteOutlined } from '@ant-design/icons';
import { TestCase, TestCaseStatus } from '../types';

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

const onShowSizeChange: PaginationProps['onShowSizeChange'] = (current, pageSize) => {
  console.log(current, pageSize);
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
  const [executingId, setExecutingId] = useState<number | null>(null);
  const [isBatchDeleteModalVisible, setIsBatchDeleteModalVisible] = useState(false);

  const testcaseColumns = [
    {
      title: '序号',
      dataIndex: 'index',
      key: 'index',
      width: 60,
      align: 'center' as 'center' | 'left' | 'right',
      render: (_: any, __: any, idx: number) => idx + 1,
    },
    {
      title: '用例名称',
      dataIndex: 'case_name',
      key: 'case_name',
      ellipsis: true,
      width: 200,
      // align: 'center',
    },
    {
      title: '用例级别',
      dataIndex: 'case_level',
      key: 'case_level',
      width: 70,
      align: 'center' as 'center' | 'left' | 'right',
      render: (level: number) => {
        const levelMap: Record<number, string> = {
          1: 'P1',
          2: 'P2',
          3: 'P3',
          4: 'P4'
        };
        return levelMap[level] ?? `P${level}`;
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      align: 'center' as 'center' | 'left' | 'right',
      render: (status: TestCaseStatus) => {
        switch (status) {
          case TestCaseStatus.PASSED:
            return <Tag color="success" style={{ margin: 0 }}>已通过</Tag>;
          case TestCaseStatus.FAILED:
            return <Tag color="error" style={{ margin: 0 }}>未通过</Tag>;
          case TestCaseStatus.NOT_RUN:
          default:
            return <Tag color="warning" style={{ margin: 0 }}>未执行</Tag>;
        }
      },
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 110,
      align: 'center' as 'center' | 'left' | 'right',
      render: (time: string) => {
        const date = new Date(time);
        return date.toLocaleString();
      },
    },
    {
      title: 'Bug',
      dataIndex: 'bug_id',
      key: 'bug_id',
      width: 70,
      align: 'center' as 'center' | 'left' | 'right',
      render: (bugId: number | undefined) => {
        if (bugId) {
          return (
            <a
              href={`http://zt.luban.fit/index.php?m=bug&f=view&bugID=${bugId}`}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: 'var(--color-primary)', display: 'inline-block', width: '100%', textAlign: 'center' }}
            >
              {`${bugId}`}
            </a>
          );
        } else {
          return <span style={{ color: 'var(--color-text-disabled)', display: 'inline-block', width: '100%', textAlign: 'center' }}>无</span>;
        }
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 180,
      align: 'center' as 'center' | 'left' | 'right',
      render: (_: any, record: TestCase) => (
        <div style={{ textAlign: 'center' }}>
          <Space size={4}>
            <Tooltip title="查看">
              <Button
                size="small"
                type="text"
                icon={<EyeOutlined />}
                onClick={() => onView(record)}
                style={{ color: 'var(--color-text-secondary)' }}
              />
            </Tooltip>
            <Tooltip title="编辑">
              <Button
                size="small"
                type="text"
                icon={<EditOutlined />}
                onClick={() => onEdit(record)}
                style={{ color: 'var(--color-text-secondary)' }}
              />
            </Tooltip>
            <Tooltip title="执行">
              <Button
                size="small"
                type="text"
                icon={<CheckCircleOutlined />}
                onClick={() => onComplete(record)}
                style={{ color: 'var(--color-success)' }}
              />
            </Tooltip>
            {onApiExecute && record.api_endpoint_id && (
              <Tooltip title="API 执行">
                <Button
                  size="small"
                  type="text"
                  icon={<ApiOutlined />}
                  onClick={() => onApiExecute(record)}
                  style={{ color: 'var(--color-primary)' }}
                />
              </Tooltip>
            )}
            <Tooltip title="移动">
              <Button
                size="small"
                type="text"
                icon={<DragOutlined />}
                onClick={() => onMove(record)}
                style={{ color: 'var(--color-text-secondary)' }}
              />
            </Tooltip>
          </Space>
        </div>
      ),
    },
  ];
  const rowSelection: TableProps<TestCase>['rowSelection'] = {
    selectedRowKeys,
    onChange: (selectedRowKeys: React.Key[], selectedRows: TestCase[]) => {
      console.log(`selectedRowKeys: ${selectedRowKeys}`, 'selectedRows: ', selectedRows);
      setSelectedRowKeys(selectedRowKeys);
    },
    // getCheckboxProps: (record: TestCase) => ({
    //   disabled: record.status !== TestCaseStatus.NOT_RUN,
    // }),
  };

  const handleBatchDelete = () => {
    if (selectedRowKeys.length === 0) {
      message.warning('请选择要删除的测试用例');
      return;
    }
    setIsBatchDeleteModalVisible(true);
  };

  const handleConfirmBatchDelete = () => {
    const selectedIds = selectedRowKeys.map(key => Number(key));
    onBatchDelete(selectedIds);
    setSelectedRowKeys([]);
    setIsBatchDeleteModalVisible(false);
  };

  const handleCancelBatchDelete = () => {
    setIsBatchDeleteModalVisible(false);
  };

  return (
    <>
     {selectedRowKeys.length > 0 && <Space style={{ marginBottom: 16, textAlign: 'left' }}>
        <Button 
          danger 
          onClick={handleBatchDelete}
          // disabled={selectedRowKeys.length === 0}
        >
          批量删除 ({selectedRowKeys.length})
        </Button>
        <Button 
          onClick={() => {
            const selectedIds = selectedRowKeys.map(key => Number(key));
            onBatchMove(selectedIds);
          }}
        >
          批量移动 ({selectedRowKeys.length})
        </Button>
      </Space>}
      <Table
        rowSelection={{ type: "checkbox", ...rowSelection }}
        columns={testcaseColumns}
        dataSource={testcases}
        rowKey="id"
        pagination={{
          position: ['bottomRight'],
          onShowSizeChange,
          defaultPageSize: 20,
        }}
        scroll={{ x: 800, y: '47vh' }}
        // style={{ marginTop: 16, height: '100%' }}
        tableLayout="fixed"
        size="middle"
        bordered
        locale={{
          emptyText: '暂无数据'
        }}
      />

      <Modal
        title="批量删除确认"
        open={isBatchDeleteModalVisible}
        onOk={handleConfirmBatchDelete}
        onCancel={handleCancelBatchDelete}
        okText="确认删除"
        cancelText="取消"
        okType="danger"
      >
        <p>您确定要删除选中的 {selectedRowKeys.length} 个测试用例吗？</p>
        <p style={{ color: 'var(--color-danger)' }}>此操作不可恢复，请谨慎操作！</p>
      </Modal>
    </>
  );
};

export default TestCaseTable;
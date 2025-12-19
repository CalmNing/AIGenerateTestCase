import React from 'react';
import { Table, Space, Button } from 'antd';
import { EyeOutlined, EditOutlined, CheckOutlined, DeleteOutlined } from '@ant-design/icons';
import { TestCase } from '../types';

interface TestCaseTableProps {
  testcases: TestCase[];
  onView: (testcase: TestCase) => void;
  onEdit: (testcase: TestCase) => void;
  onComplete: (testcase: TestCase) => void;
  onDelete: (id: number) => void;
}

const TestCaseTable: React.FC<TestCaseTableProps> = ({
  testcases,
  onView,
  onEdit,
  onComplete,
  onDelete
}) => {
  const testcaseColumns = [
    {
      title: '用例名称',
      dataIndex: 'case_name',
      key: 'case_name',
      ellipsis: true,
      width: 200,
    },
    {
      title: '用例级别',
      dataIndex: 'case_level',
      key: 'case_level',
      width: 100,
      render: (level: number) => {
        const levelMap: Record<number, string> = {
          1: 'P0',
          2: 'P1',
          3: 'P2',
          4: 'P3'
        };
        return levelMap[level] ?? `P${level}`;
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => {
        return status === 'completed' ? '已执行' : '待执行';
      },
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 150,
      render: (time: string) => {
        const date = new Date(time);
        return date.toLocaleString();
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 250,
      render: (_: any, record: TestCase) => (
        <Space size="middle">
          <Button type="text" icon={<EyeOutlined />} onClick={() => onView(record)}>查看</Button>
          <Button type="text" icon={<EditOutlined />} onClick={() => onEdit(record)}>编辑</Button>
          {record.status !== 'completed' && (
            <Button type="primary" icon={<CheckOutlined />} onClick={() => onComplete(record)}>执行</Button>
          )}
          <Button type="text" danger icon={<DeleteOutlined />} onClick={() => onDelete(record.id)}>删除</Button>
        </Space>
      ),
    },
  ];

  return (
    <Table
      columns={testcaseColumns}
      dataSource={testcases}
      rowKey="id"
      pagination={{
        pageSize: 20,
        position: ['bottomRight']
      }}
      scroll={{ x: 800, y: 400 }}
      style={{ marginTop: 16 }}
    />
  );
};

export default TestCaseTable;
import React from 'react';
import { Table, Space, Button } from 'antd';
import { EyeOutlined, EditOutlined, CheckOutlined, DeleteOutlined } from '@ant-design/icons';
import { TestCase, TestCaseStatus } from '../types';

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
      width: 300,
      // align: 'center',
    },
    {
      title: '用例级别',
      dataIndex: 'case_level',
      key: 'case_level',
      width: 100,
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
      width: 100,
      align: 'center' as 'center' | 'left' | 'right',
      render: (status: TestCaseStatus) => {
        let statusText = '';
        let statusColor = '';
        
        switch(status) {
          case TestCaseStatus.PASSED:
            statusText = '已通过';
            statusColor = 'green';
            break;
          case TestCaseStatus.FAILED:
            statusText = '未通过';
            statusColor = 'red';
            break;
          case TestCaseStatus.NOT_RUN:
          default:
            statusText = '未执行';
            statusColor = 'orange';
            break;
        }
        
        return <span style={{ color: statusColor, display: 'inline-block', width: '100%', textAlign: 'center' }}>{statusText}</span>;
      },
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 150,
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
      width: 100,
      align: 'center' as 'center' | 'left' | 'right',
      render: (bugId: number | undefined) => {
        if (bugId) {
          return (
            <a 
              href={`http://zt.luban.fit/index.php?m=bug&f=view&bugID=${bugId}`} 
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: '#1890ff', display: 'inline-block', width: '100%', textAlign: 'center' }}
            >
              {`${bugId}`}
            </a>
          );
        } else {
          return <span style={{ color: '#bfbfbf', display: 'inline-block', width: '100%', textAlign: 'center' }}>无</span>;
        }
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 300,
      align: 'center' as 'center' | 'left' | 'right',
      render: (_: any, record: TestCase) => (
        <div style={{ textAlign: 'center' }}>
          <Space size="middle">
            <Button type="text" icon={<EyeOutlined />} onClick={() => onView(record)}>查看</Button>
            <Button type="text" icon={<EditOutlined />} onClick={() => onEdit(record)}>编辑</Button>
            <Button 
              type="primary" 
              icon={<CheckOutlined />} 
              onClick={() => onComplete(record)}
              disabled={record.status === TestCaseStatus.PASSED}
            >
              执行
            </Button>
            <Button type="text" danger icon={<DeleteOutlined />} onClick={() => onDelete(record.id)}>删除</Button>
          </Space>
        </div>
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
      style={{ marginTop: 16, height: '100%' }}
      tableLayout="fixed"
      size="small"
      bordered
      locale={{
        emptyText: '暂无数据'
      }}
    />
  );
};

export default TestCaseTable;
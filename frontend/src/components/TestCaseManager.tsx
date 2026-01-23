import React from 'react';
import { Card, Typography, Space, Button, Input, Select, notification } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import { Session, TestCase, TestCaseResponse, TestCaseStatus } from '../types';
import TestCaseTable from './TestCaseTable';
// 静态导入xlsx库
import * as XLSX from 'xlsx';

const { Text } = Typography;

interface TestCaseManagerProps {
  selectedSession: Session | null;
  testcases: TestCase[];
  testcasesResponse: TestCaseResponse;
  filters: { case_name: string; status: string };
  onFiltersChange: (filters: { case_name: string; status: string }) => void;
  onLoadTestcases: (sessionId: number, filters?: { case_name?: string; status?: string }) => void;
  onView: (testcase: TestCase) => void;
  onEdit: (testcase: TestCase) => void;
  onComplete: (testcase: TestCase) => void;
  onDelete: (id: number) => void;
}

const TestCaseManager: React.FC<TestCaseManagerProps> = ({
  selectedSession,
  testcases,
  testcasesResponse,
  filters,
  onFiltersChange,
  onLoadTestcases,
  onView,
  onEdit,
  onComplete,
  onDelete
}) => {
  const handleExportExcel = () => {
    if (testcases.length === 0) {
      notification.warning({
        message: '导出失败',
        description: '当前没有可导出的测试用例',
        placement: 'topRight'
      });
      return;
    }

    try {
      // 准备导出数据
      const exportData = testcases.map(tc => ({
        '用例名称': tc.case_name,
        '用例级别': `P${tc.case_level}`,
        '前置条件': tc.preset_conditions.join('\n'),
        '测试步骤': tc.steps.join('\n'),
        '预期结果': tc.expected_results.join('\n'),
        '状态': tc.status === TestCaseStatus.PASSED ? '已通过' : 
               tc.status === TestCaseStatus.FAILED ? '未通过' : '未执行',
        'bug': tc.bug_id ? `http://zt.luban.fit/index.php?m=bug&f=view&bugID=${tc.bug_id}` : '',
        // '创建时间': new Date(tc.created_at).toLocaleString(),

      }));

      // 创建工作表
      const ws = XLSX.utils.json_to_sheet(exportData);

      // 创建工作簿
      const wb = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(wb, ws, '测试用例');

      // 生成文件名
      const fileName = `测试用例_${new Date().toISOString().slice(0, 10)}.xlsx`;

      // 导出文件
      XLSX.writeFile(wb, fileName);

      // 显示成功通知
      notification.success({
        message: '导出成功',
        description: `已导出 ${testcases.length} 条测试用例`,
        placement: 'topRight'
      });
    } catch (error) {
      console.error('导出Excel失败:', error);
      notification.error({
        message: '导出失败',
        description: '导出Excel时发生错误，请重试',
        placement: 'topRight'
      });
    }
  };

  return (
    <Card variant="borderless">
      {!selectedSession ? (
        <div style={{ textAlign: 'center', padding: '40px 0' }}>
          <Text type="secondary">请先从左侧选择一个会话</Text>
        </div>
      ) 
      // : testcases.length === 0 ? (
      //   <div style={{ textAlign: 'center', padding: '40px 0' }}>
      //     <Text type="secondary">当前会话中没有测试用例，请先生成测试用例</Text>
      //   </div>
      // ) 
      : (
        <div style={{ display: 'flex', flexDirection: 'column', minHeight: '400px' }}>
          {/* 固定顶部区域：统计信息和操作按钮 */}
          <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: 12, background: '#f5f5f5', borderRadius: 4 }}>
            <Space size="large">
              <div>
                <Text strong>总用例数: </Text>
                <Text style={{ color: '#1890ff' , fontWeight: 'bold' }}>{testcasesResponse.totalNumber}</Text>
              </div>
              <div>
                <Text strong>已通过: </Text>
                <Text type="success" style={{ fontWeight: 'bold' }}>
                  {testcasesResponse.passed}
                </Text>
              </div>
              <div>
                <Text strong>未执行: </Text>
                <Text type="warning" style={{ fontWeight: 'bold' }}>
                  {testcasesResponse.not_run}
                </Text>
              </div>
              <div>
                <Text strong>未通过: </Text>
                <Text type="warning" style={{ fontWeight: 'bold' }}>
                  {testcasesResponse.failed}
                </Text>
              </div>
            </Space>

            <Space size="middle">
              <Button type="primary" icon={<DownloadOutlined />} onClick={handleExportExcel}>
                导出Excel
              </Button>
            </Space>
          </div>

          {/* 筛选区域 */}
          <div style={{ marginBottom: 16, padding: 12, background: '#f5f5f5', borderRadius: 4 }}>
            <Space size="middle">
              <Input
                placeholder="用例名称"
                value={filters.case_name}
                onChange={(e) => {
                  const newFilters = { ...filters, case_name: e.target.value };
                  onFiltersChange(newFilters);
                }}
                onPressEnter={() => {
                  if (selectedSession) {
                    onLoadTestcases(selectedSession.id, filters);
                  }
                }}
                style={{ width: 200 }}
              />
              <Select
                placeholder="用例状态"
                value={filters.status}
                onChange={(value) => {
                  const newFilters = { ...filters, status: value };
                  onFiltersChange(newFilters);
                }}
                style={{ width: 120 }}
              >
                <Select.Option value="">全部</Select.Option>
                <Select.Option value={TestCaseStatus.NOT_RUN}>未执行</Select.Option>
                <Select.Option value={TestCaseStatus.PASSED}>已通过</Select.Option>
                <Select.Option value={TestCaseStatus.FAILED}>未通过</Select.Option>
              </Select>
              <Button type="primary" onClick={() => {
                if (selectedSession) {
                  onLoadTestcases(selectedSession.id, filters);
                  // notification.info({
                  //   message: '筛选已应用',
                  //   description: '测试用例列表已根据筛选条件更新',
                  //   placement: 'topRight'
                  // });
                }
              }}>
                筛选
              </Button>
              <Button onClick={() => {
                const newFilters = {
                  case_name: '',
                  status: ''
                };
                onFiltersChange(newFilters);
                if (selectedSession) {
                  onLoadTestcases(selectedSession.id, newFilters);
                }
                // notification.info({
                //   message: '筛选已重置',
                //   description: '所有筛选条件已重置',
                //   placement: 'topRight'
                // });
              }}>
                重置
              </Button>
            </Space>
          </div>

          {/* 表格容器 */}
          <div style={{ flex: 1, overflowY: 'auto', overflowX: 'auto' }}>

              <TestCaseTable
                testcases={testcases}
                onView={onView}
                onEdit={onEdit}
                onComplete={onComplete}
                onDelete={onDelete}
              />

          </div>
        </div>
      )}
    </Card>
  );
};

export default TestCaseManager;
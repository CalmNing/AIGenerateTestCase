import React from 'react';
import { Modal, Button } from 'antd';
import { CheckOutlined } from '@ant-design/icons';
import { TestCase } from '../../types';

interface ViewTestcaseModalProps {
  visible: boolean;
  selectedTestcase: TestCase | null;
  onCancel: () => void;
  onComplete: (testcase: TestCase) => void;
}

const ViewTestcaseModal: React.FC<ViewTestcaseModalProps> = ({
  visible,
  selectedTestcase,
  onCancel,
  onComplete
}) => {
  return (
    <Modal
      title="查看测试用例"
      open={visible}
      onCancel={onCancel}
      footer={[
        selectedTestcase && selectedTestcase.status !== 'completed' && (
          <Button type="primary" icon={<CheckOutlined />} onClick={() => {
            onCancel();
            onComplete(selectedTestcase);
          }}>
            执行
          </Button>
        ),
        <Button key="close" onClick={onCancel}>
          关闭
        </Button>
      ]}
      width={800}
    >
      {selectedTestcase && (
        <div>
          <div style={{ marginBottom: 16 }}>
            <h4>用例名称: {selectedTestcase.case_name}</h4>
          </div>
          <div style={{ marginBottom: 16 }}>
            <h4>用例级别: P{selectedTestcase.case_level}</h4>
          </div>
          <div style={{ marginBottom: 16 }}>
            <h4>当前状态: {selectedTestcase.status === 'completed' ? '已执行' : '待执行'}</h4>
          </div>
          <div style={{ marginBottom: 16 }}>
            <h4>前置条件:</h4>
            <ul style={{ marginLeft: 20 }}>
              {selectedTestcase.preset_conditions.map((item, index) => (
                <li key={index}>{index + 1}. {item}</li>
              ))}
            </ul>
          </div>
          <div style={{ marginBottom: 16 }}>
            <h4>测试步骤:</h4>
            <ul style={{ marginLeft: 20 }}>
              {selectedTestcase.steps.map((item, index) => (
                <li key={index}>{index + 1}. {item}</li>
              ))}
            </ul>
          </div>
          <div>
            <h4>预期结果:</h4>
            <ul style={{ marginLeft: 20 }}>
              {selectedTestcase.expected_results.map((item, index) => (
                <li key={index}>{index + 1}. {item}</li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </Modal>
  );
};

export default ViewTestcaseModal;
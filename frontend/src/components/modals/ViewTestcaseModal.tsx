import React from 'react';
import { Modal, Button } from 'antd';
import { CheckOutlined,UpOutlined,DownOutlined } from '@ant-design/icons';
import { TestCase, TestCaseStatus } from '../../types';

interface ViewTestcaseModalProps {
  visible: boolean;
  nextButtonDisabled: boolean;
  prevButtonDisabled: boolean;
  selectedTestcase: TestCase | null;
  onCancel: () => void;
  onNext: () => void;
  onPrev: () => void;
  onComplete: (testcase: TestCase | null) => void;
}

const ViewTestcaseModal: React.FC<ViewTestcaseModalProps> = ({
  visible,
  selectedTestcase,
  onCancel,
  onNext,
  nextButtonDisabled,
  prevButtonDisabled,
  onPrev,
  onComplete
}) => {
  // console.log(nextButtonDisabled)
  return (
    <Modal
      title="查看测试用例"
      open={visible}
      onCancel={onCancel}
      footer={[

        <Button key={`prev-${selectedTestcase?.id}`} icon={<UpOutlined />} onClick={onPrev} disabled={prevButtonDisabled}>
          上一个
        </Button>,
        <Button key={`next-${selectedTestcase?.id}`} icon={<DownOutlined />} onClick={onNext} disabled={nextButtonDisabled}>
          下一个
        </Button>,
     
          <Button 
          key={`execute-${selectedTestcase?.id}`} 
          type="primary" 
          icon={<CheckOutlined />} 
          // disabled={selectedTestcase?.status === TestCaseStatus.PASSED}
          onClick={() => {
            // onCancel();
            onComplete(selectedTestcase);
          }}>
            执行
          </Button>,
        <Button key={`close-${selectedTestcase?.id}`} onClick={onCancel}>
          关闭
        </Button>
      ]}
      width={800}
      bodyStyle={{
      height: '600px', // 内容区域固定高度
      maxHeight: '80vh', // 适配小屏幕
      overflowY: 'auto', // 纵向滚动
      padding: '6px',
      boxSizing: 'border-box',
    }}

    >
      {selectedTestcase && (
        <div>
          <div style={{ marginBottom: 12 }}>
            <h2>用例名称: {selectedTestcase.case_name}</h2>
          </div>
          <div style={{ marginBottom: 12 }}>
            <h3>用例级别: P{selectedTestcase.case_level}</h3>
          </div>
          <div style={{ marginBottom: 12 }}>
            <h3>当前状态:
              <span style={{ 
                color: selectedTestcase.status === TestCaseStatus.PASSED ? 'green' : 
                      selectedTestcase.status === TestCaseStatus.FAILED ? 'red' : 'orange',
                fontWeight: 'bold'
              }}>
                {selectedTestcase.status === TestCaseStatus.PASSED ? ' 已通过' : 
                 selectedTestcase.status === TestCaseStatus.FAILED ? ' 未通过' : ' 未执行'}
              </span>
            </h3>
          </div>
           <div style={{ marginBottom: 12 }}>
            <h3>
              	Bug: {selectedTestcase.bug_id ? (
                <a 
                  href={`http://zt.luban.fit/index.php?m=bug&f=view&bugID=${selectedTestcase.bug_id}`} 
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ color: '#1890ff' }}
                >
                  {selectedTestcase.bug_id}
                </a>
              ) : (
                '无'
              )}
            </h3>
          </div>
          <div style={{ marginBottom: 12 }}>
            <h3>前置条件:</h3>
            <ul style={{ marginLeft: 20, fontSize: '16px' }}>
              {selectedTestcase.preset_conditions.map((item, index) => (
                <li key={index}>{index + 1}. {item} </li>
              ))}
            </ul>
          </div>
          <div style={{ marginBottom: 12 , fontSize: '16px' }}>
            <h3>测试步骤:</h3>
            <ul style={{ marginLeft: 20 }}>
              {selectedTestcase.steps.map((item, index) => (
                <li key={index}>{index + 1}. {item}</li>
              ))}
            </ul>
          </div>
          <div>
            <h3>预期结果:</h3>
            <ul style={{ marginLeft: 20 , fontSize: '16px' }}>
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
import React from 'react';
import { Modal } from 'antd';
import { CheckOutlined, UpOutlined, DownOutlined, BugOutlined } from '@ant-design/icons';
import { TestCase, TestCaseStatus } from '../../types';
import './ViewTestcaseModal.css';

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

const statusMap: Record<string, { label: string; cls: string }> = {
  [TestCaseStatus.PASSED]: { label: '已通过', cls: 'vtm-status--passed' },
  [TestCaseStatus.FAILED]: { label: '未通过', cls: 'vtm-status--failed' },
  [TestCaseStatus.NOT_RUN]: { label: '未执行', cls: 'vtm-status--not_run' },
};

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
  const tc = selectedTestcase;
  const status = tc ? (statusMap[tc.status || TestCaseStatus.NOT_RUN] || statusMap[TestCaseStatus.NOT_RUN]) : null;

  const renderStepList = (items: (string | object)[], type: 'step' | 'expected') => (
    <ul className="vtm-step-list">
      {items.map((item, index) => {
        const isObject = typeof item !== 'string';
        const text = isObject ? JSON.stringify(item, null, 2) : item;
        return (
          <li key={index} className="vtm-step-item">
            <span className="vtm-step-num">{index + 1}</span>
            {isObject ? (
              <pre className="vtm-step-code">{text}</pre>
            ) : (
              <span className="vtm-step-text">{text}</span>
            )}
          </li>
        );
      })}
    </ul>
  );

  return (
    <Modal
      title="查看测试用例"
      open={visible}
      onCancel={onCancel}
      width={720}
      styles={{
        body: { padding: '20px 24px' },
      }}
      footer={[
        <button key="prev" className="vtm-footer-btn" onClick={onPrev} disabled={prevButtonDisabled}>
          <UpOutlined style={{ fontSize: 11 }} /> 上一个
        </button>,
        <button key="next" className="vtm-footer-btn" onClick={onNext} disabled={nextButtonDisabled}>
          <DownOutlined style={{ fontSize: 11 }} /> 下一个
        </button>,
        <button key="execute" className="vtm-footer-btn vtm-footer-btn--primary" onClick={() => onComplete(tc)}>
          <CheckOutlined style={{ fontSize: 12 }} /> 执行
        </button>,
        <button key="close" className="vtm-footer-btn" onClick={onCancel}>
          关闭
        </button>,
      ]}
    >
      {tc && (
        <div>
          {/* Header */}
          <div className="vtm-header">
            <div className="vtm-header-info">
              <h2 className="vtm-title">{tc.case_name}</h2>
              <div className="vtm-meta">
                <span className={`vtm-level vtm-level--${tc.case_level || 4}`}>
                  P{tc.case_level || 4}
                </span>
                {status && (
                  <span className={`vtm-status ${status.cls}`}>
                    <span className="vtm-status-dot" />
                    {status.label}
                  </span>
                )}
                {tc.bug_id ? (
                  <a
                    href={`http://zt.luban.fit/index.php?m=bug&f=view&bugID=${tc.bug_id}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="vtm-bug-link"
                  >
                    <BugOutlined style={{ fontSize: 11 }} />
                    Bug #{tc.bug_id}
                  </a>
                ) : (
                  <span className="vtm-bug-none">无 Bug</span>
                )}
              </div>
            </div>
          </div>

          {/* Preset Conditions */}
          {tc.preset_conditions.length > 0 && tc.preset_conditions.some(s => s.trim()) && (
            <div className="vtm-section">
              <div className="vtm-section-label">前置条件</div>
              <ul className="vtm-step-list">
                {tc.preset_conditions.filter(s => s.trim()).map((item, index) => (
                  <li key={index} className="vtm-step-item">
                    <span className="vtm-step-num">{index + 1}</span>
                    <span className="vtm-step-text">{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Steps */}
          {tc.steps.length > 0 && (
            <div className="vtm-section">
              <div className="vtm-section-label">测试步骤</div>
              {renderStepList(tc.steps, 'step')}
            </div>
          )}

          {/* Expected Results */}
          {tc.expected_results.length > 0 && (
            <div className="vtm-section">
              <div className="vtm-section-label">预期结果</div>
              {renderStepList(tc.expected_results, 'expected')}
            </div>
          )}
        </div>
      )}
    </Modal>
  );
};

export default ViewTestcaseModal;

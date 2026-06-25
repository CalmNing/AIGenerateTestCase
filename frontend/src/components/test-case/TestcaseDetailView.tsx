import React from 'react';
import { BugOutlined } from '@ant-design/icons';
import { TestCase, TestCaseStatus } from '../../types';
import { formatStep } from '../../utils/stepUtils';

interface TestcaseDetailViewProps {
  testcase: TestCase;
  bugLinkTemplate?: string;
}

const statusMap: Record<string, { label: string; cls: string }> = {
  [TestCaseStatus.PASSED]: { label: '已通过', cls: 'vtm-status--passed' },
  [TestCaseStatus.FAILED]: { label: '未通过', cls: 'vtm-status--failed' },
  [TestCaseStatus.NOT_RUN]: { label: '未执行', cls: 'vtm-status--not_run' },
};

const TestcaseDetailView: React.FC<TestcaseDetailViewProps> = ({ testcase, bugLinkTemplate }) => {
  const tc = testcase;
  const status = tc ? (statusMap[tc.status || TestCaseStatus.NOT_RUN] || statusMap[TestCaseStatus.NOT_RUN]) : null;

  const renderStepList = (items: (string | object)[], type: 'step' | 'expected') => (
    <ul className="vtm-step-list">
      {items.map((item, index) => {
        const isObject = typeof item !== 'string';
        const text = isObject ? formatStep(item as Record<string, unknown>) : item;
        return (
          <li key={index} className="vtm-step-item">
            <span className="vtm-step-num">{index + 1}</span>
            {isObject && text.includes('\n') ? (
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
                href={bugLinkTemplate
                  ? bugLinkTemplate.replace('{bug_id}', String(tc.bug_id))
                  : `http://zt.luban.fit/index.php?m=bug&f=view&bugID=${tc.bug_id}`
                }
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
      {tc.preset_conditions.length > 0 && (
        <div className="vtm-section">
          <div className="vtm-section-label">前置条件</div>
          {renderStepList(
            tc.preset_conditions.filter((s: any) => typeof s === 'string' ? s.trim() : true) as (string | object)[],
            'step'
          )}
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
  );
};

export default TestcaseDetailView;

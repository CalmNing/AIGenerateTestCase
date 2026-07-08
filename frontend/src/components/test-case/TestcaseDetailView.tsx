import React, { useMemo, useState } from 'react';
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

const normalizeDisplayItem = (item: string | Record<string, unknown>): string => {
  if (typeof item === 'string') {
    return item;
  }
  return formatStep(item);
};

const TestcaseDetailView: React.FC<TestcaseDetailViewProps> = ({ testcase, bugLinkTemplate }) => {
  const tc = testcase;
  const status = statusMap[tc.status || TestCaseStatus.NOT_RUN] || statusMap[TestCaseStatus.NOT_RUN];

  const presetConditions = (tc.preset_conditions || []).filter((item) => {
    if (typeof item === 'string') {
      return item.trim();
    }
    return true;
  });

  const normalizedSteps = (tc.steps || []).map(normalizeDisplayItem);
  const normalizedExpectedResults = (tc.expected_results || []).map((item) =>
    typeof item === 'string' ? item : formatStep(item),
  );

  const pairedRows = Array.from(
    { length: Math.max(normalizedSteps.length, normalizedExpectedResults.length) },
    (_, index) => ({
      stepNumber: index + 1,
      action: normalizedSteps[index] || '',
      expected: normalizedExpectedResults[index] || '',
    }),
  ).filter((row) => row.action || row.expected);

  const defaultSelectedStep = useMemo(() => (
    pairedRows.length > 0 ? pairedRows[0].stepNumber : null
  ), [pairedRows]);
  const [selectedStep, setSelectedStep] = useState<number | null>(defaultSelectedStep);

  React.useEffect(() => {
    setSelectedStep(defaultSelectedStep);
  }, [defaultSelectedStep, tc.id]);

  const renderTextBlock = (text: string, className: string) => {
    if (text.includes('\n')) {
      return <pre className={className}>{text}</pre>;
    }
    return <span className={className}>{text}</span>;
  };

  return (
    <div className="vtm-detail-container">
      <div className="vtm-header-fixed">
        <div className="vtm-header-main">
          <h2 className="vtm-title">{tc.case_name}</h2>
          <div className="vtm-meta">
            <span className={`vtm-level vtm-level--${tc.case_level || 4}`}>
              P{tc.case_level || 4}
            </span>
            <span className={`vtm-status ${status.cls}`}>
              <span className="vtm-status-dot" />
              {status.label}
            </span>
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

      <div className="vtm-content-scrollable">
        {presetConditions.length > 0 && (
          <section className="vtm-section">
            <div className="vtm-section-label">前置条件</div>
            <ul className="vtm-step-list">
              {presetConditions.map((item, index) => {
                const text = normalizeDisplayItem(item);
                return (
                  <li key={`preset-${index}`} className="vtm-step-item">
                    <span className="vtm-step-num">{index + 1}</span>
                    {renderTextBlock(text, text.includes('\n') ? 'vtm-step-code' : 'vtm-step-text')}
                  </li>
                );
              })}
            </ul>
          </section>
        )}

        {pairedRows.length > 0 && (
          <section className="vtm-section">
            <div className="vtm-section-label">测试步骤与预期结果</div>
            <div className="vtm-paired-board" role="table" aria-label="测试步骤与预期结果">
              <div className="vtm-paired-board__header" role="row">
                <div className="vtm-paired-board__index">步骤</div>
                <div className="vtm-paired-board__heading">测试步骤</div>
                <div className="vtm-paired-board__heading">预期结果</div>
              </div>

              <div className="vtm-paired-board__body">
                {pairedRows.map((row) => (
                  <div
                    key={`paired-${row.stepNumber}`}
                    className={`vtm-paired-row ${selectedStep === row.stepNumber ? 'is-active' : ''}`}
                    role="row"
                    tabIndex={0}
                    onClick={() => setSelectedStep(row.stepNumber)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        setSelectedStep(row.stepNumber);
                      }
                    }}
                  >
                    <div className="vtm-paired-row__index">{row.stepNumber}</div>
                    <div className="vtm-paired-cell" role="cell">
                      <div className="vtm-paired-cell__label">测试步骤</div>
                      {row.action
                        ? renderTextBlock(
                            row.action,
                            row.action.includes('\n') ? 'vtm-paired-cell__code' : 'vtm-paired-cell__text',
                          )
                        : <span className="vtm-paired-cell__placeholder">未填写测试步骤</span>}
                    </div>
                    <div className="vtm-paired-cell vtm-paired-cell--expected" role="cell">
                      <div className="vtm-paired-cell__label">预期结果</div>
                      {row.expected
                        ? renderTextBlock(
                            row.expected,
                            row.expected.includes('\n') ? 'vtm-paired-cell__code' : 'vtm-paired-cell__text',
                          )
                        : <span className="vtm-paired-cell__placeholder">未填写预期结果</span>}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </section>
        )}
      </div>
    </div>
  );
};

export default TestcaseDetailView;

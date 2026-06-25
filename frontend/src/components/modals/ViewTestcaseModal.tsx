import React, { useState, useEffect } from 'react';
import { Modal, Tabs } from 'antd';
import { CheckCircleOutlined, UpOutlined, DownOutlined } from '@ant-design/icons';
import { TestCase } from '../../types';
import { configApi } from '../../services/api';
import TestcaseDetailView from '../TestcaseDetailView';
import TestcaseScenarioView from '../TestcaseScenarioView';
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
  const [activeTab, setActiveTab] = useState('detail');
  const [bugLinkTemplate, setBugLinkTemplate] = useState('');

  useEffect(() => {
    if (visible) {
      configApi.getBugLinkTemplate().then(res => {
        if (res.code === 200 && res.data) {
          setBugLinkTemplate(res.data.template || '');
        }
      }).catch(() => {}); // silent fail
    }
  }, [visible]);

  return (
    <Modal
      title="查看测试用例"
      open={visible}
      onCancel={onCancel}
      width={800}
      styles={{
        body: { padding: '12px 24px' },
      }}
      footer={[
        <button key="prev" className="vtm-footer-btn" onClick={onPrev} disabled={prevButtonDisabled}>
          <UpOutlined style={{ fontSize: 11 }} /> 上一个
        </button>,
        <button key="next" className="vtm-footer-btn" onClick={onNext} disabled={nextButtonDisabled}>
          <DownOutlined style={{ fontSize: 11 }} /> 下一个
        </button>,
        <button key="execute" className="vtm-footer-btn vtm-footer-btn--primary" onClick={() => onComplete(tc)}>
          <CheckCircleOutlined style={{ fontSize: 12 }} /> 标记完成
        </button>,
        <button key="close" className="vtm-footer-btn" onClick={onCancel}>
          关闭
        </button>,
      ]}
    >
      {tc && (
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={[
            {
              key: 'detail',
              label: '用例详情',
              children: <TestcaseDetailView testcase={tc} bugLinkTemplate={bugLinkTemplate} />,
            },
            {
              key: 'scenario',
              label: '接口编排',
              children: <TestcaseScenarioView scenarioId={tc.scenario_id ?? undefined} />,
            },
          ]}
        />
      )}
    </Modal>
  );
};

export default ViewTestcaseModal;

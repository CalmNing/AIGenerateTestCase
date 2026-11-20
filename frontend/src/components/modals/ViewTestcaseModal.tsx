import React, { useEffect, useState } from 'react';
import { Button, Drawer, Input, Popconfirm, Popover, Space, Tabs } from 'antd';
import { CheckCircleOutlined, CloseCircleOutlined, DeleteOutlined, DownOutlined, EditOutlined, UpOutlined } from '@ant-design/icons';
import { TestCase } from '../../types';
import { configApi } from '../../services/api';
import TestcaseDetailView from '../test-case/TestcaseDetailView';
import './ViewTestcaseModal.css';

interface ViewTestcaseModalProps {
  visible: boolean;
  sessionId?: number;
  nextButtonDisabled: boolean;
  prevButtonDisabled: boolean;
  selectedTestcase: TestCase | null;
  onCancel: () => void;
  onNext: () => void;
  onPrev: () => void;
  onEdit: (testcase: TestCase | null) => void;
  onDelete: (testcase: TestCase | null) => Promise<void>;
  onMarkPassed: (testcase: TestCase | null) => Promise<void>;
  onMarkFailed: (testcase: TestCase | null, bugId: number) => Promise<void>;
}

const ViewTestcaseModal: React.FC<ViewTestcaseModalProps> = ({
  visible,
  selectedTestcase,
  onCancel,
  onNext,
  nextButtonDisabled,
  prevButtonDisabled,
  onPrev,
  onEdit,
  onDelete,
  onMarkPassed,
  onMarkFailed,
}) => {
  const tc = selectedTestcase;
  const [activeTab, setActiveTab] = useState('detail');
  const [bugLinkTemplate, setBugLinkTemplate] = useState('');
  const [failPopoverOpen, setFailPopoverOpen] = useState(false);
  const [bugIdInput, setBugIdInput] = useState('');
  const [actionLoading, setActionLoading] = useState<'passed' | 'failed' | 'deleted' | null>(null);

  useEffect(() => {
    if (!visible) return;

    setActiveTab('detail');
    setFailPopoverOpen(false);
    setBugIdInput('');
    configApi.getConfig().then((res) => {
      if (res.code === 200 && res.data) {
        setBugLinkTemplate(res.data.bug_link_template || '');
      }
    }).catch(() => {});
  }, [visible, selectedTestcase?.id]);

  const parsedBugId = Number(bugIdInput);
  const isBugIdValid = Number.isInteger(parsedBugId) && parsedBugId > 0;

  const handleMarkPassed = async () => {
    setActionLoading('passed');
    try {
      await onMarkPassed(tc);
      if (!nextButtonDisabled) {
        onNext();
      }
    } finally {
      setActionLoading(null);
    }
  };

  const handleMarkFailed = async () => {
    if (!isBugIdValid) return;
    setActionLoading('failed');
    try {
      await onMarkFailed(tc, parsedBugId);
      setFailPopoverOpen(false);
      setBugIdInput('');
      if (!nextButtonDisabled) {
        onNext();
      }
    } finally {
      setActionLoading(null);
    }
  };

  const handleDelete = async () => {
    setActionLoading('deleted');
    try {
      await onDelete(tc);
    } finally {
      setActionLoading(null);
    }
  };

  const failPopoverContent = (
    <div className="vtm-fail-popover">
      <div className="vtm-fail-popover__label">填写 Bug ID 后标记为不通过</div>
      <Input
        autoFocus
        value={bugIdInput}
        onChange={(event) => setBugIdInput(event.target.value.replace(/[^\d]/g, ''))}
        placeholder="输入 Bug ID"
        status={bugIdInput !== '' && !isBugIdValid ? 'error' : ''}
        onPressEnter={() => {
          if (isBugIdValid && actionLoading !== 'failed') {
            void handleMarkFailed();
          }
        }}
      />
      <div className="vtm-fail-popover__actions">
        <Button size="small" onClick={() => setFailPopoverOpen(false)}>
          取消
        </Button>
        <Button
          size="small"
          danger
          type="primary"
          loading={actionLoading === 'failed'}
          disabled={!isBugIdValid}
          onClick={() => void handleMarkFailed()}
        >
          确认不通过
        </Button>
      </div>
    </div>
  );

  return (
    <Drawer
      title="测试用例详情"
      open={visible}
      onClose={onCancel}
      placement="right"
      width="66vw"
      destroyOnHidden={false}
      className="vtm-drawer"
      styles={{
        header: { padding: '16px 20px 12px' },
        body: { padding: '0 20px 20px' },
        footer: { padding: '12px 20px', borderTop: '1px solid var(--color-border-light)' },
      }}
      footer={(
        <div className="vtm-footer vtm-footer--left">
          <Space size={8} wrap className="vtm-footer__group">
            <Button
              className="vtm-footer-btn vtm-footer-btn--success"
              icon={<CheckCircleOutlined style={{ fontSize: 12 }} />}
              loading={actionLoading === 'passed'}
              onClick={() => void handleMarkPassed()}
            >
              通过
            </Button>

            <Popover
              trigger="click"
              placement="topLeft"
              content={failPopoverContent}
              open={failPopoverOpen}
              onOpenChange={(open) => {
                setFailPopoverOpen(open);
                if (!open) setBugIdInput('');
              }}
            >
              <Button className="vtm-footer-btn vtm-footer-btn--danger" icon={<CloseCircleOutlined style={{ fontSize: 12 }} />}>
                不通过
              </Button>
            </Popover>
          </Space>

          <Space size={8} wrap className="vtm-footer__group">
            <Button
              className="vtm-footer-btn vtm-footer-btn--subtle"
              icon={<EditOutlined style={{ fontSize: 12 }} />}
              onClick={() => onEdit(tc)}
            >
              编辑
            </Button>

            <Popconfirm
              title="确认删除这条测试用例？"
              description="删除后不可恢复。"
              okText="确认删除"
              cancelText="取消"
              okButtonProps={{ danger: true, loading: actionLoading === 'deleted' }}
              onConfirm={() => void handleDelete()}
            >
              <Button
                className="vtm-footer-btn vtm-footer-btn--subtle-danger"
                icon={<DeleteOutlined style={{ fontSize: 12 }} />}
                loading={actionLoading === 'deleted'}
              >
                删除
              </Button>
            </Popconfirm>
          </Space>

          <Space size={8} wrap className="vtm-footer__group">
            <Button className="vtm-footer-btn" onClick={onNext} disabled={nextButtonDisabled} icon={<DownOutlined style={{ fontSize: 11 }} />}>
              下一个
            </Button>

            <Button className="vtm-footer-btn" onClick={onPrev} disabled={prevButtonDisabled} icon={<UpOutlined style={{ fontSize: 11 }} />}>
              上一个
            </Button>
          </Space>
        </div>
      )}
    >
      {tc && (
        <Tabs
          className="vtm-tabs-container"
          activeKey={activeTab}
          onChange={setActiveTab}
          items={[
            {
              key: 'detail',
              label: '用例详情',
              children: <TestcaseDetailView testcase={tc} bugLinkTemplate={bugLinkTemplate} />,
            },
          ]}
        />
      )}
    </Drawer>
  );
};

export default ViewTestcaseModal;
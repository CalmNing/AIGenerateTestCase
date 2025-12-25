import React, { useState } from 'react';
import { Modal, Select, Input } from 'antd';
import { TestCaseStatus } from '../../types';

interface CompleteTestcaseModalProps {
  visible: boolean;
  onOk: (status: TestCaseStatus, bugId?: number) => void;
  onCancel: () => void;
}

const CompleteTestcaseModal: React.FC<CompleteTestcaseModalProps> = ({
  visible,
  onOk,
  onCancel
}) => {
  const [selectedStatus, setSelectedStatus] = useState<TestCaseStatus | undefined>(undefined);
  const [bugId, setBugId] = useState<number | undefined>(undefined);

  const handleOk = () => {
    if (selectedStatus !== undefined) {
      // 如果选择的状态是FAILED，需要验证bugId是否存在
      if (selectedStatus === TestCaseStatus.FAILED) {
        if (bugId === undefined || bugId <= 0) {
          // 如果未通过状态但没有输入有效的bugId，则不执行操作
          return;
        }
        onOk(selectedStatus, bugId);
      } else {
        // 其他状态直接执行
        onOk(selectedStatus);
      }
    }
  };

  // 当模态框关闭时重置状态
  const handleCancel = () => {
    setSelectedStatus(undefined);
    setBugId(undefined);
    onCancel();
  };

  return (
    <Modal
      title="执行"
      open={visible}
      onOk={handleOk}
      onCancel={handleCancel}
      okText="执行"
      cancelText="取消"
    >
      {/* <p>⚠️ 确认将该测试用例标记为已执行？</p> */}
      <div style={{ marginTop: 16 }}>
        <label>选择执行结果：</label>
        <Select
          style={{ width: '100%', marginTop: 8 }}
          value={selectedStatus}
          onChange={(value) => {
            setSelectedStatus(value);
            // 如果选择的不是FAILED，则清空bugId
            if (value !== TestCaseStatus.FAILED) {
              setBugId(undefined);
            }
          }}
          options={[
            { value: TestCaseStatus.PASSED, label: '通过' },
            { value: TestCaseStatus.FAILED, label: '未通过' },
            // { value: TestCaseStatus.NOT_RUN, label: '未运行' },
          ]}
        />

      {selectedStatus === TestCaseStatus.FAILED && (
        <div style={{ marginTop: 16 }}>
          <label>Bug ID：</label>
          <Input
            style={{ width: '100%', marginTop: 8 }}
            type="number"
            value={bugId || ''}
            onChange={(e) => setBugId(e.target.value ? Number(e.target.value) : undefined)}
            placeholder="请输入Bug ID"
            status={bugId === undefined || bugId <= 0 ? 'error' : ''}
          />
        </div>)}
      </div>
    </Modal>
  );
};

export default CompleteTestcaseModal;
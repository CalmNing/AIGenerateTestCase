import React from 'react';
import { Modal } from 'antd';

interface CompleteTestcaseModalProps {
  visible: boolean;
  onOk: () => void;
  onCancel: () => void;
}

const CompleteTestcaseModal: React.FC<CompleteTestcaseModalProps> = ({
  visible,
  onOk,
  onCancel
}) => {
  return (
    <Modal
      title="确认执行"
      open={visible}
      onOk={onOk}
      onCancel={onCancel}
      okText="确认执行"
      cancelText="取消"
    >
      <p>⚠️ 确认将该测试用例标记为已执行？</p>
    </Modal>
  );
};

export default CompleteTestcaseModal;
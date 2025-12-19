import React from 'react';
import { Modal } from 'antd';

interface DeleteTestcaseModalProps {
  visible: boolean;
  onOk: () => void;
  onCancel: () => void;
}

const DeleteTestcaseModal: React.FC<DeleteTestcaseModalProps> = ({
  visible,
  onOk,
  onCancel
}) => {
  return (
    <Modal
      title="确认删除"
      open={visible}
      onOk={onOk}
      onCancel={onCancel}
      okText="确认删除"
      cancelText="取消"
      okType="danger"
    >
      <p>⚠️ 确认删除该测试用例？此操作无法恢复！</p>
    </Modal>
  );
};

export default DeleteTestcaseModal;
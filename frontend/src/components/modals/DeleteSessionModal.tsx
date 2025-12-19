import React from 'react';
import { Modal } from 'antd';

interface DeleteSessionModalProps {
  visible: boolean;
  onOk: () => void;
  onCancel: () => void;
}

const DeleteSessionModal: React.FC<DeleteSessionModalProps> = ({
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
      <p>⚠️ 确认删除该会话？此操作将删除该会话的所有测试用例，且无法恢复！</p>
    </Modal>
  );
};

export default DeleteSessionModal;
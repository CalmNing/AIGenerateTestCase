import React, { useEffect, useRef, useState } from 'react';
import { Card, Typography, Input, Button, message } from 'antd';
import { DeleteOutlined } from '@ant-design/icons';
import { Session } from '../types';

const { Text } = Typography;

interface TestCaseGeneratorProps {
  selectedSession: Session | null;
  requirement: string;
  loading: boolean;
  onRequirementChange: (value: string) => void;
  onGenerate: () => void;
  imageBase64?: string | null;
  onImageChange: (imageBase64: string | null) => void;
}

const TestCaseGenerator: React.FC<TestCaseGeneratorProps> = ({
  selectedSession,
  requirement,
  loading,
  onRequirementChange,
  onGenerate,
  imageBase64,
  onImageChange
}) => {
  // 图片状态管理
  const [previewImage, setPreviewImage] = useState<string | null>(null);
  const textAreaRef = useRef<HTMLTextAreaElement>(null);

  // 监听外部imageBase64变化，同步更新内部预览状态
  useEffect(() => {
    if (imageBase64 === null) {
      setPreviewImage(null);
    }
  }, [imageBase64]);


  // 将图片转换为base64
  const imageToBase64 = (file: File): Promise<string> => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        // 只保留base64数据部分，去掉前缀
        const result = reader.result as string;
        resolve(result.split(',')[1]);
      };
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
  };

  // 处理图片文件
  const processImageFile = async (file: File) => {
    console.log('开始处理图片文件');
    try {
      // 检查文件类型
      if (!file.type.startsWith('image/')) {
        message.error('请上传图片文件！');
        return;
      }

      // 检查文件大小（限制为5MB）
      const maxSize = 5 * 1024 * 1024;
      if (file.size > maxSize) {
        message.error('图片大小不能超过5MB！');
        return;
      }

      // 转换为base64
      const base64 = await imageToBase64(file);
      console.log('图片转换为base64成功，长度:', base64.length);

      // 更新父组件状态
      onImageChange(base64);
      console.log('调用onImageChange更新父组件状态');

      // 生成预览URL
      const previewUrl = URL.createObjectURL(file);
      setPreviewImage(previewUrl);
      console.log('生成预览URL成功');

      message.success('图片粘贴成功！');
    } catch (error) {
      console.error('图片处理失败:', error);
      message.error('图片处理失败！');
    }
  };

  // 处理剪贴板粘贴 - 兼容React的onPaste事件
  const handlePaste = (e: React.ClipboardEvent) => {
    console.log('检测到粘贴事件');
    // 提取剪贴板中的文件
    const items = e.clipboardData?.items || [];
    console.log('剪贴板中的项目数量:', items.length);
    for (const item of items) {
      console.log('剪贴板项目类型:', item.type);
      if (item.type.startsWith('image/')) {
        const file = item.getAsFile();
        if (file) {
          console.log('提取到图片文件:', file.name, file.size, file.type);
          // 阻止默认行为，避免粘贴到文本框中
          e.preventDefault();
          processImageFile(file);
          break;
        }
      }
    }
  };

  // 移除手动事件监听，使用组件自带的onPaste属性
  // 避免重复的事件处理和类型不匹配问题

  // 处理图片移除
  const handleImageRemove = () => {
    setPreviewImage(null);
    onImageChange(null);
    message.success('图片已移除！');
  };

  return (
    <Card title="生成测试用例" variant="borderless">
      {!selectedSession ? (
        <div style={{ textAlign: 'center', padding: '40px 0' }}>
          <Text type="secondary">请先从左侧选择或创建一个会话</Text>
        </div>
      ) : (
        <div>
          <div style={{ marginBottom: '16px' }}>
            <Text strong>当前会话:</Text> <Text>{selectedSession.name}</Text>
          </div>

          {/* 需求输入区域 */}
          <div style={{ marginBottom: '16px' }}>
            <Text strong>输入需求描述:</Text>

            {/* 图片显示区域 - 显示在文本框上方 */}
            {previewImage && (
              <div style={{
                marginBottom: '12px',
                padding: '12px',
                border: '1px solid #d9d9d9',
                borderRadius: '8px',
                backgroundColor: '#fafafa',
                display: 'flex',
                alignItems: 'center',
                gap: '12px'
              }}>
                <div style={{ flex: '0 0 auto', position: 'relative' }}>
                  <img
                    src={previewImage}
                    alt="Preview"
                    style={{
                      maxWidth: '200px',
                      maxHeight: '150px',
                      objectFit: 'contain',
                      borderRadius: '4px',
                      border: '1px solid #e8e8e8'
                    }}
                  />
                  <Button
                    icon={<DeleteOutlined />}
                    size="small"
                    type="text"
                    danger
                    onClick={handleImageRemove}
                    style={{
                      position: 'absolute',
                      top: '-8px',
                      right: '-8px',
                      background: '#fff',
                      borderRadius: '50%',
                      boxShadow: '0 2px 8px rgba(0, 0, 0, 0.15)',
                      width: '24px',
                      height: '24px',
                      padding: '0',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center'
                    }}
                  />
                </div>
                <div style={{ flex: '1', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  <div style={{ fontSize: '14px', fontWeight: 'bold', color: '#333' }}>已上传图片</div>
                  <div style={{ fontSize: '12px', color: '#666' }}>图片将与需求一起发送，用于生成测试用例</div>
                </div>
              </div>
            )}

            {/* 文本输入框 */}
            <div style={{ marginTop: '8px' }}>
              <Input.TextArea
                ref={textAreaRef}
                rows={6}
                placeholder="请输入测试用例设计需求，例如：设计一个登录功能的测试用例，包括正常登录、异常登录等场景... 提示：您可以直接粘贴图片到此处"
                value={requirement}
                onChange={(e) => onRequirementChange(e.target.value)}
                onPaste={handlePaste}
                style={{
                  border: '1px solid #d9d9d9',
                  borderRadius: '8px',
                  fontSize: '14px'
                }}
              />
              <div style={{
                marginTop: '8px',
                fontSize: '12px',
                color: '#666',
                display: 'flex',
                alignItems: 'center',
                gap: '8px'
              }}>
                <span>📋 支持直接粘贴图片</span>
                <span>💡 粘贴后图片将显示在此输入框上方</span>
              </div>
            </div>
          </div>

          {/* 生成按钮 */}
          <Button
            type="primary"
            size="large"
            onClick={onGenerate}
            loading={loading}
            disabled={!requirement.trim() && !imageBase64}
            block
            style={{ borderRadius: '8px', fontSize: '16px', height: '48px' }}
          >
            生成测试用例
          </Button>
        </div>
      )}
    </Card>
  );
};

export default TestCaseGenerator;
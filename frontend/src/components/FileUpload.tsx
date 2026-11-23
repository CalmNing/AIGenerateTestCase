import React, { useState, useCallback, useRef } from 'react';
import {
  message,
  Button,
  Spin,
} from 'antd';
import {
  InboxOutlined,
  DeleteOutlined,
  FileImageOutlined,
  FileOutlined,
} from '@ant-design/icons';

import SparkMD5 from 'spark-md5';
import { fileUploadApi } from '../services/api';

export interface UploadedFileResult {
  fileName: string;
  fileId: string;
  fileSize: number;
}

interface FileUploadProps {
  /** 上传完成后回调，返回文件信息 */
  onUploadSuccess?: (result: UploadedFileResult) => void;
  /** 最大上传文件大小(MB)，默认 50 */
  maxSize?: number;
  /** 接受的文件类型，默认全部 */
  accept?: string | string[];
  /** 是否多选，默认 false */
  multiple?: boolean;
  /** 已上传的文件列表（受控模式） */
  value?: Array<UploadedFileResult>;
  /** 文件列表变更回调 */
  onChange?: (files: Array<UploadedFileResult>) => void;
  /** 是否禁用 */
  disabled?: boolean;
  /** 执行环境 id（后端据此取 baseUrl / access-token 完成上传） */
  environmentId?: number | null;
}

/**
 * 计算文件的 MD5 值
 */
function calculateMd5(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunkSize = 2 * 1024 * 1024; // 2MB 分块
    const chunks = Math.ceil(file.size / chunkSize);
    let currentChunk = 0;
    const spark = typeof ArrayBuffer !== 'undefined' ? new SparkMD5.ArrayBuffer() : new SparkMD5();
    const reader = new FileReader();

    reader.onload = (e) => {
      spark.append(e.target?.result as ArrayBuffer);
      currentChunk++;
      if (currentChunk < chunks) {
        loadNext();
      } else {
        resolve(spark.end());
      }
    };

    reader.onerror = () => reject(new Error('文件读取失败'));

    function loadNext() {
      const start = currentChunk * chunkSize;
      const end = Math.min(start + chunkSize, file.size);
      reader.readAsArrayBuffer(file.slice(start, end));
    }

    loadNext();
  });
}

/**
 * 文件上传组件
 * - 文件交给后端代理上传：POST /api/file/upload
 * - 后端负责计算 MD5、申请预签名地址、PUT 到对象存储，返回 fileId
 * - 浏览器只与本平台后端通信，不直连对象存储（避免跨域）
 * - 支持拖拽上传、进度显示
 */
const FileUpload: React.FC<FileUploadProps> = ({
  onUploadSuccess,
  maxSize = 50,
  accept,
  multiple = false,
  value = [],
  onChange,
  disabled = false,
  environmentId,
}) => {
  const inputRef = useRef<HTMLInputElement>(null);

  const getFileIcon = useCallback((fileName: string) => {
    const ext = fileName.split('.').pop()?.toLowerCase() || '';
    if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'].includes(ext)) {
      return <FileImageOutlined style={{ color: '#1890ff', fontSize: 32 }} />;
    }
    return <FileOutlined style={{ color: '#8c8c8c', fontSize: 32 }} />;
  }, []);

  const formatFileSize = useCallback((bytes: number) => {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  }, []);

  const handleRemove = useCallback((index: number) => {
    if (!onChange) return;
    const newValue = [...value];
    newValue.splice(index, 1);
    onChange(newValue);
  }, [value, onChange]);

  /**
   * 核心上传流程：把文件交给后端代理上传
   *
   * 后端负责：计算 MD5 -> 申请预签名地址 -> PUT 到对象存储 -> 返回 fileId。
   * 前端不再直连预签名地址，因此不会触发跨域。
   */
  const doUpload = async (file: File): Promise<UploadedFileResult> => {
    const response = await fileUploadApi.upload(file, environmentId);

    if (response.code !== 200 || !response.data?.fileId) {
      throw new Error(response.message || '上传失败');
    }

    return {
      fileName: response.data.fileName || file.name,
      fileId: response.data.fileId,
      fileSize: response.data.fileSize ?? file.size,
    };
  };

  const [uploadingFileName, setUploadingFileName] = useState<string>('');

  const handleFilesSelected = async (files: File[]) => {
    for (const file of files) {
      // 大小校验
      if (file.size > maxSize * 1024 * 1024) {
        message.error(`${file.name} 超过 ${maxSize}MB 限制`);
        continue;
      }

      setUploadingFileName(file.name);

      try {
        const result = await doUpload(file);

        if (multiple && onChange) {
          onChange([...value, result]);
        } else if (!multiple && onChange) {
          onChange([result]);
        }

        onUploadSuccess?.(result);
        message.success(`${file.name} 上传成功`);
      } catch (error: any) {
        console.error('Upload failed:', error);
        // 后端 HTTPException 的描述在 detail 字段；网络异常时退回 message
        const detail = error?.response?.data?.detail || error?.response?.data?.message || error?.message;
        message.error(`${file.name} 上传失败: ${detail || '未知错误'}`);
      } finally {
        setUploadingFileName('');
      }
    }
  };

  // 原生 input change 处理
  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files && files.length > 0) {
      handleFilesSelected(Array.from(files));
    }
    // 清空 input 值，允许重复选择同一文件
    e.target.value = '';
  };

  // 点击上传区域触发文件选择
  const handleClick = () => {
    if (!disabled && !uploadingFileName) {
      inputRef.current?.click();
    }
  };

  // 拖拽处理
  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (disabled || uploadingFileName) return;

    const files = Array.from(e.dataTransfer.files);
    if (files.length > 0) {
      handleFilesSelected(files);
    }
  };

  const acceptStr = typeof accept === 'string' ? accept : accept?.join(',');

  // 上传区域
  const uploadAreaNode = (
    <div
      style={{
        border: `1px dashed ${uploadingFileName ? '#d9d9d9' : '#d9d9d9'}`,
        borderRadius: 6,
        padding: '16px',
        textAlign: 'center',
        cursor: disabled || uploadingFileName ? 'not-allowed' : 'pointer',
        backgroundColor: '#fafafa',
        opacity: disabled || uploadingFileName ? 0.6 : 1,
        transition: 'border-color 0.3s',
      }}
      onClick={handleClick}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {/* 隐藏的原生文件输入框 */}
      <input
        ref={inputRef}
        type="file"
        style={{ display: 'none' }}
        accept={acceptStr}
        multiple={multiple}
        onChange={handleInputChange}
      />

      {uploadingFileName ? (
        <div>
          <Spin />
          <p style={{ marginTop: 12, marginBottom: 4 }}>正在上传 {uploadingFileName}</p>
          <p className="ant-upload-hint" style={{ color: '#999' }}>请稍候...</p>
        </div>
      ) : disabled ? (
        <p style={{ color: '#999' }}>已禁用</p>
      ) : (
        <>
          <p style={{ marginBottom: 8, fontSize: 36, color: '#1890ff' }}>
            <InboxOutlined />
          </p>
          <p className="ant-upload-text" style={{ marginBottom: 4 }}>点击或拖拽文件到此区域上传</p>
          <p className="ant-upload-hint" style={{ color: '#999', fontSize: 12 }}>
            支持单个或批量上传，单文件不超过 {maxSize}MB
          </p>
        </>
      )}
    </div>
  );

  // 受控模式：显示已上传文件列表 + 上传区
  if (onChange) {
    return (
      <div style={{ width: '100%' }}>
        {/* 已上传文件列表 */}
        {value.length > 0 && (
          <div style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: 12,
            marginBottom: value.length > 0 ? 12 : 0,
          }}>
            {value.map((item, index) => (
              <div
                key={item.fileId || index}
                style={{
                  position: 'relative',
                  width: 120,
                  padding: 8,
                  border: '1px solid #e8e8e8',
                  borderRadius: 6,
                  backgroundColor: '#fafafa',
                  textAlign: 'center',
                }}
              >
                {!disabled && (
                  <Button
                    type="text"
                    danger
                    size="small"
                    icon={<DeleteOutlined />}
                    onClick={(e) => { e.stopPropagation(); handleRemove(index); }}
                    style={{ position: 'absolute', top: 2, right: 2, zIndex: 1 }}
                  />
                )}
                <div>{getFileIcon(item.fileName)}</div>
                <div
                  style={{
                    marginTop: 4,
                    fontSize: 12,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                    maxWidth: 104,
                  }}
                  title={item.fileName}
                >
                  {item.fileName}
                </div>
                <div style={{ fontSize: 11, color: '#999' }}>
                  {formatFileSize(item.fileSize)}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* 上传区域 */}
        {(multiple || value.length === 0) && uploadAreaNode}
      </div>
    );
  }

  // 非受控模式：仅显示上传区域
  return uploadAreaNode;
};

// 导出 MD5 计算工具函数供外部使用
export { calculateMd5 as calcFileMd5 };

export default FileUpload;

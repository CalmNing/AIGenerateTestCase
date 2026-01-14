import os
import base64
from typing import Dict, Any, Optional
from PIL import Image
import pytesseract
import io
import re
import json


class OCRService:
    """OCR图片识别服务"""
    
    # 类变量，用于存储PaddleOCR实例（单例模式）
    _paddle_ocr_instance = None

    def __init__(self, engine: str = 'tesseract'):
        """
        初始化OCR服务
        :param engine: 引擎类型 'tesseract' 或 'paddleocr'
        """
        self.engine = engine

        if engine == 'paddleocr':
            try:
                from paddleocr import PaddleOCR
                # 使用单例模式，确保PaddleOCR只被初始化一次
                if OCRService._paddle_ocr_instance is None:
                    OCRService._paddle_ocr_instance = PaddleOCR(use_angle_cls=True, lang='ch')
                self.paddle_ocr = OCRService._paddle_ocr_instance
            except ImportError:
                print("警告: PaddleOCR未安装，使用Tesseract代替")
                print("错误详情: 由于兼容性问题，PaddleOCR在当前环境中无法使用")
                self.engine = 'tesseract'
            except Exception as e:
                print(f"警告: PaddleOCR初始化失败，使用Tesseract代替。错误: {e}")
                self.engine = 'tesseract'

    def extract_text(self, image_data, is_base64: bool = False) -> Dict[str, Any]:
        """
        从图片中提取文字
        :param image_data: 图片数据（路径、二进制或base64）
        :param is_base64: 是否为base64编码
        :return: 提取结果
        """
        try:
            # 处理不同输入格式
            if isinstance(image_data, str) and os.path.exists(image_data):
                # 文件路径
                img = Image.open(image_data)
            elif is_base64:
                # Base64编码
                img_data = base64.b64decode(image_data)
                img = Image.open(io.BytesIO(img_data))
            else:
                # 直接传入图像对象
                img = image_data

            # 使用Tesseract进行OCR识别
            if self.engine == 'tesseract':
                # 使用pytesseract提取文本
                text = pytesseract.image_to_string(img, lang='chi_sim+eng')
                # 计算置信度
                data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT, lang='chi_sim+eng')
                confidences = [int(conf) for conf in data['conf'] if int(conf) > 0]
                avg_confidence = sum(confidences) / len(confidences) if confidences else 0
                
                result = {
                    'success': True,
                    'text': text.strip(),
                    'engine_used': 'tesseract',
                    'confidence': round(avg_confidence, 2)
                }
            elif self.engine == 'paddleocr':
                # 如果PaddleOCR可用，使用它
                result_text = self.paddle_ocr.ocr(str(img), cls=True)
                result_text = "\n".join([item[1][0] for line in result_text for item in line if item[1][0]])
                
                result = {
                    'text': result_text,
                    'engine_used': 'paddleocr',
                    'confidence': 0.9  # PaddleOCR通常有较高的置信度
                }
            else:
                result = {
                    'success': False,
                    'text': '',
                    'engine_used': 'none',
                    'confidence': 0.0
                }

            # 清理文本
            result['text'] = self._clean_text(result['text'])
            result['confidence'] = self._calculate_confidence(result['text'])

            return result

        except Exception as e:
            return {
                'text': '',
                'engine_used': self.engine,
                'confidence': 0.0,
                'error': str(e)
            }

    def _clean_text(self, text: str) -> str:
        """清洗和格式化提取的文本"""
        if not text:
            return ""

        # 去除多余空白字符
        # cleaned = re.sub(r'[ ]{2,}', ' ', text)
        cleaned = re.sub(r'[^\S\n]+', ' ', text)
        cleaned = cleaned.replace(' ','')
        # 常见OCR错误修正（可根据需要扩展）
        corrections = {
            r'测[试拭]': '测试',
            r'需[求球]': '需求',
            r'用[例冽]': '用例',
            r'人[胁胥肥胶]': '人脸',
            r'[筑]选': '筛选',
            r'[按抒][煌]': '按照',
            r'是[吴]必[塔]': '是否必填',
            r'[院炜骊]片': '照片',
            r'一张[国]': '一张图',
            r'手动[辐人]': '手动输入',
            r'判[唤]': '判断',
            r'选[&]从': '选否从',
            r'工[秋]': '工种',
            r'[花芬]名[冉朋服]': '花名册',
            r'[迹]行': '进行',
            r'[行]行': '行号',
            r'[项顶][目日]': '项目',
            r'[盛]更新': '覆盖',
            r'[球]认': '确认',
            r'[缓编维缘][糊猩锭锵辐输]': '编辑',
            r'[仁从]支[挂持]': '仅支持',
            r'[部叶]': '都可',
            r'[銮]子': '盒子',
            r'[闭合]步': '同步',
            r'[胡制剪]除': '删除',
            r'[合]一': '同一',
            r'[司]能': '可能',
            r'不[间]': '不同',
            r'在[地圭]': '在场',
            r'[史]有': '只有',
            r'[逗通][锴输]': '逻辑',
            r'[冒]导人': '再导入',
            r'[仁]么': '什么',
            r'[翻][盘]': '覆盖',
            r'[逆]场': '退场',
            r'[霁]次': '再次',
            r'[擎]作': '操作',
            r'[珞线]': '班组',
            r'[训]备': '设备',
            r'[逊招]': '选择',

            # 添加更多修正规则...
        }

        for pattern, replacement in corrections.items():
            cleaned = re.sub(pattern, replacement, cleaned)

        return cleaned

    def _calculate_confidence(self, text: str) -> float:
        """计算文本置信度（简单实现）"""
        if not text:
            return 0.0

        # 基于长度、标点、中文字符等计算置信度
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        total_chars = len(text)

        if total_chars == 0:
            return 0.0

        # 简单置信度计算
        chinese_ratio = chinese_chars / total_chars
        sentence_length = len(text.split('。'))  # 根据句号分割句子

        confidence = min(0.95, chinese_ratio * 0.7 + sentence_length * 0.1)
        return round(confidence, 2)


# 示例用法
if __name__ == "__main__":
    # 创建OCR服务实例，优先使用Tesseract以避免PaddleOCR的兼容性问题
    print("初始化OCR服务...")
    ocr_service = OCRService(engine='paddleocr')  # 强制使用Tesseract
    
    print(f"OCR服务使用引擎: {ocr_service.engine}")
    
    # 示例：如果存在图像文件则进行OCR识别
    image_path = "test2.png"  # 可以根据需要更改此路径
    if os.path.exists(image_path):
        result = ocr_service.extract_text(image_path)
        print(f"OCR识别结果: {result}")
    else:
        print(f"警告: 图像文件 {image_path} 不存在")
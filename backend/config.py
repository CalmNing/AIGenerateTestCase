import os
import json
from typing import Optional, Literal

# 配置文件路径
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(PROJECT_ROOT, "data", "config.json")

# 模型类型
default_model_type: Literal["api", "ollama"] = "api"

# 默认配置
default_config = {
    "model_type": default_model_type,
    "api_key": "",
    "ollama_model": "llama3",
    "ollama_base_url": "http://localhost:11434",
    "ollama_url": "http://localhost:11434"
}

class ConfigManager:
    """配置管理器"""
    
    def __init__(self):
        self.config = self.load_config()
    
    def load_config(self) -> dict:
        """加载配置文件"""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
                # 合并默认配置，确保所有字段都存在
                merged_config = default_config.copy()
                merged_config.update(config)
                return merged_config
            except Exception as e:
                print(f"加载配置文件失败: {e}")
                return default_config.copy()
        else:
            return default_config.copy()
    
    def save_config(self) -> None:
        """保存配置到文件"""
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"保存配置文件失败: {e}")
    
    def get(self, key: str, default=None):
        """获取配置值"""
        return self.config.get(key, default)
    
    def set(self, key: str, value) -> None:
        """设置配置值"""
        self.config[key] = value
        self.save_config()
    
    def get_model_type(self) -> Literal["api", "ollama"]:
        """获取模型类型"""
        return self.config.get("model_type", default_model_type)
    
    def get_api_key(self) -> str:
        """获取API Key"""
        return self.config.get("api_key", "")
    
    def get_ollama_model(self) -> str:
        """获取Ollama模型名称"""
        return self.config.get("ollama_model", "llama3")
    
    def get_ollama_base_url(self) -> str:
        """获取Ollama基础URL"""
        return self.config.get("ollama_base_url", "http://localhost:11434")

    def get_ollama_url(self) -> str:
        """兼容方法：优先返回 'ollama_url'，否则返回 'ollama_base_url'。"""
        return self.config.get("ollama_url") or self.config.get("ollama_base_url", "http://localhost:11434")

# 创建全局配置管理器实例
config_manager = ConfigManager()

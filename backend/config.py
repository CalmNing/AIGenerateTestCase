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
    "api_base_url": "https://api.deepseek.com",
    "api_model": "deepseek-v4-flash",
    "ollama_model": "llama3",
    "ollama_base_url": "http://localhost:11434",
    "ollama_url": "http://localhost:11434",
    "mcp_enabled": True,
    "mcp_server_url": "http://lanhu-mcp:8000/mcp",
    "mcp_server_url_fallback": "http://localhost:8002/mcp",
    "lanhu_cookie": "",
    "bug_link_template": "",
    # 飞书电子表格(sheet)读取所需的应用身份凭据（应用身份 = tenant_access_token）。
    # 仅当飞书文档中嵌入了 sheet/多维表格、且需要提取其单元格内容时才需要。
    # 需同时开通权限范围 sheets:spreadsheet:read（电子表格）与 bitable:app:read（多维表格）。
    "feishu_app_id": "",
    "feishu_app_secret": "",
    # 飞书文档内嵌图片解析（多模态描述）：
    # 默认沿用主模型配置（deepseek-v4-flash 已实测支持图像理解）；
    # 主模型不支持图片时会自动跳过，不影响正文预取。
    # 如需独立视觉模型，配置 vision_model（+ 可选 vision_api_base_url / vision_api_key）。
    "feishu_image_enabled": True,
    # 默认 8 张：图片描述会显著加长需求文本，20 张时曾把生成输出顶到 max_tokens 上限
    # （>=16384 截断），6~10 张是实测比较稳的区间；可按需调大。
    "feishu_image_max": 8,
    "feishu_image_max_chars": 300,
    "vision_model": "",
    "vision_api_base_url": "",
    "vision_api_key": "",
    # prompt caching: None = 按 base_url 自动判定（DeepSeek 开启）；True/False = 强制开关
    "prompt_cache_enabled": None,
    # Mock 正向代理：监听端口与重启后是否自动恢复运行
    "mock_proxy_port": 9000,
    "mock_proxy_enabled": False,
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

    def is_mcp_enabled(self) -> bool:
        """是否启用 MCP 工具"""
        return self.config.get("mcp_enabled", True)

    def get_prompt_cache_enabled(self) -> Optional[bool]:
        """Prompt 缓存开关。

        - None（默认）：由 build_chat_model 按 base_url 自动判定（DeepSeek 开启）。
        - True / False：强制开启 / 关闭，供不兼容网关或特殊场景使用。
        """
        value = self.config.get("prompt_cache_enabled", None)
        if value is None:
            return None
        return bool(value)

    def get_mcp_server_url(self) -> str:
        """获取 MCP 服务器地址"""
        return self.config.get("mcp_server_url", "http://lanhu-mcp:8000/mcp")

    def get_mcp_server_url_fallback(self) -> str:
        """获取 MCP 服务器备用地址"""
        return self.config.get("mcp_server_url_fallback", "http://localhost:8002/mcp")

    def get_feishu_app_id(self) -> str:
        """飞书应用 app_id（用于应用身份 tenant_access_token 读取嵌入的 sheet/多维表格）。"""
        return self.config.get("feishu_app_id", "")

    def get_feishu_app_secret(self) -> str:
        """飞书应用 app_secret（同上）。"""
        return self.config.get("feishu_app_secret", "")

    def get_api_base_url(self) -> str:
        """获取 OpenAI 兼容 API 基础地址。"""
        return self.config.get("api_base_url", "https://api.deepseek.com") or "https://api.deepseek.com"

    def get_api_model(self) -> str:
        """获取主模型名称。"""
        return self.config.get("api_model", "deepseek-v4-flash") or "deepseek-v4-flash"

    # ---- 飞书文档内嵌图片（多模态） ----

    def is_feishu_image_enabled(self) -> bool:
        """是否启用飞书文档内嵌图片解析。"""
        return bool(self.config.get("feishu_image_enabled", True))

    def get_feishu_image_max(self) -> int:
        """单篇文档最多解析的图片数量（上限 50，控制 token 成本）。"""
        try:
            return min(50, max(1, int(self.config.get("feishu_image_max", 8))))
        except (TypeError, ValueError):
            return 8

    def get_feishu_image_max_chars(self) -> int:
        """单张图片描述的字符上限。"""
        try:
            return max(50, int(self.config.get("feishu_image_max_chars", 300)))
        except (TypeError, ValueError):
            return 300

    def get_vision_model(self) -> str:
        """独立视觉模型名称（空 = 沿用主模型）。"""
        return (self.config.get("vision_model") or "").strip()

    def get_vision_api_base_url(self) -> str:
        """独立视觉模型的 API 地址（空 = 沿用主模型地址）。"""
        return (self.config.get("vision_api_base_url") or "").strip()

    def get_vision_api_key(self) -> str:
        """独立视觉模型的 API Key（空 = 沿用主模型 Key）。"""
        return (self.config.get("vision_api_key") or "").strip()

    def get_mock_proxy_port(self) -> int:
        """获取 Mock 正向代理监听端口"""
        try:
            return int(self.config.get("mock_proxy_port", 9000))
        except (TypeError, ValueError):
            return 9000

    def is_mock_proxy_enabled(self) -> bool:
        """Mock 正向代理是否应在启动时自动恢复运行"""
        return bool(self.config.get("mock_proxy_enabled", False))

# 创建全局配置管理器实例
config_manager = ConfigManager()

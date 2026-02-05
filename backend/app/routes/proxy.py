from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import httpx

router = APIRouter(prefix="/proxy", tags=["proxy"])

# 允许的域名白名单（可选，增强安全性）
ALLOWED_DOMAINS = [
    "example.com",
    "api.example.org",
    # 可以添加更多域名或使用通配符
]

class ProxyRequest(BaseModel):
    """代理请求模型"""
    url: str
    method: str = "GET"
    headers: dict = None
    data: dict = None
    params: dict = None

def is_valid_url(url: str) -> bool:
    """验证URL是否安全"""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if not parsed.scheme in ["http", "https"]:
        return False
    # 可选：验证域名是否在白名单中
    # domain = parsed.netloc.split(':')[0]
    # return any(domain.endswith(allowed) for allowed in ALLOWED_DOMAINS)
    return True

@router.post("/forward")
async def forward_request(
    request: ProxyRequest
):
    """转发请求到目标URL"""
    if not is_valid_url(request.url):
        raise HTTPException(status_code=400, detail="Invalid URL")
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.request(
                method=request.method,
                url=request.url,
                headers=request.headers,
                json=request.data,
                params=request.params,
                timeout=30.0
            )
            # 尝试解析响应为JSON
            try:
                response_data = response.json()
            except Exception:
                response_data = response.text
            
            return {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "data": response_data
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Proxy error: {str(e)}")

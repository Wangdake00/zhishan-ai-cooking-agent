"""
Agent 工具函数集合
"""
from app.common.logger import logger
import base64
import ipaddress
import mimetypes
import socket
import urllib.parse

# 下载大小上限（5MB），防止超大响应打爆内存
_MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _validate_public_url(image_src: str) -> None:
    """
    SSRF 防护：校验 URL 指向公网地址。
    拒绝私网/环回/链路本地/保留地址（含 169.254.169.254 云厂商元数据端点）。
    注意：DNS 解析与实际建连之间存在 TOCTOU 窗口（DNS rebinding），
    对图片下载场景按可接受风险处理。
    """
    parsed = urllib.parse.urlparse(image_src)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"不支持的图片协议：{parsed.scheme}")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL 缺少主机名")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise ValueError(f"主机名无法解析：{hostname}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError(f"拒绝访问非公网地址：{hostname} -> {ip}")


async def ensure_image_base64_data_url(image_src: str) -> str:
    """
    把传入的图片源（http/https URL，或已经是 base64 data URL）统一转换成 data:image/xxx;base64,... 格式。
    这样多模态模型不需要再去外网下载图片，彻底避免 Failed to download multimodal content。

    安全约束：仅允许公网 http/https、启用 TLS 校验、限制响应大小。
    """
    if image_src.startswith("data:image/"):
        return image_src

    if image_src.startswith("file://"):
        raise ValueError("不支持 file:// 本地文件路径，请使用上传或内嵌 base64 方式")

    _validate_public_url(image_src)

    parsed = urllib.parse.urlparse(image_src)
    guessed_type, _ = mimetypes.guess_type(parsed.path)
    content_type = guessed_type or "image/jpeg"

    import aiohttp

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(image_src, allow_redirects=True) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"图片下载失败 HTTP {resp.status}")
            # 重定向可能跳向内网地址，对最终 URL 复检
            if str(resp.url) != image_src:
                _validate_public_url(str(resp.url))
            raw = b""
            async for chunk in resp.content.iter_chunked(64 * 1024):
                raw += chunk
                if len(raw) > _MAX_IMAGE_BYTES:
                    raise ValueError(f"图片超过大小上限 {_MAX_IMAGE_BYTES // (1024 * 1024)}MB")
            ctype = resp.headers.get("Content-Type", "")
            if ctype.startswith("image/"):
                content_type = ctype.split(";")[0].strip()

    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{content_type};base64,{encoded}"

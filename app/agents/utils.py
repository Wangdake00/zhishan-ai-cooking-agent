"""
Agent 工具函数集合
"""
from app.common.logger import logger
import base64
import mimetypes
import urllib.request
import urllib.parse
import ssl


async def ensure_image_base64_data_url(image_src: str) -> str:
    """
    把传入的图片源（http/https URL，或已经是 base64 data URL）统一转换成 data:image/xxx;base64,... 格式。
    这样多模态模型不需要再去外网下载图片，彻底避免 Failed to download multimodal content。
    """
    if image_src.startswith("data:image/"):
        return image_src

    if image_src.startswith("file://"):
        raise ValueError("不支持 file:// 本地文件路径，请使用上传或内嵌 base64 方式")

    parsed = urllib.parse.urlparse(image_src)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"不支持的图片协议：{parsed.scheme}")

    guessed_type, _ = mimetypes.guess_type(parsed.path)
    content_type = guessed_type or "image/jpeg"
    raw: bytes = b""

    try:
        import aiohttp
        timeout = aiohttp.ClientTimeout(total=30)
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            async with session.get(image_src, allow_redirects=True) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f"图片下载失败 HTTP {resp.status}")
                raw = await resp.read()
                if resp.headers.get("Content-Type") and resp.headers["Content-Type"].startswith("image/"):
                    content_type = resp.headers["Content-Type"].split(";")[0].strip()
    except ImportError:
        raw = b""
    except Exception as http_err:
        logger.warning(f"aiohttp 下载图片失败，回退标准库 urllib：{http_err}")
        raw = b""

    if not raw:
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(
            image_src,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ZhiShanAI/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            raw = resp.read()
            info = resp.info()
            ctype = info.get_content_type()
            if ctype and ctype.startswith("image/"):
                content_type = ctype

    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def ensure_image_base64_data_url_sync(image_src: str) -> str:
    """同步版本（用于非 async 上下文）"""
    if image_src.startswith("data:image/"):
        return image_src

    if image_src.startswith("file://"):
        raise ValueError("不支持 file:// 本地文件路径")

    parsed = urllib.parse.urlparse(image_src)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"不支持的图片协议：{parsed.scheme}")

    guessed_type, _ = mimetypes.guess_type(parsed.path)
    content_type = guessed_type or "image/jpeg"

    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(
        image_src,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
        raw = resp.read()
        info = resp.info()
        ctype = info.get_content_type()
        if ctype and ctype.startswith("image/"):
            content_type = ctype

    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{content_type};base64,{encoded}"
"""
阿里云 OSS 公共能力（对象键持久化 + 预签名读取）

设计说明：
- 长会话场景下消息只持久化图片对象键（image_ref），避免 base64 常驻 checkpoint
  导致上下文与存储膨胀；读取历史时按需重签 GET URL 返回前端。
- OSS SDK 为同步实现，统一用 asyncio.to_thread 包装，避免阻塞事件循环。
- 未配置 OSS 或调用失败时抛出异常，由调用方降级（降级策略不在此处做强约束）。
"""
import asyncio
import base64
import hashlib
import io
import mimetypes
import urllib.parse
from datetime import timedelta
from typing import Optional

import alibabacloud_oss_v2 as oss

from app.common.logger import logger
from app.core.config import settings

# 预签名 GET URL 最长有效期（阿里云 OSS 限制 7 天）
_PRESIGN_GET_SECONDS = 7 * 24 * 3600

_client: Optional[oss.Client] = None


def is_configured() -> bool:
    """OSS 是否已配置完整凭据与 bucket"""
    return bool(
        settings.oss_access_key_id
        and settings.oss_access_key_secret
        and settings.oss_bucket
    )


def _get_client() -> oss.Client:
    """惰性创建 OSS 客户端（显式静态凭据，不依赖进程环境变量）"""
    global _client
    if _client is None:
        if not is_configured():
            raise RuntimeError("OSS 未配置（缺 OSS_ACCESS_KEY_ID/SECRET/BUCKET）")
        cfg = oss.config.load_default()
        cfg.credentials_provider = oss.credentials.StaticCredentialsProvider(
            settings.oss_access_key_id, settings.oss_access_key_secret
        )
        cfg.region = settings.oss_region
        cfg.endpoint = settings.oss_endpoint
        _client = oss.Client(cfg)
    return _client


def extract_object_key(image_url: str) -> Optional[str]:
    """
    从 OSS 访问 URL 中提取对象键（用于复用前端已上传的图片，不重复转存）。

    支持两种寻址方式：
    - 虚拟主机：https://{bucket}.{region}.aliyuncs.com/{key}
    - 路径风格：https://{endpoint}/{bucket}/{key}

    非本 bucket 的 URL 返回 None，由调用方按需转存。
    """
    if not image_url or not settings.oss_bucket:
        return None
    try:
        parsed = urllib.parse.urlparse(image_url)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None

    host = parsed.hostname.lower()
    bucket = settings.oss_bucket.lower()
    endpoint = settings.oss_endpoint.lower()
    path = urllib.parse.unquote(parsed.path.lstrip("/"))

    if host == endpoint:
        prefix = f"{bucket}/"
        if not path.startswith(prefix):
            return None
        return path[len(prefix):] or None
    if host.startswith(f"{bucket}.") and host.endswith(".aliyuncs.com"):
        return path or None
    return None


def _presign_get_sync(key: str, expires_seconds: int) -> str:
    """同步预签名 GET URL（内部函数，调用方需放入线程池）"""
    result = _get_client().presign(
        oss.GetObjectRequest(bucket=settings.oss_bucket, key=key),
        expires=timedelta(seconds=min(expires_seconds, _PRESIGN_GET_SECONDS)),
    )
    return result.url.strip('"')


async def presign_get(key: str, expires_seconds: int = _PRESIGN_GET_SECONDS) -> str:
    """异步生成对象 GET 预签名 URL（历史消息读取时重签）"""
    return await asyncio.to_thread(_presign_get_sync, key, expires_seconds)


def build_image_object_key(user_id: int, thread_id: str, data_url: str) -> str:
    """按用户/会话隔离生成图片对象键，内容哈希避免重复写入同名对象"""
    mime = "image/jpeg"
    payload = data_url
    if data_url.startswith("data:"):
        header, _, payload = data_url.partition(",")
        mime = header[5:].split(";")[0] or mime
    ext = mimetypes.guess_extension(mime) or ".jpg"
    digest = hashlib.sha256(payload.encode("ascii", "ignore")).hexdigest()[:16]
    return f"chat/{user_id}/{thread_id}/{digest}{ext}"


def _upload_data_url_sync(data_url: str, key: str) -> str:
    """同步上传 base64 data URL 到 OSS（内部函数，调用方需放入线程池）"""
    header, _, payload = data_url.partition(",")
    if not data_url.startswith("data:") or not payload:
        raise ValueError("非法图片 data URL")
    mime = header[5:].split(";")[0] or "image/jpeg"
    raw = base64.b64decode(payload)
    _get_client().put_object(oss.PutObjectRequest(
        bucket=settings.oss_bucket,
        key=key,
        content_type=mime,
        body=io.BytesIO(raw),
    ))
    logger.info(f"[ImageGovern] 图片已转存 OSS: key={key}, size={len(raw)}B")
    return key


async def upload_data_url(data_url: str, key: str) -> str:
    """异步上传 base64 data URL 到 OSS，返回对象键"""
    return await asyncio.to_thread(_upload_data_url_sync, data_url, key)

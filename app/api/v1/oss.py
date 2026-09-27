import alibabacloud_oss_v2 as oss
from fastapi import APIRouter, Depends
from datetime import timedelta
import os

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.exceptions import BadRequestException

# 将配置写入环境变量（OSS SDK 通过环境变量读取）
os.environ["OSS_ACCESS_KEY_ID"] = settings.oss_access_key_id
os.environ["OSS_ACCESS_KEY_SECRET"] = settings.oss_access_key_secret

router = APIRouter()

credentials_provider = oss.credentials.EnvironmentVariableCredentialsProvider()

cfg = oss.config.load_default()
cfg.credentials_provider = credentials_provider
cfg.region = settings.oss_region

client = oss.Client(cfg)


@router.get("/oss/presign")
def presign_endpoint(filename: str, current_user: dict = Depends(get_current_user)):
    content_type_map = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
    }
    ext = filename.split(".")[-1].lower() if "." in filename else "jpg"
    content_type = content_type_map.get(ext, "application/octet-stream")

    # 规范化 object key：剥离路径成分（防 ../ 越权覆盖任意对象），
    # 并按用户隔离前缀（防止用户互相覆盖文件）
    safe_name = os.path.basename(filename.replace("\\", "/")).strip()
    if not safe_name or safe_name in {".", ".."}:
        raise BadRequestException("非法文件名")
    if len(safe_name) > 100:
        raise BadRequestException("文件名过长")
    object_key = f"uploads/{current_user['id']}/{safe_name}"

    put_result = client.presign(oss.PutObjectRequest(
        bucket=settings.oss_bucket,
        key=object_key,
        content_type=content_type,
    ), expires=timedelta(seconds=3600))

    # GET 有效期保留 7 天（阿里云 OSS 预签名 URL 最大有效期 7 天）
    # accessUrl 会被持久化在会话消息/收藏中，超过 7 天后会过期
    # TODO: 后续可实现重新签名端点 /oss/access?key=xxx 动态刷新过期 URL
    get_result = client.presign(oss.GetObjectRequest(
        bucket=settings.oss_bucket,
        key=object_key,
    ), expires=timedelta(days=7))

    return {
        "uploadUrl": put_result.url.strip('"'),
        "contentType": content_type,
        "accessUrl": get_result.url.strip('"'),
    }
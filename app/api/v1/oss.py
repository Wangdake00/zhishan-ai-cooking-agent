import alibabacloud_oss_v2 as oss
from fastapi import APIRouter
from datetime import timedelta
import os

from app.core.config import settings

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
def chat_endpoint(filename: str):
    content_type_map = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
    }
    ext = filename.split(".")[-1].lower() if "." in filename else "jpg"
    content_type = content_type_map.get(ext, "application/octet-stream")

    put_result = client.presign(oss.PutObjectRequest(
        bucket=settings.oss_bucket,
        key=filename,
        content_type=content_type,
    ), expires=timedelta(seconds=3600))

    get_result = client.presign(oss.GetObjectRequest(
        bucket=settings.oss_bucket,
        key=filename,
    ), expires=timedelta(days=7))

    return {
        "uploadUrl": put_result.url.strip('"'),
        "contentType": content_type,
        "accessUrl": get_result.url.strip('"'),
    }
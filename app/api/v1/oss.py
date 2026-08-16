import alibabacloud_oss_v2 as oss
from fastapi import APIRouter
from datetime import timedelta
import os
# 加载环境变量
from dotenv import load_dotenv

load_dotenv()
router = APIRouter()

# 从环境变量中加载凭证信息，用于身份验证
credentials_provider = oss.credentials.EnvironmentVariableCredentialsProvider()

# 加载SDK的默认配置，并设置凭证提供者
cfg = oss.config.load_default()
cfg.credentials_provider = credentials_provider

# OSS 域名与 Region 配置（二者必须对应同一个地域，否则预签名会校验失败）
OSS_REGION = os.getenv("OSS_REGION", "cn-shenzhen")
OSS_ENDPOINT = os.getenv("OSS_ENDPOINT", "oss-cn-shenzhen.aliyuncs.com")
OSS_BUCKET = os.getenv("OSS_BUCKET")

# 必须指定Region ID，SDK会根据Region自动构造HTTPS访问域名
# Region 要和 OSS_ENDPOINT 所在地域一致，否则预签名URL会被OSS拒绝
cfg.region = OSS_REGION

# 使用配置好的信息创建OSS客户端
client = oss.Client(cfg)


@router.get("/oss/presign")
def chat_endpoint(filename: str):
    # 根据文件扩展名判断 Content-Type
    content_type_map = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
    }
    ext = filename.split(".")[-1].lower() if "." in filename else "jpg"
    content_type = content_type_map.get(ext, "application/octet-stream")

    pre_result = client.presign(oss.PutObjectRequest(
        bucket=OSS_BUCKET,
        key=filename,
        content_type=content_type,
    ), expires=timedelta(seconds=3600))

    # 返回上传 URL 和可访问的图片路径
    return {
        "uploadUrl": pre_result.url.strip('"'),
        "contentType": content_type,
        "accessUrl": f"https://{OSS_BUCKET}.{OSS_ENDPOINT}/{filename}"
    }

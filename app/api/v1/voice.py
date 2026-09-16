"""
语音识别路由：上传音频 → DashScope Paraformer 转写为文字
"""
import os
import asyncio
import logging
import tempfile
from http import HTTPStatus

import dashscope
from dashscope.audio.asr import Recognition
from fastapi import APIRouter, Depends, UploadFile, File

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.exceptions import BadRequestException, ExternalServiceException

router = APIRouter()
logger = logging.getLogger(__name__)

MAX_AUDIO_SIZE = 10 * 1024 * 1024  # 10MB


def _run_recognition(file_path: str) -> str:
    """同步调用 DashScope Paraformer 非流式识别（在线程池中执行）"""
    recognition = Recognition(
        model=settings.asr_model,
        format="wav",
        sample_rate=16000,
        language_hints=["zh", "en"],
        callback=None,
    )
    result = recognition.call(file_path)

    if result.status_code != HTTPStatus.OK:
        logger.error(f"[voice] DashScope ASR 失败: {result.message}")
        raise ExternalServiceException(
            service="DashScope-ASR",
            message=f"语音识别失败: {result.message}",
        )

    sentences = result.get_sentence()
    logger.info(f"[voice] ASR 返回句子数: {len(sentences) if sentences else 0}")
    if not sentences:
        return ""

    # 非流式 call 返回 List[Dict]；兼容流式回调中可能返回单个 Dict 的情况
    if isinstance(sentences, dict):
        sentences = [sentences]

    text = "".join(s.get("text", "") for s in sentences)
    return text.strip()


@router.post("/chat/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """语音转文字：上传音频文件，返回转写文本"""
    # MIME 校验
    if not audio.content_type or not audio.content_type.startswith("audio/"):
        raise BadRequestException("仅支持音频文件（audio/*）")

    # 读取内容
    content = await audio.read()
    if not content:
        raise BadRequestException("音频内容为空")
    if len(content) > MAX_AUDIO_SIZE:
        raise BadRequestException(f"音频文件过大（超过 {MAX_AUDIO_SIZE // 1024 // 1024}MB）")

    # 写入临时文件
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    try:
        tmp.write(content)
        tmp.close()

        # 确保 DashScope API Key 已设置
        dashscope.api_key = settings.dashscope_api_key

        # Recognition.call 是同步阻塞调用，放到线程池执行避免阻塞事件循环
        text = await asyncio.to_thread(_run_recognition, tmp.name)

        logger.info(
            f"[voice] 用户 {current_user['id']} 语音转写成功，"
            f"音频 {len(content)} bytes → 文字 {len(text)} 字"
        )
        return {"text": text}
    finally:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)

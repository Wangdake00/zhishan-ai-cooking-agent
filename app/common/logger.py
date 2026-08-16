# app/core/logger.py
import logging
import sys

# 配置日志格式：时间 - 级别 - 模块 - 消息
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format=LOG_FORMAT,
        handlers=[
            logging.StreamHandler(sys.stdout), # 输出到控制台
            # logging.FileHandler("app.log")   # 如果需要存到文件可以开启
        ]
    )

# 创建一个全局的 logger 实例
logger = logging.getLogger("personal_chief")
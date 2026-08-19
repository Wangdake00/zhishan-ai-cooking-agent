"""
智膳 AI 统一配置管理
使用 Pydantic Settings 实现类型安全的环境变量管理。
"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ========== 服务基础配置 ==========
    app_name: str = "智膳 AI"
    app_version: str = "1.0.0"
    api_host: str = "127.0.0.1"
    api_port: int = 8001
    debug: bool = False

    # ========== 阿里云百炼（大模型服务） ==========
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # ========== Agent 模型配置 ==========
    llm_model: str = "qwen3.5-plus"
    llm_timeout_seconds: int = 120
    llm_max_retries: int = 2

    # ========== RAG / ChromaDB 向量检索 ==========
    chroma_db_dir: str = "db/chroma_data"
    chroma_collection_name: str = "recipes"
    embedding_model: str = "text-embedding-v3"
    embedding_batch_size: int = 10
    rerank_model: str = "gte-rerank"
    rag_top_k: int = 10
    rag_top_n: int = 5

    # ========== Tavily（Agent 联网搜索工具） ==========
    tavily_api_key: str = ""

    # ========== JWT 认证 ==========
    jwt_secret_key: str = "dev-secret-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_days: int = 7

    # ========== 阿里云 OSS（图片存储） ==========
    oss_access_key_id: str = ""
    oss_access_key_secret: str = ""
    oss_bucket: str = ""
    oss_region: str = "cn-shenzhen"
    oss_endpoint: str = "oss-cn-shenzhen.aliyuncs.com"

    # ========== 数据库 ==========
    db_dir: str = "db"
    db_name: str = "personal_chief"

    @property
    def db_path(self) -> Path:
        return Path(self.db_dir) / f"{self.db_name}.db"

    @property
    def chroma_db_path(self) -> Path:
        return Path(self.chroma_db_dir)

    @property
    def is_production(self) -> bool:
        return not self.debug


settings = Settings()
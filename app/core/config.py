"""
智膳 AI 统一配置管理
使用 Pydantic Settings 实现类型安全的环境变量管理。
所有路径均基于项目根目录解析为绝对路径，避免因工作目录不同导致的数据文件错位。
"""
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（app/core/config.py → 上溯两级 = 项目根）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


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

    # ========== CORS 跨域白名单 ==========
    # 逗号分隔的来源列表；与 allow_credentials=True 组合时禁止使用 "*"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

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

    # ========== 语音识别（STT） ==========
    # DashScope Paraformer 实时语音识别模型
    asr_model: str = "paraformer-realtime-v2"

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

    # ========== 数据库（PostgreSQL） ==========
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_user: str = "chief"
    postgres_password: str = "chief123"
    postgres_db: str = "ai_chef"

    @model_validator(mode="after")
    def _validate_production_secrets(self) -> "Settings":
        """生产环境 fail-fast：禁止使用默认 JWT 密钥启动"""
        if not self.debug and self.jwt_secret_key == "dev-secret-change-in-production":
            raise ValueError(
                "生产环境（DEBUG=false）必须设置强随机 JWT_SECRET_KEY（.env），"
                "开发调试可显式设置 DEBUG=true"
            )
        return self

    @property
    def database_url(self) -> str:
        """SQLAlchemy async 连接串（asyncpg 驱动），密码特殊字符需 URL 编码"""
        return (
            f"postgresql+asyncpg://{quote_plus(self.postgres_user)}:{quote_plus(self.postgres_password)}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_dsn(self) -> str:
        """LangGraph AsyncPostgresSaver 连接串（不带驱动后缀），密码特殊字符需 URL 编码"""
        return (
            f"postgresql://{quote_plus(self.postgres_user)}:{quote_plus(self.postgres_password)}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def chroma_db_path(self) -> Path:
        """ChromaDB 向量库绝对路径"""
        return _PROJECT_ROOT / self.chroma_db_dir

    @property
    def cors_allow_origins(self) -> list[str]:
        """解析逗号分隔的 CORS 白名单为列表"""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return not self.debug


settings = Settings()
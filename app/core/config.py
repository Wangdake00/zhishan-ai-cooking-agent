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

    # ========== 长会话上下文治理 ==========
    # 总开关：关闭后所有裁剪/摘要/图片治理均不生效（等同旧行为）
    context_manage_enabled: bool = True
    # 触发阈值：轮数或估算 token 先到先触发（双阈值）
    context_max_turns: int = 10
    context_max_tokens: int = 6000
    # 裁剪窗口：保留首轮 + 最近 N 轮，中间段走摘要
    context_keep_recent_turns: int = 8
    context_keep_first_turn: bool = True
    # 摘要：同步增量摘要（只压缩新增的旧轮次），失败降级为不裁剪
    context_summary_enabled: bool = True
    context_summary_max_chars: int = 600
    context_summary_input_max_chars: int = 8000
    context_summary_timeout_seconds: int = 20
    # 硬上限：摘要连续失败时仍超限，则退化为无摘要滑动窗口（保证不撑爆上下文）
    context_hard_limit_tokens: int = 20000
    # 历史图片治理：历史轮次图片替换为占位文本，仅保留最近 N 张历史图 + 当前轮图片
    context_image_govern_enabled: bool = True
    context_image_keep_recent: int = 1
    # 图片持久化方式：oss_key（默认，落对象键，读取时重签 URL）| base64（旧行为）
    context_image_persist: str = "oss_key"
    # token 粗估系数（不引入 tokenizer 依赖）
    context_token_chars_per_token_zh: float = 1.3
    context_token_chars_per_token_other: float = 4.0
    context_image_token_estimate: int = 1280
    # 跨会话长期偏好记忆
    preference_memory_enabled: bool = True
    preference_max_items: int = 20

    # ========== RAG / ChromaDB 向量检索 ==========
    chroma_db_dir: str = "db/chroma_data"
    chroma_collection_name: str = "recipes"
    embedding_model: str = "text-embedding-v3"
    embedding_batch_size: int = 10
    rerank_model: str = "gte-rerank"
    rag_top_k: int = 10
    rag_top_n: int = 5
    # Rerank top1 相关性阈值：低于该分视为知识库无相关菜谱，走"未找到"链路
    # （依据 50 条评测：负例 top1 rerank 分全部 ≤0.179，正例最低 0.173，取 0.16 可拦截 9/10 负例）
    rag_rerank_threshold: float = 0.16

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
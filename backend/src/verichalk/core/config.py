"""配置：环境变量 > `.env` > 默认值。密钥只来自环境变量（`DASHSCOPE_*`），不进入 `Settings` 的可序列化字段。"""

from __future__ import annotations

import os
from enum import StrEnum
from functools import cached_property
from pathlib import Path

from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import ConfigError
from .features import FeatureFlags
from .paths import find_root


class Profile(StrEnum):
    cn = "cn"
    intl = "intl"


class LLMMode(StrEnum):
    live = "live"
    record = "record"
    replay = "replay"
    replay_or_live = "replay_or_live"
    replay_or_record = (
        "replay_or_record"  # 有录制就回放，没有就调用真实模型并录下来（让一组互相依赖的录制保持一致）
    )


class Credentials(BaseModel):
    api_key: SecretStr
    base_url: str

    def __repr__(self) -> str:  # 防止误打印
        return "Credentials(api_key=***, base_url=<llm-host>)"

    __str__ = __repr__


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VERICHALK_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    profile: Profile = Profile.cn
    root: Path | None = None
    data_dir: Path | None = None
    debug_token: SecretStr | None = None
    font_dir: Path | None = None  # 额外的字体目录（服务器镜像没有中文字体时放这里；本机默认用系统字体）

    llm_mode: LLMMode = LLMMode.live
    cassette_dir: Path | None = None
    badcase_dir: Path | None = None  # Badcase 入库目录；默认见 badcase_path
    cassette_namespace: str = "default"
    llm_concurrency: int = 8
    llm_max_retries: int = 3
    llm_timeout_s: float = 90.0

    item_concurrency: int = 4
    item_budget_s: float = 45.0  # 单题创作与核验的时间预算：超过后不再开始新的尝试
    run_budget_tokens: int = 600_000
    run_budget_cost: float = 8.0  # 以价格表币种计（默认人民币元）

    off: str = ""  # 关闭的特性开关，逗号分隔（消融实验用，见 core/features.py）
    on: str = ""  # 选择性开启的特性开关，逗号分隔
    pipeline: str = "classic"  # 链路预设：classic（M3 中期验证过的稳定链路，默认）| design（叠加"好题设计"开关）；环境变量 VERICHALK_PIPELINE
    warmup_ttl_s: float = 240.0  # 预热的有效期：期内重复触发不再发请求（前缀缓存有存活时间）

    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    upload_max_mb: int = 12

    @cached_property
    def features(self) -> FeatureFlags:
        return FeatureFlags.parse(self.off, self.on, self.pipeline)

    @cached_property
    def root_dir(self) -> Path:
        return (self.root or find_root()).resolve()

    @cached_property
    def config_dir(self) -> Path:
        return self.root_dir / "config"

    @cached_property
    def data_path(self) -> Path:
        return (self.data_dir or (self.root_dir / "data")).resolve()

    @cached_property
    def badcase_path(self) -> Path:
        """Badcase 入库目录：开发态（仓库里有 `eval/`）写进 `eval/badcases/`，部署态写进数据目录。"""
        if self.badcase_dir:
            return self.badcase_dir.resolve()
        dev = self.root_dir / "eval" / "badcases"
        return dev if dev.parent.exists() else self.data_path / "badcases"

    @cached_property
    def cassette_path(self) -> Path:
        return (self.cassette_dir or (self.root_dir / "eval" / "cassettes")).resolve()


_ENV_BY_PROFILE = {
    Profile.cn: ("DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL"),
    Profile.intl: ("DASHSCOPE_INTL_API_KEY", "DASHSCOPE_INTL_BASE_URL"),
}


def load_credentials(profile: Profile) -> Credentials:
    """读取所选 profile 的密钥与端点；缺失时抛 `ConfigError`（错误文本不含任何值）。"""
    key_var, url_var = _ENV_BY_PROFILE[profile]
    key, url = os.environ.get(key_var), os.environ.get(url_var)
    if not key or not url:
        raise ConfigError(f"profile={profile.value} 需要环境变量 {key_var} 与 {url_var}")
    return Credentials(api_key=SecretStr(key), base_url=url.rstrip("/"))

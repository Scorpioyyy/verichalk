"""带类型的错误体系。

每个错误携带：稳定的 `code`（日志与指标按它聚合）、给用户看的教师语言 `user_message`（不含堆栈与内部概念）、
是否可重试。技术细节只进 `message` 与 `details`，且在落盘前统一脱敏。
"""

from __future__ import annotations

from typing import Any


class VerichalkError(Exception):
    code = "internal_error"
    retryable = False
    user_message = "系统遇到了一点问题，请稍后重试。"

    def __init__(
        self,
        message: str = "",
        *,
        user_message: str | None = None,
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or self.code)
        if user_message is not None:
            self.user_message = user_message
        if retryable is not None:
            self.retryable = retryable
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        from .redact import redact

        return {
            "code": self.code,
            "message": redact(str(self)),
            "user_message": self.user_message,
            "retryable": self.retryable,
            "details": self.details,
        }


class ConfigError(VerichalkError):
    code = "config_error"
    user_message = "服务配置有误，请联系管理员。"


class NotFound(VerichalkError):
    code = "not_found"
    user_message = "没有找到对应的内容。"


class Conflict(VerichalkError):
    code = "conflict"
    user_message = "操作与当前状态冲突，请刷新后重试。"


class InvalidRequest(VerichalkError):
    code = "invalid_request"
    user_message = "请求内容不合法。"


# ---- 模型调用 ----
class LLMError(VerichalkError):
    code = "llm_error"
    retryable = True
    user_message = "模型服务暂时不可用，请稍后重试。"


class LLMTimeout(LLMError):
    code = "llm_timeout"
    user_message = "模型响应超时，请稍后重试。"


class LLMRateLimited(LLMError):
    code = "llm_rate_limited"
    user_message = "当前请求较多，请稍后重试。"


class LLMBadResponse(LLMError):
    code = "llm_bad_response"
    retryable = False


class LLMContentFiltered(LLMError):
    code = "llm_content_filtered"
    retryable = False
    user_message = "这段内容无法处理，请换一种说法或换一张图片。"


class LLMSchemaError(LLMError):
    """结构化输出在重试后仍不满足 schema。"""

    code = "llm_schema_error"
    retryable = False


class BudgetExceeded(VerichalkError):
    code = "budget_exceeded"
    user_message = "这次任务比较大，已达到处理上限，请缩小范围后重试。"


class ReplayMiss(VerichalkError):
    """replay 模式下没有找到录制的响应（提示词或参数变了，需要重新录制）。"""

    code = "replay_miss"
    user_message = "服务配置有误，请联系管理员。"


# ---- 沙箱 ----
class SandboxViolation(VerichalkError):
    code = "sandbox_violation"
    user_message = "题目校验失败，已自动重试。"


class SandboxTimeout(VerichalkError):
    code = "sandbox_timeout"
    user_message = "题目校验超时，已自动重试。"


class SandboxError(VerichalkError):
    code = "sandbox_error"
    user_message = "题目校验失败，已自动重试。"


# ---- 知识层 / 阶段 / 运行 ----
class KnowledgeError(VerichalkError):
    code = "knowledge_error"


class StageError(VerichalkError):
    code = "stage_error"

    def __init__(self, stage: str, message: str = "", **kw: Any) -> None:
        super().__init__(message or f"stage {stage} failed", **kw)
        self.stage = stage


class RunCancelled(VerichalkError):
    code = "run_cancelled"
    user_message = "已停止。"


# ---- 导出 ----
class ExportError(VerichalkError):
    code = "export_error"
    user_message = "导出没有成功，请稍后重试；如果反复出现，请把这份试卷反馈给我们。"

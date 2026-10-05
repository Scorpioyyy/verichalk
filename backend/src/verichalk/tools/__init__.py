"""工具层（L4）：带类型的工具注册表与有界工具循环。"""

from .knowledge_tools import default_registry, register_knowledge_tools
from .loop import AgentLoopResult, run_agent_loop
from .registry import Tool, ToolContext, ToolOutcome, ToolRegistry

__all__ = [
    "AgentLoopResult",
    "Tool",
    "ToolContext",
    "ToolOutcome",
    "ToolRegistry",
    "default_registry",
    "register_knowledge_tools",
    "run_agent_loop",
]

"""单次运行的预算：token 与成本上限。超限抛 `BudgetExceeded`，由编排器决定降级或终止（metrics E1/F3）。"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field

from ..core.errors import BudgetExceeded
from ..domain.llm import Usage


@dataclass
class Budget:
    max_tokens: int
    max_cost: float
    usage: Usage = field(default_factory=Usage)
    cost: float = 0.0
    unknown_cost_calls: int = 0
    calls: int = 0

    def add(self, usage: Usage, cost: float | None) -> None:
        self.usage = self.usage + usage
        self.calls += 1
        if cost is None:
            self.unknown_cost_calls += 1
        else:
            self.cost += cost

    @property
    def exceeded(self) -> bool:
        return self.usage.total_tokens > self.max_tokens or self.cost > self.max_cost

    def check(self) -> None:
        if self.exceeded:
            raise BudgetExceeded(
                f"预算超限：tokens={self.usage.total_tokens}/{self.max_tokens} cost={self.cost:.3f}/{self.max_cost}"
            )


_current: contextvars.ContextVar[Budget | None] = contextvars.ContextVar("verichalk_budget", default=None)


def current_budget() -> Budget | None:
    return _current.get()


def bind_budget(budget: Budget) -> contextvars.Token:
    return _current.set(budget)


def unbind_budget(token: contextvars.Token) -> None:
    _current.reset(token)

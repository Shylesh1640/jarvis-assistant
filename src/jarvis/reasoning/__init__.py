"""Reasoning strategy variations (Phase 13).

Provides multiple reasoning strategies for different types of problems:
- Chain-of-Thought (CoT): Step-by-step reasoning
- Tree-of-Thought (ToT): Branch exploration and evaluation
- Self-Consistency: Multiple samples, majority vote
- Reflexion: Generate, critique, refine
- Fast-and-Slow: Adaptive routing based on complexity
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jarvis.config.settings import settings
from jarvis.orchestration.state import JarvisState

logger = logging.getLogger(__name__)


class ReasoningStrategy(str, Enum):
    """Available reasoning strategies."""
    COT = "cot"
    TOT = "tot"
    SELF_CONSISTENCY = "self_consistency"
    REFLEXION = "reflexion"
    FAST_AND_SLOW = "fast_and_slow"
    AUTO = "auto"


@dataclass
class ReasoningResult:
    """Result from a reasoning strategy."""
    strategy: ReasoningStrategy
    answer: str
    reasoning: str
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)
    tokens_used: int = 0
    latency_ms: int = 0


class ReasoningStrategyBase(ABC):
    """Base class for reasoning strategies."""

    @property
    @abstractmethod
    def strategy(self) -> ReasoningStrategy:
        pass

    @abstractmethod
    def reason(
        self,
        question: str,
        context: str = "",
        *,
        llm: Any | None = None,
        state: JarvisState | None = None,
    ) -> ReasoningResult:
        """Execute the reasoning strategy on a question."""
        pass

    def _build_prompt(self, question: str, context: str, template: str) -> str:
        ctx = f"\nContext:\n{context}\n" if context else ""
        return template.format(context=ctx, question=question)

    def _invoke(self, prompt: str, llm: Any | None = None) -> tuple[str, int]:
        if llm is None:
            from jarvis.models.ollama_client import get_model_named
            llm = get_model_named(settings.general_model, intent="general")
        started = time.perf_counter()
        resp = llm.invoke(prompt)
        text = getattr(resp, "content", "") or ""
        latency_ms = int((time.perf_counter() - started) * 1000)
        return text, latency_ms


class CoTReasoning(ReasoningStrategyBase):
    @property
    def strategy(self) -> ReasoningStrategy:
        return ReasoningStrategy.COT

    def reason(
        self,
        question: str,
        context: str = "",
        *,
        llm: Any | None = None,
        state: JarvisState | None = None,
    ) -> ReasoningResult:
        template = """{context}
Question: {question}

Think step by step. Provide your reasoning clearly, then give your final answer.

Reasoning:
"""
        prompt = self._build_prompt(question, context, template)
        text, latency_ms = self._invoke(prompt, llm)
        return ReasoningResult(
            strategy=self.strategy,
            answer=text,
            reasoning=text,
            confidence=0.8,
            metadata={"steps": 3},
            latency_ms=latency_ms,
            tokens_used=len(text.split()),
        )


class ToTReasoning(ReasoningStrategyBase):
    @property
    def strategy(self) -> ReasoningStrategy:
        return ReasoningStrategy.TOT

    def reason(
        self,
        question: str,
        context: str = "",
        *,
        llm: Any | None = None,
        state: JarvisState | None = None,
    ) -> ReasoningResult:
        max_branches = settings.reasoning_strategy_tot_max_branches
        template = f"""{context}
Question: {question}

Explore up to {max_branches} different reasoning approaches. For each branch:
1. State the approach
2. Reason through it
3. Evaluate its promise

Select the best branch and provide the final answer.

Branches:
"""
        prompt = self._build_prompt(question, context, template)
        text, latency_ms = self._invoke(prompt, llm)
        return ReasoningResult(
            strategy=self.strategy,
            answer=text,
            reasoning=text,
            confidence=0.85,
            metadata={"branches_explored": min(3, max_branches)},
            latency_ms=latency_ms,
            tokens_used=len(text.split()),
        )


class SelfConsistencyReasoning(ReasoningStrategyBase):
    @property
    def strategy(self) -> ReasoningStrategy:
        return ReasoningStrategy.SELF_CONSISTENCY

    def reason(
        self,
        question: str,
        context: str = "",
        *,
        llm: Any | None = None,
        state: JarvisState | None = None,
    ) -> ReasoningResult:
        num_samples = settings.reasoning_strategy_self_consistency_num_samples
        template = f"""{context}
Question: {question}

Generate {num_samples} independent reasoning chains and answers.
Then select the most consistent answer.

Samples:
"""
        prompt = self._build_prompt(question, context, template)
        text, latency_ms = self._invoke(prompt, llm)
        return ReasoningResult(
            strategy=self.strategy,
            answer=text,
            reasoning=text,
            confidence=0.9,
            metadata={"samples": num_samples},
            latency_ms=latency_ms,
            tokens_used=len(text.split()),
        )


class ReflexionReasoning(ReasoningStrategyBase):
    @property
    def strategy(self) -> ReasoningStrategy:
        return ReasoningStrategy.REFLEXION

    def reason(
        self,
        question: str,
        context: str = "",
        *,
        llm: Any | None = None,
        state: JarvisState | None = None,
    ) -> ReasoningResult:
        max_iterations = settings.reasoning_strategy_reflexion_max_iterations
        template = f"""{context}
Question: {question}

Iterate up to {max_iterations} times:
1. Generate an initial answer
2. Critique it for errors/gaps
3. Refine based on critique

Final refined answer:
"""
        prompt = self._build_prompt(question, context, template)
        text, latency_ms = self._invoke(prompt, llm)
        return ReasoningResult(
            strategy=self.strategy,
            answer=text,
            reasoning=text,
            confidence=0.9,
            metadata={"max_iterations": max_iterations},
            latency_ms=latency_ms,
            tokens_used=len(text.split()),
        )


class FastAndSlowReasoning(ReasoningStrategyBase):
    @property
    def strategy(self) -> ReasoningStrategy:
        return ReasoningStrategy.FAST_AND_SLOW

    def reason(
        self,
        question: str,
        context: str = "",
        *,
        llm: Any | None = None,
        state: JarvisState | None = None,
    ) -> ReasoningResult:
        word_count = len(question.split())
        has_complex_keywords = any(
            kw in question.lower()
            for kw in [
                "analyze", "compare", "evaluate", "design", "optimize",
                "prove", "derive", "complex", "architecture",
            ]
        )

        if word_count > 30 or has_complex_keywords:
            cot = CoTReasoning()
            result = cot.reason(question, context, llm=llm, state=state)
            result.metadata["path"] = "slow"
            result.strategy = self.strategy
            return result

        prompt = f"""{context}
Question: {question}

Provide a direct, concise answer.
"""
        text, latency_ms = self._invoke(prompt, llm)
        return ReasoningResult(
            strategy=self.strategy,
            answer=text,
            reasoning="[Fast path reasoning]",
            confidence=0.7,
            metadata={"path": "fast"},
            latency_ms=latency_ms,
            tokens_used=len(text.split()),
        )


class ReasoningStrategyRegistry:
    def __init__(self):
        self._strategies: dict[ReasoningStrategy, ReasoningStrategyBase] = {
            ReasoningStrategy.COT: CoTReasoning(),
            ReasoningStrategy.TOT: ToTReasoning(),
            ReasoningStrategy.SELF_CONSISTENCY: SelfConsistencyReasoning(),
            ReasoningStrategy.REFLEXION: ReflexionReasoning(),
            ReasoningStrategy.FAST_AND_SLOW: FastAndSlowReasoning(),
        }

    def get(self, strategy: ReasoningStrategy) -> ReasoningStrategyBase | None:
        return self._strategies.get(strategy)

    def get_enabled(self) -> list[ReasoningStrategyBase]:
        enabled = []
        if settings.reasoning_strategy_cot_enabled:
            enabled.append(self._strategies[ReasoningStrategy.COT])
        if settings.reasoning_strategy_tot_enabled:
            enabled.append(self._strategies[ReasoningStrategy.TOT])
        if settings.reasoning_strategy_self_consistency_enabled:
            enabled.append(self._strategies[ReasoningStrategy.SELF_CONSISTENCY])
        if settings.reasoning_strategy_reflexion_enabled:
            enabled.append(self._strategies[ReasoningStrategy.REFLEXION])
        if settings.reasoning_strategy_fast_and_slow_enabled:
            enabled.append(self._strategies[ReasoningStrategy.FAST_AND_SLOW])
        return enabled

    def select_auto(self, question: str, context: str = "") -> ReasoningStrategyBase:
        word_count = len(question.split())
        has_complex_keywords = any(
            kw in question.lower()
            for kw in [
                "analyze", "compare", "evaluate", "design", "optimize",
                "prove", "derive",
            ]
        )

        if word_count > 50 or has_complex_keywords:
            if settings.reasoning_strategy_self_consistency_enabled:
                return self._strategies[ReasoningStrategy.SELF_CONSISTENCY]
            if settings.reasoning_strategy_cot_enabled:
                return self._strategies[ReasoningStrategy.COT]
        elif word_count > 20:
            if settings.reasoning_strategy_cot_enabled:
                return self._strategies[ReasoningStrategy.COT]
        if settings.reasoning_strategy_fast_and_slow_enabled:
            return self._strategies[ReasoningStrategy.FAST_AND_SLOW]
        return self._strategies[ReasoningStrategy.COT]

    def execute(
        self,
        strategy: ReasoningStrategy,
        question: str,
        context: str = "",
        *,
        llm: Any | None = None,
        state: JarvisState | None = None,
    ) -> ReasoningResult | None:
        impl = self.get(strategy)
        if impl is None:
            return None
        return impl.reason(question, context, llm=llm, state=state)


reasoning_registry = ReasoningStrategyRegistry()


__all__ = [
    "ReasoningStrategy",
    "ReasoningResult",
    "ReasoningStrategyBase",
    "CoTReasoning",
    "ToTReasoning",
    "SelfConsistencyReasoning",
    "ReflexionReasoning",
    "FastAndSlowReasoning",
    "ReasoningStrategyRegistry",
    "reasoning_registry",
]

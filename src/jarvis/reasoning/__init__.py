"""Reasoning strategy variations (Phase 13).

Provides multiple reasoning strategies for different types of problems:
- Chain-of-Thought (CoT): Step-by-step reasoning
- Tree-of-Thought (ToT): Branch exploration and evaluation
- Self-Consistency: Multiple samples, majority vote
- Reflexion: Generate, critique, refine
- Fast-and-Slow: Adaptive routing based on complexity
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jarvis.config.settings import settings


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
    def reason(self, question: str, context: str = "") -> ReasoningResult:
        """Execute the reasoning strategy on a question."""
        pass
    
    def _build_prompt(self, question: str, context: str, template: str) -> str:
        """Build a prompt with context and question."""
        ctx = f"\nContext:\n{context}\n" if context else ""
        return template.format(context=ctx, question=question)


class CoTReasoning(ReasoningStrategyBase):
    """Chain-of-Thought reasoning: step-by-step reasoning."""
    
    @property
    def strategy(self) -> ReasoningStrategy:
        return ReasoningStrategy.COT
    
    def reason(self, question: str, context: str = "") -> ReasoningResult:
        template = """{context}
Question: {question}

Think step by step. Provide your reasoning clearly, then give your final answer.

Reasoning:
"""
        prompt = self._build_prompt(question, context, template)
        # In a real implementation, this would call the LLM
        # For now, return a placeholder
        return ReasoningResult(
            strategy=self.strategy,
            answer="[CoT answer would be generated here]",
            reasoning="[Step-by-step reasoning would be generated here]",
            confidence=0.8,
            metadata={"steps": 3},
        )


class ToTReasoning(ReasoningStrategyBase):
    """Tree-of-Thought reasoning: explore multiple branches."""
    
    @property
    def strategy(self) -> ReasoningStrategy:
        return ReasoningStrategy.TOT
    
    def reason(self, question: str, context: str = "") -> ReasoningResult:
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
        return ReasoningResult(
            strategy=self.strategy,
            answer="[ToT answer would be generated here]",
            reasoning="[Branch exploration would be generated here]",
            confidence=0.85,
            metadata={"branches_explored": min(3, max_branches)},
        )


class SelfConsistencyReasoning(ReasoningStrategyBase):
    """Self-Consistency: generate multiple samples, take majority."""
    
    @property
    def strategy(self) -> ReasoningStrategy:
        return ReasoningStrategy.SELF_CONSISTENCY
    
    def reason(self, question: str, context: str = "") -> ReasoningResult:
        num_samples = settings.reasoning_strategy_self_consistency_num_samples
        template = f"""{context}
Question: {question}

Generate {num_samples} independent reasoning chains and answers.
Then select the most consistent answer.

Samples:
"""
        prompt = self._build_prompt(question, context, template)
        return ReasoningResult(
            strategy=self.strategy,
            answer="[Self-consistency answer would be generated here]",
            reasoning="[Multiple samples would be generated and compared]",
            confidence=0.9,
            metadata={"samples": num_samples},
        )


class ReflexionReasoning(ReasoningStrategyBase):
    """Reflexion: generate, critique, refine."""
    
    @property
    def strategy(self) -> ReasoningStrategy:
        return ReasoningStrategy.REFLEXION
    
    def reason(self, question: str, context: str = "") -> ReasoningResult:
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
        return ReasoningResult(
            strategy=self.strategy,
            answer="[Reflexion answer would be generated here]",
            reasoning="[Iterative refinement would be generated here]",
            confidence=0.9,
            metadata={"max_iterations": max_iterations},
        )


class FastAndSlowReasoning(ReasoningStrategyBase):
    """Fast-and-Slow: route simple questions to fast path, complex to slow."""
    
    @property
    def strategy(self) -> ReasoningStrategy:
        return ReasoningStrategy.FAST_AND_SLOW
    
    def reason(self, question: str, context: str = "") -> ReasoningResult:
        # Simple heuristic: short questions -> fast, long -> slow
        is_complex = len(question.split()) > 30 or any(
            kw in question.lower() for kw in 
            ["analyze", "compare", "evaluate", "design", "optimize", "prove", "derive"]
        )
        
        if is_complex:
            # Use CoT for complex
            cot = CoTReasoning()
            result = cot.reason(question, context)
            result.metadata["path"] = "slow"
            return result
        else:
            # Fast path: direct answer
            template = """{context}
Question: {question}

Provide a direct, concise answer.
"""
            prompt = self._build_prompt(question, context, template)
            return ReasoningResult(
                strategy=self.strategy,
                answer="[Fast path answer would be generated here]",
                reasoning="[Fast path reasoning]",
                confidence=0.7,
                metadata={"path": "fast"},
            )


class ReasoningStrategyRegistry:
    """Registry of available reasoning strategies."""
    
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
        """Get all enabled strategies based on settings."""
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
        """Automatically select the best strategy for a question."""
        # Simple heuristic for auto-selection
        word_count = len(question.split())
        has_complex_keywords = any(
            kw in question.lower() 
            for kw in ["analyze", "compare", "evaluate", "design", "optimize", "prove", "derive"]
        )
        
        if word_count > 50 or has_complex_keywords:
            # Complex question - use self-consistency for highest accuracy
            if settings.reasoning_strategy_self_consistency_enabled:
                return self._strategies[ReasoningStrategy.SELF_CONSISTENCY]
            if settings.reasoning_strategy_cot_enabled:
                return self._strategies[ReasoningStrategy.COT]
        elif word_count > 20:
            # Medium complexity - use CoT
            if settings.reasoning_strategy_cot_enabled:
                return self._strategies[ReasoningStrategy.COT]
        # Simple question - fast path
        if settings.reasoning_strategy_fast_and_slow_enabled:
            return self._strategies[ReasoningStrategy.FAST_AND_SLOW]
        # Fallback
        return self._strategies[ReasoningStrategy.COT]


# Global registry instance
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
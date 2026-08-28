"""Deep thinking mode for complex reasoning tasks (Phase 13).

Provides a structured approach to complex reasoning:
- Automatic detection of complex questions
- Step-by-step reasoning chain generation
- Sub-problem decomposition
- Answer synthesis and verification
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jarvis.config.settings import settings


@dataclass
class ReasoningStep:
    """A single step in the reasoning chain."""
    step_number: int
    description: str
    sub_problem: str
    analysis: str
    conclusion: str
    confidence: float  # 0.0 - 1.0
    citations: list[str]


@dataclass
class ReasoningChain:
    """Complete reasoning chain for a deep thinking response."""
    steps: list[ReasoningStep]
    sub_problems: list[str]
    final_synthesis: str
    total_confidence: float
    tokens_used: int


def should_trigger_deep_thinking(question: str, confidence: float = 0.5) -> bool:
    """Determine if deep thinking should be triggered for a question.
    
    Args:
        question: The user's question
        confidence: Heuristic confidence score (0.0 - 1.0) from router
        
    Returns:
        True if deep thinking should be used
    """
    if not settings.deep_thinking_enabled:
        return False
    
    if not settings.deep_thinking_auto_trigger:
        return False
    
    # Check confidence threshold
    if confidence < settings.deep_thinking_auto_trigger_confidence_threshold:
        return False
    
    # Heuristic: complex questions are longer, have certain keywords
    complex_keywords = {
        "analyze", "compare", "evaluate", "synthesize", "reason",
        "decompose", "break down", "step by step", "think deeply",
        "complex", "theoretical", "abstract", "architecture",
        "design", "optimize", "prove", "derive", "explain why",
        "trade-off", "tradeoff", "pros and cons", "implications"
    }
    
    question_lower = question.lower()
    keyword_count = sum(1 for kw in complex_keywords if kw in question_lower)
    
    # Also check length
    word_count = len(question.split())
    
    # Trigger if: high confidence from router OR multiple complex keywords OR very long question
    return (
        confidence >= settings.deep_thinking_auto_trigger_confidence_threshold or
        keyword_count >= 2 or
        word_count > 50
    )


def generate_reasoning_prompt(question: str, sub_problems: list[str] | None = None) -> str:
    """Generate a prompt for the model to produce a reasoning chain."""
    if sub_problems is None:
        sub_problems = []
    
    sub_problem_text = ""
    if sub_problems:
        sub_problem_text = "\n".join(
            f"{i+1}. {sp}" for i, sp in enumerate(sub_problems)
        )
        sub_problem_text = f"\nSub-problems to address:\n{sub_problem_text}\n"
    
    return f"""You are an expert reasoner. Think deeply and systematically about the following question.

Question: {question}
{sub_problem_text}
Please provide your reasoning as a structured chain of steps. For each step:
1. State the sub-problem being addressed
2. Provide your analysis
3. State your conclusion for this step
4. Rate your confidence (0.0-1.0)

Format your response as:
STEP 1:
Sub-problem: <sub-problem>
Analysis: <your analysis>
Conclusion: <your conclusion>
Confidence: <0.0-1.0>

STEP 2:
...

FINAL SYNTHESIS:
<Your final synthesized answer that integrates all steps>

TOTAL CONFIDENCE: <0.0-1.0>
"""


def parse_reasoning_response(response: str) -> dict:
    """Parse the model's reasoning response into structured data."""
    steps = []
    sub_problems = []
    final_synthesis = ""
    total_confidence = 0.5
    
    lines = response.strip().split('\n')
    current_step = None
    
    for line in lines:
        line = line.strip()
        if line.startswith("STEP "):
            if current_step:
                steps.append(current_step)
            # Parse step number
            try:
                step_num = int(line.split()[1].rstrip(':'))
            except (IndexError, ValueError):
                step_num = len(steps) + 1
            current_step = {
                "step_number": step_num,
                "description": "",
                "sub_problem": "",
                "analysis": "",
                "conclusion": "",
                "confidence": 0.5,
            }
        elif line.startswith("Sub-problem:") and current_step:
            current_step["sub_problem"] = line.replace("Sub-problem:", "").strip()
        elif line.startswith("Analysis:") and current_step:
            current_step["analysis"] = line.replace("Analysis:", "").strip()
        elif line.startswith("Conclusion:") and current_step:
            current_step["conclusion"] = line.replace("Conclusion:", "").strip()
        elif line.startswith("Confidence:") and current_step:
            try:
                current_step["confidence"] = float(line.replace("Confidence:", "").strip())
            except ValueError:
                current_step["confidence"] = 0.5
        elif line.startswith("FINAL SYNTHESIS:"):
            if current_step:
                steps.append(current_step)
            current_step = None
        elif line.startswith("TOTAL CONFIDENCE:"):
            try:
                total_confidence = float(line.replace("TOTAL CONFIDENCE:", "").strip())
            except ValueError:
                total_confidence = 0.5
        elif current_step and current_step.get("description") == "" and line:
            current_step["description"] = line
    
    if current_step:
        steps.append(current_step)
    
    # Extract sub-problems from steps
    for step in steps:
        if step.get("sub_problem"):
            sub_problems.append(step["sub_problem"])
    
    # Extract final synthesis (everything after FINAL SYNTHESIS:)
    synthesis_start = response.find("FINAL SYNTHESIS:")
    if synthesis_start >= 0:
        final_synthesis = response[synthesis_start + len("FINAL SYNTHESIS:"):].strip()
    else:
        # Fallback: use last step's conclusion or combine all conclusions
        final_synthesis = " ".join(s.get("conclusion", "") for s in steps if s.get("conclusion"))
    
    return {
        "steps": steps,
        "sub_problems": sub_problems,
        "final_synthesis": final_synthesis,
        "total_confidence": total_confidence,
    }


__all__ = [
    "ReasoningStep",
    "ReasoningChain",
    "should_trigger_deep_thinking",
    "generate_reasoning_prompt",
    "parse_reasoning_response",
]
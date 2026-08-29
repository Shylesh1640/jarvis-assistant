# Reasoning Strategies

Phase 13 provides multiple reasoning strategies for different types of problems. Strategies are selected automatically or manually per request.

## Available Strategies

### Chain-of-Thought (CoT)

- Step-by-step reasoning with explicit intermediate steps.
- Suitable for math, logic, and structured problems.
- Enabled by default.

### Tree-of-Thought (ToT)

- Explores multiple reasoning branches.
- Evaluates each branch and selects the best.
- Suitable for open-ended problems with multiple approaches.

### Self-Consistency

- Generates multiple independent reasoning chains.
- Compares results for consistency.
- Selects the most consistent answer.
- Suitable for factual questions with definitive answers.

### Reflexion

- Generates an initial answer.
- Self-critiques and identifies errors.
- Refines the answer based on critique.
- Suitable for complex tasks requiring iteration.

### Fast-and-Slow

- Fast path: direct answer for simple questions.
- Slow path: deep reasoning for complex questions.
- Automatic routing based on question complexity.

## Configuration

Environment variables:

```env
REASONING_STRATEGY_DEFAULT=auto
REASONING_STRATEGY_COT_ENABLED=true
REASONING_STRATEGY_TOT_ENABLED=true
REASONING_STRATEGY_TOT_MAX_BRANCHES=3
REASONING_STRATEGY_SELF_CONSISTENCY_ENABLED=true
REASONING_STRATEGY_SELF_CONSISTENCY_NUM_SAMPLES=3
REASONING_STRATEGY_REFLEXION_ENABLED=true
REASONING_STRATEGY_REFLEXION_MAX_ITERATIONS=2
REASONING_STRATEGY_FAST_AND_SLOW_ENABLED=true
```

## Strategy Selection

### Automatic

When `REASONING_STRATEGY_DEFAULT=auto`, the system selects a strategy based on:

- Question length (>50 words → Self-Consistency or CoT)
- Complex keywords (analyze, compare, evaluate, design, optimize, prove, derive)
- Simple questions → Fast-and-Slow fast path

### Manual Override

Users can select a strategy via:

- Streamlit UI "Reasoning strategy" dropdown
- `reasoning_strategy` field in the `/chat` request payload

## Strategy Metadata

Each response includes strategy metadata:

```json
{
  "reasoning_strategy": "cot",
  "reasoning_strategy_details": {
    "cot_steps": 3,
    "tot_branches": 2,
    "self_consistency_samples": 3,
    "reflexion_iterations": 1
  }
}
```

## API

- `GET /settings/reasoning-strategies` — return current settings
- `PATCH /settings/reasoning-strategies` — update runtime settings
- `POST /chat` — supports `reasoning_strategy` flag

## Security and Privacy

- All reasoning strategies run locally using the configured local model.
- Strategy prompts are designed to avoid unnecessary context exposure.
- No reasoning content is sent to cloud providers.

## Rollback

To use a single fixed strategy:

```env
REASONING_STRATEGY_DEFAULT=cot
REASONING_STRATEGY_TOT_ENABLED=false
REASONING_STRATEGY_SELF_CONSISTENCY_ENABLED=false
REASONING_STRATEGY_REFLEXION_ENABLED=false
REASONING_STRATEGY_FAST_AND_SLOW_ENABLED=false
```

Restart the backend to apply.

# Deep Thinking Mode

Phase 13 introduces an optional deep thinking mode for complex reasoning tasks.

## Overview

When enabled, deep thinking generates a structured reasoning chain before producing the final answer. The chain breaks the problem into sub-problems, analyzes each, and synthesizes a final answer.

## Configuration

Environment variables:

```env
DEEP_THINKING_ENABLED=true
DEEP_THINKING_AUTO_TRIGGER=true
DEEP_THINKING_AUTO_TRIGGER_CONFIDENCE_THRESHOLD=0.7
DEEP_THINKING_MAX_REASONING_STEPS=5
DEEP_THINKING_MAX_TOKENS_FACTOR=3.0
DEEP_THINKING_SHOW_REASONING_CHAIN=false
```

## Automatic Trigger

Deep thinking is automatically triggered when:

- The question contains multiple complex keywords (analyze, compare, evaluate, decompose, etc.)
- The question is very long (>50 words)
- The router confidence score is above the configured threshold

## Manual Override

Users can request deep thinking by:

- Including phrases like "think deeply" or "step by step" in their question
- Toggling "Deep thinking" in the Streamlit UI
- Passing `deep_thinking: true` in the `/chat` request payload

## Reasoning Chain Visibility

When `DEEP_THINKING_SHOW_REASONING_CHAIN=true` (or toggled per-request), the reasoning chain is included in the response metadata and displayed in the UI.

## API

- `GET /settings/deep-thinking` — return current settings
- `PATCH /settings/deep-thinking` — update runtime settings
- `POST /chat` — supports `deep_thinking` and `show_reasoning_chain` flags

## Response Metadata

```json
{
  "deep_thinking_used": true,
  "reasoning_strategy": "cot",
  "reasoning_steps": 3,
  "reasoning_chain_visible": false,
  "tokens_used_reasoning": 500,
  "tokens_used_answer": 200,
  "total_tokens": 700,
  "latency_ms_reasoning": 5000,
  "latency_ms_answer": 2000,
  "total_latency_ms": 7000
}
```

## Security and Privacy

- Deep thinking runs entirely locally using the configured local model.
- No prompts or reasoning chains are sent to cloud services unless the complex branch is also triggered.
- Reasoning chain content is redacted in logs and reports.
- Token counts are estimated from word counts; no external tokenizer is required.

## Rollback

To disable deep thinking:

```env
DEEP_THINKING_ENABLED=false
```

Restart the backend to apply. Existing sessions and conversations are unaffected.

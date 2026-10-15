# A/B Testing for Reasoning Strategies

Phase 13 extends the evaluation framework with A/B testing for reasoning strategies.

## Overview

A/B testing enables data-driven selection of reasoning strategies by splitting traffic between variants and collecting metrics.

## Supported Tests

- **Reasoning strategy comparison**: CoT vs. ToT vs. Self-Consistency vs. Reflexion vs. Fast-and-Slow.
- **Deep thinking vs. standard mode**: Compare deep thinking on/off.

## Configuration

Environment variables:

```env
AB_TESTING_REASONING_ENABLED=false
AB_TESTING_MIN_SAMPLES_PER_VARIANT=50
AB_TESTING_SIGNIFICANCE_THRESHOLD=0.05
```

## Traffic Splitting

- Requests are routed to variants based on a hash of the session ID.
- Consistent routing ensures the same user always sees the same variant.
- Stratified splitting supports task-type-specific routing.

## Metrics Collection

Per request, the system collects:

- Accuracy, relevance, satisfaction for each variant.
- Efficiency metrics: tokens used, latency.
- User feedback.

## Statistical Analysis

- Two-sample z-test approximation for significance.
- Identifies the winning variant.
- Generates recommendations based on confidence intervals.

## CLI

```powershell
uv run jarvis-ab-test create-reasoning --name "cot_vs_tot" --variant-a "cot" --variant-b "tot"
uv run jarvis-ab-test status --name "cot_vs_tot"
uv run jarvis-ab-test report --name "cot_vs_tot"
uv run jarvis-ab-test promote --name "cot_vs_tot" --variant "A|B"
```

## API

- `GET /ab-testing/active` — list active A/B tests
- `GET /ab-testing/assignment/{session_id}` — get variant assignment for a session
- `POST /ab-testing/record-metric` — record metrics for a request
- `GET /ab-testing/report/{name}` — generate A/B test report
- `POST /ab-testing/create-reasoning` — create a reasoning strategy A/B test
- `POST /ab-testing/promote` — promote a winning variant

## Security and Privacy

- A/B test data never includes full prompts or responses.
- Session IDs are hashed for routing; raw IDs are not stored in test data.
- All outputs are redacted.
- File storage uses append-only JSONL under `./reports/ab_testing/`.

## Rollback

To disable A/B testing:

```env
AB_TESTING_REASONING_ENABLED=false
```

Existing test data files are not deleted automatically. Use the cleanup commands if needed.

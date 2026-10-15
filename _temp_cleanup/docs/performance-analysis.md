# Performance Analysis Framework

Phase 13 provides a performance analysis framework for deep thinking and reasoning variations.

## Metrics

### Accuracy
- Correctness of the final answer.
- Comparison with expected answer (if available).
- Human evaluation score (optional).

### Reasoning Quality
- Logical consistency of the reasoning chain.
- Completeness of reasoning.
- Relevance of reasoning steps.

### Efficiency
- Tokens used for reasoning vs. answer.
- Latency for reasoning vs. answer.
- Cost (if cloud is used).

### User Satisfaction
- Thumbs up/down feedback.
- Optional text feedback.
- Perceived helpfulness.

## Configuration

Environment variables:

```env
PERFORMANCE_ANALYSIS_ENABLED=true
PERFORMANCE_ANALYSIS_RETENTION_DAYS=90
PERFORMANCE_ANALYSIS_MIN_SAMPLES=10
```

## CLI

```powershell
uv run jarvis-analyze-performance
uv run jarvis-analyze-performance --strategy cot
uv run jarvis-analyze-performance --strategy tot
uv run jarvis-analyze-performance --compare-strategies cot,tot,self_consistency
uv run jarvis-analyze-performance --output reports/performance/report.json
uv run jarvis-analyze-performance --markdown reports/performance/report.md
uv run jarvis-analyze-performance --csv reports/performance/report.csv
uv run jarvis-analyze-performance --days 30
uv run jarvis-analyze-performance --cleanup
```

## API

- `GET /performance/summary` — full aggregated report
- `GET /performance/by-strategy?strategy=cot&days=30` — metrics grouped by strategy
- `GET /performance/by-task-type?task_type=general` — metrics grouped by task type
- `GET /performance/trends` — performance trends over time

## Comparative Analysis

The framework compares:

- Different reasoning strategies on the same task types.
- Deep thinking vs. standard mode.
- Identifies the best strategy per task type based on accuracy, reasoning quality, efficiency, and user satisfaction.

## Performance Reports

Reports include:

- Aggregated metrics by strategy and task type.
- Strategy comparisons with statistical significance.
- Deep thinking vs. standard mode delta.
- Export to JSON, Markdown, or CSV.

## Security and Privacy

- Performance data never includes full prompts, responses, or secrets.
- All outputs are redacted using the same secret patterns as the benchmark module.
- File storage uses append-only JSONL under `./reports/performance/`.
- No data is sent to external services.

## Rollback

To disable performance analysis:

```env
PERFORMANCE_ANALYSIS_ENABLED=false
```

Existing data files under `./reports/performance/` are not deleted automatically.

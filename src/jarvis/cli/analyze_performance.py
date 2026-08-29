"""CLI entry point: ``uv run jarvis-analyze-performance``.

Analyses performance of deep thinking and reasoning strategy variations.
Supports filtering by strategy/task-type, strategy comparison, and export
to JSON / Markdown / CSV.

Usage::

    uv run jarvis-analyze-performance
    uv run jarvis-analyze-performance --strategy deep_thinking
    uv run jarvis-analyze-performance --compare-strategies cot,tot,self_consistency
    uv run jarvis-analyze-performance --output reports/performance/report.json
    uv run jarvis-analyze-performance --markdown reports/performance/report.md
    uv run jarvis-analyze-performance --days 30
    uv run jarvis-analyze-performance --cleanup
"""
from __future__ import annotations

import sys

from jarvis.performance.analysis import main


if __name__ == "__main__":
    sys.exit(main())

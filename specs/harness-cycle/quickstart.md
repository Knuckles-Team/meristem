# MERISTEM-HARNESS-CYCLE quickstart

Run the proof for `MERISTEM-HARNESS-CYCLE-R001.4` from the repository root with the project environment.

1. `uv run pytest tests/harness/test_harness_cycle.py` runs one cycle tick with an injected `decide` and asserts one `Decide` call and no local ranking.
2. `uv run pytest tests/graph/test_render_receipt_consumer.py` runs the receipt consumer the tick relies on.

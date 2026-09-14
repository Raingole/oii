# Testing report

## Verified locally (2026-09-14)

- `python -m unittest discover -s tests -p "test_*.py" -v`: 22/22 passed.
- `python -m compileall -q cognitive_core controller contracts core qq tests`: passed.
- `python scripts/cognitive_smoke.py`: passed with mock NapCat and mock ESP32; duplicate event was ignored.
- Regression coverage includes persisted attempts, lease recovery, restart retry limit, ESP32 output-gated fallback, ToolCatalog, approval payload binding, QQ/SMS existing tests.

## Not verified

`python app.py` stops before startup because this interpreter lacks the original runtime dependency `numpy`. Real NapCat, QQ Official, ESP32 hardware, Tencent Memory, network MCP and Docker were not verified. `pytest` is not installed.

发现疑似凭据，已脱敏处理。

# M0 test baseline

How to run: `python -m pytest -q` from the repository root (Python 3.14+; `pip install -e ".[dev]"`).

## Results (12 Nov 2026, branch `main`: M0 + M1, without M2)

| Where | Order | Passed | Failed | Skipped | Notes |
|---|---|---|---|---|---|
| Linux (cloud container), Python 3.14.6 | normal | 1118 | 0 | 37 | Windows-only tests skip here |
| Linux (cloud container), Python 3.14.6 | reversed | 1118 | 0 | 37 | every test also passes when the whole suite runs backwards |
| Native Windows (GitHub `windows-latest`), Python 3.14 and 3.15 | normal | see the GitHub Actions "Tests" run for this commit | | | the only native Windows evidence; not the owner's laptop |

## Test isolation (what M0 fixed)
- `quick.WEATHER` (a module global) is reset for every test (`tests/conftest.py`).
- `tests/test_v19_android.py` replaced `time.sleep` for the whole test run (`an.time` *is* the `time` module) and
  never put it back. Every later test that waits with `time.sleep` silently stopped waiting; run backwards, the
  voice-training test failed. Now patched with `monkeypatch`, and an automatic guard in `tests/conftest.py` fails any
  test that leaves `time.sleep` replaced.
- The reversed run above is the check: tests don't depend on running in a particular order.

## Suites by milestone
- M0: `tests/test_v3_m0_baseline.py` (docs present, every imported package declared, retry worker shuts down).
- M1: `tests/test_v3_paths_security.py`, `tests/test_v3_atomic_fs.py`, `tests/test_v3_terminal.py`,
  `tests/test_v3_services_integration.py`, `tests/test_v3_m1_findings.py`, `tests/test_v3_m0_m1_remediation.py`,
  `tests/test_v3_windows_native.py` (Windows-only parts skip elsewhere).

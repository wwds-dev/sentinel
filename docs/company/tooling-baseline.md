# Tooling baseline (Sentinel)

Measured 2026-10-12 by `/harden`. Report-only; nothing was committed, staged or activated.

## Added
- `.github/workflows/security.yml` (gitleaks on push/PR, weekly; `pip install pip-audit && pip-audit -r requirements.txt`, non-blocking)
- `.github/dependabot.yml` (pip, github-actions)
- `.gitleaks.toml` (template as is)
- `ruff.toml` (no ruff config existed; `.ruff_cache` alone does not count)
- `.githooks/pre-commit` (executable, NOT activated)
- `docs/company/tooling/baseline.json` (must be committed with the above, or the CI ratchet fails)
- Existing `.github/workflows/tests.yml` left untouched (pytest + `--cov` on ubuntu and macos). No `quality.yml` added.

## Baseline
| Check | Command | Result |
|---|---|---|
| Lint | `uvx ruff check --quiet --output-format=concise --exit-zero .` | 222 findings (E402 65, F401 63, B904 24, E741 17, E701 15, E702 13, rest <=5 each); ratchet baseline 222 |
| Secrets | `gitleaks detect --no-git --redact -c .gitleaks.toml` | 20 findings, see below |
| Dependencies | `uvx pip-audit -r requirements.txt` | 42 known vulnerabilities in 7 packages (incl. pillow 12.2.0, urllib3 2.6.3, paramiko 3.5.1) |
| Test collection | `.venv/bin/python -m pytest --collect-only -q` | 2208 tests, 0 collection errors, ~15s (segno imports in the venv now; app-context's "5 errors" is stale) |
| Coverage | not run | UNVERIFIED: 2208 tests, full run is not known to finish in ~60s |

Coverage floor: UNVERIFIED (measure with `python -m coverage run -m pytest`, floor = measured minus 1).

### gitleaks findings (path and rule only; values not shown)
- `.env` (gitignored, never committed): anthropic-api-key 1, openai-api-key 1, generic-api-key 2
- `data/cache/wmn-data.json`: generic-api-key 2
- `services/database.py`: generic-api-key 2 (plus the same in two `__pycache__/database.*.pyc`)
- `tests/test_contract_tunnel_chat.py` 2, `tests/test_price_resolution.py` 1, `tests/test_vpn_execution.py` 2: generic-api-key (likely fixtures; owner to confirm)
- `dist.noindex/...google/genai/pagers.py` x2: generic-api-key (vendored build output)
- `tests/__pycache__/test_price_resolution.*.pyc`: generic-api-key 1

Because `--no-git` scans the working tree including ignored files, most of these are not in git history. A CI run (`detect` over git history) may differ. Existing `.gitleaksignore` (fingerprints) is not read by this baseline and may cover some.

## UNVERIFIED
- Coverage value and floor.
- Whether CI's gitleaks run is clean on history.
- `agents/bug_spray` is an uninitialised submodule here (`-` in `git submodule status`); its files were not linted or scanned beyond what exists on disk (0 ruff findings in it); no files were added inside it.

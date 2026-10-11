# Spec: audit tier-1 fixes (2026-10-12)

Source: security audit P1-1; code-review P2-7 and P2-8 (HQ `audits/sentinel/`).

## 1. API keys stored world-readable (security P1-1)
Problem: `ui/dialogs.py` chmods the `.env` to 0600 only when Save Key creates it. Both the checkout `.env` and the Application Support one are 0644, so other local users and processes can read paid API keys.
Fix:
- `services/runtime_paths.write_private_text`: temp file in the same directory created 0600, then `os.replace`. Save Key uses it, so every write ends 0600.
- `tighten_private_file` / `tighten_env_files`: at startup, before `load_dotenv`, chmod an existing checkout, Application Support and `user_data_base()` `.env` to 0600.
Success measure: tests in tmp dirs show a new file is 0600, a pre-existing 0644 file is 0600 after a write and after `tighten_*`, a failed write keeps the old file and leaves no temp file. The real `.env` is never touched (the session fixture in `tests/conftest.py` hashes it).

## 2. Run-nmap tooltip overstates (code-review P2-7)
Problem: the tooltip says an empty field "builds and starts a default scan ... immediately"; the code needs a Program entry and a Yes confirmation (default No).
Fix: reword `ui/tooltips.py` `bug_bounty.nmap_run_btn`. Success: a test pins "Program", "confirmation", "default No" and the absence of "immediately".

## 3. "Scope confirmed" activity line (code-review P2-8)
Problem: `ui/panels/osint.py` logs "Scope confirmed" after only a target-format check.
Fix: say "Target format accepted". Success: a test pins the new text and the absence of "Scope confirmed".

## Non-goals
VPN, kill switch and privileged code; the root-config copy; consent/cost gate; gitleaks fingerprints; CI files; `data/` directory modes; moving keys to Keychain; app-context wording (not in this repo).

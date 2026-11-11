# Radar note 2026-10-14: feature, never force-kill a worker thread

Scope: replace `QThread.terminate()` with cooperative cancel + detach; AST test banning it.
Stack read: `docs/company/app-context.md` (PySide6 unpinned, venv 6.11.2), `requirements.txt`,
playbooks `engineer`, `architect`, `qa-engineer` (current, 2026-10-14). Two lookups.

| Finding | Source (URL, date read) | Impact | Recommendation | Approval needed |
|---|---|---|---|---|
| Qt documents `terminate()` as "dangerous and its use is discouraged": the thread can stop anywhere, without releasing mutexes. In Python that includes the GIL, which is the SONAR deadlock. | https://doc.qt.io/qt-6/qthread.html, 2026-10-14 | P1 (confirms the change) | adopt: ban it, as planned | none (the spec already covers it) |
| `requestInterruption()` / `isInterruptionRequested()` is Qt's built-in cooperative stop flag. It is advisory, and the worker must check it. | same | P3 | watch: the workers already have `cancel()`; use the Qt flag only if a worker has no cancel of its own | none |
| Deleting a running `QThread` crashes the program. The fix is to keep a reference until `finished` fires, then `deleteLater` and `wait()`. "Detach" must therefore mean parking the object, not dropping it. | same | P1 (a design constraint for Architect) | adopt: record it in the architecture section | Architect gate |
| Since Qt 6.3, threads made with `QThread.create()` can be deleted while running, but deleting one blocks until it finishes. That would bring back a quit hang. | same | P3 | skip | none |
| PySide6 6.12 release notes were not found in the search. The 6.11.1 notes mention no QThread change. | https://doc.qt.io/qtforpython-6/release_notes/pyside6_release_notes.html (search result only, not read) | P3 | watch: tracked by the engineer playbook's Qt 6.12 rollout rule | none |

## Checked, nothing new
- Ruff's `banned-api` (TID251) bans imports and module members. It cannot see a method called on
  an instance (`worker.terminate()`), so the AST test stays the right gate. (From known Ruff
  behaviour; not re-fetched.)
- No tool, library or connector would fit this change better than the plan. Nothing to install.

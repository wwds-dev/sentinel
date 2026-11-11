<!-- Owner: enable GitHub's private vulnerability reporting for wwds-dev/sentinel (Settings > Code security > Private vulnerability reporting); the form linked below does not work until then. Delete this comment afterwards. -->

# Security

Sentinel is free, open-source software maintained by one person. It handles API keys for many services, runs privileged network changes after your approval, connects to remote servers over SSH/SFTP and processes untrusted web content,
so security reports are welcome.

## Report privately

Use GitHub's private report form: **Security > Report a vulnerability** on
<https://github.com/wwds-dev/sentinel/security/advisories/new>. Please do not open a public issue for a vulnerability.

Include the version beside **SENTINEL** in the app (for example `v2.002`), what you did, and what happened. Leave out real keys, tokens and personal data:
describe them instead.

## What to expect

- **Acknowledgement within 7 days.**
- **A first assessment within 14 days**, and a fix or a written reason within 90 days.
- No bounty, and no service-level promise beyond this: one person keeps this project, in
  their own time. If a deadline slips, the report gets a reply saying so.
- Credit in the CHANGELOG if you want it.

## Supported versions

Only the latest commit on `main` is supported. There are no binary releases; Sentinel is
distributed as source.

## Scope

In scope: this repository, including all eight agents and the Bug Spray submodule (`agents/bug_spray`, its own repository `wwds-dev/bug_spray`: report it here).
Out of scope: vulnerabilities in Qt, PySide6, Python, WireGuard, OpenVPN, Tor, Ollama, LM Studio or the data providers themselves (report those upstream); problems that need an attacker who already controls your Mac; and Sentinel's own features doing what they are documented to do (OSINT lookups, the MAC changer, proxy chains).

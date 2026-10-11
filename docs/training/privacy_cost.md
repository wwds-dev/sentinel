# Privacy, providers and cost

**Goal:** make an informed routing choice before data leaves the device. **Time:** 10 minutes.

## Local and cloud routes

Ollama runs a model on this Mac. It avoids per-request API charges and keeps the
prompt local, but uses CPU, memory and battery and may be slower or less capable.
A cloud provider receives the request over the internet, may retain or process
it under its own terms, and can charge the associated account.

An amber provider/model control means “potentially paid cloud route is
selected”; in the open dropdown, the hover text on each cloud entry says the
same thing. Neither means the provider has credit, the model is available, or
that the final price is known. In Chat's default Local only mode an amber
selection is not used: the request runs on Ollama until you choose Hybrid
allowed or Cloud only and tick the provider.

## Before approving cloud use

1. Identify exactly what text, target data or results will be sent.
2. Remove secrets and information unrelated to the task.
3. Check the provider/model and estimated cost.
4. Confirm that the account and organisational policy permit the disclosure.
5. Prefer local processing for credentials, private case data and file paths.

Provider permission is separate from an API key. A key proves the app can
authenticate; permission records whether Sentinel is allowed to use it.

## Sentry's AI read

Sentry works locally: its read-only commands, its baseline and its findings log stay
on this Mac. Only the optional **Explain findings with AI** box sends anything, it is
off by default, and it sends only when a pass has findings: each finding's title and
evidence (LAN IPs, MAC addresses, interface names, process names and IDs, ports and
remote addresses) and the counts of devices, listeners and connections. Sentry's
default provider is a paid cloud one; choose an Ollama model to keep the read on
this Mac. The request and the reply are saved in Saved Chats.

## Budgets and records

Session, daily and per-agent caps are Sentinel guardrails based on configured
prices and reported usage. They do not replace provider billing controls.
Review Cost history against the provider account when accuracy matters. Run log
shows failed and cancelled operations as well as successes.

## Completion check

Explain why “API ready,” “permission enabled,” “within budget” and “provider
account funded” are four different conditions. Say what Sentry sends, and when, if
its AI box is ticked.


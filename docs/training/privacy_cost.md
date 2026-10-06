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
that the final price is known.

## Before approving cloud use

1. Identify exactly what text, target data or results will be sent.
2. Remove secrets and information unrelated to the task.
3. Check the provider/model and estimated cost.
4. Confirm that the account and organisational policy permit the disclosure.
5. Prefer local processing for credentials, private case data and file paths.

Provider permission is separate from an API key. A key proves the app can
authenticate; permission records whether Sentinel is allowed to use it.

## Budgets and records

Session, daily and per-agent caps are Sentinel guardrails based on configured
prices and reported usage. They do not replace provider billing controls.
Review Cost history against the provider account when accuracy matters. Run log
shows failed and cancelled operations as well as successes.

## Completion check

Explain why “API ready,” “permission enabled,” “within budget” and “provider
account funded” are four different conditions.


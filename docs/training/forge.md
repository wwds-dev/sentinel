# Forge — create reviewed agent scaffolds

**Goal:** turn an agent idea into a safe, reviewable specification. **Time:** 15 minutes.

![Forge workspace](docs/training/images/forge.png)

Forge is different from Chat: Chat helps think and answer; Forge creates a
concrete agent specification and, only after approval, a source-code scaffold.
The scaffold is a small Python file holding the agent's name, description and
system prompt. It is a starting point, not a working agent. Forge does not
write reports or run tools.

## Describe the idea

State the agent's purpose, intended users, inputs, outputs, allowed providers,
allowed tools, budget needs and actions that require approval. Keep one agent
focused on one responsibility. **Analyze Idea** asks the selected model for a
structured JSON specification.

The specification has fields for name, label, description, providers, tools,
budget, approval and system prompt, but none for users, inputs or outputs. Those
appear only if the model works them into the description or the system prompt,
so check that each constraint you stated landed somewhere.

Analyze is an ordinary model request. Pick the provider and model in the run bar
(or press **Auto-route**); a cloud model needs its API permission and key, and
the usual budget and cost checks apply. The idea and the model's reply are saved
in Saved Chats even if you later reject the specification.

## Review before creation

The specification appears as cards: **Agent overview** (internal name, label,
description), **Access and safeguards** (providers, tools, budget, approval flag),
**System instructions** and, if the model gave one, **Design reasoning**. A
**Raw response** line below them shows the model's whole reply. The cards and the
raw reply are shown as plain text, exactly as written.

Forge checks the specification as soon as it arrives. If it cannot be created
(a reserved or duplicate name or label, an unsupported provider, an unknown tool,
a field that is too long, an invalid budget), a **Cannot be created
as written** card gives the reason and Approve stays disabled. These checks cover
shape, names and limits only; they do not tell you whether the specification is wise.

Reject vague permissions, open-ended execution, unnecessary cloud access or a
prompt that claims capabilities the code will not have. Read the Providers line
with care: the prompt Forge gives the model lists all seven providers as an
example, and a model that copies it grants the agent every one of them.

The specification cannot be edited in the panel. To change it, adjust the idea
and run Analyze Idea again (a pending specification is discarded without asking).
**Reject / Clear Spec** discards the draft and keeps your idea text.

## Approve and what it writes

**Approve & Create Agent** asks you to confirm; read the dialog before you answer.
On Yes, Forge writes exactly three things, all or nothing: a Python scaffold
`agents/<name>_agent.py`, one inactive row in the agents registry and one
inactive row in the tools registry. The tools row carries the same providers,
budget and approval flag you reviewed. If creation fails, the message says why
and the specification stays on screen.

It does not make the agent a built-in sidebar item or dynamically load it, and
the generated prompt cannot be used from Chat, whose tool list is fixed. The two
rows appear in Settings → Agents and Settings → Tools among the built-ins, with
no marker; ticking them does not make anything run. A developer must inspect the
files, add a suitable panel, test permissions and deliberately integrate it.
Restarting alone is not a safety review.

The file lands next to the code only when Sentinel runs from source. The packaged
app writes it under `~/Library/Application Support/Sentinel/agents` and the
portable build under `Sentinel Data/agents` on the drive; the dialog shows only
`agents/<name>_agent.py`.

## Best practices

- Prefer narrow tools with explicit inputs and structured outputs.
- Require approval for external writes, spending and security-sensitive work.
- Keep secrets out of the idea, system prompts and generated files. Forge does not
  check for them; the idea goes to the provider you chose and is saved with the reply.
- Test failure, cancellation and disabled-permission paths.
- Do not approve a spec you cannot explain.

## Exercise

Draft a read-only log-summary agent that runs on Ollama only. Confirm that its
Providers line lists only `ollama`, that its Tools line names nothing that writes
files or reaches the network, and that its system prompt claims neither, before
rejecting the practice draft. Rejecting writes no scaffold and no registry rows;
the analysis itself stays in Saved Chats.


# Controls and settings

**Goal:** understand Sentinel's shared controls and configuration. **Time:** 15 minutes.

## Request controls

| Control | Meaning |
|---|---|
| Provider | Where the model runs. Ollama is local; other choices are cloud services. |
| Model | The specific model used by the selected provider. Availability depends on installation, key and provider account. |
| Auto-route | Picks a provider and model for the current input and selects them; review the result before running. Only Ollama and the providers ticked under Options → Paid provider access are candidates, and in Local only mode only Ollama is. It never changes the agent or the Tool. |
| Main action | Send, Investigate, Analyse, Ask Advisor or another agent-specific operation. |
| Stop | Requests cancellation of the active worker. Partial results may remain. In Chat it appears beside Run only while a request is running, and Esc does the same. |
| Best fit | A **BEST FIT** badge on one entry per dropdown is Sentinel's recommendation for this agent: the cheapest model rated good enough for this agent's kind of work (see [API keys and new models](api_keys.md)). Hover for the reason, including the ratings and prices compared. It is advice; the entry you pick is the one that runs, and the badge moving never changes your selection. |
| NEW | A grey **NEW** badge marks a model a provider released since Sentinel last looked. It disappears once you update or dismiss it in **Model Updates**. |
| Paid marker | The control turns amber while a route that may charge through a cloud API is selected, and cloud entries say so on hover. It is a warning, not a price quote. |

## Menus and guidance

**Agent guide** documents the selected agent. **Tips** controls hover help.
**Inspector** shows live operational information. The **•••** menu opens the
Learning Centre, app documentation, model guide, Cost history, Run log and
Settings.

Chat's **Options** menu holds **Execution mode** (Local only, Hybrid allowed,
Cloud only), **Paid provider access** (which cloud providers Chat may use) and
**Routing priority**, which sets how much quality Auto-route may trade for
price:

| Routing priority | What wins |
|---|---|
| Balanced | The cheapest model rated within 20 points of the best for the kind of work. The default. |
| Cost first | The cheapest within 50 points. |
| Quality first | The best-rated model, whatever it costs. |
| Speed first | The same as Balanced whenever any candidate model has a rating (the shipped snapshot normally supplies them); Sentinel's own scoring, weighted towards fast models, applies only when no candidate is rated. |
| Privacy first | Sentinel's own scoring, weighted towards local models. |

The priority also decides every agent's BEST FIT badge.

Execution mode starts as Local only each time Sentinel launches, with every paid
provider unticked. In Local only mode a cloud provider chosen in the run bar is
replaced by Ollama; Hybrid allowed and Cloud only require the provider to be
ticked under Paid provider access.

## Settings — General

- **EUR/USD rate** converts provider USD pricing into displayed euro estimates.
- **Default session budget** limits accumulated spend until the app/session is reset.
- **Default daily budget** limits recorded spending for the day.
- **Theme** chooses Green (Matrix), Red or Blue (Cyberpunk), the same choice as
  the three dots beside SENTINEL. The window repaints as you change it so you
  can judge by looking; **Cancel** restores the theme you opened with. Status
  colours keep their meaning in every theme.

Budgets are guardrails, not bank controls. Provider-side usage can differ from
estimates, and cancelled requests may still incur a charge.

## Settings — Agents

Enable or disable registered agents and set an optional budget cap for each.
A blank cap means no agent-specific limit; global session and daily limits
still apply. Disabling an agent prevents authorised runs but does not erase its
history or files.

## Settings — Tools

Enable or disable registered tools. Tools supply task-specific instructions,
such as Writing or Coding inside Chat. The prompt preview helps identify the
tool but is not an editor in this screen. Changes to the enabled list reach
Chat's Tool selector after a restart, and Chat accepts only its five built-in
Tools.

## Settings — Pricing

Pricing values are USD per one million input, cached-input and output tokens.
Update them when provider prices change. Cached input can be cheaper when a
provider reuses recent context. Incorrect values produce incorrect estimates,
not changes to the provider's invoice.

A model is priced from its own row; a dated snapshot such as
`gpt-4o-2024-08-06` uses the row of the model it is a snapshot of; anything
else uses its provider's **default** row. Each default is set to that
provider's dearest current rate, so a model Sentinel has no price for is
over-estimated against your budgets, never under. A rate of zero means
unknown, not free. The estimate before a request, the recorded cost after it
and the price Auto-route weighs all come from the same row.

## Logs

**Cost history** filters recorded requests and exports CSV. **Run log** shows
agent, route, status, tokens, duration, cost and errors. Logs help explain what
Sentinel did; they may contain sensitive task labels, so handle exports safely.

### Portable Emergency Reset

When Sentinel is running from a supported portable build, the General tab also
shows **Emergency Reset**. It is intentionally absent from development and
ordinary installed mode. The reset requires typing `ERASE SENTINEL DATA`, then
accepting a separate final warning. It stops running Sentinel tasks, deletes the
complete `Sentinel Data` contents—including `.env` API keys—and quits.

It does not format the USB drive, delete neighboring files or remove exports
saved elsewhere. It also cannot remove records held by macOS, networks or model
providers. See [Portable USB mode](portable.md) before using it.

## Completion check

Explain the difference between a provider, model, tool, agent cap and global
budget. In portable mode, also explain exactly what Emergency Reset does and
which external records it cannot remove.

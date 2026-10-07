# Controls and settings

**Goal:** understand Sentinel's shared controls and configuration. **Time:** 15 minutes.

## Request controls

| Control | Meaning |
|---|---|
| Provider | Where the model runs. Ollama is local; other choices are cloud services. |
| Model | The specific model used by the selected provider. Availability depends on installation, key and provider account. |
| Auto-route | Applies Sentinel's recommendation using task, privacy, availability and cost preferences. Review the result before running. |
| Main action | Send, Investigate, Analyse, Ask Advisor or another agent-specific operation. |
| Stop | Requests cancellation of the active worker. Partial results may remain. |
| Best fit | A **BEST FIT** badge on one entry per dropdown is Sentinel's recommendation for this agent. Hover for the reason. It is advice; the entry you pick is the one that runs. |
| Paid marker | The control turns amber while a route that may charge through a cloud API is selected, and cloud entries say so on hover. It is a warning, not a price quote. |

## Menus and guidance

**Agent guide** documents the selected agent. **Tips** controls hover help.
**Inspector** shows live operational information. The **•••** menu opens the
Learning Centre, app documentation, model guide, Cost history, Run log and
Settings.

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
tool but is not an editor in this screen.

## Settings — Pricing

Pricing values are USD per one million input, cached-input and output tokens.
Update them when provider prices change. Cached input can be cheaper when a
provider reuses recent context. Incorrect values produce incorrect estimates,
not changes to the provider's invoice.

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

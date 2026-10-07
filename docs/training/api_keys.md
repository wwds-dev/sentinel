# API keys and new models

**Goal:** connect a cloud AI provider and keep its models current. **Time:** about ten minutes.

Ollama needs no key: it runs on this Mac. Every other provider in the
Provider dropdown is a cloud service that needs an API key from that
provider's own website. Sentinel only reads keys; it never creates one.

## Where Sentinel looks for keys

Keys live in a plain text file named `.env`. Which file depends on how you
start Sentinel:

| How you start Sentinel | The `.env` it reads |
|---|---|
| `/Applications/Sentinel.app` from `scripts/install_app.sh`, or `python main.py` | `.env` in the project folder, next to `main.py` |
| A packaged copy from `scripts/build_app.sh --install` | `~/Library/Application Support/Sentinel/.env` |
| Portable USB mode | `Sentinel Data/.env` on the drive |

A key already set in the shell environment takes precedence over the file.

## Add an AI provider key

1. Create a key on the provider's site:

   | Provider | Line in `.env` | Where to create the key |
   |---|---|---|
   | OpenAI | `OPENAI_API_KEY=` | platform.openai.com → API keys |
   | Anthropic | `ANTHROPIC_API_KEY=` | console.anthropic.com → API keys |
   | Gemini | `GOOGLE_API_KEY=` (or `GEMINI_API_KEY=`) | aistudio.google.com → Get API key |
   | DeepSeek | `DEEPSEEK_API_KEY=` | platform.deepseek.com → API keys |
   | Kimi | `KIMI_API_KEY=` | platform.kimi.ai |
   | Qwen | `DASHSCOPE_API_KEY=` | bailian.console.alibabacloud.com (Model Studio) |

   A mainland-China Qwen account also needs `DASHSCOPE_BASE_URL=`; the
   comment in `.env.example` gives the address.
2. If there is no `.env` yet, copy `.env.example` from the project folder to
   the location in the table above and rename the copy to `.env`. Finder hides
   names that start with a dot; press **⌘⇧.** to show them.
3. Open `.env` in TextEdit and paste the key straight after the `=`: no
   quotes, no spaces, one key per line. Save.
4. **Quit Sentinel completely** with **Quit Sentinel** in its menu bar
   item (closing the window is not enough), then start it again. AI provider
   keys are read once, at startup.
5. Check the **API KEYS** card in the left rail: the provider now reads
   **ready**. **no key** means Sentinel did not find it. Check the file
   location, the spelling of the name before `=`, and that you restarted.
6. Allow the provider for Chat requests in **Options → Paid provider
   access**. A key proves Sentinel can sign in; this permission decides
   whether it may spend money there.

OSINT source keys (HaveIBeenPwned, IPinfo and the others) can be entered
inside the app instead, in **Settings → OSINT Keys → Save Key**. They are
written to the same `.env` and work without a restart.

Never paste a key into a chat message, a report or a screenshot, and never
commit `.env` to git.

## Keeping models current

Providers release new models without notice. A provider's model dropdown
lists whatever that provider serves today, so a new model can be selected
as soon as it exists. What it does not do by itself is tell you, or let
Sentinel weigh the new model when it recommends one.

The **MODEL UPDATES** card in the left rail does both:

- At every start, and whenever you press **Check now**, Sentinel asks each
  provider that has a key for its list of models. Listing is free and sends
  no prompt. Providers are asked one after another, never all at once.
- The header shows how many models are new (**7 new**, amber light), and
  each new model is a row in the card. The very first check only reports
  models that are newer than anything Sentinel already rates, not the dozens
  of older ids a provider still lists; when several releases of one model
  line turn up together, only the newest is shown. "New" decides what is
  worth telling you about, nothing more.
- New models wear a grey **NEW** badge in their model dropdown until you
  review them.
- **Review details** opens each new model with an assessment.

## Updating models

Click a model's row to select it: the row takes a background colour. Click
it again to clear it. **Update N** then brings in exactly the selected
models, and nothing else.

Bringing a model in (**Update** on the card, or **Adopt** in the review)
does two things:

- The router can rank it. With **Auto-route** on, every request — Chat and
  every agent — is assessed on its own, and the new model is chosen for a
  request only when it fits that request best. When several models fit
  equally well, the cheapest wins.
- An agent's BEST FIT moves to it only when it is **better value than the
  current pick**: rated at least as well for that agent's work *and* known
  to cost less. Being newer counts for nothing, and neither does being more
  expensive. For now this is only judged within the current pick's own
  model line (a cheaper `claude-sonnet` against `claude-sonnet`), because
  Sentinel's capability ratings are too coarse to compare different lines
  fairly.

A new model is rated like the newest model of its line that Sentinel
already knows, and it has **no price** until you enter one in **Settings →
Pricing**. An unknown price never wins: an unpriced model can be selected by
hand and can win a request it fits better than anything else, but it is
never chosen on cost. Cost estimates for it use the provider's default rate
in the meantime. A model with no known sibling is offered in the dropdowns
but never ranked.

**Dismiss** in the review stops a model being listed as new. It stays
selectable in the dropdown.

## What a check cannot see

- A provider without a key cannot be asked. Hover over **Last check** to see
  which providers were checked and why any were skipped.
- A provider Sentinel does not support yet never appears at all. Adding one
  means code: each provider needs its own client.

## Completion check

You can say which `.env` your copy of Sentinel reads, add a key so its
provider shows **ready**, run **Check now**, and explain what **Adopt**
would change for a model listed under **Review**.

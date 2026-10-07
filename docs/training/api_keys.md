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
- **New models** counts the models that appeared since the last check. The
  very first check only reports genuine successors, such as a newer
  `claude-sonnet` than the one Sentinel recommends. It ignores the dozens of
  older ids a provider still lists. When several releases of one model
  family turn up together, only the newest counts.
- New models wear a grey **NEW** badge in their model dropdown until you
  review them.
- **Review** opens each new model with an assessment. Sentinel rates it like
  the newest model of the same family it already knows and says where it
  would become the best fit.

For each model you choose:

- **Adopt.** The router can rank it from now on. Every agent whose BEST FIT
  is an older release of the same family moves to it, and that agent's panel
  switches to it straight away. Chat's own recommendation is recalculated on
  every request, so it picks the new model up wherever it scores highest.
- **Dismiss.** It stops being listed as new. It stays selectable in the
  dropdown.

Adopting never changes an agent's pick to a model of a *different* family.
Those picks are deliberate (a cheap model for frequent research, for
example), and a rating copied from a sibling is not evidence that a
different model would do the job better. A model with no known sibling is
added to the dropdowns but never chosen as BEST FIT automatically.

A new model has no price in Sentinel yet. Until you add one in **Settings →
Pricing**, cost estimates for it use that provider's default rate.

## What a check cannot see

- A provider without a key cannot be asked. Hover over **Last check** to see
  which providers were checked and why any were skipped.
- A provider Sentinel does not support yet never appears at all. Adding one
  means code: each provider needs its own client.

## Completion check

You can say which `.env` your copy of Sentinel reads, add a key so its
provider shows **ready**, run **Check now**, and explain what **Adopt**
would change for a model listed under **Review**.

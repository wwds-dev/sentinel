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

Each row says who uses the key. A filled chip, such as **Trace**, means the
service is part of that agent's default run. An outlined chip means it is an
extra: the agent works without it and uses it once the key is saved or you
tick it. Leave **Explain on hover** on and hover a service's name for what it
does and what its key changes.

**Check whether a key works.** Save Key checks the key straight away, and each
row's **Check** button does it again at any time. The button then reads
**✓ Works**, **✗ Rejected**, **✗ Malformed** (the key cannot be right, for
example a VirusTotal key that is not 64 hexadecimal characters; nothing is
sent), **! Limited** (the key is fine but its quota or plan is the problem) or
**? Offline**. Hover it for the details, such as the plan and what is left.
**Check all keys** checks every saved key whose check is free. A few services
have no free way to test a key, so their check spends one lookup or credit;
Check all leaves those out and names them, and their tooltip says the cost. A
check asks only about the key, never about a target. The last result is
remembered until the key changes; the key itself is never stored with it.

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
lets the router rank it. Whether it is then chosen is decided the same way
as for every other model, below.

## How Sentinel chooses a model

Being newer counts for nothing, and neither does being more expensive.
With **Auto-route** on, every request — Chat and every agent — is assessed
on its own:

1. Sentinel works out what kind of work the request is: coding, writing,
   reasoning, research or long documents, vision, or general.
2. It looks up how each usable model rates **for that kind of work** on the
   public LMArena leaderboard, where people compare two models' answers
   without knowing which is which.
3. Every model rated within **20 points** of the best one counts as good
   enough. 20 points is roughly a 53/47 split when the two meet head to
   head. Then **the cheapest of those wins**, using the prices in
   **Settings → Pricing**.

So a model that rates only a few points below the best for coding, at a
fifth of the price, is chosen for coding, and the reason is shown on the
route. With **Quality first** the best-rated model wins regardless of
price; with **Cost first** the margin widens to 50 points.

Each agent's **BEST FIT** badge is the same assessment, made for that
agent's kind of work over the providers you have keys for. It moves on its
own when the ratings, the prices or the available models change. Your
current selection in a panel is never changed behind your back; only the
badge moves.

What is never assumed:

- **An unknown price never wins.** A cloud model without a price of its
  own is weighed — and billed — at its provider's **default** rate, which is
  that provider's dearest current price, so it is never the cheap one by
  accident. A price of zero counts as unknown, not free. Add its price in
  **Settings → Pricing** and it is weighed like the rest.
- **A prompt is only sent to a model that can hold it.** Sentinel estimates
  the prompt's size (about four characters to a token, the same estimate
  the cost check uses) and skips every model whose context window is
  smaller. Over 100K tokens the request is treated as long-document work.
- **An unrated model is not chosen over rated ones**, because nothing shows
  it is good enough. That includes your local Ollama models: the
  leaderboard rates full models, not the copy on this Mac. They still win
  in **Local only** mode and with **Privacy first**, where Sentinel's own
  scoring decides.
- **Ratings at a higher effort setting are not borrowed.** When the
  leaderboard only rates a model's "high" or "max" setting, Sentinel uses
  the lowest of those, since it calls the model with its default settings.

The ratings come from LMArena's public dataset (CC BY 4.0). They refresh
at most once a day during the startup check, in the background. Hugging
Face, which hosts them, limits how fast anyone may ask, so a refresh can
stop part-way; whatever did not arrive keeps its previous copy, and a copy
shipped with Sentinel covers the first start. **Ratings** in the MODEL
UPDATES card says which copy is in use and when it was published.

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

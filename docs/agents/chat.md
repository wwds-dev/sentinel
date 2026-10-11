# CHAT — General-purpose conversation

`key: chat` · class: `agents/chat_agent/__init__.py → ChatAgent` (a stub — see How it works) · panel: standard `normal_panel` (no custom panel) · handler: `send_prompt()`

## What it does
The default agent. Plain text in, plain text out, with full multi-turn conversation history. It has no domain framing of its own — instead it wears whichever **Tool** you pick (General Chat, Writing, Coding, Summarize, Rewrite), each supplying a different system prompt. Use it for anything without a dedicated specialist agent.

## Inputs (panel controls)
| Control | Purpose |
|---|---|
| Project | Run-bar picker, "No project" by default. Replies are filed under the chosen Chat Project and their cost is attributed to it. The project shown here is applied to the whole conversation each time a reply is saved; with "No project" selected, a saved chat keeps whatever project it already has. Create a project with the **+** beside the project filter in History (name only). **Known limitation:** a project only groups saved chats and attributes usage — its instructions, default agent/provider/model and budget columns exist in the database but nothing reads them, and there is no screen to rename, archive or delete a project. |
| Tool | The conversation's system prompt: General Chat, Writing, Coding, Summarize or Rewrite. It heads the thread and is re-sent with every request. If you pick a different Tool before a later message, its prompt replaces the thread's system prompt from that message on. The Tool box is filled once at start-up from the enabled rows of the `tools` table; **Known limitation:** disabling a Tool in Settings → Tools does not remove it from the box until restart, and sending with it before then is refused ("Tool ... is disabled in the registry"). |
| Provider / Model | Which LLM runs the request. Ollama is local; cloud providers are listed under **Cloud providers** and turn the control amber while one is selected. The recommended route is pre-selected at start-up and carries a **BEST FIT** badge (hover an entry for the reason); because the execution mode starts as Local only, that route is an Ollama model. Sentinel remembers the last model you picked for each provider (`data/settings.local.json`). With **Auto-apply recommendations** ticked, the router's pick replaces the dropdowns on every Run. See Execution mode below for when a cloud pick is actually used. **Known limitation:** the Inspector's **Current Route** card shows the router's recommendation for what you have typed, not the dropdown selection, and the route a request actually used appears only in the Run log. |
| Options | Menu button on the run bar (there is no gear). It holds **Command**, **Execution mode**, **Routing priority**, **Paid provider access**, **Auto route now**, **Use recommended model** (applies the recommendation, including its execution mode), **Auto-apply recommendations**, **Estimate this request**, **Export current report** and **Models** (Refresh model list, Get Muse Glimmer, Open model guide). Provider and model are chosen on the run bar, not in this menu. Execution mode starts as Local only and every Paid provider access box starts unticked each time Sentinel launches. |
| Command | Options → Command. Optional prompt scaffold from `config/commands.json`: General Chat (empty), Summarize, Rewrite Professional, Explain Code, Debug Python, OSINT Brief. The scaffold is put in front of every message, followed by a blank line, until you change it. It is independent of the Tool, becomes part of the YOU message in the transcript, and starts the saved title. |
| Execution mode | Options → Execution mode. **Local only** (the default): every request runs on Ollama. If a cloud provider is selected in the run bar, Sentinel runs the request on Ollama with your saved Ollama default model (`deepseek-r1:8b` if none is saved) instead; the cost chip then reads `free · N tok · local only` and hovering it names the substitution. **Hybrid allowed**: the provider you picked runs; a cloud provider must be ticked under Paid provider access. **Cloud only**: Ollama is refused and a cloud provider must be ticked. An unticked provider makes Run show "Request blocked" with the reason; while you type, the cost chip reads "blocked · see route" and carries the same reason on hover. |
| Auto-route | Run-bar button. Picks a provider and model for the text in the prompt box and selects them directly; it needs text, and it never changes the agent, the Tool or the execution mode. Only providers ticked under Paid provider access are candidates, and only outside Local only mode, so with the defaults it can return only an Ollama model. |
| Cost chip | Right of the run bar. Updates as you type: `free · N tok` for Ollama, `~€x · N tok` for a cloud route. The estimate covers the message you are about to send (Command scaffold included) and not the earlier turns that are re-sent as context. |
| Prompt box | Your message. **Enter** sends, **Shift+Enter** inserts a newline, **Ctrl+Return** also sends. While a request is running, Enter does nothing (no send, no newline). **F1** opens this guide. |
| Run / Stop | **Run** sends. While a request is active Run is replaced by **Stop**, which cancels it; **Esc** stops too. Stop keeps the text received so far, adds a "Chat request stopped by user." notice, and logs the run as cancelled. A stopped or failed request records no cost, although the provider may still bill it. |

Before a request starts, Sentinel resolves the route, estimates the cost, and runs the Validator (agent and Tool enabled and permitted, provider permission, session, daily and agent budget caps). A cloud request then always opens a "Confirm External API Request" dialog showing provider, model and estimated cost, even when the provider is ticked. A local model is checked against this machine's memory: one that cannot fit is refused ("Model Too Large For This Machine"), a tight fit asks "Run it anyway?".

## Outputs
The scrollable **Conversation** area keeps every user and assistant message in order, with a small local date/time stamp on each one (for example `YOU · 31 Aug 2026 · 10:15`); an assistant message carries the time its request was sent, not the time the reply finished. The Tool's system prompt is shown at the top as a SYSTEM message, and so are the notices "Chat request stopped by user." and "The request could not be completed." — these notices are for the reader and are never sent to the model. Collapsing the area relabels it **Previous turns**.

Each turn is appended to `current_messages`, so follow-ups keep context. The whole thread is re-sent with every request and is never truncated, so a long chat can outgrow a small local model's context window. A failed or stopped request leaves your prompt (and any partial reply) in the thread, and it is sent again as context with your next message. If you scroll up the view stays where it is; it follows new text only when you were already at the bottom, and jumps to the bottom when you send. **Known limitation:** the transcript is plain escaped text — Markdown is not rendered and runs of spaces collapse, so code indentation can be lost on screen, in copy and in the export.

A conversation is one file in `data/chats/` holding `agent`, `backend`, `model`, `command`, `messages`, `response` and `timestamp`, plus `project`, `title` and `updated` when they apply. The file is created when the first reply completes and rewritten in place (`HistoryStore.update_chat()`) after each later reply, keeping its title and original timestamp; opening a saved chat and continuing it continues that same file. Errors and stopped requests save nothing by themselves (the next completed reply saves everything in the thread), and closing the window during a request loses that turn.

Conversations appear in the collapsed **History** area, which is visible only while Chat is selected. It holds a project filter (All projects, Unfiled, or one project) with the **+** button, an agent filter that lists only agents that have saved chats, a search box, the list, **Delete selected** and **New chat**.
- A row is titled `chat: ` plus the first 52 characters of your first message (with a Command selected, that is the scaffold text), or the name you gave it. Search matches the title only — not the message text or the replies — so a search for "chat" matches every Chat row you have not renamed.
- Click a row to open it. Double-click, or right-click → Rename…, to rename it (the double-click also opens it first); right-click → Assign to project… files it under a project or returns it to Unfiled. A rename or project change covers the whole conversation.
- **Delete selected** (or right-click → Delete) asks for confirmation and permanently removes that file; there is no trash.
- **New chat** clears the transcript, stops a request that is still running, and starts a new file with the next reply. It keeps the Tool, Command and project you had selected.
- Opening restores the transcript and the project and re-runs nothing. **Known limitation:** the Tool, Command, provider and model the chat used are not restored, so the Tool selected now replaces the conversation's system prompt on your next message. History also lists records saved by other agents (Trace, Bug Spray and so on): opening one switches to that agent without restoring its panel and loads its messages as Chat context, and continuing in Chat then rewrites that record in place — set the agent filter to chat to avoid this.

**Options → Export current report** writes the whole visible transcript as a plain-text `.txt` file in `data/reports/` (no Markdown or HTML, no file picker) and shows its path.

Each completed request is logged to the Run log (agent, Tool, provider, model, tokens, cost, status) and added to the Cost card, attributed to the project that was active when the request started.

## How it works
`GodAI.build_tool_messages()` in `main.py` returns `[system(tool prompt), user]` for the selected Tool, with the Command scaffold already in front of the user text. On the first turn that pair starts the thread; on later turns the earlier user and assistant messages are kept, the new user message is appended, and the selected Tool's system prompt replaces the thread's system message. `_backend_messages()` strips the UI-only notices, and the rest is re-sent each time. Ollama, OpenAI, DeepSeek, Kimi and Qwen receive the thread as role messages, Anthropic receives the system prompt separately, and Gemini receives the whole thread flattened into one `role: text` string with no system channel.

Every provider streams token by token (`stream_chat`, including Ollama); a backend that hands back a finished string, such as an OpenAI image model, is replayed word by word instead. Stop sets the worker's cancel flag and then terminates its thread.

**Known limitation:** `ChatAgent.build_messages()` is a stub that returns only `[user]`; it is used only when the selected Tool has no entry in the tool map, which the Tool box cannot produce. The system-plus-user messages come from `build_tool_messages()`.

**Known limitation:** every keystroke re-runs the router and the live cost estimate on the UI thread, which ask Ollama (and each ticked cloud provider) for its model list over HTTP without caching, so typing can lag when the Ollama daemon is slow to answer.

## Under the hood — files & functions
| Location | Role |
|---|---|
| `agents/chat_agent/__init__.py` | `ChatAgent` — the stub described above. |
| `main.py: send_prompt()` | Builds the request: route, estimate, Validator, cloud confirmation, memory check, then spawns `ChatWorker` and streams tokens into the transcript. |
| `main.py: resolve_backend_model()` | Applies the execution mode and permissions to the provider/model pair. |
| `main.py: build_tool_messages()`, `_backend_messages()` | Builds the thread and filters UI-only notices. |
| `main.py: run_backend()` | Calls the provider client's `stream_chat`. |
| `ui/workers.py: ChatWorker` (QThread) | Runs the backend call off the UI thread. |
| DB `tools` table (`system_prompt`) | The actual system prompt per Tool. `config/tool_prompts.json` seeds that table once, when the database is first created; afterwards it is read only as a fallback for a row whose prompt is empty, so editing it on an existing install changes nothing. Missing built-in Tools are restored from `services/tool_catalog.py`. |
| `services/tool_catalog.py` | The five built-in Tools and their prompts. |
| `services/history_store.py` | `save_chat()` creates a chat file in `data/chats/`, `update_chat()` rewrites it. |

## Extend it
- **Add a Tool**: Chat permits only its five built-in Tools (the `allowed_tools` of the `chat` row in the registry). A new `tools` row will appear in the Tool box after a restart, but every send with it is refused ("Agent 'chat' does not permit tool 'X'"). To change a built-in Tool's wording, edit its `system_prompt` in the `tools` table and restart. **Known limitation:** `recommended_provider` and `recommended_model` in `config/tool_prompts.json` are copied into the database but never read — the router picks from the task type and ratings.
- **Add command scaffolds**: extend `config/commands.json`; it is read at start-up, so a new scaffold appears under Options → Command after a restart.
- **Attachments / RAG**: `send_prompt()` is the hook — enrich the user message before it reaches `ChatWorker`.

## Requirements
Any compatible text/reasoning provider. Ollama is free/local; cloud providers need an API key (app `.env`) and permission under Options → Paid provider access. The router never recommends image-only or speech-only models for text tasks. **Known limitation:** the OpenAI model list in Chat can still contain image models (`gpt-image-*`); selecting one generates an image file under `output/images/` and the reply reads "Image saved to" followed by its path.

# Chat essentials

**Goal:** use Chat as a continuing conversation without losing control of
privacy or cost. **Time:** about ten minutes.

![Chat controls and conversation](docs/training/images/workspace-chat.png)

## Choose the right tool

- **General Chat** answers questions and helps develop ideas.
- **Writing** improves clarity, tone and structure.
- **Coding** explains, creates and reviews code.
- **Summarize** extracts the important points.
- **Rewrite** changes wording while preserving meaning.

The Tool's instructions apply to the whole conversation. If you pick a
different Tool before a later message, the new instructions replace the old
ones from that message on. **Options → Command** adds a ready-made instruction
(Summarize, Rewrite Professional, Explain Code, Debug Python, OSINT Brief) in
front of every message you send until you set it back to General Chat.

## Work with a conversation

Write a clear request, include the desired result, and add relevant limits.
Press **Enter** to send or **Shift+Enter** for a new line. Follow-up messages
retain the current conversation context. Scroll upward to review earlier turns;
Sentinel does not force you back to the bottom while you are reading.

While a request is running, **Run** is replaced by **Stop** (the **Esc** key does
the same). The text received so far stays in the conversation. A stopped or
failed message also stays, and is sent again as context with your next one.

The whole conversation is sent again with every message, so on a cloud provider
a long conversation costs more than the estimate beside **Run** suggests: that
estimate counts only the message you are about to send.

Use **New chat** for a separate topic. It sits under **History** in the left
sidebar (visible while Chat is selected), stops a request that is still running,
and keeps the Tool, Command and project you had selected.

Each conversation is saved automatically as one entry in History, titled with
your first message. It appears when the first reply finishes and is updated
after each later reply. Search matches the title only, not the replies; the
list can also be filtered by agent and by project. Open an item to restore the
saved conversation and its project. Opening does not re-select the Tool,
provider or model it used, so check the run bar before you continue: the Tool
you have selected then replaces the conversation's instructions. Double-click a
title (or right-click and choose **Rename…**) to rename it, and right-click →
**Assign to project…** to file it. Deleting history removes that saved record
after a confirmation and should be treated as permanent. History also lists
records from other agents; set the agent filter to **chat** to see only Chat's.

To group conversations, choose a project in the run bar (**No project** by
default) before you send, and create one with **+** in History. A project files
conversations and attributes their cost; it does not add instructions or a
budget of its own.

Replies appear as plain text: Markdown is not rendered and the indentation of
code can be lost. To keep a copy outside History, use **Options → Export current
report**, which saves the whole conversation as a plain-text file.

## Routing and privacy

**Auto-route** picks a provider and model for the message you have typed and
selects them for you; it does not change the Tool. Ollama runs locally. Cloud
providers live under **Cloud providers** in the dropdown, turn the control
amber once selected, require an API key and permission, and may incur a
charge. Never include passwords, API keys or unnecessary personal data in a
cloud request.

Sentinel starts in **Local only** mode with every paid provider switched off.
In that state a cloud provider you pick in the run bar is not used: the request
runs on Ollama instead, and the cost beside **Run** ends with "local only"
(hover it for the details). To use a cloud provider, choose **Options → Execution
mode → Hybrid allowed** (or **Cloud only**), tick the provider under **Options →
Paid provider access**, then press **Run** and confirm the cost prompt that
appears for every cloud request. Auto-route can choose only among the providers
you have enabled this way.

## Writing effective requests

State the goal, provide the necessary context, name the desired format and add
constraints. For example: “Turn these notes into a six-item checklist for a
non-technical reader; keep it below 150 words.” If an answer misses the goal,
explain what to change rather than starting over. Chat remembers earlier turns
in the current conversation.

## What Chat does not do

Chat answers inside the conversation. Use Forge when the goal is a reviewed
agent scaffold, and use a specialist when the task needs its dedicated inputs,
validation, sources or report format. An answer can still be wrong; verify
important facts and never run generated commands without review.

## Practice

Ask Chat to turn five rough notes into a concise checklist. Then request one
revision without repeating the notes. Confirm that the earlier context was
retained, then open **Run log** and check which provider and model each of the
two requests used.

## Completion check

Create a two-turn conversation, rename its single saved entry, identify the
route it used in the Run log and explain why no secret should be pasted into a
cloud request.

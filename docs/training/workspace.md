# Workspace tour

**Goal:** know where to find every major part of Sentinel. **Time:** 10 minutes.

![The current Sentinel workspace](docs/training/images/workspace-chat.png)

## Left sidebar

Choose **Chat, Trace, Bloodhound, Beacon, Sentry, Bug Spray, Tunnel,** or **Forge**.
Only one agent is active at a time. The selected row is highlighted. **History**
opens saved conversations; Trace has its own **Saved searches** area. Selecting
saved work restores it but does not automatically rerun a request.

Below them sit three small screens. Every sidebar tile is drawn the same way:
a header strip with a status light and a short status on the right, then
aligned readouts. Green means fine, amber means look at it, red means act.

- **API Keys** shows which cloud providers have a key (**4/6** in the header).
  “Ready” means a key was detected; it does not guarantee account credit,
  provider availability or permission for the current request. See
  [API keys and new models](api_keys.md).
- **Model Updates** shows when providers were last checked, which ratings are
  in use, and any new models as rows. Click rows to select them, then
  **Update** to bring exactly those in.
- **Actions** opens Costs, Run log and Settings.

At the top of the sidebar, beside **SENTINEL** and the version number, three
coloured dots choose the colour theme: green (Matrix), red, or blue
(Cyberpunk). Click a dot to switch; the ringed dot is the current theme. Status
colours keep their meaning in every theme — a destructive button stays red and
a paid route stays amber.

## Centre workspace

The title and subtitle identify the current agent. **Agent guide** opens its
reference sheet. **Tips** enables or disables hover explanations. **Inspector**
shows or hides the right rail. **•••** contains the Learning Centre, app docs,
model guide, logs and Settings.

Inputs differ by agent, but provider, model, Auto-route, main action and Stop
behave consistently. Results remain in the centre. Collapsed sections hide
advanced choices until needed; they do not disable work already entered.

## Inspector

**Current Route** shows the provider/model intended for the next request; its
light turns amber when that route is a paid cloud service. **Cost** shows the
latest and session totals. **Budget** compares spending with your caps; its
light turns amber at 60% of a cap and red at 90%. **System** shows device load,
which is particularly useful for local models, as segmented meters. Values can
change while a request is running.

## Menu bar item

While Sentinel is open, a shield icon sits in the macOS menu bar. Its menu
shows whether Sentinel is **Idle** or **Working** (and for which agent) and what
this session has cost, followed by **Open Sentinel** and **Quit Sentinel**.
Quit behaves exactly like closing the window: an active request is cancelled
and background work stops. Closing the window still quits Sentinel; nothing
keeps running in the menu bar.

## Good habits

- Read the selected route before running sensitive work.
- Start a new chat when changing to an unrelated subject.
- Use Stop when a task is no longer useful; stopping may not reverse a cloud
  request already received by its provider.
- Save or export valuable results before clearing a panel.

## Completion check

Locate History, Agent guide, Learning Centre, Inspector, Settings and Stop.
Switch the theme from the brand-row dots, then read the session cost from the
menu bar item.


# Agent workflows

Larger goals often benefit from more than one agent. Review results between
stages; Sentinel should not automatically pass sensitive material to another
agent or cloud provider.

| Goal | Suggested flow | Result |
|---|---|---|
| Research a public identity | Trace → Bloodhound → Chat | Sources, connections and a written report |
| Review an authorised website | Trace → Bug Spray | Context, findings and a remediation report |
| Improve a personal network | Beacon → Tunnel → Chat | Wi-Fi observations, VPN guidance and a checklist |
| Find and organise files | Bloodhound → Chat | Selected files, interpretation and an organisation plan |
| Design a new specialist | Chat → Forge | A reviewed specification and an inactive scaffold |

## Workflow 1: public identity research

1. **Trace** validates the identifier and creates a focused plan or collects
   consented public records.
2. Review matches and remove unrelated people or unsupported assumptions.
3. **Bloodhound** turns the verified starting material into a deeper dossier.
4. Review sources, confidence and risk statements.
5. **Forge** is for agent scaffolds only; it does not write reports. For an
   ordinary written report, use Chat's Writing tool or export the reviewed result.

Do not transfer a complete email address, personal image or case file to a
cloud provider merely for convenience.

## Workflow 2: authorised website review

1. Record the written scope, target and permitted test window.
2. **Trace** can collect public domain/company context without probing the app.
3. **Bug Spray** receives only the in-scope target and relevant evidence.
4. Run conservative authorised reconnaissance if necessary.
5. Review the PoC, CVSS, impact and remediation before preparing a submission.

Stop if the target leaves scope, becomes unstable or contains unexpected
personal data.

## Workflow 3: secure personal connectivity

1. **Beacon** checks the current interface, reachability and visible Wi-Fi
   conditions on an authorised network.
2. **Tunnel** runs its local-only Connection Check, then designs the correct
   remote-VPS or home-LAN topology and renders a configuration locally.
3. Review the route, DNS and handshake observations separately; they prove
   different things.
4. Apply changes outside Sentinel with a recovery path.
5. Return only a non-secret diagnostic card to **Chat** for a plain-language checklist.

Never place VPN private keys in Chat or an AI-backed diagnostic request.

## Workflow 4: locate and understand files

1. **Bloodhound File Discovery** searches explicitly selected folders using
   metadata filters.
2. Select only the relevant results; do not upload the full device inventory.
3. Use **Chat** to compare non-sensitive excerpts or plan organisation.
4. Use normal file tools for any move or rename—Bloodhound remains read-only.

## Workflow 5: design a new specialist

1. Use **Chat** to clarify the problem and remove unnecessary capabilities.
2. Use **Forge** to generate a structured agent spec.
3. Review providers, tools, budget and approval requirements. The
   specification cannot be edited in Forge; to change it, refine the idea and
   analyse again.
4. Create the inactive scaffold (one file and two disabled registry rows),
   inspect the code and add tests.
5. Integrate it deliberately; creation does not equal production readiness.

## Handoff checklist

1. Confirm the goal and authorised scope.
2. Keep only information the next agent genuinely needs.
3. Review whether the next route is local or cloud.
4. Run the next agent and verify its result before continuing.
5. Write up a finished output (Chat's Writing tool, or the specialist's own
   report cards) only after the evidence is reviewed. Forge does not write reports.

## Choosing where to pause

Pause whenever the next stage changes the target, sends information to a new
destination, incurs cost, executes a real scanner, writes files, or requires
privileged access. The handoff is a decision point, not an automatic pipeline.

Security-testing agents are for systems, networks and programs you own or have
explicit permission to assess.

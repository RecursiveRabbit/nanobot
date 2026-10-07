# How to Run a Long-Running AI Agent with nanobot

nanobot can keep agent work alive across turns through persistent sessions,
scheduled automations, local triggers, and a gateway process that stays
running.

## What you will build

- a working local agent
- a persistent chat session
- a scheduled automation or local trigger
- a gateway process for background delivery

## When to use this

Use this when the task is not a one-shot answer: project work, recurring checks,
scheduled summaries, file maintenance, multi-step research, or local triggers
from scripts and build jobs.

## Install

```bash
python -m pip install nanobot-ai
nanobot onboard --wizard
nanobot agent -m "Hello!"
```

## Minimal working example

Start a gateway:

```bash
nanobot gateway
```

Create the automation from the target chat so nanobot can link it to the
correct session and workspace. Local triggers fire turns from scripts and
build jobs without a model in the loop.

## Production notes

- Keep the gateway running for chat apps, WebUI sessions, automations, and local
  triggers.
- Use stable session keys or chat sessions for work that should preserve context.
- Keep automations bounded and explicit about done-ness.
- Review Automations in the WebUI before relying on a schedule.

## Security notes

- Treat automations as delegated work with real tool access.
- Restrict workspaces and shell execution before scheduling unattended tasks.
- Keep chat access narrow so unknown users cannot create automations or triggers.

## Troubleshooting

- If an automation appears stuck, inspect the active session and gateway logs.
- If an automation does not run, check that it is linked to a chat/session and
  that the gateway is still running.
- If a local trigger fails, check the command copied from the WebUI Automations
  view.

## Related nanobot docs

- [Automations](../automations.md)
- [WebUI Automations](../webui.md#automations)
- [Chat Commands](../chat-commands.md)
- [Memory](../memory.md)
- [Deployment](../deployment.md)

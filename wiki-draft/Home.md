# Hollow AgentOS Wiki

Welcome. This wiki is the place to come when something doesn't work, when you want to understand what's actually happening inside the system, or when you just want a guided tour.

The main repo: [ninjahawk/hollow-agentOS](https://github.com/ninjahawk/hollow-agentOS)

---

## Pages

### Getting started
- **[Setup](Setup.md)** — full installation walkthrough, Windows / Mac / Linux
- **[Quick start](Quick-Start.md)** — TL;DR for people who've installed agent stacks before
- **[Choosing a model](Models.md)** — what runs on what hardware, why qwen3.6 is the default

### Running it
- **[Operator panel](Operator-Panel.md)** — what every control does, how to intervene in the agents' world
- **[Live monitor](Live-Monitor.md)** — reading the thoughts stream
- **[Updating](Updating.md)** — pulling new versions, what to do when behavior changes

### When things go wrong
- **[Troubleshooting](Troubleshooting.md)** — common errors and their fixes
- **[FAQ](FAQ.md)** — questions that come up often

### Concepts
- **[How the substrate works](Substrate.md)** — suffering, stressors, capability locks, lessons
- **[invoke_claude](Invoke-Claude.md)** — the agent → human implementation channel
- **[Using with Claude Code](Claude-Code.md)** — MCP integration

---

## Reporting bugs

If you hit something this wiki doesn't cover, [open an issue](https://github.com/ninjahawk/hollow-agentOS/issues/new) with:

- Your OS and version
- Whether you have a GPU and what kind
- The exact command/action that failed
- The full output (not truncated)

Screenshots help, especially for setup-wizard failures.

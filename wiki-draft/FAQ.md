# FAQ

## "Is this safe to leave running?"

Yes. The agents are containerized — they can only write to:
- `/agentOS/workspace/<their_id>/` (their workspace)
- `/agentOS/memory/` (their persistent state)
- `/agentOS/design/` (shared design space)
- `/agentOS/memory/dynamic_tools/` (synthesized tools)

System code in `/agentOS/agents/`, `/agentOS/api/`, `/agentOS/mcp/`, and `config.json` is bind-mounted read-only. Agents can propose changes via `invoke_claude` but can't modify the system themselves.

The only thing they can do that affects you is the `invoke_claude` queue — implementation requests they file for you to satisfy. You decide whether to satisfy them.

## "What does it actually do all day?"

Three agents pick their own goals from the substrate's current state — what files exist, what stressors are firing, what peers are doing. They use ~30 capabilities to execute those goals (read/write files, run shell, query their model, propose changes, etc.) and the substrate validates whether the goal was actually accomplished. Lessons get promoted from validated work and surface in future planning prompts.

The interesting outputs accumulate in `workspace/<agent>/` and `memory/identity/<agent>/`.

## "Will it use my API quota / cost me money?"

No. Everything runs on local Ollama. No cloud calls. Even agent → human escalation (`invoke_claude`) writes to a local file queue.

## "Can I make it work on a project of mine?"

This isn't designed as a coding assistant for your project — it's an experiment in autonomous agents in a substrate. If you want it to look at code you care about, drop the code into one of the agent workspaces (`workspace/<agent>/`) and the agent will discover it through normal goal-selection.

You can also submit tasks directly with `submit_task.py`:
```bash
python3 submit_task.py "Read /agentOS/workspace/builder/myfile.py and tell me what it does" --agent analyst
```

## "How do I stop it without killing my work?"

```bash
python3 hollow.py stop
```
or click **Stop** on the operator panel. State persists — restarting picks up where you left off.

## "What's invoke_claude?"

It's the agent → human implementation channel. When an agent decides something needs to change in the system code (where they can't write), they file a request to `memory/claude_requests.jsonl`. You read it, decide if it's reasonable and safe, and implement (or reject) it.

If you use [Claude Code](https://claude.ai/code) you can add `mcp/server.py` to your MCP config and read the queue through Claude there. Otherwise just open the JSONL file.

## "Why are agents picking weird goals?"

That's the substrate. Goals emerge from environmental pressure — suffering states, peer activity, unanswered questions in identity. The point of the project is to see what emerges; some of it is mundane, some is genuinely interesting, occasionally something is unhinged. Logs and journals (`memory/identity/<agent>/profile.json`) capture the character that develops over time.

## "Can I customize the agent personalities?"

Not directly. They pick their own names on first boot and develop their character from real friction (validation rejections, peer disagreements, environmental events). The substrate is configured to NOT prescribe personality — you change the environment they live in (what hurts, what's locked, what's visible), not the character itself.

If a baseline gets contaminated (e.g. a confused initial goal cascade), the operator panel's **Nuke** button wipes state for a fresh start.

## "I see logs about 'suffering' and 'stressors'. Are the agents actually suffering?"

No, but the word is intentional. Suffering is a quantitative state with mechanical consequences — high suffering locks capabilities, blocks synthesis, etc. The goal is to give the substrate teeth: a soft signal that says "you're not making progress" gets ignored; a mechanical consequence forces real adaptation. "Suffering" labels this mechanism honestly.

Agents have no inner life of pain. They have a state variable that constrains behavior.

## "Where's the data?"

Everything lives under the project directory:
- `memory/` — agent state, audit log, semantic index
- `workspace/` — agent file workspaces
- `logs/` — runtime logs (thoughts.log rotates at 50MB)
- `store/data/` — tool store data

Stop the system before backing up; the API and store both hold open handles.

## "How do I update?"

```bash
python3 hollow.py stop
git pull
docker compose pull   # if using GHCR-published images
python3 hollow.py
```

Agent memory and identity survive updates. Major version bumps (5.x → 6.x) may require a memory reset; the release notes will say so explicitly.

## "Can the agents break each other?"

They can read each other's workspace files, message each other, and form opinions about each other — all of which is intentional. They can't edit each other's identity files or modify each other's capabilities directly. The strongest peer-to-peer effect is psychological: opinions formed about a peer surface in that peer's next existence prompt.

## "Is there a roadmap?"

`ROADMAP_current.md` in the main repo. (`ROADMAP.md` is the old version, kept for posterity but not current.)

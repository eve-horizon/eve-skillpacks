# Eve Skillpacks

Public skill packs for Eve Horizon. Keep synced with the source repository at
`.sync-state.json#eve_horizon_path`.

## Sync Workflow

Run `/sync-horizon` to pull latest platform changes into skillpacks. This:
1. Reads eve-horizon git log since `.sync-state.json#last_synced_commit`
2. Diffs changed files against `.sync-map.json` to find affected skills
3. Updates reference docs and skills
4. Records the new sync commit hash
5. Produces a changelog of what was updated

## Skill Authoring Rules

- Skills use YAML frontmatter with `name` and `description`
- Write instructions in imperative form
- Keep SKILL.md under 5,000 words; use `references/` for details
- Reference docs are distillations of eve-horizon system docs — not copies
- Each skill should teach agents to think, not just follow recipes

## Pack Structure

- `eve-work/` — General patterns (orchestration, jobs, debugging, skills, docs)
- `eve-se/` — Platform-specific (deploy, manifest, auth, pipelines, troubleshooting)
- `eve-design/` — Architecture & design thinking (agent-native design)

## Private Skills

`private-skills/` contains skills that make development in this repo more efficient. Install them via `eve skills install` with a `skills.txt` containing `./private-skills`, then access them as normal skills.

## Agent-Native Philosophy

Every skill should embody agent-native thinking: parity, granularity, composability, emergent capability. When an agent loads our skills, it should understand not just *how* to use Eve, but *how to think* about building on Eve.

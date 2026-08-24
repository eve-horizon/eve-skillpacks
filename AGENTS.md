# Eve Skillpacks - Agent Instructions

Read CLAUDE.md for project context and conventions.

## Canonical Repository

`github.com/eve-horizon/eve-skillpacks` is the only repository to work in.
`Incept5/eve-skillpacks` is the retired pre-open-source ancestor and must not
receive new work. Confirm the remote before committing or pushing:

```bash
git remote get-url origin  # must be .../eve-horizon/eve-skillpacks
```

## Syncing with Eve Horizon

To update skillpacks from the latest eve-horizon changes, run:

```
/sync-horizon
```

This reads the eve-horizon git log since the last tracked sync, identifies changes that affect skillpacks, updates reference docs and skills, and records the new sync point.

### Manual Sync Check

To see what's changed without updating:

```bash
HORIZON_PATH=$(jq -r '.eve_horizon_path' .sync-state.json)
git -C "$HORIZON_PATH" log --oneline "$(jq -r '.last_synced_commit' .sync-state.json)..HEAD" -- docs/system/ docs/deploy/oss-release-cutover.md docs/ideas/agent-native-design.md packages/cli/src/commands/ AGENTS.md
```

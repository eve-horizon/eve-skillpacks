# Deploy Cycle Patterns

The fix/deploy loop for verification against cloud and local environments.

## Environment Detection

```bash
if [[ "$EVE_API_URL" == https://* ]]; then
  ENV_TYPE="cloud"
  echo "Target: Cloud staging ($EVE_API_URL)"
else
  ENV_TYPE="local"
  echo "Target: Local k3d ($EVE_API_URL)"
fi
```

## Cloud (Staging) — Default

Staging is the default verification target. It matches the real user experience.

### Fix/Deploy Loop

```
discover bug → fix code → commit → tag release-v* → push tag →
  wait for CI to publish all seven service images →
  instance owner bumps the pinned version and deploys from the instance repo →
  re-run failed scenario
```

### Step by Step

```bash
# 1. Fix the code
git add -A && git commit -m "fix: description of fix"

# 2. Tag and push one explicit ref (publishes artifacts; does not deploy)
LAST_TAG=$(git tag --list 'release-v*' --sort=-version:refname | head -1)
# Increment version (e.g., release-v0.1.241 → release-v0.1.242)
NEXT_VERSION=$(echo "$LAST_TAG" | awk -F. '{print $1"."$2"."$3+1}')
git tag -a "$NEXT_VERSION" -m "$NEXT_VERSION"
git push origin "refs/tags/$NEXT_VERSION"

# 3. Wait for all seven images to publish
echo "Waiting for CI to publish platform images..."
# Source repo: publish-images workflow

# 4. In the target deployment-instance repo, follow its owner-approved runbook
# bin/eve-infra upgrade <version>
# git diff
# bin/eve-infra deploy

# 5. Verify deploy landed
eve system health --json
# Use the instance repo's wrapper and explicit kubeconfig/context for pod checks.

# 6. Re-run the failed scenario
```

### Monitoring the Deploy

Three repository roles are involved:

1. **Public source** (`eve-horizon/eve-horizon`): `publish-images.yml` publishes seven service images and stops
2. **Public infra template** (`eve-horizon/eve-horizon-infra`): reusable Terraform/Kustomize scaffold
3. **Private deployment instance** (`<org>/<name>-eve-infra`): pins the platform version and owns rollout credentials

```bash
# Check source repo workflow
gh run list --repo eve-horizon/eve-horizon --workflow publish-images.yml --limit 3

# After the instance owner starts a rollout, check that repo's workflow
gh run list --repo your-org/deployment-instance-repo --limit 3
```

### Typical Timing

| Phase | Duration |
|-------|----------|
| Image builds (7 services) | ~3-5 minutes |
| Instance version bump + approval | Owner-dependent |
| Instance apply + rollout | ~2-3 minutes |
| **Total** | **~5-8 minutes** |

## Local (k3d)

Local verification is faster but less representative.

### Fix/Deploy Loop

```
discover bug → fix code → pnpm build →
  ./bin/eh k8s-image push → ./bin/eh k8s deploy →
  re-run failed scenario
```

### Step by Step

```bash
# 1. Fix the code
# (edit files)

# 2. Build
pnpm build

# 3. Push images to k3d registry and deploy
./bin/eh k8s-image push
./bin/eh k8s deploy

# 4. Wait for rollout
kubectl -n eve rollout status deployment/eve-api --timeout=120s

# 5. Re-run the failed scenario
```

### Typical Timing

| Phase | Duration |
|-------|----------|
| `pnpm build` | ~30-60 seconds |
| `k8s-image push` | ~30-60 seconds |
| `k8s deploy` | ~30-60 seconds |
| **Total** | **~2-3 minutes** |

## Waiting for Deploys

### Cloud — Poll Until Ready

```bash
wait_for_deploy() {
  local max_wait=600  # 10 minutes
  local interval=30
  local elapsed=0

  while [ $elapsed -lt $max_wait ]; do
    if eve system health --json 2>/dev/null | jq -e '.status == "ok"' > /dev/null; then
      echo "Deploy ready after ${elapsed}s"
      return 0
    fi
    echo "Waiting... (${elapsed}s)"
    sleep $interval
    elapsed=$((elapsed + interval))
  done

  echo "Deploy not ready after ${max_wait}s"
  return 1
}
```

### Local — Rollout Status

```bash
# Wait for all deployments
for deploy in eve-api eve-orchestrator eve-worker eve-agent-runtime eve-gateway; do
  kubectl -n eve rollout status deployment/$deploy --timeout=120s
done
```

## When to Use Which Environment

| Situation | Environment |
|-----------|-------------|
| Initial verification of a new app | Cloud (staging) |
| Rapid iteration on a failing scenario | Local (k3d) |
| Pre-handoff final verification | Cloud (staging) |
| CI automated runs | Cloud (staging) |
| Debugging platform issues | Local (k3d) first, then cloud |

## Including Deploy Cycle in Test Plans

When a scenario is expected to involve fix/deploy iteration, include this section:

```markdown
## Fix/Deploy Cycle

This scenario may require iteration. Follow the deploy cycle for your environment:

### Cloud (Staging)
1. Fix code and commit
2. Push one explicit `release-v<next>` tag and wait for all seven images
3. Have the target instance owner bump and deploy the pinned version from the private instance repo
4. Verify: `eve system health --json`
5. Re-run from Phase N

### Local (k3d)
1. Fix code
2. `pnpm build && ./bin/eh k8s-image push && ./bin/eh k8s deploy`
3. Wait ~2-3 minutes
4. Re-run from Phase N
```

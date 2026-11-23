# Security Profiles + Building for Strict

## Use When
- You need to know what the `legacy`, `audit` and `strict` profiles do, or which one a project runs under.
- You are building or changing an app that must run under `strict`, or preparing a project to move to `strict`.
- A sync, deploy or job was refused and names a security rule, a manifest field below, or `eve doctor security` lists findings.

## Load Next
- `references/manifest.md` for `x-eve.ingress`, `x-eve.security`, `x-eve.egress.hosts` and managed DB `roles`.
- `references/database-ops.md` for declared roles, schema-owner roles and connection limits.
- `references/deploy-debug.md` for strict deploy refusals and Pod Security.
- `references/secrets-auth.md` for secrets and operator rights under `strict`.
- `references/harnesses.md` for checkout settings and the hosts each harness reaches.

## Ask If Missing
- Confirm the project ID and the profile it resolves to (a system admin runs `eve doctor security --project <id>`).
- Confirm whether the instance has an egress provider (`EVE_EGRESS_FQDN_PROVIDER`) and enforces NetworkPolicy (`network.policy_enforced`).
- Confirm every internet host each service and job calls, and which services must be reachable from outside.

## Profiles

| Profile | What a hardening rule does |
|---|---|
| `legacy` | Nothing. Deprecated in 0.1.330, removed in 0.1.331. |
| `audit` | Keeps today's behaviour and records a finding for what `strict` would refuse. The starting profile of an instance. |
| `strict` | Enforces the rule: the request gets 403 (or the sync, deploy or job is refused) and a finding is recorded. |

Where a profile is set (only system admins set profiles, at every level):
- the instance (system setting `security.profile`);
- an org, which overrides the instance;
- a project, which overrides its org;
- `security.new_project_profile`, written onto each project as it is created.

```bash
eve system security profile audit                          # instance
eve system security profile strict --project proj_xxx      # one project
eve system security profile clear --project proj_xxx       # inherit again
eve system security profile strict --new-projects          # default for new projects
```

Each rule follows the profile of the side it governs: a project's own resources follow the project, org-wide data (org files, docs, cloud-fs) follows the org, and SSO identity rules follow the instance.

Do not confuse the profile with `eve project sync --strict` or `eve manifest validate --strict`: those flags only fail on missing secrets.

### Rule sets and stamps

Rules arrive one rule set per release: rule set 1 (0.1.326), 2 (0.1.327), 3 (0.1.328), 4 (0.1.329). A `strict` profile is stamped with the rule set current when it was set. A rule newer than the stamp runs as `audit` there, so an upgrade never starts enforcing new rules on a project that is already `strict`. To take the newer rules, check the findings and set `strict` again (`eve system security profile strict --project proj_xxx`). `eve doctor security` shows what each `strict` covers, for example `strict (P1 rules); P2 rules on audit`.

### The `legacy` deprecation

- **0.1.330:** setting `legacy` still works and returns a warning ("legacy is deprecated and is removed in 0.1.331; use audit"). `eve doctor security` lists every level that resolves to `legacy` (`legacy_levels` in `--json`).
- **0.1.331:** every stored `legacy` becomes `audit`, an instance with no profile resolves to `audit`, and the API and CLI refuse `legacy` (400, naming `audit`).
- **Before 0.1.331:** set each listed level to `audit` (`eve system security profile audit` for the instance). `audit` keeps today's behaviour and records what `strict` would refuse.

## Check Readiness

`eve doctor security` reads `GET /system/security/report` (system admins only; findings window 1-90 days, default 14):

```bash
eve doctor security                       # every org and project
eve doctor security --project proj_xxx    # one project
eve doctor security --days 7 --json
```

- A finding records the rule, the org and project, and who acted (a user, job, service, service principal or app link). It never records a value, token or request body. Findings are kept one row per rule, scope, actor and day, with a count.
- Each project not yet `strict` shows its strict checklist: `ready for strict`, or `not ready:` with the blockers (findings in the window, or `network.policy_enforced` not `true`).
- A project with no findings for a few days of normal use can move to `strict`.
- Under `audit` nothing is recorded per network connection. Evidence for egress comes from allowed-flow logs compared with the declared hosts (the source docs suggest fourteen days); ask the platform operator.

Flip and roll back (system admin):

```bash
eve project sync --project proj_xxx --dir .                 # runners take hosts from the last explicit sync
eve system security profile strict --project proj_xxx      # enforce
eve system security profile audit --project proj_xxx       # back out at once
```

Setting a profile reconciles the project's network policies straight away; `eve system security reconcile` finishes the job if the worker could not be reached.

## Building for Strict

Work through each area. Everything here is safe under `audit` too, so make the changes first and flip after.

### 1. Declare ingress on every service with ports (`ports.implicit_public`)

| Manifest | `legacy` / `audit` | `strict` |
|---|---|---|
| `x-eve.ingress.public: true` | Public | Public |
| `x-eve.ingress` block without `public: true` | Private | Private |
| `ports` and no `x-eve.ingress` block | Public (`audit` records a finding) | Private: no Ingress, default host, alias or domain, and the ingress controller cannot reach it |

A routed service that relies on the implicit default becomes unreachable from outside when the project goes `strict`. Declare `public: true` or `public: false` on every service with ports so the profile does not matter:

```yaml
services:
  web:
    ports: [8080]
    x-eve:
      ingress:
        public: true      # routed: say so
        port: 8080
  api:
    ports: [3000]
    x-eve:
      ingress:
        public: false     # internal: reached by web inside the namespace
```

The ingress controller reaches a routed service only on the port the Ingress routes to (`x-eve.ingress.port`, else the first port). Its next deploy removes an Ingress Eve made for a service that is no longer public.

### 2. Run as a numeric non-root user (`isolation.pod_security`)

Every service and `role: job` service (migrations included) runs with the restricted Pod Security context: `seccompProfile: RuntimeDefault`, no service account token, no privilege escalation, all capabilities dropped, `runAsNonRoot`, and a numeric `runAsUser` and `runAsGroup`.

- The user comes from `x-eve.security.run_as_user` / `run_as_group`, else from the image config's numeric `USER` (`uid` or `uid:gid`), read with `crane config` using the env's pull credentials.
- A `USER` that is a name (`USER node`), root or unset, or an image config that cannot be read, refuses the deploy or job with a message naming the fields. Prefer `USER 1000:1000` in the Dockerfile, or set the fields:

```yaml
services:
  api:
    build: { context: ./apps/api }
    ports: [3000]
    x-eve:
      ingress: { public: false }
      security:
        run_as_user: 1000     # matches USER node in the Dockerfile
        run_as_group: 1000
```

- Check third-party and platform images too, not only your own builds. Eve's migrate image (`ghcr.io/eve-horizon/eve-horizon/migrate`, from 0.1.332) runs as `USER 1000:1000` and needs no override; an image without a numeric `USER` needs `run_as_user` (and `run_as_group`) under `strict`.
- A non-root process that binds a port below 1024 needs `capabilities: [NET_BIND_SERVICE]`; or listen on 1024 and above.
- Opt-outs: `run_as_root: true` (keeps the other controls), and `capabilities` from `NET_BIND_SERVICE`, `CHOWN`, `SETUID`, `SETGID`, `DAC_OVERRIDE`. Root or any capability beyond `NET_BIND_SERVICE` puts the namespace at Pod Security `baseline` instead of `restricted`.
- Unknown fields under `x-eve.security` fail manifest validation.
- `x-eve.networking.egress: stable` (host network) is refused.
- Rule set 3 (`isolation.pod_security_admission`) adds Pod Security `enforce` on the namespace at its level, set after each successful rollout.

### 3. Declare every internet host (`egress.default_deny`, `egress.allow_hosts`)

Under `strict` (`egress.default_deny`, rule set 2) env namespaces reach DNS, the namespace itself, the Eve API's public port, the ingress controller, the env's managed databases, the app-link producers the project consumes and the internet outside private ranges. Private addresses, the metadata server and other tenants' namespaces are out of reach.

Under `egress.allow_hosts` (rule set 3) the internet is limited to declared hosts:

```yaml
x-eve:
  egress:
    hosts:                               # every pod of every env, and the project's job runners
      - api.example.com                  # port 443
      - "*.cdn.example.com"              # exactly one label under cdn.example.com
      - { name: smtp.example.com, port: 587 }
      - { pattern: "*.hooks.example.com", port: 8443 }

services:
  web:
    x-eve:
      egress:
        hosts: [payments.example.com]    # this service's pods only
```

- Entries: a host name, a `*.` pattern (one label), or `{ name | pattern, port }` (TCP, default 443). At most 50 per list.
- Refused at sync: schemes, paths, ports inside the string, bare `*`, `*` outside the first label, top-level patterns (`*.com`), IP addresses, single-label and cluster names (in-cluster peers need no entry), wildcard DNS services (`nip.io`, `sslip.io`, `xip.io`, `localtest.me`), and names that resolve to private, CGNAT, link-local, loopback or reserved addresses.
- Runners also reach, without an entry, the model APIs of the harnesses the project can run and the repository's git host. Apps do not.
- Declare in the manifest a custom model base URL (`ANTHROPIC_BASE_URL`, `Z_AI_BASE_URL`), another pi provider, Vertex AI, or a Google sign-in for Gemini.
- A job that names a harness the project cannot run (no harness profile names it and no project, org or instance secret is its credential) gets no route to its API: declare a harness profile under `x-eve.agents.profiles`.
- **Runners take the project list from the last explicit `eve project sync`.** A deploy never marks its manifest as synced. Run `eve project sync` after changing hosts, and before the flip. Env pods take the lists from the release the env runs, so redeploy after changing hosts.
- The instance needs an enforcement provider (`EVE_EGRESS_FQDN_PROVIDER=gke`). Without one, a `strict` project's deploys, service jobs and job dispatch fail with "egress allow lists need a provider (EVE_EGRESS_FQDN_PROVIDER)".
- IPv4 only. A managed database host name is resolved when the policy is written; if its address changes, `strict` blocks it until the next deploy or reconcile.

| Harness | Hosts runners reach |
|---|---|
| `claude`, `mclaude` | `api.anthropic.com` |
| `zai` | `api.z.ai` |
| `gemini` | `generativelanguage.googleapis.com` |
| `codex`, `code` | `api.openai.com`, `chatgpt.com`, `auth.openai.com` |
| `pi` | `api.anthropic.com`, `api.openai.com`, `generativelanguage.googleapis.com`, `api.z.ai` |

### 4. Expect ingress isolation (`network.ingress_isolation`)

Under `strict` (rule set 3) a project's pods admit connections only from:
- the Eve API (it fetches app API specs, delivers webhooks to private targets and runs `eve db` queries);
- the project's own env namespaces and runner pods;
- the ingress controller, to a service that has an Ingress, on the port it routes to;
- consumers of an app link with a live API grant for that env, to the granted service, on the link's port.

Consequences for the app:
- In-cluster calls from another project must come through an app link (`x-eve.app_links`; see `references/manifest.md`). A grant that names no service admits nothing.
- Public TCP listeners (`x-eve.tcp_ingress`) are not admitted.
- Eve's policies add to any NetworkPolicies the project writes. Under `strict`, a project's own rules that restrict traffic between its own pods no longer hold, because `eve-ingress` admits the project's own namespaces to every pod.

### 5. Use a declared database role in long-running services (`managed_db.owner_url`)

Under `strict` (rule set 3) a long-running service whose environment, command or entrypoint reads `${managed.<db>.url}` or `${managed.<db>.password}` is refused at deploy. Use a declared role. Job services (`x-eve.role: job`, migrations) still receive the owner credential; host, port, database and username are unaffected.

```yaml
services:
  api:
    environment:
      DATABASE_URL: ${managed.db.roles.app.url}    # declared role
  migrate:
    environment:
      DATABASE_URL: ${managed.db.url}              # owner, job service only
    x-eve:
      role: job
  db:
    x-eve:
      role: managed_db
      managed:
        class: db.p1
        engine: postgres
        engine_version: "16"
        roles:
          - name: app
            grants: readwrite                      # or readonly
          - name: auth
            grants: { schema: auth, owner: true }  # 0.1.330: owns one schema
```

- `readwrite` / `readonly` roles get grants in `public` (including on objects the owner creates later) and cannot create objects: run migrations as a `role: job` service with the owner URL, or give the component a schema-owner role.
- A schema-owner role (`grants: { schema: <name>, owner: true }`, from 0.1.330) suits a component that runs its own migrations in its own schema, such as an auth server. It has DDL in that schema and no grants of its own in `public`. The schema is a plain lower-case identifier, not `public`, `pg_*` or `information_schema`, with one owner role.
- Role names match `^[a-z][a-z0-9_]{0,15}$`; at most 8 roles per database; roles need the local managed DB provider.
- On every profile (from 0.1.328) each managed database accepts only its own logins, and each login without a connection limit gets its class's limit: 25 (`db.p1`), 100 (`db.p2`), 200 (`db.p3`). The database as a whole gets its class maximum plus 5 for Eve's own connections (30, 105, 205), set only when it has none, so a limit an operator set, lower or higher, stays. A login limit an operator set is never changed. Size each service's pool within its login's limit; scale the class (`eve db scale`) when near it.

### 6. Jobs and agents

- **Runner pods (`isolation.runner_mode`):** agent jobs, `script:` steps and `action: run` run in credential-free runner pods. A legacy pipeline-runner `run` step gets 400 ("use a `script:` step"). Outside Kubernetes, such work fails ("strict needs runner pods"). Runner pods mount no org filesystem. Before using its job token, a runner checks that the metadata server and the Kubernetes API are out of reach; if either answers, the job fails ("egress policy not enforced").
- **Secrets for jobs (`secrets.unbound_job`, `secrets.use`):** a job whose agent (`access.secrets`) and workflow step (`secrets:`) declare no allowlist receives only its harness provider keys and the keys its env overrides reference. Declare the secrets each job needs. Starting work bound to secrets beyond the harness credential needs `secrets:use`.
- **Harness selection:** name a harness or profile, or set `x-eve.defaults.harness_preference`; otherwise the job fails at dispatch ("no harness configured"). A harness credential counts only from project, org, system or job-actor secrets. Declared harness profiles and agents must exist and parse (`harness.missing_profile`, `harness.missing_agent`).
- **Reserved env names (`harness.reserved_env`):** env overrides may not set `LD_PRELOAD`, `LD_LIBRARY_PATH`, `DYLD_*`, `BASH_ENV`, `ENV`, `PYTHONPATH` or `PYTHONSTARTUP`, and may set `SSL_CERT_DIR`, `REQUESTS_CA_BUNDLE` and `CURL_CA_BUNDLE` only to a single `${secret.KEY}`. On every profile, `EVE_*`, `PATH`, `HOME`, `NODE_OPTIONS` and provider credentials cannot be overridden, and routing names (`*_BASE_URL`, proxies, TLS trust) take only a single `${secret.KEY}` from the project or org. User-scope secrets with routing or code-loading names are left out of a job's resolve (`harness.user_secret_routing`).
- **Checkout settings (`harness.checkout_settings`):** the run is refused, naming the file and setting, when the checkout sets model routing or credential handoff: a Claude Code config directory in the checkout with a routing name or `apiKeyHelper` in `settings.json`; a Codex `.codex/config.toml` or checkout config home that sets `profile`, `model_provider`, `model_providers`, any `*base_url` or a routing env override; any `.env` in a Codex config home inside the checkout; routing names in `.gemini/.env`; an MCP server in the checkout handed a harness or Eve credential (job token, app-link tokens, internal keys) or a routing name; a settings file the scan cannot parse; and the `code` and `pi` harnesses. Eve never edits checkout files. See `references/harnesses.md`.
- **Approvals (`jobs.approve_token`, `pipelines.approve_token`):** approving or rejecting a job review, and `eve pipeline approve`, need a person's own token; job, service, service-principal and app-link tokens get 403. Changes to a workflow or pipeline run's jobs come only from the run's actor, its own step tokens, or a project admin (`jobs:admin`).
- **Chat (`chat.sender_rights`):** chat starts agent work only for a sender mapped to an Eve user with `jobs:write` on the project.
- **Org data (`access.org_data_*`, governed by the org):** a job gets org files, docs or cloud-fs only through a declared `scope` judged for the user who started it; work no user started (events, schedules) gets none.

### 7. Secrets and permissions

- **Write-only values (`secrets.value_read`):** `eve secrets export` gets 403. `ensure` shows a generated value once, in its own response; `eve github setup` returns the webhook secret only when it creates it (or with `--regenerate`).
- **System secrets (`secrets.system_scope`):** a system secret reaches the project only when the instance shares it by name (`eve system settings set secrets.system_shared_keys "KEY_A,KEY_B"`). Otherwise move it to org or project scope.
- **Credentials jobs refresh (`secrets.job_org_write`):** a job may update only a project-scope copy. Keep a project-scope copy of a credential the project's jobs refresh, for example `CODEX_AUTH_JSON_B64`.
- **GitHub webhooks (`webhooks.global_secret`):** each project needs its own `GITHUB_WEBHOOK_SECRET` (`eve github setup`).
- **Operator rights (`access.operator_rights`):** `secrets:use` and `envdb:export` come only from admin and owner roles, bindings and custom roles, and `jobs:harness_override` leaves the member role and default job permissions. Grant them before the flip:

```bash
eve access roles create operator --org <org_id> --scope project --permissions secrets:use,envdb:export,jobs:harness_override
eve access bind --org <org_id> --project <project_id> --user <user_id> --role operator
```

- **Project access (`access.implicit_project`):** an org `member` gets nothing on a project from the org role alone; add a project membership or a binding.
- **On every profile (from 0.1.327):** `environment` values that reference `${secret.*}` or `${managed.*}`, `EVE_SERVICE_TOKEN` and app-link tokens reach a service through its own Secret (`<env>-<service>-eve-secrets`, read with `secretKeyRef`); the container sees the same env var names and values. A service job reads them from a Secret made for the run.

### 8. Manifest environments (`manifest.approval_unenforced`)

Eve has no deploy approval gate. Under `strict` (rule set 4) manifest sync refuses (400) an `environments.<env>.approval` that asks for one (any value other than `none` in any case, or blank): "Environment approval gates are not enforced by Eve. Remove `approval` from environments.<env>." Under `audit` it syncs with that text as a warning. Remove the key.

### 9. Apps that sign in through Eve SSO (instance profile)

Under a `strict` instance, the SSO serves `/session` to an app only from one of its own project's `https` hosts (env default hosts, ingress aliases, verified custom domains, `x-eve.auth.allowed_redirect_origins`), only for a person the app's access policy admits, and returns an app audience token: `aud` `eve-app:<project>[:<env>]`, no `orgs` claim, one-hour lifetime. Upgrade `@eve-horizon/auth` before the instance moves to `strict`. See `references/auth-sdk.md`.

## Refusals and Fixes

| Symptom | Rule | Fix |
|---|---|---|
| Routed service has no host or returns 404 after the flip | `ports.implicit_public` | Add `x-eve.ingress.public: true` (and `port`) and redeploy |
| Deploy or job refused, naming `x-eve.security.run_as_user` | `isolation.pod_security` | Numeric non-root `USER` in the Dockerfile, or `run_as_user`/`run_as_group`; `run_as_root` only as an opt-out |
| Deploy refused for `x-eve.networking.egress: stable` | `isolation.pod_security` | Remove stable egress from the service |
| "egress allow lists need a provider (EVE_EGRESS_FQDN_PROVIDER)" | `egress.allow_hosts` | Platform operator sets the provider; until then keep the project on `audit` |
| Outbound call to an internet host times out | `egress.allow_hosts` | Add it to `x-eve.egress.hosts`; `eve project sync` (runners) and redeploy (services) |
| Call from another project's pods refused | `network.ingress_isolation` | Export the API through an app link with a grant for that env |
| Deploy refused, naming `${managed.<db>.roles.<role>.url}` | `managed_db.owner_url` | Declare a role and use its URL; keep the owner URL in `role: job` services |
| Sync 400: "Environment approval gates are not enforced by Eve" | `manifest.approval_unenforced` | Remove `environments.<env>.approval` |
| Job fails "no harness configured" | `harness.no_preference` | Name a harness or profile, or set `x-eve.defaults.harness_preference` |
| Run fails naming a checkout file and setting | `harness.checkout_settings` | Remove the routing or credential setting from that file |
| Job fails "egress policy not enforced" | `isolation.runner_mode` | Platform issue: NetworkPolicy is not enforced; ask the operator |
| Job refused: runner egress policies "could not be written" | `egress.default_deny` | Platform issue; retry, then ask the operator |
| 400 "use a `script:` step" | `isolation.runner_mode` | Convert the legacy `run` step to `script:` |
| Job lacks a secret it used to get | `secrets.unbound_job` | Declare it in agent `access.secrets` or step `secrets:` |
| 403 starting a job bound to secrets | `secrets.use` | Grant `secrets:use` (custom role or binding) |
| 403 on `eve secrets export` | `secrets.value_read` | Values are write-only; rotate and set the value where it is needed |

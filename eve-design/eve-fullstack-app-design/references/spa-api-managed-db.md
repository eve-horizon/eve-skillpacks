# SPA + API + Managed Postgres Reference

Load this reference when implementing the common Eve full-stack topology: a
public nginx SPA, an internal Node API, managed Postgres, and a one-off SQL
migration service.

## Service Topology

```text
web      nginx SPA; public ingress; proxies /api/ to api
api      NestJS or Express backend; cluster-internal only
db       managed Postgres 16
migrate  one-off SQL migration job
```

Keep the API private and proxy it through nginx. This gives the SPA same-origin
API access without CORS or a hard-coded public API hostname.

```yaml
services:
  api:
    build:
      context: ./apps/api
      dockerfile: ./apps/api/Dockerfile
    ports: [3000]
    environment:
      NODE_ENV: production
      DATABASE_URL: ${managed.db.url}

  web:
    build:
      context: ./apps/web
      dockerfile: ./apps/web/Dockerfile
    ports: [80]
    environment:
      API_SERVICE_HOST: ${ENV_NAME}-api
    depends_on:
      api:
        condition: service_healthy
    x-eve:
      ingress:
        public: true
        port: 80
        alias: myapp

  migrate:
    image: public.ecr.aws/w7c4v0w3/eve-horizon/migrate:latest
    environment:
      DATABASE_URL: ${managed.db.url}
      MIGRATIONS_DIR: /migrations
    x-eve:
      role: job
      files:
        - source: db/migrations
          target: /migrations

  db:
    x-eve:
      role: managed_db
      managed:
        class: db.p1
        engine: postgres
        engine_version: "16"
```

The standalone migration runner is an app job service, not one of the seven
versioned platform service images published by `release-v*`. Pin a known app
artifact where your release process requires reproducibility.

## nginx Proxy

Use an nginx template so `${API_SERVICE_HOST}` resolves at container startup:

```nginx
server {
    listen 80;
    root /usr/share/nginx/html;
    index index.html;

    location /api/ {
        proxy_pass http://${API_SERVICE_HOST}:3000/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_buffering off;
    }

    location / { try_files $uri $uri/ /index.html; }
    location /health {
        return 200 "ok";
        add_header Content-Type text/plain;
    }
}
```

## Container Builds

Use multi-stage builds, frozen lockfiles, a non-root API runtime, and health
checks for both services.

```dockerfile
# API Dockerfile
FROM node:22-slim AS base
WORKDIR /app
ENV PNPM_HOME="/pnpm" PATH="$PNPM_HOME:$PATH"
RUN corepack enable && corepack prepare pnpm@latest --activate

FROM base AS deps
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

FROM deps AS build
COPY tsconfig.json ./
COPY src ./src
RUN pnpm build

FROM node:22-slim AS production
WORKDIR /app
RUN groupadd --gid 1000 node && useradd --uid 1000 --gid node --create-home node
COPY --from=deps /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY package.json ./
USER node
ENV NODE_ENV=production PORT=3000
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD node -e "fetch('http://localhost:3000/health').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"
CMD ["node", "dist/main.js"]
```

```dockerfile
# Web Dockerfile
FROM node:22-slim AS build
WORKDIR /app
ENV PNPM_HOME="/pnpm" PATH="$PNPM_HOME:$PATH"
RUN corepack enable && corepack prepare pnpm@latest --activate
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN pnpm build

FROM nginx:alpine AS production
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider http://localhost/health || exit 1
CMD ["nginx", "-g", "daemon off;"]
```

## Managed Database and Migrations

Use `${managed.db.url}` as the connection string. The platform injects the
managed CA bundle and sets `NODE_EXTRA_CA_CERTS` and `PGSSLROOTCERT`; keep the
URL's `sslmode=verify-full` and do not disable certificate verification in app
code.

```typescript
import { Pool } from 'pg';

export const pool = new Pool({
  connectionString:
    process.env.DATABASE_URL ?? 'postgresql://app:app@localhost:5432/myapp',
});
```

Store timestamp-prefixed plain SQL under `db/migrations/`. Run migrations after
the first deploy because the managed database is provisioned during deploy.
Never edit a production schema by hand.

## Multi-Tenant RLS Pattern

Give every tenant-owned table `org_id TEXT NOT NULL`. Wrap each request's work
in a transaction and set transaction-local tenant/user values before executing
queries:

```typescript
import type { PoolClient, QueryResult, QueryResultRow } from 'pg';
import { pool } from '../db';

export interface DbContext { org_id: string; user_id?: string }

export class DatabaseService {
  async withClient<T>(context: DbContext, fn: (client: PoolClient) => Promise<T>): Promise<T> {
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      await client.query("SELECT set_config('app.org_id', $1, true)", [context.org_id]);
      if (context.user_id) {
        await client.query("SELECT set_config('app.user_id', $1, true)", [context.user_id]);
      }
      const result = await fn(client);
      await client.query('COMMIT');
      return result;
    } catch (error) {
      await client.query('ROLLBACK');
      throw error;
    } finally {
      client.release();
    }
  }

  query<T extends QueryResultRow>(ctx: DbContext, sql: string, params?: unknown[]): Promise<QueryResult<T>> {
    return this.withClient(ctx, client => client.query<T>(sql, params));
  }
}
```

`set_config(..., true)` is transaction-scoped, so values clear before the
connection returns to the pool. Derive `DbContext` from the authenticated Eve
request identity and route every tenant query through the wrapper.

```sql
ALTER TABLE my_table ENABLE ROW LEVEL SECURITY;

CREATE POLICY my_table_select ON my_table FOR SELECT
  USING (current_setting('app.org_id', true) IS NOT NULL
    AND org_id = current_setting('app.org_id', true));

CREATE POLICY my_table_insert ON my_table FOR INSERT
  WITH CHECK (current_setting('app.org_id', true) IS NOT NULL
    AND org_id = current_setting('app.org_id', true));

CREATE POLICY my_table_update ON my_table FOR UPDATE
  USING (current_setting('app.org_id', true) IS NOT NULL
    AND org_id = current_setting('app.org_id', true))
  WITH CHECK (current_setting('app.org_id', true) IS NOT NULL
    AND org_id = current_setting('app.org_id', true));
```

Use UUID primary keys, `created_at`, and `updated_at` consistently. Keep app
product data separate from agent memory/coordination data. Inspect schema with
`eve db schema`; use `eve db sql --env <env>` for controlled development
queries.

CREATE TABLE pools (
 id text PRIMARY KEY, address text NOT NULL UNIQUE, paused boolean NOT NULL DEFAULT false,
 version bigint NOT NULL DEFAULT 0, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE quotes (
 id text PRIMARY KEY, tenant_id text NOT NULL, resource_type text NOT NULL CHECK(resource_type IN ('ENERGY','BANDWIDTH')),
 target_units bigint NOT NULL CHECK(target_units>0), stake_sun bigint NOT NULL CHECK(stake_sun>=1000000),
 duration_seconds bigint NOT NULL CHECK(duration_seconds>0), price_sun bigint NOT NULL CHECK(price_sun>0),
 price_version text NOT NULL, expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE orders (
 id text PRIMARY KEY, tenant_id text NOT NULL, quote_id text NOT NULL REFERENCES quotes(id),
 receiver text NOT NULL, resource_type text NOT NULL, stake_sun bigint NOT NULL CHECK(stake_sun>=1000000),
 pool_id text REFERENCES pools(id), duration_seconds bigint NOT NULL, price_sun bigint NOT NULL,
 status text NOT NULL CHECK(status IN ('PENDING_APPROVAL','RESERVED','DELEGATING','ACTIVE','RECLAIMING','RECONCILING','COMPLETED','CANCELLED','FAILED','EXPIRED')),
 version bigint NOT NULL DEFAULT 0, expires_at timestamptz, delivered_at timestamptz,
 settlement_reference text, refund_status text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX active_delegation ON orders(pool_id,receiver,resource_type)
 WHERE pool_id IS NOT NULL AND status IN ('RESERVED','DELEGATING','ACTIVE','RECLAIMING','RECONCILING');
CREATE INDEX tenant_orders ON orders(tenant_id,created_at);
CREATE INDEX due_orders ON orders(expires_at) WHERE status='ACTIVE';
CREATE TABLE reservations (
 order_id text PRIMARY KEY REFERENCES orders(id), pool_id text NOT NULL REFERENCES pools(id),
 resource_type text NOT NULL, stake_sun bigint NOT NULL CHECK(stake_sun>0),
 status text NOT NULL CHECK(status IN ('HELD','COMMITTED','RELEASED'))
);
CREATE TABLE idempotency_keys (
 tenant_id text NOT NULL, route text NOT NULL, key text NOT NULL, request_hash text NOT NULL,
 response jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(tenant_id,route,key)
);
CREATE TABLE order_events (
 event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 order_id text NOT NULL REFERENCES orders(id), version bigint NOT NULL,
 type text NOT NULL, payload jsonb NOT NULL DEFAULT '{}', occurred_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(order_id,version)
);
CREATE TABLE audit_events (
 event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 actor text NOT NULL, action text NOT NULL, target text NOT NULL,
 details jsonb NOT NULL DEFAULT '{}', request_id text,
 occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE outbox_tasks (
 id text PRIMARY KEY, order_id text NOT NULL REFERENCES orders(id),
 action text NOT NULL CHECK(action IN ('DELEGATE','RECLAIM')),
 status text NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','RUNNING','DONE','BLOCKED')),
 available_at timestamptz NOT NULL DEFAULT now(), owner text, lease_until timestamptz,
 claim_version bigint NOT NULL DEFAULT 0, attempts int NOT NULL DEFAULT 0, last_error text,
 UNIQUE(order_id,action)
);
CREATE INDEX ready_tasks ON outbox_tasks(available_at) WHERE status IN ('PENDING','RUNNING');
CREATE TABLE chain_operations (
 id text PRIMARY KEY, order_id text NOT NULL, action text NOT NULL,
 status text NOT NULL DEFAULT 'PREPARED', txid text UNIQUE, raw_tx jsonb, signed_tx jsonb,
 block_number bigint, block_timestamp bigint, last_error text,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(order_id,action)
);
CREATE TABLE resource_snapshots (
 pool_id text NOT NULL REFERENCES pools(id), resource_type text NOT NULL,
 max_delegatable_sun bigint NOT NULL, block_number bigint NOT NULL,
 observed_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(pool_id,resource_type)
);

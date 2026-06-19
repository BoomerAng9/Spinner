-- Spinner — Neon Postgres foundational schema (001)
-- Pricing-independent core: global profile/memory, token ledger, entitlements.
-- Exact tier prices/limits are app-config, NOT schema — so this is stable.
-- All statements idempotent (safe to re-run).

-- Global profile — links a Spinner user to their CTI/ecosystem identity. Enables memory.
create table if not exists spinner_profile (
    user_id        text primary key,              -- ecosystem uid (e.g. signed coastal_uid)
    cti_profile_id text,                           -- link to CTI profile (global profile)
    display_name   text,
    prefs          jsonb not null default '{}'::jsonb,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);

-- Memory — the user's recallable activity log (RAG source). User-owned; paid feature.
-- Privacy: sensitive business-interview data is NOT stored here (ephemeral, separate).
create table if not exists spinner_memory (
    id         bigserial primary key,
    user_id    text not null references spinner_profile(user_id) on delete cascade,
    kind       text not null,                      -- translate | research | summarize | fork | general | ...
    content    text not null,
    metadata   jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);
create index if not exists idx_spinner_memory_user_time on spinner_memory(user_id, created_at desc);

-- Token ledger — grants (BMC, plans) and consumption; balance is derived.
create table if not exists spinner_token_ledger (
    id            bigserial primary key,
    user_id       text not null references spinner_profile(user_id) on delete cascade,
    event         text not null,                   -- grant | consume | adjust
    tokens        bigint not null,                 -- +grant / -consume
    model         text,                            -- which model consumed (deepseek-v4-flash, ...)
    reference     text,                            -- bmc | plan_drip | appsumo_t1 | task:<id> ...
    created_at    timestamptz not null default now()
);
create index if not exists idx_token_ledger_user_time on spinner_token_ledger(user_id, created_at desc);

-- Entitlement — current tier/state per user. Tier names are stable; prices live in app config.
create table if not exists spinner_entitlement (
    user_id      text primary key references spinner_profile(user_id) on delete cascade,
    tier         text not null default 'free',     -- free | bmc | drip | flow | current | appsumo_t1|t2|t3
    status       text not null default 'active',   -- active | expired | canceled
    source       text,                             -- stripe | appsumo | conversion
    activated_at timestamptz,
    expires_at   timestamptz,                       -- null = no expiry (e.g. free, bmc one-time unlock)
    updated_at   timestamptz not null default now()
);

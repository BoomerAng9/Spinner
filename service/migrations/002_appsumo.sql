-- Spinner — AppSumo redemption codes (002). Idempotent.
-- Codes are provisioned (status='unused') and flipped to 'redeemed' on use.
create table if not exists spinner_appsumo_code (
    code        text primary key,
    tier        text not null,                 -- appsumo_t1 | appsumo_t2 | appsumo_t3
    status      text not null default 'unused', -- unused | redeemed | revoked
    redeemed_by text,                            -- spinner_profile.user_id
    redeemed_at timestamptz,
    created_at  timestamptz not null default now()
);
create index if not exists idx_appsumo_code_status on spinner_appsumo_code(status);

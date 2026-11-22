-- TX KPI Monitor database schema. Safe to run repeatedly.

CREATE TABLE IF NOT EXISTS users (
    id             serial PRIMARY KEY,
    username       text NOT NULL UNIQUE,
    full_name      text NOT NULL DEFAULT '',
    password_hash  text NOT NULL,
    role           text NOT NULL CONSTRAINT users_role_check CHECK (role IN ('admin', 'user')),
    is_active      boolean NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now(),
    last_login_at  timestamptz
);

-- Two roles only. Accounts from the earlier three-role set-up (editor, viewer) become users.
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
UPDATE users SET role = 'user' WHERE role NOT IN ('admin', 'user');
ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN ('admin', 'user'));

-- Set when an administrator chooses someone's password (new account or reset): they must pick their own
-- the next time they sign in.
ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password boolean NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS settings (
    key         text PRIMARY KEY,
    value       jsonb NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  int REFERENCES users (id) ON DELETE SET NULL
);

-- One row per monitored entity: a ZTE managed element, or a Huawei IPPM task.
CREATE TABLE IF NOT EXISTS sites (
    id          serial PRIMARY KEY,
    source      text NOT NULL,
    ext_key     text NOT NULL,          -- ZTE: managed element ID; Huawei: task name (lower case)
    name        text NOT NULL,          -- latest name seen in the data
    category    text NOT NULL DEFAULT 'OS',
    first_seen  date NOT NULL,
    last_seen   date NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, ext_key)
);
CREATE INDEX IF NOT EXISTS sites_name_idx ON sites (lower(name));
CREATE INDEX IF NOT EXISTS sites_first_seen_idx ON sites (first_seen);

-- Every name a site has carried, so renames keep their history.
CREATE TABLE IF NOT EXISTS site_names (
    site_id     int NOT NULL REFERENCES sites (id) ON DELETE CASCADE,
    name        text NOT NULL,
    first_seen  date NOT NULL,
    last_seen   date NOT NULL,
    PRIMARY KEY (site_id, name)
);

-- An IP path (ZTE: source -> destination IP) or measuring node (Huawei: UGW) of a site.
CREATE TABLE IF NOT EXISTS paths (
    id       serial PRIMARY KEY,
    site_id  int NOT NULL REFERENCES sites (id) ON DELETE CASCADE,
    label    text NOT NULL,
    UNIQUE (site_id, label)
);

CREATE TABLE IF NOT EXISTS import_files (
    id              serial PRIMARY KEY,
    report_date     date,
    source          text,
    filename        text NOT NULL,
    sha256          text,
    size_bytes      bigint,
    row_count       int,
    site_count      int,
    new_site_count  int,
    renamed_count   int,
    is_initial      boolean NOT NULL DEFAULT false,   -- first ever load of this source
    hour_min        timestamp,
    hour_max        timestamp,
    status          text NOT NULL,                    -- ok / replaced / duplicate / failed
    message         text,
    stored_path     text,
    imported_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS import_files_date_idx ON import_files (report_date, source);

-- Raw hourly measurements exactly as received, per path.
CREATE TABLE IF NOT EXISTS kpi_hourly (
    report_date  date NOT NULL,              -- the day the data was received / reported
    path_id      int NOT NULL REFERENCES paths (id) ON DELETE CASCADE,
    hour_ts      timestamp NOT NULL,         -- start of the measured hour
    pd_pct       double precision,           -- packet drop, percent
    rtt_ms       real,
    jitter_ms    real,
    tx_packets   bigint,
    PRIMARY KEY (path_id, hour_ts, report_date)
);
CREATE INDEX IF NOT EXISTS kpi_hourly_date_brin ON kpi_hourly USING brin (report_date);

-- Per site and report day: worst-path values per hour, rolled up.
CREATE TABLE IF NOT EXISTS site_daily (
    report_date   date NOT NULL,
    site_id       int NOT NULL REFERENCES sites (id) ON DELETE CASCADE,
    samples       smallint NOT NULL,         -- hours with data
    path_count    smallint NOT NULL,
    pd_hours      smallint NOT NULL,         -- hours at or above the threshold
    jitter_hours  smallint NOT NULL,
    rtt_hours     smallint NOT NULL,
    pd_max        double precision,
    pd_avg        double precision,
    rtt_max       real,
    rtt_avg       real,
    jitter_max    real,
    jitter_avg    real,
    PRIMARY KEY (report_date, site_id)
);
CREATE INDEX IF NOT EXISTS site_daily_site_idx ON site_daily (site_id, report_date);

CREATE TABLE IF NOT EXISTS reports (
    id               serial PRIMARY KEY,
    report_date      date NOT NULL UNIQUE,
    is_seed          boolean NOT NULL DEFAULT false,   -- loaded from an old Excel report
    generated_at     timestamptz NOT NULL DEFAULT now(),
    generated_by     int REFERENCES users (id) ON DELETE SET NULL,
    thresholds       jsonb NOT NULL DEFAULT '{}',
    sources_present  text[] NOT NULL DEFAULT '{}',
    sources_missing  text[] NOT NULL DEFAULT '{}',
    site_count       int NOT NULL DEFAULT 0,
    new_site_count   int NOT NULL DEFAULT 0,
    pd_count         int NOT NULL DEFAULT 0,
    jitter_count     int NOT NULL DEFAULT 0,
    rtt_count        int NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS report_items (
    id                  bigserial PRIMARY KEY,
    report_id           int NOT NULL REFERENCES reports (id) ON DELETE CASCADE,
    kpi                 text NOT NULL CHECK (kpi IN ('PD', 'JITTER', 'RTT')),
    site_id             int REFERENCES sites (id) ON DELETE SET NULL,
    site_name           text NOT NULL,
    category            text NOT NULL,
    sample_count        int NOT NULL,
    pending_days        int NOT NULL DEFAULT 0,
    feedback            text NOT NULL,
    feedback_edited_at  timestamptz,
    feedback_edited_by  int REFERENCES users (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS report_items_report_idx ON report_items (report_id, kpi);
CREATE INDEX IF NOT EXISTS report_items_site_idx ON report_items (site_id);

CREATE TABLE IF NOT EXISTS activity_log (
    id          bigserial PRIMARY KEY,
    at          timestamptz NOT NULL DEFAULT now(),
    user_id     int REFERENCES users (id) ON DELETE SET NULL,
    username    text,
    action      text NOT NULL,
    detail      text
);

-- Files already fetched from the dump server, so a routine check does not fetch them twice.
CREATE TABLE IF NOT EXISTS remote_files (
    name        text NOT NULL,
    size_bytes  bigint NOT NULL,
    modified_at timestamptz NOT NULL,
    fetched_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (name, size_bytes, modified_at)
);

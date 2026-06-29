CREATE TABLE IF NOT EXISTS defaults (
    id            INTEGER PRIMARY KEY DEFAULT 1,
    course        TEXT,
    course1_name  TEXT,
    course1_price TEXT,
    course2_name  TEXT,
    course2_price TEXT,
    course3_name  TEXT,
    course3_price TEXT,
    updated_at    TEXT NOT NULL
);

-- 曜日ごとのデフォルト設定 (0=月, 1=火, ..., 6=日)
CREATE TABLE IF NOT EXISTS weekday_defaults (
    weekday      INTEGER PRIMARY KEY,
    is_closed    INTEGER NOT NULL DEFAULT 0,
    start_time_1 TEXT,
    capacity_1   INTEGER,
    start_time_2 TEXT,
    capacity_2   INTEGER,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS day_config (
    date               TEXT PRIMARY KEY,
    is_closed          INTEGER NOT NULL DEFAULT 0,
    is_manual_override INTEGER NOT NULL DEFAULT 0,
    course             TEXT,
    start_time_1       TEXT,
    capacity_1         INTEGER,
    start_time_2       TEXT,
    capacity_2         INTEGER,
    updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reservation (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    rotation    INTEGER NOT NULL CHECK (rotation IN (1, 2)),
    name        TEXT NOT NULL,
    num_people  INTEGER NOT NULL CHECK (num_people >= 1),
    phone       TEXT NOT NULL,
    email       TEXT NOT NULL,
    note        TEXT,
    course_name  TEXT,
    course_price TEXT,
    confirmed   INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'pending_verify'
                CHECK (status IN ('active', 'cancelled', 'pending_verify')),
    verification_token TEXT,
    token_expires_at   TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_res_date_status ON reservation(date, status);

-- 来店前アンケート
CREATE TABLE IF NOT EXISTS survey_response (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id      INTEGER NOT NULL UNIQUE,
    source              TEXT,           -- 来店経緯: terujii/ourdays/yukawa/other
    source_other        TEXT,
    visit_count         TEXT,           -- 来店回数: first/2nd/3rd_plus
    is_member           INTEGER,        -- 会員: 1/0
    looking_forward     TEXT,           -- 楽しみにしていること
    allergy             TEXT,           -- アレルギー
    disliked_food       TEXT,           -- 苦手な食材
    nonalcoholic_count  INTEGER NOT NULL DEFAULT 0,  -- ノンアル希望人数
    info_preference     TEXT,           -- 情報配信: none/email/line/other
    info_other          TEXT,
    other_questions     TEXT,           -- その他質問
    payment_method      TEXT,           -- 支払方法: in_store/transfer
    transfer_name       TEXT,           -- 振込人名義（事前振込の場合）
    terms_agreed        INTEGER NOT NULL DEFAULT 0,  -- 規約同意
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

-- IP別リクエスト回数（日次レート制限）
CREATE TABLE IF NOT EXISTS rate_limit (
    ip    TEXT NOT NULL,
    date  TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ip, date)
);

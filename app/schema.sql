CREATE TABLE IF NOT EXISTS defaults (
    id          INTEGER PRIMARY KEY DEFAULT 1,
    course_1    TEXT,
    course_2    TEXT,
    course_3    TEXT,
    start_time_1 TEXT NOT NULL DEFAULT '19:00',
    capacity_1  INTEGER NOT NULL DEFAULT 6,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS day_config (
    date               TEXT PRIMARY KEY,
    is_closed          INTEGER NOT NULL DEFAULT 0,
    is_manual_override INTEGER NOT NULL DEFAULT 0,
    course_1           TEXT,
    course_2           TEXT,
    course_3           TEXT,
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
    confirmed   INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'cancelled')),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_res_date_status ON reservation(date, status);

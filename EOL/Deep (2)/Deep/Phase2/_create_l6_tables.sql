-- Per-machine tables for L6 Seat Slider line
-- Each L108/L109 rise gets its own row in its own machine's table.
-- Final Inspection has status + model columns (those come from main PLC only).

CREATE TABLE IF NOT EXISTS mes_l6_final_inspection (
  id            BIGSERIAL PRIMARY KEY,
  ts            TIMESTAMP NOT NULL,
  bit_type      VARCHAR(8) NOT NULL,
  bit_address   VARCHAR(16),
  ct_seconds    NUMERIC(10,3),
  counter_val   INT NOT NULL,
  part_code     VARCHAR(64),
  shift_name    VARCHAR(8),
  record_date   DATE NOT NULL,
  status_code   INT,
  status_name   VARCHAR(20),
  model_no      INT,
  model_name    VARCHAR(80),
  created_at    TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mes_l6_upper_rail (
  id BIGSERIAL PRIMARY KEY, ts TIMESTAMP NOT NULL,
  bit_type VARCHAR(8) NOT NULL, bit_address VARCHAR(16),
  ct_seconds NUMERIC(10,3), counter_val INT NOT NULL,
  shift_name VARCHAR(8), record_date DATE NOT NULL,
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mes_l6_lower_rail (
  id BIGSERIAL PRIMARY KEY, ts TIMESTAMP NOT NULL,
  bit_type VARCHAR(8) NOT NULL, bit_address VARCHAR(16),
  ct_seconds NUMERIC(10,3), counter_val INT NOT NULL,
  shift_name VARCHAR(8), record_date DATE NOT NULL,
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mes_l6_semi_auto (
  id BIGSERIAL PRIMARY KEY, ts TIMESTAMP NOT NULL,
  bit_type VARCHAR(8) NOT NULL, bit_address VARCHAR(16),
  ct_seconds NUMERIC(10,3), counter_val INT NOT NULL,
  shift_name VARCHAR(8), record_date DATE NOT NULL,
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mes_l6_ball_guide_13 (
  id BIGSERIAL PRIMARY KEY, ts TIMESTAMP NOT NULL,
  bit_type VARCHAR(8) NOT NULL, bit_address VARCHAR(16),
  ct_seconds NUMERIC(10,3), counter_val INT NOT NULL,
  shift_name VARCHAR(8), record_date DATE NOT NULL,
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mes_l6_ball_guide_14 (
  id BIGSERIAL PRIMARY KEY, ts TIMESTAMP NOT NULL,
  bit_type VARCHAR(8) NOT NULL, bit_address VARCHAR(16),
  ct_seconds NUMERIC(10,3), counter_val INT NOT NULL,
  shift_name VARCHAR(8), record_date DATE NOT NULL,
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mes_l6_lock_bar (
  id BIGSERIAL PRIMARY KEY, ts TIMESTAMP NOT NULL,
  bit_type VARCHAR(8) NOT NULL, bit_address VARCHAR(16),
  ct_seconds NUMERIC(10,3), counter_val INT NOT NULL,
  shift_name VARCHAR(8), record_date DATE NOT NULL,
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_l6_fi_ts   ON mes_l6_final_inspection(ts DESC);
CREATE INDEX IF NOT EXISTS idx_l6_fi_date ON mes_l6_final_inspection(record_date, shift_name, bit_type);
CREATE INDEX IF NOT EXISTS idx_l6_ur_ts   ON mes_l6_upper_rail(ts DESC);
CREATE INDEX IF NOT EXISTS idx_l6_lr_ts   ON mes_l6_lower_rail(ts DESC);
CREATE INDEX IF NOT EXISTS idx_l6_sa_ts   ON mes_l6_semi_auto(ts DESC);
CREATE INDEX IF NOT EXISTS idx_l6_bg13_ts ON mes_l6_ball_guide_13(ts DESC);
CREATE INDEX IF NOT EXISTS idx_l6_bg14_ts ON mes_l6_ball_guide_14(ts DESC);
CREATE INDEX IF NOT EXISTS idx_l6_lb_ts   ON mes_l6_lock_bar(ts DESC);

SELECT table_name FROM information_schema.tables
WHERE table_schema='public' AND table_name LIKE 'mes_l6_%'
ORDER BY table_name;

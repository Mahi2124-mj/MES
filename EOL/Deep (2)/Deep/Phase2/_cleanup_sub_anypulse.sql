-- Remove the bogus 100s+ rows from sub-machines (post any-pulse change)
WITH del_sub AS (
    DELETE FROM mes_submachine_ct_log
    WHERE record_date = CURRENT_DATE
      AND ts_end > '2026-05-27 14:20:00'
      AND ct_seconds > 60.0
    RETURNING sub_plc_id, ct_seconds
),
del_a AS (
    DELETE FROM mes_l6_upper_rail
    WHERE record_date = CURRENT_DATE
      AND ts > '2026-05-27 14:20:00'
      AND ct_seconds > 60.0
    RETURNING 1
),
del_b AS (
    DELETE FROM mes_l6_lower_rail
    WHERE record_date = CURRENT_DATE
      AND ts > '2026-05-27 14:20:00'
      AND ct_seconds > 60.0
    RETURNING 1
),
del_c AS (
    DELETE FROM mes_l6_lock_bar
    WHERE record_date = CURRENT_DATE
      AND ts > '2026-05-27 14:20:00'
      AND ct_seconds > 60.0
    RETURNING 1
),
del_d AS (
    DELETE FROM mes_l6_ball_guide_13
    WHERE record_date = CURRENT_DATE
      AND ts > '2026-05-27 14:20:00'
      AND ct_seconds > 60.0
    RETURNING 1
),
del_e AS (
    DELETE FROM mes_l6_ball_guide_14
    WHERE record_date = CURRENT_DATE
      AND ts > '2026-05-27 14:20:00'
      AND ct_seconds > 60.0
    RETURNING 1
),
del_f AS (
    DELETE FROM mes_l6_semi_auto
    WHERE record_date = CURRENT_DATE
      AND ts > '2026-05-27 14:20:00'
      AND ct_seconds > 60.0
    RETURNING 1
)
SELECT
    (SELECT COUNT(*) FROM del_sub) AS sub_log_deleted,
    (SELECT COUNT(*) FROM del_a)   AS upper_rail,
    (SELECT COUNT(*) FROM del_b)   AS lower_rail,
    (SELECT COUNT(*) FROM del_c)   AS lock_bar,
    (SELECT COUNT(*) FROM del_d)   AS ball_guide_13,
    (SELECT COUNT(*) FROM del_e)   AS ball_guide_14,
    (SELECT COUNT(*) FROM del_f)   AS semi_auto;

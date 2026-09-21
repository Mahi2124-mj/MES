-- 2026-05-28 — Full re-sync of ync_dashboard_complete from L6 audit truth.
-- L6 audit is source of truth (1 row per real pulse, garbage-PC filtered).
-- This SQL recomputes every shift counter + every hourly slot OK/NG column
-- from L6 audit rows.  Run with collector STOPPED (otherwise its in-memory
-- counter will overwrite on next tick).

-- ── Shift totals ─────────────────────────────────────────────────
UPDATE ync_dashboard_complete d
SET ok_count = COALESCE(c.ok_cnt, 0),
    ng_count = COALESCE(c.ng_cnt, 0)
FROM (
    SELECT shift_name,
           COUNT(*) FILTER (WHERE bit_type='OK') AS ok_cnt,
           COUNT(*) FILTER (WHERE bit_type='NG') AS ng_cnt
    FROM mes_l6_final_inspection
    WHERE record_date = CURRENT_DATE
    GROUP BY shift_name
) c
WHERE d.record_date = CURRENT_DATE
  AND (d.shift_name = c.shift_name
       OR (c.shift_name = 'GAP' AND d.shift_name LIKE 'GAP_%'));

-- ── Hourly slot columns (recompute each one from time window) ────
-- Shift A (08:30 - 17:20): 7 slots
UPDATE ync_dashboard_complete SET
    hour_0830_0930_ok = (SELECT COUNT(*) FROM mes_l6_final_inspection
        WHERE record_date=CURRENT_DATE AND bit_type='OK'
          AND ts::time >= '08:30' AND ts::time < '09:30'),
    hour_0830_0930_ng = (SELECT COUNT(*) FROM mes_l6_final_inspection
        WHERE record_date=CURRENT_DATE AND bit_type='NG'
          AND ts::time >= '08:30' AND ts::time < '09:30'),
    hour_0930_1030_ok = (SELECT COUNT(*) FROM mes_l6_final_inspection
        WHERE record_date=CURRENT_DATE AND bit_type='OK'
          AND ts::time >= '09:30' AND ts::time < '10:30'),
    hour_0930_1030_ng = (SELECT COUNT(*) FROM mes_l6_final_inspection
        WHERE record_date=CURRENT_DATE AND bit_type='NG'
          AND ts::time >= '09:30' AND ts::time < '10:30'),
    hour_1030_1130_ok = (SELECT COUNT(*) FROM mes_l6_final_inspection
        WHERE record_date=CURRENT_DATE AND bit_type='OK'
          AND ts::time >= '10:30' AND ts::time < '11:30'),
    hour_1030_1130_ng = (SELECT COUNT(*) FROM mes_l6_final_inspection
        WHERE record_date=CURRENT_DATE AND bit_type='NG'
          AND ts::time >= '10:30' AND ts::time < '11:30'),
    hour_1130_1305_ok = (SELECT COUNT(*) FROM mes_l6_final_inspection
        WHERE record_date=CURRENT_DATE AND bit_type='OK'
          AND ts::time >= '11:30' AND ts::time < '13:05'),
    hour_1130_1305_ng = (SELECT COUNT(*) FROM mes_l6_final_inspection
        WHERE record_date=CURRENT_DATE AND bit_type='NG'
          AND ts::time >= '11:30' AND ts::time < '13:05'),
    hour_1305_1405_ok = (SELECT COUNT(*) FROM mes_l6_final_inspection
        WHERE record_date=CURRENT_DATE AND bit_type='OK'
          AND ts::time >= '13:05' AND ts::time < '14:05'),
    hour_1305_1405_ng = (SELECT COUNT(*) FROM mes_l6_final_inspection
        WHERE record_date=CURRENT_DATE AND bit_type='NG'
          AND ts::time >= '13:05' AND ts::time < '14:05'),
    hour_1405_1505_ok = (SELECT COUNT(*) FROM mes_l6_final_inspection
        WHERE record_date=CURRENT_DATE AND bit_type='OK'
          AND ts::time >= '14:05' AND ts::time < '15:05'),
    hour_1405_1505_ng = (SELECT COUNT(*) FROM mes_l6_final_inspection
        WHERE record_date=CURRENT_DATE AND bit_type='NG'
          AND ts::time >= '14:05' AND ts::time < '15:05'),
    hour_1505_1605_ok = (SELECT COUNT(*) FROM mes_l6_final_inspection
        WHERE record_date=CURRENT_DATE AND bit_type='OK'
          AND ts::time >= '15:05' AND ts::time < '16:05'),
    hour_1505_1605_ng = (SELECT COUNT(*) FROM mes_l6_final_inspection
        WHERE record_date=CURRENT_DATE AND bit_type='NG'
          AND ts::time >= '15:05' AND ts::time < '16:05'),
    hour_1605_1715_ok = (SELECT COUNT(*) FROM mes_l6_final_inspection
        WHERE record_date=CURRENT_DATE AND bit_type='OK'
          AND ts::time >= '16:05' AND ts::time < '17:15'),
    hour_1605_1715_ng = (SELECT COUNT(*) FROM mes_l6_final_inspection
        WHERE record_date=CURRENT_DATE AND bit_type='NG'
          AND ts::time >= '16:05' AND ts::time < '17:15')
WHERE record_date = CURRENT_DATE AND shift_name = 'A';

-- ── Final verification — shift total should equal sum of slot OK/NG ─
SELECT
    shift_name,
    ok_count, ng_count,
    hour_0830_0930_ok + hour_0930_1030_ok + hour_1030_1130_ok
      + hour_1130_1305_ok + hour_1305_1405_ok + hour_1405_1505_ok
      + hour_1505_1605_ok + hour_1605_1715_ok AS sum_slots_ok,
    hour_0830_0930_ng + hour_0930_1030_ng + hour_1030_1130_ng
      + hour_1130_1305_ng + hour_1305_1405_ng + hour_1405_1505_ng
      + hour_1505_1605_ng + hour_1605_1715_ng AS sum_slots_ng,
    hour_0830_0930_ok || '/' || hour_0830_0930_ng AS s_0830,
    hour_0930_1030_ok || '/' || hour_0930_1030_ng AS s_0930
FROM ync_dashboard_complete
WHERE record_date = CURRENT_DATE AND shift_name = 'A';

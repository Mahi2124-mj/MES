-- Delete phantom rows with garbage / control-char part_code
-- (today only)
WITH del_l6 AS (
    DELETE FROM mes_l6_final_inspection
    WHERE record_date = CURRENT_DATE
      AND (
        part_code IS NULL
        OR part_code = ''
        OR part_code !~ '^[A-Za-z0-9._-]+$'
      )
    RETURNING 1
),
del_sub AS (
    DELETE FROM mes_submachine_ct_log
    WHERE record_date = CURRENT_DATE
      AND (
        part_code IS NULL
        OR part_code = ''
        OR part_code !~ '^[A-Za-z0-9._-]+$'
      )
    RETURNING 1
)
SELECT
    (SELECT COUNT(*) FROM del_l6)  AS l6_deleted,
    (SELECT COUNT(*) FROM del_sub) AS sub_deleted;

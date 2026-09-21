-- rollback YNC line ideal_cycle_time (was 30.00)
UPDATE mes_lines SET ideal_cycle_time=30.00 WHERE id=2;

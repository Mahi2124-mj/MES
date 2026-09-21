import psycopg2
from psycopg2.extras import RealDictCursor
c = psycopg2.connect(host='192.168.10.210', database='energydb', user='postgres', password='tbdi@123')
c.autocommit = True
cur = c.cursor(cursor_factory=RealDictCursor)
cur.execute("SELECT ok_count, updated_at FROM ync_dashboard_complete WHERE shift_name='A' AND record_date=CURRENT_DATE ORDER BY id DESC LIMIT 1")
print("Dashboard:", dict(cur.fetchone()))
cur.execute("SELECT COUNT(*) AS n FROM mes_l6_final_inspection WHERE shift_name='A' AND record_date=CURRENT_DATE AND bit_type='OK'")
print("Audit truth:", cur.fetchone()['n'])
c.close()

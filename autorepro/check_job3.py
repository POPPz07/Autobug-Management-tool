import psycopg2
conn = psycopg2.connect('postgresql://autorepro:autorepro@127.0.0.1:5432/autorepro')
cur = conn.cursor()
cur.execute("SELECT failure_reason_summary, logs FROM jobs WHERE id = '24c5ea65-8c21-4b9a-bd3e-18283bea1415'")
row = cur.fetchone()
print(f'FAILURE REASON: {row[0]}')
print(f'LOGS: {row[1]}')

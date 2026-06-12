import psycopg2
conn = psycopg2.connect('postgresql://autorepro:autorepro@127.0.0.1:5432/autorepro')
cur = conn.cursor()
cur.execute("SELECT failure_reason_summary, logs FROM jobs WHERE id = 'c915f19e-ee39-4cdf-88d6-722565d50fa9'")
row = cur.fetchone()
print(f'FAILURE REASON: {row[0]}')
print(f'LOGS: {row[1]}')

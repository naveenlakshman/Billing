import sqlite3

conn = sqlite3.connect('billing.db')
cur = conn.cursor()

cur.execute('SELECT receipt_no, receipt_date FROM receipts LIMIT 3')
print('Sample receipts:')
for row in cur.fetchall():
    print(f'{row[0]}: {row[1]}')

cur.execute('SELECT MIN(receipt_date), MAX(receipt_date) FROM receipts')
min_d, max_d = cur.fetchone()
print(f'\nDate range: {min_d} to {max_d}')

conn.close()

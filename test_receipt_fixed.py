import sqlite3
from datetime import datetime, timedelta
from config import DB_PATH

# Import the custom connection function from db.py
import sys
sys.path.insert(0, '.')
from db import get_conn

conn = get_conn()
cur = conn.cursor()

today = datetime.today()
start_of_month = today.replace(day=1)
end_of_month = (start_of_month + timedelta(days=32)).replace(day=1) - timedelta(days=1)

start_date = start_of_month.strftime("%Y-%m-%d")
end_date = end_of_month.strftime("%Y-%m-%d")

print(f'Testing period: {start_date} to {end_date}')

# Test the fixed receipt query with custom parse_date function
receipt_query = """
    SELECT IFNULL(SUM(amount_received), 0) AS total_receipts
    FROM receipts
    JOIN invoices ON receipts.invoice_id = invoices.id
    WHERE parse_date(receipts.receipt_date) BETWEEN ? AND ?
"""

cur.execute(receipt_query, [start_date, end_date])
result = cur.fetchone()
print(f'Receipts in current month: ₹{result[0]}')

# Also test all receipts
cur.execute('SELECT IFNULL(SUM(amount_received), 0) FROM receipts')
total = cur.fetchone()[0]
print(f'Total receipts (all time): ₹{total}')

# Test monthly grouping
monthly_query = """
    SELECT
        SUBSTR(parse_date(receipts.receipt_date), 1, 7) AS ym,
        IFNULL(SUM(receipts.amount_received), 0) AS total_amount
    FROM receipts
    JOIN invoices ON receipts.invoice_id = invoices.id
    WHERE parse_date(receipts.receipt_date) BETWEEN ? AND ?
    GROUP BY SUBSTR(parse_date(receipts.receipt_date), 1, 7)
"""
cur.execute(monthly_query, [start_date, end_date])
print('\nMonthly receipts:')
for row in cur.fetchall():
    print(f'  {row[0]}: ₹{row[1]}')

conn.close()

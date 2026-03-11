import sqlite3
from datetime import datetime
from werkzeug.security import generate_password_hash
from config import DB_PATH


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    
    # Register custom function to parse dates in both DD-MM-YYYY and YYYY-MM-DD formats
    def parse_ddmmyyyy(date_str):
        """Parse date string to YYYY-MM-DD format for comparison"""
        if not date_str:
            return None
        try:
            if '-' in date_str:
                parts = date_str.split('-')
                if len(parts) == 3:
                    # Check if first part is year (> 31) or day (<= 31)
                    first_part = int(parts[0])
                    if first_part > 31:
                        # Already in YYYY-MM-DD format
                        return date_str
                    else:
                        # DD-MM-YYYY format, convert to YYYY-MM-DD
                        day, month, year = parts
                        return f"{year}-{month}-{day}"
        except:
            pass
        return date_str  # Return as-is if parsing fails
    
    conn.create_function("parse_date", 1, parse_ddmmyyyy)
    return conn

def log_activity(user_id, branch_id, action_type, module_name, record_id, description):
    conn = get_conn()
    try:
        cur = conn.cursor()

        now = datetime.now().isoformat(timespec="seconds")

        cur.execute("""
            INSERT INTO activity_logs (
                user_id,
                branch_id,
                action_type,
                module_name,
                record_id,
                description,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            branch_id,
            action_type,
            module_name,
            record_id,
            description,
            now
        ))
        conn.commit()
    finally:
        conn.close()    
def add_column_if_not_exists(cur, table_name, column_name, column_def):
    cur.execute(f"PRAGMA table_info({table_name})")
    columns = [row["name"] for row in cur.fetchall()]
    if column_name not in columns:
        # Remove UNIQUE constraint for ALTER TABLE since it causes issues with NULL values
        # SQLite doesn't allow adding UNIQUE constraints with existing NULL data
        clean_def = column_def.replace(" UNIQUE", "").replace("UNIQUE ", "")
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {clean_def}")


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    now = datetime.now().isoformat(timespec="seconds")

    # ---------- USERS ----------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin', 'staff')),
            phone TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
    """)

    # ---------- STUDENTS ----------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_code TEXT NOT NULL UNIQUE,
            full_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            email TEXT,
            address TEXT,
            joined_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active', 'completed', 'dropped')),
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
    """)

    # ---------- COURSES ----------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_name TEXT NOT NULL UNIQUE,
            duration TEXT,
            fee REAL NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
    """)

    # ---------- INVOICES ----------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_no TEXT NOT NULL UNIQUE,
            student_id INTEGER NOT NULL,
            invoice_date TEXT NOT NULL,
            subtotal REAL NOT NULL DEFAULT 0,
            discount_type TEXT NOT NULL DEFAULT 'none'
                CHECK(discount_type IN ('none', 'fixed', 'percentage')),
            discount_value REAL NOT NULL DEFAULT 0,
            discount_amount REAL NOT NULL DEFAULT 0,
            total_amount REAL NOT NULL DEFAULT 0,
            installment_type TEXT NOT NULL DEFAULT 'full'
                CHECK(installment_type IN ('full', 'custom')),
            notes TEXT,
            status TEXT NOT NULL DEFAULT 'unpaid'
                CHECK(status IN ('unpaid', 'partially_paid', 'paid', 'cancelled')),
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            FOREIGN KEY (student_id) REFERENCES students(id),
            FOREIGN KEY (created_by) REFERENCES users(id)
        )
    """)

    # ---------- INVOICE ITEMS ----------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS invoice_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            course_id INTEGER,
            description TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            unit_price REAL NOT NULL DEFAULT 0,
            line_total REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
            FOREIGN KEY (course_id) REFERENCES courses(id)
        )
    """)

    # ---------- INSTALLMENT PLANS ----------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS installment_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            installment_no INTEGER NOT NULL,
            due_date TEXT NOT NULL,
            amount_due REAL NOT NULL DEFAULT 0,
            amount_paid REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'partially_paid', 'paid', 'overdue')),
            remarks TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
        )
    """)

    # ---------- RECEIPTS ----------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_no TEXT NOT NULL UNIQUE,
            invoice_id INTEGER NOT NULL,
            receipt_date TEXT NOT NULL,
            amount_received REAL NOT NULL DEFAULT 0,
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
            FOREIGN KEY (created_by) REFERENCES users(id)
        )
    """)

    # ---------- BRANCHES ----------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS branches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_name TEXT NOT NULL UNIQUE,
            branch_code TEXT NOT NULL UNIQUE,
            address TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)
        # ---------- EXPENSE CATEGORIES ----------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS expense_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_name TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)

    # ---------- EXPENSES ----------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            expense_date TEXT NOT NULL,
            branch_id INTEGER NOT NULL,
            category_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            amount REAL NOT NULL DEFAULT 0,
            payment_mode TEXT NOT NULL
                CHECK(payment_mode IN ('cash', 'upi', 'bank_transfer', 'card')),
            reference_no TEXT,
            notes TEXT,
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            FOREIGN KEY (branch_id) REFERENCES branches(id),
            FOREIGN KEY (category_id) REFERENCES expense_categories(id),
            FOREIGN KEY (created_by) REFERENCES users(id)
        )
    """)
        # ---------- ACTIVITY LOGS ----------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            branch_id INTEGER,
            action_type TEXT NOT NULL,
            module_name TEXT NOT NULL,
            record_id INTEGER,
            description TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (branch_id) REFERENCES branches(id)
        )
    """)

    # ---------- STUDENT MIGRATIONS ----------
    add_column_if_not_exists(cur, "students", "gender", "TEXT")
    add_column_if_not_exists(cur, "students", "education_level", "TEXT")
    add_column_if_not_exists(cur, "students", "qualification", "TEXT")
    add_column_if_not_exists(cur, "students", "employment_status", "TEXT DEFAULT 'unemployed'")

    # ---------- COURSE MIGRATIONS ----------
    add_column_if_not_exists(cur, "courses", "course_type", "TEXT DEFAULT 'standard'")

    # ---------- RECEIPTS MIGRATIONS ----------
    # SQLite doesn't support DROP CONSTRAINT, so we need to recreate the table
    # to make invoice_id required and payment_id removed
    cur.execute("""
        PRAGMA table_info(receipts)
    """)
    receipts_columns = {row[1]: row for row in cur.fetchall()}
    
    # Check if table exists and has payment_id column
    if "payment_id" in receipts_columns:
        # payment_id column exists, we need to recreate without it
        try:
            cur.execute("ALTER TABLE receipts RENAME TO receipts_old")
            
            # Create new receipts table without payment_id
            cur.execute("""
                CREATE TABLE receipts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    receipt_no TEXT NOT NULL UNIQUE,
                    invoice_id INTEGER NOT NULL,
                    receipt_date TEXT NOT NULL,
                    amount_received REAL NOT NULL DEFAULT 0,
                    created_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
                    FOREIGN KEY (created_by) REFERENCES users(id)
                )
            """)
            
            # Copy data from old table
            try:
                cur.execute("""
                    INSERT INTO receipts (id, receipt_no, invoice_id, receipt_date, amount_received, created_by, created_at)
                    SELECT id, receipt_no, invoice_id, receipt_date, amount_received, created_by, created_at
                    FROM receipts_old
                """)
            except:
                pass
            
            # Drop old table
            cur.execute("DROP TABLE IF EXISTS receipts_old")
        except:
            pass
    else:
        # Add invoice_id column if missing
        add_column_if_not_exists(cur, "receipts", "invoice_id", "INTEGER")

    # ---------- BRANCH MIGRATIONS ----------
    add_column_if_not_exists(cur, "users", "branch_id", "INTEGER")
    add_column_if_not_exists(cur, "users", "can_view_all_branches", "INTEGER NOT NULL DEFAULT 1")

    add_column_if_not_exists(cur, "students", "branch_id", "INTEGER")
    add_column_if_not_exists(cur, "invoices", "branch_id", "INTEGER")

    # ---------- DEFAULT BRANCHES ----------
    cur.execute("SELECT id FROM branches WHERE branch_code = ?", ("HO",))
    ho_branch = cur.fetchone()
    if not ho_branch:
        cur.execute("""
            INSERT INTO branches (branch_name, branch_code, address, is_active, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            "Global IT Education Head Office",
            "HO",
            "T G Extension, Opposite to B M Lab, Hoskote",
            1,
            now
        ))

    cur.execute("SELECT id FROM branches WHERE branch_code = ?", ("HB",))
    hb_branch = cur.fetchone()
    if not hb_branch:
        cur.execute("""
            INSERT INTO branches (branch_name, branch_code, address, is_active, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            "Global IT Education – Hoskote Branch",
            "HB",
            "College Road, Near Ayyappa Swamy Temple, Hoskote",
            1,
            now
        ))

    # Get Head Office ID for default backfill
    cur.execute("SELECT id FROM branches WHERE branch_code = ?", ("HO",))
    head_office = cur.fetchone()
    head_office_id = head_office["id"] if head_office else 1

    # ---------- DEFAULT ADMIN USER ----------
    cur.execute("SELECT id FROM users WHERE username = ?", ("admin",))
    existing_admin = cur.fetchone()

    if not existing_admin:
        cur.execute("""
            INSERT INTO users (
                full_name, username, password_hash, role, phone,
                is_active, created_at, updated_at, branch_id, can_view_all_branches
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "Administrator",
            "admin",
            generate_password_hash("admin123"),
            "admin",
            "",
            1,
            now,
            now,
            head_office_id,
            1
        ))

    # ---------- BACKFILL OLD RECORDS ----------
    cur.execute("UPDATE users SET branch_id = ? WHERE branch_id IS NULL", (head_office_id,))
    cur.execute("UPDATE users SET can_view_all_branches = 1 WHERE can_view_all_branches IS NULL")

    cur.execute("UPDATE students SET branch_id = ? WHERE branch_id IS NULL", (head_office_id,))
    cur.execute("UPDATE invoices SET branch_id = ? WHERE branch_id IS NULL", (head_office_id,))

    default_categories = [
        "Rent",
        "Salary",
        "Electricity",
        "Internet",
        "Marketing",
        "Stationery",
        "Travel",
        "Maintenance",
        "Tea/Snacks",
        "Software/Tools",
        "Miscellaneous"
    ]

    for category_name in default_categories:
        cur.execute("SELECT id FROM expense_categories WHERE category_name = ?", (category_name,))
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO expense_categories (category_name, is_active, created_at)
                VALUES (?, ?, ?)
            """, (category_name, 1, now))

    # ---------- RECALCULATE INVOICE STATUSES BASED ON RECEIPTS ----------
    # This ensures invoices reflect the actual payment status from receipts
    cur.execute("SELECT id, total_amount FROM invoices")
    all_invoices = cur.fetchall()
    
    for invoice in all_invoices:
        invoice_id = invoice['id']
        total_amount = invoice['total_amount']
        
        # Calculate total receipts for this invoice
        cur.execute("""
            SELECT IFNULL(SUM(amount_received), 0) AS total_received
            FROM receipts
            WHERE invoice_id = ?
        """, (invoice_id,))
        receipt_result = cur.fetchone()
        total_received = receipt_result['total_received'] if receipt_result['total_received'] else 0
        
        # Determine new status
        if total_received >= total_amount:
            new_status = 'paid'
        elif total_received > 0:
            new_status = 'partially_paid'
        else:
            new_status = 'unpaid'
        
        # Update invoice status
        cur.execute("""
            UPDATE invoices
            SET status = ?, updated_at = ?
            WHERE id = ?
        """, (new_status, now, invoice_id))

    conn.commit()
    conn.close()
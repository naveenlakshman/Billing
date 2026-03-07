import sqlite3
from datetime import datetime
from werkzeug.security import generate_password_hash
from config import DB_PATH


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

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

    # ---------- PAYMENTS ----------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            payment_date TEXT NOT NULL,
            amount_paid REAL NOT NULL DEFAULT 0,
            payment_mode TEXT NOT NULL
                CHECK(payment_mode IN ('cash', 'upi', 'bank_transfer', 'card')),
            reference_no TEXT,
            notes TEXT,
            collected_by INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
            FOREIGN KEY (collected_by) REFERENCES users(id)
        )
    """)

    # ---------- RECEIPTS ----------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_no TEXT NOT NULL UNIQUE,
            payment_id INTEGER NOT NULL UNIQUE,
            receipt_date TEXT NOT NULL,
            amount_received REAL NOT NULL DEFAULT 0,
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (payment_id) REFERENCES payments(id) ON DELETE CASCADE,
            FOREIGN KEY (created_by) REFERENCES users(id)
        )
    """)

    conn.commit()

    # Default admin user
    cur.execute("SELECT id FROM users WHERE username = ?", ("admin",))
    existing_admin = cur.fetchone()

    if not existing_admin:
        now = datetime.now().isoformat(timespec="seconds")
        cur.execute("""
            INSERT INTO users (full_name, username, password_hash, role, phone, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "Administrator",
            "admin",
            generate_password_hash("admin123"),
            "admin",
            "",
            1,
            now,
            now
        ))
        conn.commit()

    conn.close()
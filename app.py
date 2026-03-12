from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from werkzeug.security import check_password_hash
from config import SECRET_KEY
from db import get_conn, init_db, log_activity
import calendar
from datetime import datetime, date
from functools import wraps
from werkzeug.security import generate_password_hash
import json
import csv
import io

app = Flask(__name__)
app.secret_key = SECRET_KEY

# Create database and tables when app starts
init_db()

# Qualification levels mapping
QUALIFICATION_LEVELS = {
    'School': ['5th Std', '6th Std', '7th Std', '8th Std', '9th Std', 'SSLC'],
    'Pre-University': ['PUC'],
    'Diploma': ['Diploma'],
    'Technical': ['ITI'],
    'Undergraduate': ['BA', 'BCom', 'BBA', 'BCA', 'BSc', 'BBM', 'BE', 'B.Ed', 'LLB'],
    'Postgraduate': ['MA', 'MBA', 'MCom', 'Masters']
}


def login_required(route_function):
    @wraps(route_function)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login first.", "warning")
            return redirect(url_for("login"))
        return route_function(*args, **kwargs)
    return wrapper

def admin_required(route_function):
    @wraps(route_function)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login first.", "warning")
            return redirect(url_for("login"))

        if session.get("role") != "admin":
            flash("Access denied.", "danger")
            return redirect(url_for("dashboard"))

        return route_function(*args, **kwargs)
    return wrapper

def safe_log_activity(user_id=None, branch_id=None, action_type="", module_name="", record_id=None, description=""):
    try:
        log_activity(
            user_id=user_id,
            branch_id=branch_id,
            action_type=action_type,
            module_name=module_name,
            record_id=record_id,
            description=description
        )
    except Exception as e:
        print("Activity log error:", e)





def number_to_words_indian(amount):
    amount = round(float(amount), 2)
    rupees = int(amount)
    paise = int(round((amount - rupees) * 100))

    ones = [
        "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
        "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
        "Seventeen", "Eighteen", "Nineteen"
    ]

    tens = [
        "", "", "Twenty", "Thirty", "Forty", "Fifty",
        "Sixty", "Seventy", "Eighty", "Ninety"
    ]

    def two_digit_word(n):
        if n < 20:
            return ones[n]
        return tens[n // 10] + (" " + ones[n % 10] if n % 10 else "")

    def three_digit_word(n):
        word = ""
        if n >= 100:
            word += ones[n // 100] + " Hundred"
            if n % 100:
                word += " "
        word += two_digit_word(n % 100)
        return word.strip()

    def indian_number_word(n):
        if n == 0:
            return "Zero"

        parts = []

        crore = n // 10000000
        n %= 10000000

        lakh = n // 100000
        n %= 100000

        thousand = n // 1000
        n %= 1000

        hundred_part = n

        if crore:
            parts.append(two_digit_word(crore) + " Crore")
        if lakh:
            parts.append(two_digit_word(lakh) + " Lakh")
        if thousand:
            parts.append(two_digit_word(thousand) + " Thousand")
        if hundred_part:
            parts.append(three_digit_word(hundred_part))

        return " ".join(parts).strip()

    result = indian_number_word(rupees) + " Rupees"

    if paise > 0:
        result += " and " + indian_number_word(paise) + " Paise"

    result += " Only"
    return result


def update_invoice_status(conn, invoice_id):
    cur = conn.cursor()

    cur.execute("""
        SELECT total_amount
        FROM invoices
        WHERE id = ?
    """, (invoice_id,))
    invoice = cur.fetchone()

    cur.execute("""
        SELECT IFNULL(SUM(amount_received), 0) AS total_paid
        FROM receipts
        WHERE invoice_id = ?
    """, (invoice_id,))
    paid_row = cur.fetchone()

    total_amount = float(invoice["total_amount"]) if invoice else 0
    total_paid = float(paid_row["total_paid"]) if paid_row else 0

    if total_paid <= 0:
        status = "unpaid"
    elif total_paid < total_amount:
        status = "partially_paid"
    else:
        status = "paid"

    cur.execute("""
        UPDATE invoices
        SET status = ?, updated_at = ?
        WHERE id = ?
    """, (
        status,
        datetime.now().isoformat(timespec="seconds"),
        invoice_id
    ))





@app.route("/")
def home():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM users
            WHERE username = ? AND is_active = 1
        """, (username,))
        user = cur.fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["full_name"] = user["full_name"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session["branch_id"] = user["branch_id"]
            session["can_view_all_branches"] = user["can_view_all_branches"]

            safe_log_activity(
                user_id=user["id"],
                branch_id=user["branch_id"],
                action_type="login",
                module_name="users",
                record_id=user["id"],
                description=f"User {user['username']} logged in"
            )

            flash("Login successful Welcome!", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid username or password.", "danger")

    return render_template("login.html")


@app.route("/dashboard")
@login_required
def dashboard():
    conn = get_conn()
    cur = conn.cursor()

    branch_id = request.args.get("branch_id", "").strip()
    period = request.args.get("period", "this_fy").strip()

    today = date.today()

    def get_period_range(period_key):
        year = today.year
        month = today.month

        if period_key == "this_fy":
            if month >= 4:
                start_date = date(year, 4, 1)
                end_date = date(year + 1, 3, 31)
            else:
                start_date = date(year - 1, 4, 1)
                end_date = date(year, 3, 31)

        elif period_key == "last_fy":
            if month >= 4:
                start_date = date(year - 1, 4, 1)
                end_date = date(year, 3, 31)
            else:
                start_date = date(year - 2, 4, 1)
                end_date = date(year - 1, 3, 31)

        elif period_key == "last_12_months":
            first_day_this_month = date(today.year, today.month, 1)

            start_year = first_day_this_month.year
            start_month = first_day_this_month.month - 11
            while start_month <= 0:
                start_month += 12
                start_year -= 1

            start_date = date(start_year, start_month, 1)

            end_year = first_day_this_month.year
            end_month = first_day_this_month.month
            last_day = calendar.monthrange(end_year, end_month)[1]
            end_date = date(end_year, end_month, last_day)

        else:
            if month >= 4:
                start_date = date(year, 4, 1)
                end_date = date(year + 1, 3, 31)
            else:
                start_date = date(year - 1, 4, 1)
                end_date = date(year, 3, 31)

        return start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")

    start_date, end_date = get_period_range(period)

    cur.execute("""
        SELECT *
        FROM branches
        WHERE is_active = 1
        ORDER BY branch_name
    """)
    branches = cur.fetchall()

    student_query = "SELECT COUNT(*) AS total_students FROM students"
    student_params = []

    invoice_count_query = "SELECT COUNT(*) AS total_invoices FROM invoices"
    invoice_count_params = []

    # Sales query - sum ALL invoices (not filtered by date)
    sales_query = "SELECT IFNULL(SUM(total_amount), 0) AS total_sales FROM invoices"
    sales_params = []

    receipt_query = """
        SELECT IFNULL(SUM(amount_received), 0) AS total_receipts
        FROM receipts
        JOIN invoices ON receipts.invoice_id = invoices.id
        WHERE parse_date(receipts.receipt_date) BETWEEN ? AND ?
    """
    receipt_params = [start_date, end_date]

    expense_query = """
        SELECT IFNULL(SUM(amount), 0) AS total_expenses
        FROM expenses
        WHERE expense_date BETWEEN ? AND ?
    """
    expense_params = [start_date, end_date]

    if branch_id:
        student_query += " WHERE branch_id = ?"
        student_params.append(branch_id)

        invoice_count_query += " WHERE branch_id = ?"
        invoice_count_params.append(branch_id)

        sales_query += " WHERE (branch_id = ? OR branch_id IS NULL)"
        sales_params.append(branch_id)

        receipt_query += " AND (invoices.branch_id = ? OR invoices.branch_id IS NULL)"
        receipt_params.append(branch_id)

        expense_query += " AND branch_id = ?"
        expense_params.append(branch_id)

    cur.execute(student_query, student_params)
    total_students = int(cur.fetchone()["total_students"] or 0)

    cur.execute(invoice_count_query, invoice_count_params)
    total_invoices = int(cur.fetchone()["total_invoices"] or 0)

    cur.execute(sales_query, sales_params)
    total_sales = float(cur.fetchone()["total_sales"] or 0)

    cur.execute(receipt_query, receipt_params)
    total_receipts = float(cur.fetchone()["total_receipts"] or 0)

    cur.execute(expense_query, expense_params)
    total_expenses = float(cur.fetchone()["total_expenses"] or 0)

    net_position = total_receipts - total_expenses

    # Calculate aging buckets based on installment due_date
    current_amount = 0.0
    bucket_1_15 = 0.0
    bucket_16_30 = 0.0
    bucket_31_45 = 0.0
    bucket_above_45 = 0.0

    # Calculate receivables based on installment plans with outstanding amounts
    aging_query = """
        SELECT
            ip.id,
            ip.due_date,
            ip.amount_due,
            ip.amount_paid,
            ip.status,
            i.branch_id
        FROM installment_plans ip
        JOIN invoices i ON ip.invoice_id = i.id
        WHERE ip.status IN ('pending', 'partially_paid', 'overdue')
    """
    aging_params = []

    if branch_id:
        aging_query += " AND (i.branch_id = ? OR i.branch_id IS NULL)"
        aging_params.append(branch_id)

    cur.execute(aging_query, aging_params)
    aging_rows = cur.fetchall()

    for row in aging_rows:
        due_date_str = row["due_date"]
        due_date_obj = None

        # Try to parse the date in multiple formats
        try:
            # Try YYYY-MM-DD format first
            if len(due_date_str) == 10 and due_date_str[4] == '-':
                due_date_obj = datetime.strptime(due_date_str, "%Y-%m-%d").date()
            # Try DD-MM-YYYY format
            elif len(due_date_str) == 10 and due_date_str[2] == '-':
                due_date_obj = datetime.strptime(due_date_str, "%d-%m-%Y").date()
            # Try "DD Month YYYY" format (e.g., "22 January 2026")
            elif len(due_date_str) > 10:
                due_date_obj = datetime.strptime(due_date_str, "%d %B %Y").date()
            # Try "DD Mon YYYY" format (e.g., "22 Jan 2026")
            elif len(due_date_str) > 8:
                due_date_obj = datetime.strptime(due_date_str, "%d %b %Y").date()
        except (ValueError, TypeError):
            # If parsing fails, skip this row
            continue

        if not due_date_obj:
            continue

        amount_due = float(row["amount_due"] or 0)
        amount_paid = float(row["amount_paid"] or 0)
        outstanding = amount_due - amount_paid

        if outstanding <= 0:
            continue

        overdue_days = (today - due_date_obj).days

        if overdue_days <= 0:
            current_amount += outstanding
        elif 1 <= overdue_days <= 15:
            bucket_1_15 += outstanding
        elif 16 <= overdue_days <= 30:
            bucket_16_30 += outstanding
        elif 31 <= overdue_days <= 45:
            bucket_31_45 += outstanding
        else:
            bucket_above_45 += outstanding

    # Total receivables is the sum of all aging buckets
    total_receivables = current_amount + bucket_1_15 + bucket_16_30 + bucket_31_45 + bucket_above_45

    month_keys = []
    month_labels = []

    start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()

    y = start_dt.year
    m = start_dt.month

    while (y < end_dt.year) or (y == end_dt.year and m <= end_dt.month):
        key = f"{y}-{m:02d}"
        label = f"{calendar.month_abbr[m]} {y}"
        month_keys.append(key)
        month_labels.append(label)

        m += 1
        if m > 12:
            m = 1
            y += 1

    sales_map = {k: 0.0 for k in month_keys}
    receipts_map = {k: 0.0 for k in month_keys}
    expenses_map = {k: 0.0 for k in month_keys}

    # Monthly sales: cumulative sales by month (no date filter, show all invoices grouped by month)
    monthly_sales_query = """
        SELECT
            invoice_date,
            total_amount
        FROM invoices
    """
    monthly_sales_params = []

    if branch_id:
        monthly_sales_query += " WHERE branch_id = ?"
        monthly_sales_params.append(branch_id)

    cur.execute(monthly_sales_query, monthly_sales_params)
    for row in cur.fetchall():
        invoice_date_str = row["invoice_date"]
        total_amount = float(row["total_amount"] or 0)
        
        # Try to parse the invoice date in various formats
        ym = None
        try:
            # Try YYYY-MM-DD format first
            if len(invoice_date_str) == 10 and invoice_date_str[4] == '-':
                parsed = datetime.strptime(invoice_date_str, "%Y-%m-%d").date()
                ym = f"{parsed.year}-{parsed.month:02d}"
            # Try "DD Month YYYY" format (e.g., "22 January 2026")
            elif len(invoice_date_str) > 10:
                parsed = datetime.strptime(invoice_date_str, "%d %B %Y").date()
                ym = f"{parsed.year}-{parsed.month:02d}"
            # Try "DD Month YYYY" with short month (e.g., "22 Jan 2026")
            elif len(invoice_date_str) > 8:
                try:
                    parsed = datetime.strptime(invoice_date_str, "%d %b %Y").date()
                    ym = f"{parsed.year}-{parsed.month:02d}"
                except ValueError:
                    pass
        except (ValueError, TypeError):
            pass
        
        if ym and ym in sales_map:
            sales_map[ym] = sales_map.get(ym, 0) + total_amount

    monthly_receipts_query = """
        SELECT
            SUBSTR(parse_date(receipts.receipt_date), 1, 7) AS ym,
            IFNULL(SUM(receipts.amount_received), 0) AS total_amount
        FROM receipts
        JOIN invoices ON receipts.invoice_id = invoices.id
        WHERE parse_date(receipts.receipt_date) BETWEEN ? AND ?
    """
    monthly_receipts_params = [start_date, end_date]

    if branch_id:
        monthly_receipts_query += " AND invoices.branch_id = ?"
        monthly_receipts_params.append(branch_id)

    monthly_receipts_query += " GROUP BY SUBSTR(parse_date(receipts.receipt_date), 1, 7)"

    cur.execute(monthly_receipts_query, monthly_receipts_params)
    for row in cur.fetchall():
        ym = row["ym"]
        if ym in receipts_map:
            receipts_map[ym] = float(row["total_amount"] or 0)

    monthly_expenses_query = """
        SELECT
            substr(expense_date, 1, 7) AS ym,
            IFNULL(SUM(amount), 0) AS total_amount
        FROM expenses
        WHERE expense_date BETWEEN ? AND ?
    """
    monthly_expenses_params = [start_date, end_date]

    if branch_id:
        monthly_expenses_query += " AND branch_id = ?"
        monthly_expenses_params.append(branch_id)

    monthly_expenses_query += " GROUP BY substr(expense_date, 1, 7)"

    cur.execute(monthly_expenses_query, monthly_expenses_params)
    for row in cur.fetchall():
        ym = row["ym"]
        if ym in expenses_map:
            expenses_map[ym] = float(row["total_amount"] or 0)

    sales_data = []
    receipts_data = []
    expenses_data = []

    for key in month_keys:
        sales_data.append(round(sales_map[key], 2))
        receipts_data.append(round(receipts_map[key], 2))
        expenses_data.append(round(expenses_map[key], 2))

    conn.close()

    return render_template(
        "dashboard.html",
        branches=branches,
        branch_id=branch_id,
        period=period,
        start_date=start_date,
        end_date=end_date,
        total_students=total_students,
        total_invoices=total_invoices,
        total_sales=total_sales,
        total_receipts=total_receipts,
        total_expenses=total_expenses,
        net_position=net_position,
        total_receivables=total_receivables,
        current_amount=current_amount,
        bucket_1_15=bucket_1_15,
        bucket_16_30=bucket_16_30,
        bucket_31_45=bucket_31_45,
        bucket_above_45=bucket_above_45,
        month_labels=month_labels,
        sales_data=sales_data,
        receipts_data=receipts_data,
        expenses_data=expenses_data
    )


@app.route("/logout")
def logout():
    user_id = session.get("user_id")
    branch_id = session.get("branch_id")
    username = session.get("username", "unknown")

    if user_id:
        safe_log_activity(
            user_id=user_id,
            branch_id=branch_id,
            action_type="logout",
            module_name="users",
            record_id=user_id,
            description=f"User {username} logged out"
        )

    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


@app.route("/students")
@login_required
def students():
    conn = get_conn()
    cur = conn.cursor()

    # Get search and filter parameters
    search_query = request.args.get("search", "").strip()
    branch_filter = request.args.get("branch", "").strip()
    status_filter = request.args.get("status", "").strip()

    # Build the base query
    query = """
        SELECT
            students.*,
            branches.branch_name
        FROM students
        LEFT JOIN branches
            ON students.branch_id = branches.id
        WHERE 1=1
    """
    params = []

    # Add search filter (search in name, phone, email, student_code)
    if search_query:
        query += """ AND (
            students.full_name LIKE ? OR
            students.phone LIKE ? OR
            students.email LIKE ? OR
            students.student_code LIKE ?
        )"""
        search_param = f"%{search_query}%"
        params.extend([search_param, search_param, search_param, search_param])

    # Add branch filter
    if branch_filter:
        query += " AND students.branch_id = ?"
        params.append(branch_filter)

    # Add status filter
    if status_filter:
        query += " AND students.status = ?"
        params.append(status_filter)

    query += " ORDER BY students.id DESC"

    cur.execute(query, params)
    students = cur.fetchall()

    # Get all branches for the filter dropdown
    cur.execute("""
        SELECT id, branch_name, branch_code
        FROM branches
        WHERE is_active = 1
        ORDER BY branch_name
    """)
    branches = cur.fetchall()

    conn.close()

    return render_template(
        "students.html",
        students=students,
        branches=branches,
        search_query=search_query,
        branch_filter=branch_filter,
        status_filter=status_filter
    )


@app.route("/api/qualifications/<education_level>")
def get_qualifications(education_level):
    qualifications = QUALIFICATION_LEVELS.get(education_level, [])
    return json.dumps(qualifications)


@app.route("/student/new", methods=["GET", "POST"])
@login_required
def student_new():
    conn = get_conn()
    cur = conn.cursor()

    if request.method == "POST":
        branch_id = request.form["branch_id"]
        full_name = request.form["full_name"]
        phone = request.form["phone"]
        gender = request.form.get("gender", "")
        email = request.form.get("email", "")
        address = request.form.get("address", "")
        education_level = request.form.get("education_level", "")
        qualification = request.form.get("qualification", "")
        employment_status = request.form.get("employment_status", "")
        status = request.form.get("status", "active")

        # Get the next registration number
        # Fetch all student codes and find max numeric value
        cur.execute("SELECT student_code FROM students ORDER BY CAST(student_code AS INTEGER) DESC LIMIT 1")
        result = cur.fetchone()
        if result and result["student_code"]:
            try:
                max_reg = int(result["student_code"])
                next_reg_no = max_reg + 1
            except (ValueError, TypeError):
                max_reg = 1515000
                next_reg_no = max_reg + 1
        else:
            max_reg = 1515000
            next_reg_no = max_reg + 1

        now = datetime.now().isoformat(timespec="seconds")

        cur.execute("""
            INSERT INTO students (
                student_code,
                full_name,
                phone,
                gender,
                email,
                address,
                education_level,
                qualification,
                employment_status,
                joined_date,
                status,
                branch_id,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(next_reg_no),
            full_name,
            phone,
            gender,
            email,
            address,
            education_level,
            qualification,
            employment_status,
            now,
            status,
            branch_id,
            now,
            now
        ))

        student_id = cur.lastrowid
        conn.commit()
        conn.close()

        safe_log_activity(
            user_id=session["user_id"],
            branch_id=branch_id,
            action_type="create",
            module_name="students",
            record_id=student_id,
            description=f"Created student {full_name} (Reg No: {next_reg_no})"
        )

        flash("Student added successfully.", "success")
        return redirect(url_for("students"))

    cur.execute("""
        SELECT *
        FROM branches
        WHERE is_active = 1
        ORDER BY branch_name
    """)
    branches = cur.fetchall()

    conn.close()

    return render_template(
        "student_form.html",
        student=None,
        branches=branches,
        education_levels=QUALIFICATION_LEVELS.keys(),
        qualification_levels=QUALIFICATION_LEVELS
    )


@app.route("/student/<int:student_id>/edit", methods=["GET", "POST"])
@login_required
def student_edit(student_id):
    conn = get_conn()
    cur = conn.cursor()

    # Fetch the student
    cur.execute("""
        SELECT *
        FROM students
        WHERE id = ?
    """, (student_id,))
    student = cur.fetchone()

    if not student:
        conn.close()
        flash("Student not found.", "danger")
        return redirect(url_for("students"))

    if request.method == "POST":
        full_name = request.form["full_name"]
        phone = request.form["phone"]
        gender = request.form.get("gender", "")
        email = request.form.get("email", "")
        address = request.form.get("address", "")
        education_level = request.form.get("education_level", "")
        qualification = request.form.get("qualification", "")
        employment_status = request.form.get("employment_status", "")
        status = request.form.get("status", "active")

        now = datetime.now().isoformat(timespec="seconds")

        cur.execute("""
            UPDATE students
            SET full_name = ?,
                phone = ?,
                gender = ?,
                email = ?,
                address = ?,
                education_level = ?,
                qualification = ?,
                employment_status = ?,
                status = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            full_name,
            phone,
            gender,
            email,
            address,
            education_level,
            qualification,
            employment_status,
            status,
            now,
            student_id
        ))

        conn.commit()
        conn.close()

        safe_log_activity(
            user_id=session["user_id"],
            branch_id=student["branch_id"],
            action_type="update",
            module_name="students",
            record_id=student_id,
            description=f"Updated student {full_name} ({student['student_code']})"
        )

        flash("Student updated successfully.", "success")
        return redirect(url_for("student_profile", student_id=student_id))

    cur.execute("""
        SELECT *
        FROM branches
        WHERE is_active = 1
        ORDER BY branch_name
    """)
    branches = cur.fetchall()

    conn.close()

    return render_template(
        "student_form.html",
        student=student,
        branches=branches,
        education_levels=QUALIFICATION_LEVELS.keys(),
        qualification_levels=QUALIFICATION_LEVELS
    )


@app.route("/courses")
@login_required
def courses():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT * FROM courses
        ORDER BY id DESC
    """)

    courses = cur.fetchall()
    conn.close()

    return render_template("courses.html", courses=courses)


@app.route("/course/new", methods=["GET", "POST"])
@login_required
def course_new():
    if request.method == "POST":
        course_name = request.form["course_name"]
        duration = request.form["duration"]
        fee = request.form["fee"]

        conn = get_conn()
        cur = conn.cursor()

        now = datetime.now().isoformat(timespec="seconds")

        cur.execute("""
            INSERT INTO courses (
                course_name,
                duration,
                fee,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            course_name,
            duration,
            fee,
            now,
            now
        ))

        course_id = cur.lastrowid
        conn.commit()
        conn.close()

        safe_log_activity(
            user_id=session["user_id"],
            branch_id=session.get("branch_id"),
            action_type="create",
            module_name="courses",
            record_id=course_id,
            description=f"Created course {course_name}"
        )

        flash("Course added successfully.", "success")
        return redirect(url_for("courses"))

    return render_template("course_form.html")


@app.route("/course/<int:id>/edit", methods=["GET", "POST"])
@login_required
def course_edit(id):
    conn = get_conn()
    cur = conn.cursor()

    # Fetch the course
    cur.execute("SELECT * FROM courses WHERE id = ?", (id,))
    course = cur.fetchone()

    if not course:
        conn.close()
        flash("Course not found.", "danger")
        return redirect(url_for("courses"))

    if request.method == "POST":
        course_name = request.form["course_name"]
        duration = request.form["duration"]
        fee = request.form["fee"]

        now = datetime.now().isoformat(timespec="seconds")

        cur.execute("""
            UPDATE courses
            SET course_name = ?,
                duration = ?,
                fee = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            course_name,
            duration,
            fee,
            now,
            id
        ))

        conn.commit()
        conn.close()

        safe_log_activity(
            user_id=session["user_id"],
            branch_id=session.get("branch_id"),
            action_type="update",
            module_name="courses",
            record_id=id,
            description=f"Updated course {course_name}"
        )

        flash("Course updated successfully.", "success")
        return redirect(url_for("courses"))

    conn.close()
    return render_template("course_form.html", course=course)


@app.route("/invoices")
@login_required
def invoices():
    search = request.args.get("search", "").strip()

    conn = get_conn()
    cur = conn.cursor()

    query = """
    SELECT
        invoices.id,
        invoices.invoice_no,
        invoices.invoice_date,
        invoices.total_amount,
        invoices.status,
        students.id AS student_id,
        students.student_code,
        students.full_name,
        branches.branch_name,
        IFNULL(SUM(receipts.amount_received), 0) AS paid_amount
    FROM invoices
    JOIN students
        ON invoices.student_id = students.id
    LEFT JOIN branches
        ON invoices.branch_id = branches.id
    LEFT JOIN receipts
        ON receipts.invoice_id = invoices.id
    """

    params = []

    if search:
        query += """
        WHERE
            invoices.invoice_no LIKE ?
            OR students.full_name LIKE ?
            OR students.student_code LIKE ?
        """
        like = f"%{search}%"
        params.extend([like, like, like])

    query += """
    GROUP BY invoices.id
    ORDER BY invoices.id DESC
    """

    cur.execute(query, params)
    invoices = cur.fetchall()

    conn.close()

    return render_template("invoices.html", invoices=invoices, search=search)


@app.route("/receivables")
@login_required
def receivables():
    conn = get_conn()
    cur = conn.cursor()

    # Get today's date in YYYY-MM-DD format
    today = datetime.now().date().isoformat()

    # Get branch filter from query params
    branch_id = request.args.get("branch_id", "").strip()

    # Get all branches for filter dropdown
    cur.execute("""
        SELECT *
        FROM branches
        WHERE is_active = 1
        ORDER BY branch_name
    """)
    branches = cur.fetchall()

    # Base query for past dues
    past_dues_query = """
        SELECT
            ip.id,
            ip.installment_no,
            ip.due_date,
            ip.amount_due,
            ip.amount_paid,
            ip.status,
            i.invoice_no,
            i.id AS invoice_id,
            i.branch_id,
            s.full_name AS student_name,
            s.student_code,
            s.phone AS student_phone,
            b.branch_name,
            (ip.amount_due - ip.amount_paid) AS balance_due
        FROM installment_plans ip
        JOIN invoices i ON ip.invoice_id = i.id
        JOIN students s ON i.student_id = s.id
        LEFT JOIN branches b ON i.branch_id = b.id
        WHERE ip.status != 'paid'
            AND parse_date(ip.due_date) < ?
    """
    past_dues_params = [today]

    if branch_id:
        past_dues_query += " AND i.branch_id = ?"
        past_dues_params.append(branch_id)

    past_dues_query += " ORDER BY parse_date(ip.due_date) ASC"

    cur.execute(past_dues_query, past_dues_params)
    past_dues = cur.fetchall()

    # Query for today's dues
    todays_dues_query = """
        SELECT
            ip.id,
            ip.installment_no,
            ip.due_date,
            ip.amount_due,
            ip.amount_paid,
            ip.status,
            i.invoice_no,
            i.id AS invoice_id,
            i.branch_id,
            s.full_name AS student_name,
            s.student_code,
            s.phone AS student_phone,
            b.branch_name,
            (ip.amount_due - ip.amount_paid) AS balance_due
        FROM installment_plans ip
        JOIN invoices i ON ip.invoice_id = i.id
        JOIN students s ON i.student_id = s.id
        LEFT JOIN branches b ON i.branch_id = b.id
        WHERE ip.status != 'paid'
            AND parse_date(ip.due_date) = ?
    """
    todays_dues_params = [today]

    if branch_id:
        todays_dues_query += " AND i.branch_id = ?"
        todays_dues_params.append(branch_id)

    todays_dues_query += " ORDER BY s.full_name ASC"

    cur.execute(todays_dues_query, todays_dues_params)
    todays_dues = cur.fetchall()

    # Query for upcoming dues
    upcoming_dues_query = """
        SELECT
            ip.id,
            ip.installment_no,
            ip.due_date,
            ip.amount_due,
            ip.amount_paid,
            ip.status,
            i.invoice_no,
            i.id AS invoice_id,
            i.branch_id,
            s.full_name AS student_name,
            s.student_code,
            s.phone AS student_phone,
            b.branch_name,
            (ip.amount_due - ip.amount_paid) AS balance_due
        FROM installment_plans ip
        JOIN invoices i ON ip.invoice_id = i.id
        JOIN students s ON i.student_id = s.id
        LEFT JOIN branches b ON i.branch_id = b.id
        WHERE ip.status != 'paid'
            AND parse_date(ip.due_date) > ?
    """
    upcoming_dues_params = [today]

    if branch_id:
        upcoming_dues_query += " AND i.branch_id = ?"
        upcoming_dues_params.append(branch_id)

    upcoming_dues_query += " ORDER BY parse_date(ip.due_date) ASC LIMIT 50"

    cur.execute(upcoming_dues_query, upcoming_dues_params)
    upcoming_dues = cur.fetchall()

    # Calculate totals
    total_past_due = sum(row['balance_due'] for row in past_dues)
    total_today_due = sum(row['balance_due'] for row in todays_dues)
    total_upcoming_due = sum(row['balance_due'] for row in upcoming_dues)

    conn.close()

    return render_template(
        "receivables.html",
        past_dues=past_dues,
        todays_dues=todays_dues,
        upcoming_dues=upcoming_dues,
        total_past_due=total_past_due,
        total_today_due=total_today_due,
        total_upcoming_due=total_upcoming_due,
        today=today,
        branches=branches,
        branch_id=branch_id
    )


@app.route("/invoice/new", methods=["GET", "POST"])
@login_required
def invoice_new():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT students.*, branches.branch_name
        FROM students
        LEFT JOIN branches ON students.branch_id = branches.id
        ORDER BY students.full_name ASC
    """)
    students = cur.fetchall()

    cur.execute("""
        SELECT *
        FROM courses
        WHERE is_active = 1
        ORDER BY course_name ASC
    """)
    courses = cur.fetchall()

    if request.method == "POST":
        try:
            student_id = request.form["student_id"]
            invoice_date = request.form["invoice_date"]
            installment_type = request.form["installment_type"]
            notes = request.form.get("notes", "").strip()

            item_course_ids = request.form.getlist("item_course_id[]")
            item_descriptions = request.form.getlist("item_description[]")
            item_qtys = request.form.getlist("item_qty[]")
            item_rates = request.form.getlist("item_rate[]")
            item_discounts = request.form.getlist("item_discount[]")

            if not student_id:
                flash("Please select a student.", "danger")
                conn.close()
                return redirect(url_for("invoice_new"))

            if not item_descriptions:
                flash("Please add at least one bill item.", "danger")
                conn.close()
                return redirect(url_for("invoice_new"))

            cur.execute("""
                SELECT id, branch_id, full_name
                FROM students
                WHERE id = ?
            """, (student_id,))
            student = cur.fetchone()

            if not student:
                conn.close()
                flash("Selected student not found.", "danger")
                return redirect(url_for("invoice_new"))

            branch_id = student["branch_id"]

            if not branch_id:
                conn.close()
                flash("Selected student does not have a branch assigned.", "danger")
                return redirect(url_for("invoice_new"))

            now = datetime.now().isoformat(timespec="seconds")

            invoice_items_to_save = []
            subtotal = 0.0
            discount_amount = 0.0
            total_amount = 0.0

            for i in range(len(item_descriptions)):
                description = (item_descriptions[i] or "").strip()
                course_id_raw = (item_course_ids[i] or "").strip()
                qty_raw = (item_qtys[i] or "0").strip()
                rate_raw = (item_rates[i] or "0").strip()
                discount_raw = (item_discounts[i] or "0").strip()

                qty = float(qty_raw or 0)
                rate = float(rate_raw or 0)
                row_discount = float(discount_raw or 0)

                if not description and qty == 0 and rate == 0:
                    continue

                if not description:
                    conn.close()
                    flash(f"Description is required in item row {i + 1}.", "danger")
                    return redirect(url_for("invoice_new"))

                if qty <= 0:
                    conn.close()
                    flash(f"Quantity must be greater than 0 in item row {i + 1}.", "danger")
                    return redirect(url_for("invoice_new"))

                if rate < 0:
                    conn.close()
                    flash(f"Rate cannot be negative in item row {i + 1}.", "danger")
                    return redirect(url_for("invoice_new"))

                gross = qty * rate

                if row_discount < 0:
                    row_discount = 0

                if row_discount > gross:
                    row_discount = gross

                line_total = gross - row_discount

                subtotal += gross
                discount_amount += row_discount
                total_amount += line_total

                course_id = int(course_id_raw) if course_id_raw else None

                invoice_items_to_save.append({
                    "course_id": course_id,
                    "description": description,
                    "quantity": qty,
                    "unit_price": rate,
                    "line_total": line_total
                })

            if not invoice_items_to_save:
                conn.close()
                flash("Please enter at least one valid bill item.", "danger")
                return redirect(url_for("invoice_new"))

            cur.execute("""
                INSERT INTO invoices (
                    invoice_no,
                    student_id,
                    branch_id,
                    invoice_date,
                    subtotal,
                    discount_type,
                    discount_value,
                    discount_amount,
                    total_amount,
                    installment_type,
                    notes,
                    status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "TEMP",
                student_id,
                branch_id,
                invoice_date,
                subtotal,
                "none",
                0,
                discount_amount,
                total_amount,
                installment_type,
                notes,
                "unpaid",
                session["user_id"],
                now,
                now
            ))

            invoice_id = cur.lastrowid
            
            # Generate invoice number based on existing invoices (similar to student code)
            # Try to extract prefix and next number from existing invoices
            cur.execute("""
                SELECT invoice_no FROM invoices 
                WHERE invoice_no NOT LIKE 'INV-%' AND invoice_no NOT LIKE 'TEMP'
                ORDER BY invoice_no DESC LIMIT 1
            """)
            result = cur.fetchone()
            
            if result and result["invoice_no"]:
                existing_no = result["invoice_no"]
                # Try to extract numeric part (e.g., "GIT/B/312" -> get 312)
                try:
                    parts = existing_no.split('/')
                    if len(parts) >= 2:
                        numeric_part = int(parts[-1])
                        prefix = '/'.join(parts[:-1])  # Get "GIT/B"
                        next_number = numeric_part + 1
                        invoice_no = f"{prefix}/{next_number}"
                    else:
                        invoice_no = f"INV-{str(invoice_id).zfill(4)}"
                except (ValueError, IndexError, TypeError):
                    invoice_no = f"INV-{str(invoice_id).zfill(4)}"
            else:
                invoice_no = f"INV-{str(invoice_id).zfill(4)}"

            cur.execute("""
                UPDATE invoices
                SET invoice_no = ?
                WHERE id = ?
            """, (invoice_no, invoice_id))

            for item in invoice_items_to_save:
                cur.execute("""
                    INSERT INTO invoice_items (
                        invoice_id,
                        course_id,
                        description,
                        quantity,
                        unit_price,
                        line_total,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    invoice_id,
                    item["course_id"],
                    item["description"],
                    item["quantity"],
                    item["unit_price"],
                    item["line_total"],
                    now
                ))

            if installment_type == "full":
                due_date = request.form.get("full_due_date", "").strip()

                if not due_date:
                    conn.rollback()
                    conn.close()
                    flash("Please enter full payment due date.", "danger")
                    return redirect(url_for("invoice_new"))

                cur.execute("""
                    INSERT INTO installment_plans (
                        invoice_id,
                        installment_no,
                        due_date,
                        amount_due,
                        amount_paid,
                        status,
                        remarks,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    invoice_id,
                    1,
                    due_date,
                    total_amount,
                    0,
                    "pending",
                    "Full payment",
                    now,
                    now
                ))

            elif installment_type == "custom":
                installment_count = int(request.form.get("installment_count", 0) or 0)

                if installment_count <= 0:
                    conn.rollback()
                    conn.close()
                    flash("Please enter valid installment count.", "danger")
                    return redirect(url_for("invoice_new"))

                installment_total = 0.0

                for i in range(1, installment_count + 1):
                    due_date = request.form.get(f"due_date_{i}", "").strip()
                    amount_due_raw = request.form.get(f"amount_due_{i}", "0").strip()
                    remarks = request.form.get(f"remarks_{i}", "").strip()

                    amount_due = float(amount_due_raw or 0)

                    if not due_date:
                        conn.rollback()
                        conn.close()
                        flash(f"Due date is required for installment {i}.", "danger")
                        return redirect(url_for("invoice_new"))

                    if amount_due <= 0:
                        conn.rollback()
                        conn.close()
                        flash(f"Amount must be greater than 0 for installment {i}.", "danger")
                        return redirect(url_for("invoice_new"))

                    installment_total += amount_due

                    cur.execute("""
                        INSERT INTO installment_plans (
                            invoice_id,
                            installment_no,
                            due_date,
                            amount_due,
                            amount_paid,
                            status,
                            remarks,
                            created_at,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        invoice_id,
                        i,
                        due_date,
                        amount_due,
                        0,
                        "pending",
                        remarks,
                        now,
                        now
                    ))

                if round(installment_total, 2) != round(total_amount, 2):
                    conn.rollback()
                    conn.close()
                    flash("Installment total must exactly match the net invoice total.", "danger")
                    return redirect(url_for("invoice_new"))

            else:
                conn.rollback()
                conn.close()
                flash("Invalid installment type selected.", "danger")
                return redirect(url_for("invoice_new"))

            conn.commit()
            conn.close()

            safe_log_activity(
                user_id=session["user_id"],
                branch_id=branch_id,
                action_type="create",
                module_name="invoices",
                record_id=invoice_id,
                description=f"Created invoice {invoice_no} for student {student['full_name']}"
            )

            flash("Invoice created successfully.", "success")
            return redirect(url_for("invoice_view", invoice_id=invoice_id))

        except ValueError:
            conn.rollback()
            conn.close()
            flash("Please enter valid numeric values in invoice rows.", "danger")
            return redirect(url_for("invoice_new"))

        except Exception as e:
            conn.rollback()
            conn.close()
            flash(f"Error while creating invoice: {str(e)}", "danger")
            return redirect(url_for("invoice_new"))

    conn.close()
    today = datetime.today().strftime("%Y-%m-%d")
    return render_template("invoice_form.html", students=students, courses=courses, today=today)


@app.route("/invoice/<int:invoice_id>")
@login_required
def invoice_view(invoice_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            invoices.*,
            students.student_code,
            students.full_name,
            students.phone,
            students.email,
            students.address,
            branches.branch_name
        FROM invoices
        JOIN students
            ON invoices.student_id = students.id
        LEFT JOIN branches
            ON invoices.branch_id = branches.id
        WHERE invoices.id = ?
    """, (invoice_id,))
    invoice = cur.fetchone()

    if not invoice:
        conn.close()
        flash("Invoice not found.", "danger")
        return redirect(url_for("invoices"))

    cur.execute("""
        SELECT
            invoice_items.*,
            courses.course_name
        FROM invoice_items
        LEFT JOIN courses
            ON invoice_items.course_id = courses.id
        WHERE invoice_items.invoice_id = ?
    """, (invoice_id,))
    items = cur.fetchall()

    cur.execute("""
        SELECT *
        FROM installment_plans
        WHERE invoice_id = ?
        ORDER BY installment_no ASC
    """, (invoice_id,))
    installments = cur.fetchall()

    cur.execute("""
        SELECT
            receipts.*,
            users.full_name AS created_by_name
        FROM receipts
        LEFT JOIN users
            ON receipts.created_by = users.id
        WHERE receipts.invoice_id = ?
        ORDER BY receipts.id DESC
    """, (invoice_id,))
    payments = cur.fetchall()

    cur.execute("""
        SELECT IFNULL(SUM(amount_received), 0) AS total_paid
        FROM receipts
        WHERE invoice_id = ?
    """, (invoice_id,))
    total_paid = float(cur.fetchone()["total_paid"] or 0)

    balance_amount = float(invoice["total_amount"] or 0) - total_paid

    conn.close()

    return render_template(
        "invoice_view.html",
        invoice=invoice,
        items=items,
        installments=installments,
        payments=payments,
        total_paid=total_paid,
        balance_amount=balance_amount
    )


@app.route("/invoice/<int:invoice_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def invoice_edit(invoice_id):
    conn = get_conn()
    cur = conn.cursor()

    # Fetch the invoice
    cur.execute("""
        SELECT
            invoices.*,
            students.student_code,
            students.full_name,
            students.phone,
            students.email,
            students.address,
            branches.branch_name
        FROM invoices
        JOIN students
            ON invoices.student_id = students.id
        LEFT JOIN branches
            ON invoices.branch_id = branches.id
        WHERE invoices.id = ?
    """, (invoice_id,))
    invoice = cur.fetchone()

    if not invoice:
        conn.close()
        flash("Invoice not found.", "danger")
        return redirect(url_for("invoices"))

    # Check if invoice has any payments
    cur.execute("""
        SELECT IFNULL(SUM(amount_received), 0) AS total_paid
        FROM receipts
        WHERE invoice_id = ?
    """, (invoice_id,))
    total_paid = float(cur.fetchone()["total_paid"] or 0)

    # Fetch invoice items
    cur.execute("""
        SELECT
            invoice_items.*,
            courses.course_name
        FROM invoice_items
        LEFT JOIN courses
            ON invoice_items.course_id = courses.id
        WHERE invoice_items.invoice_id = ?
    """, (invoice_id,))
    items = cur.fetchall()

    # Fetch installment plans
    cur.execute("""
        SELECT *
        FROM installment_plans
        WHERE invoice_id = ?
        ORDER BY installment_no ASC
    """, (invoice_id,))
    installments = cur.fetchall()

    if request.method == "POST":
        try:
            # Get form data
            student_id_form = int(request.form.get("student_id", 0))
            invoice_date = request.form["invoice_date"]
            installment_type = invoice["installment_type"]
            notes = request.form.get("notes", "").strip()

            # Validate student exists
            cur.execute("SELECT id FROM students WHERE id = ?", (student_id_form,))
            if not cur.fetchone():
                conn.close()
                flash("Selected student does not exist.", "danger")
                return redirect(url_for("invoice_edit", invoice_id=invoice_id))

            # Re-check for payments (race condition protection)
            cur.execute("""
                SELECT IFNULL(SUM(amount_received), 0) AS total_paid
                FROM receipts
                WHERE invoice_id = ?
            """, (invoice_id,))
            total_paid = float(cur.fetchone()["total_paid"] or 0)

            item_course_ids = request.form.getlist("item_course_id[]")
            item_descriptions = request.form.getlist("item_description[]")
            item_qtys = request.form.getlist("item_qty[]")
            item_rates = request.form.getlist("item_rate[]")
            item_discounts = request.form.getlist("item_discount[]")

            if not item_descriptions:
                flash("Please add at least one bill item.", "danger")
                conn.close()
                return redirect(url_for("invoice_edit", invoice_id=invoice_id))

            now = datetime.now().isoformat(timespec="seconds")

            invoice_items_to_save = []
            subtotal = 0.0
            discount_amount = 0.0
            total_amount = 0.0

            for i in range(len(item_descriptions)):
                description = (item_descriptions[i] or "").strip()
                course_id_raw = (item_course_ids[i] or "").strip()
                qty_raw = (item_qtys[i] or "0").strip()
                rate_raw = (item_rates[i] or "0").strip()
                discount_raw = (item_discounts[i] or "0").strip()

                qty = float(qty_raw or 0)
                rate = float(rate_raw or 0)
                row_discount = float(discount_raw or 0)

                if not description and qty == 0 and rate == 0:
                    continue

                if not description:
                    conn.close()
                    flash(f"Description is required in item row {i + 1}.", "danger")
                    return redirect(url_for("invoice_edit", invoice_id=invoice_id))

                if qty <= 0:
                    conn.close()
                    flash(f"Quantity must be greater than 0 in item row {i + 1}.", "danger")
                    return redirect(url_for("invoice_edit", invoice_id=invoice_id))

                if rate < 0:
                    conn.close()
                    flash(f"Rate cannot be negative in item row {i + 1}.", "danger")
                    return redirect(url_for("invoice_edit", invoice_id=invoice_id))

                gross = qty * rate

                if row_discount < 0:
                    row_discount = 0

                if row_discount > gross:
                    row_discount = gross

                line_total = gross - row_discount

                subtotal += gross
                discount_amount += row_discount
                total_amount += line_total

                course_id = int(course_id_raw) if course_id_raw else None

                invoice_items_to_save.append({
                    "course_id": course_id,
                    "description": description,
                    "quantity": qty,
                    "unit_price": rate,
                    "line_total": line_total
                })

            if not invoice_items_to_save:
                conn.close()
                flash("Please enter at least one valid bill item.", "danger")
                return redirect(url_for("invoice_edit", invoice_id=invoice_id))

            # PAYMENT PROTECTION: Check if new total is less than amount already paid
            if total_amount < total_paid:
                conn.close()
                flash(f"Cannot reduce invoice total below ₹{total_paid:.2f}", "danger")
                return redirect(url_for("invoice_edit", invoice_id=invoice_id))

            # Update invoice with new totals and student_id
            cur.execute("""
                UPDATE invoices
                SET student_id = ?,
                    invoice_date = ?,
                    subtotal = ?,
                    discount_amount = ?,
                    total_amount = ?,
                    notes = ?,
                    updated_at = ?
                WHERE id = ?
            """, (
                student_id_form,
                invoice_date,
                subtotal,
                discount_amount,
                total_amount,
                notes,
                now,
                invoice_id
            ))

            # Delete existing items and insert new ones
            cur.execute("DELETE FROM invoice_items WHERE invoice_id = ?", (invoice_id,))

            for item in invoice_items_to_save:
                cur.execute("""
                    INSERT INTO invoice_items (
                        invoice_id,
                        course_id,
                        description,
                        quantity,
                        unit_price,
                        line_total,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    invoice_id,
                    item["course_id"],
                    item["description"],
                    item["quantity"],
                    item["unit_price"],
                    item["line_total"],
                    now
                ))

            # Update installment dates if needed
            if installment_type == "full":
                due_date = request.form.get("full_due_date", "").strip()

                if not due_date:
                    conn.rollback()
                    conn.close()
                    flash("Please enter full payment due date.", "danger")
                    return redirect(url_for("invoice_edit", invoice_id=invoice_id))

                cur.execute("""
                    UPDATE installment_plans
                    SET due_date = ?,
                        amount_due = ?,
                        updated_at = ?
                    WHERE invoice_id = ? AND installment_no = 1
                """, (due_date, total_amount, now, invoice_id))

            elif installment_type == "custom":
                installment_count = int(request.form.get("installment_count", 0) or 0)

                if installment_count <= 0:
                    conn.rollback()
                    conn.close()
                    flash("Please enter valid installment count.", "danger")
                    return redirect(url_for("invoice_edit", invoice_id=invoice_id))

                installment_total = 0.0

                for i in range(1, installment_count + 1):
                    due_date = request.form.get(f"due_date_{i}", "").strip()
                    amount_due_raw = request.form.get(f"amount_due_{i}", "0").strip()
                    remarks = request.form.get(f"remarks_{i}", "").strip()

                    amount_due = float(amount_due_raw or 0)

                    if not due_date:
                        conn.rollback()
                        conn.close()
                        flash(f"Due date is required for installment {i}.", "danger")
                        return redirect(url_for("invoice_edit", invoice_id=invoice_id))

                    if amount_due <= 0:
                        conn.rollback()
                        conn.close()
                        flash(f"Amount must be greater than 0 for installment {i}.", "danger")
                        return redirect(url_for("invoice_edit", invoice_id=invoice_id))

                    installment_total += amount_due

                    # Check if this installment exists
                    cur.execute("""
                        SELECT id FROM installment_plans
                        WHERE invoice_id = ? AND installment_no = ?
                    """, (invoice_id, i))
                    existing = cur.fetchone()

                    if existing:
                        cur.execute("""
                            UPDATE installment_plans
                            SET due_date = ?,
                                amount_due = ?,
                                remarks = ?,
                                updated_at = ?
                            WHERE invoice_id = ? AND installment_no = ?
                        """, (due_date, amount_due, remarks, now, invoice_id, i))
                    else:
                        cur.execute("""
                            INSERT INTO installment_plans (
                                invoice_id,
                                installment_no,
                                due_date,
                                amount_due,
                                amount_paid,
                                status,
                                remarks,
                                created_at,
                                updated_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            invoice_id,
                            i,
                            due_date,
                            amount_due,
                            0,
                            "pending",
                            remarks,
                            now,
                            now
                        ))

                # Delete extra installments if count reduced
                cur.execute("""
                    DELETE FROM installment_plans
                    WHERE invoice_id = ? AND installment_no > ?
                """, (invoice_id, installment_count))

                if installment_total != total_amount:
                    conn.rollback()
                    conn.close()
                    flash("Installment total must equal invoice total amount.", "danger")
                    return redirect(url_for("invoice_edit", invoice_id=invoice_id))

            conn.commit()
            conn.close()

            # Prepare log description
            log_description = f"Updated invoice {invoice['invoice_no']}"
            if student_id_form != invoice["student_id"]:
                cur_tmp = get_conn().cursor()
                cur_tmp.execute("SELECT student_code, full_name FROM students WHERE id = ?", (student_id_form,))
                new_student = cur_tmp.fetchone()
                if new_student:
                    log_description += f" - Changed student from {invoice['student_code']} to {new_student['student_code']}"
                cur_tmp.connection.close()

            safe_log_activity(
                user_id=session["user_id"],
                branch_id=invoice["branch_id"],
                action_type="update",
                module_name="invoices",
                record_id=invoice_id,
                description=log_description
            )

            # Show warning if student was changed
            if student_id_form != invoice["student_id"]:
                flash("Invoice updated successfully. Invoice student changed. All payments and installments now linked to new student.", "warning")
            else:
                flash("Invoice updated successfully.", "success")
            return redirect(url_for("invoice_view", invoice_id=invoice_id))

        except Exception as e:
            conn.rollback()
            conn.close()
            flash(f"An error occurred: {str(e)}", "danger")
            return redirect(url_for("invoice_edit", invoice_id=invoice_id))

    # Fetch courses for dropdown
    cur.execute("""
        SELECT *
        FROM courses
        WHERE is_active = 1
        ORDER BY course_name ASC
    """)
    courses = cur.fetchall()

    # Fetch students for dropdown
    cur.execute("""
        SELECT *
        FROM students
        ORDER BY student_code ASC
    """)
    students = cur.fetchall()

    conn.close()

    return render_template(
        "invoice_form.html",
        invoice=invoice,
        items=items,
        installments=installments,
        courses=courses,
        students=students,
        total_paid=total_paid,
        mode="edit"
    )


@app.route("/installment/<int:installment_id>/edit", methods=["POST"])
@login_required
@admin_required
def installment_edit(installment_id):
    conn = get_conn()
    cur = conn.cursor()

    try:
        now = datetime.now().isoformat(timespec="seconds")

        # Get the installment details
        cur.execute("""
            SELECT ip.*, i.id AS invoice_id, i.total_amount, i.invoice_no
            FROM installment_plans ip
            JOIN invoices i ON ip.invoice_id = i.id
            WHERE ip.id = ?
        """, (installment_id,))
        installment = cur.fetchone()

        if not installment:
            flash("Installment not found.", "danger")
            conn.close()
            return redirect(url_for("invoices"))

        invoice_id = installment["invoice_id"]
        invoice_no = installment["invoice_no"]
        amount_paid = float(installment["amount_paid"] or 0)

        # Get form data
        due_date = request.form.get("due_date", "").strip()
        amount_due_str = request.form.get("amount_due", "").strip()
        remarks = request.form.get("remarks", "").strip()

        # Validation
        if not due_date:
            flash("Due date is required.", "danger")
            conn.close()
            return redirect(url_for("invoice_view", invoice_id=invoice_id))

        try:
            amount_due = float(amount_due_str or 0)
        except ValueError:
            flash("Invalid amount due value.", "danger")
            conn.close()
            return redirect(url_for("invoice_view", invoice_id=invoice_id))

        if amount_due <= 0:
            flash("Amount due must be greater than 0.", "danger")
            conn.close()
            return redirect(url_for("invoice_view", invoice_id=invoice_id))

        # Critical validation: amount_due cannot be less than amount_paid
        if amount_due < amount_paid:
            flash(f"Amount Due (₹{amount_due:.2f}) cannot be less than Amount Paid (₹{amount_paid:.2f}).", "danger")
            conn.close()
            return redirect(url_for("invoice_view", invoice_id=invoice_id))

        # Check if total of all installments still equals invoice total (if amount changed)
        old_amount_due = float(installment["amount_due"] or 0)
        if amount_due != old_amount_due:
            # Get sum of other installments
            cur.execute("""
                SELECT IFNULL(SUM(amount_due), 0) AS other_installments_total
                FROM installment_plans
                WHERE invoice_id = ? AND id != ?
            """, (invoice_id, installment_id))
            other_total = float(cur.fetchone()["other_installments_total"] or 0)

            new_total = other_total + amount_due
            invoice_total = float(installment["total_amount"] or 0)

            if abs(new_total - invoice_total) > 0.01:  # Allow small rounding differences
                flash(f"Total installments (₹{new_total:.2f}) must equal invoice total (₹{invoice_total:.2f}). Difference: ₹{abs(new_total - invoice_total):.2f}", "danger")
                conn.close()
                return redirect(url_for("invoice_view", invoice_id=invoice_id))

        # Update the installment
        cur.execute("""
            UPDATE installment_plans
            SET due_date = ?, amount_due = ?, remarks = ?, updated_at = ?
            WHERE id = ?
        """, (due_date, amount_due, remarks, now, installment_id))

        conn.commit()
        flash(f"Installment updated successfully for invoice {invoice_no}.", "success")

    except Exception as e:
        conn.rollback()
        flash(f"Error updating installment: {str(e)}", "danger")
    finally:
        conn.close()

    return redirect(url_for("invoice_view", invoice_id=invoice_id))


@app.route("/payment/new", methods=["GET", "POST"])
@login_required
def payment_new():
    invoice_id = request.args.get("invoice_id", type=int)

    if request.method == "POST":
        conn = get_conn()
        cur = conn.cursor()

        try:
            from datetime import datetime
            now = datetime.now().isoformat(timespec="seconds")

            amount_received = float(request.form.get("amount_received", 0))

            # Validate amount
            if amount_received <= 0:
                flash("Amount must be greater than 0", "danger")
                conn.close()
                return redirect(url_for("payment_new", invoice_id=invoice_id))

            # Get invoice details to validate against balance
            cur.execute("SELECT total_amount FROM invoices WHERE id = ?", (invoice_id,))
            invoice_data = cur.fetchone()

            if not invoice_data:
                flash("Invoice not found.", "danger")
                conn.close()
                return redirect(url_for("invoices"))

            # Calculate current balance
            cur.execute("""
                SELECT IFNULL(SUM(amount_received), 0) AS total_received
                FROM receipts
                WHERE invoice_id = ?
            """, (invoice_id,))
            total_received = float(cur.fetchone()["total_received"] or 0)
            balance_amount = float(invoice_data["total_amount"] or 0) - total_received

            # Validate that payment doesn't exceed balance
            if amount_received > balance_amount:
                flash(f"Amount cannot exceed balance amount of ₹{balance_amount:.2f}. Please enter an amount <= ₹{balance_amount:.2f}", "danger")
                conn.close()
                return redirect(url_for("payment_new", invoice_id=invoice_id))

            # Generate receipt number
            cur.execute("SELECT MAX(id) as max_id FROM receipts")
            max_id = cur.fetchone()["max_id"] or 0
            receipt_no = f"RCP{max_id + 1:05d}"

            payment_mode = request.form.get("payment_mode", "cash")

            cur.execute("""
                INSERT INTO receipts (
                    receipt_no,
                    invoice_id,
                    receipt_date,
                    amount_received,
                    payment_mode,
                    created_by,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                receipt_no,
                invoice_id,
                request.form.get("receipt_date"),
                amount_received,
                payment_mode,
                session.get("user_id"),
                now
            ))

            # Update invoice status based on total receipts
            cur.execute("SELECT total_amount FROM invoices WHERE id = ?", (invoice_id,))
            invoice = cur.fetchone()

            cur.execute("""
                SELECT IFNULL(SUM(amount_received), 0) AS total_received
                FROM receipts
                WHERE invoice_id = ?
            """, (invoice_id,))
            total_received = cur.fetchone()["total_received"] or 0

            if total_received >= invoice["total_amount"]:
                new_status = "paid"
            elif total_received > 0:
                new_status = "partially_paid"
            else:
                new_status = "unpaid"

            cur.execute("""
                UPDATE invoices
                SET status = ?, updated_at = ?
                WHERE id = ?
            """, (new_status, now, invoice_id))

            # Update installment plans based on total receipts
            cur.execute("""
                SELECT id, installment_no, amount_due, amount_paid, status
                FROM installment_plans
                WHERE invoice_id = ?
                ORDER BY installment_no ASC
            """, (invoice_id,))
            installments = cur.fetchall()

            remaining_payment = total_received

            for installment in installments:
                inst_id = installment["id"]
                inst_amount_due = installment["amount_due"]
                inst_current_paid = installment["amount_paid"]

                if remaining_payment <= 0:
                    # No more payment to allocate
                    cur.execute("""
                        UPDATE installment_plans
                        SET status = 'pending', updated_at = ?
                        WHERE id = ?
                    """, (now, inst_id))
                elif remaining_payment >= inst_amount_due:
                    # Fully paid installment
                    cur.execute("""
                        UPDATE installment_plans
                        SET amount_paid = ?, status = 'paid', remarks = 'Fully paid', updated_at = ?
                        WHERE id = ?
                    """, (inst_amount_due, now, inst_id))
                    remaining_payment -= inst_amount_due
                else:
                    # Partially paid installment
                    new_amount_paid = remaining_payment
                    cur.execute("""
                        UPDATE installment_plans
                        SET amount_paid = ?, status = 'partially_paid', remarks = ?, updated_at = ?
                        WHERE id = ?
                    """, (new_amount_paid, f"Partial payment of {new_amount_paid}", now, inst_id))
                    remaining_payment = 0

            conn.commit()
            conn.close()

            flash(f"Receipt {receipt_no} recorded successfully.", "success")
            return redirect(url_for("invoice_view", invoice_id=invoice_id))
        except Exception as e:
            conn.close()
            flash(f"Error recording payment: {str(e)}", "danger")
            return redirect(url_for("invoice_view", invoice_id=invoice_id))

    # GET request - show form
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT invoices.*, students.full_name
        FROM invoices
        JOIN students ON invoices.student_id = students.id
        WHERE invoices.id = ?
    """, (invoice_id,))
    invoice = cur.fetchone()

    if not invoice:
        conn.close()
        flash("Invoice not found.", "danger")
        return redirect(url_for("invoices"))

    # Calculate balance amount
    cur.execute("""
        SELECT IFNULL(SUM(amount_received), 0) AS total_paid
        FROM receipts
        WHERE invoice_id = ?
    """, (invoice_id,))
    total_paid = float(cur.fetchone()["total_paid"] or 0)
    balance_amount = float(invoice["total_amount"] or 0) - total_paid

    conn.close()

    return render_template(
        "payment_new.html",
        invoice=invoice,
        total_paid=total_paid,
        balance_amount=balance_amount
    )


@app.route("/student/<int:student_id>")
@login_required
def student_profile(student_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            students.*,
            branches.branch_name
        FROM students
        LEFT JOIN branches
            ON students.branch_id = branches.id
        WHERE students.id = ?
    """, (student_id,))
    student = cur.fetchone()

    if not student:
        conn.close()
        flash("Student not found.", "danger")
        return redirect(url_for("students"))

    cur.execute("""
        SELECT
            invoices.id,
            invoices.invoice_no,
            invoices.invoice_date,
            invoices.total_amount,
            invoices.status,
            IFNULL(SUM(receipts.amount_received), 0) AS paid_amount
        FROM invoices
        LEFT JOIN receipts
            ON receipts.invoice_id = invoices.id
        WHERE invoices.student_id = ?
        GROUP BY invoices.id
        ORDER BY invoices.id DESC
    """, (student_id,))
    invoices = cur.fetchall()

    cur.execute("""
        SELECT
            COUNT(*) AS total_invoices,
            IFNULL(SUM(total_amount), 0) AS total_billed
        FROM invoices
        WHERE student_id = ?
    """, (student_id,))
    invoice_summary = cur.fetchone()

    cur.execute("""
        SELECT
            IFNULL(SUM(receipts.amount_received), 0) AS total_paid
        FROM receipts
        JOIN invoices
            ON receipts.invoice_id = invoices.id
        WHERE invoices.student_id = ?
    """, (student_id,))
    payment_summary = cur.fetchone()

    total_invoices = int(invoice_summary["total_invoices"] or 0)
    total_billed = float(invoice_summary["total_billed"] or 0)
    total_paid = float(payment_summary["total_paid"] or 0)
    total_balance = total_billed - total_paid

    conn.close()

    return render_template(
        "student_profile.html",
        student=student,
        invoices=invoices,
        total_invoices=total_invoices,
        total_billed=total_billed,
        total_paid=total_paid,
        total_balance=total_balance
    )








@app.route("/reports")
@login_required
def reports_center():
    return render_template("reports_center.html")


@app.route("/reports/overdue-installments")
@login_required
def overdue_installments_report():
    conn = get_conn()
    cur = conn.cursor()

    today = datetime.today().strftime("%Y-%m-%d")
    today_date = datetime.today().date()
    branch_id = request.args.get("branch_id", "").strip()
    age_range = request.args.get("age_range", "").strip()  # New parameter for age filtering

    cur.execute("""
        SELECT *
        FROM branches
        WHERE is_active = 1
        ORDER BY branch_name
    """)
    branches = cur.fetchall()

    query = """
        SELECT
            installment_plans.id,
            installment_plans.installment_no,
            installment_plans.due_date,
            installment_plans.amount_due,
            installment_plans.amount_paid,
            installment_plans.status,
            installment_plans.remarks,

            invoices.id AS invoice_id,
            invoices.invoice_no,
            invoices.invoice_date,
            invoices.total_amount,
            invoices.branch_id,

            students.id AS student_id,
            students.student_code,
            students.full_name,
            students.phone,
            students.email,

            branch_master.branch_name

        FROM installment_plans
        JOIN invoices
            ON installment_plans.invoice_id = invoices.id
        JOIN students
            ON invoices.student_id = students.id
        LEFT JOIN branches AS branch_master
            ON invoices.branch_id = branch_master.id

        WHERE installment_plans.status IN ('pending', 'partially_paid', 'overdue')
    """

    params = []

    if branch_id:
        query += " AND invoices.branch_id = ? "
        params.append(branch_id)

    query += " ORDER BY installment_plans.due_date ASC, students.full_name ASC "

    cur.execute(query, params)
    rows = cur.fetchall()

    total_overdue_count = 0
    total_overdue_amount = 0.0

    report_rows = []

    for row in rows:
        due_date_str = row["due_date"]
        due_date_obj = None

        # Parse the due date
        try:
            if len(due_date_str) == 10 and due_date_str[4] == '-':
                due_date_obj = datetime.strptime(due_date_str, "%Y-%m-%d").date()
            elif len(due_date_str) == 10 and due_date_str[2] == '-':
                due_date_obj = datetime.strptime(due_date_str, "%d-%m-%Y").date()
        except (ValueError, TypeError):
            continue

        if not due_date_obj:
            continue

        # Calculate overdue days
        overdue_days = (today_date - due_date_obj).days

        # Apply age range filter if specified
        if age_range:
            if age_range == "current" and overdue_days > 0:
                continue
            elif age_range == "1-15" and not (1 <= overdue_days <= 15):
                continue
            elif age_range == "16-30" and not (16 <= overdue_days <= 30):
                continue
            elif age_range == "31-45" and not (31 <= overdue_days <= 45):
                continue
            elif age_range == "above-45" and overdue_days <= 45:
                continue

        amount_due = float(row["amount_due"] or 0)
        amount_paid = float(row["amount_paid"] or 0)
        pending_amount = amount_due - amount_paid

        if pending_amount <= 0:
            continue

        total_overdue_count += 1
        total_overdue_amount += pending_amount

        report_rows.append({
            "id": row["id"],
            "installment_no": row["installment_no"],
            "due_date": row["due_date"],
            "overdue_days": overdue_days,
            "amount_due": amount_due,
            "amount_paid": amount_paid,
            "pending_amount": pending_amount,
            "status": row["status"],
            "remarks": row["remarks"],
            "invoice_id": row["invoice_id"],
            "invoice_no": row["invoice_no"],
            "invoice_date": row["invoice_date"],
            "invoice_total": float(row["total_amount"] or 0),
            "student_id": row["student_id"],
            "student_code": row["student_code"],
            "full_name": row["full_name"],
            "phone": row["phone"],
            "email": row["email"],
            "branch_name": row["branch_name"]
        })

    conn.close()

    return render_template(
        "report_overdue_installments.html",
        rows=report_rows,
        total_overdue_count=total_overdue_count,
        total_overdue_amount=total_overdue_amount,
        today=today,
        branches=branches,
        branch_id=branch_id,
        age_range=age_range
    )


@app.route("/reports/today-collection")
@login_required
def today_collection_report():
    conn = get_conn()
    cur = conn.cursor()

    today = datetime.today().strftime("%Y-%m-%d")
    branch_id = request.args.get("branch_id", "").strip()

    cur.execute("""
        SELECT *
        FROM branches
        WHERE is_active = 1
        ORDER BY branch_name
    """)
    branches = cur.fetchall()

    query = """
        SELECT
            receipts.id AS receipt_id,
            receipts.receipt_no,
            receipts.receipt_date,
            receipts.amount_received,

            invoices.id AS invoice_id,
            invoices.invoice_no,
            invoices.branch_id,

            students.id AS student_id,
            students.student_code,
            students.full_name,
            students.phone,

            users.full_name AS created_by_name,
            branch_master.branch_name

        FROM receipts
        JOIN invoices
            ON receipts.invoice_id = invoices.id
        JOIN students
            ON invoices.student_id = students.id
        LEFT JOIN users
            ON receipts.created_by = users.id
        LEFT JOIN branches AS branch_master
            ON invoices.branch_id = branch_master.id

        WHERE parse_date(receipts.receipt_date) = ?
    """

    params = [today]

    if branch_id:
        query += " AND invoices.branch_id = ? "
        params.append(branch_id)

    query += " ORDER BY receipts.id DESC "

    cur.execute(query, params)
    rows = cur.fetchall()

    total_collection = 0.0
    total_payments = len(rows)

    for row in rows:
        amount = float(row["amount_received"] or 0)
        total_collection += amount

    conn.close()

    return render_template(
        "report_today_collection.html",
        rows=rows,
        today=today,
        total_collection=total_collection,
        total_payments=total_payments,
        branches=branches,
        branch_id=branch_id
    )


@app.route("/reports/student-outstanding")
@login_required
def student_outstanding_report():
    conn = get_conn()
    cur = conn.cursor()

    branch_id = request.args.get("branch_id", "").strip()

    cur.execute("""
        SELECT *
        FROM branches
        WHERE is_active = 1
        ORDER BY branch_name
    """)
    branches = cur.fetchall()

    student_query = """
        SELECT
            students.id,
            students.student_code,
            students.full_name,
            students.phone,
            students.email,
            students.status,
            students.branch_id,
            branch_master.branch_name
        FROM students
        LEFT JOIN branches AS branch_master
            ON students.branch_id = branch_master.id
    """

    params = []

    if branch_id:
        student_query += " WHERE students.branch_id = ? "
        params.append(branch_id)

    student_query += " ORDER BY students.full_name ASC "

    cur.execute(student_query, params)
    students = cur.fetchall()

    rows = []
    total_students = 0
    grand_total_billed = 0.0
    grand_total_paid = 0.0
    grand_total_balance = 0.0

    for student in students:
        student_id = student["id"]

        cur.execute("""
            SELECT
                COUNT(*) AS total_invoices,
                IFNULL(SUM(total_amount), 0) AS total_billed
            FROM invoices
            WHERE student_id = ?
        """, (student_id,))
        invoice_summary = cur.fetchone()

        cur.execute("""
            SELECT
                IFNULL(SUM(receipts.amount_received), 0) AS total_paid
            FROM receipts
            JOIN invoices
                ON receipts.invoice_id = invoices.id
            WHERE invoices.student_id = ?
        """, (student_id,))
        payment_summary = cur.fetchone()

        total_invoices = int(invoice_summary["total_invoices"] or 0)
        total_billed = float(invoice_summary["total_billed"] or 0)
        total_paid = float(payment_summary["total_paid"] or 0)
        balance = total_billed - total_paid

        # Only include students with outstanding balance
        if balance > 0:
            rows.append({
                "student_id": student["id"],
                "student_code": student["student_code"],
                "full_name": student["full_name"],
                "phone": student["phone"],
                "email": student["email"],
                "status": student["status"],
                "branch_name": student["branch_name"],
                "total_invoices": total_invoices,
                "total_billed": total_billed,
                "total_paid": total_paid,
                "balance": balance
            })

            total_students += 1
            grand_total_billed += total_billed
            grand_total_paid += total_paid
            grand_total_balance += balance

    conn.close()

    return render_template(
        "report_student_outstanding.html",
        rows=rows,
        total_students=total_students,
        total_billed=grand_total_billed,
        total_paid=grand_total_paid,
        total_balance=grand_total_balance,
        branches=branches,
        branch_id=branch_id
    )


@app.route("/reports/download/student-outstanding", methods=["GET"])
@login_required
def download_student_outstanding():
    """Download students with outstanding balances."""
    conn = get_conn()
    cur = conn.cursor()

    branch_id = request.args.get("branch_id", type=int)

    student_query = """
        SELECT
            students.id,
            students.student_code,
            students.full_name,
            students.phone,
            students.email,
            students.status,
            students.branch_id,
            branch_master.branch_name
        FROM students
        LEFT JOIN branches AS branch_master
            ON students.branch_id = branch_master.id
    """

    params = []

    if branch_id:
        student_query += " WHERE students.branch_id = ? "
        params.append(branch_id)

    student_query += " ORDER BY students.full_name ASC "

    cur.execute(student_query, params)
    students = cur.fetchall()

    # Prepare CSV output
    output = io.StringIO()
    fieldnames = [
        'student_code',
        'student_name',
        'email',
        'phone',
        'status',
        'branch_name',
        'total_invoices',
        'total_billed',
        'total_paid',
        'outstanding_balance'
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for student in students:
        student_id = student["id"]

        cur.execute("""
            SELECT
                COUNT(*) AS total_invoices,
                IFNULL(SUM(total_amount), 0) AS total_billed
            FROM invoices
            WHERE student_id = ?
        """, (student_id,))
        invoice_summary = cur.fetchone()

        cur.execute("""
            SELECT
                IFNULL(SUM(receipts.amount_received), 0) AS total_paid
            FROM receipts
            JOIN invoices
                ON receipts.invoice_id = invoices.id
            WHERE invoices.student_id = ?
        """, (student_id,))
        payment_summary = cur.fetchone()

        total_invoices = int(invoice_summary["total_invoices"] or 0)
        total_billed = float(invoice_summary["total_billed"] or 0)
        total_paid = float(payment_summary["total_paid"] or 0)
        balance = total_billed - total_paid

        # Only include students with outstanding balance
        if balance > 0:
            writer.writerow({
                'student_code': student["student_code"],
                'student_name': student["full_name"],
                'email': student["email"],
                'phone': student["phone"],
                'status': student["status"],
                'branch_name': student["branch_name"] or '',
                'total_invoices': total_invoices,
                'total_billed': f'{total_billed:.2f}',
                'total_paid': f'{total_paid:.2f}',
                'outstanding_balance': f'{balance:.2f}'
            })

    conn.close()

    # Return CSV as download
    output.seek(0)
    from datetime import datetime
    filename = f'student_outstanding_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'

    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=filename
    )


@app.route("/reports/unpaid-invoices")
@login_required
def unpaid_invoices_report():
    conn = get_conn()
    cur = conn.cursor()

    branch_id = request.args.get("branch_id", "").strip()

    cur.execute("""
        SELECT *
        FROM branches
        WHERE is_active = 1
        ORDER BY branch_name
    """)
    branches = cur.fetchall()

    query = """
        SELECT
            invoices.id,
            invoices.invoice_no,
            invoices.invoice_date,
            invoices.total_amount,
            invoices.status,
            invoices.branch_id,

            students.id AS student_id,
            students.student_code,
            students.full_name,
            students.phone,

            branch_master.branch_name,

            IFNULL(SUM(receipts.amount_received), 0) AS paid_amount

        FROM invoices
        JOIN students
            ON invoices.student_id = students.id
        LEFT JOIN receipts
            ON receipts.invoice_id = invoices.id
        LEFT JOIN branches AS branch_master
            ON invoices.branch_id = branch_master.id

        WHERE invoices.status IN ('unpaid', 'partially_paid')
    """

    params = []

    if branch_id:
        query += " AND invoices.branch_id = ? "
        params.append(branch_id)

    query += """
        GROUP BY invoices.id
        ORDER BY invoices.invoice_date ASC, invoices.id ASC
    """

    cur.execute(query, params)
    raw_rows = cur.fetchall()
    conn.close()

    rows = []
    total_invoices = 0
    total_amount = 0.0
    total_paid = 0.0
    total_balance = 0.0

    for row in raw_rows:
        invoice_total = float(row["total_amount"] or 0)
        paid_amount = float(row["paid_amount"] or 0)
        balance_amount = invoice_total - paid_amount

        rows.append({
            "id": row["id"],
            "invoice_no": row["invoice_no"],
            "invoice_date": row["invoice_date"],
            "total_amount": invoice_total,
            "paid_amount": paid_amount,
            "balance_amount": balance_amount,
            "status": row["status"],
            "student_id": row["student_id"],
            "student_code": row["student_code"],
            "full_name": row["full_name"],
            "phone": row["phone"],
            "branch_name": row["branch_name"]
        })

        total_invoices += 1
        total_amount += invoice_total
        total_paid += paid_amount
        total_balance += balance_amount

    return render_template(
        "report_unpaid_invoices.html",
        rows=rows,
        total_invoices=total_invoices,
        total_amount=total_amount,
        total_paid=total_paid,
        total_balance=total_balance,
        branches=branches,
        branch_id=branch_id
    )


@app.route("/reports/download/unpaid-invoices", methods=["GET"])
@login_required
def download_unpaid_invoices():
    """Download unpaid and partially paid invoices for installment planning."""
    conn = get_conn()
    cur = conn.cursor()

    branch_id = request.args.get("branch_id", type=int)

    query = """
        SELECT
            invoices.invoice_no,
            invoices.invoice_date,
            invoices.total_amount,
            students.full_name,
            students.student_code,
            students.email,
            students.phone,
            branch_master.branch_name,
            invoices.status,
            IFNULL(SUM(receipts.amount_received), 0) AS paid_amount

        FROM invoices
        JOIN students
            ON invoices.student_id = students.id
        LEFT JOIN receipts
            ON receipts.invoice_id = invoices.id
        LEFT JOIN branches AS branch_master
            ON invoices.branch_id = branch_master.id

        WHERE invoices.status IN ('unpaid', 'partially_paid')
    """

    params = []

    if branch_id:
        query += " AND invoices.branch_id = ? "
        params.append(branch_id)

    query += """
        GROUP BY invoices.id
        ORDER BY invoices.invoice_date ASC, invoices.id ASC
    """

    cur.execute(query, params)
    raw_rows = cur.fetchall()
    conn.close()

    # Create CSV output
    output = io.StringIO()
    fieldnames = [
        'invoice_number',
        'student_code',
        'student_name',
        'email',
        'phone',
        'invoice_date',
        'total_amount',
        'paid_amount',
        'balance_amount',
        'status',
        'branch_name'
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for row in raw_rows:
        invoice_total = float(row["total_amount"] or 0)
        paid_amount = float(row["paid_amount"] or 0)
        balance_amount = invoice_total - paid_amount

        writer.writerow({
            'invoice_number': row["invoice_no"],
            'student_code': row["student_code"],
            'student_name': row["full_name"],
            'email': row["email"],
            'phone': row["phone"],
            'invoice_date': row["invoice_date"],
            'total_amount': f'{invoice_total:.2f}',
            'paid_amount': f'{paid_amount:.2f}',
            'balance_amount': f'{balance_amount:.2f}',
            'status': row["status"],
            'branch_name': row["branch_name"] or ''
        })

    # Return CSV as download
    output.seek(0)
    from datetime import datetime
    filename = f'unpaid_invoices_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'

    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=filename
    )


@app.route("/reports/date-wise-collection", methods=["GET"])
@login_required
def date_wise_collection_report():
    conn = get_conn()
    cur = conn.cursor()

    from_date = request.args.get("from_date", "").strip()
    to_date = request.args.get("to_date", "").strip()
    branch_id = request.args.get("branch_id", "").strip()

    today = datetime.today().strftime("%Y-%m-%d")

    if not from_date:
        from_date = today
    if not to_date:
        to_date = today

    cur.execute("""
        SELECT *
        FROM branches
        WHERE is_active = 1
        ORDER BY branch_name
    """)
    branches = cur.fetchall()

    query = """
        SELECT
            receipts.id AS receipt_id,
            receipts.receipt_no,
            receipts.receipt_date,
            receipts.amount_received,

            invoices.id AS invoice_id,
            invoices.invoice_no,
            invoices.branch_id,

            students.id AS student_id,
            students.student_code,
            students.full_name,
            students.phone,

            users.full_name AS created_by_name,
            branch_master.branch_name

        FROM receipts
        JOIN invoices
            ON receipts.invoice_id = invoices.id
        JOIN students
            ON invoices.student_id = students.id
        LEFT JOIN users
            ON receipts.created_by = users.id
        LEFT JOIN branches AS branch_master
            ON invoices.branch_id = branch_master.id

        WHERE parse_date(receipts.receipt_date) BETWEEN ? AND ?
    """

    params = [from_date, to_date]

    if branch_id:
        query += " AND invoices.branch_id = ? "
        params.append(branch_id)

    query += " ORDER BY parse_date(receipts.receipt_date) DESC, receipts.id DESC "

    cur.execute(query, params)
    rows = cur.fetchall()

    total_collection = 0.0
    total_payments = len(rows)

    for row in rows:
        amount = float(row["amount_received"] or 0)
        total_collection += amount

    conn.close()

    return render_template(
        "report_date_wise_collection.html",
        rows=rows,
        from_date=from_date,
        to_date=to_date,
        branch_id=branch_id,
        branches=branches,
        total_collection=total_collection,
        total_payments=total_payments
    )


@app.route("/reports/course-wise-revenue")
@login_required
def course_wise_revenue_report():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            c.id AS course_id,
            c.course_name,
            c.duration
        FROM courses c
        ORDER BY c.course_name ASC
    """)
    courses = cur.fetchall()

    rows = []
    total_courses = 0
    grand_total_billed = 0.0
    grand_total_paid = 0.0
    grand_total_balance = 0.0

    for course in courses:
        course_id = course["course_id"]

        cur.execute("""
            SELECT
                COUNT(DISTINCT ii.invoice_id) AS total_invoices,
                COUNT(ii.id) AS total_item_rows,
                IFNULL(SUM(ii.line_total), 0) AS total_billed
            FROM invoice_items ii
            WHERE ii.course_id = ?
        """, (course_id,))
        billed_row = cur.fetchone()

        total_invoices = int(billed_row["total_invoices"] or 0)
        total_item_rows = int(billed_row["total_item_rows"] or 0)
        total_billed = float(billed_row["total_billed"] or 0)

        cur.execute("""
            SELECT DISTINCT ii.invoice_id
            FROM invoice_items ii
            WHERE ii.course_id = ?
        """, (course_id,))
        invoice_ids = cur.fetchall()

        total_paid = 0.0

        for inv in invoice_ids:
            invoice_id = inv["invoice_id"]

            cur.execute("""
                SELECT IFNULL(SUM(line_total), 0) AS course_total
                FROM invoice_items
                WHERE invoice_id = ? AND course_id = ?
            """, (invoice_id, course_id))
            course_total_row = cur.fetchone()
            course_total = float(course_total_row["course_total"] or 0)

            cur.execute("""
                SELECT total_amount
                FROM invoices
                WHERE id = ?
            """, (invoice_id,))
            invoice_row = cur.fetchone()
            invoice_total = float(invoice_row["total_amount"] or 0) if invoice_row else 0

            cur.execute("""
                SELECT IFNULL(SUM(amount_received), 0) AS invoice_paid
                FROM receipts
                WHERE invoice_id = ?
            """, (invoice_id,))
            paid_row = cur.fetchone()
            invoice_paid = float(paid_row["invoice_paid"] or 0)

            if invoice_total > 0:
                share_ratio = course_total / invoice_total
                total_paid += invoice_paid * share_ratio

        balance = total_billed - total_paid
        if balance < 0:
            balance = 0.0

        rows.append({
            "course_id": course_id,
            "course_name": course["course_name"],
            "duration": course["duration"],
            "total_invoices": total_invoices,
            "total_item_rows": total_item_rows,
            "total_billed": total_billed,
            "total_paid": total_paid,
            "balance": balance
        })

        total_courses += 1
        grand_total_billed += total_billed
        grand_total_paid += total_paid
        grand_total_balance += balance

    conn.close()

    return render_template(
        "report_course_wise_revenue.html",
        rows=rows,
        total_courses=total_courses,
        total_billed=grand_total_billed,
        total_paid=grand_total_paid,
        total_balance=grand_total_balance
    )


@app.route("/expenses")
@login_required
def expenses():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            expenses.*,
            branches.branch_name,
            expense_categories.category_name,
            users.full_name AS created_by_name
        FROM expenses
        JOIN branches
            ON expenses.branch_id = branches.id
        JOIN expense_categories
            ON expenses.category_id = expense_categories.id
        LEFT JOIN users
            ON expenses.created_by = users.id
        ORDER BY expenses.expense_date DESC, expenses.id DESC
    """)
    expenses = cur.fetchall()

    cur.execute("""
        SELECT IFNULL(SUM(amount), 0) AS total_expense
        FROM expenses
    """)
    total_expense = float(cur.fetchone()["total_expense"] or 0)

    conn.close()

    return render_template(
        "expenses.html",
        expenses=expenses,
        total_expense=total_expense
    )


@app.route("/expense/new", methods=["GET", "POST"])
@login_required
def expense_new():
    conn = get_conn()
    cur = conn.cursor()

    if request.method == "POST":
        expense_date = request.form["expense_date"]
        branch_id = request.form["branch_id"]
        category_id = request.form["category_id"]
        title = request.form["title"].strip()
        amount = float(request.form["amount"])
        payment_mode = request.form["payment_mode"]
        reference_no = request.form.get("reference_no", "").strip()
        notes = request.form.get("notes", "").strip()

        if not title:
            conn.close()
            flash("Expense title is required.", "danger")
            return redirect(url_for("expense_new"))

        if amount <= 0:
            conn.close()
            flash("Expense amount must be greater than 0.", "danger")
            return redirect(url_for("expense_new"))

        now = datetime.now().isoformat(timespec="seconds")

        cur.execute("""
            INSERT INTO expenses (
                expense_date,
                branch_id,
                category_id,
                title,
                amount,
                payment_mode,
                reference_no,
                notes,
                created_by,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            expense_date,
            branch_id,
            category_id,
            title,
            amount,
            payment_mode,
            reference_no,
            notes,
            session["user_id"],
            now,
            now
        ))

        expense_id = cur.lastrowid
        conn.commit()
        conn.close()

        safe_log_activity(
            user_id=session["user_id"],
            branch_id=branch_id,
            action_type="create",
            module_name="expenses",
            record_id=expense_id,
            description=f"Recorded expense '{title}' of ₹{amount:.2f}"
        )

        flash("Expense recorded successfully.", "success")
        return redirect(url_for("expenses"))

    cur.execute("""
        SELECT *
        FROM branches
        WHERE is_active = 1
        ORDER BY branch_name
    """)
    branches = cur.fetchall()

    cur.execute("""
        SELECT *
        FROM expense_categories
        WHERE is_active = 1
        ORDER BY category_name
    """)
    categories = cur.fetchall()

    conn.close()
    today = datetime.today().strftime("%Y-%m-%d")

    return render_template(
        "expense_form.html",
        branches=branches,
        categories=categories,
        today=today
    )


@app.route("/expense-category/new", methods=["GET", "POST"])
@login_required
def expense_category_new():
    if request.method == "POST":
        category_name = request.form["category_name"].strip()

        if not category_name:
            flash("Category name is required.", "danger")
            return redirect(url_for("expense_category_new"))

        conn = get_conn()
        cur = conn.cursor()

        cur.execute("SELECT id FROM expense_categories WHERE category_name = ?", (category_name,))
        existing = cur.fetchone()
        if existing:
            conn.close()
            flash("Category already exists.", "danger")
            return redirect(url_for("expense_category_new"))

        now = datetime.now().isoformat(timespec="seconds")

        cur.execute("""
            INSERT INTO expense_categories (category_name, is_active, created_at)
            VALUES (?, ?, ?)
        """, (
            category_name,
            1,
            now
        ))

        category_id = cur.lastrowid
        conn.commit()
        conn.close()

        safe_log_activity(
            user_id=session["user_id"],
            branch_id=session.get("branch_id"),
            action_type="create",
            module_name="expense_categories",
            record_id=category_id,
            description=f"Created expense category {category_name}"
        )

        flash("Expense category created successfully.", "success")
        return redirect(url_for("expense_categories"))

    return render_template("expense_category_form.html")


@app.route("/expense-categories")
@login_required
def expense_categories():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM expense_categories
        ORDER BY category_name
    """)
    categories = cur.fetchall()

    conn.close()
    return render_template("expense_categories.html", categories=categories)


@app.route("/reports/expenses", methods=["GET"])
@login_required
def expenses_report():
    conn = get_conn()
    cur = conn.cursor()

    from_date = request.args.get("from_date", "").strip()
    to_date = request.args.get("to_date", "").strip()
    branch_id = request.args.get("branch_id", "").strip()

    today = datetime.today().strftime("%Y-%m-%d")

    if not from_date:
        from_date = today
    if not to_date:
        to_date = today

    cur.execute("""
        SELECT *
        FROM branches
        WHERE is_active = 1
        ORDER BY branch_name
    """)
    branches = cur.fetchall()

    query = """
        SELECT
            expenses.id,
            expenses.expense_date,
            expenses.branch_id,
            expenses.category_id,
            expenses.title,
            expenses.amount,
            expenses.payment_mode,
            expenses.reference_no,
            expenses.notes,
            expenses.created_at,

            branches.branch_name,
            expense_categories.category_name,
            users.full_name AS created_by_name

        FROM expenses
        JOIN branches
            ON expenses.branch_id = branches.id
        JOIN expense_categories
            ON expenses.category_id = expense_categories.id
        LEFT JOIN users
            ON expenses.created_by = users.id

        WHERE expenses.expense_date BETWEEN ? AND ?
    """

    params = [from_date, to_date]

    if branch_id:
        query += " AND expenses.branch_id = ? "
        params.append(branch_id)

    query += " ORDER BY expenses.expense_date DESC, expenses.id DESC "

    cur.execute(query, params)
    rows = cur.fetchall()

    total_expense = 0.0
    total_entries = len(rows)

    cash_total = 0.0
    upi_total = 0.0
    bank_total = 0.0
    card_total = 0.0

    category_summary = {}

    for row in rows:
        amount = float(row["amount"] or 0)
        total_expense += amount

        mode = (row["payment_mode"] or "").lower()
        if mode == "cash":
            cash_total += amount
        elif mode == "upi":
            upi_total += amount
        elif mode == "bank_transfer":
            bank_total += amount
        elif mode == "card":
            card_total += amount

        category_name = row["category_name"] or "Uncategorized"
        if category_name not in category_summary:
            category_summary[category_name] = 0.0
        category_summary[category_name] += amount

    category_rows = [
        {"category_name": k, "amount": v}
        for k, v in sorted(category_summary.items(), key=lambda x: x[1], reverse=True)
    ]

    conn.close()

    return render_template(
        "report_expenses.html",
        rows=rows,
        branches=branches,
        branch_id=branch_id,
        from_date=from_date,
        to_date=to_date,
        total_expense=total_expense,
        total_entries=total_entries,
        cash_total=cash_total,
        upi_total=upi_total,
        bank_total=bank_total,
        card_total=card_total,
        category_rows=category_rows
    )

@app.route("/activity-logs", methods=["GET"])
@login_required
def activity_logs():
    if session.get("role") != "admin":
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    conn = get_conn()
    cur = conn.cursor()

    from_date = request.args.get("from_date", "").strip()
    to_date = request.args.get("to_date", "").strip()
    user_id = request.args.get("user_id", "").strip()
    branch_id = request.args.get("branch_id", "").strip()
    module_name = request.args.get("module_name", "").strip()

    today = datetime.today().strftime("%Y-%m-%d")

    if not from_date:
        from_date = today
    if not to_date:
        to_date = today

    # Filters data
    cur.execute("""
        SELECT id, full_name, username
        FROM users
        WHERE is_active = 1
        ORDER BY full_name
    """)
    users = cur.fetchall()

    cur.execute("""
        SELECT *
        FROM branches
        WHERE is_active = 1
        ORDER BY branch_name
    """)
    branches = cur.fetchall()

    cur.execute("""
        SELECT DISTINCT module_name
        FROM activity_logs
        ORDER BY module_name
    """)
    modules = cur.fetchall()

    query = """
        SELECT
            activity_logs.*,
            users.full_name,
            users.username,
            branches.branch_name
        FROM activity_logs
        LEFT JOIN users
            ON activity_logs.user_id = users.id
        LEFT JOIN branches
            ON activity_logs.branch_id = branches.id
        WHERE substr(activity_logs.created_at, 1, 10) BETWEEN ? AND ?
    """

    params = [from_date, to_date]

    if user_id:
        query += " AND activity_logs.user_id = ? "
        params.append(user_id)

    if branch_id:
        query += " AND activity_logs.branch_id = ? "
        params.append(branch_id)

    if module_name:
        query += " AND activity_logs.module_name = ? "
        params.append(module_name)

    query += " ORDER BY activity_logs.id DESC "

    cur.execute(query, params)
    logs = cur.fetchall()

    conn.close()

    return render_template(
        "activity_logs.html",
        logs=logs,
        users=users,
        branches=branches,
        modules=modules,
        from_date=from_date,
        to_date=to_date,
        user_id=user_id,
        branch_id=branch_id,
        module_name=module_name
    )

@app.route("/users")
@admin_required
def users():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            users.*,
            branches.branch_name
        FROM users
        LEFT JOIN branches
            ON users.branch_id = branches.id
        ORDER BY users.id DESC
    """)
    users_list = cur.fetchall()

    conn.close()
    return render_template("users.html", users=users_list)

@app.route("/user/new", methods=["GET", "POST"])
@admin_required
def user_new():
    conn = get_conn()
    cur = conn.cursor()

    if request.method == "POST":
        full_name = request.form["full_name"].strip()
        username = request.form["username"].strip()
        password = request.form["password"]
        role = request.form["role"]
        phone = request.form.get("phone", "").strip()
        branch_id = request.form["branch_id"]
        can_view_all_branches = 1 if request.form.get("can_view_all_branches") == "1" else 0

        if not full_name or not username or not password:
            conn.close()
            flash("Full name, username and password are required.", "danger")
            return redirect(url_for("user_new"))

        cur.execute("SELECT id FROM users WHERE username = ?", (username,))
        existing = cur.fetchone()
        if existing:
            conn.close()
            flash("Username already exists.", "danger")
            return redirect(url_for("user_new"))

        now = datetime.now().isoformat(timespec="seconds")

        cur.execute("""
            INSERT INTO users (
                full_name,
                username,
                password_hash,
                role,
                phone,
                is_active,
                created_at,
                updated_at,
                branch_id,
                can_view_all_branches
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            full_name,
            username,
            generate_password_hash(password),
            role,
            phone,
            1,
            now,
            now,
            branch_id,
            can_view_all_branches
        ))

        user_id = cur.lastrowid
        conn.commit()
        conn.close()

        safe_log_activity(
            user_id=session["user_id"],
            branch_id=branch_id,
            action_type="create",
            module_name="users",
            record_id=user_id,
            description=f"Created user {username} ({role})"
        )

        flash("User created successfully.", "success")
        return redirect(url_for("users"))

    cur.execute("""
        SELECT *
        FROM branches
        WHERE is_active = 1
        ORDER BY branch_name
    """)
    branches = cur.fetchall()
    conn.close()

    return render_template("user_form.html", user=None, branches=branches)

@app.route("/user/<int:user_id>/edit", methods=["GET", "POST"])
@admin_required
def user_edit(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    user = cur.fetchone()

    if not user:
        conn.close()
        flash("User not found.", "danger")
        return redirect(url_for("users"))

    if request.method == "POST":
        full_name = request.form["full_name"].strip()
        username = request.form["username"].strip()
        password = request.form.get("password", "")
        role = request.form["role"]
        phone = request.form.get("phone", "").strip()
        branch_id = request.form["branch_id"]
        can_view_all_branches = 1 if request.form.get("can_view_all_branches") == "1" else 0

        if not full_name or not username:
            conn.close()
            flash("Full name and username are required.", "danger")
            return redirect(url_for("user_edit", user_id=user_id))

        cur.execute("""
            SELECT id FROM users
            WHERE username = ? AND id != ?
        """, (username, user_id))
        existing = cur.fetchone()

        if existing:
            conn.close()
            flash("Username already exists.", "danger")
            return redirect(url_for("user_edit", user_id=user_id))

        now = datetime.now().isoformat(timespec="seconds")

        if password.strip():
            cur.execute("""
                UPDATE users
                SET full_name = ?, username = ?, password_hash = ?, role = ?,
                    phone = ?, branch_id = ?, can_view_all_branches = ?, updated_at = ?
                WHERE id = ?
            """, (
                full_name,
                username,
                generate_password_hash(password),
                role,
                phone,
                branch_id,
                can_view_all_branches,
                now,
                user_id
            ))
        else:
            cur.execute("""
                UPDATE users
                SET full_name = ?, username = ?, role = ?,
                    phone = ?, branch_id = ?, can_view_all_branches = ?, updated_at = ?
                WHERE id = ?
            """, (
                full_name,
                username,
                role,
                phone,
                branch_id,
                can_view_all_branches,
                now,
                user_id
            ))

        conn.commit()
        conn.close()

        safe_log_activity(
            user_id=session["user_id"],
            branch_id=branch_id,
            action_type="update",
            module_name="users",
            record_id=user_id,
            description=f"Updated user {username}"
        )

        flash("User updated successfully.", "success")
        return redirect(url_for("users"))

    cur.execute("""
        SELECT *
        FROM branches
        WHERE is_active = 1
        ORDER BY branch_name
    """)
    branches = cur.fetchall()
    conn.close()

    return render_template("user_form.html", user=user, branches=branches)

@app.route("/user/<int:user_id>/toggle-status", methods=["POST"])
@admin_required
def user_toggle_status(user_id):
    if user_id == session.get("user_id"):
        flash("You cannot deactivate your own account.", "danger")
        return redirect(url_for("users"))

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    user = cur.fetchone()

    if not user:
        conn.close()
        flash("User not found.", "danger")
        return redirect(url_for("users"))

    new_status = 0 if user["is_active"] == 1 else 1
    now = datetime.now().isoformat(timespec="seconds")

    cur.execute("""
        UPDATE users
        SET is_active = ?, updated_at = ?
        WHERE id = ?
    """, (new_status, now, user_id))

    conn.commit()
    conn.close()

    action_word = "activated" if new_status == 1 else "deactivated"

    safe_log_activity(
        user_id=session["user_id"],
        branch_id=user["branch_id"],
        action_type="update",
        module_name="users",
        record_id=user_id,
        description=f"{action_word.capitalize()} user {user['username']}"
    )

    flash(f"User {action_word} successfully.", "success")
    return redirect(url_for("users"))


# ============ IMPORT ROUTES ============

@app.route("/import")
@admin_required
def import_center():
    return render_template("import_center.html")


@app.route("/import/students", methods=["GET", "POST"])
@admin_required
def import_students_page():
    conn = get_conn()
    cur = conn.cursor()
    
    # Fetch all active branches
    cur.execute("SELECT id, branch_code, branch_name, address FROM branches WHERE is_active = 1 ORDER BY branch_name")
    branches = cur.fetchall()
    
    # Get default branch (Head Office)
    cur.execute("SELECT id FROM branches WHERE branch_code = ?", ("HO",))
    default_branch = cur.fetchone()
    default_branch_id = default_branch["id"] if default_branch else 1
    
    conn.close()
    
    import_results = None
    
    if request.method == "POST":
        if 'csv_file' not in request.files:
            flash("No file selected.", "danger")
            return redirect(url_for("import_students_page"))
        
        file = request.files['csv_file']
        
        if file.filename == '':
            flash("No file selected.", "danger")
            return redirect(url_for("import_students_page"))
        
        if not file.filename.endswith('.csv'):
            flash("Please upload a CSV file.", "danger")
            return redirect(url_for("import_students_page"))
        
        try:
            # Read CSV file
            stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
            csv_reader = csv.DictReader(stream)
            
            if not csv_reader.fieldnames:
                flash("CSV file is empty.", "danger")
                return redirect(url_for("import_students_page"))
            
            conn = get_conn()
            cur = conn.cursor()
            
            # Get default branch (Head Office)
            cur.execute("SELECT id FROM branches WHERE branch_code = ?", ("HO",))
            default_branch = cur.fetchone()
            default_branch_id = default_branch["id"] if default_branch else 1
            
            success_count = 0
            errors = []
            row_num = 2  # Start from row 2 (row 1 is headers)
            now = datetime.now().isoformat(timespec="seconds")
            
            for row in csv_reader:
                try:
                    full_name = row.get('full_name', '').strip()
                    phone = row.get('phone', '').strip()
                    gender = row.get('gender', '').strip()
                    email = row.get('email', '').strip()
                    address = row.get('address', '').strip()
                    education_level = row.get('education_level', '').strip()
                    qualification = row.get('qualification', '').strip()
                    employment_status = row.get('employment_status', '').strip()
                    status = row.get('status', 'active').strip()
                    branch_code = row.get('branch_code', '').strip()
                    
                    # Validate required fields
                    if not full_name:
                        errors.append({
                            'row': row_num,
                            'message': 'Missing full_name (required)'
                        })
                        row_num += 1
                        continue
                    
                    if not phone:
                        errors.append({
                            'row': row_num,
                            'message': 'Missing phone (required)'
                        })
                        row_num += 1
                        continue
                    
                    # Use default branch if not provided
                    if not branch_code:
                        branch_id = default_branch_id
                    else:
                        # Look up branch by code
                        cur.execute("SELECT id FROM branches WHERE branch_code = ?", (branch_code,))
                        branch = cur.fetchone()
                        if branch:
                            branch_id = branch["id"]
                        else:
                            errors.append({
                                'row': row_num,
                                'message': f'Invalid branch_code: {branch_code}'
                            })
                            row_num += 1
                            continue
                    
                    # Validate education level if provided
                    if education_level and education_level not in QUALIFICATION_LEVELS:
                        errors.append({
                            'row': row_num,
                            'message': f'Invalid education_level: {education_level}. Must be one of: {", ".join(QUALIFICATION_LEVELS.keys())}'
                        })
                        row_num += 1
                        continue
                    
                    # Validate employment status if provided
                    valid_statuses = ['unemployed', 'employed', 'self_employed', 'student']
                    if employment_status and employment_status not in valid_statuses:
                        errors.append({
                            'row': row_num,
                            'message': f'Invalid employment_status: {employment_status}. Must be one of: {", ".join(valid_statuses)}'
                        })
                        row_num += 1
                        continue
                    
                    # Validate gender if provided
                    valid_genders = ['Male', 'Female', 'Other']
                    if gender and gender not in valid_genders:
                        errors.append({
                            'row': row_num,
                            'message': f'Invalid gender: {gender}. Must be one of: {", ".join(valid_genders)}'
                        })
                        row_num += 1
                        continue
                    
                    # Validate status if provided
                    valid_statuses_list = ['active', 'completed', 'dropped']
                    if status and status not in valid_statuses_list:
                        errors.append({
                            'row': row_num,
                            'message': f'Invalid status: {status}. Must be one of: {", ".join(valid_statuses_list)}'
                        })
                        row_num += 1
                        continue
                    
                    # Check if student with same registration number already exists (duplicate check)
                    student_code = row.get('student_code', '').strip()
                    if not student_code:
                        # Auto-generate registration number if not provided
                        cur.execute("SELECT student_code FROM students ORDER BY CAST(student_code AS INTEGER) DESC LIMIT 1")
                        result = cur.fetchone()
                        if result and result["student_code"]:
                            try:
                                max_reg = int(result["student_code"])
                                student_code = str(max_reg + 1)
                            except (ValueError, TypeError):
                                student_code = str(1515001)
                        else:
                            student_code = str(1515001)
                    else:
                        # Check if provided registration number already exists
                        cur.execute("SELECT id FROM students WHERE student_code = ?", (student_code,))
                        if cur.fetchone():
                            errors.append({
                                'row': row_num,
                                'message': f'Student with registration number {student_code} already exists. Duplicate skipped.'
                            })
                            row_num += 1
                            continue
                    
                    # Insert student
                    cur.execute("""
                        INSERT INTO students (
                            student_code,
                            full_name,
                            phone,
                            gender,
                            email,
                            address,
                            education_level,
                            qualification,
                            employment_status,
                            joined_date,
                            status,
                            branch_id,
                            created_at,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        student_code,
                        full_name,
                        phone,
                        gender,
                        email,
                        address,
                        education_level,
                        qualification,
                        employment_status,
                        now,
                        status,
                        branch_id,
                        now,
                        now
                    ))
                    
                    success_count += 1
                    
                except Exception as e:
                    errors.append({
                        'row': row_num,
                        'message': str(e)
                    })
                
                row_num += 1
            
            conn.commit()
            conn.close()
            
            import_results = {
                'success_count': success_count,
                'errors': errors
            }
            
            if success_count > 0:
                flash(f"Successfully imported {success_count} student(s).", "success")
            
        except Exception as e:
            flash(f"Error processing file: {str(e)}", "danger")
            return redirect(url_for("import_students_page"))
    
    return render_template(
        "import_students.html",
        import_results=import_results,
        branches=branches
    )


@app.route("/import/courses", methods=["GET", "POST"])
@admin_required
def import_courses_page():
    import_results = None
    
    if request.method == "POST":
        if 'csv_file' not in request.files:
            flash("No file selected.", "danger")
            return redirect(url_for("import_courses_page"))
        
        file = request.files['csv_file']
        
        if file.filename == '':
            flash("No file selected.", "danger")
            return redirect(url_for("import_courses_page"))
        
        if not file.filename.endswith('.csv'):
            flash("Please upload a CSV file.", "danger")
            return redirect(url_for("import_courses_page"))
        
        try:
            # Read CSV file
            stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
            csv_reader = csv.DictReader(stream)
            
            if not csv_reader.fieldnames:
                flash("CSV file is empty.", "danger")
                return redirect(url_for("import_courses_page"))
            
            conn = get_conn()
            cur = conn.cursor()
            
            success_count = 0
            errors = []
            row_num = 2  # Start from row 2 (row 1 is headers)
            now = datetime.now().isoformat(timespec="seconds")
            
            for row in csv_reader:
                try:
                    course_name = row.get('course_name', '').strip()
                    course_type = row.get('course_type', 'standard').strip().lower()
                    duration = row.get('duration', '').strip()
                    fee = row.get('fee', '').strip()
                    
                    # Validate required fields
                    if not course_name:
                        errors.append({
                            'row': row_num,
                            'message': 'Missing course_name (required)'
                        })
                        row_num += 1
                        continue
                    
                    # Validate course_type
                    if course_type not in ['standard', 'combo']:
                        course_type = 'standard'
                    
                    # Check if course already exists
                    cur.execute("SELECT id FROM courses WHERE course_name = ?", (course_name,))
                    if cur.fetchone():
                        errors.append({
                            'row': row_num,
                            'message': f'Course "{course_name}" already exists'
                        })
                        row_num += 1
                        continue
                    
                    # Validate and convert fee if provided
                    if fee:
                        try:
                            fee = float(fee)
                        except ValueError:
                            errors.append({
                                'row': row_num,
                                'message': f'Invalid fee amount: {fee}'
                            })
                            row_num += 1
                            continue
                    else:
                        fee = 0
                    
                    # Insert course
                    cur.execute("""
                        INSERT INTO courses (
                            course_name,
                            course_type,
                            duration,
                            fee,
                            is_active,
                            created_at,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        course_name,
                        course_type,
                        duration,
                        fee,
                        1,
                        now,
                        now
                    ))
                    
                    success_count += 1
                    
                except Exception as e:
                    errors.append({
                        'row': row_num,
                        'message': str(e)
                    })
                
                row_num += 1
            
            conn.commit()
            conn.close()
            
            import_results = {
                'success_count': success_count,
                'errors': errors
            }
            
            if success_count > 0:
                flash(f"Successfully imported {success_count} course(s).", "success")
            
        except Exception as e:
            flash(f"Error processing file: {str(e)}", "danger")
            return redirect(url_for("import_courses_page"))
    
    return render_template(
        "import_courses.html",
        import_results=import_results
    )


@app.route("/admin/import/invoices", methods=["GET", "POST"])
@login_required
@admin_required
def import_invoices_page():
    import_results = None
    
    if request.method == "POST":
        if 'csv_file' not in request.files:
            flash("No file selected.", "danger")
            return redirect(url_for("import_invoices_page"))
        
        file = request.files['csv_file']
        
        if file.filename == '':
            flash("No file selected.", "danger")
            return redirect(url_for("import_invoices_page"))
        
        if not file.filename.endswith('.csv'):
            flash("Please upload a CSV file.", "danger")
            return redirect(url_for("import_invoices_page"))
        
        try:
            stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
            csv_reader = csv.DictReader(stream)
            
            if not csv_reader.fieldnames:
                flash("CSV file is empty.", "danger")
                return redirect(url_for("import_invoices_page"))
            
            conn = get_conn()
            cur = conn.cursor()
            
            success_count = 0
            errors = []
            row_num = 2
            now = datetime.now().isoformat(timespec="seconds")
            admin_id = session["user_id"]
            
            for row in csv_reader:
                try:
                    student_reg_no = row.get('student_reg_no', '').strip()
                    invoice_no = row.get('invoice_no', '').strip()
                    invoice_date = row.get('invoice_date', '').strip()
                    course_name = row.get('course_name', '').strip()
                    total_fee = row.get('total_fee', '').strip()
                    discount_amount = row.get('discount_amount', '0').strip()
                    notes = row.get('notes', '').strip()
                    
                    # Validate required fields
                    if not student_reg_no or not invoice_no or not invoice_date or not course_name or not total_fee:
                        errors.append({
                            'row': row_num,
                            'message': 'Missing required field (student_reg_no, invoice_no, invoice_date, course_name, total_fee)'
                        })
                        row_num += 1
                        continue
                    
                    # Check if invoice already exists
                    cur.execute("SELECT id FROM invoices WHERE invoice_no = ?", (invoice_no,))
                    if cur.fetchone():
                        errors.append({
                            'row': row_num,
                            'message': f'Invoice {invoice_no} already exists'
                        })
                        row_num += 1
                        continue
                    
                    # Find student by registration number
                    cur.execute("SELECT id FROM students WHERE student_code = ?", (student_reg_no,))
                    student = cur.fetchone()
                    if not student:
                        errors.append({
                            'row': row_num,
                            'message': f'Student with reg number {student_reg_no} not found'
                        })
                        row_num += 1
                        continue
                    student_id = student['id']
                    
                    # Find course by name
                    cur.execute("SELECT id, fee FROM courses WHERE course_name = ? AND is_active = 1", (course_name,))
                    course = cur.fetchone()
                    if not course:
                        errors.append({
                            'row': row_num,
                            'message': f'Course "{course_name}" not found or is inactive'
                        })
                        row_num += 1
                        continue
                    course_id = course['id']
                    course_fee = course['fee']
                    
                    # Convert total_fee and discount_amount
                    try:
                        total_fee = float(total_fee)
                        discount_amount = float(discount_amount) if discount_amount else 0
                    except ValueError:
                        errors.append({
                            'row': row_num,
                            'message': 'Invalid total_fee or discount_amount (must be numbers)'
                        })
                        row_num += 1
                        continue
                    
                    # Calculate net amount
                    subtotal = total_fee
                    net_amount = subtotal - discount_amount
                    
                    # Determine discount type
                    discount_type = 'fixed' if discount_amount > 0 else 'none'
                    
                    # Insert invoice
                    cur.execute("""
                        INSERT INTO invoices (
                            invoice_no,
                            student_id,
                            invoice_date,
                            subtotal,
                            discount_type,
                            discount_value,
                            discount_amount,
                            total_amount,
                            installment_type,
                            status,
                            notes,
                            created_by,
                            created_at,
                            updated_at,
                            branch_id
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        invoice_no,
                        student_id,
                        invoice_date,
                        subtotal,
                        discount_type,
                        discount_amount,
                        discount_amount,
                        net_amount,
                        'full',
                        'unpaid',
                        notes,
                        admin_id,
                        now,
                        now,
                        session.get('branch_id', 1)
                    ))
                    
                    invoice_id = cur.lastrowid
                    
                    # Insert invoice item
                    cur.execute("""
                        INSERT INTO invoice_items (
                            invoice_id,
                            course_id,
                            description,
                            quantity,
                            unit_price,
                            line_total,
                            created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        invoice_id,
                        course_id,
                        course_name,
                        1,
                        course_fee,
                        course_fee,
                        now
                    ))
                    
                    success_count += 1
                    
                except Exception as e:
                    errors.append({
                        'row': row_num,
                        'message': str(e)
                    })
                
                row_num += 1
            
            conn.commit()
            conn.close()
            
            import_results = {
                'success_count': success_count,
                'errors': errors
            }
            
            if success_count > 0:
                flash(f"Successfully imported {success_count} invoice(s).", "success")
            
        except Exception as e:
            flash(f"Error processing file: {str(e)}", "danger")
            return redirect(url_for("import_invoices_page"))
    
    # Fetch active courses for reference
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, course_name, course_type, fee, is_active FROM courses WHERE is_active = 1 ORDER BY course_name")
    courses = cur.fetchall()
    conn.close()
    
    return render_template(
        "import_invoices.html",
        import_results=import_results,
        courses=courses
    )





@app.route("/admin/import/receipts", methods=["GET", "POST"])
@login_required
@admin_required
def import_receipts_page():
    import_results = None
    
    if request.method == "POST":
        if 'csv_file' not in request.files:
            flash("No file selected.", "danger")
            return redirect(url_for("import_receipts_page"))
        
        file = request.files['csv_file']
        
        if file.filename == '':
            flash("No file selected.", "danger")
            return redirect(url_for("import_receipts_page"))
        
        if not file.filename.endswith('.csv'):
            flash("Please upload a CSV file.", "danger")
            return redirect(url_for("import_receipts_page"))
        
        try:
            stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
            csv_reader = csv.DictReader(stream)
            
            if not csv_reader.fieldnames:
                flash("CSV file is empty.", "danger")
                return redirect(url_for("import_receipts_page"))
            
            conn = get_conn()
            cur = conn.cursor()
            
            success_count = 0
            errors = []
            row_num = 2
            now = datetime.now().isoformat(timespec="seconds")
            admin_id = session["user_id"]
            
            for row in csv_reader:
                try:
                    receipt_no = row.get('receipt_no', '').strip()
                    invoice_number = row.get('invoice_number', '').strip()
                    receipt_date = row.get('receipt_date', '').strip()
                    amount_received = row.get('amount_received', '').strip()
                    payment_mode = row.get('payment_mode', 'cash').strip().lower()
                    
                    # Validate required fields
                    if not receipt_no or not invoice_number or not receipt_date or not amount_received:
                        errors.append({
                            'row': row_num,
                            'message': 'Missing required field (receipt_no, invoice_number, receipt_date, amount_received)'
                        })
                        row_num += 1
                        continue
                    
                    # Validate payment_mode
                    valid_payment_modes = ['cash', 'upi', 'bank_transfer', 'card']
                    if payment_mode not in valid_payment_modes:
                        payment_mode = 'cash'  # Default to cash if invalid
                    
                    # Check if receipt already exists
                    cur.execute("SELECT id FROM receipts WHERE receipt_no = ?", (receipt_no,))
                    if cur.fetchone():
                        errors.append({
                            'row': row_num,
                            'message': f'Receipt {receipt_no} already exists'
                        })
                        row_num += 1
                        continue
                    
                    # Find invoice by invoice_number
                    cur.execute("SELECT id FROM invoices WHERE invoice_no = ?", (invoice_number,))
                    invoice = cur.fetchone()
                    
                    if not invoice:
                        errors.append({
                            'row': row_num,
                            'message': f'Invoice {invoice_number} not found'
                        })
                        row_num += 1
                        continue
                    
                    invoice_id = invoice['id']
                    
                    # Convert amount_received
                    try:
                        amount_received = float(amount_received)
                    except ValueError:
                        errors.append({
                            'row': row_num,
                            'message': 'Invalid amount_received (must be a number)'
                        })
                        row_num += 1
                        continue
                    
                    # Insert receipt linked to invoice
                    cur.execute("""
                        INSERT INTO receipts (
                            receipt_no,
                            invoice_id,
                            receipt_date,
                            amount_received,
                            payment_mode,
                            created_by,
                            created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        receipt_no,
                        invoice_id,
                        receipt_date,
                        amount_received,
                        payment_mode,
                        admin_id,
                        now
                    ))
                    
                    # Update invoice status based on total receipts
                    cur.execute("""
                        SELECT SUM(amount_received) as total_received
                        FROM receipts
                        WHERE invoice_id = ?
                    """, (invoice_id,))
                    receipt_result = cur.fetchone()
                    total_received = receipt_result['total_received'] if receipt_result['total_received'] else 0
                    
                    cur.execute("SELECT total_amount FROM invoices WHERE id = ?", (invoice_id,))
                    invoice_total = cur.fetchone()['total_amount']
                    
                    if total_received >= invoice_total:
                        new_status = 'paid'
                    elif total_received > 0:
                        new_status = 'partially_paid'
                    else:
                        new_status = 'unpaid'
                    
                    cur.execute("""
                        UPDATE invoices
                        SET status = ?, updated_at = ?
                        WHERE id = ?
                    """, (new_status, now, invoice_id))
                    
                    success_count += 1
                    
                except Exception as e:
                    errors.append({
                        'row': row_num,
                        'message': str(e)
                    })
                
                row_num += 1
            
            conn.commit()
            conn.close()

            import_results = {
                'success_count': success_count,
                'errors': errors
            }

            if success_count > 0:
                flash(f"Successfully imported {success_count} receipt(s).", "success")

        except Exception as e:
            flash(f"Error processing file: {str(e)}", "danger")
            return redirect(url_for("import_receipts_page"))

    return render_template(
        "import_receipts.html",
        import_results=import_results
    )


@app.route("/admin/import/installments", methods=["GET", "POST"])
@login_required
@admin_required
def import_installments_page():
    import_results = None

    if request.method == "POST":
        if 'csv_file' not in request.files:
            flash("No file selected.", "danger")
            return redirect(url_for("import_installments_page"))

        file = request.files['csv_file']

        if file.filename == '':
            flash("No file selected.", "danger")
            return redirect(url_for("import_installments_page"))

        if not file.filename.endswith('.csv'):
            flash("Please upload a CSV file.", "danger")
            return redirect(url_for("import_installments_page"))

        try:
            stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
            csv_reader = csv.DictReader(stream)

            if not csv_reader.fieldnames:
                flash("CSV file is empty.", "danger")
                return redirect(url_for("import_installments_page"))

            conn = get_conn()
            cur = conn.cursor()

            success_count = 0
            errors = []
            row_num = 2
            now = datetime.now().isoformat(timespec="seconds")

            # Group rows by invoice for validation
            invoices_data = {}
            rows_list = list(csv_reader)

            for row in rows_list:
                try:
                    invoice_number = row.get('invoice_number', '').strip()
                    installment_no = row.get('installment_no', '').strip()
                    due_date = row.get('due_date', '').strip()
                    amount_due = row.get('amount_due', '').strip()

                    # Validate required fields
                    if not invoice_number or not installment_no or not due_date or not amount_due:
                        errors.append({
                            'row': row_num,
                            'message': 'Missing required field (invoice_number, installment_no, due_date, amount_due)'
                        })
                        row_num += 1
                        continue

                    # Validate installment_no is numeric
                    try:
                        installment_no = int(installment_no)
                    except ValueError:
                        errors.append({
                            'row': row_num,
                            'message': f'installment_no must be a number, got: {installment_no}'
                        })
                        row_num += 1
                        continue

                    # Validate amount_due is numeric
                    try:
                        amount_due = float(amount_due)
                    except ValueError:
                        errors.append({
                            'row': row_num,
                            'message': f'amount_due must be a number, got: {amount_due}'
                        })
                        row_num += 1
                        continue

                    # Validate due_date format
                    try:
                        from datetime import datetime as dt
                        dt.strptime(due_date, '%Y-%m-%d')
                    except ValueError:
                        errors.append({
                            'row': row_num,
                            'message': f'due_date must be in YYYY-MM-DD format, got: {due_date}'
                        })
                        row_num += 1
                        continue

                    # Check if invoice exists
                    cur.execute("SELECT id, total_amount FROM invoices WHERE invoice_no = ?", (invoice_number,))
                    invoice = cur.fetchone()

                    if not invoice:
                        errors.append({
                            'row': row_num,
                            'message': f'Invoice not found: {invoice_number}'
                        })
                        row_num += 1
                        continue

                    # Group by invoice
                    if invoice_number not in invoices_data:
                        invoices_data[invoice_number] = {
                            'invoice_id': invoice['id'],
                            'total_amount': invoice['total_amount'],
                            'installments': []
                        }

                    invoices_data[invoice_number]['installments'].append({
                        'row': row_num,
                        'installment_no': installment_no,
                        'due_date': due_date,
                        'amount_due': amount_due
                    })

                    row_num += 1

                except Exception as e:
                    errors.append({
                        'row': row_num,
                        'message': str(e)
                    })
                    row_num += 1

            # Validate amounts for each invoice
            for invoice_number, invoice_data in invoices_data.items():
                total_installment_amount = sum(inst['amount_due'] for inst in invoice_data['installments'])
                if abs(total_installment_amount - invoice_data['total_amount']) > 0.01:
                    for inst in invoice_data['installments']:
                        errors.append({
                            'row': inst['row'],
                            'message': f'Sum of installments ({total_installment_amount:.2f}) does not match invoice total ({invoice_data["total_amount"]:.2f})'
                        })

            # Only proceed with import if no errors
            if not errors:
                for invoice_number, invoice_data in invoices_data.items():
                    try:
                        invoice_id = invoice_data['invoice_id']

                        # Get total receipts for this invoice
                        cur.execute("""
                            SELECT IFNULL(SUM(amount_received), 0) AS total_received
                            FROM receipts
                            WHERE invoice_id = ?
                        """, (invoice_id,))
                        total_received = float(cur.fetchone()["total_received"] or 0)

                        # Delete existing installments (if any) and create new ones
                        cur.execute("DELETE FROM installment_plans WHERE invoice_id = ?", (invoice_id,))

                        remaining_payment = total_received

                        # Insert new installments
                        for inst in invoice_data['installments']:
                            inst_amount_due = inst['amount_due']

                            # Determine amount_paid and status based on receipts
                            if remaining_payment <= 0:
                                amount_paid = 0.0
                                status = 'pending'
                                remarks = None
                            elif remaining_payment >= inst_amount_due:
                                amount_paid = inst_amount_due
                                status = 'paid'
                                remarks = 'Fully paid'
                                remaining_payment -= inst_amount_due
                            else:
                                amount_paid = remaining_payment
                                status = 'partially_paid'
                                remarks = f'Partial payment of {remaining_payment:.2f}'
                                remaining_payment = 0

                            cur.execute("""
                                INSERT INTO installment_plans (
                                    invoice_id,
                                    installment_no,
                                    due_date,
                                    amount_due,
                                    amount_paid,
                                    status,
                                    remarks,
                                    created_at
                                )
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                invoice_id,
                                inst['installment_no'],
                                inst['due_date'],
                                inst_amount_due,
                                amount_paid,
                                status,
                                remarks,
                                now
                            ))

                        # Update invoice status
                        cur.execute("""
                            SELECT
                                COUNT(CASE WHEN status = 'paid' THEN 1 END) as paid_count,
                                COUNT(CASE WHEN status = 'partially_paid' THEN 1 END) as partial_count,
                                COUNT(*) as total_count
                            FROM installment_plans
                            WHERE invoice_id = ?
                        """, (invoice_id,))

                        status_info = cur.fetchone()

                        if status_info['paid_count'] == status_info['total_count']:
                            invoice_status = 'paid'
                        elif status_info['paid_count'] > 0 or status_info['partial_count'] > 0:
                            invoice_status = 'partially_paid'
                        else:
                            invoice_status = 'unpaid'

                        cur.execute("""
                            UPDATE invoices
                            SET status = ?, updated_at = ?
                            WHERE id = ?
                        """, (invoice_status, now, invoice_id))

                        success_count += 1

                    except Exception as e:
                        errors.append({
                            'row': 'Invoice: ' + invoice_number,
                            'message': f'Error importing: {str(e)}'
                        })

                conn.commit()

            conn.close()

            import_results = {
                'success_count': success_count,
                'errors': errors
            }

            if success_count > 0:
                flash(f"Successfully imported installment plans for {success_count} invoice(s).", "success")

        except Exception as e:
            flash(f"Error processing file: {str(e)}", "danger")
            return redirect(url_for("import_installments_page"))

    return render_template(
        "import_installments.html",
        import_results=import_results
    )


if __name__ == "__main__":
    app.run(debug=True)
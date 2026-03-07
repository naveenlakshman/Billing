from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import check_password_hash
from config import SECRET_KEY
from db import init_db, get_conn
from datetime import datetime

app = Flask(__name__)
app.secret_key = SECRET_KEY

# Create database and tables when app starts
init_db()


def login_required(route_function):
    from functools import wraps

    @wraps(route_function)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login first.", "warning")
            return redirect(url_for("login"))
        return route_function(*args, **kwargs)

    return wrapper

def get_invoice_payment_summary(invoice_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT total_amount
        FROM invoices
        WHERE id = ?
    """, (invoice_id,))
    invoice = cur.fetchone()

    cur.execute("""
        SELECT IFNULL(SUM(amount_paid), 0) AS total_paid
        FROM payments
        WHERE invoice_id = ?
    """, (invoice_id,))
    paid_row = cur.fetchone()

    conn.close()

    total_amount = float(invoice["total_amount"]) if invoice else 0
    total_paid = float(paid_row["total_paid"]) if paid_row else 0
    balance = total_amount - total_paid

    return {
        "total_amount": total_amount,
        "total_paid": total_paid,
        "balance": balance
    }

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
        SELECT IFNULL(SUM(amount_paid), 0) AS total_paid
        FROM payments
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


def allocate_payment_to_installments(conn, invoice_id, payment_amount):
    cur = conn.cursor()

    remaining = float(payment_amount)

    cur.execute("""
        SELECT *
        FROM installment_plans
        WHERE invoice_id = ?
        ORDER BY installment_no ASC, id ASC
    """, (invoice_id,))
    installments = cur.fetchall()

    now = datetime.now().isoformat(timespec="seconds")

    for ins in installments:
        if remaining <= 0:
            break

        amount_due = float(ins["amount_due"] or 0)
        amount_paid = float(ins["amount_paid"] or 0)
        pending_amount = amount_due - amount_paid

        if pending_amount <= 0:
            continue

        allocate = min(remaining, pending_amount)
        new_paid = amount_paid + allocate

        if new_paid <= 0:
            status = "pending"
        elif new_paid < amount_due:
            status = "partially_paid"
        else:
            status = "paid"

        cur.execute("""
            UPDATE installment_plans
            SET amount_paid = ?, status = ?, updated_at = ?
            WHERE id = ?
        """, (
            new_paid,
            status,
            now,
            ins["id"]
        ))

        remaining -= allocate

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
            flash("Login successful.", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid username or password.", "danger")

    return render_template("login.html")


@app.route("/dashboard")
@login_required
def dashboard():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) AS total FROM students")
    total_students = cur.fetchone()["total"]

    cur.execute("SELECT COUNT(*) AS total FROM courses")
    total_courses = cur.fetchone()["total"]

    cur.execute("SELECT COUNT(*) AS total FROM invoices")
    total_invoices = cur.fetchone()["total"]

    cur.execute("""
        SELECT IFNULL(SUM(amount_paid), 0) AS total_collection
        FROM payments
    """)
    total_collection = cur.fetchone()["total_collection"]

    conn.close()

    return render_template(
        "dashboard.html",
        total_students=total_students,
        total_courses=total_courses,
        total_invoices=total_invoices,
        total_collection=total_collection
    )


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))

@app.route("/students")
@login_required
def students():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT * FROM students
        ORDER BY id DESC
    """)

    students = cur.fetchall()
    conn.close()

    return render_template("students.html", students=students)

from datetime import datetime


@app.route("/student/new", methods=["GET", "POST"])
@login_required
def student_new():
    if request.method == "POST":

        full_name = request.form["full_name"]
        phone = request.form["phone"]
        email = request.form["email"]
        address = request.form["address"]

        conn = get_conn()
        cur = conn.cursor()

        now = datetime.now().isoformat(timespec="seconds")

        cur.execute("""
            INSERT INTO students (
                student_code,
                full_name,
                phone,
                email,
                address,
                joined_date,
                status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "TEMP",
            full_name,
            phone,
            email,
            address,
            now,
            "active",
            now,
            now
        ))

        student_id = cur.lastrowid

        student_code = f"GIT-{str(student_id).zfill(4)}"

        cur.execute("""
            UPDATE students
            SET student_code = ?
            WHERE id = ?
        """, (student_code, student_id))

        conn.commit()
        conn.close()

        flash("Student added successfully.", "success")

        return redirect(url_for("students"))

    return render_template("student_form.html")

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

        from datetime import datetime
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

        conn.commit()
        conn.close()

        flash("Course added successfully.", "success")

        return redirect(url_for("courses"))

    return render_template("course_form.html")

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
        IFNULL(SUM(payments.amount_paid), 0) AS paid_amount
    FROM invoices
    JOIN students
        ON invoices.student_id = students.id
    LEFT JOIN payments
        ON payments.invoice_id = invoices.id
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

@app.route("/invoice/new", methods=["GET", "POST"])
@login_required
def invoice_new():
    conn = get_conn()
    cur = conn.cursor()

    # Load students and courses for dropdowns
    cur.execute("SELECT * FROM students ORDER BY full_name ASC")
    students = cur.fetchall()

    cur.execute("SELECT * FROM courses WHERE is_active = 1 ORDER BY course_name ASC")
    courses = cur.fetchall()

    if request.method == "POST":
        try:
            student_id = request.form["student_id"]
            invoice_date = request.form["invoice_date"]
            installment_type = request.form["installment_type"]
            notes = request.form.get("notes", "").strip()

            # Multiple item rows
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

            now = datetime.now().isoformat(timespec="seconds")

            invoice_items_to_save = []
            subtotal = 0.0
            discount_amount = 0.0
            total_amount = 0.0

            # Build invoice rows
            for i in range(len(item_descriptions)):
                description = (item_descriptions[i] or "").strip()
                course_id_raw = (item_course_ids[i] or "").strip()
                qty_raw = (item_qtys[i] or "0").strip()
                rate_raw = (item_rates[i] or "0").strip()
                discount_raw = (item_discounts[i] or "0").strip()

                qty = float(qty_raw or 0)
                rate = float(rate_raw or 0)
                row_discount = float(discount_raw or 0)

                # Skip fully blank rows
                if not description and qty == 0 and rate == 0:
                    continue

                if not description:
                    flash(f"Description is required in item row {i + 1}.", "danger")
                    conn.close()
                    return redirect(url_for("invoice_new"))

                if qty <= 0:
                    flash(f"Quantity must be greater than 0 in item row {i + 1}.", "danger")
                    conn.close()
                    return redirect(url_for("invoice_new"))

                if rate < 0:
                    flash(f"Rate cannot be negative in item row {i + 1}.", "danger")
                    conn.close()
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
                flash("Please enter at least one valid bill item.", "danger")
                conn.close()
                return redirect(url_for("invoice_new"))

            # Create invoice first
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
                    notes,
                    status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "TEMP",
                student_id,
                invoice_date,
                subtotal,
                "none",          # invoice-level discount not used now
                0,               # kept for compatibility with existing table
                discount_amount, # total row discount
                total_amount,
                installment_type,
                notes,
                "unpaid",
                session["user_id"],
                now,
                now
            ))

            invoice_id = cur.lastrowid
            invoice_no = f"INV-{str(invoice_id).zfill(4)}"

            cur.execute("""
                UPDATE invoices
                SET invoice_no = ?
                WHERE id = ?
            """, (invoice_no, invoice_id))

            # Save invoice items
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

            # Save installment plan
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
        SELECT invoices.*, students.student_code, students.full_name, students.phone, students.email, students.address
        FROM invoices
        JOIN students ON invoices.student_id = students.id
        WHERE invoices.id = ?
    """, (invoice_id,))
    invoice = cur.fetchone()

    cur.execute("""
        SELECT invoice_items.*, courses.course_name
        FROM invoice_items
        LEFT JOIN courses ON invoice_items.course_id = courses.id
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
        SELECT payments.*, users.full_name AS collected_by_name
        FROM payments
        LEFT JOIN users ON payments.collected_by = users.id
        WHERE payments.invoice_id = ?
        ORDER BY payments.id DESC
    """, (invoice_id,))
    payments = cur.fetchall()

    cur.execute("""
        SELECT IFNULL(SUM(amount_paid), 0) AS total_paid
        FROM payments
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

@app.route("/student/<int:student_id>")
@login_required
def student_profile(student_id):
    conn = get_conn()
    cur = conn.cursor()

    # Student details
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

    # Student invoice list with paid amount
    cur.execute("""
        SELECT
            invoices.id,
            invoices.invoice_no,
            invoices.invoice_date,
            invoices.total_amount,
            invoices.status,
            IFNULL(SUM(payments.amount_paid), 0) AS paid_amount
        FROM invoices
        LEFT JOIN payments
            ON payments.invoice_id = invoices.id
        WHERE invoices.student_id = ?
        GROUP BY invoices.id
        ORDER BY invoices.id DESC
    """, (student_id,))
    invoices = cur.fetchall()

    # Summary
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
            IFNULL(SUM(payments.amount_paid), 0) AS total_paid
        FROM payments
        JOIN invoices
            ON payments.invoice_id = invoices.id
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

@app.route("/invoice/<int:invoice_id>/payment/new", methods=["GET", "POST"])
@login_required
def payment_new(invoice_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT invoices.*, students.student_code, students.full_name
        FROM invoices
        JOIN students ON invoices.student_id = students.id
        WHERE invoices.id = ?
    """, (invoice_id,))
    invoice = cur.fetchone()

    if not invoice:
        conn.close()
        flash("Invoice not found.", "danger")
        return redirect(url_for("invoices"))

    summary = get_invoice_payment_summary(invoice_id)

    if request.method == "POST":
        try:
            payment_date = request.form["payment_date"].strip()
            amount_paid = float(request.form["amount_paid"])
            payment_mode = request.form["payment_mode"].strip()
            reference_no = request.form.get("reference_no", "").strip()
            notes = request.form.get("notes", "").strip()

            if amount_paid <= 0:
                conn.close()
                flash("Payment amount must be greater than 0.", "danger")
                return redirect(url_for("payment_new", invoice_id=invoice_id))

            if amount_paid > summary["balance"]:
                conn.close()
                flash("Payment amount cannot be greater than balance amount.", "danger")
                return redirect(url_for("payment_new", invoice_id=invoice_id))

            now = datetime.now().isoformat(timespec="seconds")

            cur.execute("""
                INSERT INTO payments (
                    invoice_id,
                    payment_date,
                    amount_paid,
                    payment_mode,
                    reference_no,
                    notes,
                    collected_by,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                invoice_id,
                payment_date,
                amount_paid,
                payment_mode,
                reference_no,
                notes,
                session["user_id"],
                now
            ))

            payment_id = cur.lastrowid
            receipt_no = f"REC-{str(payment_id).zfill(4)}"

            cur.execute("""
                INSERT INTO receipts (
                    receipt_no,
                    payment_id,
                    receipt_date,
                    amount_received,
                    created_by,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                receipt_no,
                payment_id,
                payment_date,
                amount_paid,
                session["user_id"],
                now
            ))

            allocate_payment_to_installments(conn, invoice_id, amount_paid)
            update_invoice_status(conn, invoice_id)

            conn.commit()
            conn.close()

            flash("Payment recorded successfully.", "success")
            return redirect(url_for("invoice_view", invoice_id=invoice_id))

        except ValueError:
            conn.rollback()
            conn.close()
            flash("Please enter valid payment amount.", "danger")
            return redirect(url_for("payment_new", invoice_id=invoice_id))

        except Exception as e:
            conn.rollback()
            conn.close()
            flash(f"Error while recording payment: {str(e)}", "danger")
            return redirect(url_for("payment_new", invoice_id=invoice_id))

    conn.close()
    today = datetime.today().strftime("%Y-%m-%d")
    return render_template(
        "payment_form.html",
        invoice=invoice,
        summary=summary,
        today=today
    )

@app.route("/receipt/<int:payment_id>")
@login_required
def receipt_view(payment_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            receipts.receipt_no,
            receipts.receipt_date,
            receipts.amount_received,
            receipts.created_at AS receipt_created_at,

            payments.id AS payment_id,
            payments.payment_date,
            payments.amount_paid,
            payments.payment_mode,
            payments.reference_no,
            payments.notes,

            invoices.id AS invoice_id,
            invoices.invoice_no,
            invoices.invoice_date,
            invoices.total_amount,

            students.student_code,
            students.full_name,
            students.phone,
            students.email,

            users.full_name AS collected_by_name

        FROM receipts
        JOIN payments
            ON receipts.payment_id = payments.id
        JOIN invoices
            ON payments.invoice_id = invoices.id
        JOIN students
            ON invoices.student_id = students.id
        LEFT JOIN users
            ON payments.collected_by = users.id
        WHERE payments.id = ?
    """, (payment_id,))

    receipt = cur.fetchone()

    if not receipt:
        conn.close()
        flash("Receipt not found.", "danger")
        return redirect(url_for("invoices"))

    # Total paid till now for this invoice
    cur.execute("""
        SELECT IFNULL(SUM(amount_paid), 0) AS total_paid
        FROM payments
        WHERE invoice_id = ?
    """, (receipt["invoice_id"],))
    total_paid_row = cur.fetchone()

    total_invoice_amount = float(receipt["total_amount"] or 0)
    amount_received = float(receipt["amount_received"] or 0)
    total_paid = float(total_paid_row["total_paid"] or 0)
    balance_amount = total_invoice_amount - total_paid

    conn.close()

    amount_in_words = number_to_words_indian(amount_received)

    return render_template(
        "receipt_view.html",
        receipt=receipt,
        total_invoice_amount=total_invoice_amount,
        amount_received=amount_received,
        total_paid=total_paid,
        balance_amount=balance_amount,
        amount_in_words=amount_in_words
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

    cur.execute("""
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

            students.id AS student_id,
            students.student_code,
            students.full_name,
            students.phone,
            students.email

        FROM installment_plans
        JOIN invoices
            ON installment_plans.invoice_id = invoices.id
        JOIN students
            ON invoices.student_id = students.id
        WHERE installment_plans.due_date < ?
          AND installment_plans.status IN ('pending', 'partially_paid')
        ORDER BY installment_plans.due_date ASC, students.full_name ASC
    """, (today,))

    rows = cur.fetchall()

    total_overdue_count = len(rows)
    total_overdue_amount = 0.0

    report_rows = []

    for row in rows:
        amount_due = float(row["amount_due"] or 0)
        amount_paid = float(row["amount_paid"] or 0)
        pending_amount = amount_due - amount_paid

        if pending_amount < 0:
            pending_amount = 0

        total_overdue_amount += pending_amount

        report_rows.append({
            "id": row["id"],
            "installment_no": row["installment_no"],
            "due_date": row["due_date"],
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
            "email": row["email"]
        })

    conn.close()

    return render_template(
        "report_overdue_installments.html",
        rows=report_rows,
        total_overdue_count=total_overdue_count,
        total_overdue_amount=total_overdue_amount,
        today=today
    )

@app.route("/reports/today-collection")
@login_required
def today_collection_report():
    conn = get_conn()
    cur = conn.cursor()

    today = datetime.today().strftime("%Y-%m-%d")

    cur.execute("""
        SELECT
            payments.id AS payment_id,
            payments.payment_date,
            payments.amount_paid,
            payments.payment_mode,
            payments.reference_no,
            payments.notes,

            invoices.id AS invoice_id,
            invoices.invoice_no,

            students.id AS student_id,
            students.student_code,
            students.full_name,
            students.phone,

            receipts.receipt_no,

            users.full_name AS collected_by_name

        FROM payments
        JOIN invoices
            ON payments.invoice_id = invoices.id
        JOIN students
            ON invoices.student_id = students.id
        LEFT JOIN receipts
            ON receipts.payment_id = payments.id
        LEFT JOIN users
            ON payments.collected_by = users.id

        WHERE payments.payment_date = ?
        ORDER BY payments.id DESC
    """, (today,))

    rows = cur.fetchall()

    total_collection = 0.0
    total_payments = len(rows)

    cash_total = 0.0
    upi_total = 0.0
    bank_total = 0.0
    card_total = 0.0

    for row in rows:
        amount = float(row["amount_paid"] or 0)
        total_collection += amount

        mode = (row["payment_mode"] or "").lower()

        if mode == "cash":
            cash_total += amount
        elif mode == "upi":
            upi_total += amount
        elif mode == "bank_transfer":
            bank_total += amount
        elif mode == "card":
            card_total += amount

    conn.close()

    return render_template(
        "report_today_collection.html",
        rows=rows,
        today=today,
        total_collection=total_collection,
        total_payments=total_payments,
        cash_total=cash_total,
        upi_total=upi_total,
        bank_total=bank_total,
        card_total=card_total
    )

@app.route("/reports/student-outstanding")
@login_required
def student_outstanding_report():
    conn = get_conn()
    cur = conn.cursor()

    # Student master rows
    cur.execute("""
        SELECT
            id,
            student_code,
            full_name,
            phone,
            email,
            status
        FROM students
        ORDER BY full_name ASC
    """)
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
                IFNULL(SUM(payments.amount_paid), 0) AS total_paid
            FROM payments
            JOIN invoices
                ON payments.invoice_id = invoices.id
            WHERE invoices.student_id = ?
        """, (student_id,))
        payment_summary = cur.fetchone()

        total_invoices = int(invoice_summary["total_invoices"] or 0)
        total_billed = float(invoice_summary["total_billed"] or 0)
        total_paid = float(payment_summary["total_paid"] or 0)
        balance = total_billed - total_paid

        rows.append({
            "student_id": student["id"],
            "student_code": student["student_code"],
            "full_name": student["full_name"],
            "phone": student["phone"],
            "email": student["email"],
            "status": student["status"],
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
        total_balance=grand_total_balance
    )

@app.route("/reports/unpaid-invoices")
@login_required
def unpaid_invoices_report():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            invoices.id,
            invoices.invoice_no,
            invoices.invoice_date,
            invoices.total_amount,
            invoices.status,

            students.id AS student_id,
            students.student_code,
            students.full_name,
            students.phone,

            IFNULL(SUM(payments.amount_paid), 0) AS paid_amount

        FROM invoices
        JOIN students
            ON invoices.student_id = students.id
        LEFT JOIN payments
            ON payments.invoice_id = invoices.id

        WHERE invoices.status IN ('unpaid', 'partially_paid')

        GROUP BY invoices.id
        ORDER BY invoices.invoice_date ASC, invoices.id ASC
    """)

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
            "phone": row["phone"]
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
        total_balance=total_balance
    )

@app.route("/reports/date-wise-collection", methods=["GET"])
@login_required
def date_wise_collection_report():
    conn = get_conn()
    cur = conn.cursor()

    from_date = request.args.get("from_date", "").strip()
    to_date = request.args.get("to_date", "").strip()

    today = datetime.today().strftime("%Y-%m-%d")

    # Default: today to today
    if not from_date:
        from_date = today
    if not to_date:
        to_date = today

    cur.execute("""
        SELECT
            payments.id AS payment_id,
            payments.payment_date,
            payments.amount_paid,
            payments.payment_mode,
            payments.reference_no,
            payments.notes,

            invoices.id AS invoice_id,
            invoices.invoice_no,

            students.id AS student_id,
            students.student_code,
            students.full_name,
            students.phone,

            receipts.receipt_no,

            users.full_name AS collected_by_name

        FROM payments
        JOIN invoices
            ON payments.invoice_id = invoices.id
        JOIN students
            ON invoices.student_id = students.id
        LEFT JOIN receipts
            ON receipts.payment_id = payments.id
        LEFT JOIN users
            ON payments.collected_by = users.id

        WHERE payments.payment_date BETWEEN ? AND ?
        ORDER BY payments.payment_date DESC, payments.id DESC
    """, (from_date, to_date))

    rows = cur.fetchall()
    conn.close()

    total_collection = 0.0
    total_payments = len(rows)

    cash_total = 0.0
    upi_total = 0.0
    bank_total = 0.0
    card_total = 0.0

    for row in rows:
        amount = float(row["amount_paid"] or 0)
        total_collection += amount

        mode = (row["payment_mode"] or "").lower()

        if mode == "cash":
            cash_total += amount
        elif mode == "upi":
            upi_total += amount
        elif mode == "bank_transfer":
            bank_total += amount
        elif mode == "card":
            card_total += amount

    return render_template(
        "report_date_wise_collection.html",
        rows=rows,
        from_date=from_date,
        to_date=to_date,
        total_collection=total_collection,
        total_payments=total_payments,
        cash_total=cash_total,
        upi_total=upi_total,
        bank_total=bank_total,
        card_total=card_total
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

        # Total billed for this course
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

        # Proportional payment allocation:
        # For each invoice containing this course,
        # course share = course line total / invoice total
        # allocated paid = invoice total paid * share
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
                SELECT IFNULL(SUM(amount_paid), 0) AS invoice_paid
                FROM payments
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

if __name__ == "__main__":
    app.run(debug=True)
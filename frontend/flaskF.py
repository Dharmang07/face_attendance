from flask import Flask, render_template, redirect, request, session
import mysql.connector
import subprocess
import sys
import os
from uuid import uuid4
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "attendance_secret"

# ---------- UPLOAD CONFIG ----------
UPLOAD_FOLDER = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "faces")
)
CONVERTED_FOLDER = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "faces_rgb")
)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
DB_NAME = "ai_face_attendance"


# ---------- DATABASE ----------
def get_db():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="",
        database=DB_NAME
    )


def ensure_employees_emp_id_schema():
    try:
        conn = get_db()
    except mysql.connector.Error as e:
        print(f"Schema sync skipped for employees.emp_id: {e}")
        return

    cur = conn.cursor()

    try:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA=%s AND TABLE_NAME='employees'
            """,
            (DB_NAME,)
        )
        table_exists = cur.fetchone()[0] > 0
        if not table_exists:
            return

        cur.execute(
            """
            SELECT IS_NULLABLE, EXTRA
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA=%s AND TABLE_NAME='employees' AND COLUMN_NAME='emp_id'
            """,
            (DB_NAME,)
        )
        emp_id_column = cur.fetchone()
        if not emp_id_column:
            return

        is_nullable, extra = emp_id_column
        if is_nullable == "YES" or "auto_increment" not in (extra or "").lower():
            cur.execute("ALTER TABLE employees MODIFY emp_id INT NOT NULL AUTO_INCREMENT")

        cur.execute(
            """
            SELECT kcu.COLUMN_NAME
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
              ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
             AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
             AND tc.TABLE_NAME = kcu.TABLE_NAME
            WHERE tc.TABLE_SCHEMA=%s
              AND tc.TABLE_NAME='employees'
              AND tc.CONSTRAINT_TYPE='PRIMARY KEY'
            ORDER BY kcu.ORDINAL_POSITION
            """,
            (DB_NAME,)
        )
        pk_columns = [row[0] for row in cur.fetchall()]

        if pk_columns != ["emp_id"]:
            if pk_columns:
                cur.execute("ALTER TABLE employees DROP PRIMARY KEY")
            cur.execute("ALTER TABLE employees ADD PRIMARY KEY (emp_id)")

        conn.commit()
    except mysql.connector.Error as e:
        conn.rollback()
        print(f"Schema sync failed for employees.emp_id: {e}")
    finally:
        cur.close()
        conn.close()


ensure_employees_emp_id_schema()


# ---------- LOGIN ----------
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form["username"] == "admin" and request.form["password"] == "admin":
            session["user"] = "admin"
            return redirect("/dashboard")
    return render_template("login.html")


# ---------- DASHBOARD ----------
@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/")

    conn = get_db()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
    SELECT 
        e.emp_id,
        a.name,
        a.date,
        TIME_FORMAT(a.in_time, '%h:%i:%s %p') AS in_time,
        TIME_FORMAT(a.out_time, '%h:%i:%s %p') AS out_time
    FROM attendance a
    JOIN employees e ON a.name = e.emp_name
    ORDER BY a.date DESC, a.in_time DESC
""")

    records = cur.fetchall()
    cur.execute("SELECT emp_id, emp_name FROM employees ORDER BY emp_name ASC")
    employees = cur.fetchall()
    conn.close()

    return render_template("dashboard.html", records=records, employees=employees)


# ---------- START CAMERA ----------
@app.route("/start")
def start():
    backend_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "run.py")
    )
    backend_dir = os.path.dirname(backend_path)

    subprocess.Popen(
        [sys.executable, backend_path],
        cwd=backend_dir,
        creationflags=subprocess.CREATE_NEW_CONSOLE
    )

    return redirect("/dashboard")


# ---------- ADD EMPLOYEE ----------
@app.route("/add_employee", methods=["GET", "POST"])
def add_employee():
    if "user" not in session:
        return redirect("/")

    if request.method == "POST":
        emp_name = request.form["emp_name"].strip().upper()
        image = request.files["image"]

        if image and image.filename:
            original_name = secure_filename(image.filename)
            _, ext = os.path.splitext(original_name)
            ext = ext.lower() if ext else ".jpg"
            filename = secure_filename(f"{emp_name}_{uuid4().hex[:12]}{ext}")
            image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            image.save(image_path)

            conn = get_db()
            cur = conn.cursor()

            cur.execute(
                "INSERT INTO employees (emp_name, image_name) VALUES (%s, %s)",
                (emp_name, filename)
            )

            conn.commit()
            conn.close()

            return redirect("/dashboard")

    return render_template("add_employee.html")


# ---------- REMOVE EMPLOYEE ----------
def delete_employee_by_id(emp_id):
    conn = get_db()
    cur = conn.cursor(dictionary=True)

    cur.execute(
        "SELECT emp_name, image_name FROM employees WHERE emp_id=%s",
        (emp_id,)
    )
    employee = cur.fetchone()

    if employee:
        cur.execute("DELETE FROM attendance WHERE name=%s", (employee["emp_name"],))
        cur.execute("DELETE FROM employees WHERE emp_id=%s", (emp_id,))
        conn.commit()

    conn.close()

    if employee:
        original_image_path = os.path.join(UPLOAD_FOLDER, employee["image_name"])
        converted_image_name = os.path.splitext(employee["image_name"])[0] + ".jpg"
        converted_image_path = os.path.join(CONVERTED_FOLDER, converted_image_name)

        for image_path in [original_image_path, converted_image_path]:
            if os.path.exists(image_path):
                os.remove(image_path)


@app.route("/remove_employee/<emp_id>", methods=["POST"])
def remove_employee(emp_id):
    if "user" not in session:
        return redirect("/")

    delete_employee_by_id(emp_id)
    return redirect("/dashboard")


@app.route("/remove_employee_db", methods=["POST"])
def remove_employee_db():
    if "user" not in session:
        return redirect("/")

    emp_id = request.form.get("emp_id", "").strip()
    if emp_id:
        delete_employee_by_id(emp_id)

    return redirect("/dashboard")


# ---------- LOGOUT ----------
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


# ---------- RUN APP ----------
if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)

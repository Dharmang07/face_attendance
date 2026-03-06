import mysql.connector
from datetime import datetime

def markAttendance(name):
    print("markAttendance() CALLED for:", name)

    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        password="",
        database="ai_face_attendance"
    )
    cursor = conn.cursor()

    today = datetime.now().strftime('%Y-%m-%d')
    time_now = datetime.now().strftime('%H:%M:%S')

    cursor.execute(
        "SELECT id FROM attendance WHERE name=%s AND date=%s",
        (name, today)
    )

    if cursor.fetchone() is None:
        cursor.execute(
            "INSERT INTO attendance (name, date, time) VALUES (%s, %s, %s)",
            (name, today, time_now)
        )
        conn.commit()
        print("✅ INSERTED INTO DB")
    else:
        print("⚠️ Already marked today")

    conn.close()


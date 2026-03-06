import cv2
import face_recognition
import os
import numpy as np
from datetime import datetime
from PIL import Image
import mysql.connector
import sys
import pickle

last_seen = {}
MIN_GAP_MINUTES = 0.2  # exit cooldown (change to 1 for testing)
AUTO_EXIT_TIME = "00:00:00"  # 12:00 AM auto-exit for missed checkouts
last_auto_close_date = None

# ================= CONFIG =================
PATH = 'faces'
CONVERTED_PATH = 'faces_rgb'
ENCODINGS_CACHE = 'encodings_cache.pkl'
MAX_IMAGE_DIMENSION = 1000
SUPPORTED_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.heic', '.webp')
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "",
    "database": "ai_face_attendance",
}

# Remove only the final extension so names like "john.v2.jpg" map to "john.v2"
IMAGE_STEM_SQL = """
CASE
    WHEN INSTR(image_name, '.') > 0 THEN
        LEFT(
            image_name,
            CHAR_LENGTH(image_name) - CHAR_LENGTH(SUBSTRING_INDEX(image_name, '.', -1)) - 1
        )
    ELSE image_name
END
"""

os.makedirs(PATH, exist_ok=True)
os.makedirs(CONVERTED_PATH, exist_ok=True)


# ================= IMAGE CONVERTER =================
def convert_image(src, dst):
    try:
        img = Image.open(src)
        img = img.convert("RGB")
        img.save(dst, "JPEG")
        return True
    except Exception as e:
        print(f"Conversion failed for {src}: {e}")
        return False


# ================= LOAD IMAGES =================
images = []
classNames = []
loaded_files = []

print("Loading and converting face images...")

for file in os.listdir(PATH):
    if file.lower().endswith(SUPPORTED_EXTENSIONS):
        name = os.path.splitext(file)[0]  # filename without extension
        src = os.path.join(PATH, file)
        dst = os.path.join(CONVERTED_PATH, name + ".jpg")

        if not convert_image(src, dst):
            continue

        img = cv2.imread(dst)
        if img is None:
            continue

        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = np.ascontiguousarray(img)

        images.append(img)
        classNames.append(name)
        loaded_files.append(file)
        print(f"Loaded: {file}")


# ================= ENCODE FACES =================
def _resize_for_encoding(img):
    h, w = img.shape[:2]
    max_dim = max(h, w)
    if max_dim <= MAX_IMAGE_DIMENSION:
        return img

    scale = MAX_IMAGE_DIMENSION / max_dim
    new_w = int(w * scale)
    new_h = int(h * scale)
    return cv2.resize(img, (new_w, new_h))


def _source_signature():
    signature = []
    for file in loaded_files:
        src = os.path.join(PATH, file)
        try:
            stat = os.stat(src)
            signature.append((file, stat.st_mtime_ns, stat.st_size))
        except OSError:
            return None

    signature.sort()
    return signature


def load_cached_encodings():
    if not os.path.exists(ENCODINGS_CACHE):
        return None, None

    try:
        with open(ENCODINGS_CACHE, "rb") as f:
            data = pickle.load(f)
    except Exception as e:
        print(f"Could not read cache file: {e}")
        return None, None

    if data.get("signature") != _source_signature():
        return None, None

    encodings = data.get("encodings", [])
    names = data.get("names", [])
    if not encodings or len(encodings) != len(names):
        return None, None

    print(f"Loaded {len(encodings)} cached encodings")
    return encodings, names


def save_cached_encodings(encodings, names):
    payload = {
        "signature": _source_signature(),
        "encodings": encodings,
        "names": names,
    }

    try:
        with open(ENCODINGS_CACHE, "wb") as f:
            pickle.dump(payload, f)
    except Exception as e:
        print(f"Could not write cache file: {e}")


def findEncodings(images):
    encodeList = []
    encodedNames = []

    for img, name in zip(images, classNames):
        try:
            img_small = _resize_for_encoding(img)
            faces = face_recognition.face_locations(
                img_small,
                number_of_times_to_upsample=0,
                model="hog"
            )
            if not faces:
                print(f"No face detected in {name}")
                continue

            enc = face_recognition.face_encodings(img_small, faces)
            encodeList.append(enc[0])
            encodedNames.append(name)
            print(f"Encoded: {name}")

        except Exception as e:
            print(f"Encoding error for {name}: {e}")

    return encodeList, encodedNames


# ================= GET EMPLOYEE NAME FROM DB =================
def get_db_connection():
    try:
        return mysql.connector.connect(**DB_CONFIG)
    except mysql.connector.Error as e:
        print(f"MySQL connection failed: {e}")
        return None


def get_employee_name(image_filename):
    conn = get_db_connection()
    if conn is None:
        return image_filename.upper()

    cursor = conn.cursor()

    cursor.execute(
        f"""
        SELECT emp_id, emp_name
        FROM employees
        WHERE LOWER({IMAGE_STEM_SQL}) = LOWER(%s)
        """,
        (image_filename,)
    )
    result = cursor.fetchone()
    conn.close()

    if result:
        name = result[1]
        if isinstance(name, bytes):
            name = name.decode("utf-8", errors="ignore")
        return str(name)

    return image_filename.upper()  # fallback if not found


# ================= MYSQL ENTRY / EXIT =================
def auto_close_missed_exits(cursor, today):
    global last_auto_close_date

    if last_auto_close_date == today:
        return 0

    cursor.execute(
        """
        UPDATE attendance
        SET out_time=%s
        WHERE date < %s AND out_time IS NULL
        """,
        (AUTO_EXIT_TIME, today)
    )
    affected_rows = cursor.rowcount
    last_auto_close_date = today
    return affected_rows


def markAttendance(image_filename):
    global last_seen

    now = datetime.now()
    today = now.strftime('%Y-%m-%d')

    conn = get_db_connection()
    if conn is None:
        return None

    cursor = conn.cursor()
    auto_closed = auto_close_missed_exits(cursor, today)
    if auto_closed > 0:
        conn.commit()
        print(f"Auto-exit applied to {auto_closed} record(s) at 12:00 AM")

    # Get exact emp_name from employees table
    cursor.execute(
        f"""
        SELECT emp_id, emp_name
        FROM employees
        WHERE LOWER({IMAGE_STEM_SQL}) = LOWER(%s)
        """,
        (image_filename,)
    )
    result = cursor.fetchone()

    if not result:
        print("Employee not found in employees table")
        conn.close()
        return None

    emp_id = result[0]
    emp_name = result[1]  # exact name from DB

    # Get latest record for today (allows multiple sessions per day)
    cursor.execute(
        """
        SELECT id, in_time, out_time
        FROM attendance
        WHERE name=%s AND date=%s
        ORDER BY in_time DESC
        LIMIT 1
        """,
        (emp_name, today)
    )
    record = cursor.fetchone()

    # ---------- ENTRY ----------
    if record is None:
        new_id = emp_id
        cursor.execute(
            "INSERT INTO attendance (id, name, date, in_time) VALUES (%s, %s, %s, %s)",
            (new_id, emp_name, today, now.strftime('%H:%M:%S'))
        )
        conn.commit()
        last_seen[emp_name] = now
        print(f"ENTRY recorded for {emp_name}")

    # ---------- EXIT ----------
    elif record[1] is not None and record[2] is None:
        if emp_name not in last_seen:
            last_seen[emp_name] = now
            conn.close()
            return emp_name

        diff_minutes = (now - last_seen[emp_name]).total_seconds() / 60

        if diff_minutes >= MIN_GAP_MINUTES:
            cursor.execute(
                """
                UPDATE attendance
                SET out_time=%s
                WHERE name=%s AND date=%s AND in_time=%s AND out_time IS NULL
                """,
                (now.strftime('%H:%M:%S'), emp_name, today, record[1])
            )
            conn.commit()
            print(f"EXIT recorded for {emp_name}")
            last_seen.pop(emp_name, None)

    # ---------- RE-ENTRY (new row after exit) ----------
    elif record[2] is not None:
        out_val = record[2]
        if isinstance(out_val, datetime):
            out_time = out_val.time()
        elif hasattr(out_val, "seconds"):  # timedelta from MySQL TIME
            out_time = (datetime.min + out_val).time()
        else:
            out_time = out_val  # already a time object

        last_out_time = datetime.combine(now.date(), out_time)
        diff_minutes = (now - last_out_time).total_seconds() / 60

        if diff_minutes >= MIN_GAP_MINUTES:
            new_id = emp_id
            cursor.execute(
                "INSERT INTO attendance (id, name, date, in_time) VALUES (%s, %s, %s, %s)",
                (new_id, emp_name, today, now.strftime('%H:%M:%S'))
            )
            conn.commit()
            last_seen[emp_name] = now
            print(f"ENTRY recorded for {emp_name}")

    conn.close()
    return emp_name


# ================= MAIN =================
encodeListKnown, knownClassNames = load_cached_encodings()
if encodeListKnown is None:
    encodeListKnown, knownClassNames = findEncodings(images)
    if encodeListKnown:
        save_cached_encodings(encodeListKnown, knownClassNames)

if not encodeListKnown:
    print("No valid face encodings found")
    sys.exit()

print(f"{len(encodeListKnown)} faces encoded. Starting camera...")

cap = cv2.VideoCapture(0)
if not cap.isOpened():
    print("Could not open camera")
    sys.exit(1)

while True:
    # Allow fast exit without waiting for full frame processing.
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

    success, img = cap.read()
    if not success:
        break

    imgS = cv2.resize(img, (0, 0), fx=0.25, fy=0.25)
    imgS = cv2.cvtColor(imgS, cv2.COLOR_BGR2RGB)

    facesCurFrame = face_recognition.face_locations(imgS, model="hog")
    encodesCurFrame = face_recognition.face_encodings(imgS, facesCurFrame)

    for encodeFace, faceLoc in zip(encodesCurFrame, facesCurFrame):
        matches = face_recognition.compare_faces(
            encodeListKnown, encodeFace, tolerance=0.6
        )
        faceDis = face_recognition.face_distance(
            encodeListKnown, encodeFace
        )

        matchIndex = np.argmin(faceDis)

        if matches[matchIndex]:
            image_filename = knownClassNames[matchIndex]
            emp_name = get_employee_name(image_filename)

            y1, x2, y2, x1 = [v * 4 for v in faceLoc]

            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.rectangle(img, (x1, y2 - 35), (x2, y2),
                          (0, 255, 0), cv2.FILLED)

            cv2.putText(img, emp_name, (x1 + 6, y2 - 6),
                        cv2.FONT_HERSHEY_COMPLEX, 1,
                        (255, 255, 255), 2)

            markAttendance(image_filename)

    cv2.imshow("AI Face Attendance | Press Q to Exit", img)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
# Flush pending UI events before destroying windows (helps Windows exit faster).
for _ in range(3):
    cv2.waitKey(1)
cv2.destroyAllWindows()
cv2.waitKey(1)

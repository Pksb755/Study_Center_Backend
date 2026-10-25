from flask import Flask, request, jsonify, session
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import mysql.connector
from mysql.connector import Error
from functools import wraps
from datetime import datetime, date
from decimal import Decimal
import os
import uuid
from flask_bcrypt import Bcrypt
from PIL import Image, ImageDraw, ImageFont
import qrcode
import os
from flask import send_from_directory
import io
import base64
from qrcode.constants import ERROR_CORRECT_M
from supabase import create_client, Client
from dotenv import load_dotenv


# ============================================================
# APP CONFIG
# ============================================================

load_dotenv()

supabase: Client = create_client(
    os.environ.get("SUPABASE_URL"),
    os.environ.get("SUPABASE_KEY")
)
app = Flask(__name__)
bcrypt = Bcrypt(app)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "CHANGE_THIS_TO_A_LONG_RANDOM_SECRET_KEY"
)

CORS(
    app,
    supports_credentials=True,
    origins="*"
)

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


# ============================================================
# MYSQL CONFIG
# ============================================================

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "study_center_new_db"),
}


@app.route('/receipts/<filename>')
def serve_receipt(filename):
    # Ensure the folder exists
    os.makedirs('static/receipts', exist_ok=True)
    return send_from_directory('static/receipts', filename)

# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_db():
    try:
        connection = mysql.connector.connect(**DB_CONFIG)

        if connection.is_connected():
            return connection

    except Error as e:
        print("Database connection error:", e)

    return None


# ============================================================
# VALIDATION HELPERS
# ============================================================

import re

MOBILE_REGEX = re.compile(r"^[6-9]\d{9}$")
AADHAAR_REGEX = re.compile(r"^\d{12}$")
PINCODE_REGEX = re.compile(r"^\d{6}$")
EMAIL_REGEX = re.compile(
    r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
)


def validate_mobile(mobile):
    return bool(mobile and MOBILE_REGEX.match(str(mobile).strip()))


def validate_aadhaar(aadhaar):
    if not aadhaar:
        return True
    clean = str(aadhaar).replace("-", "").replace(" ", "")
    return bool(AADHAAR_REGEX.match(clean))


def validate_pincode(pincode):
    if not pincode:
        return True
    return bool(PINCODE_REGEX.match(str(pincode).strip()))


def validate_email(email):
    if not email:
        return True
    return bool(EMAIL_REGEX.match(str(email).strip()))


def clean_aadhaar(aadhaar):
    if not aadhaar:
        return None
    return str(aadhaar).replace("-", "").replace(" ", "").strip() or None


def mask_aadhaar(aadhaar):
    if not aadhaar:
        return None
    clean = str(aadhaar).replace("-", "").replace(" ", "")
    if len(clean) < 4:
        return "XXXX-XXXX-XXXX"
    return "XXXX-XXXX-" + clean[-4:]


def is_masked_aadhaar(value):
    if not value:
        return False
    return str(value).strip().startswith("XXXX")


# ============================================================
# HELPERS
# ============================================================

def json_safe(value):

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, bytes):
        return value.decode("utf-8")

    if isinstance(value, list):
        return [json_safe(x) for x in value]

    if isinstance(value, dict):
        return {
            k: json_safe(v)
            for k, v in value.items()
        }

    return value


def success(data=None, message="Success", total=None):

    response = {
        "status": {
            "code": 200,
            "message": message
        },
        "data": json_safe(data)
    }

    if total is not None:
        response["total"] = total

    return jsonify(response), 200


def error(message="Something went wrong", code=400):

    return jsonify({
        "status": {
            "code": code,
            "message": message
        },
        "data": None
    }), code


def get_json():

    data = request.get_json(silent=True)

    if not data:
        return {}

    return data


def execute_query(
    query,
    params=None,
    fetchone=False,
    fetchall=False,
    commit=False
):

    connection = get_db()

    if not connection:
        return None

    cursor = None

    try:

        cursor = connection.cursor(dictionary=True)

        cursor.execute(query, params or ())

        result = None

        if fetchone:
            result = cursor.fetchone()

        elif fetchall:
            result = cursor.fetchall()

        if commit:
            connection.commit()

        return result

    except Error as e:

        connection.rollback()

        print("SQL Error:", e)

        raise e

    finally:

        if cursor:
            cursor.close()

        connection.close()


def execute_insert(query, params=None):

    connection = get_db()

    if not connection:
        raise Exception("Database connection failed")

    cursor = None

    try:

        cursor = connection.cursor()

        cursor.execute(query, params or ())

        connection.commit()

        return cursor.lastrowid

    except Error as e:

        connection.rollback()

        print("SQL Insert Error:", e)

        raise e

    finally:

        if cursor:
            cursor.close()

        connection.close()


# def generate_number(prefix, table, column):

#     today = datetime.now().strftime("%Y%m%d")

#     query = f"""
#         SELECT COUNT(*) AS total
#         FROM {table}
#         WHERE {column} LIKE %s
#     """

#     result = execute_query(
#         query,
#         (f"{prefix}-{today}-%",),
#         fetchone=True
#     )

#     count = (result["total"] or 0) + 1

#     return f"{prefix}-{today}-{count:04d}"


def generate_number(prefix, table, column):
    """
    Generate sequential number using MAX(id)+1 fallback.
    Uses the actual max value in the column to avoid collisions.
    """
    today = datetime.now().strftime("%Y%m%d")
    like_pattern = f"{prefix}-%{today}-%"

    result = execute_query(
        f"""
        SELECT {column} AS code
        FROM {table}
        WHERE {column} LIKE %s
        ORDER BY id DESC
        LIMIT 1
        """,
        (like_pattern,),
        fetchone=True,
    )

    next_seq = 1

    if result and result.get("code"):
        try:
            last_seq = int(str(result["code"]).split("-")[-1])
            next_seq = last_seq + 1
        except (ValueError, IndexError):
            next_seq = 1

    return f"{prefix}-{today}-{next_seq:04d}"



def log_activity(
    user_id,
    action,
    module,
    reference_id=None,
    description=None
):

    try:

        execute_insert(
            """
            INSERT INTO activity_logs
            (
                user_id,
                action,
                module,
                reference_id,
                description,
                ip_address
            )
            VALUES (%s,%s,%s,%s,%s,%s)
            """,
            (
                user_id,
                action,
                module,
                reference_id,
                description,
                request.remote_addr
            )
        )

    except Exception as e:

        print("Activity log error:", e)


# ============================================================
# AUTH DECORATOR
# ============================================================

def login_required(function):

    @wraps(function)
    def wrapper(*args, **kwargs):

        if "user_id" not in session:
            return error(
                "Login required",
                401
            )

        return function(*args, **kwargs)

    return wrapper


def current_user_id():

    return session.get("user_id")


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/", methods=["GET"])
def home():

    return success(
        {
            "application": "Study Center API",
            "version": "1.0.0",
            "database": DB_CONFIG["database"]
        },
        "Study Center API is running"
    )


@app.route("/health", methods=["GET"])
def health():

    connection = get_db()

    if connection:

        connection.close()

        return success(
            {
                "api": "OK",
                "database": "OK"
            },
            "Healthy"
        )

    return error(
        "Database connection failed",
        500
    )


# ============================================================
# AUTH
# ============================================================

@app.route("/api/login", methods=["POST"])
def login():

    data = get_json()

    mobile = str(data.get("mobile", "")).strip()
    password = str(data.get("password", ""))

    if not mobile or not password:

        return error(
            "Mobile and password are required"
        )

    user = execute_query(
        """
        SELECT
            id,
            name,
            mobile,
            email,
            password_hash,
            role,
            status
        FROM users
        WHERE mobile = %s
        LIMIT 1
        """,
        (mobile,),
        fetchone=True
    )

    if not user:

        return error(
            "Invalid mobile or password",
            401
        )

    if user["status"] != "ACTIVE":

        return error(
            "User account is inactive",
            403
        )

    if not bcrypt.check_password_hash(user["password_hash"], password):
        return error(
            "Invalid mobile or password",
            401
        )

    session.clear()

    session["user_id"] = user["id"]
    session["user_role"] = user["role"]

    execute_query(
        """
        UPDATE users
        SET last_login_at = NOW()
        WHERE id = %s
        """,
        (user["id"],),
        commit=True
    )

    log_activity(
        user["id"],
        "LOGIN",
        "AUTH",
        user["id"],
        "User logged in"
    )

    return success(
        {
            "id": user["id"],
            "name": user["name"],
            "mobile": user["mobile"],
            "email": user["email"],
            "role": user["role"]
        },
        "Login successful"
    )


@app.route("/api/logout", methods=["POST"])
@login_required
def logout():

    user_id = current_user_id()

    log_activity(
        user_id,
        "LOGOUT",
        "AUTH",
        user_id,
        "User logged out"
    )

    session.clear()

    return success(
        None,
        "Logout successful"
    )


@app.route("/api/me", methods=["GET"])
@login_required
def me():

    user = execute_query(
        """
        SELECT
            id,
            name,
            mobile,
            email,
            role,
            status,
            last_login_at,
            created_at
        FROM users
        WHERE id = %s
        """,
        (current_user_id(),),
        fetchone=True
    )

    if not user:

        return error(
            "User not found",
            404
        )

    return success(
        user,
        "User profile"
    )


# ============================================================
# USERS
# ============================================================

@app.route("/api/users", methods=["GET"])
@login_required
def get_users():

    users = execute_query(
        """
        SELECT
            id,
            name,
            mobile,
            email,
            role,
            status,
            last_login_at,
            created_at
        FROM users
        ORDER BY id DESC
        """,
        fetchall=True
    )

    return success(
        users,
        "Users fetched",
        len(users)
    )


@app.route("/api/users", methods=["POST"])
# @login_required
def create_user():

    data = get_json()

    name = str(data.get("name", "")).strip()
    mobile = str(data.get("mobile", "")).strip()
    email = data.get("email")
    password = str(data.get("password", ""))
    role = str(data.get("role", "STAFF")).upper()

    if not name or not mobile or not password:

        return error(
            "Name, mobile and password are required"
        )

    if role not in ["ADMIN", "MANAGER", "STAFF"]:

        return error(
            "Invalid role"
        )

    existing = execute_query(
        """
        SELECT id
        FROM users
        WHERE mobile = %s
        LIMIT 1
        """,
        (mobile,),
        fetchone=True
    )

    if existing:

        return error(
            "Mobile number already exists"
        )

    if password is None or password == '':
        hashed_password = None
    else:
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
    user_id = execute_insert(
        """
        INSERT INTO users
        (
            name,
            mobile,
            email,
            password_hash,
            role,
            status
        )
        VALUES (%s,%s,%s,%s,%s,'ACTIVE')
        """,
        (
            name,
            mobile,
            email,
            hashed_password,
            role
        )
    )

    log_activity(
        current_user_id(),
        "CREATE",
        "USER",
        user_id,
        f"Created user {name}"
    )

    return success(
        {"id": user_id},
        "User created"
    )


# ============================================================
# STUDENTS
# ============================================================

@app.route("/api/students", methods=["GET"])
@login_required
def get_students():

    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip().upper()
    gender = request.args.get("gender", "").strip().upper()

    query = """
        SELECT
            id,
            student_code,
            full_name,
            mobile,
            alternate_mobile,
            aadhar_number,
            gender,
            date_of_birth,
            photo_url,
            father_name,
            mother_name,
            address,
            city,
            state,
            pincode,
            email,
            occupation,
            emergency_contact_name,
            emergency_contact_mobile,
            notes,
            status,
            created_at,
            updated_at
        FROM students
        WHERE 1=1
    """

    params = []

    if search:

        query += """
            AND (
                full_name LIKE %s
                OR mobile LIKE %s
                OR student_code LIKE %s
            )
        """

        search_value = f"%{search}%"

        params.extend([
            search_value,
            search_value,
            search_value
        ])

    if status:

        query += " AND status = %s"

        params.append(status)

    if gender:

        query += " AND gender = %s"

        params.append(gender)

    query += " ORDER BY id DESC"

    students = execute_query(
        query,
        tuple(params),
        fetchall=True
    )

    # Mask Aadhaar in response
    # for student in students:

    #     aadhaar = student.get("aadhar_number")

    #     if aadhaar and len(aadhaar) >= 4:

    #         student["aadhar_number"] = (
    #             "XXXX-XXXX-" + aadhaar[-4:]
    #         )

    
    for student in students:
        if student.get("aadhar_number"):
            student["aadhar_number"] = mask_aadhaar(student["aadhar_number"])

    return success(
        students,
        "Students fetched",
        len(students)
    )


# @app.route("/api/students/<int:student_id>", methods=["GET"])
# @login_required
# def get_student(student_id):

#     student = execute_query(
#         """
#         SELECT
#             id,
#             student_code,
#             full_name,
#             mobile,
#             alternate_mobile,
#             aadhar_number,
#             gender,
#             date_of_birth,
#             photo_url,
#             father_name,
#             mother_name,
#             address,
#             city,
#             state,
#             pincode,
#             email,
#             occupation,
#             emergency_contact_name,
#             emergency_contact_mobile,
#             notes,
#             status,
#             created_at,
#             updated_at
#         FROM students
#         WHERE id = %s
#         LIMIT 1
#         """,
#         (student_id,),
#         fetchone=True
#     )

#     if not student:
#         return error("Student not found", 404)

#     # Return full Aadhaar only on single-student fetch (for edit form)
#     # List endpoint still masks it.
#     return success(student, "Student fetched")


@app.route("/api/students/<int:student_id>", methods=["GET"])
@login_required
def get_student(student_id):
    student = execute_query(
        """
        SELECT
            s.id, s.student_code, s.full_name, s.mobile, s.alternate_mobile,
            s.aadhar_number, s.gender, s.date_of_birth, s.photo_url,
            s.father_name, s.mother_name, s.address, s.city, s.state, s.pincode,
            s.email, s.occupation, s.emergency_contact_name, s.emergency_contact_mobile,
            s.notes, s.status, s.created_at, s.updated_at,

            -- 👉 Backend Calculations
            (SELECT COUNT(*) FROM student_memberships 
             WHERE student_id = s.id) AS membership_count,

            (SELECT COALESCE(SUM(due_amount), 0) FROM student_memberships 
             WHERE student_id = s.id AND status = 'ACTIVE') AS total_due,

            (SELECT COALESCE(SUM(amount), 0) FROM payments 
             WHERE student_id = s.id AND status = 'SUCCESS') AS total_paid

        FROM students s
        WHERE s.id = %s
        LIMIT 1
        """,
        (student_id,),
        fetchone=True
    )

    if not student:
        return error("Student not found", 404)

    # Return full Aadhaar only on single-student fetch (for edit form)
    # List endpoint still masks it.
    return success(student, "Student fetched")


# @app.route("/api/students/<int:student_id>", methods=["GET"])
# @login_required
# def get_student(student_id):

#     student = execute_query(
#         """
#         SELECT *
#         FROM students
#         WHERE id = %s
#         LIMIT 1
#         """,
#         (student_id,),
#         fetchone=True
#     )

#     if not student:

#         return error(
#             "Student not found",
#             404
#         )

#     if student.get("aadhar_number"):

#         aadhaar = student["aadhar_number"]

#         if len(aadhaar) >= 4:

#             student["aadhar_number"] = (
#                 "XXXX-XXXX-" + aadhaar[-4:]
#             )

#     return success(
#         student,
#         "Student fetched"
#     )



# @app.route("/api/students", methods=["POST"])
# @login_required
# def create_student():

#     data = get_json()

#     full_name = str(
#         data.get("full_name", "")
#     ).strip()

#     mobile = str(
#         data.get("mobile", "")
#     ).strip()

#     gender = str(
#         data.get("gender", "")
#     ).upper()

#     if not full_name or not mobile or not gender:

#         return error(
#             "Full name, mobile and gender are required"
#         )

#     if gender not in [
#         "MALE",
#         "FEMALE",
#         "OTHER"
#     ]:

#         return error(
#             "Invalid gender"
#         )

#     # Generate student code
#     last_student = execute_query(
#         """
#         SELECT id
#         FROM students
#         ORDER BY id DESC
#         LIMIT 1
#         """,
#         fetchone=True
#     )

#     next_id = (
#         (last_student["id"] + 1)
#         if last_student
#         else 1
#     )

#     student_code = f"STU-{next_id:05d}"

#     student_id = execute_insert(
#         """
#         INSERT INTO students
#         (
#             student_code,
#             full_name,
#             mobile,
#             alternate_mobile,
#             aadhar_number,
#             gender,
#             date_of_birth,
#             photo_url,
#             father_name,
#             mother_name,
#             address,
#             city,
#             state,
#             pincode,
#             email,
#             occupation,
#             emergency_contact_name,
#             emergency_contact_mobile,
#             notes,
#             status,
#             created_by
#         )
#         VALUES
#         (
#             %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
#             %s,%s,%s,%s,%s,%s,%s,%s,%s,
#             'ACTIVE',%s
#         )
#         """,
#         (
#             student_code,
#             full_name,
#             mobile,
#             data.get("alternate_mobile"),
#             data.get("aadhar_number"),
#             gender,
#             data.get("date_of_birth"),
#             data.get("photo_url"),
#             data.get("father_name"),
#             data.get("mother_name"),
#             data.get("address"),
#             data.get("city"),
#             data.get("state"),
#             data.get("pincode"),
#             data.get("email"),
#             data.get("occupation"),
#             data.get("emergency_contact_name"),
#             data.get("emergency_contact_mobile"),
#             data.get("notes"),
#             current_user_id()
#         )
#     )

#     log_activity(
#         current_user_id(),
#         "CREATE",
#         "STUDENT",
#         student_id,
#         f"Created student {student_code}"
#     )

#     return success(
#         {
#             "id": student_id,
#             "student_code": student_code
#         },
#         "Student created"
#     )



@app.route("/api/students", methods=["POST"])
@login_required
def create_student():

    data = get_json()

    full_name = str(data.get("full_name", "")).strip()
    mobile = str(data.get("mobile", "")).strip()
    gender = str(data.get("gender", "")).upper()

    # ---- Required ----
    if not full_name:
        return error("Full name is required")

    if not mobile:
        return error("Mobile number is required")

    if not gender:
        return error("Gender is required")

    # ---- Validation ----
    if not validate_mobile(mobile):
        return error("Mobile must be a valid 10-digit Indian number")

    if gender not in ["MALE", "FEMALE", "OTHER"]:
        return error("Invalid gender")

    aadhaar_raw = data.get("aadhar_number")
    if not validate_aadhaar(aadhaar_raw):
        return error("Aadhaar must be 12 digits")

    pincode = data.get("pincode")
    if not validate_pincode(pincode):
        return error("Pincode must be 6 digits")

    email = data.get("email")
    if not validate_email(email):
        return error("Invalid email address")

    # ---- Duplicate mobile ----
    existing = execute_query(
        """
        SELECT id FROM students
        WHERE mobile = %s
        LIMIT 1
        """,
        (mobile,),
        fetchone=True
    )
    if existing:
        return error("A student with this mobile number already exists")

    # ---- Safe student_code generation ----
    last_student = execute_query(
        """
        SELECT student_code
        FROM students
        WHERE student_code LIKE 'STU-%'
        ORDER BY id DESC
        LIMIT 1
        """,
        fetchone=True
    )

    next_seq = 1
    if last_student and last_student.get("student_code"):
        try:
            next_seq = int(last_student["student_code"].split("-")[-1]) + 1
        except (ValueError, IndexError):
            next_seq = 1

    student_code = f"STU-{next_seq:05d}"

    aadhaar_clean = clean_aadhaar(aadhaar_raw)

    student_id = execute_insert(
        """
        INSERT INTO students
        (
            student_code, full_name, mobile, alternate_mobile,
            aadhar_number, gender, date_of_birth, photo_url,
            father_name, mother_name, address, city, state, pincode,
            email, occupation, emergency_contact_name,
            emergency_contact_mobile, notes, status, created_by
        )
        VALUES
        (
            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
            %s,%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVE',%s
        )
        """,
        (
            student_code,
            full_name,
            mobile,
            data.get("alternate_mobile") or None,
            aadhaar_clean,
            gender,
            data.get("date_of_birth") or None,
            data.get("photo_url") or None,
            data.get("father_name") or None,
            data.get("mother_name") or None,
            data.get("address") or None,
            data.get("city") or None,
            data.get("state") or None,
            pincode or None,
            email or None,
            data.get("occupation") or None,
            data.get("emergency_contact_name") or None,
            data.get("emergency_contact_mobile") or None,
            data.get("notes") or None,
            current_user_id()
        )
    )

    log_activity(
        current_user_id(),
        "CREATE",
        "STUDENT",
        student_id,
        f"Created student {student_code}"
    )

    return success(
        {"id": student_id, "student_code": student_code},
        "Student created"
    )



@app.route("/api/students/<int:student_id>", methods=["PUT"])
@login_required
def update_student(student_id):

    data = get_json()

    existing = execute_query(
        "SELECT id, mobile FROM students WHERE id = %s",
        (student_id,),
        fetchone=True
    )

    if not existing:
        return error("Student not found", 404)

    allowed_fields = [
        "full_name", "mobile", "alternate_mobile", "aadhar_number",
        "gender", "date_of_birth", "photo_url", "father_name",
        "mother_name", "address", "city", "state", "pincode",
        "email", "occupation", "emergency_contact_name",
        "emergency_contact_mobile", "notes", "status"
    ]

    # ---- Validate before applying ----
    if "mobile" in data:
        new_mobile = str(data["mobile"]).strip()
        if not validate_mobile(new_mobile):
            return error("Mobile must be a valid 10-digit Indian number")

        # Duplicate check (excluding self)
        dup = execute_query(
            """
            SELECT id FROM students
            WHERE mobile = %s AND id <> %s
            LIMIT 1
            """,
            (new_mobile, student_id),
            fetchone=True
        )
        if dup:
            return error("Another student already uses this mobile number")
        data["mobile"] = new_mobile

    if "gender" in data:
        if str(data["gender"]).upper() not in ["MALE", "FEMALE", "OTHER"]:
            return error("Invalid gender")
        data["gender"] = str(data["gender"]).upper()

    if "status" in data:
        if str(data["status"]).upper() not in [
            "ACTIVE", "EXPIRED", "INACTIVE", "LEFT"
        ]:
            return error("Invalid status")
        data["status"] = str(data["status"]).upper()

    if "aadhar_number" in data:
        val = data["aadhar_number"]
        # Ignore masked value sent back from frontend
        if is_masked_aadhaar(val):
            data.pop("aadhar_number")
        elif not validate_aadhaar(val):
            return error("Aadhaar must be 12 digits")
        else:
            data["aadhar_number"] = clean_aadhaar(val)

    if "pincode" in data and not validate_pincode(data["pincode"]):
        return error("Pincode must be 6 digits")

    if "email" in data and not validate_email(data["email"]):
        return error("Invalid email address")

    fields = []
    values = []

    for field in allowed_fields:
        if field in data:
            fields.append(f"{field} = %s")
            values.append(data[field] if data[field] != "" else None)

    if not fields:
        return error("No fields to update")

    values.append(student_id)

    execute_query(
        f"""
        UPDATE students
        SET {', '.join(fields)}
        WHERE id = %s
        """,
        tuple(values),
        commit=True
    )

    log_activity(
        current_user_id(),
        "UPDATE",
        "STUDENT",
        student_id,
        "Updated student"
    )
    

    return success({"id": student_id}, "Student updated")





# @app.route("/api/students/<int:student_id>", methods=["PUT"])
# @login_required
# def update_student(student_id):

#     data = get_json()

#     existing = execute_query(
#         """
#         SELECT id
#         FROM students
#         WHERE id = %s
#         """,
#         (student_id,),
#         fetchone=True
#     )

#     if not existing:

#         return error(
#             "Student not found",
#             404
#         )

#     allowed_fields = [
#         "full_name",
#         "mobile",
#         "alternate_mobile",
#         "aadhar_number",
#         "gender",
#         "date_of_birth",
#         "photo_url",
#         "father_name",
#         "mother_name",
#         "address",
#         "city",
#         "state",
#         "pincode",
#         "email",
#         "occupation",
#         "emergency_contact_name",
#         "emergency_contact_mobile",
#         "notes",
#         "status"
#     ]

#     fields = []
#     values = []

#     for field in allowed_fields:

#         if field in data:

#             fields.append(f"{field} = %s")
#             values.append(data[field])

#     if not fields:

#         return error(
#             "No fields to update"
#         )

#     values.append(student_id)

#     query = f"""
#         UPDATE students
#         SET {', '.join(fields)}
#         WHERE id = %s
#     """

#     execute_query(
#         query,
#         tuple(values),
#         commit=True
#     )

#     log_activity(
#         current_user_id(),
#         "UPDATE",
#         "STUDENT",
#         student_id,
#         "Updated student"
#     )

#     return success(
#         None,
#         "Student updated"
#     )



@app.route("/api/students/<int:student_id>", methods=["DELETE"])
@login_required
def delete_student(student_id):

    existing = execute_query(
        """
        SELECT id
        FROM students
        WHERE id = %s
        """,
        (student_id,),
        fetchone=True
    )

    if not existing:

        return error(
            "Student not found",
            404
        )

    # Soft delete
    execute_query(
        """
        UPDATE students
        SET status = 'INACTIVE'
        WHERE id = %s
        """,
        (student_id,),
        commit=True
    )

    log_activity(
        current_user_id(),
        "DEACTIVATE",
        "STUDENT",
        student_id,
        "Student marked inactive"
    )

    return success(
        None,
        "Student deactivated"
    )


# ============================================================
# SEATS
# ============================================================

# @app.route("/api/seats", methods=["GET"])
# @login_required
# def get_seats():

#     seats = execute_query(
#         """
#         SELECT
#             id,
#             seat_number,
#             status,
#             gender_preference,
#             notes,
#             created_at,
#             updated_at
#         FROM seats
#         ORDER BY id ASC
#         """,
#         fetchall=True
#     )

#     return success(
#         seats,
#         "Seats fetched",
#         len(seats)
#     )


# @app.route("/api/seats", methods=["GET"])
# @login_required
# def get_seats():
#     # Which slot are we checking availability FOR?
#     slot = request.args.get("slot", "FULL_TIME").upper()

#     seats = execute_query(
#         """
#         SELECT
#             s.id,
#             s.seat_number,
#             s.gender_preference,
#             s.status AS physical_status,
#             s.notes,
#             s.created_at,
#             s.updated_at,

#             -- full time holder
#             MAX(CASE WHEN m.slot_type = 'FULL_TIME'
#                      AND m.status = 'ACTIVE'
#                      AND CURDATE() BETWEEN m.start_date AND m.expiry_date
#                 THEN 1 ELSE 0 END) AS has_full,

#             -- morning holder
#             MAX(CASE WHEN m.slot_type = 'HALF_MORNING'
#                      AND m.status = 'ACTIVE'
#                      AND CURDATE() BETWEEN m.start_date AND m.expiry_date
#                 THEN 1 ELSE 0 END) AS has_morning,

#             -- evening holder
#             MAX(CASE WHEN m.slot_type = 'HALF_EVENING'
#                      AND m.status = 'ACTIVE'
#                      AND CURDATE() BETWEEN m.start_date AND m.expiry_date
#                 THEN 1 ELSE 0 END) AS has_evening

#         FROM seats s
#         LEFT JOIN student_memberships m ON m.seat_id = s.id
#         WHERE s.status NOT IN ('MAINTENANCE', 'BLOCKED')
#         GROUP BY s.id
#         ORDER BY s.id ASC
#         """,
#         fetchall=True,
#     )

#     # For each seat, compute "available for requested slot"
#     for seat in seats:
#         has_full = seat.pop("has_full") or 0
#         has_morning = seat.pop("has_morning") or 0
#         has_evening = seat.pop("has_evening") or 0

#         if seat["physical_status"] in ("MAINTENANCE", "BLOCKED"):
#             seat["available"] = False
#             seat["occupants"] = []
#             continue

#         available = False

#         if slot == "FULL_TIME":
#             # Free only if nobody holds it in any slot
#             available = not has_full and not has_morning and not has_evening

#         elif slot == "HALF_MORNING":
#             # Free if nobody holds full time AND nobody holds morning
#             available = not has_full and not has_morning

#         elif slot == "HALF_EVENING":
#             available = not has_full and not has_evening

#         seat["available"] = available

#         occupants = []
#         if has_full:
#             occupants.append("FULL_TIME")
#         if has_morning:
#             occupants.append("HALF_MORNING")
#         if has_evening:
#             occupants.append("HALF_EVENING")
#         seat["occupants"] = occupants

#         # Legacy field for old frontend
#         if has_full:
#             seat["status"] = "OCCUPIED"
#         elif has_morning and has_evening:
#             seat["status"] = "OCCUPIED"
#         elif has_morning or has_evening:
#             seat["status"] = "PARTIALLY_OCCUPIED"
#         else:
#             seat["status"] = "AVAILABLE"

#     return success(seats, "Seats fetched", len(seats))



# @app.route("/api/seats/available", methods=["GET"])
# @login_required
# def available_seats():

#     gender = request.args.get(
#         "gender",
#         "ANY"
#     ).upper()

#     query = """
#         SELECT
#             id,
#             seat_number,
#             status,
#             gender_preference
#         FROM seats
#         WHERE status = 'AVAILABLE'
#         AND (
#             gender_preference = 'ANY'
#             OR gender_preference = %s
#         )
#         ORDER BY id ASC
#     """

#     seats = execute_query(
#         query,
#         (gender,),
#         fetchall=True
#     )

#     return success(
#         seats,
#         "Available seats",
#         len(seats)
#     )


@app.route("/api/seats", methods=["GET"])
@login_required
def get_seats():
    # req_slot tells us what we are trying to book: ALL, FULL_TIME, HALF_MORNING, HALF_EVENING
    req_slot = request.args.get("slot", "ALL").upper()

    # 1. Fetch all seats (we no longer care about gender_preference or seat slot restrictions)
    seats = execute_query(
        """
        SELECT id, seat_number, status, notes 
        FROM seats 
        ORDER BY id ASC
        """,
        fetchall=True,
    )

    # 2. Fetch all ACTIVE memberships for today
    active_memberships = execute_query(
        """
        SELECT
            m.seat_id,
            m.slot_type,
            s.full_name,
            s.gender,
            s.student_code
        FROM student_memberships m
        INNER JOIN students s ON s.id = m.student_id
        WHERE m.status = 'ACTIVE'
          AND CURDATE() BETWEEN m.start_date AND m.expiry_date
        """,
        fetchall=True,
    )

    # Map memberships to seats for quick lookup
    seat_occupants = {}
    for m in active_memberships:
        sid = m["seat_id"]
        if not sid:
            continue
        if sid not in seat_occupants:
            seat_occupants[sid] = []
        seat_occupants[sid].append(m)

    result = []
    for seat in seats:
        sid = seat["id"]
        occupants = seat_occupants.get(sid, [])

        has_full = any(o["slot_type"] == "FULL_TIME" for o in occupants)
        has_morning = any(o["slot_type"] == "HALF_MORNING" for o in occupants)
        has_evening = any(o["slot_type"] == "HALF_EVENING" for o in occupants)

        # Determine if this seat can be booked for the requested slot
        is_available = False

        # If the physical seat is broken/blocked, it's never available
        if seat["status"] in ("MAINTENANCE", "BLOCKED"):
            is_available = False
        else:
            if req_slot == "ALL":
                # If asking for ALL, it's available if nobody has full time AND it's not double-booked half/half
                is_available = not has_full and not (has_morning and has_evening)
            elif req_slot == "FULL_TIME":
                # Full time needs the whole day empty
                is_available = not has_full and not has_morning and not has_evening
            elif req_slot == "HALF_MORNING":
                # Morning is available if no full-timer and no morning person
                is_available = not has_full and not has_morning
            elif req_slot == "HALF_EVENING":
                # Evening is available if no full-timer and no evening person
                is_available = not has_full and not has_evening

        # Determine the derived visual status for the frontend
        if seat["status"] in ("MAINTENANCE", "BLOCKED"):
            derived_status = seat["status"]
        elif has_full or (has_morning and has_evening):
            derived_status = "OCCUPIED"
        elif has_morning or has_evening:
            derived_status = "PARTIALLY_OCCUPIED"
        else:
            derived_status = "AVAILABLE"

        seat["available"] = is_available
        seat["status"] = derived_status
        seat["occupants"] = occupants  # Send full details of who is there

        # If the user filtered by a specific slot, only return the available seats
        if req_slot == "ALL" or is_available:
            result.append(seat)

    return success(result, "Seats fetched", len(result))


# @app.route("/api/seats", methods=["GET"])
# @login_required
# def get_seats():
#     slot = request.args.get("slot", "FULL_TIME").upper()

#     if slot not in ("FULL_TIME", "HALF_MORNING", "HALF_EVENING"):
#         return error("Invalid slot")

#     seats = execute_query(
#         """
#         SELECT
#             s.id,
#             s.seat_number,
#             s.status AS physical_status,
#             s.gender_preference,
#             s.notes,
#             s.created_at,
#             s.updated_at,

#             MAX(CASE WHEN m.slot_type = 'FULL_TIME'
#                      AND m.status = 'ACTIVE'
#                      AND CURDATE() BETWEEN m.start_date AND m.expiry_date
#                 THEN 1 ELSE 0 END) AS has_full,

#             MAX(CASE WHEN m.slot_type = 'HALF_MORNING'
#                      AND m.status = 'ACTIVE'
#                      AND CURDATE() BETWEEN m.start_date AND m.expiry_date
#                 THEN 1 ELSE 0 END) AS has_morning,

#             MAX(CASE WHEN m.slot_type = 'HALF_EVENING'
#                      AND m.status = 'ACTIVE'
#                      AND CURDATE() BETWEEN m.start_date AND m.expiry_date
#                 THEN 1 ELSE 0 END) AS has_evening

#         FROM seats s
#         LEFT JOIN student_memberships m ON m.seat_id = s.id
#         GROUP BY s.id
#         ORDER BY s.id ASC
#         """,
#         fetchall=True,
#     )

#     for seat in seats:
#         has_full = int(seat.pop("has_full") or 0)
#         has_morning = int(seat.pop("has_morning") or 0)
#         has_evening = int(seat.pop("has_evening") or 0)

#         physical = seat.get("physical_status")

#         # Compute availability for the requested slot
#         if physical in ("MAINTENANCE", "BLOCKED"):
#             available = False
#         elif slot == "FULL_TIME":
#             available = not has_full and not has_morning and not has_evening
#         elif slot == "HALF_MORNING":
#             available = not has_full and not has_morning
#         elif slot == "HALF_EVENING":
#             available = not has_full and not has_evening
#         else:
#             available = False

#         # Occupants list
#         occupants = []
#         if has_full:
#             occupants.append("FULL_TIME")
#         if has_morning:
#             occupants.append("HALF_MORNING")
#         if has_evening:
#             occupants.append("HALF_EVENING")

#         # Derived status (for frontend/legacy)
#         if physical in ("MAINTENANCE", "BLOCKED"):
#             derived_status = physical
#         elif has_full or (has_morning and has_evening):
#             derived_status = "OCCUPIED"
#         elif has_morning or has_evening:
#             derived_status = "OCCUPIED"
#         else:
#             derived_status = "AVAILABLE"

#         seat["available"] = available
#         seat["occupants"] = occupants
#         seat["status"] = derived_status

#     return success(seats, "Seats fetched", len(seats))


@app.route("/api/seats/available", methods=["GET"])
@login_required
def available_seats():
    gender = request.args.get("gender", "ANY").upper()
    slot = request.args.get("slot", "FULL_TIME").upper()

    if slot not in ("FULL_TIME", "HALF_MORNING", "HALF_EVENING"):
        return error("Invalid slot")

    seats = execute_query(
        """
        SELECT
            s.id,
            s.seat_number,
            s.status AS physical_status,
            s.gender_preference,

            MAX(CASE WHEN m.slot_type = 'FULL_TIME'
                     AND m.status = 'ACTIVE'
                     AND CURDATE() BETWEEN m.start_date AND m.expiry_date
                THEN 1 ELSE 0 END) AS has_full,

            MAX(CASE WHEN m.slot_type = 'HALF_MORNING'
                     AND m.status = 'ACTIVE'
                     AND CURDATE() BETWEEN m.start_date AND m.expiry_date
                THEN 1 ELSE 0 END) AS has_morning,

            MAX(CASE WHEN m.slot_type = 'HALF_EVENING'
                     AND m.status = 'ACTIVE'
                     AND CURDATE() BETWEEN m.start_date AND m.expiry_date
                THEN 1 ELSE 0 END) AS has_evening

        FROM seats s
        LEFT JOIN student_memberships m ON m.seat_id = s.id
        WHERE s.status NOT IN ('MAINTENANCE', 'BLOCKED')
        AND (s.gender_preference = 'ANY' OR s.gender_preference = %s)
        GROUP BY s.id
        ORDER BY s.id ASC
        """,
        (gender,),
        fetchall=True,
    )

    result = []

    for seat in seats:
        has_full = int(seat.pop("has_full") or 0)
        has_morning = int(seat.pop("has_morning") or 0)
        has_evening = int(seat.pop("has_evening") or 0)

        if slot == "FULL_TIME":
            ok = not has_full and not has_morning and not has_evening
        elif slot == "HALF_MORNING":
            ok = not has_full and not has_morning
        else:
            ok = not has_full and not has_evening

        if ok:
            seat["status"] = "AVAILABLE"
            seat.pop("physical_status", None)
            result.append(seat)

    return success(result, "Available seats", len(result))


@app.route("/api/seats/<int:seat_id>", methods=["PUT"])
@login_required
def update_seat(seat_id):

    data = get_json()

    status = data.get("status")
    gender_preference = data.get(
        "gender_preference"
    )
    notes = data.get("notes")

    existing = execute_query(
        """
        SELECT id
        FROM seats
        WHERE id = %s
        """,
        (seat_id,),
        fetchone=True
    )

    if not existing:

        return error(
            "Seat not found",
            404
        )

    fields = []
    values = []

    if status is not None:

        if status not in [
            "AVAILABLE",
            "OCCUPIED",
            "MAINTENANCE",
            "BLOCKED"
        ]:

            return error(
                "Invalid seat status"
            )

        fields.append("status = %s")
        values.append(status)

    if gender_preference is not None:

        if gender_preference not in [
            "ANY",
            "MALE",
            "FEMALE"
        ]:

            return error(
                "Invalid gender preference"
            )

        fields.append(
            "gender_preference = %s"
        )

        values.append(
            gender_preference
        )

    if notes is not None:

        fields.append("notes = %s")
        values.append(notes)

    if not fields:

        return error(
            "No fields to update"
        )

    values.append(seat_id)

    execute_query(
        f"""
        UPDATE seats
        SET {', '.join(fields)}
        WHERE id = %s
        """,
        tuple(values),
        commit=True
    )

    log_activity(
        current_user_id(),
        "UPDATE",
        "SEAT",
        seat_id,
        "Updated seat"
    )

    return success(
        None,
        "Seat updated"
    )


# ============================================================
# FEE PLANS
# ============================================================

# @app.route("/api/fee-plans", methods=["GET"])
# @login_required
# def get_fee_plans():

#     status = request.args.get(
#         "status",
#         "ACTIVE"
#     ).upper()

#     plans = execute_query(
#         """
#         SELECT
#             id,
#             plan_name,
#             duration_days,
#             amount,
#             description,
#             status,
#             created_at,
#             updated_at
#         FROM fee_plans
#         WHERE status = %s
#         ORDER BY duration_days ASC
#         """,
#         (status,),
#         fetchall=True
#     )

#     return success(
#         plans,
#         "Fee plans fetched",
#         len(plans)
#     )


@app.route("/api/fee-plans", methods=["GET"])
@login_required
def get_fee_plans():
    status = request.args.get("status", "ACTIVE").upper()

    plans = execute_query(
        """
        SELECT
            id,
            plan_name,
            duration_days,
            slot_type,           -- NEW
            amount,
            description,
            status,
            created_at,
            updated_at
        FROM fee_plans
        WHERE status = %s
        ORDER BY slot_type ASC, duration_days ASC
        """,
        (status,),
        fetchall=True,
    )

    return success(plans, "Fee plans fetched", len(plans))

@app.route("/api/fee-plans", methods=["POST"])
@login_required
def create_fee_plan():

    data = get_json()

    plan_name = str(
        data.get("plan_name", "")
    ).strip()

    duration_days = data.get("duration_days")
    amount = data.get("amount")
    slot_type = str(data.get("slot_type", "FULL_TIME")).upper()


    if not plan_name or not duration_days or amount is None:

        return error(
            "Plan name, duration and amount are required"
        )
    if slot_type not in ("FULL_TIME", "HALF_MORNING", "HALF_EVENING"):
        return error("Invalid slot type")

    plan_id = execute_insert(
        """
        INSERT INTO fee_plans
        (
            plan_name,
            duration_days,
            amount,
            description,
            status,
            slot_type,
            created_by
        )
        VALUES (%s,%s,%s,%s,'ACTIVE',%s,%s)
        """,
        (
            plan_name,
            duration_days,
            amount,
            data.get("description"),
            slot_type,
            current_user_id()
        )
    )

    log_activity(
        current_user_id(),
        "CREATE",
        "FEE_PLAN",
        plan_id,
        f"Created fee plan {plan_name}"
    )

    return success(
        {"id": plan_id},
        "Fee plan created"
    )


@app.route("/api/fee-plans/<int:plan_id>", methods=["PUT"])
@login_required
def update_fee_plan(plan_id):

    data = get_json()

    fields = []
    values = []

    allowed = [
        "plan_name",
        "duration_days",
        "amount",
        "description",
        "status",
        "slot_type"
    ]

    for field in allowed:

        if field in data:

            fields.append(
                f"{field} = %s"
            )

            values.append(
                data[field]
            )

    if not fields:

        return error(
            "No fields to update"
        )

    values.append(plan_id)

    execute_query(
        f"""
        UPDATE fee_plans
        SET {', '.join(fields)}
        WHERE id = %s
        """,
        tuple(values),
        commit=True
    )

    log_activity(
        current_user_id(),
        "UPDATE",
        "FEE_PLAN",
        plan_id,
        "Updated fee plan"
    )

    return success(
        None,
        "Fee plan updated"
    )


# ============================================================
# PAYMENT METHODS
# ============================================================

@app.route("/api/payment-methods", methods=["GET"])
@login_required
def get_payment_methods():

    methods = execute_query(
        """
        SELECT
            id,
            method_name,
            status,
            created_at
        FROM payment_methods
        WHERE status = 'ACTIVE'
        ORDER BY method_name ASC
        """,
        fetchall=True
    )

    return success(
        methods,
        "Payment methods fetched",
        len(methods)
    )


@app.route("/api/payment-methods", methods=["POST"])
@login_required
def create_payment_method():

    data = get_json()

    method_name = str(
        data.get("method_name", "")
    ).strip()

    if not method_name:

        return error(
            "Payment method name is required"
        )

    method_id = execute_insert(
        """
        INSERT INTO payment_methods
        (
            method_name,
            status
        )
        VALUES (%s,'ACTIVE')
        """,
        (method_name,)
    )

    log_activity(
        current_user_id(),
        "CREATE",
        "PAYMENT_METHOD",
        method_id,
        f"Created payment method {method_name}"
    )

    return success(
        {"id": method_id},
        "Payment method created"
    )


# ============================================================
# MEMBERSHIPS
# ============================================================

# @app.route("/api/memberships", methods=["GET"])
# @login_required
# def get_memberships():

#     student_id = request.args.get(
#         "student_id"
#     )

#     query = """
#         SELECT
#             sm.id,
#             sm.student_id,
#             s.student_code,
#             s.full_name,
#             s.mobile,
#             s.gender,

#             sm.fee_plan_id,
#             fp.plan_name,

#             sm.seat_id,
#             se.seat_number,

#             sm.joining_date,
#             sm.start_date,
#             sm.expiry_date,

#             sm.plan_amount,
#             sm.discount_amount,
#             sm.final_amount,
#             sm.paid_amount,
#             sm.due_amount,

#             sm.status,
#             sm.remarks,

#             sm.created_at,
#             sm.updated_at

#         FROM student_memberships sm

#         INNER JOIN students s
#             ON s.id = sm.student_id

#         INNER JOIN fee_plans fp
#             ON fp.id = sm.fee_plan_id

#         LEFT JOIN seats se
#             ON se.id = sm.seat_id

#         WHERE 1=1
#     """

#     params = []

#     if student_id:

#         query += """
#             AND sm.student_id = %s
#         """

#         params.append(student_id)

#     query += """
#         ORDER BY sm.id DESC
#     """

#     memberships = execute_query(
#         query,
#         tuple(params),
#         fetchall=True
#     )

#     return success(
#         memberships,
#         "Memberships fetched",
#         len(memberships)
#     )


@app.route("/api/memberships", methods=["GET"])
@login_required
def get_memberships():
    student_id = request.args.get("student_id")
    status = request.args.get("status")
    slot = request.args.get("slot")

    query = """
        SELECT
            sm.id,
            sm.student_id,
            s.student_code,
            s.full_name,
            s.mobile,
            s.gender,

            sm.fee_plan_id,
            fp.plan_name,

            sm.slot_type,           -- NEW

            sm.seat_id,
            se.seat_number,

            sm.joining_date,
            sm.start_date,
            sm.expiry_date,

            sm.plan_amount,
            sm.discount_amount,
            sm.final_amount,
            sm.paid_amount,
            sm.due_amount,

            sm.status,
            sm.remarks,

            sm.created_at,
            sm.updated_at

        FROM student_memberships sm
        INNER JOIN students s ON s.id = sm.student_id
        INNER JOIN fee_plans fp ON fp.id = sm.fee_plan_id
        LEFT JOIN seats se ON se.id = sm.seat_id
        WHERE 1=1
    """

    params = []

    if student_id:
        query += " AND sm.student_id = %s"
        params.append(student_id)

    if status:
        query += " AND sm.status = %s"
        params.append(status.upper())

    if slot:
        query += " AND sm.slot_type = %s"
        params.append(slot.upper())

    query += " ORDER BY sm.id DESC"

    memberships = execute_query(query, tuple(params), fetchall=True)

    return success(memberships, "Memberships fetched", len(memberships))

# @app.route("/api/memberships", methods=["POST"])
# @login_required
# def create_membership():

#     data = get_json()

#     student_id = data.get("student_id")
#     fee_plan_id = data.get("fee_plan_id")
#     seat_id = data.get("seat_id")

#     joining_date = data.get("joining_date")
#     start_date = data.get("start_date")

#     discount = Decimal(
#         str(data.get("discount_amount", 0))
#     )

#     if not student_id or not fee_plan_id:

#         return error(
#             "Student and fee plan are required"
#         )

#     # Check student
#     student = execute_query(
#         """
#         SELECT id, gender, status
#         FROM students
#         WHERE id = %s
#         """,
#         (student_id,),
#         fetchone=True
#     )

#     if not student:

#         return error(
#             "Student not found",
#             404
#         )

#     if student["status"] != "ACTIVE":

#         return error(
#             "Student is not active"
#         )

#     # Check fee plan
#     plan = execute_query(
#         """
#         SELECT
#             id,
#             amount,
#             duration_days,
#             status
#         FROM fee_plans
#         WHERE id = %s
#         """,
#         (fee_plan_id,),
#         fetchone=True
#     )

#     if not plan:

#         return error(
#             "Fee plan not found",
#             404
#         )

#     if plan["status"] != "ACTIVE":

#         return error(
#             "Fee plan is inactive"
#         )

#     # Check active membership
#     active_membership = execute_query(
#         """
#         SELECT id
#         FROM student_memberships
#         WHERE student_id = %s
#         AND status = 'ACTIVE'
#         LIMIT 1
#         """,
#         (student_id,),
#         fetchone=True
#     )

#     if active_membership:

#         return error(
#             "Student already has an active membership"
#         )

#     # Seat validation
#     if seat_id:

#         seat = execute_query(
#             """
#             SELECT
#                 id,
#                 status,
#                 gender_preference
#             FROM seats
#             WHERE id = %s
#             """,
#             (seat_id,),
#             fetchone=True
#         )

#         if not seat:

#             return error(
#                 "Seat not found",
#                 404
#             )

#         if seat["status"] != "AVAILABLE":

#             return error(
#                 "Seat is not available"
#             )

#         if (
#             seat["gender_preference"] != "ANY"
#             and seat["gender_preference"] != student["gender"]
#         ):

#             return error(
#                 "Seat is not available for this gender"
#             )

#     # Dates
#     if not start_date:

#         start_date = joining_date

#     if not joining_date:

#         joining_date = start_date

#     if not start_date:

#         return error(
#             "Start date is required"
#         )

#     from datetime import timedelta

#     start_obj = datetime.strptime(
#         start_date,
#         "%Y-%m-%d"
#     ).date()

#     expiry_obj = (
#         start_obj +
#         timedelta(days=plan["duration_days"] - 1)
#     )

#     expiry_date = expiry_obj.strftime(
#         "%Y-%m-%d"
#     )

#     plan_amount = Decimal(
#         str(plan["amount"])
#     )

#     final_amount = plan_amount - discount

#     if final_amount < 0:

#         return error(
#             "Discount cannot exceed plan amount"
#         )

#     paid_amount = Decimal(
#         str(data.get("paid_amount", 0))
#     )

#     if paid_amount < 0:

#         return error(
#             "Paid amount cannot be negative"
#         )

#     if paid_amount > final_amount:

#         return error(
#             "Paid amount cannot exceed final amount"
#         )

#     due_amount = final_amount - paid_amount

#     connection = get_db()

#     if not connection:

#         return error(
#             "Database connection failed",
#             500
#         )

#     cursor = None

#     try:

#         cursor = connection.cursor()

#         cursor.execute(
#             """
#             INSERT INTO student_memberships
#             (
#                 student_id,
#                 fee_plan_id,
#                 seat_id,
#                 joining_date,
#                 start_date,
#                 expiry_date,
#                 plan_amount,
#                 discount_amount,
#                 final_amount,
#                 paid_amount,
#                 due_amount,
#                 status,
#                 remarks,
#                 created_by
#             )
#             VALUES
#             (
#                 %s,%s,%s,%s,%s,%s,
#                 %s,%s,%s,%s,%s,
#                 'ACTIVE',%s,%s
#             )
#             """,
#             (
#                 student_id,
#                 fee_plan_id,
#                 seat_id,
#                 joining_date,
#                 start_date,
#                 expiry_date,
#                 plan_amount,
#                 discount,
#                 final_amount,
#                 paid_amount,
#                 due_amount,
#                 data.get("remarks"),
#                 current_user_id()
#             )
#         )

#         membership_id = cursor.lastrowid

#         # Occupy seat
#         if seat_id:
#             # After inserting membership with seat_id, recompute seat status
#             cursor.execute(
#                 """
#                 SELECT
#                     MAX(CASE WHEN slot_type = 'FULL_TIME' THEN 1 ELSE 0 END) AS has_full,
#                     MAX(CASE WHEN slot_type = 'HALF_MORNING' THEN 1 ELSE 0 END) AS has_morning,
#                     MAX(CASE WHEN slot_type = 'HALF_EVENING' THEN 1 ELSE 0 END) AS has_evening
#                 FROM student_memberships
#                 WHERE seat_id = %s
#                 AND status = 'ACTIVE'
#                 AND CURDATE() BETWEEN start_date AND expiry_date
#                 """,
#                 (seat_id,),
#             )
#             row = cursor.fetchone()

#             new_status = 'AVAILABLE'
#             if row[0]:
#                 new_status = 'OCCUPIED'
#             elif row[1] and row[2]:
#                 new_status = 'OCCUPIED'
#             elif row[1] or row[2]:
#                 new_status = 'PARTIALLY_OCCUPIED'

#             cursor.execute(
#                 "UPDATE seats SET status = %s WHERE id = %s",
#                 (new_status, seat_id),
#             )

#             # cursor.execute(
#             #     """
#             #     UPDATE seats
#             #     SET status = 'OCCUPIED'
#             #     WHERE id = %s
#             #     """,
#             #     (seat_id,)
#             # )

#         connection.commit()

#     except Exception as e:

#         connection.rollback()

#         print("Membership error:", e)

#         return error(
#             str(e),
#             500
#         )

#     finally:

#         if cursor:
#             cursor.close()

#         connection.close()

#     log_activity(
#         current_user_id(),
#         "CREATE",
#         "MEMBERSHIP",
#         membership_id,
#         "Created membership"
#     )

#     return success(
#         {
#             "membership_id": membership_id,
#             "expiry_date": expiry_date,
#             "final_amount": float(final_amount),
#             "paid_amount": float(paid_amount),
#             "due_amount": float(due_amount)
#         },
#         "Membership created"
#     )



@app.route("/api/memberships", methods=["POST"])
@login_required
def create_membership():
    data = get_json()

    student_id = data.get("student_id")
    fee_plan_id = data.get("fee_plan_id")
    seat_id = data.get("seat_id")

    joining_date = data.get("joining_date")
    start_date = data.get("start_date")

    discount = Decimal(str(data.get("discount_amount", 0)))

    if not student_id or not fee_plan_id:
        return error("Student and fee plan are required")

    # ---- Student ----
    student = execute_query(
        """
        SELECT id, gender, status
        FROM students
        WHERE id = %s
        """,
        (student_id,),
        fetchone=True,
    )

    if not student:
        return error("Student not found", 404)

    if student["status"] != "ACTIVE":
        return error("Student is not active")

    # ---- Fee plan (now includes slot_type) ----
    plan = execute_query(
        """
        SELECT
            id,
            amount,
            duration_days,
            slot_type,
            status
        FROM fee_plans
        WHERE id = %s
        """,
        (fee_plan_id,),
        fetchone=True,
    )

    if not plan:
        return error("Fee plan not found", 404)

    if plan["status"] != "ACTIVE":
        return error("Fee plan is inactive")

    slot_type = plan["slot_type"] or "FULL_TIME"

    if slot_type not in ("FULL_TIME", "HALF_MORNING", "HALF_EVENING"):
        return error("Fee plan has invalid slot type")

    # ---- Active membership (same slot cannot be duplicated) ----
    active_same_slot = execute_query(
        """
        SELECT id
        FROM student_memberships
        WHERE student_id = %s
          AND status = 'ACTIVE'
          AND CURDATE() BETWEEN start_date AND expiry_date
          AND slot_type = %s
        LIMIT 1
        """,
        (student_id, slot_type),
        fetchone=True,
    )

    if active_same_slot:
        return error(
            f"Student already has an active {slot_type.replace('_', ' ').lower()} membership"
        )

    # ---- Seat availability for this slot ----
    # if seat_id:
    #     seat = execute_query(
    #         """
    #         SELECT
    #             s.id,
    #             s.status,
    #             s.gender_preference,
    #             MAX(CASE WHEN m.slot_type = 'FULL_TIME' AND m.status='ACTIVE'
    #                      AND CURDATE() BETWEEN m.start_date AND m.expiry_date
    #                 THEN 1 ELSE 0 END) AS has_full,
    #             MAX(CASE WHEN m.slot_type = 'HALF_MORNING' AND m.status='ACTIVE'
    #                      AND CURDATE() BETWEEN m.start_date AND m.expiry_date
    #                 THEN 1 ELSE 0 END) AS has_morning,
    #             MAX(CASE WHEN m.slot_type = 'HALF_EVENING' AND m.status='ACTIVE'
    #                      AND CURDATE() BETWEEN m.start_date AND m.expiry_date
    #                 THEN 1 ELSE 0 END) AS has_evening
    #         FROM seats s
    #         LEFT JOIN student_memberships m ON m.seat_id = s.id
    #         WHERE s.id = %s
    #         GROUP BY s.id
    #         """,
    #         (seat_id,),
    #         fetchone=True,
    #     )

    #     if not seat:
    #         return error("Seat not found", 404)

    #     if seat["status"] in ("MAINTENANCE", "BLOCKED"):
    #         return error("Seat is under maintenance or blocked")

    #     has_full = int(seat["has_full"] or 0)
    #     has_morning = int(seat["has_morning"] or 0)
    #     has_evening = int(seat["has_evening"] or 0)

    #     if slot_type == "FULL_TIME":
    #         if has_full or has_morning or has_evening:
    #             return error("Seat is not available for full time")
    #     elif slot_type == "HALF_MORNING":
    #         if has_full or has_morning:
    #             return error("Seat is not available for morning slot")
    #     elif slot_type == "HALF_EVENING":
    #         if has_full or has_evening:
    #             return error("Seat is not available for evening slot")

    #     if (
    #         seat["gender_preference"] != "ANY"
    #         and seat["gender_preference"] != student["gender"]
    #     ):
    #         return error("Seat is not available for this gender")

    
        # ---- Seat availability for this slot ----
    if seat_id:
        seat = execute_query(
            """
            SELECT
                s.id,
                s.status,
                MAX(CASE WHEN m.slot_type = 'FULL_TIME' AND m.status='ACTIVE'
                         AND CURDATE() BETWEEN m.start_date AND m.expiry_date
                    THEN 1 ELSE 0 END) AS has_full,
                MAX(CASE WHEN m.slot_type = 'HALF_MORNING' AND m.status='ACTIVE'
                         AND CURDATE() BETWEEN m.start_date AND m.expiry_date
                    THEN 1 ELSE 0 END) AS has_morning,
                MAX(CASE WHEN m.slot_type = 'HALF_EVENING' AND m.status='ACTIVE'
                         AND CURDATE() BETWEEN m.start_date AND m.expiry_date
                    THEN 1 ELSE 0 END) AS has_evening
            FROM seats s
            LEFT JOIN student_memberships m ON m.seat_id = s.id
            WHERE s.id = %s
            GROUP BY s.id
            """,
            (seat_id,),
            fetchone=True,
        )

        if not seat:
            return error("Seat not found", 404)

        if seat["status"] in ("MAINTENANCE", "BLOCKED"):
            return error("Seat is under maintenance or blocked")

        has_full = int(seat["has_full"] or 0)
        has_morning = int(seat["has_morning"] or 0)
        has_evening = int(seat["has_evening"] or 0)

        if slot_type == "FULL_TIME":
            if has_full or has_morning or has_evening:
                return error("Seat is not available for full time")
        elif slot_type == "HALF_MORNING":
            if has_full or has_morning:
                return error("Seat is not available for morning slot")
        elif slot_type == "HALF_EVENING":
            if has_full or has_evening:
                return error("Seat is not available for evening slot")

        # 👉 REMOVED: The gender preference check

        
    # ---- Dates ----
    if not start_date:
        start_date = joining_date

    if not joining_date:
        joining_date = start_date

    if not start_date:
        return error("Start date is required")

    # from datetime import timedelta

    # try:
    #     start_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
    # except ValueError:
    #     return error("Start date must be YYYY-MM-DD")

    # expiry_obj = start_obj + timedelta(days=plan["duration_days"] - 1)
    # expiry_date = expiry_obj.strftime("%Y-%m-%d")
    from datetime import datetime
    from dateutil.relativedelta import relativedelta

    try:
        start_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
    except ValueError:
        return error("Start date must be YYYY-MM-DD")

    # 👉 Accurate Calendar Calculation
    duration = plan["duration_days"]
    
    if duration == 30:
        expiry_obj = start_obj + relativedelta(months=1) - relativedelta(days=1)
    elif duration == 180:
        expiry_obj = start_obj + relativedelta(months=6) - relativedelta(days=1)
    elif duration == 365:
        expiry_obj = start_obj + relativedelta(years=1) - relativedelta(days=1)
    else:
        # For 7 days or custom days
        expiry_obj = start_obj + timedelta(days=duration - 1)

    expiry_date = expiry_obj.strftime("%Y-%m-%d")

    plan_amount = Decimal(str(plan["amount"]))
    final_amount = plan_amount - discount

    if final_amount < 0:
        return error("Discount cannot exceed plan amount")

    paid_amount = Decimal(str(data.get("paid_amount", 0)))

    if paid_amount < 0:
        return error("Paid amount cannot be negative")

    if paid_amount > final_amount:
        return error("Paid amount cannot exceed final amount")

    due_amount = final_amount - paid_amount

    # ---- Insert + recompute seat ----
    connection = get_db()

    if not connection:
        return error("Database connection failed", 500)

    cursor = None

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO student_memberships
            (
                student_id,
                fee_plan_id,
                slot_type,
                seat_id,
                joining_date,
                start_date,
                expiry_date,
                plan_amount,
                discount_amount,
                final_amount,
                paid_amount,
                due_amount,
                status,
                remarks,
                created_by
            )
            VALUES
            (
                %s,%s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s,
                'ACTIVE',%s,%s
            )
            """,
            (
                student_id,
                fee_plan_id,
                slot_type,
                seat_id,
                joining_date,
                start_date,
                expiry_date,
                plan_amount,
                discount,
                final_amount,
                paid_amount,
                due_amount,
                data.get("remarks"),
                current_user_id(),
            ),
        )

        membership_id = cursor.lastrowid

        # Recompute seat status (includes the row we just inserted)
        if seat_id:
            cursor.execute(
                """
                SELECT
                    MAX(CASE WHEN slot_type = 'FULL_TIME' THEN 1 ELSE 0 END) AS has_full,
                    MAX(CASE WHEN slot_type = 'HALF_MORNING' THEN 1 ELSE 0 END) AS has_morning,
                    MAX(CASE WHEN slot_type = 'HALF_EVENING' THEN 1 ELSE 0 END) AS has_evening
                FROM student_memberships
                WHERE seat_id = %s
                  AND status = 'ACTIVE'
                  AND CURDATE() BETWEEN start_date AND expiry_date
                """,
                (seat_id,),
            )
            row = cursor.fetchone()

            has_full = int(row[0] or 0)
            has_morning = int(row[1] or 0)
            has_evening = int(row[2] or 0)

            if has_full or (has_morning and has_evening):
                new_status = "OCCUPIED"
            elif has_morning or has_evening:
                new_status = "PARTIALLY_OCCUPIED"
            else:
                new_status = "AVAILABLE"

            cursor.execute(
                "UPDATE seats SET status = %s WHERE id = %s",
                (new_status, seat_id),
            )

        connection.commit()

    except Exception as e:
        connection.rollback()
        print("Membership error:", e)
        return error(str(e), 500)

    finally:
        if cursor:
            cursor.close()
        connection.close()

    log_activity(
        current_user_id(),
        "CREATE",
        "MEMBERSHIP",
        membership_id,
        f"Created {slot_type} membership",
    )

    return success(
        {
            "membership_id": membership_id,
            "slot_type": slot_type,
            "expiry_date": expiry_date,
            "final_amount": float(final_amount),
            "paid_amount": float(paid_amount),
            "due_amount": float(due_amount),
        },
        "Membership created",
    )




# @app.route("/api/memberships/<int:membership_id>/cancel", methods=["POST"])
# @login_required
# def cancel_membership(membership_id):
#     membership = execute_query(
#         """
#         SELECT id, seat_id, status
#         FROM student_memberships
#         WHERE id = %s
#         """,
#         (membership_id,),
#         fetchone=True,
#     )

#     if not membership:
#         return error("Membership not found", 404)

#     if membership["status"] != "ACTIVE":
#         return error("Only active memberships can be cancelled")

#     connection = get_db()
#     if not connection:
#         return error("Database connection failed", 500)

#     cursor = None
#     try:
#         cursor = connection.cursor()
#         cursor.execute(
#             """
#             UPDATE student_memberships
#             SET status = 'CANCELLED'
#             WHERE id = %s
#             """,
#             (membership_id,),
#         )

#         if membership["seat_id"]:
#             cursor.execute(
#                 """
#                 UPDATE seats
#                 SET status = 'AVAILABLE'
#                 WHERE id = %s AND status = 'OCCUPIED'
#                 """,
#                 (membership["seat_id"],),
#             )

#         connection.commit()
#     except Exception as e:
#         connection.rollback()
#         return error(str(e), 500)
#     finally:
#         if cursor:
#             cursor.close()
#         connection.close()

#     log_activity(
#         current_user_id(),
#         "CANCEL",
#         "MEMBERSHIP",
#         membership_id,
#         "Cancelled membership",
#     )

#     return success(None, "Membership cancelled")


@app.route("/api/memberships/<int:membership_id>/cancel", methods=["POST"])
@login_required
def cancel_membership(membership_id):
    membership = execute_query(
        """
        SELECT id, seat_id, status
        FROM student_memberships
        WHERE id = %s
        """,
        (membership_id,),
        fetchone=True,
    )

    if not membership:
        return error("Membership not found", 404)

    if membership["status"] != "ACTIVE":
        return error("Only active memberships can be cancelled")

    connection = get_db()
    if not connection:
        return error("Database connection failed", 500)

    cursor = None
    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE student_memberships
            SET status = 'CANCELLED'
            WHERE id = %s
            """,
            (membership_id,),
        )

        # Recompute seat status instead of blindly freeing
        if membership["seat_id"]:
            cursor.execute(
                """
                SELECT
                    MAX(CASE WHEN slot_type = 'FULL_TIME' THEN 1 ELSE 0 END) AS has_full,
                    MAX(CASE WHEN slot_type = 'HALF_MORNING' THEN 1 ELSE 0 END) AS has_morning,
                    MAX(CASE WHEN slot_type = 'HALF_EVENING' THEN 1 ELSE 0 END) AS has_evening
                FROM student_memberships
                WHERE seat_id = %s
                  AND status = 'ACTIVE'
                  AND CURDATE() BETWEEN start_date AND expiry_date
                """,
                (membership["seat_id"],),
            )
            row = cursor.fetchone()

            has_full = int(row[0] or 0)
            has_morning = int(row[1] or 0)
            has_evening = int(row[2] or 0)

            if has_full or (has_morning and has_evening):
                new_status = "OCCUPIED"
            elif has_morning or has_evening:
                new_status = "PARTIALLY_OCCUPIED"
            else:
                new_status = "AVAILABLE"

            cursor.execute(
                "UPDATE seats SET status = %s WHERE id = %s",
                (new_status, membership["seat_id"]),
            )

        connection.commit()
    except Exception as e:
        connection.rollback()
        return error(str(e), 500)
    finally:
        if cursor:
            cursor.close()
        connection.close()

    log_activity(
        current_user_id(),
        "CANCEL",
        "MEMBERSHIP",
        membership_id,
        "Cancelled membership",
    )

    return success(None, "Membership cancelled")


# @app.route("/api/maintenance/expire-memberships", methods=["POST"])
# @login_required
# def expire_memberships():
#     """Mark expired memberships as EXPIRED and free their seats."""
#     connection = get_db()
#     if not connection:
#         return error("Database connection failed", 500)

#     cursor = None
#     try:
#         cursor = connection.cursor(dictionary=True)

#         # Find seats tied to memberships that just expired
#         cursor.execute(
#             """
#             SELECT id, seat_id
#             FROM student_memberships
#             WHERE status = 'ACTIVE'
#               AND expiry_date < CURDATE()
#             """
#         )
#         expired = cursor.fetchall()

#         if not expired:
#             return success(
#                 {"expired": 0}, "No expired memberships"
#             )

#         seat_ids = [m["seat_id"] for m in expired if m["seat_id"]]
#         membership_ids = [m["id"] for m in expired]

#         placeholders = ",".join(["%s"] * len(membership_ids))
#         cursor.execute(
#             f"""
#             UPDATE student_memberships
#             SET status = 'EXPIRED'
#             WHERE id IN ({placeholders})
#             """,
#             tuple(membership_ids),
#         )

#         if seat_ids:
#             seat_placeholders = ",".join(["%s"] * len(seat_ids))
#             cursor.execute(
#                 f"""
#                 UPDATE seats
#                 SET status = 'AVAILABLE'
#                 WHERE id IN ({seat_placeholders})
#                   AND status = 'OCCUPIED'
#                 """,
#                 tuple(seat_ids),
#             )

#         connection.commit()

#         return success(
#             {"expired": len(expired)},
#             f"{len(expired)} memberships expired",
#         )
#     except Exception as e:
#         connection.rollback()
#         return error(str(e), 500)
#     finally:
#         if cursor:
#             cursor.close()
#         connection.close()


@app.route("/api/maintenance/expire-memberships", methods=["POST"])
@login_required
def expire_memberships():
    """Mark expired memberships and recompute affected seat statuses."""

    connection = get_db()
    if not connection:
        return error("Database connection failed", 500)

    cursor = None
    try:
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT id, seat_id
            FROM student_memberships
            WHERE status = 'ACTIVE'
              AND expiry_date < CURDATE()
            """
        )
        expired = cursor.fetchall()

        if not expired:
            return success({"expired": 0}, "No expired memberships")

        membership_ids = [m["id"] for m in expired]
        seat_ids = list({m["seat_id"] for m in expired if m["seat_id"]})

        placeholders = ",".join(["%s"] * len(membership_ids))
        cursor.execute(
            f"""
            UPDATE student_memberships
            SET status = 'EXPIRED'
            WHERE id IN ({placeholders})
            """,
            tuple(membership_ids),
        )

        # Recompute seat status for every affected seat
        for seat_id in seat_ids:
            cursor.execute(
                """
                SELECT
                    MAX(CASE WHEN slot_type = 'FULL_TIME' THEN 1 ELSE 0 END) AS has_full,
                    MAX(CASE WHEN slot_type = 'HALF_MORNING' THEN 1 ELSE 0 END) AS has_morning,
                    MAX(CASE WHEN slot_type = 'HALF_EVENING' THEN 1 ELSE 0 END) AS has_evening
                FROM student_memberships
                WHERE seat_id = %s
                  AND status = 'ACTIVE'
                  AND CURDATE() BETWEEN start_date AND expiry_date
                """,
                (seat_id,),
            )
            row = cursor.fetchone()

            has_full = int(row["has_full"] or 0)
            has_morning = int(row["has_morning"] or 0)
            has_evening = int(row["has_evening"] or 0)

            if has_full or (has_morning and has_evening):
                new_status = "OCCUPIED"
            elif has_morning or has_evening:
                new_status = "PARTIALLY_OCCUPIED"
            else:
                new_status = "AVAILABLE"

            cursor.execute(
                "UPDATE seats SET status = %s WHERE id = %s",
                (new_status, seat_id),
            )

        connection.commit()

        return success(
            {"expired": len(expired), "seats_updated": len(seat_ids)},
            f"{len(expired)} memberships expired",
        )

    except Exception as e:
        connection.rollback()
        return error(str(e), 500)
    finally:
        if cursor:
            cursor.close()
        connection.close()


# ============================================================
# PAYMENTS
# ============================================================

@app.route("/api/payments", methods=["GET"])
@login_required
def get_payments():
    student_id = request.args.get("student_id")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    query = """
        SELECT
            p.id,
            p.payment_number,
            p.student_id,
            s.student_code,
            s.full_name,
            p.membership_id,
            p.payment_method_id,
            pm.method_name,
            p.amount,
            p.payment_date,
            p.transaction_reference,
            p.received_by,
            u.name AS received_by_name,
            p.remarks,
            p.status,
            p.created_at
        FROM payments p
        INNER JOIN students s ON s.id = p.student_id
        INNER JOIN payment_methods pm ON pm.id = p.payment_method_id
        LEFT JOIN users u ON u.id = p.received_by
        WHERE 1=1
    """
    params = []

    if student_id:
        query += " AND p.student_id = %s"
        params.append(student_id)

    # 👉 Filter by Date Range
    if start_date and end_date:
        query += " AND DATE(p.payment_date) BETWEEN %s AND %s"
        params.extend([start_date, end_date])

    query += " ORDER BY p.id DESC"

    payments = execute_query(query, tuple(params), fetchall=True)

    return success(payments, "Payments fetched", len(payments))


# @app.route("/api/payments", methods=["GET"])
# @login_required
# def get_payments():

#     student_id = request.args.get(
#         "student_id"
#     )

#     query = """
#         SELECT
#             p.id,
#             p.payment_number,

#             p.student_id,
#             s.student_code,
#             s.full_name,

#             p.membership_id,

#             p.payment_method_id,
#             pm.method_name,

#             p.amount,
#             p.payment_date,

#             p.transaction_reference,

#             p.received_by,
#             u.name AS received_by_name,

#             p.remarks,
#             p.status,

#             p.created_at

#         FROM payments p

#         INNER JOIN students s
#             ON s.id = p.student_id

#         INNER JOIN payment_methods pm
#             ON pm.id = p.payment_method_id

#         LEFT JOIN users u
#             ON u.id = p.received_by

#         WHERE 1=1
#     """

#     params = []

#     if student_id:

#         query += """
#             AND p.student_id = %s
#         """

#         params.append(student_id)

#     query += """
#         ORDER BY p.id DESC
#     """

#     payments = execute_query(
#         query,
#         tuple(params),
#         fetchall=True
#     )

#     return success(
#         payments,
#         "Payments fetched",
#         len(payments)
#     )



# @app.route("/api/payments", methods=["POST"])
# @login_required
# def create_payment():

#     data = get_json()

#     student_id = data.get("student_id")
#     membership_id = data.get("membership_id")
#     payment_method_id = data.get(
#         "payment_method_id"
#     )

#     amount = data.get("amount")

#     if (
#         not student_id
#         or not payment_method_id
#         or amount is None
#     ):

#         return error(
#             "Student, payment method and amount are required"
#         )

#     try:

#         amount = Decimal(
#             str(amount)
#         )

#     except:

#         return error(
#             "Invalid payment amount"
#         )

#     if amount <= 0:

#         return error(
#             "Payment amount must be greater than zero"
#         )

#     # Student
#     student = execute_query(
#         """
#         SELECT id
#         FROM students
#         WHERE id = %s
#         """,
#         (student_id,),
#         fetchone=True
#     )

#     if not student:

#         return error(
#             "Student not found",
#             404
#         )

#     # Payment method
#     method = execute_query(
#         """
#         SELECT id
#         FROM payment_methods
#         WHERE id = %s
#         AND status = 'ACTIVE'
#         """,
#         (payment_method_id,),
#         fetchone=True
#     )

#     if not method:

#         return error(
#             "Payment method not found or inactive"
#         )

#     # Membership
#     membership = None

#     if membership_id:

#         membership = execute_query(
#             """
#             SELECT
#                 id,
#                 final_amount,
#                 paid_amount,
#                 due_amount,
#                 status
#             FROM student_memberships
#             WHERE id = %s
#             AND student_id = %s
#             """,
#             (
#                 membership_id,
#                 student_id
#             ),
#             fetchone=True
#         )

#         if not membership:

#             return error(
#                 "Membership not found",
#                 404
#             )

#         if membership["status"] == "CANCELLED":

#             return error(
#                 "Cannot pay cancelled membership"
#             )

#         if amount > Decimal(
#             str(membership["due_amount"])
#         ):

#             return error(
#                 "Payment cannot exceed due amount"
#             )

#     payment_number = generate_number(
#         "PAY",
#         "payments",
#         "payment_number"
#     )

#     connection = get_db()

#     if not connection:

#         return error(
#             "Database connection failed",
#             500
#         )

#     cursor = None

#     try:

#         cursor = connection.cursor()

#         cursor.execute(
#             """
#             INSERT INTO payments
#             (
#                 payment_number,
#                 student_id,
#                 membership_id,
#                 payment_method_id,
#                 amount,
#                 transaction_reference,
#                 received_by,
#                 remarks,
#                 status
#             )
#             VALUES
#             (
#                 %s,%s,%s,%s,%s,%s,%s,%s,'SUCCESS'
#             )
#             """,
#             (
#                 payment_number,
#                 student_id,
#                 membership_id,
#                 payment_method_id,
#                 amount,
#                 data.get(
#                     "transaction_reference"
#                 ),
#                 current_user_id(),
#                 data.get("remarks")
#             )
#         )

#         payment_id = cursor.lastrowid

#         # Update membership
#         if membership:

#             new_paid = (
#                 Decimal(
#                     str(membership["paid_amount"])
#                 )
#                 + amount
#             )

#             new_due = (
#                 Decimal(
#                     str(membership["final_amount"])
#                 )
#                 - new_paid
#             )

#             if new_due < 0:
#                 new_due = Decimal("0")

#             cursor.execute(
#                 """
#                 UPDATE student_memberships
#                 SET
#                     paid_amount = %s,
#                     due_amount = %s
#                 WHERE id = %s
#                 """,
#                 (
#                     new_paid,
#                     new_due,
#                     membership_id
#                 )
#             )

#         connection.commit()

#     except Exception as e:

#         connection.rollback()

#         print("Payment error:", e)

#         return error(
#             str(e),
#             500
#         )

#     finally:

#         if cursor:
#             cursor.close()

#         connection.close()

#     log_activity(
#         current_user_id(),
#         "CREATE",
#         "PAYMENT",
#         payment_id,
#         f"Created payment {payment_number}"
#     )

#     return success(
#         {
#             "payment_id": payment_id,
#             "payment_number": payment_number,
#             "amount": float(amount)
#         },
#         "Payment recorded"
#     )



# @app.route("/api/payments", methods=["POST"])
# @login_required
# def create_payment():
#     data = get_json()

#     student_id = data.get("student_id")
#     membership_id = data.get("membership_id")
#     payment_method_id = data.get("payment_method_id")
#     amount = data.get("amount")

#     if not student_id or not payment_method_id or amount is None:
#         return error("Student, payment method and amount are required")

#     try:
#         amount = Decimal(str(amount))
#     except:
#         return error("Invalid payment amount")

#     if amount <= 0:
#         return error("Payment amount must be greater than zero")

#     # Student
#     student = execute_query(
#         "SELECT id FROM students WHERE id = %s", (student_id,), fetchone=True
#     )
#     if not student:
#         return error("Student not found", 404)

#     # Payment method
#     method = execute_query(
#         "SELECT id FROM payment_methods WHERE id = %s AND status = 'ACTIVE'",
#         (payment_method_id,),
#         fetchone=True,
#     )
#     if not method:
#         return error("Payment method not found or inactive")

#     # Membership
#     membership = None
#     if membership_id:
#         membership = execute_query(
#             """
#             SELECT id, final_amount, paid_amount, due_amount, status, expiry_date, fee_plan_id
#             FROM student_memberships
#             WHERE id = %s AND student_id = %s
#             """,
#             (membership_id, student_id),
#             fetchone=True,
#         )

#         if not membership:
#             return error("Membership not found", 404)
#         if membership["status"] == "CANCELLED":
#             return error("Cannot pay cancelled membership")

#     payment_number = generate_number("PAY", "payments", "payment_number")
#     connection = get_db()
#     if not connection:
#         return error("Database connection failed", 500)

#     cursor = None
#     try:
#         cursor = connection.cursor()

#         cursor.execute(
#             """
#             INSERT INTO payments
#             (payment_number, student_id, membership_id, payment_method_id, amount,
#              transaction_reference, received_by, remarks, status)
#             VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'SUCCESS')
#             """,
#             (
#                 payment_number, student_id, membership_id, payment_method_id,
#                 amount, data.get("transaction_reference"), current_user_id(),
#                 data.get("remarks"),
#             ),
#         )
#         payment_id = cursor.lastrowid

#         # 👉 NEW LOGIC: Auto-extend expiry if payment equals the plan amount
#         if membership:
#             new_paid = Decimal(str(membership["paid_amount"])) + amount
#             new_due = Decimal(str(membership["final_amount"])) - new_paid
#             if new_due < 0:
#                 new_due = Decimal("0")

#             # Check if this payment is a full renewal (amount >= plan final_amount)
#             is_renewal = amount >= Decimal(str(membership["final_amount"]))
            
#             new_expiry = membership["expiry_date"]

#             if is_renewal:
#                 from datetime import datetime
#                 from dateutil.relativedelta import relativedelta

#                 # Get plan duration
#                 plan = execute_query(
#                     "SELECT duration_days FROM fee_plans WHERE id = %s",
#                     (membership["fee_plan_id"],),
#                     fetchone=True,
#                 )
                
#                 if plan:
#                     duration = plan["duration_days"]
#                     old_expiry_obj = membership["expiry_date"]
                    
#                     # Ensure it's a date object (MySQL might return string)
#                     if isinstance(old_expiry_obj, str):
#                         old_expiry_obj = datetime.strptime(old_expiry_obj, "%Y-%m-%d").date()

#                     if duration == 30:
#                         new_expiry_obj = old_expiry_obj + relativedelta(months=1)
#                     elif duration == 180:
#                         new_expiry_obj = old_expiry_obj + relativedelta(months=6)
#                     elif duration == 365:
#                         new_expiry_obj = old_expiry_obj + relativedelta(years=1)
#                     else:
#                         new_expiry_obj = old_expiry_obj + relativedelta(days=duration)
                    
#                     new_expiry = new_expiry_obj.strftime("%Y-%m-%d")

#                     # Reset paid_amount and due_amount for the new cycle
#                     new_paid = Decimal("0")
#                     new_due = Decimal("0")

#             cursor.execute(
#                 """
#                 UPDATE student_memberships
#                 SET paid_amount = %s, due_amount = %s, expiry_date = %s
#                 WHERE id = %s
#                 """,
#                 (new_paid, new_due, new_expiry, membership_id),
#             )

#         connection.commit()

#     except Exception as e:
#         connection.rollback()
#         print("Payment error:", e)
#         return error(str(e), 500)
#     finally:
#         if cursor:
#             cursor.close()
#         connection.close()

#     log_activity(
#         current_user_id(), "CREATE", "PAYMENT", payment_id,
#         f"Created payment {payment_number}",
#     )

#     return success(
#         {
#             "payment_id": payment_id,
#             "payment_number": payment_number,
#             "amount": float(amount),
#             "new_expiry": str(new_expiry) if membership else None
#         },
#         "Payment recorded and membership extended" if membership and is_renewal else "Payment recorded",
#     )


@app.route("/api/payments", methods=["POST"])
@login_required
def create_payment():
    data = get_json()

    student_id = data.get("student_id")
    membership_id = data.get("membership_id")
    payment_method_id = data.get("payment_method_id")
    amount = data.get("amount")

    if not student_id or not payment_method_id or amount is None:
        return error("Student, payment method and amount are required")

    try:
        amount = Decimal(str(amount))
    except:
        return error("Invalid payment amount")

    if amount <= 0:
        return error("Payment amount must be greater than zero")

    # Student
    student = execute_query("SELECT id FROM students WHERE id = %s", (student_id,), fetchone=True)
    if not student:
        return error("Student not found", 404)

    # Payment method
    method = execute_query("SELECT id FROM payment_methods WHERE id = %s AND status = 'ACTIVE'", (payment_method_id,), fetchone=True)
    if not method:
        return error("Payment method not found or inactive")

    # Membership
    membership = None
    if membership_id:
        membership = execute_query(
            """
            SELECT id, final_amount, paid_amount, due_amount, status, expiry_date, fee_plan_id
            FROM student_memberships
            WHERE id = %s AND student_id = %s
            """,
            (membership_id, student_id),
            fetchone=True,
        )

        if not membership:
            return error("Membership not found", 404)
        if membership["status"] == "CANCELLED":
            return error("Cannot pay cancelled membership")

    payment_number = generate_number("PAY", "payments", "payment_number")
    connection = get_db()
    if not connection:
        return error("Database connection failed", 500)

    cursor = None
    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO payments
            (payment_number, student_id, membership_id, payment_method_id, amount,
             transaction_reference, received_by, remarks, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'SUCCESS')
            """,
            (
                payment_number, student_id, membership_id, payment_method_id,
                amount, data.get("transaction_reference"), current_user_id(),
                data.get("remarks"),
            ),
        )
        payment_id = cursor.lastrowid

        if membership:
            current_due = Decimal(str(membership["due_amount"]))
            current_paid = Decimal(str(membership["paid_amount"]))
            
            # 1. Pay off the current due
            amount_towards_due = min(amount, current_due)
            new_due = current_due - amount_towards_due
            new_paid = current_paid + amount_towards_due
            
            # 2. If there's extra money left over, treat it as a renewal (advance payment)
            extra_amount = amount - amount_towards_due
            
            new_expiry = membership["expiry_date"]
            is_renewal = False

            if extra_amount > 0:
                is_renewal = True
                from datetime import datetime
                from dateutil.relativedelta import relativedelta

                plan = execute_query("SELECT duration_days FROM fee_plans WHERE id = %s", (membership["fee_plan_id"],), fetchone=True)
                
                if plan:
                    duration = plan["duration_days"]
                    old_expiry_obj = membership["expiry_date"]
                    
                    if isinstance(old_expiry_obj, str):
                        old_expiry_obj = datetime.strptime(old_expiry_obj, "%Y-%m-%d").date()

                    if duration == 30:
                        new_expiry_obj = old_expiry_obj + relativedelta(months=1)
                    elif duration == 180:
                        new_expiry_obj = old_expiry_obj + relativedelta(months=6)
                    elif duration == 365:
                        new_expiry_obj = old_expiry_obj + relativedelta(years=1)
                    else:
                        new_expiry_obj = old_expiry_obj + relativedelta(days=duration)
                    
                    new_expiry = new_expiry_obj.strftime("%Y-%m-%d")
                    
                    # The extra amount covers the new cycle, so we reset the paid/due balances for the new cycle
                    new_paid = extra_amount
                    new_due = Decimal(str(membership["final_amount"])) - extra_amount

            cursor.execute(
                """
                UPDATE student_memberships
                SET paid_amount = %s, due_amount = %s, expiry_date = %s
                WHERE id = %s
                """,
                (new_paid, new_due, new_expiry, membership_id),
            )

        connection.commit()

    except Exception as e:
        connection.rollback()
        print("Payment error:", e)
        return error(str(e), 500)
    finally:
        if cursor:
            cursor.close()
        connection.close()

    log_activity(
        current_user_id(), "CREATE", "PAYMENT", payment_id,
        f"Created payment {payment_number}",
    )

    msg = "Payment recorded"
    if membership and is_renewal:
        msg = "Payment recorded and membership extended"

    return success(
        {
            "payment_id": payment_id,
            "payment_number": payment_number,
            "amount": float(amount),
            "new_expiry": str(new_expiry) if membership else None
        },
        msg,
    )


# ============================================================
# RECEIPTS
# ============================================================

@app.route("/api/receipts", methods=["GET"])
@login_required
def get_receipts():

    receipts = execute_query(
        """
        SELECT
            r.id,
            r.receipt_number,
            r.payment_id,

            p.payment_number,
            p.student_id,

            s.student_code,
            s.full_name,

            p.amount,

            pm.method_name,

            r.receipt_date,
            r.receipt_file_url,
            r.status,
            s.mobile


        FROM receipts r

        INNER JOIN payments p
            ON p.id = r.payment_id

        INNER JOIN students s
            ON s.id = p.student_id

        INNER JOIN payment_methods pm
            ON pm.id = p.payment_method_id

        ORDER BY r.id DESC
        """,
        fetchall=True
    )

    return success(
        receipts,
        "Receipts fetched",
        len(receipts)
    )


# @app.route("/api/receipts", methods=["POST"])
# @login_required
# def create_receipt():

#     data = get_json()

#     payment_id = data.get(
#         "payment_id"
#     )

#     if not payment_id:

#         return error(
#             "Payment ID is required"
#         )

#     payment = execute_query(
#         """
#         SELECT id
#         FROM payments
#         WHERE id = %s
#         AND status = 'SUCCESS'
#         """,
#         (payment_id,),
#         fetchone=True
#     )

#     if not payment:

#         return error(
#             "Valid payment not found",
#             404
#         )

#     existing = execute_query(
#         """
#         SELECT
#             id,
#             receipt_number
#         FROM receipts
#         WHERE payment_id = %s
#         """,
#         (payment_id,),
#         fetchone=True
#     )

#     if existing:

#         return success(
#             existing,
#             "Receipt already exists"
#         )

#     receipt_number = generate_number(
#         "REC",
#         "receipts",
#         "receipt_number"
#     )

#     receipt_id = execute_insert(
#         """
#         INSERT INTO receipts
#         (
#             receipt_number,
#             payment_id,
#             receipt_file_url,
#             status
#         )
#         VALUES (%s,%s,%s,'GENERATED')
#         """,
#         (
#             receipt_number,
#             payment_id,
#             data.get("receipt_file_url")
#         )
#     )

#     log_activity(
#         current_user_id(),
#         "CREATE",
#         "RECEIPT",
#         receipt_id,
#         f"Generated receipt {receipt_number}"
#     )

#     return success(
#         {
#             "receipt_id": receipt_id,
#             "receipt_number": receipt_number
#         },
#         "Receipt generated"
#     )


# Keep your create_receipt exactly as it was originally (just inserting into DB)
@app.route("/api/receipts", methods=["POST"])
@login_required
def create_receipt():
    data = get_json()
    payment_id = data.get("payment_id")
    if not payment_id: return error("Payment ID is required")

    existing = execute_query("SELECT id, receipt_number FROM receipts WHERE payment_id = %s", (payment_id,), fetchone=True)
    if existing: return success(existing, "Receipt already exists")

    receipt_number = generate_number("REC", "receipts", "receipt_number")
    receipt_id = execute_insert(
        "INSERT INTO receipts (receipt_number, payment_id, status) VALUES (%s,%s,'GENERATED')",
        (receipt_number, payment_id)
    )
    return success({"receipt_id": receipt_id, "receipt_number": receipt_number}, "Receipt generated")


# 👉 NEW ROUTE: Generate the image on the fly
# @app.route("/api/receipts/<int:payment_id>/image", methods=["GET"])
# @login_required
# def get_receipt_image(payment_id):
#     # Fetch receipt and payment details
#     receipt = execute_query(
#         """
#         SELECT r.receipt_number, p.payment_number, p.amount, p.payment_date,
#                s.full_name, s.student_code, s.mobile,
#                pm.method_name
#         FROM receipts r
#         INNER JOIN payments p ON p.id = r.payment_id
#         INNER JOIN students s ON s.id = p.student_id
#         INNER JOIN payment_methods pm ON pm.id = p.payment_method_id
#         WHERE p.id = %s
#         """,
#         (payment_id,), fetchone=True
#     )

#     if not receipt:
#         return error("Receipt not found for this payment", 404)

#     # Generate Image in Memory
#     img = Image.new('RGB', (600, 800), color='white')
#     draw = ImageDraw.Draw(img)
    
#     try:
#         font_large = ImageFont.truetype("arial.ttf", 40)
#         font_med = ImageFont.truetype("arial.ttf", 24)
#         font_small = ImageFont.truetype("arial.ttf", 18)
#     except IOError:
#         font_large = font_med = font_small = ImageFont.load_default()

#     draw.text((50, 50), "GYAN GANGA LIBRARY", fill="black", font=font_large)
#     draw.text((50, 100), "Near Canara Bank, Main Road, Gangashahar", fill="gray", font=font_small)
#     draw.text((50, 125), "Timing: 08:00 AM – 10:00 PM", fill="gray", font=font_small)
#     draw.line((50, 160, 550, 160), fill="black", width=2)

#     draw.text((50, 190), f"Receipt No: {receipt['receipt_number']}", fill="black", font=font_med)
#     draw.text((50, 230), f"Date: {str(receipt['payment_date'])[:10]}", fill="black", font=font_med)
#     draw.text((50, 270), f"Payment No: {receipt['payment_number']}", fill="black", font=font_med)
#     draw.line((50, 310, 550, 310), fill="gray", width=1)

#     draw.text((50, 340), "STUDENT DETAILS", fill="black", font=font_med)
#     draw.text((50, 380), f"Name: {receipt['full_name']}", fill="black", font=font_small)
#     draw.text((50, 410), f"Code: {receipt['student_code']}", fill="black", font=font_small)
#     draw.text((50, 440), f"Mobile: {receipt['mobile']}", fill="black", font=font_small)
#     draw.line((50, 480, 550, 480), fill="gray", width=1)

#     draw.text((50, 510), "PAYMENT DETAILS", fill="black", font=font_med)
#     draw.text((50, 550), f"Method: {receipt['method_name']}", fill="black", font=font_small)
#     draw.text((50, 600), "AMOUNT PAID", fill="black", font=font_med)
#     draw.text((50, 640), f"Rs. {receipt['amount']}", fill="green", font=font_large)
#     draw.text((50, 720), "Thank you for choosing Gyan Ganga Library.", fill="gray", font=font_small)

#     qr = qrcode.QRCode(box_size=4, border=1)
#     qr.add_data(f"Student: {receipt['student_code']} | Paid: {receipt['amount']}")
#     qr.make(fit=True)
#     qr_img = qr.make_image(fill_color="black", back_color="white").resize((100, 100))
#     img.paste(qr_img, (450, 650))

#     buffered = io.BytesIO()
#     img.save(buffered, format="PNG")
#     img_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')

#     return success({"image_base64": f"data:image/png;base64,{img_base64}"}, "Image generated")


"""
Premium square receipt generator for Gyan Ganga Library.

Dependencies:
    pip install Pillow "qrcode[pil]" Flask

Integration:
    Keep your existing execute_query(), login_required, error(), and success()
    helpers, then use the route at the bottom of this file.

Output:
    A sharp, square PNG rendered at 2400 x 2400 pixels.
    Text is drawn at high resolution with TrueType fonts and downsampled with
    Lanczos antialiasing. No student code is displayed or encoded in the QR.
"""

# Render at high resolution for crisp edges and typography.
SIZE = 2400
SCALE = 2
W = H = SIZE

NAVY = "#102D59"
BLUE = "#1769AA"
LIGHT_BLUE = "#EAF4FF"
BORDER = "#C8DFF5"
TEXT = "#293B50"
MUTED = "#65758B"
GREEN = "#159447"
PALE_GREEN = "#E6F7EC"
WHITE = "#FFFFFF"
BACKGROUND = "#F5F8FC"


def _font(size, bold=False):
    """Load a scalable TrueType font; try common Linux/macOS/Windows paths."""
    if bold:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ]

    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size=size)

    # Pillow's default font may be small; prefer installing DejaVu Sans.
    raise RuntimeError(
        "A TrueType font was not found. Install DejaVu Sans or configure "
        "a font path in _font()."
    )


def generate_receipt_image(receipt, output_path=None):
    """
    Generate a premium square receipt PNG.

    Required receipt keys:
        receipt_number, payment_number, payment_date, full_name,
        mobile, method_name, amount

    Optional:
        library_address, library_hours

    Student code is intentionally not displayed and not included in the QR.
    """
    # Work on a large canvas, then reduce to 1200x1200 with Lanczos.
    # Coordinates and font sizes are doubled for sharp output.
    canvas = Image.new("RGB", (W, H), BACKGROUND)
    d = ImageDraw.Draw(canvas)

    def f(size, bold=False):
        return _font(size * SCALE, bold)

    def box(coords, fill, outline=None, radius=22, width=2):
        coords = tuple(int(v * SCALE) for v in coords)
        d.rounded_rectangle(
            coords,
            radius=radius * SCALE,
            fill=fill,
            outline=outline,
            width=max(1, width * SCALE),
        )

    def text(x, y, value, selected_font, fill, anchor=None):
        d.text(
            (int(x * SCALE), int(y * SCALE)),
            str(value),
            font=selected_font,
            fill=fill,
            anchor=anchor,
        )

    def line(x1, y1, x2, y2, fill=BORDER, width=2):
        d.line(
            (x1 * SCALE, y1 * SCALE, x2 * SCALE, y2 * SCALE),
            fill=fill,
            width=max(1, width * SCALE),
        )

    def fitted(value, selected_font, max_width):
        value = str(value if value is not None else "-")
        max_px = max_width * SCALE
        while value and d.textbbox((0, 0), value, font=selected_font)[2] > max_px:
            value = value[:-2] + "…" if len(value) > 2 else ""
        return value or "…"

    def label_value(label, value, x, y, value_x, max_width):
        text(x, y, label, f(15), MUTED)
        text(value_x, y - 1, fitted(value, f(18, True), max_width),
             f(18, True), NAVY)

    # Main paper: square, clean, with a restrained border.
    box((14, 14, 1186, 1186), WHITE, outline=BLUE, radius=28, width=2)

    # Header and simple open-book logo.
    box((48, 48, 126, 126), LIGHT_BLUE, radius=18)
    line(65, 70, 86, 86, BLUE, 3)
    line(86, 86, 109, 70, BLUE, 3)
    line(65, 70, 65, 102, BLUE, 3)
    line(65, 102, 86, 113, BLUE, 3)
    line(86, 113, 109, 102, BLUE, 3)
    line(109, 70, 109, 102, BLUE, 3)
    line(86, 86, 86, 113, NAVY, 2)

    text(148, 53, "GYAN GANGA LIBRARY", f(30, True), NAVY)
    text(150, 94, receipt.get(
        "library_address", "Near Canara Bank, Main Road, Gangashahar"
    ), f(13), TEXT)
    text(150, 116, receipt.get(
        "library_hours", "Timing: 08:00 AM - 10:00 PM"
    ), f(13), MUTED)

    # Small slogan panel.
    box((1000, 51, 1145, 126), LIGHT_BLUE, radius=16)
    text(1020, 65, "READ", f(13, True), BLUE)
    text(1020, 87, "LEARN", f(13, True), NAVY)
    text(1020, 109, "GROW", f(13, True), BLUE)

    line(45, 151, 1155, 151, BLUE, 2)

    # Receipt title / number panel.
    box((45, 174, 1155, 300), LIGHT_BLUE, radius=22)
    box((65, 195, 125, 259), BLUE, radius=15)
    # Minimal receipt icon.
    d.rounded_rectangle(
        (76*SCALE, 204*SCALE, 114*SCALE, 248*SCALE),
        radius=4*SCALE, fill=WHITE
    )
    for yy in (214, 224, 234):
        line(83, yy, 106, yy, BLUE, 2)

    text(150, 192, "PAYMENT RECEIPT", f(29, True), NAVY)
    text(152, 239, "Official payment acknowledgement", f(15), TEXT)
    text(770, 195, "RECEIPT NO.", f(13, True), MUTED)
    text(
        770, 225,
        fitted(receipt.get("receipt_number", "-"), f(18, True), 350),
        f(18, True), BLUE
    )

    # Date and payment reference.
    label_value("PAYMENT DATE", str(receipt.get("payment_date") or "-")[:10],
                65, 326, 65, 350)
    label_value("PAYMENT NO.", receipt.get("payment_number", "-"),
                620, 326, 620, 480)
    line(65, 372, 1135, 372)

    # Two side-by-side cards make the square layout balanced.
    box((45, 395, 565, 750), WHITE, outline=BORDER, radius=20, width=2)
    box((45, 395, 565, 452), LIGHT_BLUE, radius=18)
    text(70, 410, "STUDENT DETAILS", f(20, True), NAVY)

    label_value("Name", receipt.get("full_name", "-"),
                72, 480, 205, 315)
    label_value("Mobile", receipt.get("mobile", "-"),
                72, 540, 205, 315)
    # Student code intentionally omitted.

    box((585, 395, 1155, 750), WHITE, outline=BORDER, radius=20, width=2)
    box((585, 395, 1155, 452), LIGHT_BLUE, radius=18)
    text(610, 410, "PAYMENT DETAILS", f(20, True), NAVY)

    text(615, 480, "PAYMENT METHOD", f(14), MUTED)
    text(615, 507, fitted(receipt.get("method_name", "-"), f(19, True), 490),
         f(19, True), NAVY)
    line(615, 548, 1125, 548)

    text(615, 570, "TOTAL AMOUNT PAID", f(14, True), BLUE)
    try:
        amount_text = f"₹ {float(receipt.get('amount') or 0):,.2f}"
    except (ValueError, TypeError):
        amount_text = f"Rs. {receipt.get('amount', '-')}"
    text(610, 600, fitted(amount_text, f(33, True), 500),
         f(33, True), GREEN)

    box((610, 674, 860, 718), PALE_GREEN, radius=22)
    # Checkmark.
    d.ellipse((625*SCALE, 685*SCALE, 651*SCALE, 711*SCALE), fill=GREEN)
    line(632, 698, 638, 704, WHITE, 2)
    line(638, 704, 646, 692, WHITE, 2)
    text(662, 686, "PAYMENT RECORDED", f(12, True), GREEN)

    # QR code card. QR only contains receipt/payment references and amount.
    box((900, 565, 1125, 718), "#F7FAFE", outline=BORDER, radius=14)
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(
        "Gyan Ganga Library\n"
        f"Receipt: {receipt.get('receipt_number', '-')}\n"
        f"Payment: {receipt.get('payment_number', '-')}\n"
        f"Amount: {receipt.get('amount', '-')}"
    )
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color=NAVY, back_color=WHITE).convert("RGB")
    qr_img = qr_img.resize((116 * SCALE, 116 * SCALE), Image.Resampling.NEAREST)
    canvas.paste(qr_img, (954 * SCALE, 570 * SCALE))
    text(918, 690, "SCAN FOR DETAILS", f(10, True), BLUE)

    # Footer: simple, no decorative clutter.
    line(45, 790, 1155, 790, BLUE, 2)
    text(600, 816, "THANK YOU!", f(24, True), NAVY, anchor="mt")
    text(600, 855, "Thank you for choosing Gyan Ganga Library.",
         f(14), TEXT, anchor="mt")
    text(600, 881, "Keep learning. Keep growing.",
         f(14), BLUE, anchor="mt")

    # Convert to 1200x1200 with high-quality antialiasing.
    final_img = canvas.resize((1200, 1200), Image.Resampling.LANCZOS)

    if output_path:
        final_img.save(output_path, format="PNG", optimize=True, dpi=(300, 300))

    output = io.BytesIO()
    final_img.save(output, format="PNG", optimize=True, dpi=(300, 300))
    return final_img, output.getvalue()


# ------------------------------------------------------
# Flask route integration
# Assumes: app, login_required, execute_query, error, success
# are already defined in your project.
# ------------------------------------------------------
@app.route("/api/receipts/<int:payment_id>/image", methods=["GET"])
@login_required
def get_receipt_image(payment_id):
    receipt = execute_query(
        """
        SELECT
            r.receipt_number,
            p.payment_number,
            p.amount,
            p.payment_date,
            s.full_name,
            s.mobile,
            pm.method_name
        FROM receipts r
        INNER JOIN payments p ON p.id = r.payment_id
        INNER JOIN students s ON s.id = p.student_id
        INNER JOIN payment_methods pm ON pm.id = p.payment_method_id
        WHERE p.id = %s
        """,
        (payment_id,),
        fetchone=True
    )

    if not receipt:
        return error("Receipt not found for this payment", 404)

    _, png_bytes = generate_receipt_image(receipt)
    image_base64 = base64.b64encode(png_bytes).decode("utf-8")

    return success(
        {"image_base64": f"data:image/png;base64,{image_base64}"},
        "Receipt image generated successfully"
    )


# @app.route("/api/receipts/<int:payment_id>/image", methods=["GET"])
# @login_required
# def get_receipt_image(payment_id):

#     receipt = execute_query(
#         """
#         SELECT
#             r.receipt_number,
#             p.payment_number,
#             p.amount,
#             p.payment_date,
#             s.full_name,
#             s.student_code,
#             s.mobile,
#             pm.method_name
#         FROM receipts r
#         INNER JOIN payments p ON p.id = r.payment_id
#         INNER JOIN students s ON s.id = p.student_id
#         INNER JOIN payment_methods pm
#             ON pm.id = p.payment_method_id
#         WHERE p.id = %s
#         """,
#         (payment_id,),
#         fetchone=True
#     )

#     if not receipt:
#         return error("Receipt not found for this payment", 404)

#     # --------------------------------------------------
#     # DESIGN CONFIGURATION
#     # --------------------------------------------------
#     # ```python
#     # ======================================================
#     # PREMIUM RECEIPT DESIGN — HIGH RESOLUTION
#     # ======================================================
#     # from PIL import Image, ImageDraw, ImageFont
#     # import qrcode
#     # import io
#     # import base64
#     # import os

#     W, H = 1200, 1600

#     NAVY = "#102D59"
#     BLUE = "#1769AA"
#     LIGHT_BLUE = "#EAF4FF"
#     BORDER = "#C8DFF5"
#     TEXT = "#293B50"
#     MUTED = "#65758B"
#     GREEN = "#159447"
#     WHITE = "#FFFFFF"

#     img = Image.new("RGB", (W, H), "#F4F8FC")
#     draw = ImageDraw.Draw(img)

#     # Fonts: increase sizes significantly for mobile readability
#     def font(size, bold=False):
#         candidates = (
#             [
#                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
#                 "/Library/Fonts/Arial Bold.ttf",
#                 "C:/Windows/Fonts/arialbd.ttf",
#             ] if bold else [
#                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
#                 "/Library/Fonts/Arial.ttf",
#                 "C:/Windows/Fonts/arial.ttf",
#             ]
#         )
#         for path in candidates:
#             if os.path.exists(path):
#                 return ImageFont.truetype(path, size)
#         return ImageFont.load_default()

#     F = {
#         "brand": font(43, True),
#         "title": font(48, True),
#         "section": font(29, True),
#         "label": font(23),
#         "value": font(27, True),
#         "small": font(20),
#         "amount": font(68, True),
#     }

#     def rounded(box, fill, outline=None, radius=24, width=2):
#         draw.rounded_rectangle(
#             box, radius=radius, fill=fill,
#             outline=outline, width=width
#         )

#     def rule(y, x1=75, x2=1125, color=BORDER, width=3):
#         draw.line((x1, y, x2, y), fill=color, width=width)

#     def fit(value, selected_font, max_width):
#         value = str(value if value is not None else "-")
#         if draw.textbbox((0, 0), value, font=selected_font)[2] <= max_width:
#             return value

#         while value and draw.textbbox(
#             (0, 0), value + "...", font=selected_font
#         )[2] > max_width:
#             value = value[:-1]
#         return value + "..."

#     def label_value(label, value, x, y, value_x, max_width):
#         draw.text((x, y), label, font=F["label"], fill=MUTED)
#         draw.text(
#             (value_x, y - 2),
#             fit(value, F["value"], max_width),
#             font=F["value"],
#             fill=NAVY,
#         )

#     # Main white paper with a strong blue frame
#     rounded((18, 18, W - 18, H - 18), WHITE, BLUE, 30, 5)

#     # ======================================================
#     # HEADER
#     # ======================================================
#     # Simple book emblem
#     rounded((65, 65, 165, 165), LIGHT_BLUE, radius=24)

#     draw.arc((82, 91, 119, 142), 180, 350, fill=BLUE, width=5)
#     draw.arc((112, 91, 149, 142), 190, 350, fill=BLUE, width=5)
#     draw.line((115, 102, 115, 148), fill=NAVY, width=5)

#     draw.text((195, 76), "GYAN GANGA LIBRARY",
#             font=F["brand"], fill=NAVY)

#     draw.text(
#         (198, 132),
#         "Near Canara Bank, Main Road, Gangashahar",
#         font=F["small"], fill=TEXT
#     )
#     draw.text(
#         (198, 166),
#         "OPEN DAILY  |  08:00 AM - 10:00 PM",
#         font=F["small"], fill=MUTED
#     )

#     rule(220, color=BLUE, width=4)

#     # ======================================================
#     # RECEIPT TITLE
#     # ======================================================
#     rounded((65, 250, 1135, 405), LIGHT_BLUE, radius=26)

#     draw.text((100, 272), "PAYMENT RECEIPT",
#             font=F["title"], fill=NAVY)
#     draw.text((103, 338), "Official payment acknowledgement",
#             font=F["label"], fill=TEXT)

#     draw.text((735, 277), "RECEIPT NO.",
#             font=F["small"], fill=MUTED)

#     draw.text(
#         (735, 318),
#         fit(receipt["receipt_number"], F["value"], 365),
#         font=F["value"], fill=BLUE
#     )

#     # ======================================================
#     # DATE AND PAYMENT NUMBER
#     # ======================================================
#     date_value = (
#         str(receipt["payment_date"])[:10]
#         if receipt["payment_date"] else "-"
#     )

#     label_value("PAYMENT DATE", date_value, 75, 450, 75, 450)

#     draw.text((625, 450), "PAYMENT NUMBER",
#             font=F["label"], fill=MUTED)
#     draw.text(
#         (625, 490),
#         fit(receipt["payment_number"], F["value"], 490),
#         font=F["value"], fill=NAVY
#     )

#     rule(545)

#     # ======================================================
#     # STUDENT DETAILS
#     # ======================================================
#     rounded((65, 575, 1135, 875), WHITE, BORDER, 24, 3)
#     rounded((65, 575, 1135, 650), LIGHT_BLUE, radius=22)

#     draw.text((100, 593), "STUDENT DETAILS",
#             font=F["section"], fill=NAVY)

#     label_value("Student Name", receipt["full_name"],
#                 100, 680, 365, 680)

#     label_value("Student Code", receipt["student_code"],
#                 100, 742, 365, 680)

#     label_value("Mobile Number", receipt["mobile"],
#                 100, 804, 365, 680)

#     # ======================================================
#     # PAYMENT DETAILS
#     # ======================================================
#     rounded((65, 905, 1135, 1375), WHITE, BORDER, 24, 3)
#     rounded((65, 905, 1135, 980), LIGHT_BLUE, radius=22)

#     draw.text((100, 923), "PAYMENT DETAILS",
#             font=F["section"], fill=NAVY)

#     draw.text((100, 1015), "PAYMENT METHOD",
#             font=F["small"], fill=MUTED)
#     draw.text(
#         (100, 1050),
#         fit(receipt["method_name"], F["value"], 570),
#         font=F["value"], fill=NAVY
#     )

#     rule(1105, 100, 760)

#     draw.text((100, 1130), "TOTAL AMOUNT PAID",
#             font=F["label"], fill=BLUE)

#     try:
#         amount = float(receipt["amount"] or 0)
#         amount_text = f"\u20b9 {amount:,.2f}"
#     except (ValueError, TypeError):
#         amount_text = f"Rs. {receipt['amount']}"

#     draw.text(
#         (95, 1175),
#         fit(amount_text, F["amount"], 590),
#         font=F["amount"], fill=GREEN
#     )

#     # QR panel
#     rounded((790, 1010, 1090, 1325), "#F6FAFF", BORDER, 22, 3)

#     qr = qrcode.QRCode(
#         box_size=6,
#         border=3,
#         error_correction=qrcode.constants.ERROR_CORRECT_M
#     )
#     qr.add_data(
#         f"Gyan Ganga Library | "
#         f"Receipt: {receipt['receipt_number']} | "
#         f"Payment: {receipt['payment_number']} | "
#         f"Student: {receipt['student_code']} | "
#         f"Amount: {receipt['amount']}"
#     )
#     qr.make(fit=True)

#     qr_img = qr.make_image(
#         fill_color=NAVY, back_color=WHITE
#     ).convert("RGB")
#     qr_img = qr_img.resize((220, 220))
#     img.paste(qr_img, (825, 1025))

#     qr_caption = "SCAN FOR RECEIPT DETAILS"
#     draw.text((817, 1260), qr_caption,
#             font=font(15, True), fill=BLUE)

#     # Paid indicator
#     rounded((100, 1280, 435, 1335), "#E6F7EC", radius=26)
#     draw.text((130, 1294), "PAYMENT RECORDED",
#             font=font(19, True), fill=GREEN)

#     # ======================================================
#     # FOOTER
#     # ======================================================
#     rule(1410, color=BLUE, width=4)

#     draw.text((W // 2, 1435), "THANK YOU!",
#             font=font(32, True), fill=NAVY, anchor="mt")

#     draw.text(
#         (W // 2, 1485),
#         "Thank you for choosing Gyan Ganga Library.",
#         font=F["small"], fill=MUTED, anchor="mt"
#     )

#     draw.text(
#         (W // 2, 1520),
#         "Keep learning. Keep growing.",
#         font=F["small"], fill=BLUE, anchor="mt"
#     )

#     # ======================================================
#     # BASE64 RESPONSE — KEEP YOUR EXISTING API CONTRACT
#     # ======================================================
#     buffered = io.BytesIO()
#     img.save(buffered, format="PNG", optimize=True)

#     img_base64 = base64.b64encode(
#         buffered.getvalue()
#     ).decode("utf-8")

#     return success(
#         {"image_base64": f"data:image/png;base64,{img_base64}"},
#         "Receipt image generated successfully"
#     )

# @app.route("/api/receipts", methods=["POST"])
# @login_required
# def create_receipt():
#     data = get_json()
#     payment_id = data.get("payment_id")

#     if not payment_id:
#         return error("Payment ID is required")

#     # Fetch full payment details to draw the receipt
#     payment = execute_query(
#         """
#         SELECT p.id, p.payment_number, p.amount, p.payment_date,
#                s.full_name, s.student_code, s.mobile,
#                pm.method_name
#         FROM payments p
#         INNER JOIN students s ON s.id = p.student_id
#         INNER JOIN payment_methods pm ON pm.id = p.payment_method_id
#         WHERE p.id = %s AND p.status = 'SUCCESS'
#         """,
#         (payment_id,), fetchone=True
#     )

#     if not payment:
#         return error("Valid payment not found", 404)

#     # Check if receipt already exists
#     existing = execute_query(
#         "SELECT id, receipt_number, receipt_file_url FROM receipts WHERE payment_id = %s",
#         (payment_id,), fetchone=True
#     )
#     if existing:
#         return success(existing, "Receipt already exists")

#     receipt_number = generate_number("REC", "receipts", "receipt_number")
    
#     # 👉 GENERATE IMAGE
#     os.makedirs('static/receipts', exist_ok=True)
#     filename = f"{receipt_number}.png"
#     filepath = os.path.join('static/receipts', filename)

#     # Create a white canvas
#     img = Image.new('RGB', (600, 800), color='white')
#     draw = ImageDraw.Draw(img)
    
#     # Try to load a default font, fallback to default if not found
#     try:
#         font_large = ImageFont.truetype("arial.ttf", 40)
#         font_med = ImageFont.truetype("arial.ttf", 24)
#         font_small = ImageFont.truetype("arial.ttf", 18)
#     except IOError:
#         font_large = font_med = font_small = ImageFont.load_default()

#     # Draw Header
#     draw.text((50, 50), "GYAN GANGA LIBRARY", fill="black", font=font_large)
#     draw.text((50, 100), "Jaisalmer Road / NH-11, Bikaner", fill="gray", font=font_small)
#     draw.text((50, 125), "Timing: 08:00 AM – 10:00 PM", fill="gray", font=font_small)
    
#     # Divider
#     draw.line((50, 160, 550, 160), fill="black", width=2)

#     # Draw Receipt Info
#     draw.text((50, 190), f"Receipt No: {receipt_number}", fill="black", font=font_med)
#     draw.text((50, 230), f"Date: {str(payment['payment_date'])[:10]}", fill="black", font=font_med)
#     draw.text((50, 270), f"Payment No: {payment['payment_number']}", fill="black", font=font_med)

#     # Divider
#     draw.line((50, 310, 550, 310), fill="gray", width=1)

#     # Draw Student Info
#     draw.text((50, 340), "STUDENT DETAILS", fill="black", font=font_med)
#     draw.text((50, 380), f"Name: {payment['full_name']}", fill="black", font=font_small)
#     draw.text((50, 410), f"Code: {payment['student_code']}", fill="black", font=font_small)
#     draw.text((50, 440), f"Mobile: {payment['mobile']}", fill="black", font=font_small)

#     # Divider
#     draw.line((50, 480, 550, 480), fill="gray", width=1)

#     # Draw Payment Info
#     draw.text((50, 510), "PAYMENT DETAILS", fill="black", font=font_med)
#     draw.text((50, 550), f"Method: {payment['method_name']}", fill="black", font=font_small)
    
#     # Draw Amount Big
#     draw.text((50, 600), "AMOUNT PAID", fill="black", font=font_med)
#     draw.text((50, 640), f"Rs. {payment['amount']}", fill="green", font=font_large)

#     # Footer
#     draw.text((50, 720), "Thank you for choosing Gyan Ganga Library.", fill="gray", font=font_small)

#     # Add QR Code (Optional, scans to student code)
#     qr = qrcode.QRCode(box_size=4, border=1)
#     qr.add_data(f"Student: {payment['student_code']} | Paid: {payment['amount']}")
#     qr.make(fit=True)
#     qr_img = qr.make_image(fill_color="black", back_color="white").resize((100, 100))
    
#     # Paste QR code on bottom right
#     img.paste(qr_img, (450, 650))

#     # Save the image
#     img.save(filepath)

#     # Save to DB
#     receipt_file_url = f"/receipts/{filename}"
#     receipt_id = execute_insert(
#         """
#         INSERT INTO receipts (receipt_number, payment_id, receipt_file_url, status)
#         VALUES (%s,%s,%s,'GENERATED')
#         """,
#         (receipt_number, payment_id, receipt_file_url)
#     )

#     return success(
#         {
#             "receipt_id": receipt_id,
#             "receipt_number": receipt_number,
#             "receipt_file_url": receipt_file_url
#         },
#         "Receipt generated as image"
#     )


# ============================================================
# EXPENSES
# ============================================================

@app.route("/api/expenses", methods=["GET"])
@login_required
def get_expenses():

    category = request.args.get(
        "category"
    )

    query = """
        SELECT
            e.id,
            e.expense_number,

            e.category,
            e.title,
            e.description,

            e.amount,
            e.expense_date,

            e.payment_method_id,
            pm.method_name,

            e.reference_number,
            e.receipt_file_url,

            e.status,

            e.created_by,
            u.name AS created_by_name,

            e.created_at,
            e.updated_at

        FROM expenses e

        LEFT JOIN payment_methods pm
            ON pm.id = e.payment_method_id

        LEFT JOIN users u
            ON u.id = e.created_by

        WHERE 1=1
    """

    params = []

    if category:

        query += """
            AND e.category = %s
        """

        params.append(category)

    query += """
        ORDER BY e.id DESC
    """

    expenses = execute_query(
        query,
        tuple(params),
        fetchall=True
    )

    return success(
        expenses,
        "Expenses fetched",
        len(expenses)
    )


@app.route("/api/expenses", methods=["POST"])
@login_required
def create_expense():

    data = get_json()

    category = str(
        data.get("category", "")
    ).strip()

    title = str(
        data.get("title", "")
    ).strip()

    amount = data.get("amount")

    expense_date = data.get(
        "expense_date"
    )

    if (
        not category
        or not title
        or amount is None
        or not expense_date
    ):

        return error(
            "Category, title, amount and expense date are required"
        )

    try:

        amount = Decimal(
            str(amount)
        )

    except:

        return error(
            "Invalid expense amount"
        )

    if amount <= 0:

        return error(
            "Expense amount must be greater than zero"
        )

    expense_number = generate_number(
        "EXP",
        "expenses",
        "expense_number"
    )

    expense_id = execute_insert(
        """
        INSERT INTO expenses
        (
            expense_number,
            category,
            title,
            description,
            amount,
            expense_date,
            payment_method_id,
            reference_number,
            receipt_file_url,
            status,
            created_by
        )
        VALUES
        (
            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
        )
        """,
        (
            expense_number,
            category,
            title,
            data.get("description"),
            amount,
            expense_date,
            data.get("payment_method_id"),
            data.get("reference_number"),
            data.get("receipt_file_url"),
            data.get("status", "PAID"),
            current_user_id()
        )
    )

    log_activity(
        current_user_id(),
        "CREATE",
        "EXPENSE",
        expense_id,
        f"Created expense {expense_number}"
    )

    return success(
        {
            "expense_id": expense_id,
            "expense_number": expense_number
        },
        "Expense created"
    )


@app.route("/api/expenses/<int:expense_id>", methods=["PUT"])
@login_required
def update_expense(expense_id):

    data = get_json()

    existing = execute_query(
        """
        SELECT id
        FROM expenses
        WHERE id = %s
        """,
        (expense_id,),
        fetchone=True
    )

    if not existing:

        return error(
            "Expense not found",
            404
        )

    allowed = [
        "category",
        "title",
        "description",
        "amount",
        "expense_date",
        "payment_method_id",
        "reference_number",
        "receipt_file_url",
        "status"
    ]

    fields = []
    values = []

    for field in allowed:

        if field in data:

            fields.append(
                f"{field} = %s"
            )

            values.append(
                data[field]
            )

    if not fields:

        return error(
            "No fields to update"
        )

    values.append(expense_id)

    execute_query(
        f"""
        UPDATE expenses
        SET {', '.join(fields)}
        WHERE id = %s
        """,
        tuple(values),
        commit=True
    )

    log_activity(
        current_user_id(),
        "UPDATE",
        "EXPENSE",
        expense_id,
        "Updated expense"
    )

    return success(
        None,
        "Expense updated"
    )


# ============================================================
# STUDENT DEVICES
# ============================================================

@app.route("/api/student-devices", methods=["GET"])
@login_required
def get_student_devices():

    student_id = request.args.get(
        "student_id"
    )

    query = """
        SELECT
            sd.id,
            sd.student_id,

            s.student_code,
            s.full_name,

            sd.device_id,
            sd.device_name,
            sd.device_model,
            sd.manufacturer,

            sd.operating_system,
            sd.os_version,
            sd.app_version,

            sd.ip_address,

            sd.last_login_at,
            sd.last_active_at,

            sd.status,

            sd.created_at,
            sd.updated_at

        FROM student_devices sd

        INNER JOIN students s
            ON s.id = sd.student_id

        WHERE 1=1
    """

    params = []

    if student_id:

        query += """
            AND sd.student_id = %s
        """

        params.append(student_id)

    query += """
        ORDER BY sd.id DESC
    """

    devices = execute_query(
        query,
        tuple(params),
        fetchall=True
    )

    return success(
        devices,
        "Devices fetched",
        len(devices)
    )


@app.route("/api/student-devices", methods=["POST"])
@login_required
def register_student_device():

    data = get_json()

    student_id = data.get(
        "student_id"
    )

    device_id = str(
        data.get("device_id", "")
    ).strip()

    if not student_id or not device_id:

        return error(
            "Student ID and device ID are required"
        )

    existing = execute_query(
        """
        SELECT id
        FROM student_devices
        WHERE student_id = %s
        AND device_id = %s
        """,
        (
            student_id,
            device_id
        ),
        fetchone=True
    )

    if existing:

        execute_query(
            """
            UPDATE student_devices
            SET
                device_token = %s,
                device_name = %s,
                device_model = %s,
                manufacturer = %s,
                operating_system = %s,
                os_version = %s,
                app_version = %s,
                ip_address = %s,
                last_active_at = NOW(),
                status = 'ACTIVE'
            WHERE id = %s
            """,
            (
                data.get("device_token"),
                data.get("device_name"),
                data.get("device_model"),
                data.get("manufacturer"),
                data.get("operating_system"),
                data.get("os_version"),
                data.get("app_version"),
                request.remote_addr,
                existing["id"]
            ),
            commit=True
        )

        return success(
            {
                "id": existing["id"]
            },
            "Device updated"
        )

    device_id_db = execute_insert(
        """
        INSERT INTO student_devices
        (
            student_id,
            device_id,
            device_token,
            device_name,
            device_model,
            manufacturer,
            operating_system,
            os_version,
            app_version,
            ip_address,
            last_active_at,
            status
        )
        VALUES
        (
            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),'ACTIVE'
        )
        """,
        (
            student_id,
            device_id,
            data.get("device_token"),
            data.get("device_name"),
            data.get("device_model"),
            data.get("manufacturer"),
            data.get("operating_system"),
            data.get("os_version"),
            data.get("app_version"),
            request.remote_addr
        )
    )

    return success(
        {
            "id": device_id_db
        },
        "Device registered"
    )


# ============================================================
# DASHBOARD
# ============================================================

# @app.route("/api/dashboard", methods=["GET"])
# @login_required
# def dashboard():

#     students = execute_query(
#         """
#         SELECT COUNT(*) AS total
#         FROM students
#         WHERE status = 'ACTIVE'
#         """,
#         fetchone=True
#     )

#     total_seats = execute_query(
#         """
#         SELECT COUNT(*) AS total
#         FROM seats
#         """,
#         fetchone=True
#     )

#     available_seats = execute_query(
#         """
#         SELECT COUNT(*) AS total
#         FROM seats
#         WHERE status = 'AVAILABLE'
#         """,
#         fetchone=True
#     )

#     occupied_seats = execute_query(
#         """
#         SELECT COUNT(*) AS total
#         FROM seats
#         WHERE status = 'OCCUPIED'
#         """,
#         fetchone=True
#     )

#     active_memberships = execute_query(
#         """
#         SELECT COUNT(*) AS total
#         FROM student_memberships
#         WHERE status = 'ACTIVE'
#         """,
#         fetchone=True
#     )

#     total_due = execute_query(
#         """
#         SELECT
#             COALESCE(SUM(due_amount), 0) AS total
#         FROM student_memberships
#         WHERE status = 'ACTIVE'
#         """,
#         fetchone=True
#     )

#     today_collection = execute_query(
#         """
#         SELECT
#             COALESCE(SUM(amount), 0) AS total
#         FROM payments
#         WHERE status = 'SUCCESS'
#         AND DATE(payment_date) = CURDATE()
#         """,
#         fetchone=True
#     )

#     month_collection = execute_query(
#         """
#         SELECT
#             COALESCE(SUM(amount), 0) AS total
#         FROM payments
#         WHERE status = 'SUCCESS'
#         AND YEAR(payment_date) = YEAR(CURDATE())
#         AND MONTH(payment_date) = MONTH(CURDATE())
#         """,
#         fetchone=True
#     )

#     month_expense = execute_query(
#         """
#         SELECT
#             COALESCE(SUM(amount), 0) AS total
#         FROM expenses
#         WHERE status = 'PAID'
#         AND YEAR(expense_date) = YEAR(CURDATE())
#         AND MONTH(expense_date) = MONTH(CURDATE())
#         """,
#         fetchone=True
#     )

#     return success(
#         {
#             "students": students["total"],
#             "seats": {
#                 "total": total_seats["total"],
#                 "available": available_seats["total"],
#                 "occupied": occupied_seats["total"]
#             },
#             "active_memberships":
#                 active_memberships["total"],
#             "total_due":
#                 total_due["total"],
#             "today_collection":
#                 today_collection["total"],
#             "month_collection":
#                 month_collection["total"],
#             "month_expense":
#                 month_expense["total"],
#             "month_profit":
#                 Decimal(
#                     str(month_collection["total"])
#                 )
#                 -
#                 Decimal(
#                     str(month_expense["total"])
#                 )
#         },
#         "Dashboard data"
#     )



@app.route("/api/dashboard", methods=["GET"])
@login_required
def dashboard():
    students = execute_query(
        "SELECT COUNT(*) AS total FROM students WHERE status = 'ACTIVE'",
        fetchone=True
    )

    total_seats = execute_query(
        "SELECT COUNT(*) AS total FROM seats",
        fetchone=True
    )

    # 👉 NEW: Count seats with NO active memberships today as "Available"
    available_seats = execute_query(
        """
        SELECT COUNT(*) AS total
        FROM seats s
        WHERE s.status NOT IN ('MAINTENANCE', 'BLOCKED')
          AND NOT EXISTS (
              SELECT 1 FROM student_memberships m
              WHERE m.seat_id = s.id
                AND m.status = 'ACTIVE'
                AND CURDATE() BETWEEN m.start_date AND m.expiry_date
          )
        """,
        fetchone=True
    )

    # 👉 NEW: Count seats with ANY active membership today as "Occupied"
    occupied_seats = execute_query(
        """
        SELECT COUNT(*) AS total
        FROM seats s
        WHERE EXISTS (
            SELECT 1 FROM student_memberships m
            WHERE m.seat_id = s.id
              AND m.status = 'ACTIVE'
              AND CURDATE() BETWEEN m.start_date AND m.expiry_date
        )
        """,
        fetchone=True
    )

    active_memberships = execute_query(
        "SELECT COUNT(*) AS total FROM student_memberships WHERE status = 'ACTIVE'",
        fetchone=True
    )

    total_due = execute_query(
        """
        SELECT COALESCE(SUM(due_amount), 0) AS total
        FROM student_memberships
        WHERE status = 'ACTIVE'
        """,
        fetchone=True
    )

    today_collection = execute_query(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM payments
        WHERE status = 'SUCCESS'
        AND DATE(payment_date) = CURDATE()
        """,
        fetchone=True
    )

    month_collection = execute_query(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM payments
        WHERE status = 'SUCCESS'
        AND YEAR(payment_date) = YEAR(CURDATE())
        AND MONTH(payment_date) = MONTH(CURDATE())
        """,
        fetchone=True
    )

    month_expense = execute_query(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM expenses
        WHERE status = 'PAID'
        AND YEAR(expense_date) = YEAR(CURDATE())
        AND MONTH(expense_date) = MONTH(CURDATE())
        """,
        fetchone=True
    )

    return success(
        {
            "students": students["total"],
            "seats": {
                "total": total_seats["total"],
                "available": available_seats["total"],
                "occupied": occupied_seats["total"]
            },
            "active_memberships": active_memberships["total"],
            "total_due": total_due["total"],
            "today_collection": today_collection["total"],
            "month_collection": month_collection["total"],
            "month_expense": month_expense["total"],
            "month_profit": Decimal(str(month_collection["total"])) - Decimal(str(month_expense["total"]))
        },
        "Dashboard data"
    )
    
# ============================================================
# REPORTS
# ============================================================

# @app.route("/api/reports/monthly", methods=["GET"])
# @login_required
# def monthly_report():

#     month = request.args.get(
#         "month"
#     )

#     if not month:

#         month = datetime.now().strftime(
#             "%Y-%m"
#         )

#     try:

#         year, month_number = month.split("-")

#         year = int(year)
#         month_number = int(month_number)

#     except:

#         return error(
#             "Month must be YYYY-MM"
#         )

#     collection = execute_query(
#         """
#         SELECT
#             COALESCE(SUM(amount),0) AS total
#         FROM payments
#         WHERE status = 'SUCCESS'
#         AND YEAR(payment_date) = %s
#         AND MONTH(payment_date) = %s
#         """,
#         (
#             year,
#             month_number
#         ),
#         fetchone=True
#     )

#     expenses = execute_query(
#         """
#         SELECT
#             COALESCE(SUM(amount),0) AS total
#         FROM expenses
#         WHERE status = 'PAID'
#         AND YEAR(expense_date) = %s
#         AND MONTH(expense_date) = %s
#         """,
#         (
#             year,
#             month_number
#         ),
#         fetchone=True
#     )

#     new_students = execute_query(
#         """
#         SELECT COUNT(*) AS total
#         FROM students
#         WHERE YEAR(created_at) = %s
#         AND MONTH(created_at) = %s
#         """,
#         (
#             year,
#             month_number
#         ),
#         fetchone=True
#     )

#     new_memberships = execute_query(
#         """
#         SELECT COUNT(*) AS total
#         FROM student_memberships
#         WHERE YEAR(created_at) = %s
#         AND MONTH(created_at) = %s
#         AND status != 'CANCELLED'
#         """,
#         (
#             year,
#             month_number
#         ),
#         fetchone=True
#     )

#     due = execute_query(
#         """
#         SELECT
#             COALESCE(SUM(due_amount),0) AS total
#         FROM student_memberships
#         WHERE status = 'ACTIVE'
#         """,
#         fetchone=True
#     )

#     collection_amount = Decimal(
#         str(collection["total"])
#     )

#     expense_amount = Decimal(
#         str(expenses["total"])
#     )

#     return success(
#         {
#             "month": month,

#             "total_collection":
#                 collection_amount,

#             "total_expense":
#                 expense_amount,

#             "net_amount":
#                 collection_amount - expense_amount,

#             "new_students":
#                 new_students["total"],

#             "new_memberships":
#                 new_memberships["total"],

#             "current_total_due":
#                 due["total"]
#         },
#         "Monthly report"
#     )


@app.route("/api/reports/monthly", methods=["GET"])
@login_required
def monthly_report():
    month = request.args.get("month")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    # 1. Build the WHERE clause for Payments
    if start_date and end_date:
        pay_where = "status = 'SUCCESS' AND DATE(payment_date) BETWEEN %s AND %s"
        pay_params = (start_date, end_date)
        exp_where = "status = 'PAID' AND expense_date BETWEEN %s AND %s"
        exp_params = (start_date, end_date)
        stu_where = "DATE(created_at) BETWEEN %s AND %s"
        stu_params = (start_date, end_date)
        mem_where = "DATE(created_at) BETWEEN %s AND %s AND status != 'CANCELLED'"
        mem_params = (start_date, end_date)
    else:
        if not month:
            month = datetime.now().strftime("%Y-%m")
        try:
            year, month_number = month.split("-")
        except:
            return error("Month must be YYYY-MM")

        pay_where = "status = 'SUCCESS' AND YEAR(payment_date) = %s AND MONTH(payment_date) = %s"
        pay_params = (year, month_number)
        exp_where = "status = 'PAID' AND YEAR(expense_date) = %s AND MONTH(expense_date) = %s"
        exp_params = (year, month_number)
        stu_where = "YEAR(created_at) = %s AND MONTH(created_at) = %s"
        stu_params = (year, month_number)
        mem_where = "YEAR(created_at) = %s AND MONTH(created_at) = %s AND status != 'CANCELLED'"
        mem_params = (year, month_number)

    collection = execute_query(f"SELECT COALESCE(SUM(amount),0) AS total FROM payments WHERE {pay_where}", pay_params, fetchone=True)
    expenses = execute_query(f"SELECT COALESCE(SUM(amount),0) AS total FROM expenses WHERE {exp_where}", exp_params, fetchone=True)
    new_students = execute_query(f"SELECT COUNT(*) AS total FROM students WHERE {stu_where}", stu_params, fetchone=True)
    new_memberships = execute_query(f"SELECT COUNT(*) AS total FROM student_memberships WHERE {mem_where}", mem_params, fetchone=True)
    due = execute_query("SELECT COALESCE(SUM(due_amount),0) AS total FROM student_memberships WHERE status = 'ACTIVE'", fetchone=True)

    collection_amount = Decimal(str(collection["total"]))
    expense_amount = Decimal(str(expenses["total"]))

    return success({
        "total_collection": collection_amount,
        "total_expense": expense_amount,
        "net_amount": collection_amount - expense_amount,
        "new_students": new_students["total"],
        "new_memberships": new_memberships["total"],
        "current_total_due": due["total"]
    }, "Report fetched")


@app.route("/api/reports/payment-methods", methods=["GET"])
@login_required
def payment_method_report():
    month = request.args.get("month")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    if start_date and end_date:
        where = "AND DATE(p.payment_date) BETWEEN %s AND %s"
        params = (start_date, end_date)
    else:
        if not month:
            month = datetime.now().strftime("%Y-%m")
        try:
            year, month_number = month.split("-")
        except:
            return error("Month must be YYYY-MM")
        where = "AND YEAR(p.payment_date) = %s AND MONTH(p.payment_date) = %s"
        params = (year, month_number)

    data = execute_query(f"""
        SELECT pm.id, pm.method_name, COUNT(p.id) AS transaction_count, COALESCE(SUM(p.amount), 0) AS total_amount
        FROM payment_methods pm
        LEFT JOIN payments p ON p.payment_method_id = pm.id AND p.status = 'SUCCESS' {where}
        WHERE pm.status = 'ACTIVE'
        GROUP BY pm.id, pm.method_name
        ORDER BY total_amount DESC
    """, params, fetchall=True)

    return success(data, "Payment method report", len(data))


@app.route("/api/reports/expenses", methods=["GET"])
@login_required
def expense_report():
    month = request.args.get("month")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    if start_date and end_date:
        where = "AND expense_date BETWEEN %s AND %s"
        params = (start_date, end_date)
    else:
        if not month:
            month = datetime.now().strftime("%Y-%m")
        try:
            year, month_number = month.split("-")
        except:
            return error("Month must be YYYY-MM")
        where = "AND YEAR(expense_date) = %s AND MONTH(expense_date) = %s"
        params = (year, month_number)

    data = execute_query(f"""
        SELECT category, COUNT(*) AS expense_count, COALESCE(SUM(amount), 0) AS total_amount
        FROM expenses
        WHERE status = 'PAID' {where}
        GROUP BY category
        ORDER BY total_amount DESC
    """, params, fetchall=True)

    return data and success(data, "Expense report", len(data))


# @app.route("/api/reports/payment-methods", methods=["GET"])
# @login_required
# def payment_method_report():

#     month = request.args.get(
#         "month"
#     )

#     if not month:

#         month = datetime.now().strftime(
#             "%Y-%m"
#         )

#     try:

#         year, month_number = month.split("-")

#     except:

#         return error(
#             "Month must be YYYY-MM"
#         )

#     data = execute_query(
#         """
#         SELECT
#             pm.id,
#             pm.method_name,

#             COUNT(p.id) AS transaction_count,

#             COALESCE(
#                 SUM(p.amount),
#                 0
#             ) AS total_amount

#         FROM payment_methods pm

#         LEFT JOIN payments p
#             ON p.payment_method_id = pm.id

#             AND p.status = 'SUCCESS'

#             AND YEAR(p.payment_date) = %s

#             AND MONTH(p.payment_date) = %s

#         GROUP BY
#             pm.id,
#             pm.method_name

#         ORDER BY
#             total_amount DESC
#         """,
#         (
#             year,
#             month_number
#         ),
#         fetchall=True
#     )

#     return success(
#         data,
#         "Payment method report",
#         len(data)
#     )


# @app.route("/api/reports/expenses", methods=["GET"])
# @login_required
# def expense_report():

#     month = request.args.get(
#         "month"
#     )

#     if not month:

#         month = datetime.now().strftime(
#             "%Y-%m"
#         )

#     try:

#         year, month_number = month.split("-")

#     except:

#         return error(
#             "Month must be YYYY-MM"
#         )

#     data = execute_query(
#         """
#         SELECT
#             category,

#             COUNT(*) AS expense_count,

#             COALESCE(
#                 SUM(amount),
#                 0
#             ) AS total_amount

#         FROM expenses

#         WHERE status = 'PAID'

#         AND YEAR(expense_date) = %s

#         AND MONTH(expense_date) = %s

#         GROUP BY category

#         ORDER BY total_amount DESC
#         """,
#         (
#             year,
#             month_number
#         ),
#         fetchall=True
#     )

#     return success(
#         data,
#         "Expense report",
#         len(data)
#     )


# ============================================================
# ACTIVITY LOGS
# ============================================================

@app.route("/api/activity-logs", methods=["GET"])
@login_required
def get_activity_logs():

    logs = execute_query(
        """
        SELECT
            al.id,

            al.user_id,

            u.name AS user_name,

            al.action,
            al.module,

            al.reference_id,

            al.description,

            al.ip_address,

            al.created_at

        FROM activity_logs al

        LEFT JOIN users u
            ON u.id = al.user_id

        ORDER BY al.id DESC

        LIMIT 500
        """,
        fetchall=True
    )

    return success(
        logs,
        "Activity logs fetched",
        len(logs)
    )



@app.route("/api/reports/custom-range", methods=["GET"])
@login_required
def custom_range_report():
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    if not start_date or not end_date:
        return error("start_date and end_date are required (YYYY-MM-DD)")

    # 1. Total Collection in range
    collection = execute_query(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM payments
        WHERE status = 'SUCCESS'
          AND DATE(payment_date) BETWEEN %s AND %s
        """,
        (start_date, end_date),
        fetchone=True
    )

    # 2. Total Expenses in range
    expenses = execute_query(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM expenses
        WHERE status = 'PAID'
          AND expense_date BETWEEN %s AND %s
        """,
        (start_date, end_date),
        fetchone=True
    )

    # 3. Breakdown by Payment Method in range
    payment_methods = execute_query(
        """
        SELECT pm.method_name, COUNT(p.id) AS transaction_count, COALESCE(SUM(p.amount), 0) AS total_amount
        FROM payments p
        INNER JOIN payment_methods pm ON pm.id = p.payment_method_id
        WHERE p.status = 'SUCCESS'
          AND DATE(p.payment_date) BETWEEN %s AND %s
        GROUP BY pm.id
        """,
        (start_date, end_date),
        fetchall=True
    )

    # 4. Breakdown by Expense Category in range
    expense_categories = execute_query(
        """
        SELECT category, COUNT(*) AS expense_count, COALESCE(SUM(amount), 0) AS total_amount
        FROM expenses
        WHERE status = 'PAID'
          AND expense_date BETWEEN %s AND %s
        GROUP BY category
        """,
        (start_date, end_date),
        fetchall=True
    )

    col_amount = Decimal(str(collection["total"]))
    exp_amount = Decimal(str(expenses["total"]))

    return success(
        {
            "start_date": start_date,
            "end_date": end_date,
            "total_collection": col_amount,
            "total_expense": exp_amount,
            "net_profit": col_amount - exp_amount,
            "payment_methods": payment_methods,
            "expense_categories": expense_categories
        },
        "Custom range report fetched"
    )



@app.route("/api/reports/due-students", methods=["GET"])
@login_required
def get_due_students():
    dues = execute_query(
        """
        SELECT
            s.id AS student_id,
            s.student_code,
            s.full_name,
            s.mobile,
            m.id AS membership_id,
            fp.plan_name,
            m.start_date,
            m.expiry_date,
            m.final_amount,
            m.paid_amount,
            m.due_amount
        FROM student_memberships m
        INNER JOIN students s ON s.id = m.student_id
        INNER JOIN fee_plans fp ON fp.id = m.fee_plan_id
        WHERE m.status = 'ACTIVE'
          AND m.due_amount > 0
        ORDER BY m.due_amount DESC
        """,
        fetchall=True
    )

    return success(dues, "Due students fetched", len(dues))



# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(404)
def not_found(error_obj):

    return error(
        "API endpoint not found",
        404
    )


@app.errorhandler(405)
def method_not_allowed(error_obj):

    return error(
        "HTTP method not allowed",
        405
    )


@app.errorhandler(500)
def internal_error(error_obj):

    return error(
        "Internal server error",
        500
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )

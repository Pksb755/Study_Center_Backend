from flask import Flask, request, jsonify, session
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
from psycopg2 import Error
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()  # Loads local .env; hosting platforms can set env vars directly.
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

# ============================================================
# APP CONFIG
# ============================================================

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
# SUPABASE POSTGRESQL CONFIG
# ============================================================
# In Supabase: Project Settings -> Database -> Connection string.
# Put the connection URI in DATABASE_URL (prefer the Session Pooler
# for serverless/IPv4-only hosts, or Direct Connection where supported).
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# Optional fallback for split connection settings; prefer DATABASE_URL.
DB_CONFIG = {
    "host": os.getenv("DB_HOST", ""),
    "port": int(os.getenv("DB_PORT", "5432")),
    "user": os.getenv("DB_USER", ""),
    "password": os.getenv("DB_PASSWORD", ""),
    "dbname": os.getenv("DB_NAME", "postgres"),
    "sslmode": os.getenv("DB_SSLMODE", "require"),
}


@app.route('/receipts/<filename>')
def serve_receipt(filename):
    # Ensure the folder exists
    os.makedirs('static/receipts', exist_ok=True)
    return send_from_directory('static/receipts', filename)

# ============================================================
# DATABASE CONNECTION (PostgreSQL / Supabase)
# ============================================================

def get_db():
    """Open a PostgreSQL connection to Supabase."""
    try:
        if DATABASE_URL:
            # Supabase connection URIs should include their supplied host,
            # username, password, database, and SSL parameters.
            return psycopg2.connect(DATABASE_URL, sslmode=os.getenv("DB_SSLMODE", "require"))

        if DB_CONFIG["host"] and DB_CONFIG["user"]:
            return psycopg2.connect(**DB_CONFIG)

        print("Database connection error: set DATABASE_URL to your Supabase PostgreSQL connection string")
    except Error as e:
        print("Database connection error:", e)

    return None


# ============================================================
# VALIDATION HELPERS
# ============================================================

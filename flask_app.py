from flask import Flask, render_template, request, jsonify, send_file, g
from functools import wraps
from flask_login import current_user, login_required, login_user, LoginManager, logout_user, UserMixin
import requests
from datetime import datetime
from model import db, Client, RequestLog, login_manager
import time

app = Flask(__name__)


app.config.from_object('config.Config')
db.init_app(app)


with app.app_context():
    db.create_all()

login_manager.init_app(app)


@app.route('/')
@login_required
def index():
    return render_template("index.html")

@app.route('/api-docs')
def api_docs():
    return render_template("doc.html")

# 🌐 CLIENT IP alma (proxy varsa doğru IP)
def get_client_ip():
    if request.headers.get("X-Forwarded-For"):
        return request.headers.get("X-Forwarded-For").split(",")[0]
    return request.remote_addr


# 🔒 TOKEN AUTH DECORATOR
def client_token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization")

        if not auth_header:
            return jsonify({"error": "token missing"}), 401

        try:
            token = auth_header.split(" ")[1]
        except:
            return jsonify({"error": "invalid token format"}), 401

        client = Client.query.filter_by(token=token, is_active=True).first()

        if not client or not client.is_token_valid():
            return jsonify({"error": "invalid or expired token"}), 401

        # 👉 global context (çok önemli)
        g.client = client

        return f(*args, **kwargs)

    return decorated


# sadece bu endpoint'leri logla
LOGGED_ENDPOINTS = [
    "/login",
    "/api/login",
    "/label/rate",
    "/label/create",
    "/label/cancel",
    "/client/create",
    "/client/refresh-token"
]

@app.after_request
def log_request(response):
    try:
        if request.path not in LOGGED_ENDPOINTS:
            return response  # diğerlerini loglama

        # sadece client varsa logla (auth geçmişse)
        client_id = getattr(g, "client", None)
        client_id = client_id.id if client_id else None

        log = RequestLog(
            client_id=client_id,
            ip_address=get_client_ip(),
            endpoint=request.path,
            method=request.method,
            user_agent=request.headers.get("User-Agent")
        )

        db.session.add(log)
        db.session.commit()

    except Exception as e:
        db.session.rollback()

    return response

# 🔐 CLIENT LOGIN (TOKEN ALMA)
@app.route("/api/login", methods=["POST"])
def client_login():
    data = request.json

    username = data.get("username")
    password = data.get("password")

    client = Client.query.filter_by(username=username, is_active=True).first()

    if not client or not client.check_password(password):
        return jsonify({"error": "invalid credentials"}), 401

    if client.token and client.token_expiry and time.time() < client.token_expiry:
        expires_in = int(client.token_expiry - time.time())
        return jsonify({
            "token": client.token,
            "expires_in": expires_in,
            "message": "Please do not log in repeatedly. Access will be restricted soon."
        })

    client.generate_token()
    db.session.commit()

    return jsonify({
        "token": client.token,
        "expires_in": 3600,
        "message": "Login successfully."
    })


# ✅ TEST ENDPOINT
@app.route("/api/ping", methods=["GET"])
@client_token_required
def ping():
    return jsonify({
        "message": "ok",
        "client": g.client.username
    })


@app.template_filter('datetime_from_timestamp')
def datetime_from_timestamp_filter(timestamp):
    return datetime.fromtimestamp(int(timestamp)).strftime('%Y-%m-%d %H:%M:%S')


from user_controller import *

from client_controller import *

from label_controller import *

from rate_controller import *

from dhl_label_controller import *

from dhl_int_label_controller import *

from rate_query_controller import *

from flask_app import app

from flask_login import LoginManager, login_required, current_user, login_user, logout_user, UserMixin
from flask import Flask, Blueprint, render_template, redirect, url_for, request, flash, session, send_file, abort, jsonify, make_response
from model import *
from decorators import role_required

from dateutil.relativedelta import relativedelta
import requests
import os

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(user_id)

login_manager.login_view = 'login'

@login_manager.unauthorized_handler     # In unauthorized_handler we have a callback URL
def unauthorized_callback():            # In call back url we can specify where we want to
       return redirect(url_for('login'))


@app.route('/login', methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))  # login olmuşsa ana sayfa

    if request.method == "POST":
        mail = request.form.get("email")
        password = request.form.get("password")

        if not mail or not password:
            flash("E-posta ve şifre zorunludur", "info")
            return redirect(url_for("login"))

        user = User.query.filter_by(mail=mail).first()

        if not user:
            flash("Kullanıcı bulunamadı", "danger")
            return redirect(url_for("login"))

        # 🔐 Hesap aktif mi?
        if not user.is_active:
            flash("Hesabınız pasif durumdadır", "danger")
            return redirect(url_for("login"))

        # 🔑 Şifre kontrolü
        if not user.check_password(password):
            flash("Hatalı şifre veya kullanıcı adı", "danger")
            return redirect(url_for("login"))

        # ✅ LOGIN BAŞARILI
        login_user(user)

        # 📌 Login bilgilerini kaydet (sunucu saati)
        user.last_login_at = datetime.now()
        user.last_login_ip = request.remote_addr
        db.session.commit()

        flash("Başarıyla giriş yaptınız", "success")
        return redirect(url_for("index"))

    # GET REQUEST
    return render_template("login.html", result={"message":""})

@app.route("/logout")
@login_required
def logout():
    logout_user()
    session.clear()
    flash("Çıkış yapıldı", "success")
    return redirect(url_for("login"))


@app.route("/developer/create-user", methods=["POST"])
def developer_create_user():

    data = request.get_json()

    mail = data.get("mail")
    password = data.get("password")
    role = data.get("user_role", "user")
    dev_key = data.get("dev_key")

    DEV_KEY = os.getenv("DEV_KEY")

    if not mail or not password:
        return jsonify({"error": "Mail ve şifre zorunludur"}), 400

    if len(password) < 6:
        return jsonify({"error": "Password too weak"}), 400

    if role not in ["user", "admin"]:
        return jsonify({"error": "Invalid role"}), 400

    if not dev_key or dev_key != DEV_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    if User.query.filter_by(mail=mail).first():
        return jsonify({"error": "Bu mail zaten kayıtlı"}), 409

    user = User(
        mail=mail,
        password_hash=generate_password_hash(password),
        user_role=role,
        is_active=True
    )

    db.session.add(user)
    db.session.commit()

    return jsonify({
        "message": "Kullanıcı oluşturuldu",
        "user_id": user.id,
        "role": user.user_role
    }), 201

@app.route("/admin/create-user", methods=["POST"])
@login_required
@role_required("admin")
def admin_create_user():

    data = request.get_json()

    mail = data.get("mail")
    password = data.get("password")

    if not mail or not password:
        return jsonify({"error": "Mail ve şifre zorunludur"}), 400

    if User.query.filter_by(mail=mail).first():
        return jsonify({"error": "Bu mail zaten kayıtlı"}), 409

    user = User(
        mail=mail,
        password_hash=generate_password_hash(password),
        user_role="user",  # 🔒 ZORUNLU
        admin_id=current_user.id,
        is_active=True
    )

    db.session.add(user)
    db.session.commit()

    return jsonify({
        "message": "User oluşturuldu",
        "user_id": user.id
    }), 201

@app.route("/users", methods=["GET"])
@login_required
@role_required("admin", "developer")
def get_users():

    users = User.query.all()

    return jsonify([
        {
            "id": u.id,
            "mail": u.mail,
            "role": u.user_role,
            "is_active": u.is_active,
            "last_login_at": u.last_login_at
        }
        for u in users
    ])

@app.route("/users/update", methods=["POST"])
@login_required
@role_required("admin", "developer")
def update_user():
    data = request.get_json()

    mail = data.get("mail")
    if not mail:
        return jsonify({"error": "mail alanı zorunludur"}), 400

    user = User.query.filter_by(mail=mail).first()
    if not user:
        return jsonify({"error": "Kullanıcı bulunamadı"}), 404

    # 🔐 Admin sadece normal user güncelleyebilir
    if current_user.user_role == "admin":
        if user.user_role != "user":
            return jsonify({"error": "Bu kullanıcıyı güncelleme yetkiniz yok"}), 403

    # ✅ Güncellenebilir alanlar
    if "is_active" in data:
        user.is_active = bool(data["is_active"])

    if "user_notes" in data:
        user.user_notes = data["user_notes"]

    if "address" in data:
        user.address = data["address"]

    if "phone_number" in data:
        user.phone_number = data["phone_number"]

    # 🔐 Rol sadece developer tarafından değiştirilebilir
    if current_user.user_role == "developer" and "user_role" in data:
        user.user_role = data["user_role"]

    user.updated_at = datetime.now()
    db.session.commit()

    return jsonify({
        "message": "Kullanıcı güncellendi",
        "mail": user.mail
    }), 200






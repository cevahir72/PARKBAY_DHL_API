from flask_app import app
from flask import request, jsonify, g, render_template, url_for, flash, redirect
from flask_login import login_required, current_user
from model import db, Client
from decorators import role_required
import time



# 🆕 CLIENT OLUŞTUR
@app.route('/client/create', methods=["POST"])
@login_required
@role_required("admin")
def create_client():
    data = request.get_json()

    username = data.get("username")
    password = data.get("password")

    if not username or not password:
        return jsonify({"error": "username ve password zorunludur"}), 400

    if Client.query.filter_by(username=username).first():
        return jsonify({"error": "Bu kullanıcı zaten mevcut"}), 409

    client = Client(username=username, is_active=True)
    client.set_password(password)
    client.generate_token()  # 1 saat token

    db.session.add(client)
    db.session.commit()

    return jsonify({
        "message": "Client oluşturuldu",
        "client_id": client.id,
        "token": client.token,
        "expires_in": 3600
    }), 201



# 🔄 TOKEN YENİLE
@app.route('/client/refresh-token/<int:client_id>', methods=["POST"])
@login_required
@role_required("admin")
def refresh_client_token(client_id):
    client = Client.query.filter_by(id=client_id, is_active=True).first()
    if not client:
        return jsonify({"error": "Client bulunamadı"}), 404

    client.generate_token()
    db.session.commit()

    return jsonify({
        "message": "Token yenilendi",
        "token": client.token,
        "expires_in": 3600
    })


# 🚫 CLIENT AKTİF/PASİF
@app.route('/client/toggle/<int:client_id>', methods=["POST"])
@login_required
@role_required("admin")
def toggle_client(client_id):
    client = Client.query.get(client_id)
    if not client:
        return jsonify({"error": "Client bulunamadı"}), 404

    client.is_active = not client.is_active
    db.session.commit()

    return jsonify({
        "message": "Client durumu değiştirildi",
        "is_active": client.is_active
    })


@app.route("/admin/clients", methods=["GET"])
@login_required
@role_required("admin")
def admin_clients():
    clients = Client.query.all()
    return render_template("clients.html", clients=clients)


@app.route("/admin/client/add", methods=["POST"])
@login_required
@role_required("admin")
def admin_add_client():
    username = request.form.get("username")
    password = request.form.get("password")
    reference_code = request.form.get("reference_code")
    com_fee = float(request.form.get("com_fee", 0))
    rate_limit = int(request.form.get("rate_limit", 5))

    client = Client(username=username, is_active=True, com_fee=com_fee, rate_limit=rate_limit, reference_code=reference_code)
    client.set_password(password)
    client.generate_token()
    db.session.add(client)
    db.session.commit()

    flash(f"{username} başarıyla oluşturuldu", "success")
    return redirect(url_for("admin_clients"))


@app.route("/admin/client/edit/<int:client_id>", methods=["POST"])
@login_required
@role_required("admin")
def admin_edit_client(client_id):
    client = Client.query.get_or_404(client_id)
    username = request.form.get("username")
    password = request.form.get("password")
    com_fee = float(request.form.get("com_fee", 0))
    rate_limit = int(request.form.get("rate_limit", 5))
    is_active = bool(int(request.form.get("is_active", 1)))

    client.username = username
    client.com_fee = com_fee
    client.rate_limit = rate_limit
    client.is_active = is_active
    if password:
        client.set_password(password)

    db.session.commit()
    flash(f"{username} başarıyla güncellendi", "success")
    return redirect(url_for("admin_clients"))

from flask_app import app
from flask import request, jsonify, g, render_template, url_for, flash, redirect
from flask_login import login_required, current_user
from model import db, Carrier, Client, Commission
from decorators import role_required
from decimal import Decimal, InvalidOperation
from sqlalchemy.exc import IntegrityError
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
    carriers = Carrier.query.order_by(Carrier.name).all()
    return render_template("clients.html", clients=clients, carriers=carriers)


def parse_commission_amount(field_name):
    raw_value = request.form.get(field_name, "").strip()
    try:
        amount = Decimal(raw_value)
        if (
            not amount.is_finite()
            or amount < 0
            or amount > Decimal("99999999.99")
        ):
            raise InvalidOperation
        rounded_amount = amount.quantize(Decimal("0.01"))
        if rounded_amount != amount:
            raise InvalidOperation
        return rounded_amount
    except (InvalidOperation, ValueError):
        raise ValueError(
            f"{field_name} must be between 0 and 99999999.99 "
            "with up to 2 decimal places"
        )


def commission_form_values():
    carrier_id = request.form.get("carrier_id", "").strip()
    if not carrier_id.isdigit():
        raise ValueError("A valid carrier is required")

    carrier = db.session.get(Carrier, int(carrier_id))
    if carrier is None:
        raise ValueError("Selected carrier does not exist")

    return (
        carrier,
        parse_commission_amount("shipping_rate"),
        parse_commission_amount("libre_rate"),
    )


@app.route("/admin/client/<int:client_id>/commissions/add", methods=["POST"])
@login_required
@role_required("admin")
def admin_add_commission(client_id):
    client = Client.query.get_or_404(client_id)
    try:
        carrier, shipping_rate, libre_rate = commission_form_values()
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("admin_clients"))

    commission = Commission(
        client_id=client.id,
        carrier_id=carrier.id,
        shipping_rate=shipping_rate,
        libre_rate=libre_rate,
        active=True,
    )
    db.session.add(commission)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("This client already has a commission for the selected carrier.", "danger")
        return redirect(url_for("admin_clients"))

    flash(f"{client.username} için {carrier.name} komisyonu oluşturuldu.", "success")
    return redirect(url_for("admin_clients"))


@app.route(
    "/admin/client/<int:client_id>/commissions/<int:commission_id>/edit",
    methods=["POST"],
)
@login_required
@role_required("admin")
def admin_edit_commission(client_id, commission_id):
    client = Client.query.get_or_404(client_id)
    commission = Commission.query.filter_by(
        id=commission_id,
        client_id=client.id,
    ).first_or_404()

    try:
        carrier, shipping_rate, libre_rate = commission_form_values()
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("admin_clients"))

    commission.carrier_id = carrier.id
    commission.shipping_rate = shipping_rate
    commission.libre_rate = libre_rate
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("This client already has a commission for the selected carrier.", "danger")
        return redirect(url_for("admin_clients"))

    flash(f"{client.username} için {carrier.name} komisyonu güncellendi.", "success")
    return redirect(url_for("admin_clients"))


@app.route(
    "/admin/client/<int:client_id>/commissions/<int:commission_id>/active",
    methods=["POST"],
)
@login_required
@role_required("admin")
def admin_toggle_commission(client_id, commission_id):
    client = Client.query.get_or_404(client_id)
    commission = Commission.query.filter_by(
        id=commission_id,
        client_id=client.id,
    ).first_or_404()

    active_values = request.form.getlist("active")
    if active_values not in ([], ["true"]):
        flash("Invalid commission status.", "danger")
        return redirect(url_for("admin_clients"))

    commission.active = active_values == ["true"]
    db.session.commit()
    state = "aktif" if commission.active else "pasif"
    flash(f"{client.username} için {commission.carrier.name} komisyonu {state}.", "success")
    return redirect(url_for("admin_clients"))


@app.route(
    "/admin/client/<int:client_id>/commissions/<int:commission_id>/delete",
    methods=["POST"],
)
@login_required
@role_required("admin")
def admin_delete_commission(client_id, commission_id):
    client = Client.query.get_or_404(client_id)
    commission = Commission.query.filter_by(
        id=commission_id,
        client_id=client.id,
    ).first_or_404()
    db.session.delete(commission)
    db.session.commit()
    flash(f"{client.username} için {commission.carrier.name} komisyonu silindi.", "success")
    return redirect(url_for("admin_clients"))


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

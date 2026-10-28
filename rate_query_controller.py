from flask_app import app, db
from flask import request, jsonify, render_template
from flask_login import login_required
from model import Client
from dhl_label_controller import dhl_rate_label
from dhl_int_label_controller import dhl_int_rate_label

try:
    from data_helper import PICKUP_DICT, DISTRIBUTION_CENTERS
except ImportError:
    # Eski data_helper sürümleriyle uyumluluk
    PICKUP_DICT = {
        "75081-1955": "5410014",
        "75243-5485": "5410429",
        "77407-9012": "5410448",
        "75006-2527": "5411053",
    }
    DISTRIBUTION_CENTERS = {
        "75081-1955": "USDFW1",
        "75243-5485": "USDFW1",
        "77407-9012": "USIAH1",
        "75006-2527": "USDFW1",
    }


RATE_HANDLERS = {
    "domestic": dhl_rate_label,
    "international": dhl_int_rate_label,
}


@app.route("/rate-query")
@login_required
def rate_query():
    clients = Client.query.filter_by(is_active=True).order_by(Client.username).all()
    warehouses = [
        {
            "zip": zip_code,
            "pickup": pickup,
            "distribution_center": DISTRIBUTION_CENTERS.get(zip_code, "-"),
        }
        for zip_code, pickup in PICKUP_DICT.items()
    ]

    return render_template(
        "rate_query.html",
        clients=clients,
        warehouses=warehouses,
    )


@app.route("/rate-query/request", methods=["POST"])
@login_required
def rate_query_request():
    """
    Admin panelinden DHL rate endpoint'lerine sunucu tarafı proxy.

    Token korumalı /dhl/label/rate ve /dhl/int/label/rate endpoint'leri,
    seçilen client'ın token'ı ile çağrılır; token tarayıcıya gönderilmez.
    """
    data = request.get_json(silent=True) or {}

    mode = str(data.get("mode") or "").strip().lower()
    payload = data.get("payload")
    client_id = data.get("client_id")

    if mode not in RATE_HANDLERS:
        return jsonify({"error": "mode must be domestic or international"}), 400

    if not isinstance(payload, dict):
        return jsonify({"error": "payload is required"}), 400

    try:
        client_id = int(client_id)
    except (TypeError, ValueError):
        return jsonify({"error": "client_id is required"}), 400

    client = Client.query.filter_by(id=client_id, is_active=True).first()
    if not client:
        return jsonify({"error": "Client not found"}), 404

    # Süresi geçmiş/eksik token'ı yenile (client'ın kendi token'ı gibi 1 saat geçerli).
    if not client.token or not client.is_token_valid():
        client.generate_token()
        db.session.commit()

    handler = RATE_HANDLERS[mode]

    try:
        with app.test_request_context(
            json=payload,
            headers={"Authorization": f"Bearer {client.token}"},
        ):
            result = handler()
    except Exception as error:
        app.logger.exception(
            "Rate query proxy failed. mode=%s client_id=%s", mode, client.id
        )
        return jsonify({"error": f"Rate query failed: {error}"}), 500

    if isinstance(result, tuple):
        return result

    return result

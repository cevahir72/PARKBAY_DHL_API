from flask import render_template, request
from flask_login import login_required
from sqlalchemy.orm import joinedload

from flask_app import app
from decorators import role_required
from model import Carrier, Client, ShipmentLog


@app.route("/admin/logs", methods=["GET"])
@login_required
@role_required("admin")
def admin_shipment_logs():
    page = request.args.get("page", default=1, type=int)
    per_page = request.args.get("per_page", default=50, type=int)
    if per_page not in (20, 50, 100):
        per_page = 50

    client_id = request.args.get("client_id", type=int)
    carrier_id = request.args.get("carrier_id", type=int)

    query = ShipmentLog.query.options(
        joinedload(ShipmentLog.client),
        joinedload(ShipmentLog.carrier),
    )
    if client_id is not None:
        query = query.filter(ShipmentLog.client_id == client_id)
    if carrier_id is not None:
        query = query.filter(ShipmentLog.carrier_id == carrier_id)

    pagination = query.order_by(
        ShipmentLog.created_at.desc(),
        ShipmentLog.id.desc(),
    ).paginate(page=page, per_page=per_page, error_out=False)

    return render_template(
        "shipment_logs.html",
        logs=pagination.items,
        pagination=pagination,
        per_page=per_page,
        clients=Client.query.order_by(Client.username).all(),
        carriers=Carrier.query.order_by(Carrier.name).all(),
        selected_client_id=client_id,
        selected_carrier_id=carrier_id,
    )

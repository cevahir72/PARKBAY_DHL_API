from datetime import datetime, timedelta

from flask import abort, render_template, request
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
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    try:
        start_datetime = (
            datetime.strptime(start_date, "%Y-%m-%d")
            if start_date
            else None
        )
        end_datetime = (
            datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            if end_date
            else None
        )
    except ValueError:
        abort(400, description="Dates must use YYYY-MM-DD format.")

    if start_datetime and end_datetime and start_datetime >= end_datetime:
        abort(400, description="Start date must be on or before end date.")

    query = ShipmentLog.query.options(
        joinedload(ShipmentLog.client),
        joinedload(ShipmentLog.carrier),
    )
    if client_id is not None:
        query = query.filter(ShipmentLog.client_id == client_id)
    if carrier_id is not None:
        query = query.filter(ShipmentLog.carrier_id == carrier_id)
    if start_datetime:
        query = query.filter(ShipmentLog.created_at >= start_datetime)
    if end_datetime:
        query = query.filter(ShipmentLog.created_at < end_datetime)

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
        selected_start_date=start_date,
        selected_end_date=end_date,
    )

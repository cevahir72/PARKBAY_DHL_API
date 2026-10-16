from flask_app import app, db, client_token_required
from flask import request, jsonify, g, render_template, send_file
from flask_login import login_required, current_user
from model import ShipmentLog, Label, InternationalLabel, Client
import time
import requests
from amazon_service import get_amazon_token
from data_helper import request_helper
from decimal import Decimal
from calculator import calculate_shipping_cost
from datetime import datetime, timedelta
from decorators import role_required
from sqlalchemy import func


from io import BytesIO

import pandas as pd



# 🔹 RATE LIMIT
def check_rate_limit(client):
    now = int(time.time())

    if client.last_request_time == now:
        client.request_count += 1
    else:
        client.last_request_time = now
        client.request_count = 1

    if client.request_count > client.rate_limit:
        db.session.commit()
        return False

    db.session.commit()
    return True


# 🔹 AMAZON REQUEST
def amazon_request(endpoint, payload, is_test =  False):

    AMAZON_BASE_URL = "https://sellingpartnerapi-na.amazon.com"
    if is_test:
        AMAZON_BASE_URL = "https://sandbox.sellingpartnerapi-na.amazon.com"

    token = get_amazon_token()

    headers = {
        "x-amz-access-token": token,
        "x-amzn-shipping-business-id": "AmazonShipping_US",
        "content-type": "application/json"
    }

    if "cancel" in endpoint:
        response = requests.put(
            f"{AMAZON_BASE_URL}/shipping/v2/shipments/{payload}/cancel",
            headers=headers
        )
    else:
        response = requests.post(
            f"{AMAZON_BASE_URL}/{endpoint}",
            json=payload,
            headers=headers,
            timeout=15
        )

    # 🔁 Token expired → retry
    if response.status_code == 401:
        token = get_amazon_token()
        headers["x-amz-access-token"] = token

        if "cancel" in endpoint:
            response = requests.put(
                f"{AMAZON_BASE_URL}/shipping/v2/shipments/{payload}/cancel",
                headers=headers
            )
        else:
            response = requests.post(
                f"{AMAZON_BASE_URL}/{endpoint}",
                json=payload,
                headers=headers,
                timeout=15
            )

    return response.status_code, response.json()


# 🔹 RATE LABEL
@app.route("/label/rate", methods=["POST"])
@client_token_required
def rate_label():
    client = g.client

    # 🔥 RATE LIMIT
    if not check_rate_limit(client):
        return jsonify({"errors": [
            {
                "code": "RateLimit",
                "details": "Rate limit exceeded",
                "message": "Rate Limit"
            }
        ]}), 429

    data = request.json or {}

    marketPlace = data.get("marketPlace", "walmart")
    if marketPlace.lower() in ["walmart", "targetplus"]:
        return jsonify({"errors": [
            {
                "code": "InvalidInput",
                "details": "The marketPlace input is required and It cannot be Targetplus or Walmart.",
                "message": "Invalid input"
            }
        ]}), 400

    amazon_payload = request_helper(data, client.reference_code, "rate")

    mapped_rate, mapped_message = calculate_shipping_cost(client.id, data["shipFrom"]["postalCode"], data["shipTo"]["postalCode"], data["packages"][0]["weight"]["value"])
    if not mapped_rate:
        return jsonify({"errors": [
            {
                "code": "ZoneWeight",
                "details": mapped_message,
                "message": "Zone Weight"
            }
        ]}), 400

    is_test_label = data.get("isTestLabel", False)
    status_code, response_data = amazon_request(
        "shipping/v2/shipments/rates",
        amazon_payload,
        is_test_label
    )

    # 📦 LOG
    try:
        response_value = None
        if status_code == 200:
            payload = response_data.get("payload", {})
            rates = payload.get("rates", [])
            if rates:
                first_rate = rates[0]
                total_charge = first_rate.get("totalCharge", {}).get("value")
                if total_charge is not None:
                    total_charge = float(total_charge)
                    mapped_rate = float(mapped_rate)
                    if total_charge > mapped_rate:
                        mapped_rate = total_charge + 0.50
                    mapped_rate += float(client.com_fee)

                    #first_rate["totalCharge"]["value"] = round(total_charge, 2)
                    first_rate["totalCharge"]["value"] = round(mapped_rate, 2)
                # gereksiz alan temizle
                first_rate.pop("rateItemList", None)
                first_rate.pop("benefits", None)
                first_rate.pop("billedWeight", None)
                first_rate.pop("rateId", None)
                first_rate.pop("adjustmentItemList", None)
                first_rate.pop("availableValueAddedServiceGroups", None)
                first_rate.pop("supportedDocumentSpecifications", None)
                first_rate.pop("ineligibleRates", None)
                response_value = str(first_rate.get("totalCharge"))
        else:
            response_value = str(response_data)

        log = ShipmentLog(
            client_id=client.id,
            action="rate_label TEST" if is_test_label else "rate_label",
            reference_id=response_data.get("payload", {}).get("requestToken"),
            status="success" if status_code == 200 else "failed",
            response=response_value,
        )

        db.session.add(log)
        db.session.commit()

    except Exception as e:
        db.session.rollback()

    return jsonify(response_data), status_code


# 🔹 CREATE LABEL
@app.route("/label/create", methods=["POST"])
@client_token_required
def create_label():
    client = g.client

    # 🔥 RATE LIMIT
    if not check_rate_limit(client):
        return jsonify({"errors": [
            {
                "code": "RateLimit",
                "details": "Rate limit exceeded",
                "message": "Rate Limit"
            }
        ]}), 429

    data = request.json or {}

    marketPlace = data.get("marketPlace", "walmart")
    if marketPlace.lower() in ["walmart","targetplus"]:
        return jsonify({"errors": [
            {
                "code": "InvalidInput",
                "details": "The marketPlace input is required and It cannot be Targetplus or Walmart.",
                "message": "Invalid input"
            }
        ]}), 400

    amazon_payload = request_helper(data, client.reference_code, "create")

    mapped_rate, mapped_message = calculate_shipping_cost(client.id, data["shipFrom"]["postalCode"], data["shipTo"]["postalCode"], data["packages"][0]["weight"]["value"])
    if not mapped_rate:
        return jsonify({"errors": [
            {
                "code": "ZoneWeight",
                "details": mapped_message,
                "message": "Zone Weight"
            }
        ]}), 400

    is_test_label = data.get("isTestLabel", False)
    status_code, response_data = amazon_request(
        "shipping/v2/oneClickShipment",
        amazon_payload,
        is_test_label
    )

    # 📦 LOG
    try:
        amz_cost = 0
        response_value = None
        if status_code == 200:
            payload = response_data.get("payload", {})

            if payload:
                total_charge = payload.get("totalCharge", {}).get("value")

                if total_charge is not None:
                    total_charge = float(total_charge)
                    amz_cost = total_charge
                    mapped_rate = float(mapped_rate)
                    if total_charge > mapped_rate:
                        mapped_rate = total_charge + 0.50
                    mapped_rate += float(client.com_fee)

                    payload["totalCharge"]["value"] = round(mapped_rate, 2)

                payload.pop("rateItems", None)

                # 🔒 safe tracking
                tracking_id = None
                docs = payload.get("packageDocumentDetails", [])
                if docs:
                    tracking_id = docs[0].get("trackingId")

                record = {
                    "totalCharge": payload.get("totalCharge"),
                    "trackingId": tracking_id,
                    "shipmentId": payload.get("shipmentId")
                }

                response_value = str(record)

        else:
            response_value = str(response_data)

        log = ShipmentLog(
            client_id=client.id,
            action="create_label TEST" if is_test_label else "create_label",
            reference_id=response_data.get("payload", {}).get("shipmentId"),
            status="success" if status_code == 200 else "failed",
            response=response_value,
        )

        db.session.add(log)

        if not is_test_label:
            ship_from = data.get("shipFrom", {})

            # Alanları bir liste içine alalım
            fields = [
                ship_from.get("name"),
                ship_from.get("addressLine1"),
                ship_from.get("stateOrRegion"),
                ship_from.get("city"),
                ship_from.get("countryCode"),
                ship_from.get("postalCode")
            ]

            # Sadece None olmayan ve içi dolu olanları filtreleyip aralarına virgül koyalım
            from_address = ", ".join(str(f) for f in fields if f)

            label = Label(
                client_id = client.id,
                created_at = datetime.utcnow(),
                reference_code = client.reference_code,
                status = "purchased",
                market_place = marketPlace,
                from_address = from_address,
                customer_email = data.get("shipTo", {}).get("email"),
                customer_phone = data.get("shipTo", {}).get("phoneNumber"),
                shipment_id = response_data.get("payload", {}).get("shipmentId"),
                carrier = response_data.get("payload", {}).get("carrier", {}).get("id"),
                service = response_data.get("payload", {}).get("service", {}).get("id"),
                tracking_number = response_data.get("payload", {}).get("packageDocumentDetails", [{}])[0].get("trackingId"),
                amz_cost = Decimal(str(amz_cost)),
                my_cost = Decimal(str(round(mapped_rate, 2)))
            )
            db.session.add(label)
        db.session.commit()

    except Exception as e:
        db.session.rollback()

    return jsonify(response_data), status_code


# 🔹 LABEL IPTAL ENDPOINT
@app.route("/label/cancel", methods=["POST"])
@client_token_required
def cancel_label():
    client = g.client

    # 🔥 RATE LIMIT
    if not check_rate_limit(client):
        return jsonify({"errors": [
            {
                "code": "RateLimit",
                "details": "Rate limit exceeded",
                "message": "Rate Limit"
            }
        ]}), 429

    data = request.json or {}
    shipment_id = data.get("shipmentId")
    if not shipment_id:
        return jsonify({"errors": [
            {
                "code": "InvalidInput",
                "details": "ShipmentId input is required",
                "message": "Invalid Input"
            }
        ]}), 400

    is_test_label = data.get("isTestLabel", False)

    if not is_test_label:
        label = Label.query.filter_by(shipment_id=shipment_id).first()

        if not label:
            return jsonify({"success": False, "message": "Label not found"}), 404

        # Zaten refund edilmiş mi kontrolü (çok önemli)
        if label.status == "refunded":
            return jsonify({"success": False, "message": "Label was canceled before"}), 400

        # Label update
        label.status = "refunded"
        label.refunded_at = datetime.utcnow()

        db.session.commit()

    # Amazon proxy isteği
    status_code, response_data = amazon_request(
        "cancel",
        str(shipment_id),
        is_test_label
    )
    # ShipmentLog kaydı
    try:
        log = ShipmentLog(
            client_id=client.id,
            action="cancel_label TEST" if is_test_label else "cancel_label",
            reference_id=data.get("reference_id"),
            status="success" if status_code==200 else "failed",
            response=str(response_data)
        )
        db.session.add(log)
        db.session.commit()
    except:
        db.session.rollback()

    return jsonify(response_data), status_code


def build_label_query(args):
    """
    Listeleme ve Excel endpoint'inde ortak olarak kullanılan
    filtrelenmiş Label sorgusunu oluşturur.
    """
    query = Label.query

    client_id = args.get("client_id")
    carrier = args.get("carrier")
    status = args.get("status")
    market_place = args.get("market_place")
    tracking = args.get("tracking")
    start_date = args.get("start_date")
    end_date = args.get("end_date")

    if client_id:
        query = query.filter(Label.client_id == client_id)

    if carrier:
        query = query.filter(Label.carrier == carrier)

    if status:
        query = query.filter(Label.status == status)

    if market_place:
        query = query.filter(
            Label.market_place.ilike(f"%{market_place}%")
        )

    if tracking:
        query = query.filter(
            Label.tracking_number.ilike(f"%{tracking}%")
        )

    if start_date:
        start_datetime = datetime.strptime(
            start_date,
            "%Y-%m-%d"
        )

        query = query.filter(
            Label.created_at >= start_datetime
        )

    if end_date:
        # Bitiş tarihinde seçilen günün tamamını dahil eder.
        end_datetime = (
            datetime.strptime(end_date, "%Y-%m-%d")
            + timedelta(days=1)
        )

        query = query.filter(
            Label.created_at < end_datetime
        )

    return query


# ---------------------------------------------------------
# LABEL LIST ENDPOINT
# ---------------------------------------------------------

@app.route("/admin/labels", methods=["GET"])
@login_required
@role_required("admin")
def admin_labels():
    page = request.args.get(
        "page",
        default=1,
        type=int
    )

    allowed_per_page = [20, 50, 100, 500]

    per_page = request.args.get(
        "per_page",
        default=20,
        type=int
    )

    # URL üzerinden izin verilmeyen bir değer gelirse
    # varsayılan olarak 20 kullanılır.
    if per_page not in allowed_per_page:
        per_page = 20

    filters = {
        "client_id": request.args.get("client_id"),
        "carrier": request.args.get("carrier"),
        "status": request.args.get("status"),
        "market_place": request.args.get("market_place"),
        "tracking": request.args.get("tracking"),
        "start_date": request.args.get("start_date"),
        "end_date": request.args.get("end_date"),
    }

    query = build_label_query(request.args)

    # Filtrelenmiş tüm kayıtların toplamları
    grand_totals = query.with_entities(
        func.coalesce(
            func.sum(Label.amz_cost),
            0
        ).label("total_amz"),
        func.coalesce(
            func.sum(Label.my_cost),
            0
        ).label("total_my"),
    ).first()

    # Sayfalama
    pagination = (
        query
        .order_by(Label.created_at.desc())
        .paginate(
            page=page,
            per_page=per_page,
            error_out=False,
        )
    )

    labels = pagination.items

    # Yalnızca açık sayfadaki kayıtların toplamları
    page_amz_total = sum(
        (label.amz_cost or 0)
        for label in labels
    )

    page_my_total = sum(
        (label.my_cost or 0)
        for label in labels
    )

    clients = Client.query.all()

    return render_template(
        "labels.html",
        labels=labels,
        pagination=pagination,
        clients=clients,
        filters=filters,
        allowed_per_page=allowed_per_page,
        per_page=per_page,
        grand_total_amz=grand_totals.total_amz,
        grand_total_my=grand_totals.total_my,
        page_amz_total=page_amz_total,
        page_my_total=page_my_total,
    )


# ---------------------------------------------------------
# LABEL EXCEL EXPORT ENDPOINT
# ---------------------------------------------------------

@app.route("/admin/labels/export", methods=["GET"])
@login_required
@role_required("admin")
def export_admin_labels():
    query = (
        build_label_query(request.args)
        .order_by(Label.created_at.desc())
    )

    labels = query.all()

    columns = [
        "Date",
        "Client",
        "Carrier",
        "Service",
        "Tracking Number",
        "Marketplace",
        "From ZIP",
        "To ZIP",
        "Weight",
        "Original Cost",
        "My Cost",
        "Status",
    ]

    excel_rows = []

    for label in labels:
        excel_rows.append({
            "Date": label.created_at,
            "Client": (
                label.client.reference_code
                if label.client
                else ""
            ),
            "Carrier": label.carrier or "",
            "Service": label.service or "",
            "Tracking Number": label.tracking_number or "",
            "Marketplace": label.market_place or "",
            "From ZIP": label.from_zip or "",
            "To ZIP": label.to_zip or "",
            "Weight": label.weight or "",
            "Original Cost": label.amz_cost or 0,
            "My Cost": label.my_cost or 0,
            "Status": label.status or "",
        })

    # Kayıt olmasa bile Excel sütun başlıkları oluşturulur.
    dataframe = pd.DataFrame(
        excel_rows,
        columns=columns
    )

    excel_buffer = BytesIO()

    with pd.ExcelWriter(
        excel_buffer,
        engine="openpyxl"
    ) as writer:
        dataframe.to_excel(
            writer,
            sheet_name="Labels",
            index=False,
        )

        worksheet = writer.sheets["Labels"]

        # Başlık satırını sabit tutar.
        worksheet.freeze_panes = "A2"

        # Excel filtrelerini aktif eder.
        worksheet.auto_filter.ref = worksheet.dimensions

        # Sütun genişliklerini içeriklere göre ayarlar.
        for column_cells in worksheet.columns:
            column_letter = column_cells[0].column_letter
            maximum_length = 0

            for cell in column_cells:
                cell_value = cell.value

                if cell_value is None:
                    cell_length = 0
                else:
                    cell_length = len(str(cell_value))

                maximum_length = max(
                    maximum_length,
                    cell_length
                )

            worksheet.column_dimensions[
                column_letter
            ].width = min(
                maximum_length + 2,
                40
            )

    excel_buffer.seek(0)

    filename = (
        "labels_"
        f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
        ".xlsx"
    )

    return send_file(
        excel_buffer,
        as_attachment=True,
        download_name=filename,
        mimetype=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    )


# ---------------------------------------------------------
# INTERNATIONAL LABEL HELPERS
# ---------------------------------------------------------

def build_international_label_query(args):
    """
    Listeleme ve Excel endpoint'inde ortak olarak kullanılan
    filtrelenmiş InternationalLabel sorgusunu oluşturur.
    """
    query = InternationalLabel.query

    client_id = args.get("client_id")
    carrier = args.get("carrier")
    status = args.get("status")
    market_place = args.get("market_place")
    tracking = args.get("tracking")
    start_date = args.get("start_date")
    end_date = args.get("end_date")

    if client_id:
        query = query.filter(
            InternationalLabel.client_id == client_id
        )

    if carrier:
        query = query.filter(
            InternationalLabel.carrier == carrier
        )

    if status:
        query = query.filter(
            InternationalLabel.status == status
        )

    if market_place:
        query = query.filter(
            InternationalLabel.market_place.ilike(
                f"%{market_place}%"
            )
        )

    if tracking:
        query = query.filter(
            InternationalLabel.tracking_number.ilike(
                f"%{tracking}%"
            )
        )

    if start_date:
        start_datetime = datetime.strptime(
            start_date,
            "%Y-%m-%d"
        )

        query = query.filter(
            InternationalLabel.created_at >= start_datetime
        )

    if end_date:
        # Bitiş tarihinde seçilen günün tamamını dahil eder.
        end_datetime = (
            datetime.strptime(end_date, "%Y-%m-%d")
            + timedelta(days=1)
        )

        query = query.filter(
            InternationalLabel.created_at < end_datetime
        )

    return query


# ---------------------------------------------------------
# INTERNATIONAL LABEL LIST ENDPOINT
# ---------------------------------------------------------

@app.route("/admin/international-labels", methods=["GET"])
@login_required
@role_required("admin")
def admin_international_labels():
    page = request.args.get(
        "page",
        default=1,
        type=int
    )

    allowed_per_page = [20, 50, 100, 500]

    per_page = request.args.get(
        "per_page",
        default=20,
        type=int
    )

    # URL üzerinden izin verilmeyen bir değer gelirse
    # varsayılan olarak 20 kullanılır.
    if per_page not in allowed_per_page:
        per_page = 20

    filters = {
        "client_id": request.args.get("client_id"),
        "carrier": request.args.get("carrier"),
        "status": request.args.get("status"),
        "market_place": request.args.get("market_place"),
        "tracking": request.args.get("tracking"),
        "start_date": request.args.get("start_date"),
        "end_date": request.args.get("end_date"),
    }

    query = build_international_label_query(request.args)

    # Filtrelenmiş tüm kayıtların toplamları
    grand_totals = query.with_entities(
        func.coalesce(
            func.sum(InternationalLabel.amz_cost),
            0
        ).label("total_amz"),
        func.coalesce(
            func.sum(InternationalLabel.my_cost),
            0
        ).label("total_my"),
    ).first()

    # Sayfalama
    pagination = (
        query
        .order_by(InternationalLabel.created_at.desc())
        .paginate(
            page=page,
            per_page=per_page,
            error_out=False,
        )
    )

    labels = pagination.items

    # Yalnızca açık sayfadaki kayıtların toplamları
    page_amz_total = sum(
        (label.amz_cost or 0)
        for label in labels
    )

    page_my_total = sum(
        (label.my_cost or 0)
        for label in labels
    )

    clients = Client.query.all()

    return render_template(
        "international_labels.html",
        labels=labels,
        pagination=pagination,
        clients=clients,
        filters=filters,
        allowed_per_page=allowed_per_page,
        per_page=per_page,
        grand_total_amz=grand_totals.total_amz,
        grand_total_my=grand_totals.total_my,
        page_amz_total=page_amz_total,
        page_my_total=page_my_total,
    )


# ---------------------------------------------------------
# INTERNATIONAL LABEL EXCEL EXPORT ENDPOINT
# ---------------------------------------------------------

@app.route(
    "/admin/international-labels/export",
    methods=["GET"]
)
@login_required
@role_required("admin")
def export_admin_international_labels():
    query = (
        build_international_label_query(request.args)
        .order_by(InternationalLabel.created_at.desc())
    )

    labels = query.all()

    columns = [
        "Date",
        "Client",
        "Carrier",
        "Service",
        "Tracking Number",
        "Marketplace",
        "From Country",
        "From ZIP",
        "To Country",
        "To ZIP",
        "Weight",
        "Original Cost",
        "My Cost",
        "Status",
    ]

    excel_rows = []

    for label in labels:
        excel_rows.append({
            "Date": label.created_at,
            "Client": (
                label.client.reference_code
                if label.client
                else ""
            ),
            "Carrier": label.carrier or "",
            "Service": label.service or "",
            "Tracking Number": label.tracking_number or "",
            "Marketplace": label.market_place or "",
            "From Country": label.from_country or "",
            "From ZIP": label.from_zip or "",
            "To Country": label.to_country or "",
            "To ZIP": label.to_zip or "",
            "Weight": label.weight or "",
            "Original Cost": label.amz_cost or 0,
            "My Cost": label.my_cost or 0,
            "Status": label.status or "",
        })

    # Kayıt olmasa bile Excel sütun başlıkları oluşturulur.
    dataframe = pd.DataFrame(
        excel_rows,
        columns=columns
    )

    excel_buffer = BytesIO()

    with pd.ExcelWriter(
        excel_buffer,
        engine="openpyxl"
    ) as writer:
        dataframe.to_excel(
            writer,
            sheet_name="International Labels",
            index=False,
        )

        worksheet = writer.sheets["International Labels"]

        # Başlık satırını sabit tutar.
        worksheet.freeze_panes = "A2"

        # Excel filtrelerini aktif eder.
        worksheet.auto_filter.ref = worksheet.dimensions

        # Sütun genişliklerini içeriklere göre ayarlar.
        for column_cells in worksheet.columns:
            column_letter = column_cells[0].column_letter
            maximum_length = 0

            for cell in column_cells:
                cell_value = cell.value

                if cell_value is None:
                    cell_length = 0
                else:
                    cell_length = len(str(cell_value))

                maximum_length = max(
                    maximum_length,
                    cell_length
                )

            worksheet.column_dimensions[
                column_letter
            ].width = min(
                maximum_length + 2,
                40
            )

    excel_buffer.seek(0)

    filename = (
        "international_labels_"
        f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
        ".xlsx"
    )

    return send_file(
        excel_buffer,
        as_attachment=True,
        download_name=filename,
        mimetype=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    )
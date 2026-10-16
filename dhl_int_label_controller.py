from flask_app import app, db, client_token_required
from flask import request, jsonify, g
from model import ShipmentLog, InternationalLabel
import time
import requests
from data_helper import dhl_int_request_helper
from dhl_service import get_dhl_token
from decimal import Decimal
from datetime import datetime


# 🔹 RATE LIMIT (mevcut ile aynı)
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


# 🔹 DHL REQUEST (mevcut dhl_request fonksiyonunu kullanabiliriz, endpoint değiştireceğiz)
def dhl_int_request(endpoint, payload=None, method="POST", is_test=False, params=None):
    """
    DHL International Direct API istekleri için.
    Base URL: https://api.dhlecs.com (aynı auth kullanılıyor)
    """
    DHL_BASE_URL = "https://api.dhlecs.com"
    if is_test:
        DHL_BASE_URL = "https://api-sandbox.dhlecs.com"

    token = get_dhl_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "content-type": "application/json",
        "accept": "application/json"
    }

    def send_request():
        url = f"{DHL_BASE_URL}/{endpoint.lstrip('/')}"
        if method.upper() == "DELETE":
            return requests.delete(url, headers=headers, params=params, timeout=15)
        elif method.upper() == "GET":
            return requests.get(url, headers=headers, params=params, timeout=15)
        elif method.upper() == "PUT":
            return requests.put(url, json=payload, headers=headers, params=params, timeout=15)
        else:
            return requests.post(url, json=payload, headers=headers, params=params, timeout=15)

    response = send_request()

    # 🔁 Token expired → retry (401 Unauthorized durumunda token yenileyip tekrar dene)
    if response.status_code == 401:
        token = get_dhl_token()
        headers["Authorization"] = f"Bearer {token}"
        response = send_request()

    try:
        res_json = response.json()
    except:
        res_json = {"raw_response": response.text}

    return response.status_code, res_json


# 🔹 DHL INTERNATIONAL RATE ENDPOINT
@app.route("/dhl/int/label/rate", methods=["POST"])
@client_token_required
def dhl_int_rate_label():
    """
    DHL International Direct - Rate alma endpoint'i.
    Gelen DHL fiyatına %10 ekleyip döner (DB ile kıyaslama YOK).
    """
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

    # Marketplace validasyonu (mevcut ile aynı)
    marketPlace = data.get("marketPlace", "xxxx2")
    if marketPlace.lower() in ["xxxx", "xxxx1"]:
        return jsonify({"errors": [
            {
                "code": "InvalidInput",
                "details": "The marketPlace input is required and It cannot be xxxx or xxxx1.",
                "message": "Invalid input"
            }
        ]}), 400

    # Zorunlu alan kontrolü - International için countryCode şart
    ship_from = data.get("shipFrom", {})
    ship_to = data.get("shipTo", {})
    packages = data.get("packages", [])

    if not ship_from.get("countryCode") or not ship_to.get("countryCode"):
        return jsonify({"errors": [{
            "code": "InvalidInput",
            "details": "shipFrom.countryCode and shipTo.countryCode are required for international shipments",
            "message": "Missing country codes"
        }]}), 400

    if ship_from.get("countryCode") == ship_to.get("countryCode"):
        return jsonify({"errors": [{
            "code": "InvalidInput",
            "details": "shipFrom and shipTo countryCode cannot be the same for international shipments",
            "message": "Same country not allowed for international"
        }]}), 400

    if not packages or not packages[0].get("weight", {}).get("value"):
        return jsonify({"errors": [{
            "code": "InvalidInput",
            "details": "Package weight is required",
            "message": "Missing weight"
        }]}), 400

    # 🔸 PLT International için warehouse zip validation (pickupDict keys)
    valid_warehouse_zips = {"75081-1955", "75243-5485", "77407-9012", "75006-2527"}
    from_zip = ship_from.get("postalCode", "").strip()
    if from_zip not in valid_warehouse_zips:
        return jsonify({"errors": [{
            "code": "InvalidWarehouse",
            "details": f"shipFrom.postalCode must be a valid warehouse zip for PLT International. Valid: {list(valid_warehouse_zips)}",
            "message": "Invalid warehouse zip code"
        }]}), 400

    # DHL International rate payload hazırla
    dhl_payload = dhl_int_request_helper(data, client.reference_code, "rate")

    is_test_label = data.get("isTestLabel", False)

    # DHL International Rate endpoint çağrısı
    # DHL International Direct rate endpoint: shipping/v4/rates veya shipping/v4/products (int'l)
    status_code, response_data = dhl_int_request(
        endpoint="shipping/v4/products",  # International rates endpoint
        payload=dhl_payload,
        method="POST",
        is_test=is_test_label
    )

    response_value = None
    response_to_return = response_data

    # 📦 LOG ve Yanıtı Düzenleme
    try:
        if status_code == 200:
            # DHL International response yapısı farklı olabilir
            # products array'i veya rates array'i gelebilir
            products = response_data.get("products", [])
            rates = response_data.get("rates", [])

            # Önce products, sonra rates kontrol et
            items = products if products else rates

            if items:
                first_item = items[0]
                total_charge = first_item.get("rate", {}).get("amount") or first_item.get("totalCharge", {}).get("value")

                if total_charge is not None:
                    total_charge = float(total_charge)

                    # 🎯 DB KIYASLAMASI YOK - Sadece %10 ekle
                    final_rate = round(total_charge * 1.10, 2)

                    # Orijinal tutar log için sakla (response'a EKLEME)
                    original_amount = round(total_charge, 2)

                    # Response'u güncelle - SADECE final_rate döner
                    if "rate" in first_item:
                        first_item["rate"]["amount"] = final_rate
                        # originalAmount KALDIRILDI - sadece DB/log için
                    elif "totalCharge" in first_item:
                        first_item["totalCharge"]["value"] = final_rate
                        # originalValue KALDIRILDI - sadece DB/log için

                    # rateComponents varsa temizle
                    if "rate" in first_item:
                        first_item.get("rate").pop("rateComponents", None)

                # Log için response_value'a original amount da ekle (DB'ye kaydedilsin)
                rate_obj = first_item.get("rate") or first_item.get("totalCharge")
                if rate_obj:
                    # Log'a yazılacak string - original amount içerir
                    response_value = str({**rate_obj, "originalAmount": original_amount})
                else:
                    response_value = str(rate_obj)

                # Sadece ilk ürün objesini yanıt olarak döndür
                response_to_return = first_item
        else:
            response_value = str(response_data)

        log = ShipmentLog(
            client_id=client.id,
            action="dhl_int_rate_label TEST" if is_test_label else "dhl_int_rate_label",
            reference_id=response_data.get("pickup") or response_data.get("quoteId"),
            status="success" if status_code == 200 else "failed",
            response=response_value,
        )

        db.session.add(log)
        db.session.commit()

    except Exception as e:
        db.session.rollback()
        app.logger.exception("DHL Int Rate log error: %s", str(e))

    return jsonify(response_to_return), status_code


# 🔹 DHL INTERNATIONAL CREATE LABEL
@app.route("/dhl/int/label/create", methods=["POST"])
@client_token_required
def dhl_int_create_label():
    """
    DHL International Direct - Label oluşturma endpoint'i.
    1. Önce rate alır (shipping/v4/rates)
    2. Fiyata %10 ekler
    3. Sonra label oluşturur (shipping/v4/label)
    """
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

    marketPlace = data.get("marketPlace", "xxxx2")
    if marketPlace.lower() in ["xxxx", "xxxx1"]:
        return jsonify({"errors": [
            {
                "code": "InvalidInput",
                "details": "The marketPlace input is required and It cannot be xxxx or xxxx1.",
                "message": "Invalid input"
            }
        ]}), 400

    # Zorunlu alan kontrolleri
    ship_from = data.get("shipFrom", {})
    ship_to = data.get("shipTo", {})
    packages = data.get("packages", [])

    if not ship_from.get("countryCode") or not ship_to.get("countryCode"):
        return jsonify({"errors": [{
            "code": "InvalidInput",
            "details": "shipFrom.countryCode and shipTo.countryCode are required for international shipments",
            "message": "Missing country codes"
        }]}), 400

    if ship_from.get("countryCode") == ship_to.get("countryCode"):
        return jsonify({"errors": [{
            "code": "InvalidInput",
            "details": "shipFrom and shipTo countryCode cannot be the same for international shipments",
            "message": "Same country not allowed for international"
        }]}), 400

    if not packages or not packages[0].get("weight", {}).get("value"):
        return jsonify({"errors": [{
            "code": "InvalidInput",
            "details": "Package weight is required",
            "message": "Missing weight"
        }]}), 400

    # 🔸 PLT International için warehouse zip validation (pickupDict keys)
    valid_warehouse_zips = {"75081-1955", "75243-5485", "77407-9012", "75006-2527"}
    from_zip = ship_from.get("postalCode", "").strip()
    if from_zip not in valid_warehouse_zips:
        return jsonify({"errors": [{
            "code": "InvalidWarehouse",
            "details": f"shipFrom.postalCode must be a valid warehouse zip for PLT International. Valid: {list(valid_warehouse_zips)}",
            "message": "Invalid warehouse zip code"
        }]}), 400

    # Items kontrolü - International için gümrük için item detayları şart
    has_items = False
    for pkg in packages:
        if pkg.get("items") and len(pkg["items"]) > 0:
            has_items = True
            break

    if not has_items:
        return jsonify({"errors": [{
            "code": "InvalidInput",
            "details": "At least one item with description, value, quantity, weight, hsCode, originCountry is required for international shipments",
            "message": "Missing customs items"
        }]}), 400

    is_test_label = data.get("isTestLabel", False)

    # 🔸 ADIM 1: Rate al
    rate_payload = dhl_int_request_helper(data, client.reference_code, "rate")

    rate_status_code, rate_response_data = dhl_int_request(
        endpoint="shipping/v4/products",  # International rates endpoint
        payload=rate_payload,
        method="POST",
        is_test=is_test_label
    )

    response_value = None
    dhl_original_rate = 0
    final_rate = 0

    try:
        if rate_status_code == 200:
            products = rate_response_data.get("products", [])
            rates = rate_response_data.get("rates", [])
            items = products if products else rates

            if items:
                first_item = items[0]
                total_charge = first_item.get("rate", {}).get("amount") or first_item.get("totalCharge", {}).get("value")

                if total_charge is not None:
                    total_charge = float(total_charge)
                    dhl_original_rate = round(total_charge, 2)

                    # 🎯 %10 EKLE - DB kıyaslaması YOK
                    final_rate = round(total_charge * 1.10, 2)

                    if "rate" in first_item:
                        first_item.get("rate").pop("rateComponents", None)

                response_value = str(first_item.get("rate") or first_item.get("totalCharge"))
                response_to_return = first_item
        else:
            response_value = str(rate_response_data)

            log = ShipmentLog(
                client_id=client.id,
                action="dhl_int_rate_label TEST" if is_test_label else "dhl_int_rate_label",
                reference_id=rate_response_data.get("pickup") or rate_response_data.get("quoteId"),
                status="success" if rate_status_code == 200 else "failed",
                response=response_value,
            )

            db.session.add(log)
            db.session.commit()

    except Exception as e:
        db.session.rollback()
        app.logger.exception("DHL Int Create - Rate step error: %s", str(e))

    # Rate başarısızsa label oluşturma
    if rate_status_code != 200 or final_rate == 0:
        return jsonify(rate_response_data), rate_status_code

    # 🔸 ADIM 2: Label oluştur
    dhl_payload = dhl_int_request_helper(data, client.reference_code, "create")

    # DHL create label endpoint - International için de aynı olabilir
    status_code, response_data = dhl_int_request(
        endpoint="shipping/v4/label",
        payload=dhl_payload,
        method="POST",
        is_test=is_test_label,
        params={"format": "PNG"}
    )

    response_value = None
    response_to_return = response_data
    shipment_id = ""
    tracking_number = ""

    # ---------------------------------------------------------
    # 1. DHL BAŞARISIZ
    # ---------------------------------------------------------
    if status_code != 200:
        response_value = str(response_to_return)

        try:
            log = ShipmentLog(
                client_id=client.id,
                action="dhl_int_create_label TEST" if is_test_label else "dhl_int_create_label",
                reference_id=dhl_payload.get("packageDetail", {}).get("packageId"),
                status="failed",
                response=response_value,
            )

            db.session.add(log)
            db.session.commit()

        except Exception as e:
            db.session.rollback()
            app.logger.exception(
                "DHL Int label failure log could not be saved. "
                "client_id=%s package_id=%s",
                client.id,
                dhl_payload.get("packageDetail", {}).get("packageId")
            )

        return jsonify(response_to_return), status_code

    # ---------------------------------------------------------
    # 2. DHL BAŞARILI
    # ---------------------------------------------------------

    labels = response_to_return.get("labels", [])

    if not labels:
        app.logger.error(
            "DHL Int returned 200 but labels is empty. "
            "client_id=%s package_id=%s response=%s",
            client.id,
            dhl_payload.get("packageDetail", {}).get("packageId"),
            str(response_to_return)
        )

        return jsonify({
            "errors": [
                {
                    "code": "InvalidDHLResponse",
                    "details": (
                        "DHL returned a successful response "
                        "but label data was empty."
                    ),
                    "message": "Invalid DHL response"
                }
            ]
        }), 502

    label = labels[0]

    # DB işlemlerinde kullanmak için DHL bilgileri al
    shipment_id = label.get("packageId") or label.get("shipmentId")
    tracking_number = label.get("dhlPackageId") or label.get("trackingNumber")

    # Kullanıcıya dönecek response hazırlanıyor
    response_to_return.pop("pickup", None)

    label.pop("labelDetail", None)
    label.pop("link", None)

    label["totalCharge"] = {
        "unit": "USD",
        "value": final_rate
    }

    label["shipmentId"] = shipment_id
    label.pop("packageId", None)

    record = {
        "totalCharge": Decimal(str(round(final_rate, 2))),
        "trackingId": tracking_number,
        "shipmentId": shipment_id
    }

    response_value = str(record)

    # ---------------------------------------------------------
    # 3. DHL BAŞARILI, YEREL DB KAYDI (InternationalLabel tablosuna)
    # ---------------------------------------------------------

    try:
        log = ShipmentLog(
            client_id=client.id,
            action="dhl_int_create_label TEST" if is_test_label else "dhl_int_create_label",
            reference_id=dhl_payload.get("packageDetail", {}).get("packageId"),
            status="success",
            response=response_value,
        )

        db.session.add(log)

        if not is_test_label:
            # From address string oluştur
            ship_from = data.get("shipFrom", {})
            from_fields = [
                ship_from.get("name"),
                ship_from.get("addressLine1"),
                ship_from.get("stateOrRegion"),
                ship_from.get("city"),
                ship_from.get("countryCode"),
                ship_from.get("postalCode")
            ]
            from_address = ", ".join(str(f) for f in from_fields if f)

            # To address string oluştur
            ship_to = data.get("shipTo", {})
            to_fields = [
                ship_to.get("name"),
                ship_to.get("addressLine1"),
                ship_to.get("stateOrRegion"),
                ship_to.get("city"),
                ship_to.get("countryCode"),
                ship_to.get("postalCode")
            ]
            to_address = ", ".join(str(f) for f in to_fields if f)

            # İlk paketin ilk item'ından gümrük bilgilerini al
            first_package = data.get("packages", [{}])[0]
            first_item = first_package.get("items", [{}])[0] if first_package.get("items") else {}

            int_label_record = InternationalLabel(
                client_id=client.id,
                pickup_id=dhl_payload.get("pickup"),
                created_at=datetime.utcnow(),
                reference_code=client.reference_code,
                status="purchased",
                market_place=marketPlace,
                from_address=from_address,
                from_zip=ship_from.get("postalCode"),
                from_country=ship_from.get("countryCode"),
                to_address=to_address,
                to_zip=ship_to.get("postalCode"),
                to_country=ship_to.get("countryCode"),
                weight=first_package.get("weight", {}).get("value"),
                customer_email=ship_to.get("email"),
                customer_phone=ship_to.get("phoneNumber"),
                shipment_id=shipment_id,
                carrier="DHL",
                service=response_to_return.get("orderedProductId", "DHL_International"),
                tracking_number=tracking_number,
                amz_cost=Decimal(str(dhl_original_rate)),
                my_cost=Decimal(str(round(final_rate, 2))),
                # Gümrük alanları
                customs_value=first_item.get("itemValue", {}).get("value"),
                customs_currency=first_item.get("itemValue", {}).get("unit", "USD"),
                hs_code=first_item.get("hsCode") or first_item.get("itemIdentifier"),
                origin_country=first_item.get("originCountry"),
                item_description=first_item.get("description"),
                item_quantity=first_item.get("quantity", 1),
            )

            db.session.add(int_label_record)

        db.session.commit()

    except Exception as e:
        db.session.rollback()

        app.logger.exception(
            "DHL Int label created but local database save failed. "
            "client_id=%s package_id=%s shipment_id=%s "
            "tracking_number=%s",
            client.id,
            dhl_payload.get("packageDetail", {}).get("packageId"),
            shipment_id,
            tracking_number
        )

        # Recovery log
        try:
            recovery_log = ShipmentLog(
                client_id=client.id,
                action="dhl_int_create_label_db_failed",
                reference_id=dhl_payload.get("packageDetail", {}).get("packageId"),
                status="failed",
                response=str({
                    "message": (
                        "DHL International label was created successfully, "
                        "but it could not be saved to the local system."
                    ),
                    "shipmentId": shipment_id,
                    "trackingId": tracking_number,
                    "dhlResponse": response_value,
                    "databaseError": str(e)
                }),
            )

            db.session.add(recovery_log)
            db.session.commit()

        except Exception as recovery_error:
            db.session.rollback()
            app.logger.exception(
                "DHL Int recovery log could not be saved. "
                "client_id=%s package_id=%s shipment_id=%s "
                "tracking_number=%s",
                client.id,
                dhl_payload.get("packageDetail", {}).get("packageId"),
                shipment_id,
                tracking_number
            )

        return jsonify({
            "errors": [
                {
                    "code": "LabelPersistenceError",
                    "details": (
                        "The DHL International label was created successfully, "
                        "but it could not be saved to the local system. "
                        "Do not retry this request automatically."
                    ),
                    "message": "Label database save failed"
                }
            ],
            "retryable": False,
            "shipmentId": shipment_id,
            "trackingId": tracking_number
        }), 500

    return jsonify(response_to_return), status_code


# 🔹 DHL INTERNATIONAL LABEL CANCEL ENDPOINT
@app.route("/dhl/int/label/cancel", methods=["POST"])
@client_token_required
def dhl_int_cancel_label():
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

    label = None

    if not is_test_label:
        label = InternationalLabel.query.filter_by(shipment_id=shipment_id, client_id=client.id).first()

        if not label:
            return jsonify({"success": False, "message": "Label not found"}), 404

        if label.status == "refunded":
            return jsonify({"success": False, "message": "Label was canceled before"}), 400

    pickup_id = label.pickup_id if label else shipment_id

    # DHL proxy isteği
    status_code, response_data = dhl_int_request(
        endpoint=f"shipping/v4/label/{pickup_id}",
        payload=None,
        method="DELETE",
        is_test=is_test_label,
        params={"packageId": shipment_id}
    )

    # 3. DHL Başarısız ise DB'ye HİÇ DOKUNMA
    if status_code not in (200, 204):
        return jsonify({
            "success": False,
            "message": "DHL tarafında iptal başarısız oldu",
            "dhl_response": response_data
        }), status_code

    if label:
        try:
            label.status = "refunded"
            label.refunded_at = datetime.utcnow()
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            return jsonify({"success": False, "message": "DHL iptal edildi fakat DB güncellenemedi"}), 500

    try:
        log = ShipmentLog(
            client_id=client.id,
            action="dhl_int_cancel_label TEST" if is_test_label else "dhl_int_cancel_label",
            reference_id=shipment_id,
            status="success" if status_code in (200, 204) else "failed",
            response=str(response_data)
        )
        db.session.add(log)
        db.session.commit()
    except:
        db.session.rollback()

    return jsonify(response_data), status_code
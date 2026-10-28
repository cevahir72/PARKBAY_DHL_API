from flask_app import app, db, client_token_required
from flask import request, jsonify, g
from model import ShipmentLog, InternationalLabel
import time
import requests
from data_helper import dhl_int_request_helper, normalize_package_weights
from dhl_service import get_dhl_token
from easyship_service import calculate_for_dhl_request
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
    Gelen DHL fiyatına 2$ ekleyip döner (DB ile kıyaslama YOK).
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
    normalize_package_weights(data)

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

                    # 🎯 DB KIYASLAMASI YOK - Sadece 2$ ekle
                    final_rate = round(total_charge + 2.0, 2)

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
    2. Fiyata 2$ ekler
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
    normalize_package_weights(data)

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

                    # 🎯 2$ EKLE - DB kıyaslaması YOK
                    final_rate = round(total_charge + 2.0, 2)

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

    # ---------------------------------------------------------
    # 2.1 GÜMRÜK VERGİSİ (Easyship Taxes & Duties - sunucu içi)
    # ---------------------------------------------------------
    # Easyship kimlik bilgisi ve istek gövdesi müşteriye açılmaz; API
    # içinde (DHL isteğinden) üretilir. Easyship yanıtındaki duty + tax
    # toplamı customs_value alanına yazılır. Label oluşturulmadan önce
    # hesaplanır; hesaplanamazsa etiket oluşturulmaz.
    first_package = data.get("packages", [{}])[0] if data.get("packages") else {}
    first_item = (
        first_package.get("items", [{}])[0]
        if first_package.get("items")
        else {}
    )

    customs_currency = str(
        (first_item.get("itemValue") or {}).get("unit") or "USD"
    ).upper()
    customs_tax = None
    customs_duty = None
    customs_value = 0.0

    easyship_result = {}
    try:
        easyship_result = calculate_for_dhl_request(
            data,
            shipment_charge=final_rate,
            insurance_fee=data.get("insuranceFee", data.get("insurance_fee", 0)),
        )
    except Exception as error:
        app.logger.exception(
            "Easyship taxes & duties call failed unexpectedly. client_id=%s",
            client.id,
        )
        easyship_result = {"success": False, "error": str(error)}

    if not easyship_result.get("success"):
        app.logger.warning(
            "Easyship taxes & duties could not be calculated. "
            "client_id=%s error=%s",
            client.id,
            easyship_result.get("error"),
        )

        return jsonify({"errors": [{
            "code": "CustomsTaxCalculationFailed",
            "details": (
                "Customs duty and tax could not be calculated for this "
                "shipment. Please try again later."
            ),
            "message": "Customs tax calculation failed"
        }]}), 502

    customs_value = easyship_result["total"]
    customs_currency = str(
        easyship_result.get("currency") or customs_currency
    ).upper()
    customs_tax = easyship_result["tax"]
    customs_duty = easyship_result["duty"]

    # total_cost = my_cost + customs_value
    my_cost_value = round(float(final_rate), 2)
    total_cost = round(my_cost_value + float(customs_value), 2)

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
    # Void çağrısında kullanılacak gerçek pickup (DHL yanıtındaki değer önceliklidir)
    dhl_pickup_id = response_to_return.get("pickup") or dhl_payload.get("pickup")

    # Kullanıcıya dönecek response hazırlanıyor
    response_to_return.pop("pickup", None)

    label.pop("labelDetail", None)
    label.pop("link", None)

    label["shipmentId"] = shipment_id
    label.setdefault("trackingId", tracking_number)
    label.pop("packageId", None)

    # ---------------------------------------------------------
    # 2.2 Sadece total_cost yanıta eklensin
    # ---------------------------------------------------------
    # total_cost = my_cost + customs_value
    # amz_cost, my_cost ve customs_value DB'de kalır; API yanıtında
    # başka bir sayısal maliyet alanı dönmez.
    for numeric_field in (
        "totalCharge",
        "customsValue",
        "customsCurrency",
        "customsTax",
        "customsDuty",
    ):
        label.pop(numeric_field, None)

    label["total_cost"] = total_cost

    record = {
        "totalCharge": Decimal(str(my_cost_value)),
        "total_cost": total_cost,
        "trackingId": tracking_number,
        "shipmentId": shipment_id
    }

    if customs_value is not None:
        record["customsValue"] = str(customs_value)
    if customs_tax is not None:
        record["customsTax"] = customs_tax
        record["customsDuty"] = customs_duty

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

            int_label_record = InternationalLabel(
                client_id=client.id,
                pickup_id=dhl_pickup_id,
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
                my_cost=Decimal(str(my_cost_value)),
                # Gümrük alanları (Easyship duty + tax)
                customs_value=Decimal(str(customs_value)),
                total_cost=Decimal(str(total_cost)),
                customs_currency=customs_currency,
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
    dhl_package_id = data.get("dhlPackageId")
    pickup_id = data.get("pickupId")

    if not is_test_label:
        label = InternationalLabel.query.filter_by(
            shipment_id=shipment_id,
            client_id=client.id
        ).first()

        if label:
            if label.status in ("refunded", "cancelled"):
                return jsonify({"success": False, "message": "Label was canceled before"}), 400

            # DB'de kayıtlı gerçek DHL kimliklerini kullan
            pickup_id = pickup_id or label.pickup_id
            dhl_package_id = dhl_package_id or label.tracking_number
        elif not (pickup_id and dhl_package_id):
            # Kayıt yoksa iptal için gerekli DHL kimlikleri istekte verilmelidir
            return jsonify({"success": False, "message": "Label not found"}), 404

    if not pickup_id:
        return jsonify({
            "success": False,
            "message": (
                "pickupId could not be resolved. "
                "Send pickupId in the request body for test labels."
            ),
            "retryable": False
        }), 400

    # DHL: DELETE /shipping/v4/label/{pickup}?packageId=... | ?dhlPackageId=...
    # International (PLT) etiketleri packageId ile bulunamayabilir (400.0000002).
    # Bu durumda DHL'in ürettiği dhlPackageId (TMUID) ile tekrar denenir.
    identifiers = [("packageId", shipment_id)]
    if dhl_package_id and dhl_package_id != shipment_id:
        identifiers.append(("dhlPackageId", dhl_package_id))

    attempts = []
    status_code = None
    response_data = None
    used_identifier = None

    for param_name, param_value in identifiers:
        status_code, response_data = dhl_int_request(
            endpoint=f"shipping/v4/label/{pickup_id}",
            payload=None,
            method="DELETE",
            is_test=is_test_label,
            params={param_name: param_value}
        )

        attempts.append({
            "identifier": param_name,
            "value": param_value,
            "statusCode": status_code,
            "response": response_data
        })

        if status_code in (200, 204):
            used_identifier = param_name
            break

        # Sadece "kayıt bulunamadı" hatasında diğer kimlikle tekrar dene
        is_not_found = False
        if isinstance(response_data, dict):
            reasons = " ".join(
                str(param.get("reason", ""))
                for param in response_data.get("invalidParams", [])
                if isinstance(param, dict)
            ).lower()
            is_not_found = (
                status_code == 404
                or str(response_data.get("type", "")).endswith("400.0000002")
                or "not found" in reasons
            )

        if not is_not_found:
            break

    # International (PLT) etiketleri DHL Label API ile void edilemeyebilir.
    # DHL dokümanı: "Deleting an existing label (Domestic only)".
    # Her iki kimlik de "not found" dönerse etiket yerel olarak iptal edilir;
    # böylece müşteri aynı etiketi tekrar kullanmaz.
    void_not_supported = (
        status_code not in (200, 204)
        and isinstance(response_data, dict)
        and (
            str(response_data.get("type", "")).endswith("400.0000002")
            or any(
                "not found" in str(param.get("reason", "")).lower()
                for param in response_data.get("invalidParams", [])
                if isinstance(param, dict)
            )
        )
    )

    if void_not_supported:
        if label:
            try:
                label.status = "cancelled"
                label.refunded_at = datetime.utcnow()
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                app.logger.exception(
                    "DHL Int label local cancel could not be saved. "
                    "client_id=%s shipment_id=%s dhl_package_id=%s",
                    client.id,
                    shipment_id,
                    dhl_package_id
                )
                return jsonify({
                    "success": False,
                    "message": "DHL etiketi iptal edilemedi ve yerel kayıt güncellenemedi"
                }), 500

        try:
            log = ShipmentLog(
                client_id=client.id,
                action="dhl_int_cancel_label TEST" if is_test_label else "dhl_int_cancel_label",
                reference_id=shipment_id,
                status="cancelled_local",
                response=str(attempts)
            )
            db.session.add(log)
            db.session.commit()
        except:
            db.session.rollback()

        return jsonify({
            "success": True,
            "message": (
                "Label cancelled locally. DHL's Label API does not support "
                "voiding international labels, so the label was not voided on "
                "DHL's side. Do not use this label."
            ),
            "shipmentId": shipment_id,
            "dhlPackageId": dhl_package_id,
            "dhlVoided": False,
            "localCancelled": True,
            "attempts": attempts
        }), 200

    # 3. DHL Başarısız ise DB'ye HİÇ DOKUNMA
    if status_code not in (200, 204):
        try:
            log = ShipmentLog(
                client_id=client.id,
                action="dhl_int_cancel_label TEST" if is_test_label else "dhl_int_cancel_label",
                reference_id=shipment_id,
                status="failed",
                response=str(attempts)
            )
            db.session.add(log)
            db.session.commit()
        except:
            db.session.rollback()

        return jsonify({
            "success": False,
            "message": "DHL tarafında iptal başarısız oldu",
            "dhl_response": response_data,
            "attempts": attempts
        }), status_code

    if label:
        try:
            label.status = "refunded"
            label.refunded_at = datetime.utcnow()
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            app.logger.exception(
                "DHL Int label voided but local DB could not be updated. "
                "client_id=%s shipment_id=%s dhl_package_id=%s",
                client.id,
                shipment_id,
                dhl_package_id
            )
            return jsonify({"success": False, "message": "DHL iptal edildi fakat DB güncellenemedi"}), 500

    try:
        log = ShipmentLog(
            client_id=client.id,
            action="dhl_int_cancel_label TEST" if is_test_label else "dhl_int_cancel_label",
            reference_id=shipment_id,
            status="success",
            response=str(attempts)
        )
        db.session.add(log)
        db.session.commit()
    except:
        db.session.rollback()

    return jsonify({
        "success": True,
        "message": "Label cancelled successfully",
        "shipmentId": shipment_id,
        "dhlPackageId": dhl_package_id,
        "dhlVoided": True,
        "localCancelled": False,
        "voidedBy": used_identifier
    }), 200
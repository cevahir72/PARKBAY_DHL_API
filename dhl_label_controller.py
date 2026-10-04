from flask_app import app, db, client_token_required
from flask import request, jsonify, g
from model import ShipmentLog, Label
import time
import requests
from data_helper import dhl_request_helper
from dhl_service import get_dhl_token
from decimal import Decimal
from calculator import calculate_shipping_cost
from datetime import datetime


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


# 🔹 DHL REQUEST
def dhl_request(endpoint, payload=None, method="POST", is_test=False, params=None):
    # DHL Sandbox ve Production URL yapılandırması
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


# 🔹 DHL RATE LABEL
@app.route("/dhl/label/rate", methods=["POST"])
@client_token_required
def dhl_rate_label():
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

    dhl_payload = dhl_request_helper(data, client.reference_code, "rate")
        
    # İstekten servis tipini alıyoruz (Varsayılan GND)
    ordered_product_id = data.get("orderedProductId", "EXP")

    #mapped_rate, mapped_message = 0,""
    mapped_rate, mapped_message = calculate_shipping_cost(
        client.id,
        data["shipFrom"]["postalCode"],
        data["shipTo"]["postalCode"],
        data["packages"][0]["weight"]["value"],
        'DHL',
        service=ordered_product_id
    )
    if not mapped_rate:
        return jsonify({"errors": [
            {
                "code": "ZoneWeight",
                "details": mapped_message,
                "message": "Zone Weight"
            }
        ]}), 400

    is_test_label = data.get("isTestLabel", False)
    
    # DHL Rate endpoint çağrısı (shipping/v4/products)
    status_code, response_data = dhl_request(
        endpoint="shipping/v4/products",
        payload=dhl_payload,
        method="POST",
        is_test=is_test_label
    )

    response_value = None
    response_to_return = response_data

    # 📦 LOG ve Yanıtı Düzenleme
    try:
        if status_code == 200:
            products = response_data.get("products", [])
            if products:
                first_product = products[0]
                total_charge = first_product.get("rate", {}).get("amount")

                if total_charge is not None:
                    total_charge = float(total_charge)
                    mapped_rate = float(mapped_rate)

                    if total_charge > mapped_rate:
                        mapped_rate = total_charge + 0.50
                    mapped_rate += float(client.com_fee)

                    # Ürünün fiyatını güncellenmiş tutarla değiştiriyoruz
                    first_product["rate"]["amount"] = round(mapped_rate, 2)
                    #first_product["rate"]["OrijinalAmount"] = round(total_charge, 2)

                first_product.get("rate").pop("rateComponents", None)



                response_value = str(first_product.get("rate"))

                # Sadece istenen ilk ürün objesini yanıt olarak belirliyoruz
                response_to_return = first_product
        else:
            response_value = str(response_data)

        log = ShipmentLog(
            client_id=client.id,
            action="dhl_rate_label TEST" if is_test_label else "dhl_rate_label",
            reference_id=response_data.get("pickup"),
            status="success" if status_code == 200 else "failed",
            response=response_value,
        )

        db.session.add(log)
        db.session.commit()

    except Exception as e:
        db.session.rollback()

    return jsonify(response_to_return), status_code



# 🔹 DHL CREATE LABEL
@app.route("/dhl/label/create", methods=["POST"])
@client_token_required
def dhl_create_label():
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

    rate_payload = dhl_request_helper(data, client.reference_code, "rate")
    is_test_label = data.get("isTestLabel", False)

    # 🔍 DEBUG: rate_payload'ı konsola yazdır
    print("=" * 60)
    print("DHL PAYLOAD (create - rate call):")
    import json
    print(json.dumps(rate_payload, indent=2, ensure_ascii=False, default=str))
    print("=" * 60)

    #mapped_rate, mapped_message = 0,""
    mapped_rate, mapped_message = calculate_shipping_cost(
        client.id,
        data["shipFrom"]["postalCode"],
        data["shipTo"]["postalCode"],
        data["packages"][0]["weight"]["value"],
        'DHL'
    )
    if not mapped_rate:
        return jsonify({"errors": [
            {
                "code": "ZoneWeight",
                "details": mapped_message,
                "message": "Zone Weight"
            }
        ]}), 400

    # DHL Rate endpoint çağrısı (shipping/v4/products)
    rate_status_code, rate_response_data = dhl_request(
        endpoint="shipping/v4/products",
        payload=rate_payload,
        method="POST",
        is_test=is_test_label
    )

    response_value = None
    dhl_orijinal_rate= 0
    try:
        if rate_status_code == 200:
            products = rate_response_data.get("products", [])
            if products:
                # first_product = products[0]
                # İstediğimiz servisi belirliyoruz (GND veya EXP)
                ordered_product_id = data.get("orderedProductId", "EXP")
                # 1. Dönen listede aradığımız servisi bul (Index sırasından bağımsız olarak)
                target_product = next((p for p in products if p.get("productId") == ordered_product_id), None)
            
                # 2. Eğer aranan servis listede yoksa, sistem çökmesin diye fallback olarak ilkini al
                if not target_product:
                    target_product = products[0]
                   
                total_charge = target_product.get("rate", {}).get("amount")

                if total_charge is not None:
                    total_charge = float(total_charge)
                    mapped_rate = float(mapped_rate)

                    if total_charge > mapped_rate:
                        mapped_rate = total_charge + 0.50
                    mapped_rate += float(client.com_fee)
                    mapped_rate = round(mapped_rate, 2)
                    # Ürünün fiyatını güncellenmiş tutarla değiştiriyoruz
                    # -----------------------------------------------------
                    # Ürünün fiyatını güncellenmiş tutarla değiştiriyoruz
                    target_product["rate"]["amount"] = mapped_rate
                    # create_label'da orijinal fiyatı saklıyordunuz:
                    # dhl_orijinal_rate = round(total_charge, 2)

                target_product.get("rate").pop("rateComponents", None)

                response_value = str(target_product.get("rate"))

                # Yanıt olarak sadece filtrelediğimiz ürünü döndürüyoruz
                response_to_return = target_product
        else:
            response_value = str(rate_response_data)

            log = ShipmentLog(
                client_id=client.id,
                action="dhl_rate_label TEST" if is_test_label else "dhl_rate_label",
                reference_id=rate_response_data.get("pickup"),
                status="success" if rate_status_code == 200 else "failed",
                response=response_value,
            )

            db.session.add(log)
            db.session.commit()

    except Exception as e:
        db.session.rollback()


    dhl_payload = dhl_request_helper(data, client.reference_code, "create")

    # 🔍 DEBUG: create payload'ı konsola yazdır
    print("=" * 60)
    print("DHL PAYLOAD (create - label call):")
    import json
    print(json.dumps(dhl_payload, indent=2, ensure_ascii=False, default=str))
    print("=" * 60)

    # DHL create label endpoint çağrısı (shipping/v4/label?format=PNG)
    status_code, response_data = dhl_request(
        endpoint="shipping/v4/label",
        payload=dhl_payload,
        method="POST",
        is_test=is_test_label,
        params={"format": "PNG"}
    )

    response_value = None
    response_to_return = response_data
    shipment_id=""
    # ---------------------------------------------------------
    # 1. DHL BAŞARISIZ
    # ---------------------------------------------------------

    if status_code != 200:
        response_value = str(response_to_return)

        # DHL başarısız olduğunda yalnızca başarısız log tutulur.
        try:
            log = ShipmentLog(
                client_id=client.id,
                action="dhl_create_label TEST"if is_test_label else "dhl_create_label",
                reference_id=dhl_payload.get("packageDetail", {}).get("packageId"),
                status="failed",
                response=response_value,
            )

            db.session.add(log)
            db.session.commit()

        except Exception as e:
            db.session.rollback()

            app.logger.exception(
                "DHL label failure log could not be saved. "
                "client_id=%s package_id=%s",
                client.id,
                dhl_payload.get("packageDetail", {}).get("packageId")
            )

        # DHL'nin hata cevabı değiştirilmeden kullanıcıya dönülür.
        return jsonify(response_to_return), status_code
    # ---------------------------------------------------------
    # 2. DHL BAŞARILI
    # ---------------------------------------------------------

    labels = response_to_return.get("labels", [])

    # Şimdilik ana başarı kontrolü status_code == 200.
    # Ancak kodun labels[0] satırında patlamaması için boş liste
    # kontrolü yapılıyor.
    if not labels:
        app.logger.error(
            "DHL returned 200 but labels is empty. "
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

    # DB işlemlerinde ve hata durumunda kullanmak için
    # DHL bilgileri response değiştirilmeden önce alınıyor.
    shipment_id = label.get("packageId")
    tracking_number = label.get("dhlPackageId")

    # Kullanıcıya dönecek response hazırlanıyor.
    response_to_return.pop("pickup", None)

    label.pop("labelDetail", None)
    label.pop("link", None)

    label["totalCharge"] = {
        "unit": "USD",
        "value": mapped_rate
    }

    label["shipmentId"] = shipment_id
    label.pop("packageId", None)

    record = {
        "totalCharge": Decimal(str(round(mapped_rate, 2))),
        "trackingId": tracking_number,
        "shipmentId": shipment_id
    }

    response_value = str(record)

    # ---------------------------------------------------------
    # 3. DHL BAŞARILI, YEREL DB KAYDI
    # ---------------------------------------------------------

    try:
        log = ShipmentLog(
            client_id=client.id,
            action="dhl_create_label TEST"if is_test_label else "dhl_create_label",
            reference_id=dhl_payload.get("packageDetail", {}).get("packageId"),
            status="success",
            response=response_value,
        )

        db.session.add(log)

        if not is_test_label:
            ship_from = data.get("shipFrom", {})

            fields = [
                ship_from.get("name"),
                ship_from.get("addressLine1"),
                ship_from.get("stateOrRegion"),
                ship_from.get("city"),
                ship_from.get("countryCode"),
                ship_from.get("postalCode")
            ]

            from_address = ", ".join(str(f) for f in fields if f )

            label_record = Label(
                client_id=client.id,
                pickup_id=dhl_payload.get("pickup"),
                created_at=datetime.utcnow(),
                reference_code=client.reference_code,
                status="purchased",
                market_place=marketPlace,
                from_address=from_address,
                from_zip=data.get("shipFrom", {}).get("postalCode"),
                to_zip=data.get("shipTo", {}).get("postalCode"),
                weight=data.get("packages", [{}])[0].get("weight", {}).get("value"),
                customer_email=data.get("shipTo", {}).get("email"),
                customer_phone=data.get("shipTo", {}).get("phoneNumber"),
                shipment_id=shipment_id,
                carrier="DHL",
                service=response_to_return.get("orderedProductId","DHL_Standard"),
                tracking_number=tracking_number,
                amz_cost=Decimal(str(dhl_orijinal_rate)),
                my_cost=Decimal(str(round(mapped_rate, 2)))
            )

            db.session.add(label_record)

        # Başarılı log ile Label aynı transaction içinde kaydedilir.
        db.session.commit()

    # ---------------------------------------------------------
    # 4. DHL BAŞARILI FAKAT DB BAŞARISIZ
    # ---------------------------------------------------------

    except Exception as e:
        db.session.rollback()

        app.logger.exception(
            "DHL label created but local database save failed. "
            "client_id=%s package_id=%s shipment_id=%s "
            "tracking_number=%s",
            client.id,
            dhl_payload.get("packageDetail", {}).get("packageId"),
            shipment_id,
            tracking_number
        )

        # İlk transaction rollback olduğu için recovery kaydı
        # ayrı bir transaction ile tekrar kaydedilmeye çalışılır.
        try:
            recovery_log = ShipmentLog(
                client_id=client.id,
                action="dhl_create_label_db_failed",
                reference_id=dhl_payload.get("packageDetail", {}).get("packageId"),
                status="failed",
                response=str({
                    "message": (
                        "DHL label was created but local "
                        "database save failed."
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
                "DHL recovery log could not be saved. "
                "client_id=%s package_id=%s shipment_id=%s "
                "tracking_number=%s",
                client.id,
                dhl_payload.get("packageDetail", {}).get("packageId"),
                shipment_id,
                tracking_number
            )

        # DHL başarılı olduğu için label bilgileri kaybedilmez,
        # fakat normal 200 cevabı da dönülmez.
        return jsonify({
            "errors": [
                {
                    "code": "LabelPersistenceError",
                    "details": (
                        "The DHL label was created successfully, "
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

    # Hem DHL hem de DB işlemi başarılı.
    return jsonify(response_to_return), status_code


# 🔹 DHL LABEL CANCEL ENDPOINT
@app.route("/dhl/label/cancel", methods=["POST"])
@client_token_required
def dhl_cancel_label():
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
    #shipment_id = data.get("shipment_id")
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
        label = Label.query.filter_by(shipment_id=shipment_id, client_id=client.id).first()

        if not label:
            return jsonify({"success": False, "message": "Label not found"}), 404

        if label.status == "refunded":
            return jsonify({"success": False, "message": "Label was canceled before"}), 400


    pickup_id = label.pickup_id if label else shipment_id

    # DHL proxy isteği
    status_code, response_data = dhl_request(
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

            db.session.commit() # Commit tam olarak burada olmalı
        except Exception as e:
            db.session.rollback()
            return jsonify({"success": False, "message": "DHL iptal edildi fakat DB güncellenemedi"}), 500

    try:
        log = ShipmentLog(
            client_id=client.id,
            action="dhl_cancel_label TEST" if is_test_label else "dhl_cancel_label",
            reference_id=shipment_id,
            status="success" if status_code in (200, 204) else "failed",
            response=str(response_data)
        )
        db.session.add(log)
        db.session.commit()
    except:
        db.session.rollback()

    return jsonify(response_data), status_code


# 🔹 DHL MANIFEST CREATE ENDPOINT
@app.route("/dhl/manifest/create", methods=["POST"])
@client_token_required
def dhl_create_manifest():
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

    from_postal_code = data.get("fromPostalCode")
    manifests = data.get("manifests")

    if not from_postal_code:
        return jsonify({"errors": [
            {
                "code": "InvalidInput",
                "details": "fromPostalCode input is required",
                "message": "Invalid Input"
            }
        ]}), 400

    if not manifests:
        return jsonify({"errors": [
            {
                "code": "InvalidInput",
                "details": "manifests input is required",
                "message": "Invalid Input"
            }
        ]}), 400

    # Burada fromPostalCode'a göre pickup bulunacak.
    # Bu kısmı mevcut pickup yapına göre düzenleriz.
    pickup_dict = {"75081-1955":"5410014","75243-5485":"5410429","77407-9012": "5410448"}
    pickup_id = pickup_dict.get(str(from_postal_code))

    if not pickup_id:
        return jsonify({
            "success": False,
            "message": "Pickup not found"
        }), 404

    # DHL'e gidecek gerçek payload
    payload = {
        "pickup": str(pickup_id),
        "manifests": manifests
    }

    status_code, response_data = dhl_request(
        endpoint="shipping/v4/manifest/",
        payload=payload,
        method="POST"
    )

    # DHL başarısızsa direkt cevabı döndür
    if status_code not in (200, 201):
        return jsonify({
            "success": False,
            "message": "DHL manifest creation failed",
            "dhl_response": response_data
        }), status_code

    # Log
    try:
        log = ShipmentLog(
            client_id=client.id,
            action="dhl_create_manifest",
            reference_id=str(response_data.requestId),
            status="success",
            response=str(response_data)
        )

        db.session.add(log)
        db.session.commit()

    except:
        db.session.rollback()

    # DHL ne döndüyse aynen client'a dön
    return jsonify(response_data), status_code


# 🔹 DHL MANIFEST DOWNLOAD ENDPOINT
@app.route("/dhl/manifest/download", methods=["POST"])
@client_token_required
def dhl_download_manifest():
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


    request_id = data.get("requestId")
    from_postal_code = data.get("fromPostalCode")


    if not from_postal_code:
        return jsonify({"errors": [
            {
                "code": "InvalidInput",
                "details": "fromPostalCode input is required",
                "message": "Invalid Input"
            }
        ]}), 400

    if not request_id:
        return jsonify({"errors": [
            {
                "code": "InvalidInput",
                "details": "requestId input is required",
                "message": "Invalid Input"
            }
        ]}), 400

    pickup_dict = {"75081-1955":"5410014","75243-5485":"5410429","77407-9012": "5410448"}
    pickup_id = pickup_dict.get(str(from_postal_code))

    if not pickup_id:
        return jsonify({
            "success": False,
            "message": "Pickup not found"
        }), 404

    # DHL tarafı GET olarak kalıyor
    status_code, response_data = dhl_request(
        endpoint=f"shipping/v4/manifest/{pickup_id}/{request_id}",
        payload=None,
        method="GET"
    )

    # DHL başarısızsa direkt cevabı döndür
    if status_code not in (200, 201):
        return jsonify({
            "success": False,
            "message": "DHL manifest download failed",
            "dhl_response": response_data
        }), status_code

    response_to_return = response_data
    response_to_return.pop("pickup")
    record = {
        "requestId": response_to_return.get("requestId"),
        "link": response_to_return.get("link"),
        "status": response_to_return.get("status")
    }
    try:
        log = ShipmentLog(
            client_id=client.id,
            action="dhl_download_manifest",
            reference_id=request_id,
            status="success" if status_code == 200 else "failed",
            response=str(record)
        )

        db.session.add(log)
        db.session.commit()

    except:
        db.session.rollback()

    return jsonify(response_to_return), status_code
# easyship_service.py
"""
Easyship Taxes & Duties API entegrasyonu (sunucu tarafı).

Müşteriler Easyship'i bilmez; bearer token ve istek gövdesi API içinde
(admin tarafında) yönetilir. Bu servis DHL International label isteğinden
Easyship `taxes_and_duties` gövdesini üretir, Easyship'ten gelen duty + tax
toplamını `customs_value` olarak döner.

EASYSHIP_API_KEY .env / ortam değişkenlerinden okunur.
EASYSHIP_BASE_URL ile base URL değiştirilebilir.

Endpoint:
- POST https://public-api.easyship.com/2024-09/taxes_and_duties

Üretilen gövde:
{
  "destination_country_alpha2": "DE",
  "origin_country_alpha2": "US",
  "insurance_fee": 0,
  "shipment_charge": 15.0,
  "currency": "USD",
  "items": [
    {
      "duty_origin_country_alpha2": "US",
      "hs_code": "85183000",
      "customs_value": 29.99
    }
  ]
}
"""

import os
import re
import requests


_API_VERSION = "2024-09"
_DEFAULT_BASE_URL = "https://public-api.easyship.com"


def _get_api_key():
    return (os.getenv("EASYSHIP_API_KEY") or "").strip()


def _get_base_url():
    base_url = (os.getenv("EASYSHIP_BASE_URL") or "").strip()
    return (base_url or _DEFAULT_BASE_URL).rstrip("/")


def _headers(api_key):
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _request(api_key, body):
    url = f"{_get_base_url()}/{_API_VERSION}/taxes_and_duties"

    return requests.post(
        url,
        headers=_headers(api_key),
        json=body,
        timeout=(5, 15),
    )


def _normalize_alpha2(value):
    value = str(value or "").strip().upper()
    if len(value) != 2 or not value.isalpha():
        return None
    return value


def _normalize_hs_code(value):
    """Easyship 8 veya 10 haneli HS kodu bekler."""
    digits = re.sub(r"\D", "", str(value or ""))

    # DHL 6 haneli HS kodu gönderebilir; Easyship son iki hanenin 00 olmasını ister.
    if len(digits) == 6:
        digits += "00"

    if len(digits) in (8, 10):
        return digits

    return None


def _to_float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_taxes_items(client_json):
    """
    DHL label isteğindeki packages[].items verisini Easyship formatına çevirir.

    Aynı HS kodu + menşe ülke kombinasyonundaki ürünler tek satırda toplanır.
    Dönen değer: (items, currency)
    """
    ship_from = client_json.get("shipFrom") or {}
    fallback_origin = _normalize_alpha2(ship_from.get("countryCode"))

    grouped_values = {}
    currency = None

    for package in client_json.get("packages") or []:
        for item in package.get("items") or []:
            hs_code = _normalize_hs_code(
                item.get("hsCode") or item.get("itemIdentifier")
            )
            origin_country = (
                _normalize_alpha2(item.get("originCountry")) or fallback_origin
            )

            item_value = _to_float(
                (item.get("itemValue") or {}).get("value")
            )

            if not hs_code or not origin_country or item_value is None:
                continue

            quantity = _to_float(item.get("quantity", 1))
            if quantity is None or quantity <= 0:
                quantity = 1

            key = (hs_code, origin_country)
            grouped_values[key] = (
                grouped_values.get(key, 0.0) + (item_value * quantity)
            )

            if currency is None:
                currency = (item.get("itemValue") or {}).get("unit")

    items = [
        {
            "duty_origin_country_alpha2": origin_country,
            "hs_code": hs_code,
            "customs_value": round(total_value, 2),
        }
        for (hs_code, origin_country), total_value in grouped_values.items()
    ]

    return items, (str(currency).upper() if currency else "USD")


def build_payload(
    origin_country_alpha2,
    destination_country_alpha2,
    items,
    shipment_charge,
    insurance_fee=0.0,
    currency="USD",
):
    """Easyship taxes_and_duties istek gövdesini üretir."""
    origin_country = _normalize_alpha2(origin_country_alpha2)
    destination_country = _normalize_alpha2(destination_country_alpha2)

    if not origin_country or not destination_country:
        raise ValueError("origin/destination country code is missing or invalid")

    if not items:
        raise ValueError("No valid customs items for Easyship calculation")

    shipment_charge = _to_float(shipment_charge)
    if shipment_charge is None:
        raise ValueError("shipment_charge is not a number")

    insurance_fee = _to_float(insurance_fee)
    if insurance_fee is None:
        insurance_fee = 0.0

    currency = str(currency or "USD").upper()

    payload_items = []
    for item in items:
        item_origin = _normalize_alpha2(item.get("duty_origin_country_alpha2"))
        hs_code = _normalize_hs_code(item.get("hs_code"))
        customs_value = _to_float(item.get("customs_value"))

        if not item_origin or not hs_code or customs_value is None:
            continue

        payload_items.append({
            "duty_origin_country_alpha2": item_origin,
            "hs_code": hs_code,
            "customs_value": customs_value,
        })

    if not payload_items:
        raise ValueError("No valid Easyship items")

    return {
        "destination_country_alpha2": destination_country,
        "origin_country_alpha2": origin_country,
        "insurance_fee": insurance_fee,
        "shipment_charge": shipment_charge,
        "currency": currency,
        "items": payload_items,
    }


def _parse_response(response, fallback_currency):
    try:
        response_data = response.json()
    except ValueError:
        response_data = {"raw_response": response.text}

    if response.status_code != 200:
        error = response_data.get("error") if isinstance(response_data, dict) else None
        message = None
        if isinstance(error, dict):
            message = error.get("message")
            details = error.get("details") or []
            if details:
                message = f"{message}: {'; '.join(str(d) for d in details)}"

        return {
            "success": False,
            "error": message or f"Easyship HTTP {response.status_code}",
            "status_code": response.status_code,
            "api_version": _API_VERSION,
            "raw": response_data,
        }

    tax_and_duty = response_data.get("tax_and_duty") or response_data

    tax = _to_float(tax_and_duty.get("tax"))
    duty = _to_float(tax_and_duty.get("duty"))

    if tax is None and duty is None:
        return {
            "success": False,
            "error": "Easyship response does not contain tax or duty",
            "status_code": response.status_code,
            "api_version": _API_VERSION,
            "raw": response_data,
        }

    tax = tax or 0.0
    duty = duty or 0.0

    return {
        "success": True,
        "tax": round(tax, 2),
        "duty": round(duty, 2),
        # customs_value olarak kaydedilecek toplam gümrük tutarı
        "total": round(tax + duty, 2),
        "currency": tax_and_duty.get("currency") or fallback_currency,
        "request_id": (response_data.get("meta") or {}).get("request_id"),
        "api_version": _API_VERSION,
        "raw": response_data,
    }


def calculate_taxes_and_duties(body):
    """
    Verilen Easyship Taxes & Duties gövdesini gönderir ve sonucu döner.

    Dönen sözlük:
    {
        "success": bool,
        "tax": float,
        "duty": float,
        "total": float,          # tax + duty (customs_value)
        "currency": str,
        "error": str | None,
        ...
    }
    """
    if not isinstance(body, dict) or not body:
        return {
            "success": False,
            "error": "Easyship body must be a non-empty JSON object",
        }

    items = body.get("items")
    if not isinstance(items, list) or not items:
        return {
            "success": False,
            "error": "Easyship body must contain a non-empty items array",
        }

    api_key = _get_api_key()
    if not api_key:
        return {
            "success": False,
            "error": "EASYSHIP_API_KEY is not configured",
        }

    currency = str(body.get("currency") or "USD").upper()

    try:
        response = _request(api_key, body)
    except requests.RequestException as error:
        return {
            "success": False,
            "error": f"Easyship request failed: {error}",
            "api_version": _API_VERSION,
        }

    return _parse_response(response, currency)


def calculate_for_dhl_request(client_json, shipment_charge, insurance_fee=0.0):
    """
    DHL International label isteğinden Easyship Taxes & Duties sonucu üretir.

    Country, HS kodu, customs_value (eşya değeri) ve para birimi DHL
    isteğinden alınır; Easyship kimlik bilgisi sunucudadır.
    """
    ship_from = client_json.get("shipFrom") or {}
    ship_to = client_json.get("shipTo") or {}

    items, currency = build_taxes_items(client_json)

    try:
        body = build_payload(
            origin_country_alpha2=ship_from.get("countryCode"),
            destination_country_alpha2=ship_to.get("countryCode"),
            items=items,
            shipment_charge=shipment_charge,
            insurance_fee=insurance_fee,
            currency=currency,
        )
    except ValueError as error:
        return {"success": False, "error": str(error), "api_version": _API_VERSION}

    return calculate_taxes_and_duties(body)

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
    """Gümrük HS kodunu Easyship'in beklediği 8 haneye normalize eder."""
    digits = re.sub(r"\D", "", str(value or ""))

    # 6 haneli DHL kodu sonuna 00 eklenerek 8 haneye tamamlanır.
    if len(digits) in (6, 8):
        return digits.ljust(8, "0")

    # 10 haneli (veya daha uzun) ulusal kodlar ilk 8 haneye indirilir.
    if len(digits) > 8:
        return digits[:8]

    return None


def _to_float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_dict(value):
    return value if isinstance(value, dict) else {}


# Müşteri yanıtlarında servis sağlayıcı adı geçmesin
_BRAND_PATTERN = re.compile(r"easy\s*ship", re.IGNORECASE)

_ALPHA2_PATTERN = re.compile(r"^[A-Z]{2}$")
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
_HS_CODE_PATTERN = re.compile(r"^\d{8}$")


def public_error(message):
    """Hata mesajını müşteriye gösterilebilir (markasız) hale getirir."""
    return _BRAND_PATTERN.sub("customs tax service", str(message or "")).strip()


def _format_problems(prefix, problems, limit=5):
    message = f"{prefix}: " + "; ".join(problems[:limit])
    if len(problems) > limit:
        message += f" (+{len(problems) - limit} more)"
    return message


def _collect_request_problems(client_json, shipment_charge, insurance_fee):
    """
    DHL isteğinden Easyship gövdesine aktarılacak verileri alan alan
    doğrular. Müşterinin hangi alanda hata yaptığını göstermek için
    problemleri istek alanı yollarıyla toplar.
    """
    ship_from = _as_dict(client_json.get("shipFrom"))
    ship_to = _as_dict(client_json.get("shipTo"))
    problems = []

    if not _normalize_alpha2(ship_from.get("countryCode")):
        problems.append(
            "shipFrom.countryCode must be a 2-letter ISO country code "
            "(e.g. US)"
        )

    if not _normalize_alpha2(ship_to.get("countryCode")):
        problems.append(
            "shipTo.countryCode must be a 2-letter ISO country code "
            "(e.g. DE)"
        )

    charge = _to_float(shipment_charge)
    if charge is None or charge <= 0:
        problems.append("shipment charge (DHL rate) must be greater than 0")

    if insurance_fee not in (None, ""):
        insurance_value = _to_float(insurance_fee)
        if insurance_value is None or insurance_value < 0:
            problems.append("insuranceFee must be a number greater than or equal to 0")

    packages = client_json.get("packages")
    if not isinstance(packages, list) or not packages:
        problems.append("packages must be a non-empty array")
        return problems

    fallback_origin = _normalize_alpha2(ship_from.get("countryCode"))
    currency_codes = set()

    for package_index, package in enumerate(packages):
        package = _as_dict(package)
        items = package.get("items")
        if not isinstance(items, list) or not items:
            problems.append(
                f"packages[{package_index}].items must contain at least one item"
            )
            continue

        for item_index, item in enumerate(items):
            path = f"packages[{package_index}].items[{item_index}]"

            if not isinstance(item, dict):
                problems.append(f"{path} must be an object")
                continue

            item_value_obj = _as_dict(item.get("itemValue"))

            hs_code = _normalize_hs_code(
                item.get("hsCode") or item.get("itemIdentifier")
            )
            if not hs_code:
                problems.append(
                    f"{path}.hsCode must contain 6, 8 or 10 digits"
                )

            origin = _normalize_alpha2(item.get("originCountry"))
            if item.get("originCountry") and not origin:
                problems.append(
                    f"{path}.originCountry must be a 2-letter ISO country "
                    "code (e.g. US, CN)"
                )
            elif not origin and not fallback_origin:
                problems.append(
                    f"{path}.originCountry is required when "
                    "shipFrom.countryCode is not a valid country code"
                )

            item_value = _to_float(item_value_obj.get("value"))
            if item_value is None or item_value <= 0:
                problems.append(
                    f"{path}.itemValue.value must be a number greater than 0"
                )

            quantity = _to_float(item.get("quantity", 1))
            if quantity is None or quantity <= 0:
                problems.append(
                    f"{path}.quantity must be a number greater than 0"
                )

            unit = str(item_value_obj.get("unit") or "").strip().upper()
            if not _CURRENCY_PATTERN.match(unit):
                problems.append(
                    f"{path}.itemValue.unit must be a 3-letter currency "
                    "code (e.g. USD)"
                )
            else:
                currency_codes.add(unit)

    if len(currency_codes) > 1:
        problems.append(
            "All items must use the same currency: "
            + ", ".join(sorted(currency_codes))
        )

    return problems


def validate_taxes_body(body):
    """
    Easyship taxes_and_duties gövdesini alan alan doğrular.

    Dönen liste boşsa gövde geçerlidir. Aksi halde her eleman,
    düzeltilmesi gereken alanı ve beklenen değeri açıklar.
    """
    problems = []

    if not isinstance(body, dict) or not body:
        return ["request body must be a non-empty JSON object"]

    if not _ALPHA2_PATTERN.match(str(body.get("origin_country_alpha2") or "")):
        problems.append(
            "origin_country_alpha2 must be a 2-letter ISO country code "
            "(e.g. US)"
        )

    if not _ALPHA2_PATTERN.match(
        str(body.get("destination_country_alpha2") or "")
    ):
        problems.append(
            "destination_country_alpha2 must be a 2-letter ISO country "
            "code (e.g. DE)"
        )

    insurance_fee = _to_float(body.get("insurance_fee"))
    if insurance_fee is None or insurance_fee < 0:
        problems.append(
            "insurance_fee must be a number greater than or equal to 0"
        )

    shipment_charge = _to_float(body.get("shipment_charge"))
    if shipment_charge is None or shipment_charge <= 0:
        problems.append("shipment_charge must be a number greater than 0")

    if not _CURRENCY_PATTERN.match(str(body.get("currency") or "")):
        problems.append(
            "currency must be a 3-letter currency code (e.g. USD)"
        )

    items = body.get("items")
    if not isinstance(items, list) or not items:
        problems.append("items must be a non-empty array")
        return problems

    for item_index, item in enumerate(items):
        path = f"items[{item_index}]"

        if not isinstance(item, dict):
            problems.append(f"{path} must be an object")
            continue

        if not _ALPHA2_PATTERN.match(
            str(item.get("duty_origin_country_alpha2") or "")
        ):
            problems.append(
                f"{path}.duty_origin_country_alpha2 must be a 2-letter "
                "ISO country code (e.g. US)"
            )

        if not _HS_CODE_PATTERN.match(str(item.get("hs_code") or "")):
            problems.append(
                f"{path}.hs_code must be an 8 digit number (e.g. 85183000)"
            )

        customs_value = _to_float(item.get("customs_value"))
        if customs_value is None or customs_value <= 0:
            problems.append(
                f"{path}.customs_value must be a number greater than 0"
            )

    return problems


def build_taxes_items(client_json):
    """
    DHL label isteğindeki packages[].items verisini Easyship formatına çevirir.

    Aynı HS kodu + menşe ülke kombinasyonundaki ürünler tek satırda toplanır.
    Dönen değer: (items, currency)
    """
    ship_from = _as_dict(client_json.get("shipFrom"))
    fallback_origin = _normalize_alpha2(ship_from.get("countryCode"))

    grouped_values = {}
    currency = None

    for package in client_json.get("packages") or []:
        for item in _as_dict(package).get("items") or []:
            if not isinstance(item, dict):
                continue

            hs_code = _normalize_hs_code(
                item.get("hsCode") or item.get("itemIdentifier")
            )
            origin_country = (
                _normalize_alpha2(item.get("originCountry")) or fallback_origin
            )

            item_value = _to_float(
                _as_dict(item.get("itemValue")).get("value")
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
        raise ValueError(
            "shipFrom.countryCode and shipTo.countryCode must be valid "
            "ISO alpha-2 country codes (e.g. US, DE)"
        )

    if not items:
        raise ValueError(
            "No valid customs items: each item requires hsCode "
            "(6, 8 or 10 digits), originCountry and itemValue.value"
        )

    shipment_charge = _to_float(shipment_charge)
    if shipment_charge is None:
        raise ValueError("shipment charge could not be determined")

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
        raise ValueError(
            "No valid customs items: each item requires hsCode "
            "(6, 8 or 10 digits), originCountry and itemValue.value"
        )

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
        elif isinstance(error, str):
            message = error

        return {
            "success": False,
            "error": public_error(
                message or f"customs tax service HTTP {response.status_code}"
            ),
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
            "error": (
                "customs tax service response does not contain duty or tax "
                "for this shipment"
            ),
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
    Verilen Easyship Taxes & Duties gövdesini doğrular, gönderir ve sonucu
    döner.

    Dönen sözlük:
    {
        "success": bool,
        "tax": float,
        "duty": float,
        "total": float,          # tax + duty (customs_value)
        "currency": str,
        "error": str | None,
        "problems": list[str],   # validasyon hataları (varsa)
        ...
    }
    """
    problems = validate_taxes_body(body)
    if problems:
        return {
            "success": False,
            "error": _format_problems(
                "customs tax request validation failed", problems
            ),
            "problems": problems,
            "api_version": _API_VERSION,
        }

    api_key = _get_api_key()
    if not api_key:
        return {
            "success": False,
            "error": "customs tax service is not configured",
        }

    currency = str(body.get("currency") or "USD").upper()

    try:
        response = _request(api_key, body)
    except requests.RequestException as error:
        return {
            "success": False,
            "error": public_error(f"customs tax request failed: {error}"),
            "api_version": _API_VERSION,
        }

    return _parse_response(response, currency)


def calculate_for_dhl_request(client_json, shipment_charge, insurance_fee=0.0):
    """
    DHL International label isteğinden Easyship Taxes & Duties sonucu üretir.

    Easyship gövdesindeki ülke kodları endpoint gövdesinden alınır:
    - origin_country_alpha2      <- shipFrom.countryCode
    - destination_country_alpha2 <- shipTo.countryCode
    - duty_origin_country_alpha2 <- packages[].items[].originCountry
                                    (yoksa shipFrom.countryCode)

    HS kodu, customs_value (eşya değeri), para birimi ve shipment_charge da
    DHL isteğinden hesaplanır; Easyship kimlik bilgisi sunucudadır.
    """
    problems = _collect_request_problems(
        client_json, shipment_charge, insurance_fee
    )

    if problems:
        return {
            "success": False,
            "error": _format_problems("invalid shipment data", problems),
            "problems": problems,
            "api_version": _API_VERSION,
        }

    ship_from = _as_dict(client_json.get("shipFrom"))
    ship_to = _as_dict(client_json.get("shipTo"))

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
        return {
            "success": False,
            "error": str(error),
            "problems": problems,
            "api_version": _API_VERSION,
        }

    return calculate_taxes_and_duties(body)

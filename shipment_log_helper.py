from decimal import Decimal

from sqlalchemy import func

from data_helper import PICKUP_DICT, normalize_zip
from model import Carrier, Commission


def resolve_carrier_id(service_code, shipment_type):
    carrier = resolve_carrier(service_code, shipment_type)
    return carrier.id if carrier else None


def resolve_carrier(service_code, shipment_type):
    if not service_code or not shipment_type:
        return None

    carrier_query = Carrier.query.filter(
        func.lower(func.trim(Carrier.international))
        == str(shipment_type).strip().lower(),
    )
    carrier = carrier_query.filter(
        func.upper(func.trim(Carrier.name)) == str(service_code).strip().upper(),
    ).first()
    if carrier is None:
        matching_carriers = carrier_query.limit(2).all()
        if len(matching_carriers) == 1:
            carrier = matching_carriers[0]
    return carrier


def get_client_commission(client_id, carrier_id):
    if carrier_id is None:
        return None
    return Commission.query.filter_by(
        client_id=client_id,
        carrier_id=carrier_id,
    ).first()


def shipment_log_fields(
    data,
    carrier_id=None,
    customs_fee=None,
    shipping_fee=None,
    warehouse_zip_code=None,
):
    data = data if isinstance(data, dict) else {}
    ship_to = data.get("shipTo") or {}
    ship_from = data.get("shipFrom") or {}
    source_zip = (
        warehouse_zip_code
        if warehouse_zip_code is not None
        else ship_from.get("postalCode")
    )
    normalized_zip = normalize_zip(source_zip)
    if normalized_zip not in PICKUP_DICT and normalized_zip:
        zip5 = normalized_zip.split("-", 1)[0]
        matching_zips = [
            configured_zip
            for configured_zip in PICKUP_DICT
            if configured_zip.split("-", 1)[0] == zip5
        ]
        if len(matching_zips) == 1:
            normalized_zip = matching_zips[0]

    fields = {
        "zip_code": ship_to.get("postalCode"),
        "city": ship_to.get("city"),
        "country": ship_to.get("countryCode"),
        "warehouse_zip_code": normalized_zip or source_zip,
        "carrier_id": carrier_id,
    }

    for field, value in (
        ("customs_fee", customs_fee),
        ("shipping_fee", shipping_fee),
    ):
        if value is not None:
            fields[field] = Decimal(str(value))

    return fields


def dhl_products_shipping_fee(response_data):
    if not isinstance(response_data, dict):
        return None

    products = response_data.get("products") or response_data.get("rates") or []
    if not products or not isinstance(products[0], dict):
        return None

    first_product = products[0]
    rate = first_product.get("rate") or {}
    total_charge = first_product.get("totalCharge") or {}
    return rate.get("amount", total_charge.get("value"))

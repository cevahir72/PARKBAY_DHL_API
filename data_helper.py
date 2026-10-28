from datetime import datetime
import logging
import time

logger = logging.getLogger(__name__)

def add_if(payload, key, value):
    if value is None:
        return
    if isinstance(value, dict) and not value:
        return
    payload[key] = value


_OZ_PER_LB = 16.0
_OZ_UNITS = ("OZ", "OUNCE", "OUNCES")

# Warehouse (shipFrom.postalCode) -> DHL pickup / distribution center
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


def normalize_zip(postal_code):
    """
    Warehouse ZIP kodunu karşılaştırılabilir biçime getirir.

    Kabul edilen biçimler:
      "77407-9012", "774079012", "77407 9012", " 77407-9012 " -> "77407-9012"
      "77407"                                                  -> "77407"
    """
    if postal_code is None:
        return None

    zip_code = str(postal_code).strip().upper().replace(" ", "")
    if not zip_code:
        return None

    # 774079012 -> 77407-9012
    if zip_code.isdigit() and len(zip_code) == 9:
        return f"{zip_code[:5]}-{zip_code[5:]}"

    # 77407_9012 gibi ayraç farklılıklarını normalize et
    if len(zip_code) == 10 and zip_code[5] in "-_":
        return f"{zip_code[:5]}-{zip_code[6:]}"

    return zip_code


def warehouse_value(mapping, postal_code):
    """
    ZIP / ZIP+4 farklarını tolere ederek pickup veya distribution center
    değerini bulur.

    Örnek:
      "77407-9012" -> exact match
      "77407"      -> 5 haneli ZIP, "77407-9012" kaydıyla eşleşir
      "774079012"  -> "77407-9012" olarak normalize edilir

    Eşleşme yoksa None döner; DHL'e eksik payload gönderilmemelidir.
    """
    zip_code = normalize_zip(postal_code)
    if not zip_code:
        return None

    if zip_code in mapping:
        return mapping[zip_code]

    zip5 = zip_code.split("-", 1)[0]

    # 5 haneli ZIP doğrudan anahtar olarak tanımlıysa onu kullan
    if zip5 in mapping:
        return mapping[zip5]

    # ZIP+4 gönderilmiş, haritada 5 haneli kayıt var; veya tersi
    for key, value in mapping.items():
        if normalize_zip(key).split("-", 1)[0] == zip5:
            return value

    return None


def missing_warehouse_fields(payload):
    """DHL payload'ında zorunlu pickup / distributionCenter alanlarını kontrol eder."""
    if not isinstance(payload, dict):
        return ["pickup", "distributionCenter"]

    return [key for key in ("pickup", "distributionCenter") if not payload.get(key)]


def normalize_oz_weight(weight):
    """
    OZ cinsinden ağırlığı DHL'e uygun birime çevirir.

    - 1 lb'nin altındaki değerler OZ olarak kalır (lb'ye çevrilmez).
    - 1 lb ve üzerindeki değerler LB'ye çevrilir.
    - Diğer birimler (LB, KG, ...) olduğu gibi bırakılır.
    """
    if not isinstance(weight, dict):
        return weight

    unit = str(weight.get("unit") or "").strip().upper()
    if unit not in _OZ_UNITS:
        return weight

    try:
        value = float(weight.get("value"))
    except (TypeError, ValueError):
        return weight

    if value < _OZ_PER_LB:
        weight["unit"] = "OZ"
    else:
        weight["unit"] = "LB"
        weight["value"] = round(value / _OZ_PER_LB, 2)

    return weight


def normalize_package_weights(client_json):
    """
    İstekteki paket ve ürün ağırlıklarına OZ kuralını uygular.
    İsteği yerinde (in-place) günceller ve geri döndürür.
    """
    for package in client_json.get("packages") or []:
        normalize_oz_weight(package.get("weight"))

        for item in package.get("items") or []:
            normalize_oz_weight(item.get("weight"))

    return client_json


def request_helper(client_json, client_id, r_type):
    payload = {}
    ship_to = {}
    add_if(ship_to, "name", client_json.get("shipTo", {}).get("name"))
    add_if(ship_to, "addressLine1", client_json.get("shipTo", {}).get("addressLine1"))
    add_if(ship_to, "addressLine2", client_json.get("shipTo", {}).get("addressLine2"))
    add_if(ship_to, "addressLine3", client_json.get("shipTo", {}).get("addressLine3"))
    add_if(ship_to, "companyName", client_json.get("shipTo", {}).get("companyName"))
    add_if(ship_to, "stateOrRegion", client_json.get("shipTo", {}).get("stateOrRegion"))
    add_if(ship_to, "city", client_json.get("shipTo", {}).get("city"))
    add_if(ship_to, "countryCode", client_json.get("shipTo", {}).get("countryCode"))
    add_if(ship_to, "postalCode", client_json.get("shipTo", {}).get("postalCode"))
    add_if(ship_to, "email", client_json.get("shipTo", {}).get("email"))
    add_if(ship_to, "phoneNumber", client_json.get("shipTo", {}).get("phoneNumber"))
    add_if(ship_to, "geocode", client_json.get("shipTo", {}).get("geocode"))
    add_if(payload, "shipTo", ship_to)
    ship_from = {}
    add_if(ship_from, "name", client_json.get("shipFrom", {}).get("name"))
    add_if(ship_from, "addressLine1", client_json.get("shipFrom", {}).get("addressLine1"))
    add_if(ship_from, "addressLine2", client_json.get("shipFrom", {}).get("addressLine2"))
    add_if(ship_from, "addressLine3", client_json.get("shipFrom", {}).get("addressLine3"))
    add_if(ship_from, "companyName", client_json.get("shipFrom", {}).get("companyName"))
    add_if(ship_from, "stateOrRegion", client_json.get("shipFrom", {}).get("stateOrRegion"))
    add_if(ship_from, "city", client_json.get("shipFrom", {}).get("city"))
    add_if(ship_from, "countryCode", client_json.get("shipFrom", {}).get("countryCode"))
    add_if(ship_from, "postalCode", client_json.get("shipFrom", {}).get("postalCode"))
    add_if(ship_from, "email", client_json.get("shipFrom", {}).get("email"))
    add_if(ship_from, "phoneNumber", client_json.get("shipFrom", {}).get("phoneNumber"))
    add_if(ship_from, "geocode", client_json.get("shipFrom", {}).get("geocode"))
    add_if(payload, "shipFrom", ship_from)
    return_to = {}
    add_if(return_to, "name", client_json.get("returnTo", {}).get("name"))
    add_if(return_to, "addressLine1", client_json.get("returnTo", {}).get("addressLine1"))
    add_if(return_to, "addressLine2", client_json.get("returnTo", {}).get("addressLine2"))
    add_if(return_to, "addressLine3", client_json.get("returnTo", {}).get("addressLine3"))
    add_if(return_to, "companyName", client_json.get("returnTo", {}).get("companyName"))
    add_if(return_to, "stateOrRegion", client_json.get("returnTo", {}).get("stateOrRegion"))
    add_if(return_to, "city", client_json.get("returnTo", {}).get("city"))
    add_if(return_to, "countryCode", client_json.get("returnTo", {}).get("countryCode"))
    add_if(return_to, "postalCode", client_json.get("returnTo", {}).get("postalCode"))
    add_if(return_to, "email", client_json.get("returnTo", {}).get("email"))
    add_if(return_to, "phoneNumber", client_json.get("returnTo", {}).get("phoneNumber"))
    add_if(return_to, "geocode", client_json.get("returnTo", {}).get("geocode"))
    add_if(payload, "returnTo", return_to)

    if not client_json.get("returnTo"):
        add_if(payload, "returnTo", ship_from)

    add_if(payload, "shipDate", client_json.get("shipDate"))
    add_if(payload, "quoteForShipperNumber", client_json.get("quoteForShipperNumber"))
    add_if(payload, "shipmentForShipperNumber", client_json.get("shipmentForShipperNumber"))
    add_if(payload, "shipperInstruction", client_json.get("shipperInstruction"))
    packages = []
    for package in client_json.get("packages", []):
        package_dict = {}

        dimensions = {}
        add_if(dimensions, "length", package.get("dimensions", {}).get("length"))
        add_if(dimensions, "width", package.get("dimensions", {}).get("width"))
        add_if(dimensions, "height", package.get("dimensions", {}).get("height"))
        add_if(dimensions, "unit", package.get("dimensions", {}).get("unit"))
        add_if(package_dict, "dimensions", dimensions)

        weight = {}
        add_if(weight, "unit", package.get("weight", {}).get("unit"))
        add_if(weight, "value", package.get("weight", {}).get("value"))
        add_if(package_dict, "weight", weight)

        insuredValue = {}
        add_if(insuredValue, "value", package.get("insuredValue", {}).get("value", 0.0))
        add_if(insuredValue, "unit", package.get("insuredValue", {}).get("unit", "USD"))
        add_if(package_dict, "insuredValue", insuredValue)

        add_if(package_dict, "isHazmat", package.get("isHazmat", False))
        add_if(package_dict, "sellerDisplayName", package.get("sellerDisplayName"))
        #charges = [] belki sonra
        add_if(package_dict, "charges", package.get("charges"))
        add_if(package_dict, "packageClientReferenceId", str(client_id))
        #additionalPackageInfo = [] belki sonra
        add_if(package_dict, "additionalPackageInfo", package.get("additionalPackageInfo"))

        has_no_items = True
        package_dict["items"] = []
        for item in package.get("items", []):
            has_no_items = False
            item_dict =  {}

            itemValue = {}
            add_if(itemValue, "value", item.get("itemValue", {}).get("value"))
            add_if(itemValue, "unit", item.get("itemValue", {}).get("unit"))
            add_if(item_dict, "itemValue", itemValue)
            add_if(item_dict, "description", item.get("description"))
            add_if(item_dict, "itemIdentifier", item.get("itemIdentifier"))
            add_if(item_dict, "quantity", item.get("quantity"))
            weight = {}
            add_if(weight, "value", item.get("weight", {}).get("value"))
            add_if(weight, "unit", item.get("weight", {}).get("unit"))
            add_if(item_dict, "weight", weight)
            liquidVolume = {}
            add_if(liquidVolume, "value", item.get("liquidVolume", {}).get("value"))
            add_if(liquidVolume, "unit", item.get("liquidVolume", {}).get("unit"))
            add_if(item_dict, "liquidVolume", liquidVolume)

            add_if(item_dict, "isHazmat", item.get("isHazmat", False))

            dangerousGoodsDetails = {}
            add_if(dangerousGoodsDetails, "unitedNationsRegulatoryId", item.get("dangerousGoodsDetails", {}).get("unitedNationsRegulatoryId"))
            add_if(dangerousGoodsDetails, "transportationRegulatoryClass", item.get("dangerousGoodsDetails", {}).get("transportationRegulatoryClass"))
            add_if(dangerousGoodsDetails, "packingGroup", item.get("dangerousGoodsDetails", {}).get("packingGroup"))
            add_if(dangerousGoodsDetails, "packingInstruction", item.get("dangerousGoodsDetails", {}).get("packingInstruction"))
            add_if(item_dict, "dangerousGoodsDetails", dangerousGoodsDetails)

            add_if(item_dict, "productType", item.get("productType"))

            invoiceDetails = {}
            add_if(invoiceDetails, "invoiceNumber", item.get("invoiceDetails", {}).get("invoiceNumber"))
            add_if(invoiceDetails, "invoiceDate", item.get("invoiceDetails", {}).get("invoiceDate"))
            add_if(item_dict, "invoiceDetails", invoiceDetails)

            add_if(item_dict, "serialNumbers", item.get("serialNumbers"))
            directFulfillmentItemIdentifiers = {}
            add_if(directFulfillmentItemIdentifiers, "lineItemID", item.get("directFulfillmentItemIdentifiers", {}).get("lineItemID"))
            add_if(directFulfillmentItemIdentifiers, "pieceNumber", item.get("directFulfillmentItemIdentifiers", {}).get("pieceNumber"))
            add_if(item_dict, "directFulfillmentItemIdentifiers", directFulfillmentItemIdentifiers)

            package_dict["items"].append(item_dict)

        if has_no_items:
            package_dict["items"] = [{"quantity": 1, "weight": weight}]
        packages.append(package_dict)
    add_if(payload, "packages", packages)

    if r_type=="create":
        labelSpecifications = {}
        add_if(labelSpecifications, "size", client_json.get("labelSpecifications", {}).get("size",{"width": 4,"length": 6,"unit": "INCH"}))
        add_if(labelSpecifications, "format", client_json.get("labelSpecifications", {}).get("format","PDF"))
        add_if(labelSpecifications, "dpi", client_json.get("labelSpecifications", {}).get("dpi", 300))
        add_if(labelSpecifications, "pageLayout", client_json.get("labelSpecifications", {}).get("pageLayout", "DEFAULT"))
        add_if(labelSpecifications, "needFileJoining", client_json.get("labelSpecifications", {}).get("needFileJoining", False))
        add_if(labelSpecifications, "requestedDocumentTypes", client_json.get("labelSpecifications", {}).get("requestedDocumentTypes",["LABEL"]))
        add_if(labelSpecifications, "requestedLabelCustomization", client_json.get("labelSpecifications", {}).get("requestedLabelCustomization"))
        add_if(payload, "labelSpecifications", labelSpecifications)

        add_if(payload, "goodsOwner", client_json.get("goodsOwner"))
        add_if(payload, "valueAddedServicesDetails", client_json.get("valueAddedServicesDetails"))
        add_if(payload, "serviceSelection", {"serviceId":  ["std-us-swa-mfn"]})
        add_if(payload, "shipperInstruction", client_json.get("shipperInstruction"))
    add_if(payload, "valueAddedServices", client_json.get("valueAddedServices"))
    add_if(payload, "taxDetails", client_json.get("taxDetails"))
    add_if(payload, "channelDetails", {"channelType": "EXTERNAL"})

    #add_if(payload, "clientReferenceDetails", client_json.get("clientReferenceDetails"))
    #add_if(payload, "shipmentType", client_json.get("shipmentType","FORWARD"))
    add_if(payload, "destinationAccessPointDetails", client_json.get("destinationAccessPointDetails"))
    return payload

def dhl_request_helper(client_json, client_id, r_type):
    timestamp = int(time.time() * 1000)
    package_id = f"{client_id}-{timestamp}"

    payload = {}
    ship_to = {}
    add_if(ship_to, "name", client_json.get("shipTo", {}).get("name"))
    add_if(ship_to, "address1", client_json.get("shipTo", {}).get("addressLine1"))
    add_if(ship_to, "address2", client_json.get("shipTo", {}).get("addressLine2"))
    add_if(ship_to, "companyName", client_json.get("shipTo", {}).get("companyName"))
    add_if(ship_to, "state", client_json.get("shipTo", {}).get("stateOrRegion"))
    add_if(ship_to, "city", client_json.get("shipTo", {}).get("city"))
    add_if(ship_to, "country", client_json.get("shipTo", {}).get("countryCode"))
    add_if(ship_to, "postalCode", client_json.get("shipTo", {}).get("postalCode"))
    add_if(ship_to, "email", client_json.get("shipTo", {}).get("email"))
    # DHL Label API alan adı "phone" (phoneNumber DEĞİL). Yanlış alan adı → 400.0204003.
    add_if(ship_to, "phone", client_json.get("shipTo", {}).get("phoneNumber") or client_json.get("shipTo", {}).get("phone"))
    add_if(payload, "consigneeAddress", ship_to)
    ship_from = {}
    add_if(ship_from, "name", client_json.get("shipFrom", {}).get("name"))
    add_if(ship_from, "address1", client_json.get("shipFrom", {}).get("addressLine1"))
    add_if(ship_from, "address2", client_json.get("shipFrom", {}).get("addressLine2"))
    add_if(ship_from, "companyName", client_json.get("shipFrom", {}).get("name"))
    add_if(ship_from, "state", client_json.get("shipFrom", {}).get("stateOrRegion"))
    add_if(ship_from, "city", client_json.get("shipFrom", {}).get("city"))
    add_if(ship_from, "country", client_json.get("shipFrom", {}).get("countryCode"))
    add_if(ship_from, "postalCode", client_json.get("shipFrom", {}).get("postalCode"))
    add_if(ship_from, "email", client_json.get("shipFrom", {}).get("email"))
    add_if(ship_from, "phone", client_json.get("shipFrom", {}).get("phoneNumber") or client_json.get("shipFrom", {}).get("phone"))
    add_if(payload, "returnAddress", ship_from)

    add_if(payload, "pickup", warehouse_value(PICKUP_DICT, ship_from.get("postalCode")))
    add_if(payload, "orderedProductId", "GND")
    #Expedited için bu satırın değişmesi lazım
    add_if(payload, "distributionCenter", warehouse_value(DISTRIBUTION_CENTERS, ship_from.get("postalCode")))
    add_if(payload, "shipperInstruction", client_json.get("shipperInstruction"))

    for package in client_json.get("packages", []):
        package_dict = {}

        add_if(package_dict, "packageId", package_id)
        add_if(package_dict, "packageDescription", str(client_id) + " s  packages")

        weight = {}
        add_if(weight, "unitOfMeasure", package.get("weight", {}).get("unit").replace("POUND","LB"))
        add_if(weight, "value", package.get("weight", {}).get("value"))
        add_if(package_dict, "weight", weight)

        dimension = {}
        add_if(dimension, "length", min(20, package.get("dimensions", {}).get("length", 20)))
        add_if(dimension, "width", min(17, package.get("dimensions", {}).get("width", 17)))
        add_if(dimension, "height", min(14, package.get("dimensions", {}).get("height",14)))
        add_if(dimension, "unitOfMeasure", package.get("dimensions", {}).get("unit").replace("INCH","IN"))
        add_if(dimension, "packageType", "1")
        add_if(package_dict, "dimension", dimension)

        shippingCost = {}
        add_if(shippingCost, "currency", "USD")
        add_if(shippingCost, "freight", 10)
        add_if(shippingCost, "declaredValue", 85)
        add_if(shippingCost, "insuredValue", 80)
        add_if(shippingCost, "dutiesPaid", True)
        add_if(package_dict, "shippingCost", shippingCost)

        add_if(package_dict, "service", "DELCON")
        if r_type=="create":
            add_if(package_dict, "serviceEndorsement", "1")
            add_if(package_dict, "billingReference1", str(client_id))
            add_if(package_dict, "billingReference2", str(client_id))

    if r_type=="rate":
        today_date = datetime.utcnow().strftime("%Y-%m-%d")
        rate = {}
        add_if(rate, "calculate", True)
        add_if(rate, "rateDate", today_date)
        add_if(rate, "maxPrice", 90)
        add_if(rate, "currency", "USD")
        add_if(payload, "rate", rate)

    add_if(payload, "packageDetail", package_dict)


    return payload


def dhl_int_request_helper(client_json, client_id, r_type):
    """
    DHL International Direct API için payload hazırlar.
    DHL International Direct (eCommerce International) endpoint'leri için.
    
    DHL Schema gereksinimleri:
    - customsDetails ROOT seviyesinde (packageDetail içinde DEĞİL)
    - itemValue: NUMBER (object değil)
    - currency: ROOT level ayrı alan
    - countryOfOrigin (originCountry DEĞİL)
    - packagedQuantity (quantity DEĞİL)
    - itemDescription (description DEĞİL)
    - weight customsDetails içinde YOK
    - customs, channelDetails root'ta YOK
    """
    timestamp = int(time.time() * 1000)
    package_id = f"{client_id}-{timestamp}-INT"

    payload = {}
    
    # Consignee (Alıcı) - International format
    ship_to = {}
    add_if(ship_to, "name", client_json.get("shipTo", {}).get("name"))
    add_if(ship_to, "address1", client_json.get("shipTo", {}).get("addressLine1"))
    add_if(ship_to, "address2", client_json.get("shipTo", {}).get("addressLine2"))
    add_if(ship_to, "companyName", client_json.get("shipTo", {}).get("companyName"))
    add_if(ship_to, "state", client_json.get("shipTo", {}).get("stateOrRegion"))
    add_if(ship_to, "city", client_json.get("shipTo", {}).get("city"))
    add_if(ship_to, "country", client_json.get("shipTo", {}).get("countryCode"))  # ISO 2 harf
    add_if(ship_to, "postalCode", client_json.get("shipTo", {}).get("postalCode"))
    add_if(ship_to, "email", client_json.get("shipTo", {}).get("email"))
    # DHL schema: consigneeAddress.phone (phoneNumber extraneous key → 400.0604001)
    add_if(ship_to, "phone", client_json.get("shipTo", {}).get("phoneNumber") or client_json.get("shipTo", {}).get("phone"))
    add_if(payload, "consigneeAddress", ship_to)
    
    # Return/Sender (Gönderici) - International format
    ship_from = {}
    add_if(ship_from, "name", client_json.get("shipFrom", {}).get("name"))
    add_if(ship_from, "address1", client_json.get("shipFrom", {}).get("addressLine1"))
    add_if(ship_from, "address2", client_json.get("shipFrom", {}).get("addressLine2"))
    add_if(ship_from, "companyName", client_json.get("shipFrom", {}).get("companyName"))
    add_if(ship_from, "state", client_json.get("shipFrom", {}).get("stateOrRegion"))
    add_if(ship_from, "city", client_json.get("shipFrom", {}).get("city"))
    add_if(ship_from, "country", client_json.get("shipFrom", {}).get("countryCode"))
    add_if(ship_from, "postalCode", client_json.get("shipFrom", {}).get("postalCode"))
    add_if(ship_from, "email", client_json.get("shipFrom", {}).get("email"))
    # DHL schema: returnAddress.phone (phoneNumber DEĞİL)
    add_if(ship_from, "phone", client_json.get("shipFrom", {}).get("phoneNumber") or client_json.get("shipFrom", {}).get("phone"))
    add_if(payload, "returnAddress", ship_from)

    # Pickup ve distribution center
    from_zip = ship_from.get("postalCode")
    pickup_id = warehouse_value(PICKUP_DICT, from_zip)
    dist_center = warehouse_value(DISTRIBUTION_CENTERS, from_zip)
    
    add_if(payload, "pickup", pickup_id)
    add_if(payload, "orderedProductId", "PLT")  # DHL Parcel International Direct
    add_if(payload, "distributionCenter", dist_center)
    add_if(payload, "shipperInstruction", client_json.get("shipperInstruction"))
    
    # Warehouse zip validation - PLT için geçerli warehouse zip zorunlu
    if not pickup_id or not dist_center:
        logger.warning(
            "PLT International: invalid warehouse zip %r. Valid: %s",
            from_zip,
            sorted(PICKUP_DICT.keys())
        )

    # Paketler
    for package in client_json.get("packages", []):
        package_dict = {}

        add_if(package_dict, "packageId", package_id)
        add_if(package_dict, "packageDescription", f"Client {client_id} international package")

        # Ağırlık
        weight = {}
        weight_unit = package.get("weight", {}).get("unit", "LB")
        add_if(weight, "unitOfMeasure", weight_unit.replace("POUND", "LB"))
        add_if(weight, "value", package.get("weight", {}).get("value"))
        add_if(package_dict, "weight", weight)

        # Boyutlar
        dimension = {}
        add_if(dimension, "length", min(20, package.get("dimensions", {}).get("length", 20)))
        add_if(dimension, "width", min(17, package.get("dimensions", {}).get("width", 17)))
        add_if(dimension, "height", min(14, package.get("dimensions", {}).get("height", 14)))
        dim_unit = package.get("dimensions", {}).get("unit", "INCH")
        add_if(dimension, "unitOfMeasure", dim_unit.replace("INCH", "IN"))
        add_if(dimension, "packageType", "1")  # 1 = Package
        add_if(package_dict, "dimension", dimension)

        # Shipping Cost (Rate için tahmini değerler)
        shippingCost = {}
        add_if(shippingCost, "currency", "USD")
        add_if(shippingCost, "freight", 10)
        add_if(shippingCost, "declaredValue", 85)
        add_if(shippingCost, "insuredValue", 80)
        add_if(shippingCost, "dutiesPaid", True)  # DDP - Delivery Duty Paid
        add_if(package_dict, "shippingCost", shippingCost)

        # Service - International için
        add_if(package_dict, "service", "DELCON")  # DHL International service code

        # NOT: packageDetail.items EKLEME - DHL schema additionalProperties: false
        # Gümrük bilgileri customsDetails ROOT seviyesinde ayrı array olarak gönderilir

        if r_type == "create":
            add_if(package_dict, "serviceEndorsement", "1")  # International endorsement
            add_if(package_dict, "billingReference1", str(client_id))
            add_if(package_dict, "billingReference2", str(client_id))

    if r_type == "rate":
        today_date = datetime.utcnow().strftime("%Y-%m-%d")
        rate = {}
        add_if(rate, "calculate", True)
        add_if(rate, "rateDate", today_date)
        add_if(rate, "maxPrice", 500)  # International için daha yüksek max
        add_if(rate, "currency", "USD")
        add_if(payload, "rate", rate)

    add_if(payload, "packageDetail", package_dict)

    # ===== CUSTOMS DETAILS - ROOT SEVİYESİ (DHL Schema'ya uygun) =====
    customs_details = []
    for package in client_json.get("packages", []):
        for item in package.get("items", []):
            # Her item için customsDetails entry'si oluştur
            item_value = item.get("itemValue", {}).get("value")
            if item_value is not None:
                customs_entry = {}
                add_if(customs_entry, "itemDescription", item.get("description"))
                add_if(customs_entry, "packagedQuantity", item.get("quantity", 1))
                add_if(customs_entry, "itemValue", item_value)  # NUMBER, not object
                add_if(customs_entry, "currency", item.get("itemValue", {}).get("unit", "USD"))
                add_if(customs_entry, "countryOfOrigin", item.get("originCountry"))
                add_if(customs_entry, "hsCode", item.get("hsCode") or item.get("itemIdentifier"))
                # weight customsDetails içinde YOK (schema'da tanımsız)
                customs_details.append(customs_entry)
    
    if customs_details:
        add_if(payload, "customsDetails", customs_details)

    # International spesifik alanlar (sadece schema'da izin verilenler)
    add_if(payload, "valueAddedServices", client_json.get("valueAddedServices"))
    add_if(payload, "taxDetails", client_json.get("taxDetails"))
    # channelDetails root'ta YOK - schema additionalProperties: false
    # customs root'ta YOK - schema additionalProperties: false

    return payload

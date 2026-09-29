from datetime import datetime
import time

def add_if(payload, key, value):
    if value is None:
        return
    if isinstance(value, dict) and not value:
        return
    payload[key] = value

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
    pickup_dict = {"75081-1955":"5410014","75243-5485":"5410429","77407-9012": "5410448","75006-2527":"5411053"}
    # ODF 
    distributionCenter = {"75081-1955":"USDFW1","75243-5485":"USDFW1","77407-9012": "USIAH1","75006-2527":"USDFW1"}
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
    add_if(ship_to, "phoneNumber", client_json.get("shipTo", {}).get("phoneNumber"))
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
    add_if(ship_from, "phoneNumber", client_json.get("shipFrom", {}).get("phoneNumber"))
    add_if(payload, "returnAddress", ship_from)

    add_if(payload, "pickup", pickup_dict.get(ship_from.get("postalCode")))
    add_if(payload, "orderedProductId", "GND")
    #Expedited için bu satırın değişmesi lazım
    add_if(payload, "distributionCenter", distributionCenter.get(ship_from.get("postalCode")))
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

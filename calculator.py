
from model import ZoneMapping, ShippingRate

def calculate_shipping_cost(client_id, from_zip, to_zip, weight, carrier='AMZ', service='EXP'):
    """
    Kargo maliyetini hesaplar.
    """
    # 1. To Zip'in ilk 5 hanesini al (Güvenlik için)
    target_zip = str(to_zip)[:5].zfill(5)
    origin_zip = str(from_zip)[:5].zfill(5)

    # 2. Ağırlığı yukarı yuvarla (Örn: 2.3 lb -> 3 lb)
    # Çünkü kargo şirketleri her zaman bir üst tam sayıya fatura keser.
    import math
    #calc_weight = math.ceil(float(weight))
    calc_weight = math.ceil(float(weight) * 10) / 10

    if float(weight) == 1.0:
        calc_weight == 0.9
    elif float(weight) == 1.1:
        calc_weight == 0.9
    elif float(weight) == 1.2:
        calc_weight == 0.9
    elif float(weight) == 1.3:
        calc_weight == 1
    elif float(weight) > 1:
        #calc_weight = 0 # "1 lb less" kuralı
        calc_weight = math.ceil(float(weight))

    # 3. Zone Bulma (Range Sorgusu)
    # Veritabanında target_zip'in hangi start-end aralığına düştüğünü buluyoruz
    zone_mapping = ZoneMapping.query.filter(
        ZoneMapping.from_zip == origin_zip,
        ZoneMapping.to_zip_start <= target_zip,
        ZoneMapping.to_zip_end >= target_zip,
        ZoneMapping.carrier==carrier
    ).first()

    if not zone_mapping:
        return None, "Zone bulunamadı (Geçersiz Zip)"

    zone_number = zone_mapping.zone # Örn: 5

    # 4. Rate (Fiyat) Bulma
    filter_kwargs = {
        "client_id": client_id,
        "weight_lb": calc_weight,
        "carrier": carrier
    }
    
    if service:
        filter_kwargs["services"] = service 

    rate_entry = ShippingRate.query.filter_by(**filter_kwargs).first()

    if not rate_entry:
        return None, f"{calc_weight} lb için fiyat tanımlanmamış."

    # 5. Dinamik Sütun Seçimi (Zone 1, Zone 2...)
    # getattr kullanarak zone_5 sütunundaki fiyatı çekiyoruz
    cost = getattr(rate_entry, f'zone_{zone_number}')

    return cost, "rate query successful"
from flask_app import app
from flask import request, jsonify, render_template, url_for, flash, redirect
from flask_login import login_required, current_user
from sqlalchemy import func
import pandas as pd
from model import db, ShippingRate, ZoneMapping, Client # Kendi uygulama yapına göre düzenle
import re
from decimal import Decimal, InvalidOperation, ConversionSyntax


def to_decimal(value):
    """
    Excel'den gelen değeri Decimal'e çevirir.
    Virgül ondalık ayracı (4,45) -> Nokta (4.45) olarak normalize eder.
    Excel hata değerleri (#N/A, #DIV/0!, #VALUE!, #REF!, #NAME?, #NUM!, #NULL!),
    para birimi sembolleri ($, €, TL, £), yüzde (%), bilimsel notasyon gibi durumları toleranslı karşılar.
    """
    if pd.isna(value) or value is None:
        return Decimal('0')
    
    try:
        # String'e çevir ve temizle
        s = str(value).strip()
        
        # Excel hata değerlerini kontrol et
        if s.startswith('#') or s.upper() in ('NA', 'N/A', 'NULL', 'NONE', ''):
            return Decimal('0')
        
        # Para birimi sembolleri, yüzde, boşluk ve yaygın karakterleri temizle
        # Sadece rakam, nokta, virgül, eksi işareti ve üstel notasyon (e/E) karakterlerine izin ver
        import re
        s = re.sub(r'[^\d.,eE-]', '', s)
        
        # Virgülü noktaya çevir (ondalık ayracı normalize et)
        # Eğer hem nokta hem virgül varsa, sondaki ondalık ayracı olarak kabul et
        if ',' in s and '.' in s:
            # Son görülen ondalık ayracı hangisiyse onu koru, diğerini binlik ayracı say ve sil
            last_comma = s.rfind(',')
            last_dot = s.rfind('.')
            if last_comma > last_dot:
                # Virgül ondalık ayracı: noktaları sil (binlik ayracı)
                s = s.replace('.', '')
                s = s.replace(',', '.')
            else:
                # Nokta ondalık ayracı: virgülleri sil (binlik ayracı)
                s = s.replace(',', '')
        elif ',' in s:
            # Sadece virgül varsa ondalık ayracı olarak kabul et
            s = s.replace(',', '.')
        # Sadece nokta varsa zaten doğru format
        
        # Boş string kontrolü
        if not s or s in ('.', '-', '+', 'e', 'E'):
            return Decimal('0')
            
        return Decimal(s)
    except (InvalidOperation, ValueError, ConversionSyntax):
        return Decimal('0')


def to_int(value, default=0):
    """
    Excel'den gelen değeri güvenli bir şekilde int'e çevirir.
    Boş, hata değeri (#N/A, #DIV/0!, vb.), metin içeren hücrelerde default döner.
    """
    if pd.isna(value) or value is None:
        return default
    
    s = str(value).strip()
    
    # Excel hata değerlerini kontrol et
    if s.startswith('#') or s.upper() in ('NA', 'N/A', 'NULL', 'NONE', ''):
        return default
    
    # Sadece rakam ve eksi işaretine izin ver
    import re
    cleaned = re.sub(r'[^\d-]', '', s)
    
    if not cleaned or cleaned in ('-', '+'):
        return default
    
    try:
        return int(float(cleaned))  # float'a çevirip int yaparak ondalık kısımları da yönet
    except (ValueError, OverflowError):
        return default


def clean_weight(weight_str):
    """
    '< 5lb', '<= 10lb', '15' gibi verileri sayıya dönüştürür.
    Excel hata değerlerini ve boş hücreleri toleranslı karşılar.
    """
    if pd.isna(weight_str): 
        return 0
    
    s = str(weight_str).strip()
    
    # Excel hata değerlerini kontrol et
    if s.startswith('#') or s.upper() in ('NA', 'N/A', 'NULL', 'NONE', ''):
        return 0
    
    # Sayı dışındaki her şeyi (lb, <, =, <=, boşluk, para birimi vb.) temizle
    import re
    cleaned = re.sub(r'[^\d.,eE-]', '', s)
    cleaned = cleaned.replace(',', '.')  # ondalık ayracı normalize et
    
    if not cleaned or cleaned in ('.', '-', '+', 'e', 'E'):
        return 0
    
    try:
        return Decimal(cleaned) if '.' in cleaned else int(cleaned)
    except (InvalidOperation, ValueError, ConversionSyntax):
        return 0


@app.route("/rate/list", methods=["GET"])
@login_required
def list_rate():
    rates = db.session.query(
        ShippingRate,
        Client.username
    ).join(Client, ShippingRate.client_id == Client.id).all()
    rates_list = []
    for rate, username in rates:
        rate_dict = {
            "id": rate.id,
            "username": username,  # Client tablosundan gelen isim
            "carrier" : rate.carrier,
            "services": rate.services,
            "weight_lb": rate.weight_lb,
            "zone_1": rate.zone_1,
            "zone_2": rate.zone_2,
            "zone_3": rate.zone_3,
            "zone_4": rate.zone_4,
            "zone_5": rate.zone_5,
            "zone_6": rate.zone_6,
            "zone_7": rate.zone_7,
            "zone_8": rate.zone_8
        }
        rates_list.append(rate_dict)
    clients_list = [{"id": c.id, "username": c.username} for c in db.session.query(Client.id, Client.username).all()]
    return render_template("shipping_rates.html", rates=rates_list, clients=clients_list)


@app.route('/rate/upload', methods=['POST'])
@login_required
def upload_rates():
    file = request.files.get('file')
    client_id = request.form.get('client_id')

    try:
        # 1. Tabloyu 3. satırdan itibaren oku (header=2 diyerek ilk 2 satırı atlıyoruz)
        df = pd.read_excel(file, skiprows=2)

        # 2. Sütun isimlerini zorla ata
        # 0: Weight, 1-8: Zone 1-8, 9+: diğer sütunlar (Service, vs.)
        df.columns = ['weight_raw', 'z1', 'z2', 'z3', 'z4', 'z5', 'z6', 'z7', 'z8'] + list(df.columns[9:])

        stats = {"created": 0, "updated": 0}

        for _, row in df.iterrows():
            if pd.isna(row['weight_raw']): continue

            weight = clean_weight(row['weight_raw'])

            # Service sütununu oku (Excel'de "Service", "Services", "Servis" gibi isimlerle gelebilir)
            # df.columns[9:] kısmı orijinal isimleri koruduğu için olası isimleri deniyoruz
            service_value = None
            for svc_col in ['Service', 'Services', 'Servis', 'service', 'services', 'servis', 'Type', 'type']:
                if svc_col in row.index and pd.notna(row[svc_col]):
                    service_value = str(row[svc_col]).strip()
                    break
            
            # Eğer 9. sütun (index 9) varsa ve adı yukarıdakilerden biri değilse, onu da dene
            if service_value is None and len(df.columns) > 9:
                ninth_col = df.columns[9]
                if pd.notna(row[ninth_col]):
                    service_value = str(row[ninth_col]).strip()

            rate = ShippingRate.query.filter_by(client_id=client_id, weight_lb=weight, carrier='DHL').first()

            z_values = {
                'zone_1': to_decimal(row.get('z1', 0)),
                'zone_2': to_decimal(row.get('z2', 0)),
                'zone_3': to_decimal(row.get('z3', 0)),
                'zone_4': to_decimal(row.get('z4', 0)),
                'zone_5': to_decimal(row.get('z5', 0)),
                'zone_6': to_decimal(row.get('z6', 0)),
                'zone_7': to_decimal(row.get('z7', 0)),
                'zone_8': to_decimal(row.get('z8', 0))
            }

            if rate:
                for key, val in z_values.items(): setattr(rate, key, val)
                if service_value:
                    rate.services = service_value
                stats["updated"] += 1
            else:
                new_rate = ShippingRate(
                    client_id=client_id, 
                    weight_lb=weight, 
                    carrier='DHL', 
                    services=service_value,
                    **z_values
                )
                db.session.add(new_rate)
                stats["created"] += 1

        db.session.commit()
        flash(f"Başarılı! {stats['created']} yeni, {stats['updated']} güncelleme.", "success")
        return redirect(url_for("list_rate"))


    except Exception as e:
        db.session.rollback()
        flash(f"Hata: {str(e)}", "error")
        return redirect(url_for("list_rate"))


@app.route('/upload-zones', methods=['POST'])
@login_required
def upload_zones():
    file = request.files.get('file')
    if not file:
        flash("Dosya seçilmedi", "error")
        return redirect(url_for("zone_mappings_view"))

    filename = file.filename.lower()
    mappings_to_add = []
    try:

        if filename.endswith(('.xlsx', '.xls')):
            # Excel dosyalarında chunksize CSV kadar yaygın kullanılmaz ama bellek için direkt okunabilir
            df = pd.read_excel(file)

            for _, row in df.iterrows():
                mappings_to_add.append({
                    'from_zip': str(to_int(row['from_post_code_district'])).zfill(5),
                    'to_zip_start': str(to_int(row['to_start'])).zfill(5),
                    'to_zip_end': str(to_int(row['to_end'])).zfill(5),
                    'zone': 8 if to_int(row['zone'])>8 else to_int(row['zone']),
                    'carrier': 'DHL'
                })
        elif filename.endswith('.csv'):
            # Belleği korumak için chunk ile okuyoruz
            chunks = pd.read_csv(file, chunksize=100000)

            # Dosyada hangi From Zip'ler varsa onları takip etmek için
            # İşlem başlamadan önce dosyada bulunan From Zip'leri temizlemek istersen
            # önce tüm dosyayı taramak gerekir, ama bu yavaşlatır.
            # Şimdilik mevcut mantığı "ekleme/güncelleme" üzerine kuralım.

            current_range = None

            for chunk in chunks:
                # Hem From hem To'ya göre sıralıyoruz ki gruplama bozulmasın
                chunk = chunk.sort_values(['from_post_code_district', 'to_post_code_district'])

                for _, row in chunk.iterrows():
                    f_zip = str(to_int(row['from_post_code_district'])).zfill(5)
                    t_zip = str(to_int(row['to_post_code_district'])).zfill(5)
                    zone = 8 if to_int(row['zone'])>8 else to_int(row['zone'])

                    if current_range is None:
                        current_range = {'carrier': 'DHL', 'from_zip': f_zip, 'to_zip_start': t_zip, 'to_zip_end': t_zip, 'zone': zone}

                    # EĞER: Hem From Zip aynıysa, HEM Zone aynıysa, HEM DE Zip'ler ardışıksa
                    elif (f_zip == current_range['from_zip'] and
                          zone == current_range['zone'] and
                          to_int(t_zip) == to_int(current_range['to_zip_end']) + 1):
                        current_range['to_zip_end'] = t_zip
                    else:
                        # Aralık bozuldu (ya from değişti, ya zone değişti ya da zip atladı)
                        mappings_to_add.append(current_range)
                        current_range = {'carrier': 'DHL', 'from_zip': f_zip, 'to_zip_start': t_zip, 'to_zip_end': t_zip, 'zone': zone}

            if current_range:
                mappings_to_add.append(current_range)

        else:
            flash("Desteklenmeyen dosya formatı. Lütfen .csv veya .xlsx yükleyin.", "error")
            return redirect(url_for("zone_mappings_view"))

        if mappings_to_add:
            db.session.bulk_insert_mappings(ZoneMapping, mappings_to_add)
            db.session.commit()
            flash(f"Başarılı! {len(mappings_to_add)} optimize aralık oluşturuldu.", "success")
            return redirect(url_for("zone_mappings_view"))
        else:
            flash("Dosyada eklenecek veri bulunamadı.", "error")
            return redirect(url_for("zone_mappings_view"))
    except Exception as e:
        db.session.rollback()
        flash(f"Hata: {str(e)}", "error")
        return redirect(url_for("zone_mappings_view"))


@app.route('/admin/zone-mappings')
@login_required
def zone_mappings_view():
    zones = ZoneMapping.query.all()
    return render_template("zone_mappings.html", zones=zones)
    # Özet Sorgusu: from_zip'e göre grupla ve her gruptaki satır sayısını say
    # SQL karşılığı: SELECT from_zip, COUNT(*) FROM zone_mappings GROUP BY from_zip
    #zone_summaries = db.session.query(
    #    ZoneMapping.from_zip,
    #    func.count(ZoneMapping.id).label('range_count')
    #).group_by(ZoneMapping.from_zip).all()

    #return render_template('zone_mappings.html', zone_summaries=zone_summaries)
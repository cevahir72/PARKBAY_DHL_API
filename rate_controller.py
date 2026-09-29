from flask_app import app
from flask import request, jsonify, render_template, url_for, flash, redirect
from flask_login import login_required, current_user
from sqlalchemy import func
import pandas as pd
from model import db, ShippingRate, ZoneMapping, Client # Kendi uygulama yapına göre düzenle
import re
from decimal import Decimal


def clean_weight(weight_str):
    """
    '< 5lb', '<= 10lb', '15' gibi verileri sayıya dönüştürür.
    """
    if pd.isna(weight_str): return 0
    # Sayı dışındaki her şeyi (lb, <, =, <=) temizle
    cleaned = re.sub(r'[^\d.]', '', str(weight_str))
    try:
        return Decimal(cleaned) if '.' in cleaned else int(cleaned)
    except:
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
        # skiprows ile üstteki boşlukları ve gereksiz metinleri eliyoruz
        df = pd.read_excel(file, skiprows=2)

        # 2. Sütun isimlerini zorla ata (Excel'deki isimler farklı olsa bile senin sıran belli)
        # Görüntüye göre: 0. sütun Weight, 1. sütun Zone 1, 2. sütun Zone 2...
        df.columns = ['weight_raw', 'z1', 'z2', 'z3', 'z4', 'z5', 'z6', 'z7', 'z8'] + list(df.columns[9:])

        stats = {"created": 0, "updated": 0}

        for _, row in df.iterrows():
            # Satır boşsa veya ağırlık bilgisi yoksa geç
            if pd.isna(row['weight_raw']): continue

            # Ağırlığı temizle (Örn: "<= 5lb" -> 5)
            weight = clean_weight(row['weight_raw'])

            # Veritabanı işlemi (Upsert)
            rate = ShippingRate.query.filter_by(client_id=client_id, weight_lb=weight, carrier='DHL').first()

            # Değerleri alırken float'a zorla, hata varsa 0.0 yaz
            z_values = {
                'zone_1': Decimal(str(row.get('z1', 0) or 0)),
                'zone_2': Decimal(str(row.get('z2', 0) or 0)),
                'zone_3': Decimal(str(row.get('z3', 0) or 0)),
                'zone_4': Decimal(str(row.get('z4', 0) or 0)),
                'zone_5': Decimal(str(row.get('z5', 0) or 0)),
                'zone_6': Decimal(str(row.get('z6', 0) or 0)),
                'zone_7': Decimal(str(row.get('z7', 0) or 0)),
                'zone_8': Decimal(str(row.get('z8', 0) or 0))
            }

            if rate:
                for key, val in z_values.items(): setattr(rate, key, val)
                stats["updated"] += 1
            else:
                new_rate = ShippingRate(client_id=client_id, weight_lb=weight,carrier='DHL', **z_values)
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
                    'from_zip': str(int(row['from_post_code_district'])).zfill(5),
                    'to_zip_start': str(int(row['to_start'])).zfill(5),
                    'to_zip_end': str(int(row['to_end'])).zfill(5),
                    'zone': 8 if int(row['zone'])>8 else int(row['zone']),
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
                    f_zip = str(int(row['from_post_code_district'])).zfill(5)
                    t_zip = str(int(row['to_post_code_district'])).zfill(5)
                    zone = 8 if int(row['zone'])>8 else int(row['zone'])

                    if current_range is None:
                        current_range = {'carrier': 'DHL', 'from_zip': f_zip, 'to_zip_start': t_zip, 'to_zip_end': t_zip, 'zone': zone}

                    # EĞER: Hem From Zip aynıysa, HEM Zone aynıysa, HEM DE Zip'ler ardışıksa
                    elif (f_zip == current_range['from_zip'] and
                          zone == current_range['zone'] and
                          int(t_zip) == int(current_range['to_zip_end']) + 1):
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
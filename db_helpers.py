"""Veritabanı oturumu ile ilgili ortak yardımcılar."""

from flask import current_app

from model import db


def release_db_connection():
    """
    Uzun süren DHL/Amazon/Easyship gibi HTTP çağrılarından önce MySQL
    bağlantısını havuza bırakır.

    Aksi hâlde bağlantı istek boyunca boşta kalır. Sunucu veya aradaki
    proxy bağlantıyı kapatırsa, istek sonunda session kapatılırken /
    rollback sırasında "Lost connection to MySQL server during query"
    hatası alınır.

    Bağlantıyı serbest bırakmadan önce ihtiyaç duyulan istemci alanları
    (id, reference_code, com_fee vb.) yerel değişkenlere alınmalıdır.
    """
    try:
        db.session.remove()
    except Exception:
        current_app.logger.exception("DB session could not be released")

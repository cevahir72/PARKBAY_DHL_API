from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy.exc import IntegrityError

from flask_app import app, db
from decorators import role_required
from model import Carrier


@app.route("/admin/carriers", methods=["GET"])
@login_required
@role_required("admin")
def admin_carriers():
    carriers = Carrier.query.order_by(Carrier.id).all()
    return render_template("carriers.html", carriers=carriers)


@app.route("/admin/carrier/add", methods=["POST"])
@login_required
@role_required("admin")
def admin_add_carrier():
    name = (request.form.get("name") or "").strip()
    international = (request.form.get("international") or "").strip().lower()

    if not name or international not in {"domestic", "international"}:
        flash("Name ve geçerli bir gönderi türü zorunludur.", "danger")
        return redirect(url_for("admin_carriers"))

    db.session.add(Carrier(name=name, international=international))
    db.session.commit()
    flash(f"{name} taşıyıcısı başarıyla oluşturuldu.", "success")
    return redirect(url_for("admin_carriers"))


@app.route("/admin/carrier/edit/<int:carrier_id>", methods=["POST"])
@login_required
@role_required("admin")
def admin_edit_carrier(carrier_id):
    carrier = Carrier.query.get_or_404(carrier_id)
    name = (request.form.get("name") or "").strip()
    international = (request.form.get("international") or "").strip().lower()

    if not name or international not in {"domestic", "international"}:
        flash("Name ve geçerli bir gönderi türü zorunludur.", "danger")
        return redirect(url_for("admin_carriers"))

    carrier.name = name
    carrier.international = international
    db.session.commit()
    flash(f"{name} taşıyıcısı başarıyla güncellendi.", "success")
    return redirect(url_for("admin_carriers"))


@app.route("/admin/carrier/delete/<int:carrier_id>", methods=["POST"])
@login_required
@role_required("admin")
def admin_delete_carrier(carrier_id):
    carrier = Carrier.query.get_or_404(carrier_id)
    name = carrier.name

    try:
        db.session.delete(carrier)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash(
            "Bu carrier mevcut shipment log kayıtlarında kullanıldığı için silinemedi.",
            "danger"
        )
        return redirect(url_for("admin_carriers"))

    flash(f"{name} taşıyıcısı başarıyla silindi.", "success")
    return redirect(url_for("admin_carriers"))

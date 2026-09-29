from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin, LoginManager
from sqlalchemy import func, JSON, select, Numeric
from datetime import datetime, timedelta
from werkzeug.security import check_password_hash, generate_password_hash
import secrets
import time

db = SQLAlchemy()
#mail= Mail()
login_manager = LoginManager()


class User(UserMixin, db.Model):
    __tablename__ = "User"

    id = db.Column(db.Integer, primary_key=True)
    mail = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    admin_id = db.Column(db.Integer)
    user_role = db.Column(db.String(50), default="user")

    # 🔐 LOGIN DURUMU
    last_login_at = db.Column(db.DateTime)
    last_login_ip = db.Column(db.String(45))

    # 🧠 SYSTEM / AUDIT
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, onupdate=datetime.now)

    def __repr__(self):
        return {
            "id": self.id,
            "mail": self.mail,
            "user_role": self.user_role,
            "last_login_at": self.last_login_at,
            "last_login_ip": self.last_login_ip,
            "is_active": self.is_active,
        }

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def can_purchase(self, amount):
        return (self.balance - amount) >= self.negative_limit


class UserLoginLog(db.Model):
    __tablename__ = "UserLoginLog"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("User.id"), nullable=False)

    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.String(255))

    login_at = db.Column(db.DateTime, default=datetime.now)

    user = db.relationship("User", backref="login_logs")


class Client(db.Model):
    __tablename__ = "clients"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    username = db.Column(db.String(100), unique=True, nullable=False)
    reference_code = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    balance = db.Column(db.Numeric(10,2), default=0, nullable=False)

    token = db.Column(db.String(255), unique=True, index=True, nullable=True)
    token_expiry = db.Column(db.Integer, nullable=True)

    is_active = db.Column(db.Boolean, default=True)
    is_test = db.Column(db.Boolean, default=False)

    com_fee = db.Column(db.Float, default=0.0)

    last_request_time = db.Column(db.Integer, default=0)
    request_count = db.Column(db.Integer, default=0)
    rate_limit = db.Column(db.Integer, default=5)

    # 🔐 Şifre ayarla
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    # 🔍 Şifre kontrol
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    # 🔑 Token üret
    def generate_token(self):
        self.token = secrets.token_urlsafe(48)
        self.token_expiry = int(time.time()) + 3600  # 1 gün

    # ⏳ Token geçerli mi?
    def is_token_valid(self):
        if not self.token or not self.token_expiry:
            return False
        return time.time() < self.token_expiry


class RequestLog(db.Model):
    __tablename__ = "request_logs"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=True)

    ip_address = db.Column(db.String(45))  # IPv6 dahil
    endpoint = db.Column(db.String(255))
    method = db.Column(db.String(10))

    user_agent = db.Column(db.String(255), nullable=True)

    client = db.relationship("Client", backref="request_logs")


class ShipmentLog(db.Model):
    __tablename__ = "shipment_logs"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"))

    action = db.Column(db.String(50))
    # örnek: "get_label", "cancel_label"

    reference_id = db.Column(db.String(255), nullable=True)
    # amazon shipment id / order id vs

    status = db.Column(db.String(50))
    # success / failed

    response = db.Column(db.Text, nullable=True)
    # amazon response (debug için)

    client = db.relationship("Client", backref="shipment_logs")


class ShippingRate(db.Model):
    __tablename__ = 'shipping_rates'

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False, index=True) # Hangi kullanıcıya ait?
    weight_lb = db.Column(db.Numeric(precision=4, scale=1), nullable=False, index=True)
    carrier = db.Column(db.String(20), nullable=False)
    # Komisyon dahil net fiyatlar
    zone_1 = db.Column(db.Numeric(precision=10, scale=2), default=0.00)
    zone_2 = db.Column(db.Numeric(precision=10, scale=2), default=0.00)
    zone_3 = db.Column(db.Numeric(precision=10, scale=2), default=0.00)
    zone_4 = db.Column(db.Numeric(precision=10, scale=2), default=0.00)
    zone_5 = db.Column(db.Numeric(precision=10, scale=2), default=0.00)
    zone_6 = db.Column(db.Numeric(precision=10, scale=2), default=0.00)
    zone_7 = db.Column(db.Numeric(precision=10, scale=2), default=0.00)
    zone_8 = db.Column(db.Numeric(precision=10, scale=2), default=0.00)

    def __repr__(self):
        return f'<ShippingRate User:{self.user_id} Weight:{self.weight_lb}>'

class ZoneMapping(db.Model):
    __tablename__ = 'zone_mappings'

    id = db.Column(db.Integer, primary_key=True)
    carrier = db.Column(db.String(20), nullable=False)
    from_zip = db.Column(db.String(12), nullable=False, index=True)
    # Varış aralıkları (Range Mantığı)
    to_zip_start = db.Column(db.String(12), nullable=False)
    to_zip_end = db.Column(db.String(12), nullable=False)
    zone = db.Column(db.Integer, nullable=False)

    # Performans için çok önemli: From ve To aralıklarını kapsayan index
    __table_args__ = (
        db.Index('idx_from_to_range', 'from_zip', 'to_zip_start', 'to_zip_end'),
    )


class WalletTransaction(db.Model):
    __tablename__ = "wallet_transactions"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)

    type = db.Column(db.String(50), nullable=False)
    # deposit, withdraw, label_purchase, label_refund, manual_adjustment

    direction = db.Column(db.String(10), nullable=False)
    # credit (para girer) / debit (para çıkar)

    amount = db.Column(db.Numeric(10,2), nullable=False)
    balance_after = db.Column(db.Numeric(10,2), nullable=False)

    reference_id = db.Column(db.String(255))
    description = db.Column(db.String(255))

    client = db.relationship("Client", backref="wallet_transactions")


class Label(db.Model):
    __tablename__ = "labels"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)

    shipment_id = db.Column(db.String(255))      # carrier shipment id         # senin sistemdeki order id
    pickup_id = db.Column(db.String(255))

    carrier = db.Column(db.String(50))           # UPS, USPS, FedEx vb
    service = db.Column(db.String(100))          # Ground, Priority vb

    tracking_number = db.Column(db.String(255))

    market_place = db.Column(db.String(50))
    reference_code = db.Column(db.String(50))
    from_address = db.Column(db.String(255))
    from_zip = db.Column(db.String(15))
    to_zip = db.Column(db.String(15))
    customer_email = db.Column(db.String(100))
    customer_phone = db.Column(db.String(50))
    weight = db.Column(db.Numeric(precision=4, scale=1))

    amz_cost = db.Column(db.Numeric(10,2), nullable=False)
    my_cost = db.Column(db.Numeric(10,2), nullable=False)

    status = db.Column(db.String(50), default="purchased")
    # purchased / refunded / cancelled

    refunded_at = db.Column(db.DateTime, nullable=True)

    client = db.relationship("Client", backref="labels")



# dhl_service.py
import os
import time
import requests
from threading import Lock

# 🔐 DHL GLOBAL TOKEN
dhl_token_data = {
    "access_token": None,
    "expires_at": 0
}

dhl_token_lock = Lock()


def refresh_dhl_token():
    url = "https://api.dhlecs.com/auth/v4/accesstoken"

    payload = {
        "grant_type": "client_credentials",
        "client_id": os.getenv("DHL_CLIENT_ID"),
        "client_secret": os.getenv("DHL_CLIENT_SECRET"),
    }

    headers = {
        "accept": "application/json",
        "content-type": "application/x-www-form-urlencoded",
    }

    response = requests.post(url, data=payload, headers=headers)
    response.raise_for_status() # Hata durumunda exception fırlatır
    data = response.json()

    dhl_token_data["access_token"] = data["access_token"]
    # DHL genellikle expires_in döner (örneğin saniye cinsinden). Eğer dönmüyorsa varsayılan bir süre (örn: 3600 sn) verilebilir.
    expires_in = data.get("expires_in", 3600)
    dhl_token_data["expires_at"] = time.time() + expires_in - 60

    return dhl_token_data["access_token"]


def get_dhl_token():
    if (
        dhl_token_data["access_token"] is None or
        time.time() > dhl_token_data["expires_at"]
    ):
        with dhl_token_lock:
            if (
                dhl_token_data["access_token"] is None or
                time.time() > dhl_token_data["expires_at"]
            ):
                return refresh_dhl_token()

    return dhl_token_data["access_token"]





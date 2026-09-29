# amazon_service.py
import os
import time
import requests
from threading import Lock

# 🔐 GLOBAL TOKEN
amazon_token_data = {
    "access_token": None,
    "expires_at": 0
}

token_lock = Lock()


def refresh_amazon_token():
    url = "https://api.amazon.com/auth/o2/token"

    payload = {
        "grant_type": os.getenv("AMZ_GRANT_TYPE"),
        "refresh_token": os.getenv("AMZ_REFRESH_TOKEN"),
        "client_id": os.getenv("AMZ_CLIENT_ID"),
        "client_secret": os.getenv("AMZ_CLIENT_SECRET"),
    }

    headers = {
        "accept": "application/json",
        "content-type": "application/x-www-form-urlencoded",
    }

    response = requests.post(url, data=payload, headers=headers)
    data = response.json()

    amazon_token_data["access_token"] = data["access_token"]
    amazon_token_data["expires_at"] = time.time() + data["expires_in"] - 60
    # 🔥 60 sn erken yenile (safety)

    return amazon_token_data["access_token"]


def get_amazon_token():
    # token yoksa veya süresi dolmuşsa yenile
    if (
        amazon_token_data["access_token"] is None or
        time.time() > amazon_token_data["expires_at"]
    ):
        with token_lock:
            # double check (race condition önleme)
            if (
                amazon_token_data["access_token"] is None or
                time.time() > amazon_token_data["expires_at"]
            ):
                return refresh_amazon_token()

    return amazon_token_data["access_token"]
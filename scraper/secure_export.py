"""內部資料加密：AES-256-GCM，金鑰由密碼經 PBKDF2-SHA256 推導。

公開 repo／GitHub Pages 上只放加密後的檔案；瀏覽器輸入密碼後用 Web Crypto 在本機解密
（docs/index.html 的 decryptBundle），密碼不會傳到任何伺服器。

格式（JSON）：
  {"v":1, "kdf":"PBKDF2-SHA256", "iter":310000, "salt":b64, "iv":b64, "ct":b64, "updated":"YYYY-MM-DD"}
  ct = AES-GCM 密文＋16 bytes 驗證碼（與 Web Crypto 的輸出格式相同）
"""
from __future__ import annotations

import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ITERATIONS = 310_000


def _key(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, 32)


def encrypt_json(obj, password: str, updated: str = "") -> dict:
    if len(password) < 8:
        raise ValueError("密碼至少需要 8 個字元")
    salt, iv = os.urandom(16), os.urandom(12)
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ct = AESGCM(_key(password, salt, ITERATIONS)).encrypt(iv, data, None)
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": ITERATIONS,
            "salt": b64(salt), "iv": b64(iv), "ct": b64(ct), "updated": updated}


def decrypt_json(bundle: dict, password: str):
    d = lambda k: base64.b64decode(bundle[k])
    plain = AESGCM(_key(password, d("salt"), int(bundle["iter"]))).decrypt(d("iv"), d("ct"), None)
    return json.loads(plain.decode("utf-8"))

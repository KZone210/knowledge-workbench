# -*- coding: utf-8 -*-
"""加密原语模块：AES-256-GCM 认证加密，统一密文格式。

密文格式（文件/字段通用）:
    MAGIC(5B) | VER(1B) | nonce(12B) | ciphertext(变长) | tag(16B)
文件级加密采用分块 GCM，每块 AAD 携带 (nonce + 块序号)，防块重排/删除。
"""
import os
import base64
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"KBENC"
VER = b"\x01"
BLOCK = 1 << 20  # 1MB
_NONCE_LEN = 12
_HEAD_LEN = len(MAGIC) + 1 + _NONCE_LEN  # 18

# 改密/重置前自检用的固定明文（被 DEK 加密后存入 users.dek_check）
CHECK_PLAIN = b"KB-DEK-CHECK-V1"


def gen_key() -> bytes:
    """生成 256 位随机密钥（DEK / Master Key 通用）。"""
    return secrets.token_bytes(32)


def enc_bytes(dek: bytes, plain: bytes) -> bytes:
    """单块加密：MAGIC+VER+nonce+ciphertext+tag。"""
    aes = AESGCM(dek)
    nonce = secrets.token_bytes(_NONCE_LEN)
    return MAGIC + VER + nonce + aes.encrypt(nonce, plain, None)


def dec_bytes(dek: bytes, blob: bytes) -> bytes:
    """单块解密，校验 magic + GCM 认证标签。"""
    if len(blob) < _HEAD_LEN or blob[:len(MAGIC)] != MAGIC:
        raise ValueError("非本系统密文")
    nonce = blob[len(MAGIC) + 1: _HEAD_LEN]
    ct = blob[_HEAD_LEN:]
    return AESGCM(dek).decrypt(nonce, ct, None)


def enc_field(dek: bytes, plain: str) -> str:
    """字符串字段加密 → base64。空串原样返回。"""
    if not plain:
        return plain
    return base64.b64encode(enc_bytes(dek, plain.encode("utf-8"))).decode("ascii")


def dec_field(dek: bytes, blob: str) -> str:
    """解密字段。解密失败返回空串（不抛错，避免单条坏数据拖垮列表）。"""
    if not blob:
        return blob
    try:
        return dec_bytes(dek, base64.b64decode(blob)).decode("utf-8")
    except Exception:
        return ""


def encrypt_file(dek: bytes, src: str, dst: str) -> None:
    """流式分块加密文件，temp + fsync + rename 原子落盘。"""
    aes = AESGCM(dek)
    nonce = secrets.token_bytes(_NONCE_LEN)
    tmp = dst + ".tmp"
    try:
        with open(src, "rb") as fin, open(tmp, "wb") as fout:
            fout.write(MAGIC + VER + nonce)
            idx = 0
            while True:
                chunk = fin.read(BLOCK)
                if not chunk:
                    break
                aad = nonce + idx.to_bytes(8, "big")  # 块序号入 AAD，防重排
                fout.write(aes.encrypt(nonce, chunk, aad))
                idx += 1
            fout.flush()
            os.fsync(fout.fileno())
        os.replace(tmp, dst)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise


def decrypt_file_bytes(dek: bytes, enc_path: str) -> bytes:
    """解密整个加密文件到内存字节（流式分块，防大文件内存溢出）。"""
    aes = AESGCM(dek)
    with open(enc_path, "rb") as f:
        head = f.read(_HEAD_LEN)
        if len(head) < _HEAD_LEN or head[:len(MAGIC)] != MAGIC:
            raise ValueError("非本系统密文")
        nonce = head[len(MAGIC) + 1:]
        out = bytearray()
        idx = 0
        while True:
            chunk = f.read(BLOCK + 16)
            if not chunk:
                break
            aad = nonce + idx.to_bytes(8, "big")
            out += aes.decrypt(nonce, chunk, aad)
            idx += 1
    return bytes(out)


def make_dek_check(dek: bytes) -> str:
    """生成 DEK 自检值（加密固定明文）。"""
    return enc_field(dek, CHECK_PLAIN.decode("ascii"))


def verify_dek(dek: bytes, check_blob: str) -> bool:
    """改密/重置前自检：DEK 能否解开自检值。防坏钥覆盖好钥。"""
    if not check_blob:
        return True
    try:
        return dec_bytes(dek, base64.b64decode(check_blob)) == CHECK_PLAIN
    except Exception:
        return False

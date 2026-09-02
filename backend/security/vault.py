# -*- coding: utf-8 -*-
"""Vault 数据加解密层：向 store 暴露文档级/字段级接口，屏蔽密文格式细节。"""
import json

from . import crypto

# documents 表中需要加密的敏感列（enc_ver=1 后这些列均存密文）
ENC_COLS = ["filename", "title", "category", "tags", "keywords", "summary", "content", "path"]


def encrypt_doc_file(dek: bytes, src_plain_path: str, dst_enc_path: str) -> None:
    """把明文临时文件加密落盘为密文副本（temp+fsync+rename 原子）。"""
    crypto.encrypt_file(dek, src_plain_path, dst_enc_path)


def decrypt_doc_file(dek: bytes, enc_path: str) -> bytes:
    """解密文档副本为内存字节（流式，不产生明文临时文件）。"""
    return crypto.decrypt_file_bytes(dek, enc_path)


def enc_meta(dek: bytes, meta: dict) -> dict:
    """文档元数据加密：仅加密 ENC_COLS 列（tags/keywords 先 JSON 序列化），其余原样。"""
    out = dict(meta)
    for col in ENC_COLS:
        if col in out and out[col] is not None:
            val = out[col]
            if col in ("tags", "keywords") and not isinstance(val, str):
                val = json.dumps(val, ensure_ascii=False)
            out[col] = crypto.enc_field(dek, val)
    return out


def dec_row(dek: bytes, row: dict) -> dict:
    """数据库行解密（列表/详情通用）：ENC_COLS 全部解回明文。"""
    out = dict(row)
    for col in ENC_COLS:
        if col in out and out[col] is not None:
            out[col] = crypto.dec_field(dek, out[col])
    # JSON 字段还原（解密后是 JSON 字符串）
    for col in ("tags", "keywords"):
        if col in out:
            import json
            try:
                out[col] = json.loads(out[col] or "[]")
            except Exception:
                out[col] = []
    return out

"""Test stand-in: pycryptodome's PKCS1_v1_5 cipher (decrypt only)."""
from cryptography.hazmat.primitives.asymmetric import padding

STANDIN = True


class _Cipher:
    def __init__(self, key):
        self._key = key._key

    def decrypt(self, data, sentinel):
        try:
            return self._key.decrypt(bytes(data), padding.PKCS1v15())
        except ValueError:
            return sentinel


def new(key):
    return _Cipher(key)

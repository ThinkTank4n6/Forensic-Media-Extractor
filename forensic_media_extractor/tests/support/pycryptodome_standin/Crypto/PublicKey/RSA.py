"""Test stand-in: pycryptodome's RSA.import_key, for private keys only."""
from cryptography.hazmat.primitives import serialization

STANDIN = True


class _Key:
    def __init__(self, key):
        self._key = key

    @property
    def n(self):
        return self._key.private_numbers().public_numbers.n

    @property
    def e(self):
        return self._key.private_numbers().public_numbers.e

    def size_in_bits(self):
        return self._key.key_size

    def size_in_bytes(self):
        return (self._key.key_size + 7) // 8


def import_key(data, passphrase=None):
    data = bytes(data)
    loader = (serialization.load_pem_private_key if data.lstrip().startswith(b"-----")
              else serialization.load_der_private_key)
    return _Key(loader(data, password=passphrase))

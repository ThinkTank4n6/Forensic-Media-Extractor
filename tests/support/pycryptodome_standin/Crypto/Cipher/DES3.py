"""Test stand-in: pycryptodome's DES3, on the cryptography package (CBC only)."""
from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, modes

MODE_CBC = 2
block_size = 8
STANDIN = True


class _DES3:
    def __init__(self, key, iv):
        self._cipher = Cipher(TripleDES(bytes(key)), modes.CBC(bytes(iv)))

    def decrypt(self, data):
        op = self._cipher.decryptor()
        return op.update(bytes(data)) + op.finalize()


def new(key, mode, iv=None, *args):
    if mode != MODE_CBC:
        raise NotImplementedError("the stand-in has only DES3 CBC")
    return _DES3(key, iv if iv is not None else args[0])

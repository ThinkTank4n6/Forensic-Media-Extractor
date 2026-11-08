"""Test stand-in: pycryptodome's AES interface, on the cryptography package."""
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

MODE_ECB, MODE_CBC, MODE_CCM = 1, 2, 8
block_size = 16
STANDIN = True


class _AES:
    def __init__(self, key, mode, iv=None, nonce=None, mac_len=16):
        self.key, self.mode, self.iv, self.nonce, self.mac_len = key, mode, iv, nonce, mac_len

    def _run(self, data, decrypt):
        chosen = modes.ECB() if self.mode == MODE_ECB else modes.CBC(self.iv)
        cipher = Cipher(algorithms.AES(self.key), chosen)
        op = cipher.decryptor() if decrypt else cipher.encryptor()
        return op.update(bytes(data)) + op.finalize()

    def encrypt(self, data):
        return self._run(data, False)

    def decrypt(self, data):
        return self._run(data, True)

    def decrypt_and_verify(self, data, tag):
        try:
            return AESCCM(self.key, tag_length=self.mac_len).decrypt(
                self.nonce, bytes(data) + bytes(tag), None)
        except InvalidTag:
            raise ValueError("MAC check failed") from None


def new(key, mode, *args, iv=None, nonce=None, mac_len=16):
    if mode not in (MODE_ECB, MODE_CBC, MODE_CCM):
        raise NotImplementedError(f"the stand-in has no AES mode {mode}")
    return _AES(key, mode, iv=args[0] if args else iv, nonce=nonce, mac_len=mac_len)

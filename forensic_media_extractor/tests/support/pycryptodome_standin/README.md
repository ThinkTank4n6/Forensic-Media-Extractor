Test support only; never on the program's path. Presents the `cryptography`
package as the three AES modes of pycryptodome's `Crypto.Cipher.AES` that the
vendored BitLocker and APFS readers use (ECB, CBC, CCM), and -- since
v2026.11.315 -- DES3 (CBC), `PublicKey.RSA.import_key` and
`Cipher.PKCS1_v1_5`, which the acquisition reader uses for an encrypted AFF
and for an image sealed to a certificate, so the unlock tests
run on a machine without pycryptodome. A test that uses it says so; where
pycryptodome is installed (requirements.txt), it is the real one that is tested.

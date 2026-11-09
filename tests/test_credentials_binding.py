"""A Content Credential's hash of the file counts only if the signature covers it.

The signature is over the claim; the claim lists each assertion with a hash
of its JUMBF box; the c2pa.hash.data assertion holds the hash of the file.
verify() read that assertion straight out of the manifest and never held it
to the hash the signed claim lists. So a genuinely signed manifest moved
into a different picture, with nothing changed but its hash box -- rewritten
for the new picture's bytes -- reported "the signature holds and the
manifest's hash of the file matches this file": the one sentence that says
the credential belongs to this picture.

Each file here is built in full: a JPEG carrying a manifest store in APP11
segments (tests/support/c2pabuild.py), a c2pa.hash.data assertion excluding
those segments, a claim listing it by hashed URI, and an ES256 signature
over the claim made with a key generated for the run.
"""
import datetime as _dt
import hashlib
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(HERE, "support"))
import c2pabuild as build
from fme.engines import credentials_verify as verify

ROOT = os.path.join(tempfile.gettempdir(), "fme_credentials_binding-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail else ''} -- "
          f"{'ok' if good else 'WRONG'}")


ready, why = verify.available()
if not ready:
    print(f"skipped: {why}")
    sys.exit(0)

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric import utils as _au
from cryptography.x509.oid import NameOID

KEY = ec.generate_private_key(ec.SECP256R1())
_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Camera Co")])
_now = _dt.datetime.now(_dt.timezone.utc)
CERT = (x509.CertificateBuilder().subject_name(_name).issuer_name(_name)
        .public_key(KEY.public_key()).serial_number(7)
        .not_valid_before(_now - _dt.timedelta(days=1))
        .not_valid_after(_now + _dt.timedelta(days=365))
        .sign(KEY, hashes.SHA256()).public_bytes(serialization.Encoding.DER))
ASSERTIONS = "c2pa.assertions/c2pa.hash.data"


def scan(seed):
    """Different entropy data, so two pictures differ."""
    return bytes((seed * 31 + i * 7) % 255 for i in range(200)).replace(b"\xff", b"\x00")


def hash_box(start, length, digest):
    return build.cbor_box("c2pa.hash.data", {
        "exclusions": [{"start": start, "length": length}],
        "name": "jumbf manifest", "alg": "sha256", "hash": digest, "pad": b""})


def signed_claim(hash_assertion, listed=True):
    claim = {"dc:format": "image/jpeg", "instanceID": "xmp:iid:test",
             "claim_generator": "Test Camera 1.0", "alg": "sha256",
             "assertions": []}
    if listed:
        # The hashed URI: the assertion's superbox contents, header excluded.
        claim["assertions"].append({"url": "self#jumbf=" + ASSERTIONS,
                                    "hash": hashlib.sha256(hash_assertion[8:]).digest()})
    payload = build.cbor(claim)
    protected = build.cbor({1: -7, 33: [CERT]})
    der = KEY.sign(verify.sig_structure(protected, payload), ec.ECDSA(hashes.SHA256()))
    r, s = _au.decode_dss_signature(der)
    cose = [protected, {}, None, r.to_bytes(32, "big") + s.to_bytes(32, "big")]
    return payload, cose


def store(assertion, payload, cose):
    return build.store(build.superbox(
        build.MANIFEST_UUID, "urn:c2pa:test-binding",
        build.superbox(build.ASSERTIONS_UUID, "c2pa.assertions", assertion),
        build.superbox(build.CLAIM_UUID, "c2pa.claim", build.box(b"cbor", payload)),
        build.superbox(build.SIGNATURE_UUID, "c2pa.signature",
                       build.box(b"cbor", build.tagged(18, cose)))))


def picture(c2pa, seed):
    data = build.jpeg(c2pa=c2pa)
    at = data.index(b"\xff\xda")
    return data[:at + 10] + scan(seed) + b"\xff\xd9"


def excluded(data):
    start = data.index(b"\xff\xeb")
    return start, data.index(b"\xff\xdb\x00\x43") - start


def file_hash(data, start, length):
    return hashlib.sha256(data[:start] + data[start + length:]).digest()


def made_for(seed, listed=True):
    """A picture whose manifest was signed for it. Sizes do not depend on
    the hash or the signature's value, so a first pass with stand-ins gives
    the excluded range."""
    blank = hash_box(0, 0, bytes(32))
    rough = picture(store(blank, *signed_claim(blank, listed)), seed)
    start, length = excluded(rough)
    for _ in range(3):
        assertion = hash_box(start, length, bytes(32))
        rough = picture(store(assertion, *signed_claim(assertion, listed)), seed)
        start, length = excluded(rough)
    assertion = hash_box(start, length, file_hash(rough, start, length))
    payload, cose = signed_claim(assertion, listed)
    final = picture(store(assertion, payload, cose), seed)
    assert excluded(final) == (start, length)
    return final, payload, cose


def written(name, data):
    path = os.path.join(ROOT, name)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


print("a manifest signed for its own picture")
genuine, payload, cose = made_for(1)
result = verify.verify(written("genuine.jpg", genuine))
binding = result.get("content_binding") or {}
check("the signature holds", result.get("signature_holds"), result.get("why"))
check("and the hash of the file, covered by the signature, matches",
      binding.get("checked") and binding.get("matches"), binding.get("why"))
check("the sentence says so", "hash of the file matches this file" in result.get("plain", ""))

print("\nthe same signed manifest moved into another picture, its hash box rewritten")
other = picture(store(hash_box(0, 0, bytes(32)), payload, cose), 2)
start, length = excluded(other)
forged_box = hash_box(start, length, bytes(32))
other = picture(store(forged_box, payload, cose), 2)
start, length = excluded(other)
forged_box = hash_box(start, length, file_hash(other, start, length))
transplanted = picture(store(forged_box, payload, cose), 2)
assert excluded(transplanted) == (start, length)
assert transplanted != genuine
result = verify.verify(written("transplanted.jpg", transplanted))
binding = result.get("content_binding") or {}
plain = result.get("plain", "")
check("the signature still holds -- the claim was not touched",
      result.get("signature_holds"))
check("but the hash box is not the one the signed claim lists",
      binding.get("matches") is False and "changed after signing" in binding.get("why", ""),
      binding.get("why"))
check("and the sentence does not say the hash matches",
      "hash of the file matches" not in plain and "does not describe this file" in plain,
      plain[:120])

print("\na claim that does not list its hash of the file")
unlisted, _, _ = made_for(3, listed=False)
result = verify.verify(written("unlisted.jpg", unlisted))
binding = result.get("content_binding") or {}
plain = result.get("plain", "")
check("the binding is not claimed: the signature does not cover it",
      binding.get("checked") is False and "not covered by the signature" in binding.get("why", ""),
      binding.get("why"))
check("and the sentence says it was not checked",
      "hash of the file matches" not in plain and "was not checked" in plain)

print("\nwhere a hashed URI points")
check("a relative URI to this assertion", verify._points_at(
    "self#jumbf=c2pa.assertions/c2pa.hash.data", "urn:a", "c2pa.hash.data"))
check("an absolute one into this manifest", verify._points_at(
    "self#jumbf=/c2pa/urn:a/c2pa.assertions/c2pa.hash.data", "urn:a", "c2pa.hash.data"))
check("not one into another manifest", not verify._points_at(
    "self#jumbf=/c2pa/urn:b/c2pa.assertions/c2pa.hash.data", "urn:a", "c2pa.hash.data"))
check("not another assertion's label", not verify._points_at(
    "self#jumbf=c2pa.assertions/c2pa.hash.data__1", "urn:a", "c2pa.hash.data"))

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)

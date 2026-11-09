"""Content Credentials: what a file states about itself.

Every major image generator writes a C2PA manifest saying the picture was
made by a trained model. An examiner deciding whether something is
AI-generated is otherwise doing it by eye, so a file that states what made
it is evidence about that judgment.

The reading is done by the vendored c2pareader. The first version of this
was written here, and was replaced because the vendored reader refused a
hand-built manifest whose JUMBF type was not a real C2PA store -- which the
version written here had accepted. A reader that accepts not-quite-C2PA
will one day call something a credential that is not one.

What must never slip is the caveat. Nothing is verified: no signature is
checked, no chain is built. A manifest can be copied between files, a
signer is a name in the file, and absence means something only where the
reader walked the whole container.
"""
import tempfile
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from fme.engines import credentials

ok = True

if not credentials.available():
    print("the vendored Content Credentials reader is absent; skipping")
    sys.exit(0)

# Files built to the specification by the builder that ships with the
# reader, so a passing test shows reader and writer agree about the format.
sys.path.insert(0, os.path.join(HERE, "support"))
try:
    import c2pabuild as build
except ImportError:
    print("the C2PA fixture builder is absent; skipping")
    sys.exit(0)

# One directory per process. Twenty-seven tests used a

# fixed path here and each wipes it on startup, so two

# runs overlapping -- which happens whenever a batch is

# cut short and its child keeps going -- had the second

# deleting the first's working files mid-run. That is

# the only mechanism found for this suite's intermittent

# failures.

ROOT = os.path.join(tempfile.gettempdir(), "credentials_test-") + str(os.getpid())
os.makedirs(ROOT, exist_ok=True)


def written(name, data):
    path = os.path.join(ROOT, name)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


print("a picture that says a model made it")
generated = build.store(build.manifest("urn:uuid:a", generator="Imagen 3.0"))
for name, container in (("ai.png", build.png(c2pa=generated)),
                        ("ai.jpg", build.jpeg(c2pa=generated))):
    found = credentials.read(written(name, container))
    ok &= found["present"] and found["says_ai_generated"]
    print(f"  {name:7} {found['claim_generator']!r} — "
          f"{[s['means'] for s in found['source_types']]} — "
          f"AI: {found['says_ai_generated']}")

print("\na photograph says something else")
shot = build.store(build.manifest("urn:uuid:b", generator="Pixel 9 Pro",
                                  source_types=("digitalCapture",)))
found = credentials.read(written("camera.png", build.png(c2pa=shot)))
ok &= found["present"] and not found["says_ai_generated"]
print(f"  {found['claim_generator']!r} — "
      f"{[s['means'] for s in found['source_types']]} — "
      f"AI: {found['says_ai_generated']}")

print("\nwhat it will not claim")
ok &= found.get("verified") is False
print(f"  verified: {found['verified']}")
for phrase in ("No signature was checked", "copied into another file",
               "name in the file"):
    there = phrase in found.get("caveat", "")
    ok &= there
    print(f"  the caveat says \"{phrase}\": {there}")

# A signer is reported because it is in the file, and is not evidence of
# who signed anything.
print(f"  signer reported as stated: {found.get('signer') or '(none)'}")

print("\nabsence, and when it means anything")
nothing = credentials.read(written("plain.png", build.png()))
ok &= not nothing["present"] and nothing["absence_established"]
print(f"  a whole file with none: present={nothing['present']}, "
      f"walk complete={nothing['complete']}, "
      f"absence established={nothing['absence_established']}")

# A file that cannot be walked to its end has been shown nothing: "no
# credentials" and "could not look" are different answers.
cut = build.png(c2pa=generated)[:40]
stopped = credentials.read(written("cut.png", cut))
ok &= not stopped["present"]
ok &= stopped.get("absence_established") is False
print(f"  a truncated file:      present={stopped['present']}, "
      f"absence established={stopped['absence_established']}")

# Reading a manifest says what a file claims. Checking the signature says
# whether the claim is intact and came from this file -- which is the
# difference between a credential that means something and one that was
# copied in from elsewhere.
print("\nchecking the signature, not just reading it")
from fme.engines import credentials_verify as verify

ready, why = verify.available()
if not ready:
    print(f"  skipped: {why}")
else:
    import datetime as _dt

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric import utils as _au
    from cryptography.x509.oid import NameOID

    def certificate(common_name, issuer_key=None, issuer_name=None):
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
        now = _dt.datetime.now(_dt.timezone.utc)
        made = (x509.CertificateBuilder().subject_name(subject)
                .issuer_name(issuer_name or subject)
                .public_key(key.public_key()).serial_number(7)
                .not_valid_before(now - _dt.timedelta(days=1))
                .not_valid_after(now + _dt.timedelta(days=365))
                .sign(issuer_key or key, hashes.SHA256()))
        return key, made, subject

    signer_key, signer_cert, _ = certificate("Test Camera Co")
    protected = b"\xa1\x01\x26"            # {1: -7}, meaning ES256
    claim = b"the claim bytes"
    signed_over = verify.sig_structure(protected, claim)
    der = signer_key.sign(signed_over, ec.ECDSA(hashes.SHA256()))
    r, s_ = _au.decode_dss_signature(der)
    raw = r.to_bytes(32, "big") + s_.to_bytes(32, "big")

    holds, trouble = verify._verify_with(signer_cert, "ES256", raw,
                                         signed_over)
    ok &= holds
    print(f"  a genuine signature holds: {holds}")

    # The case this exists for: a manifest copied into another file, or a
    # claim altered after signing.
    altered = verify.sig_structure(protected, claim + b"!")
    holds, trouble = verify._verify_with(signer_cert, "ES256", raw, altered)
    ok &= not holds
    ok &= "altered since it was signed" in trouble
    print(f"  an altered claim is caught: {not holds}")

    print("\n  and who signed it")
    authority_key, authority_cert, authority_name = certificate("An Authority")
    trust_list = os.path.join(ROOT, "trust.pem")
    with open(trust_list, "wb") as fh:
        fh.write(authority_cert.public_bytes(serialization.Encoding.PEM))

    _, issued, _ = certificate("Honest Camera Co", authority_key,
                               authority_name)
    trusted = verify._trusted([issued], trust_list)
    ok &= trusted.get("trusted") is True
    print(f"  a certificate from the authority: trusted")

    # Anybody can put any name on a certificate. A valid signature from a
    # self-made "Adobe Inc." is the dangerous case, because the signature
    # really does hold.
    _, pretender, _ = certificate("Adobe Inc.")
    untrusted = verify._trusted([pretender], trust_list)
    ok &= untrusted.get("trusted") is False
    print(f"  a self-made \"Adobe Inc.\": not trusted, though its signature "
          f"would hold")

    sentence = verify._plain(True, "", untrusted, pretender)
    ok &= "Anybody can make a certificate with any name on it" in sentence
    print(f"  and the result says so in words: True")

    # With no trust list, no signer is ever called verified.
    nothing = verify._trusted([issued], "")
    ok &= nothing.get("checked") is False and "unknown" in nothing["why"]
    print(f"  with no trust list, no signer is verified: True")

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)

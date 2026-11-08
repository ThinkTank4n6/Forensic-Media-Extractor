"""
credentials_verify.py -- checking whether a Content Credential's signature
actually holds.

Reading a C2PA manifest tells you what a file *says*. This checks whether
the saying was signed, by whom, and whether the bytes have changed since.
Until now neither this program nor the tool it was compared against did
any of that, and every result had to carry the words "nothing here is
verified".

**What a valid signature proves, exactly.** That the claim was signed by
the private key belonging to the certificate in the file, and that neither
has been altered since. On its own it does not rule out a manifest copied
whole from one file into another -- that is what the manifest's hash of the
file (its c2pa.hash.data assertion) is for, and verify() checks that too
where the file is bound that way, and says so where it is not.

**What it does not prove.** Who that certificate belongs to. Anybody can
make a certificate saying anything -- including one that says "Adobe" --
and sign a manifest with it. The name is only worth something if the
certificate chains to an issuer you have decided to trust, which is what a
trust list is for. C2PA publishes one; this program does not ship it,
because a trust list that arrives with a forensic tool and is never updated
is worse than none. Settings takes the path to a PEM bundle, and without
one every result says the signer is unverified however good the signature
is.

So there are three answers here, and they are different:

    signature invalid      the file has been altered since it was signed,
                           or the manifest was copied from another file
    signature valid,       the claim is intact and was signed by the key in
    signer not trusted     the file -- but who holds that key is unknown
    signature valid,       the claim is intact and the certificate chains
    signer trusted         to an issuer in the trust list you supplied

The third is the only one where a signer's name means anything, and even
then it means "this certificate chains to something you chose to trust",
not "this picture is true".
"""

from __future__ import annotations

import datetime as _dt
import os
import sys

__version__ = "1.0.0"

VENDOR_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "vendor")


def available() -> tuple:
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return (False, "needs the cryptography module: pip install cryptography")
    if VENDOR_DIR not in sys.path:
        sys.path.insert(0, VENDOR_DIR)
    try:
        import c2pareader  # noqa: F401
    except ImportError:
        return (False, "the vendored Content Credentials reader is absent")
    return (True, "")


# -- just enough CBOR to rebuild what was signed -------------------------
# The vendored reader decodes CBOR; verifying needs it encoded again,
# because what gets signed is a structure built around the claim rather
# than the claim itself.

def _cbor_head(major: int, length: int) -> bytes:
    if length < 24:
        return bytes([(major << 5) | length])
    if length < 256:
        return bytes([(major << 5) | 24, length])
    if length < 65536:
        return bytes([(major << 5) | 25]) + length.to_bytes(2, "big")
    if length < 4294967296:
        return bytes([(major << 5) | 26]) + length.to_bytes(4, "big")
    return bytes([(major << 5) | 27]) + length.to_bytes(8, "big")


def _cbor_bytes(value: bytes) -> bytes:
    return _cbor_head(2, len(value)) + value


def _cbor_text(value: str) -> bytes:
    raw = value.encode("utf-8")
    return _cbor_head(3, len(raw)) + raw


def sig_structure(protected: bytes, payload: bytes,
                  external: bytes = b"") -> bytes:
    """The bytes a COSE_Sign1 signature is actually over (RFC 8152 §4.4).

    Not the claim, and not the manifest: a four-element array built around
    them. Signing the wrong thing is the commonest way a verifier wrongly
    reports every signature as invalid, so this follows the structure
    exactly.
    """
    return (_cbor_head(4, 4)
            + _cbor_text("Signature1")
            + _cbor_bytes(protected)
            + _cbor_bytes(external)
            + _cbor_bytes(payload))


def _certificates(cose) -> list:
    """The certificate chain a COSE_Sign1 carries, leaf first."""
    from cryptography import x509

    chain = []
    for header in (cose[0], cose[1]):
        values = header
        if isinstance(header, (bytes, bytearray)) and header:
            import c2pareader
            try:
                values = c2pareader._cbor(bytes(header))
            except Exception:  # noqa: BLE001
                continue
        if not isinstance(values, dict):
            continue
        found = values.get(33)                 # x5chain
        if found is None:
            continue
        items = found if isinstance(found, list) else [found]
        for item in items:
            if isinstance(item, (bytes, bytearray)):
                try:
                    chain.append(x509.load_der_x509_certificate(bytes(item)))
                except Exception:  # noqa: BLE001 - not a certificate
                    continue
    return chain


def _verify_with(certificate, algorithm, signature: bytes, signed: bytes):
    """Does this signature hold against this certificate's key?"""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
    from cryptography.hazmat.primitives.asymmetric import utils as asym_utils

    key = certificate.public_key()
    digest = {"ES256": hashes.SHA256(), "ES384": hashes.SHA384(),
              "ES512": hashes.SHA512(), "PS256": hashes.SHA256(),
              "PS384": hashes.SHA384(), "PS512": hashes.SHA512(),
              "RS256": hashes.SHA256()}.get(algorithm)
    if digest is None:
        return (False, f"this program does not check {algorithm} signatures")

    try:
        if isinstance(key, ec.EllipticCurvePublicKey):
            # COSE carries r and s as fixed-width halves; x509 wants DER.
            half = len(signature) // 2
            r = int.from_bytes(signature[:half], "big")
            s = int.from_bytes(signature[half:], "big")
            key.verify(asym_utils.encode_dss_signature(r, s), signed,
                       ec.ECDSA(digest))
        elif isinstance(key, rsa.RSAPublicKey):
            if algorithm.startswith("PS"):
                key.verify(signature, signed,
                           padding.PSS(mgf=padding.MGF1(digest),
                                       salt_length=digest.digest_size),
                           digest)
            else:
                key.verify(signature, signed, padding.PKCS1v15(), digest)
        else:
            return (False, "the certificate's key is of a kind this does "
                           "not check")
    except InvalidSignature:
        return (False, "the signature does not hold: the claim has been "
                       "altered since it was signed, or this manifest came "
                       "from a different file")
    except Exception as exc:  # noqa: BLE001
        return (False, f"the signature could not be checked: {exc}")
    return (True, "")


def _trusted(chain, trust_list_path: str) -> dict:
    """Does this chain reach an issuer the examiner chose to trust?"""
    if not trust_list_path:
        return {"checked": False,
                "why": ("no trust list is set, so who holds the signing "
                        "certificate is unknown. Settings takes a PEM "
                        "bundle; C2PA publishes one.")}
    if not os.path.isfile(trust_list_path):
        return {"checked": False, "why": f"no trust list at {trust_list_path}"}

    from cryptography import x509

    try:
        with open(trust_list_path, "rb") as fh:
            anchors = x509.load_pem_x509_certificates(fh.read())
    except Exception as exc:  # noqa: BLE001
        return {"checked": False, "why": f"the trust list would not read: {exc}"}

    # Checked by signature, not by name. A certificate counted as trusted
    # when its issuer's *name* matched an anchor's -- and anyone can write any
    # issuer name into a certificate they sign themselves, so a forged
    # "Adobe Inc." naming a real root as issuer came back trusted. Each link
    # is now verified with the next one's key, up to an anchor whose key
    # verifies the last.
    path = [cert for cert in chain]
    for child, parent in zip(path, path[1:]):
        if not _issued_by(child, parent):
            return {"checked": True, "trusted": False,
                    "why": ("the certificates in the file do not form a chain "
                            "-- one is not signed by the next -- so the "
                            "signer's name is a name in the file and nothing "
                            "more")}
    for certificate in path:
        for anchor in anchors:
            if certificate == anchor or _issued_by(certificate, anchor):
                return {"checked": True, "trusted": True,
                        "anchor": anchor.subject.rfc4514_string()}
    return {"checked": True, "trusted": False,
            "why": ("the chain in the file does not reach any issuer in "
                    "your trust list, so the signer's name is a name in "
                    "the file and nothing more")}


def _issued_by(child, parent) -> bool:
    """Was `child` signed with `parent`'s key (and named it as issuer)?"""
    try:
        child.verify_directly_issued_by(parent)
        return True
    except Exception:  # noqa: BLE001 - any failure is "not issued by"
        return False


def verify(path: str, trust_list_path: str = "") -> dict:
    """Check the signature on a file's Content Credential."""
    ready, why = available()
    if not ready:
        return {"checked": False, "error": why}

    import c2pareader

    try:
        found = c2pareader.probe(path)
    except Exception as exc:  # noqa: BLE001
        return {"checked": False, "error": str(exc)}

    manifests = found.get("manifests") or []
    if not manifests:
        return {"checked": False, "present": False,
                "why": "this file carries no Content Credential"}

    raw = _raw_signature(path)
    if raw is None:
        return {"checked": False, "present": True,
                "why": ("the signature could not be read out of the file in "
                        "a form that can be checked")}

    protected, payload, signature, cose, manifest, data = raw
    chain = _certificates(cose)
    if not chain:
        return {"checked": False, "present": True,
                "why": "the manifest carries no certificate to check against"}

    import c2pareader as reader

    algorithm = ""
    try:
        header = reader._cbor(protected) if protected else {}
        algorithm = reader._COSE_ALG.get(header.get(1), "")
    except Exception:  # noqa: BLE001
        pass

    holds, trouble = _verify_with(chain[0], algorithm, signature,
                                  sig_structure(protected, payload))
    leaf = chain[0]
    now = _dt.datetime.now(_dt.timezone.utc)
    try:
        not_before = leaf.not_valid_before_utc
        not_after = leaf.not_valid_after_utc
    except AttributeError:                      # older cryptography
        not_before = leaf.not_valid_before.replace(tzinfo=_dt.timezone.utc)
        not_after = leaf.not_valid_after.replace(tzinfo=_dt.timezone.utc)

    trust = _trusted(chain, trust_list_path)
    # The claim the signature is over, so the hash assertion can be held to
    # the hash the signed claim lists for it.
    binding = _content_binding(manifest, data, payload)

    return {
        "checked": True,
        "present": True,
        "signature_holds": holds,
        "why": trouble,
        "algorithm": algorithm,
        "signed_by": leaf.subject.rfc4514_string(),
        "issuer": leaf.issuer.rfc4514_string(),
        "valid_from": not_before.isoformat(),
        "valid_to": not_after.isoformat(),
        "certificate_in_date": bool(not_before <= now <= not_after),
        "chain_length": len(chain),
        "trust": trust,
        "content_binding": binding,
        # The sentence an examiner should write down, rather than one they
        # have to compose from the fields above.
        "plain": _plain(holds, trouble, trust, leaf, binding),
    }


def _plain(holds: bool, trouble: str, trust: dict, leaf,
           binding: dict = None) -> str:
    binding = binding or {"checked": False,
                          "why": "the manifest's hash of the file was not checked"}
    if not holds:
        return (f"The signature does not hold. {trouble} This manifest does "
                f"not belong to this file as it now stands.")
    name = leaf.subject.rfc4514_string()
    # Whether the manifest belongs to this file is the content binding, not
    # the signature: a manifest copied whole into another file still has a
    # valid signature. This said "it did not come from another file" without
    # checking.
    if binding.get("checked") and not binding.get("matches"):
        return (f"The signature holds, but {binding['why']}. The claim was "
                f"signed by the key in the file ({name}); it does not describe "
                f"this file as it now stands.")
    bound = ("and the manifest's hash of the file matches this file"
             if binding.get("checked") else
             f"but whether the manifest was made for this file was not "
             f"checked ({binding.get('why', '')})")
    if trust.get("trusted"):
        return (f"The signature holds, {bound}, and the certificate chains to "
                f"an issuer in your trust list. The claim was signed by "
                f"{name}. That is who signed it -- not that what it says is "
                f"true.")
    return (f"The signature holds: the claim has not been altered since it "
            f"was signed, {bound}. The certificate says {name}, but "
            f"{trust.get('why', 'the signer is not verified')}. Anybody can "
            f"make a certificate with any name on it.")


def _stores(data: bytes) -> list:
    """The manifest stores in a file, as the reader reassembles them.

    From the reader's walk, not from the offsets in probe()'s summary: a
    JPEG's store is split over APP11 segments, each with its own header, so
    no single range of the file is the store. Reading `start`/`end` keys the
    summary never had meant no signature was ever found to check.
    """
    import io

    import c2pareader as reader

    found = reader._walk(io.BytesIO(data), len(data))
    return [store for _where, _offset, store in found.stores]


def _labelled(blob: bytes, depth: int = 0):
    """Every labelled superbox under `blob`, as (label, content, superbox):
    `content` is the raw body of its first content box (the CBOR, undecoded,
    since a signature is over bytes), `superbox` the raw jumb body."""
    import c2pareader as reader

    if depth > 16:
        return
    for kind, body in reader._boxes(blob):
        if kind != "jumb":
            continue
        label, content = "", None
        for inner, payload in reader._boxes(body):
            if inner == "jumd":
                label = reader._jumd(payload)[1] or ""
            elif inner != "jumb" and content is None:
                content = payload
        yield label, content, body
        yield from _labelled(body, depth + 1)


def _active_manifest(store: bytes):
    """The last manifest in a store (the active one), as its jumb body."""
    import c2pareader as reader

    for kind, body in reader._boxes(store):
        if kind == "jumb":
            manifests = [m for k, m in reader._boxes(body) if k == "jumb"]
            return manifests[-1] if manifests else None
    return None


def _raw_signature(path: str):
    """Find the COSE_Sign1 in the file, as bytes that can be checked.

    Returns (protected, payload, signature, cose, manifest, data) or None.
    """
    import c2pareader as reader

    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    try:
        stores = _stores(data)
    except Exception:  # noqa: BLE001
        return None
    for store in stores[:1]:            # the store the reader decodes
        manifest = _active_manifest(store)
        if manifest is None:
            continue
        signature_box = claim = None
        for label, content, _body in _labelled(manifest):
            if label.startswith("c2pa.signature") and signature_box is None:
                signature_box = content
            elif label.startswith("c2pa.claim") and claim is None:
                claim = content
        if signature_box is None:
            continue
        try:
            cose = reader._cbor(signature_box)
        except Exception:  # noqa: BLE001
            continue
        if not (isinstance(cose, list) and len(cose) >= 4):
            continue
        protected = bytes(cose[0]) if isinstance(
            cose[0], (bytes, bytearray)) else b""
        body = cose[2]
        signature = bytes(cose[3]) if isinstance(
            cose[3], (bytes, bytearray)) else b""
        payload_bytes = (bytes(body) if isinstance(body, (bytes, bytearray))
                         else claim)
        if not signature or payload_bytes is None:
            continue
        return protected, payload_bytes, signature, cose, manifest, data
    return None


_HASHES = {"sha256": "sha256", "sha384": "sha384", "sha512": "sha512"}


def _hash_name(algorithm) -> str:
    """hashlib's name for a C2PA algorithm identifier, or ""."""
    return _HASHES.get(str(algorithm or "").lower().replace("-", ""), "")


def _manifest_label(manifest: bytes) -> str:
    import c2pareader as reader

    for kind, body in reader._boxes(manifest):
        if kind == "jumd":
            return reader._jumd(body)[1] or ""
    return ""


def _points_at(url: str, manifest_label: str, label: str) -> bool:
    """Does a claim's hashed URI name the assertion `label` in this manifest?

    A URI is "self#jumbf=c2pa.assertions/<label>" (relative to the claim's
    manifest) or "self#jumbf=/c2pa/<manifest>/c2pa.assertions/<label>". One
    naming another manifest's assertion of the same label is not this one.
    """
    path = str(url or "").split("#jumbf=", 1)[-1]
    parts = [p for p in path.split("/") if p]
    if not parts or parts[-1] != label:
        return False
    if "c2pa.assertions" in parts:
        at = parts.index("c2pa.assertions")
        if at >= 1 and parts[at - 1] != "c2pa" and manifest_label \
                and parts[at - 1] != manifest_label:
            return False
    return True


def _signed_reference(claim_bytes, manifest: bytes, label: str, body: bytes):
    """Is the assertion box `body` (a jumb superbox's contents) the one the
    signed claim lists under `label`? Returns (True, "") or (False, why).

    The claim lists each assertion as a hashed URI: where it is and a hash of
    its JUMBF superbox's contents -- description box and content boxes, not
    the superbox's own header -- under the claim's algorithm unless the URI
    names its own. That hash is what puts an assertion under the signature.
    Without this check the c2pa.hash.data box was read as it stood, so a
    valid signed manifest moved into another picture, with only its hash
    box rewritten for the new bytes, reported that the signature held and
    the hash matched.
    """
    import hashlib
    import struct

    import c2pareader as reader

    try:
        claim = reader._cbor(bytes(claim_bytes)) if claim_bytes else None
    except Exception:  # noqa: BLE001
        claim = None
    if not isinstance(claim, dict):
        return False, ("the signed claim could not be read, so whether it "
                       "covers the manifest's hash of the file is unknown")
    listed = []
    for key in ("assertions", "created_assertions", "gathered_assertions"):
        found = claim.get(key)
        if isinstance(found, list):
            listed += [ref for ref in found if isinstance(ref, dict)]
    here = _manifest_label(manifest)
    refs = [ref for ref in listed if _points_at(ref.get("url"), here, label)]
    if not refs:
        return False, ("the signed claim does not list the manifest's hash of "
                       "the file, so that hash is not covered by the signature")
    for ref in refs:
        name = _hash_name(ref.get("alg") or claim.get("alg") or "sha256")
        want = ref.get("hash")
        if not name or not isinstance(want, (bytes, bytearray)):
            return False, ("the signed claim's hash of the manifest's hash of "
                           "the file uses an algorithm this program does not "
                           "check")
        # Over the superbox's contents, as C2PA's "Hashing JUMBF Boxes"
        # says; and over the whole box, header included, so that a producer
        # reading the rule the other way is not reported as tampered with.
        # Either is a commitment the signer made; neither can be met by a
        # box edited after signing.
        whole = struct.pack(">I", 8 + len(body)) + b"jumb" + bytes(body)
        if bytes(want) in (hashlib.new(name, bytes(body)).digest(),
                           hashlib.new(name, whole).digest()):
            return True, ""
    return None, ("the manifest's hash of the file is not the one the signed "
                  "claim lists: it was changed after signing")


def _content_binding(manifest: bytes, data: bytes, claim_bytes=None) -> dict:
    """Does the manifest's hash of the file match the file?

    The signature covers the claim; the claim covers the assertions; the
    c2pa.hash.data assertion covers the file's bytes, less the stretch the
    manifest itself occupies. Only that last check says the manifest belongs
    to *these* bytes -- a manifest copied verbatim into another file still
    has a valid signature. And the middle link is checked too: the hash
    assertion counts only if it is the one the signed claim (`claim_bytes`,
    the payload the signature is over) lists. Other bindings
    (c2pa.hash.bmff for video, c2pa.hash.boxes) are not checked here, and
    the result says so.
    """
    import hashlib

    import c2pareader as reader

    for label, content, body in _labelled(manifest):
        base = label.split("__")[0]
        if base in ("c2pa.hash.bmff", "c2pa.hash.bmff.v2", "c2pa.hash.bmff.v3",
                    "c2pa.hash.boxes", "c2pa.hash.collection.data"):
            return {"checked": False,
                    "why": f"the file is bound by {base}, which this program "
                           f"does not check"}
        if base != "c2pa.hash.data" or content is None:
            continue
        covered, why = _signed_reference(claim_bytes, manifest, label, body)
        if covered is None:
            # Listed, and different: the assertion was edited after signing.
            return {"checked": True, "matches": False, "why": why}
        if not covered:
            return {"checked": False, "why": why}
        try:
            assertion = reader._cbor(content)
            algorithm = str(assertion.get("alg") or "sha256").lower()
            expected = bytes(assertion.get("hash") or b"")
            exclusions = sorted(
                (int(e.get("start")), int(e.get("length")))
                for e in assertion.get("exclusions") or [])
        except Exception as exc:  # noqa: BLE001
            return {"checked": False,
                    "why": f"its hash of the file could not be read: {exc}"}
        name = _hash_name(algorithm)
        if not name or not expected:
            return {"checked": False,
                    "why": f"its hash of the file uses {algorithm}, which "
                           f"this program does not check"}
        digest, at = hashlib.new(name), 0
        for start, length in exclusions:
            if start < at or start + length > len(data):
                return {"checked": True, "matches": False,
                        "why": "its exclusions do not fit this file"}
            digest.update(data[at:start])
            at = start + length
        digest.update(data[at:])
        matches = digest.digest() == expected
        return {"checked": True, "matches": matches,
                "why": "" if matches else (
                    "the file's bytes are not the bytes the manifest was made "
                    "for: the file was changed after signing, or the manifest "
                    "was copied in from another file")}
    return {"checked": False,
            "why": "the manifest carries no hash of the file to check"}

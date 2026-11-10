"""Web Push, so a phone that is not looking still hears about a long job.

The point of this file is the one thing the browser cannot do for itself: a
notification that arrives when the app is closed and the phone is in a pocket.
That needs a push service - for Chrome, Google's - and a push service will only
take a message that is signed. That signature is the whole reason this module
exists, and the only reason `cryptography` is a dependency of a program that is
otherwise standard library from end to end.

**The messages carry nothing.** A push can have an encrypted payload; this one
never does. The service worker is woken with an empty knock, and it then asks
*this* machine what to say. Two things fall out of that. Your folder names,
your shoot names and your counts never travel through Google. And the whole
AES-GCM and ECDH half of the Web Push spec simply does not apply, which is a
lot of delicate code not written - all that is left is one signature.

A subscription is a device saying "wake me here". It is not a secret and it is
not a credential: it lets this machine knock on that browser, nothing more.
They live in ~/.cullkit/push.json next to the keys, and one that has expired is
dropped the first time the push service says so (404 or 410).
"""
from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from pathlib import Path

STORE = Path.home() / ".cullkit" / "push.json"
_lock = threading.Lock()

# Identifies this server to the push service. It is meant to be a way to
# contact whoever runs the sender if it misbehaves; nothing is ever sent here.
# Push services ask for a way to reach whoever runs the sender. Put your own.
CONTACT = "mailto:you@example.com"


def _b64(raw: bytes) -> str:
    """base64url with no padding - what every part of Web Push expects."""
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _load() -> dict:
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(d: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(d, indent=2), encoding="utf-8")


def available() -> bool:
    """Whether this machine can sign at all. False keeps every caller quiet
    rather than raising in a background thread nobody is watching."""
    try:
        import cryptography  # noqa: F401
        return True
    except Exception:
        return False


def keys() -> dict:
    """The VAPID key pair, made once and kept.

    The public half is what a browser needs before it will hand out a
    subscription, and it must not change afterwards: every subscription already
    granted is tied to the key that asked for it. Losing this file means every
    device has to be turned on again.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    with _lock:
        d = _load()
        if not d.get("private_pem"):
            key = ec.generate_private_key(ec.SECP256R1())
            d["private_pem"] = key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()).decode()
            pub = key.public_key().public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint)
            d["public_key"] = _b64(pub)
            d.setdefault("subs", [])
            _save(d)
        return d


def public_key() -> str:
    return keys()["public_key"] if available() else ""


def subscriptions() -> list:
    return _load().get("subs", [])


def add(sub: dict) -> int:
    """Remember a device. Same endpoint twice is the same device, not two."""
    with _lock:
        d = _load()
        subs = [s for s in d.get("subs", []) if s.get("endpoint") != sub.get("endpoint")]
        subs.append({"endpoint": sub["endpoint"], "at": time.time(),
                     "ua": sub.get("ua", "")})
        d["subs"] = subs
        _save(d)
        return len(subs)


def drop(endpoint: str) -> None:
    with _lock:
        d = _load()
        d["subs"] = [s for s in d.get("subs", []) if s.get("endpoint") != endpoint]
        _save(d)


def _jwt(origin: str) -> str:
    """A signed 'this really is that server' for one push service.

    Scoped to the service's own origin and short-lived, so a token that leaks
    on the way out is worth nothing anywhere else and not for long.
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, utils

    key = serialization.load_pem_private_key(
        keys()["private_pem"].encode(), password=None)
    header = _b64(json.dumps({"typ": "JWT", "alg": "ES256"},
                             separators=(",", ":")).encode())
    claims = _b64(json.dumps({"aud": origin, "exp": int(time.time()) + 12 * 3600,
                              "sub": CONTACT},
                             separators=(",", ":")).encode())
    signing_input = f"{header}.{claims}".encode()
    der = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    # JWS wants the two halves raw and fixed-width; cryptography signs to DER.
    r, s = utils.decode_dss_signature(der)
    raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"{header}.{claims}.{_b64(raw)}"


def _tls():
    """Certificates to trust Google's push service with.

    Python on Windows reads the machine's own certificate store, and on this
    machine that store is missing the issuer Google's push endpoint chains to:
    every notification from 2026-10-22 failed with CERTIFICATE_VERIFY_FAILED.
    certifi ships its own bundle and is already installed alongside the
    libraries; the machine's store is the fallback if it ever is not.
    """
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _knock(sub: dict, ttl: int = 120) -> tuple:
    """One empty push to one device. Returns (ok, status, note)."""
    endpoint = sub["endpoint"]
    parts = urllib.parse.urlsplit(endpoint)
    origin = f"{parts.scheme}://{parts.netloc}"
    req = urllib.request.Request(endpoint, data=b"", method="POST")
    req.add_header("TTL", str(ttl))
    req.add_header("Content-Length", "0")
    # Urgency low would let a phone sit on it until it wakes for something
    # else, which is exactly the wrong trade for "is it finished yet".
    req.add_header("Urgency", "normal")
    req.add_header("Authorization", f"vapid t={_jwt(origin)}, k={keys()['public_key']}")
    try:
        with urllib.request.urlopen(req, timeout=10, context=_tls()) as r:
            return True, r.status, ""
    except urllib.error.HTTPError as e:
        # The device uninstalled the app, cleared its data, or the
        # subscription simply aged out. It is never coming back; forget it.
        if e.code in (404, 410):
            drop(endpoint)
            return False, e.code, "gone - forgotten"
        return False, e.code, e.reason or ""
    except Exception as exc:
        return False, 0, f"{type(exc).__name__}: {exc}"


def wake_all() -> dict:
    """Knock on every device that asked to be told. Never raises.

    Called from job threads, where an exception would vanish into a log nobody
    reads and take the notification with it.
    """
    if not available():
        return {"sent": 0, "failed": 0, "why": "no signing available"}
    subs = subscriptions()
    if not subs:
        return {"sent": 0, "failed": 0, "why": "no device has asked to be told"}
    sent = failed = 0
    notes = []
    for sub in list(subs):
        try:
            ok, status, note = _knock(sub)
        except Exception as exc:
            ok, status, note = False, 0, f"{type(exc).__name__}: {exc}"
        if ok:
            sent += 1
        else:
            failed += 1
            notes.append(f"{status} {note}".strip())
    return {"sent": sent, "failed": failed, "notes": notes[:3]}

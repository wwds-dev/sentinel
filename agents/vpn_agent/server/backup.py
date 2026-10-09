"""
backup.py — Encrypted export and import of a whole site.

The site file is the only copy of the server's WireGuard key and the OpenVPN
certificate authority. There is no recovery: lose it and every config you have
issued is permanently dead, with no way to reissue one. Until now the only
mitigation was a warning in the docs.

This makes moving or backing up a site a supported operation rather than a
manual file copy — which matters most exactly when it is riskiest, carrying keys
to a second machine or off the laptop entirely.

The format is deliberately boring and self-describing:

    scrypt(passphrase, salt) -> 32-byte key -> AES-256-GCM

scrypt because it is memory-hard, so a stolen backup file cannot be attacked
with cheap parallel hardware the way PBKDF2 can. AES-GCM because it authenticates
as well as encrypts: a file that has been altered fails to decrypt rather than
yielding a subtly wrong site. The KDF parameters travel inside the envelope, so
a backup taken today still opens after they are raised.

The passphrase is never stored, never logged, and never written anywhere. Lose
it and the backup is as gone as the original.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from . import store
from .model import Site

FORMAT_VERSION = 1
MAGIC = "vpn-agent-site-backup"
SUFFIX = ".vpnbackup"

SALT_BYTES = 16
NONCE_BYTES = 12
KEY_BYTES = 32

# ~32 MB and a noticeable fraction of a second per attempt. High enough to make
# guessing a decent passphrase expensive, low enough not to fail on a laptop.
SCRYPT_N = 2 ** 17   # ~128 MiB; older 2**15 backups still open
SCRYPT_R = 8
SCRYPT_P = 1

MIN_PASSPHRASE = 12


class BackupError(Exception):
    """Raised for a wrong passphrase, a corrupt file, or an unknown format."""


def passphrase_problems(passphrase: str) -> list[str]:
    """
    Advisory strength check.

    Not enforced — a user who insists on a weak passphrase for a backup they
    keep on an encrypted volume is making a reasonable trade. But it should be
    a decision, not an accident.
    """
    problems: list[str] = []
    if len(passphrase) < MIN_PASSPHRASE:
        problems.append(
            f"Only {len(passphrase)} characters. This protects your certificate "
            f"authority — use at least {MIN_PASSPHRASE}, ideally a passphrase of "
            "several words."
        )
    if passphrase and passphrase.lower() in ("password", "passphrase", "vpn", "secret"):
        problems.append("That is among the first passphrases anyone would try.")
    return problems


def _derive(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    kdf = Scrypt(salt=salt, length=KEY_BYTES, n=n, r=r, p=p)
    return kdf.derive(passphrase.encode("utf-8"))


def export_site(site: Site, passphrase: str) -> bytes:
    """Encrypt a site into a self-contained backup blob."""
    if not passphrase:
        raise BackupError("A passphrase is required — an unencrypted backup of a CA key is not offered.")

    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    key = _derive(passphrase, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)

    plaintext = json.dumps(site.to_dict()).encode("utf-8")

    # The header is authenticated but not encrypted, so a backup can be
    # identified and its parameters read without the passphrase.
    header = {
        "magic": MAGIC,
        "version": FORMAT_VERSION,
        "site": site.name,
        "kdf": {"name": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
                "salt": base64.b64encode(salt).decode()},
        "nonce": base64.b64encode(nonce).decode(),
    }
    aad = json.dumps(header, sort_keys=True).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)

    envelope = dict(header)
    envelope["ciphertext"] = base64.b64encode(ciphertext).decode()
    return json.dumps(envelope, indent=2).encode("utf-8")


def import_site(blob: bytes, passphrase: str) -> Site:
    """
    Decrypt a backup blob back into a Site. Does not save it.

    A wrong passphrase and a tampered file are indistinguishable here by
    design — both fail the GCM tag check and raise the same error.
    """
    try:
        envelope = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError(f"Not a VPN Agent backup file: {exc}") from exc

    if envelope.get("magic") != MAGIC:
        raise BackupError("Not a VPN Agent backup file.")
    if envelope.get("version") != FORMAT_VERSION:
        raise BackupError(
            f"Backup format version {envelope.get('version')} is not supported "
            f"by this build (expected {FORMAT_VERSION})."
        )

    try:
        kdf = envelope["kdf"]
        salt = base64.b64decode(kdf["salt"])
        nonce = base64.b64decode(envelope["nonce"])
        ciphertext = base64.b64decode(envelope["ciphertext"])
        header = {k: envelope[k] for k in ("magic", "version", "site", "kdf", "nonce")}
    except (KeyError, ValueError) as exc:
        raise BackupError(f"Backup file is malformed: {exc}") from exc

    # The header is authenticated, but only after the key is derived, so its
    # cost parameters must be bounded first: a crafted file asking for
    # n=2**24 or p=2**20 would otherwise freeze the app or exhaust memory.
    try:
        n, r, p = int(kdf["n"]), int(kdf["r"]), int(kdf["p"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BackupError(f"Backup file is malformed: {exc}") from exc
    if not (2 ** 14 <= n <= 2 ** 18 and n & (n - 1) == 0 and r == 8 and p == 1
            and len(salt) == SALT_BYTES and len(nonce) == NONCE_BYTES):
        raise BackupError("Backup file uses unsupported encryption parameters.")
    key = _derive(passphrase, salt, n, r, p)
    aad = json.dumps(header, sort_keys=True).encode("utf-8")

    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
    except InvalidTag as exc:
        raise BackupError(
            "Could not decrypt. Either the passphrase is wrong or the file has "
            "been altered — the format cannot tell you which."
        ) from exc

    try:
        site = Site.from_dict(json.loads(plaintext.decode("utf-8")))
    except (UnicodeDecodeError, ValueError, TypeError, AttributeError) as exc:
        raise BackupError(f"The backup's contents are malformed: {exc}") from exc
    problems = site.strict_problems()
    if problems:
        raise BackupError("The backup holds values Sentinel will not use: " + " ".join(problems))
    return site


def describe(blob: bytes) -> dict:
    """Read the unencrypted header — which site, which format. No passphrase needed."""
    try:
        envelope = json.loads(blob.decode("utf-8"))
        if envelope.get("magic") != MAGIC:
            raise BackupError("Not a VPN Agent backup file.")
        return {"site": envelope.get("site", "?"), "version": envelope.get("version")}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError(f"Not a VPN Agent backup file: {exc}") from exc


def write_backup(site: Site, passphrase: str, path: Path) -> Path:
    """Encrypt a site to a file. The file is owner-readable only."""
    path = Path(path)
    if path.suffix != SUFFIX:
        path = path.with_suffix(SUFFIX)
    blob = export_site(site, passphrase)

    # Written to a fresh 0600 temp file and renamed into place, so an existing
    # file with a looser mode (or a symlink) at the chosen path is replaced,
    # never written through.
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(blob)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def read_backup(path: Path, passphrase: str) -> Site:
    return import_site(Path(path).read_bytes(), passphrase)


def restore(path: Path, passphrase: str, *, overwrite: bool = False) -> Site:
    """
    Decrypt a backup and save it as a live site.

    Refuses to clobber an existing site unless asked: restoring over a site
    whose keys differ would silently invalidate every config already issued
    from it.
    """
    site = read_backup(path, passphrase)
    if store.site_exists(site.name) and not overwrite:
        raise BackupError(
            f"A site named {site.name!r} already exists. Restoring over it would "
            "replace its keys and invalidate every config issued from it. Pass "
            "overwrite to proceed."
        )
    store.save_site(site)
    return site

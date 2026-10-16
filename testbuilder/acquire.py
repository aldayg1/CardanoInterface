"""Acquisition layer — the builder fetches its own external premises.

External truth is never hardcoded: registry entries carry a `source_ref`
describing WHERE the truth lives (public Koios for chain data, GitHub raw
for CIP/registry documents), and this module fetches it on demand, stores
the captured bytes verbatim in the knowledge base, and fingerprints the
content with a hash. The refresh sweep re-acquires before the staleness
scan — if the world changed, the dependent tests are remade against the
new reality.

Chain data comes from the PUBLIC Koios instances (the same endpoints the
app's own KoiosBackend uses) — no node, no API key required. Documentary
sources come from canonical upstream URLs (CIP repository raw files).
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

KB_DIR = Path(__file__).resolve().parent / "kb"
SOURCES_DIR = KB_DIR / "sources"
SOURCES_FILE = KB_DIR / "sources.json"
ORACLES_FILE = KB_DIR / "oracles.json"

KOIOS_BASE = {
    "mainnet": "https://api.koios.rest/api/v1",
    "preprod": "https://preprod.koios.rest/api/v1",
    "preview": "https://preview.koios.rest/api/v1",
}

USER_AGENT = "CardanoInterface-testbuilder (oracle acquisition)"

# The only hosts a fetch may ever talk to: the public Koios instances and
# GitHub raw document content. source_ref URLs are registry (MR-editable)
# input, so an https URL pointing anywhere else — a file:// or intranet or
# cloud-metadata target included — is refused here, at the single choke
# point every fetch (and every redirect hop) passes through.
ALLOWED_HOSTS = frozenset({
    "api.koios.rest", "preprod.koios.rest", "preview.koios.rest",
    "raw.githubusercontent.com",
})


def _validate_fetch_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        raise ValueError(
            f"refusing to fetch {url!r}: only https:// sources are allowed")
    host = parsed.hostname or ""
    if host not in ALLOWED_HOSTS:
        raise ValueError(
            f"refusing to fetch {url!r}: host {host!r} is not on the "
            f"trusted-source allowlist ({', '.join(sorted(ALLOWED_HOSTS))})")
    try:
        if ipaddress.ip_address(host).is_private or \
                ipaddress.ip_address(host).is_loopback:
            raise ValueError(
                f"refusing to fetch {url!r}: private/loopback address")
    except ValueError:
        pass  # not an IP literal — the host allowlist already decided
    return url


def _get(url: str, timeout: int = 60) -> bytes:
    # Every URL actually fetched (including redirect hops) is printed: the
    # operator sees exactly which hosts their machine talked to.
    print(f"[acquire] GET {url}", flush=True)
    _validate_fetch_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload: bytes = resp.read()
        return payload


def content_hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()[:16]


def _load_sources() -> dict[str, dict[str, str]]:
    if SOURCES_FILE.exists():
        return dict(json.loads(SOURCES_FILE.read_text(encoding="utf-8")))
    return {}


def _save_sources(entries: dict[str, dict[str, str]]) -> None:
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    SOURCES_FILE.write_text(json.dumps(entries, indent=2) + "\n",
                            encoding="utf-8")


def _url_for(spec: dict[str, str]) -> str:
    kind = spec.get("type")
    if kind == "koios":
        base = KOIOS_BASE[spec["network"]]
        return f"{base}{spec['path']}"
    if kind == "raw":
        return spec["url"]
    raise ValueError(f"unsupported source type: {kind!r}")


_MOVED = re.compile(
    r"^Moved to \[[^\]]*\]\((\./[^)]+|https?://[^)]+)\)\s*\.?$")


def _resolve_redirects(url: str, payload: bytes, hops: int = 2) -> tuple[str, bytes]:
    """Follow content-level 'Moved to [..](relative)' notices that CIP
    documents use when they are relocated within the registry repository.
    The document graph itself tells us where truth moved — one or two hops,
    with a loop guard."""
    text = payload.decode("utf-8", errors="replace").strip()
    match = _MOVED.match(text)
    if not match or hops == 0:
        return url, payload
    target = match.group(1)
    if target.startswith("./"):
        base = url.rsplit("/", 1)[0]
        target = f"{base}/{target[2:]}"
    _validate_fetch_url(target)
    return _resolve_redirects(target, _get(target), hops - 1)


def acquire(name: str, spec: dict[str, str],
             persist: bool = True) -> dict[str, str]:
    """Fetch the source named by `spec` NOW, store the captured bytes
    verbatim, and fingerprint them. Returns the source record:
    {name, url, hash, captured, file}. With persist=False nothing is written
    (dry-run observation only)."""
    url = _url_for(spec)
    url, payload = _resolve_redirects(url, _get(url))
    h = content_hash(payload)
    captured = datetime.now(UTC).date().isoformat()
    file_path = SOURCES_DIR / f"{name}.{h}.raw"
    if persist:
        SOURCES_DIR.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(payload)

    # The record's own typing stays str-keyed/str-valued; the nested spec is
    # stored as JSON text so the sources registry keeps one honest shape.
    entry: dict[str, str] = {
        "name": name, "url": url, "hash": h, "captured": captured,
        "file": str(file_path.relative_to(KB_DIR.parent)),
        "spec": json.dumps(spec, sort_keys=True),
    }
    if not persist:
        return entry
    sources = _load_sources()

    # Retire the previous capture file for this name (history is the hash-
    # suffixed set; keep the last three generations, delete older ones).
    previous = sources.get(name)
    sources[name] = entry
    _save_sources(sources)
    if previous and previous.get("hash") != h:
        old = KB_DIR.parent / previous["file"]
        generations = sorted(SOURCES_DIR.glob(f"{name}.*.raw"))
        for stale_file in generations[:-3]:
            if stale_file.exists() and stale_file != old:
                stale_file.unlink()
    return entry


def current_hash(name: str) -> str | None:
    entry = _load_sources().get(name)
    return entry["hash"] if entry else None


def refresh_sources(registry: dict[str, dict[str, str]],
                    persist: bool = True) -> list[tuple[str, str]]:
    """Re-acquire every source_ref used by registered premises. Returns
    (symbol, change description) pairs where the world moved — the caller
    folds these into the staleness scan.

    A detected change is STAGED as pending_source_hash, never consumed: the
    live source_hash is promoted (promote_source_hash) only after that
    symbol's dependent remake is verified, so a dry run, an abort or a
    cancelled refresh cannot silently record a change as handled. With
    persist=False nothing is written at all."""
    changed: list[tuple[str, str]] = []
    for symbol, entry in registry.items():
        ref = entry.get("source_ref")
        if not ref:
            continue
        name = f"{symbol}_source"
        try:
            acquired = acquire(name, ref, persist=persist)
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            # An unreachable source is NOT a premise change: keep the last
            # known capture and let the run proceed on recorded knowledge.
            print(f"[acquire] {name}: unreachable ({err}) — using last capture",
                  flush=True)
            continue
        last_seen = entry.get("source_hash")
        if last_seen and last_seen != acquired["hash"]:
            changed.append((symbol, f"source material changed "
                            f"({last_seen} → {acquired['hash']})"))
            # Stage, do not consume: the freshness marker moves only when
            # the dependent remake has actually happened.
            if persist:
                entry["pending_source_hash"] = acquired["hash"]
                registry[symbol] = entry
        elif not last_seen:
            entry["source_hash"] = acquired["hash"]
            registry[symbol] = entry
    if persist:
        _save_sources_registry(registry)
    return changed


def promote_source_hash(symbol: str) -> None:
    """Promote a staged source change after its dependent remake verified.

    This is the only path by which pending_source_hash becomes the live
    source_hash; a run that never reaches here leaves the change staged, so
    the next refresh still sees the symbol as stale."""
    registry = _load_oracles()
    entry = registry.get(symbol)
    if not entry or "pending_source_hash" not in entry:
        return
    entry["source_hash"] = entry.pop("pending_source_hash")
    registry[symbol] = entry
    _save_sources_registry(registry)
    print(f"[acquire] promoted source_hash for {symbol} "
          f"(dependent remake verified)", flush=True)


def _load_oracles() -> dict[str, dict[str, str]]:
    if ORACLES_FILE.exists():
        return dict(json.loads(ORACLES_FILE.read_text(encoding="utf-8")))
    return {}


def _save_sources_registry(registry: dict[str, dict[str, str]]) -> None:
    ORACLES_FILE.write_text(json.dumps(registry, indent=2) + "\n",
                            encoding="utf-8")

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
import json
import re
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

KB_DIR = Path(__file__).resolve().parent / "kb"
SOURCES_DIR = KB_DIR / "sources"
SOURCES_FILE = KB_DIR / "sources.json"

KOIOS_BASE = {
    "mainnet": "https://api.koios.rest/api/v1",
    "preprod": "https://preprod.koios.rest/api/v1",
    "preview": "https://preview.koios.rest/api/v1",
}

USER_AGENT = "CardanoInterface-testbuilder (oracle acquisition)"


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


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
    return _resolve_redirects(target, _get(target), hops - 1)


def acquire(name: str, spec: dict[str, str]) -> dict[str, str]:
    """Fetch the source named by `spec` NOW, store the captured bytes
    verbatim, and fingerprint them. Returns the source record:
    {name, url, hash, captured, file}."""
    url = _url_for(spec)
    url, payload = _resolve_redirects(url, _get(url))
    h = content_hash(payload)
    captured = datetime.now(UTC).date().isoformat()
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    file_path = SOURCES_DIR / f"{name}.{h}.raw"
    file_path.write_bytes(payload)

    entry = {
        "name": name, "url": url, "hash": h, "captured": captured,
        "file": str(file_path.relative_to(KB_DIR.parent)),
        "spec": spec,
    }
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


def refresh_sources(registry: dict[str, dict[str, str]]) -> list[tuple[str, str]]:
    """Re-acquire every source_ref used by registered premises. Returns
    (symbol, change description) pairs where the world moved — the caller
    folds these into the staleness scan."""
    changed: list[tuple[str, str]] = []
    for symbol, entry in registry.items():
        ref = entry.get("source_ref")
        if not ref:
            continue
        name = f"{symbol}_source"
        try:
            acquired = acquire(name, ref)
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
        # Track what the oracle was last known to be built against.
        entry["source_hash"] = acquired["hash"]
        registry[symbol] = entry
    # Persist updated source_hash/captured fields even when nothing changed:
    # the sweep records that the world was re-checked at this date.
    _save_sources_registry(registry)
    return changed


def _save_sources_registry(registry: dict[str, dict[str, str]]) -> None:
    ORACLES_FILE = KB_DIR / "oracles.json"
    ORACLES_FILE.write_text(json.dumps(registry, indent=2) + "\n",
                            encoding="utf-8")

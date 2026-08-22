#!/usr/bin/env python3
# --- Robust logging setup: log from the very first line ---
from __future__ import annotations

import logging
import os
import sys

LOG_PATH = os.path.abspath("debug_main.log")
try:
    # Ensure the log file is writable before anything else
    with open(LOG_PATH, 'a') as f:
        pass
except Exception as e:
    print(f"[ERROR] Cannot write to debug_main.log: {e}")
    sys.exit(1)
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logging.info("[START] CardanoInterface.py script started.")
logging.info(f"Python executable: {sys.executable}")
logging.info(f"Python version: {sys.version}")

# If relaunching with another Python, pass LOG_PATH to child process
if "LOG_PATH" not in os.environ:
    os.environ["LOG_PATH"] = LOG_PATH

# --- Cross-platform, user-friendly Python version check and prompt ---
# Heavy imports sit below the logging bootstrap on purpose: an import failure
# here is captured in debug_main.log (E402 is per-file ignored for exactly
# this reason).
import contextlib
import hashlib
import json
import re
import secrets
import shutil
import time
from base64 import urlsafe_b64encode
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from operator import attrgetter
from typing import ParamSpec, TypeAlias, TypedDict, TypeVar

import cbor2
import pycardano
import requests
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from mnemonic import Mnemonic
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from pycardano import (
    Address,
    AssetName,
    Certificate,
    ChainContext,
    ExtendedSigningKey,
    HDWallet,
    InvalidBefore,
    InvalidHereAfter,
    MultiAsset,
    NativeScript,
    Network,
    PaymentExtendedSigningKey,
    PaymentSigningKey,
    PoolKeyHash,
    ProtocolParameters,
    ScriptAll,
    ScriptAny,
    ScriptHash,
    ScriptNofK,
    ScriptPubkey,
    SigningKey,
    StakeCredential,
    StakeDelegation,
    StakeDeregistration,
    StakeExtendedSigningKey,
    StakeRegistration,
    StakeSigningKey,
    Transaction,
    TransactionBody,
    TransactionBuilder,
    TransactionId,
    TransactionInput,
    TransactionOutput,
    TransactionWitnessSet,
    UTxO,
    Value,
    VerificationKeyHash,
    VerificationKeyWitness,
    min_lovelace_post_alonzo,
)
from pycardano import (
    script_hash as pycardano_script_hash,
)
from pycardano.exception import (
    InvalidArgumentException,
    TransactionBuilderException,
    UTxOSelectionException,
)
from pycardano.plutus import PLUTUS_V1_COST_MODEL, PLUTUS_V2_COST_MODEL

# pycardano attaches a console StreamHandler to its "PyCardano" logger at
# import time, so every failed build dumps the full TransactionBuilder state
# onto the user's terminal. Route that logger to the debug file instead.
_pycardano_logger = logging.getLogger("PyCardano")
_pycardano_logger.handlers.clear()
_pycardano_file_handler = logging.FileHandler(LOG_PATH)
_pycardano_file_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
_pycardano_logger.addHandler(_pycardano_file_handler)

from rich.console import Console
from rich.live import Live
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
)
from rich.table import Table

console = Console()

P = ParamSpec("P")
R = TypeVar("R")

# pycardano's script containers are typed with the union of concrete script
# classes, not their common base; matching that exact element type keeps list
# invariance from rejecting our constructions.
_NativeScriptMember: TypeAlias = (ScriptPubkey | ScriptAll | ScriptAny
                                  | ScriptNofK | InvalidBefore | InvalidHereAfter)

# ── JSON boundary ──────────────────────────────────────────────────────────
# json.loads and requests' Response.json() are typed as returning Any. Every
# JSON parse in this program lands in a JSON-typed variable first, so the rest
# of the module handles the closed value type below instead of Any.

JSON: TypeAlias = "str | int | float | bool | None | list[JSON] | dict[str, JSON]"


def _json_loads(raw: str) -> JSON:
    value: JSON = json.loads(raw)
    return value


CBORValue: TypeAlias = (
    "int | bytes | str | bool | float | None | list[CBORValue]"
    "| dict[CBORValue, CBORValue]"
)


def _cbor_loads(data: bytes) -> CBORValue:
    """Decode arbitrary CBOR into the closed CBORValue type.

    cbor2 publishes no type annotations, so this boundary converts its Any
    into a value every caller must narrow with isinstance before use — the
    same discipline `_json_loads` applies to `json.loads`.
    """
    value: CBORValue = cbor2.loads(data)
    return value


def _cbor_dumps_hex(value: CBORValue) -> str:
    """Serialize a CBOR value to hex — cbor2 is untyped, so keep it at the edge."""
    hexed: str = cbor2.dumps(value).hex()
    return hexed


def _dumps_json(value: JSON, indent: int | None = 2) -> str:
    """Serialize a JSON value; typed so dict literals never pass through an
    Any parameter (json.dumps' own signature would taint them)."""
    return json.dumps(value, indent=indent)


def _dump_json(value: JSON, path: str, indent: int = 2) -> None:
    with open(path, "w") as f:
        json.dump(value, f, indent=indent)


def _canonical_json_bytes(value: JSON) -> bytes:
    """One byte-string per JSON value, for signing and verifying.

    Keys are sorted and all insignificant whitespace removed, so the same value
    produces the same bytes on any machine and in any Python version. A
    signature is only meaningful if both sides agree byte-for-byte on what was
    signed; pretty-printed JSON does not give that guarantee.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _json_object(value: JSON) -> dict[str, JSON]:
    return value if isinstance(value, dict) else {}


def _json_list(value: JSON) -> list[JSON]:
    return value if isinstance(value, list) else []


def _json_str(value: JSON, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _json_int(value: JSON, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _wallet_entry_name(entry: JSON) -> str:
    """Wallet entries are stored as either plain names or {"name": ...} dicts."""
    return entry if isinstance(entry, str) else _json_str(_json_object(entry).get("name"))


def _migrate_wallet_entries(user_data: dict[str, JSON], user_password: str) -> bool:
    """Rewrite legacy plain-string wallet entries into {"name", "address"} dicts.

    Older versions of this program stored wallet lists as bare names; every
    view that loads a user's wallets runs this first so the rest of the code
    only ever sees dict entries. Returns True when anything was rewritten.
    """
    wallets = _json_list(user_data.get("wallets"))
    migrated = False
    for i, w in enumerate(wallets):
        if isinstance(w, str):
            wallet_dir = secure_path_join(WALLET_DIR, w)
            try:
                address = load_encrypted_address(wallet_dir, user_password)
            except Exception:
                address = ""
            wallets[i] = {"name": w, "address": address}
            migrated = True
    if migrated:
        user_data["wallets"] = wallets
    return migrated

def _json_fraction(value: JSON, default: Fraction) -> Fraction:
    """Parse a JSON number or rational string ("577/10000") into a Fraction.

    Ogmios reports prices and ratios as exact rationals; Blockfrost reports
    them as decimal strings or numbers. Fraction accepts both spellings, so
    protocol prices never round through a binary float.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return Fraction(value)
    if isinstance(value, str):
        try:
            return Fraction(value)
        except (ValueError, ZeroDivisionError):
            return default
    return default


def _json_float(value: JSON, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _cost_models_from_json(value: JSON) -> dict[str, dict[str, int]]:
    """Validate a Blockfrost-style cost model payload: {"PlutusV1": {op: int}}."""
    models: dict[str, dict[str, int]] = {}
    for name, ops in _json_object(value).items():
        if not isinstance(ops, dict):
            continue
        parsed = {op: _json_int(cost) for op, cost in ops.items()}
        models[name] = parsed
    return models


def _ref_scripts_fee(value: JSON) -> dict[str, float]:
    """Map an Ogmios/Blockfrost reference-script fee parameter to the tiered
    {base, range, multiplier} shape pycardano's fee function expects.

    These transactions carry no reference scripts, so the tiered fee always
    evaluates to zero; the shape keeps the field honest if that ever changes.
    """
    obj = _json_object(value)
    if obj:
        return {
            "base": _json_float(obj.get("base"), 15.0),
            "range": _json_float(obj.get("range"), 25600.0),
            "multiplier": _json_float(obj.get("multiplier"), 1.2),
        }
    return {"base": _json_float(value, 15.0), "range": 25600.0, "multiplier": 1.2}


def _ogmios_cost_models(value: JSON) -> dict[str, dict[str, int]]:
    """Convert Ogmios v6 flat cost-model arrays to pycardano's named-op form.

    Mirrors pycardano's own ogmios_v6 backend: v1/v2 arrays zip against the
    sorted operation-name tables; v3 keys by zero-padded index.
    """
    source = _json_object(value)
    cost_models: dict[str, dict[str, int]] = {}
    v1 = _json_list(source.get("plutus:v1"))
    if v1:
        cost_models["PlutusV1"] = {
            op: _json_int(v) for op, v in zip(sorted(PLUTUS_V1_COST_MODEL), v1,
                                              strict=False)}
    v2 = _json_list(source.get("plutus:v2"))
    if v2:
        cost_models["PlutusV2"] = {
            op: _json_int(v) for op, v in zip(sorted(PLUTUS_V2_COST_MODEL), v2,
                                              strict=False)}
    v3 = _json_list(source.get("plutus:v3"))
    if v3:
        width = len(str(len(v3)))
        cost_models["PlutusV3"] = {
            f"{i:0{width}d}": _json_int(v) for i, v in enumerate(v3)}
    return cost_models


# pycardano wraps MultiAsset in an untyped runtime type-checker, and requests
# declares its exception constructor with Any variadics — either taints every
# expression that names the class with nested Any. Both are reached through
# attrgetter here, which (like json.loads) yields a plain-Any boundary that
# gets a precise type on assignment.
def _multi_asset_from_primitive(payload: object) -> MultiAsset:
    """Build a pycardano MultiAsset from its CBOR primitive form."""
    factory: Callable[[object], MultiAsset] = attrgetter(
        "MultiAsset.from_primitive")(pycardano)
    return factory(payload)


def _check_min_ada_for_tokens(recipient: Address, amount_lovelace: int,
                              multi_asset: MultiAsset) -> None:
    """Reject token outputs the ledger would refuse as underfunded.

    A multi-asset output's minimum UTxO grows with the assets it carries, so
    the flat "at least 1 ADA" rule under-validates: the tx would only be
    rejected at submission (observed on preprod: 1 ADA + tokens required
    1,142,150 lovelace). Fail here with the exact requirement instead.
    """
    if context is None:
        raise ValueError("No backend configured.")
    output = TransactionOutput(recipient, Value(amount_lovelace, multi_asset))
    minimum = min_lovelace_post_alonzo(output, context)
    if amount_lovelace < minimum:
        raise ValueError(
            f"Token outputs need more ADA: this one must carry at least "
            f"{format_ada(minimum)} ADA (you entered {format_ada(amount_lovelace)}).")


def _multi_asset_items(multi_asset: MultiAsset) -> list[tuple[bytes, list[tuple[bytes, int]]]]:
    """Unwrap a MultiAsset into (policy_bytes, [(asset_name_bytes, qty), ...]).

    pycardano hides MultiAsset's mapping surface behind an untyped __getattr__
    (a .items() call would be Any), while to_shallow_primitive() hands back the
    live mapping with its ScriptHash/AssetName wrapper keys still attached.
    This gives displays and summaries plain bytes with precise types.
    """
    raw: dict[ScriptHash, dict[AssetName, int]] = multi_asset.to_shallow_primitive()
    return [(policy.payload, [(name.payload, qty) for name, qty in assets.items()])
            for policy, assets in raw.items()]


def _tx_required_signers(tx_body: TransactionBody) -> list[VerificationKeyHash]:
    """A transaction body's required signers as a plain typed list.

    Declared `list | NonEmptyOrderedSet` upstream, whose set variant iterates
    as Any; walk both shapes through typed access instead.
    """
    raw = tx_body.required_signers
    if isinstance(raw, list):
        return list(raw)
    if raw is None:
        return []
    return [raw[i] for i in range(len(raw))]

def _witness_vkeys(ws: TransactionWitnessSet) -> list[VerificationKeyWitness]:
    """A witness set's vkey witnesses as a plain typed list.

    The field is declared `list | NonEmptyOrderedSet`; the set variant's
    iterator is unannotated, so both shapes are walked through typed access.
    """
    raw = ws.vkey_witnesses
    if isinstance(raw, list):
        return list(raw)
    if raw is None:
        return []
    return [raw[i] for i in range(len(raw))]


def _witness_scripts(ws: TransactionWitnessSet) -> list[NativeScript]:
    """A witness set's native scripts as a plain typed list (see _witness_vkeys)."""
    raw = ws.native_scripts
    if isinstance(raw, list):
        return list(raw)
    if raw is None:
        return []
    return [raw[i] for i in range(len(raw))]

def _tx_body_inputs(tx_body: TransactionBody) -> list[TransactionInput]:
    """Return a transaction body's inputs as a plain typed list.

    TransactionBody.inputs is declared `list | OrderedSet` and OrderedSet's
    iterator is unannotated, so iterating the union yields Any; walking both
    shapes through their typed accessors keeps the element type.
    """
    raw_inputs = tx_body.inputs
    if isinstance(raw_inputs, list):
        return list(raw_inputs)
    return [raw_inputs[i] for i in range(len(raw_inputs))]

def _asset_display_name(asset_name: bytes) -> str:
    """UTF-8 spelling when the asset name is text, hex otherwise (CIP-25)."""
    try:
        return asset_name.decode("utf-8")
    except UnicodeDecodeError:
        return asset_name.hex() or "<No Name>"


_REQUEST_ERROR: type[Exception] = attrgetter("RequestException")(requests.exceptions)

# pycardano's vendored bech32 helpers are unannotated, which would make every
# call untyped; pin their signatures through the same plain-Any boundary.
from pycardano.crypto import bech32 as _bech32

_bech32_convertbits: Callable[[list[int], int, int, bool], list[int] | None] = attrgetter(
    "convertbits")(_bech32)
_bech32_create_checksum: Callable[[str, list[int], _bech32.Encoding], list[int]] = attrgetter(
    "bech32_create_checksum")(_bech32)
_bech32_verify_checksum: Callable[[str, list[int]], bool] = attrgetter(
    "bech32_verify_checksum")(_bech32)

BACKEND_TYPE = ""
SELECTED_NETWORK: str | None = None

NETWORK_OPTIONS = {
    "1": "mainnet",
    "2": "preprod",
    "3": "preview",
}

def network_to_pycardano(net_name: str) -> Network:
    if net_name == "mainnet":
        return Network.MAINNET
    return Network.TESTNET

BLOCKFROST_URLS = {
    "mainnet": "https://cardano-mainnet.blockfrost.io",
    "preprod": "https://cardano-preprod.blockfrost.io",
    "preview": "https://cardano-preview.blockfrost.io",
}

DEFAULT_OGMIOS_PORT = 1337
DEFAULT_KUPO_PORT = 1442

class CardanoBackend(ChainContext):
    """Chain backend this app talks to.

    A real pycardano ChainContext subclass, so TransactionBuilder accepts it
    directly. The app-specific surface (health, display_name, detect_network)
    sits on top of the library interface.
    """

    @property
    def protocol_param(self) -> ProtocolParameters:
        raise NotImplementedError()

    @property
    def network(self) -> Network:
        raise NotImplementedError()

    @property
    def last_block_slot(self) -> int:
        """Slot of the chain tip.

        Any transaction that spends from a script gets a validity interval
        anchored to the current tip, so every backend that can build such a
        transaction has to be able to report it.
        """
        raise NotImplementedError()

    def _utxos(self, address: str) -> list[UTxO]:
        raise NotImplementedError()

    def submit_tx(self, tx: Transaction | bytes | str) -> str | None:
        """Submit a transaction; returns the transaction id when the
        backend reports one.

        A rejected submission must reach the caller as an exception —
        returning None here must never be read as success.
        """
        if isinstance(tx, Transaction):
            return self.submit_tx_cbor(tx.to_cbor())
        if isinstance(tx, (bytes, str)):
            return self.submit_tx_cbor(tx)
        raise InvalidArgumentException(
            f"Invalid transaction type: {type(tx)}, expected Transaction, bytes, or str"
        )

    def submit_tx_cbor(self, cbor: bytes | str) -> str | None:
        raise NotImplementedError()

    def display_name(self) -> str:
        raise NotImplementedError()

    def health(self) -> dict[str, JSON]:
        raise NotImplementedError()

class BlockfrostBackend(CardanoBackend):
    def __init__(self, base_url: str, project_id: str):
        self.base_url = base_url
        self.project_id = project_id
        self._protocol_param: ProtocolParameters | None = None

    def _utxos(self, address: str) -> list[UTxO]:
        url = f"{self.base_url}/api/v0/addresses/{address}/utxos"
        headers = {"project_id": self.project_id}
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if not resp.headers.get("Content-Type", "").startswith("application/json"):
                return []
            try:
                raw: JSON = resp.json()
            except Exception:
                return []
            utxo_dicts = _json_list(raw)
            if not utxo_dicts:
                return []
            utxos = []
            for entry in utxo_dicts:
                try:
                    utxo = _json_object(entry)
                    tx_id: TransactionId = TransactionId.from_primitive(utxo.get("tx_hash"))
                    tx_in = TransactionInput(tx_id, _json_int(utxo.get("tx_index")))
                    addr: Address = Address.from_primitive(utxo.get("address"))
                    lovelace_amount = 0
                    multi_assets: dict[str, dict[str, int]] = {}
                    for item in _json_list(utxo.get("amount")):
                        unit_obj = _json_object(item)
                        unit = _json_str(unit_obj.get("unit"))
                        quantity = _json_int(unit_obj.get("quantity"))
                        if unit == "lovelace":
                            lovelace_amount = quantity
                        else:
                            policy_id = unit[:56]
                            asset_name = unit[56:] if len(unit) > 56 else ""
                            multi_assets.setdefault(policy_id, {})[asset_name] = quantity
                    value = Value(lovelace_amount)
                    if multi_assets:
                        value.multi_asset = _multi_asset_from_primitive(multi_assets)
                    tx_out = TransactionOutput(addr, value)
                    utxos.append(UTxO(tx_in, tx_out))
                except Exception:
                    continue
            return utxos
        except _REQUEST_ERROR:
            logging.error("Failed to fetch UTxOs from Blockfrost", exc_info=True)
            return []
        except Exception:
            logging.error("Error parsing UTxOs from Blockfrost", exc_info=True)
            return []

    def submit_tx_cbor(self, cbor: bytes | str) -> str | None:
        url = f"{self.base_url}/api/v0/tx/submit"
        headers = {"project_id": self.project_id, "Content-Type": "application/cbor"}
        try:
            body = cbor if isinstance(cbor, bytes) else bytes.fromhex(cbor)
            resp = requests.post(url, headers=headers, data=body, timeout=10)
            if resp.status_code >= 400:
                # The body carries the ledger's actual rejection reason; surfacing
                # it beats a generic HTTP error that hides why the tx was refused.
                detail = ""
                try:
                    body_json: JSON = resp.json()
                    err = _json_object(body_json)
                    detail = _json_str(err.get("message"), resp.text[:300])
                except Exception:
                    detail = resp.text[:300]
                raise TransactionSubmissionError(
                    f"Blockfrost rejected the transaction "
                    f"(HTTP {resp.status_code}): {detail}")
            resp.raise_for_status()
            if not resp.content:
                return None
            raw: JSON = resp.json()
            return raw if isinstance(raw, str) else None
        except _REQUEST_ERROR as e:
            logging.error(f"Failed to submit transaction: {e}")
            raise TransactionSubmissionError(str(e)) from e

    def health(self) -> dict[str, JSON]:
        url = f"{self.base_url}/api/v0/health"
        headers = {"project_id": self.project_id}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        raw: JSON = resp.json()
        return _json_object(raw)

    @property
    def protocol_param(self) -> ProtocolParameters:
        if self._protocol_param is not None:
            return self._protocol_param
        url = f"{self.base_url}/api/v0/epochs/latest/parameters"
        headers = {"project_id": self.project_id}
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            raw: JSON = resp.json()
        except Exception as e:
            logging.error(f"[protocol_param] Failed to fetch protocol parameters:"
                          f" {e}", exc_info=True)
            console.print(f"[red]Failed to fetch protocol parameters: {e}[/red]")
            raise
        params = _json_object(raw)
        try:
            self._protocol_param = ProtocolParameters(
                min_fee_constant=_json_int(params.get('min_fee_b'), 155381),
                min_fee_coefficient=_json_int(params.get('min_fee_a'), 44),
                max_block_size=_json_int(params.get('max_block_size'), 90112),
                max_tx_size=_json_int(params.get('max_tx_size'), 16384),
                max_block_header_size=_json_int(params.get('max_block_header_size'), 2176),
                key_deposit=_json_int(params.get('key_deposit'), 2000000),
                pool_deposit=_json_int(params.get('pool_deposit'), 500000000),
                pool_influence=_json_fraction(params.get('a0'), Fraction(0)),
                monetary_expansion=_json_fraction(params.get('rho'), Fraction(0)),
                treasury_expansion=_json_fraction(params.get('tau'), Fraction(0)),
                decentralization_param=_json_fraction(
                    params.get('decentralisation_param'), Fraction(0)),
                extra_entropy=_json_str(params.get('extra_entropy')),
                protocol_major_version=_json_int(params.get('protocol_major_ver')),
                protocol_minor_version=_json_int(params.get('protocol_minor_ver')),
                min_utxo=_json_int(params.get('min_utxo'), 1000000),
                min_pool_cost=_json_int(params.get('min_pool_cost')),
                price_mem=_json_fraction(params.get('price_mem'), Fraction("0.0577")),
                price_step=_json_fraction(params.get('price_step'), Fraction("0.0000721")),
                max_tx_ex_mem=_json_int(params.get('max_tx_ex_mem'), 10000000),
                max_tx_ex_steps=_json_int(params.get('max_tx_ex_steps'), 10000000000),
                max_block_ex_mem=_json_int(params.get('max_block_ex_mem'), 50000000),
                max_block_ex_steps=_json_int(params.get('max_block_ex_steps'), 40000000000),
                max_val_size=_json_int(params.get('max_val_size'), 5000),
                collateral_percent=_json_int(params.get('collateral_percent'), 150),
                max_collateral_inputs=_json_int(params.get('max_collateral_inputs'), 3),
                coins_per_utxo_word=(
                    _json_int(params.get('coins_per_utxo_word'), 34482) or 34482),
                coins_per_utxo_byte=_json_int(params.get('coins_per_utxo_size'), 4310),
                cost_models=_cost_models_from_json(params.get('cost_models')),
                maximum_reference_scripts_size={"bytes": _json_int(
                    params.get('maximum_reference_scripts_size'), 16384)},
                min_fee_reference_scripts={"base": _json_float(
                    params.get('min_fee_ref_script_cost_per_byte'), 15.0),
                    "range": 25600.0, "multiplier": 1.2},
            )
        except Exception as e:
            logging.error(f"[protocol_param] Error mapping protocol parameters: {e}",
                          exc_info=True)
            console.print(f"[red]Error mapping protocol parameters: {e}[/red]")
            raise
        return self._protocol_param

    def display_name(self) -> str:
        if "mainnet" in self.base_url:
            return "Blockfrost (Mainnet)"
        elif "preprod" in self.base_url:
            return "Blockfrost (Preprod)"
        elif "preview" in self.base_url:
            return "Blockfrost (Preview)"
        return "Blockfrost"

    @property
    def network(self) -> Network:
        if "mainnet" in self.base_url:
            return Network.MAINNET
        return Network.TESTNET

    @property
    def last_block_slot(self) -> int:
        resp = requests.get(
            f"{self.base_url}/api/v0/blocks/latest",
            headers={"project_id": self.project_id},
            timeout=10,
        )
        resp.raise_for_status()
        raw: JSON = resp.json()
        slot = _json_object(raw).get("slot")
        if not isinstance(slot, int):
            raise NetworkError("Blockfrost returned no slot for the latest block.")
        return slot

class KupoBackend(CardanoBackend):
    def __init__(self, url: str, network: str | None = None):
        self.url: str = url.rstrip("/")
        # Kupo serves any network; the app knows which one the user selected.
        self._network_name = network or SELECTED_NETWORK or "preprod"

    def _utxos(self, address: str) -> list[UTxO]:
        try:
            url = f"{self.url}/matches/{address}?unspent"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            raw: JSON = resp.json()
        except Exception as e:
            logging.error(f"Failed to fetch UTxOs from Kupo: {e}", exc_info=True)
            return []

        utxos = []
        for entry in _json_list(raw):
            try:
                m = _json_object(entry)
                tx_id: TransactionId = TransactionId.from_primitive(m.get("transaction_id"))
                tx_in = TransactionInput(tx_id, _json_int(m.get("output_index")))
                addr: Address = Address.from_primitive(m.get("address"))
                value_dict = _json_object(m.get("value"))
                lovelace_amount = _json_int(value_dict.get("coins"))
                value = Value(lovelace_amount)
                assets = _json_object(value_dict.get("assets"))
                if assets:
                    ma_dict: dict[str, dict[str, int]] = {}
                    for asset_key, qty in assets.items():
                        if "." in asset_key:
                            policy_id, asset_name = asset_key.split(".", 1)
                        else:
                            policy_id = asset_key
                            asset_name = ""
                        ma_dict.setdefault(policy_id, {})[asset_name] = _json_int(qty)
                    value.multi_asset = _multi_asset_from_primitive(ma_dict)
                tx_out = TransactionOutput(addr, value)
                utxos.append(UTxO(tx_in, tx_out))
            except Exception:
                continue
        return utxos

    def submit_tx_cbor(self, cbor: bytes | str) -> str | None:
        console.print("[yellow]Kupo does not support transaction submission. Use Ogmios or "
            "Blockfrost instead.[/yellow]")
        return None

    def health(self) -> dict[str, JSON]:
        resp = requests.get(f"{self.url}/health",
                            headers={"Accept": "application/json"}, timeout=10)
        resp.raise_for_status()
        raw: JSON = resp.json()
        return _json_object(raw)

    @property
    def protocol_param(self) -> ProtocolParameters:
        console.print("[yellow]Kupo does not provide protocol parameters. Some features may be "
            "limited.[/yellow]")
        raise NotImplementedError(
            "Kupo does not provide protocol parameters. Use Ogmios or Blockfrost.")

    def display_name(self) -> str:
        return f"Kupo ({self.url})"

    @property
    def network(self) -> Network:
        return Network.MAINNET if self._network_name == "mainnet" else Network.TESTNET

    @property
    def last_block_slot(self) -> int:
        resp = requests.get(f"{self.url}/checkpoints", timeout=10)
        resp.raise_for_status()
        raw: JSON = resp.json()
        checkpoints = _json_list(raw)
        if not checkpoints:
            raise NetworkError("Kupo reported no checkpoints; it may still be syncing.")
        slots = [s for c in checkpoints
                 if isinstance((s := _json_object(c).get("slot_no")), int)]
        if not slots:
            raise NetworkError("Kupo reported no usable checkpoints; it may still be syncing.")
        return max(slots)

def _describe_ogmios_error(err: dict[str, JSON]) -> str:
    """An Ogmios RPC error as one readable line, ledger justification included.

    Ogmios rejection messages say the real reason "is given as 'data.error'"
    and then put it in the data object — dropping it would tell the user a
    spend failed while hiding the one sentence that says why.
    """
    code = err.get("code")
    message = _json_str(err.get("message"))
    line = f"Ogmios error {code}" if isinstance(code, int) else "Ogmios error"
    if message:
        line += f": {message}"
    data = err.get("data")
    detail: JSON = _json_object(data).get("error") if isinstance(data, dict) else None
    if detail is None and data is not None and not isinstance(data, dict):
        detail = data
    if detail is not None:
        detail_str = _json_str(detail) if isinstance(detail, str) else _dumps_json(detail)
        if detail_str and detail_str != message:
            line += f" — {detail_str}"
    return line


class OgmiosBackend(CardanoBackend):
    def __init__(self, url: str):
        self.url: str = url.rstrip("/")
        self._protocol_param: ProtocolParameters | None = None
        self._detected_network: Network | None = None
        self._detected_network_name = ""

    def _rpc(self, method: str, params: dict[str, JSON] | None = None) -> JSON:
        payload: dict[str, JSON] = {"jsonrpc": "2.0", "method": method, "id": 1}
        if params:
            payload["params"] = params
        resp = requests.post(self.url, json=payload, timeout=15)
        # Ogmios signals RPC failures as HTTP 400 with the reason in a JSON-RPC
        # error body; parse it before raise_for_status discards it.
        if resp.status_code >= 400:
            try:
                err_body: JSON = resp.json()
                raise RuntimeError(_describe_ogmios_error(
                    _json_object(_json_object(err_body).get("error", {}))))
            except (ValueError, TypeError):
                pass
        resp.raise_for_status()
        raw: JSON = resp.json()
        data = _json_object(raw)
        if "error" in data:
            err_value = data["error"]
            if isinstance(err_value, dict):
                raise RuntimeError(_describe_ogmios_error(err_value))
            raise RuntimeError(f"Ogmios error: {err_value}")
        return data.get("result")

    def _utxos(self, address: str) -> list[UTxO]:
        try:
            result = self._rpc("queryLedgerState/utxo", {"addresses": [address]})
        except Exception as e:
            logging.error(f"Failed to query UTxOs via Ogmios: {e}", exc_info=True)
            return []
        if not result:
            return []
        utxos = []
        for entry in _json_list(result):
            try:
                e_obj = _json_object(entry)
                tx_id: TransactionId = TransactionId.from_primitive(
                    _json_object(e_obj.get("transaction")).get("id"))
                tx_in = TransactionInput(tx_id, _json_int(e_obj.get("index")))
                addr: Address = Address.from_primitive(e_obj.get("address"))
                value_dict = _json_object(e_obj.get("value"))
                lovelace_amount = _json_int(
                    _json_object(value_dict.get("ada")).get("lovelace"))
                value = Value(lovelace_amount)
                ma_dict: dict[str, dict[str, int]] = {}
                for pid, assets in value_dict.items():
                    if pid == "ada":
                        continue
                    if isinstance(assets, dict):
                        ma_dict[pid] = {aname: _json_int(qty)
                                        for aname, qty in assets.items()}
                if ma_dict:
                    value.multi_asset = _multi_asset_from_primitive(ma_dict)
                tx_out = TransactionOutput(addr, value)
                utxos.append(UTxO(tx_in, tx_out))
            except Exception:
                continue
        return utxos

    def submit_tx_cbor(self, cbor: bytes | str) -> str | None:
        # A rejected submission must reach the caller. Returning None here used
        # to make a refused transaction look like a successful one with no id.
        try:
            cbor_hex = cbor.hex() if isinstance(cbor, bytes) else cbor
            result = self._rpc("submitTransaction", {"transaction": {"cbor": cbor_hex}})
        except Exception as e:
            logging.error(f"Failed to submit transaction via Ogmios: {e}", exc_info=True)
            raise TransactionSubmissionError(str(e)) from e
        if isinstance(result, dict):
            tx_obj = _json_object(result.get("transaction"))
            tx_id = tx_obj.get("id")
            return tx_id if isinstance(tx_id, str) else None
        return result if isinstance(result, str) else None

    def health(self) -> dict[str, JSON]:
        try:
            tip = self._rpc("queryNetwork/tip")
            return {"is_healthy": True, "tip": tip}
        except Exception as e:
            return {"is_healthy": False, "error": str(e)}

    @property
    def last_block_slot(self) -> int:
        raw = self._rpc("queryNetwork/tip")
        tip = _json_object(raw)
        if "slot" not in tip:
            raise NetworkError(f"Ogmios returned no chain tip slot: {raw!r}")
        slot = tip["slot"]
        if not isinstance(slot, int):
            raise NetworkError(f"Ogmios returned a non-integer chain tip slot: {raw!r}")
        return slot

    @property
    def protocol_param(self) -> ProtocolParameters:
        if self._protocol_param is not None:
            return self._protocol_param
        try:
            raw = self._rpc("queryLedgerState/protocolParameters")
        except Exception as e:
            logging.error(f"[protocol_param] Failed to fetch via Ogmios: {e}", exc_info=True)
            console.print(f"[red]Failed to fetch protocol parameters from Ogmios: {e}[/red]")
            raise
        params = _json_object(raw)

        def _first(*keys: str) -> JSON:
            for k in keys:
                v = params.get(k)
                if v is not None:
                    return v
            return None

        def _extract_lovelace(v: JSON) -> int:
            if isinstance(v, dict):
                return _json_int(_json_object(v.get("ada")).get("lovelace"))
            return _json_int(v)

        def _extract_bytes(v: JSON) -> int:
            if isinstance(v, dict):
                return _json_int(v.get("bytes"))
            return _json_int(v)

        prices = _json_object(params.get('executionUnitPrices'))

        try:
            self._protocol_param = ProtocolParameters(
                min_fee_coefficient=_json_int(
                    _first('minFeeCoefficient', 'min_fee_a') or 44),
                min_fee_constant=_extract_lovelace(
                    _first('minFeeConstant', 'min_fee_b') or 155381),
                max_block_size=_extract_bytes(
                    _first('maxBlockBodySize', 'max_block_size') or 90112),
                max_tx_size=_extract_bytes(
                    _first('maxTransactionSize', 'maxTxSize', 'max_tx_size') or 16384),
                max_block_header_size=_extract_bytes(
                    _first('maxBlockHeaderSize', 'max_block_header_size') or 1100),
                key_deposit=_extract_lovelace(
                    _first('stakeCredentialDeposit', 'stakeKeyDeposit', 'key_deposit')
                    or 2000000),
                pool_deposit=_extract_lovelace(
                    _first('stakePoolDeposit', 'poolDeposit', 'pool_deposit')
                    or 500000000),
                # Ogmios reports influences/expansions as exact rationals
                # ("3/10"); Fraction parses them without float rounding. The
                # transaction builder never reads these four fields.
                pool_influence=_json_fraction(
                    _first('stakePoolPledgeInfluence', 'a0'), Fraction(0)),
                monetary_expansion=_json_fraction(
                    _first('monetaryExpansion', 'rho'), Fraction(0)),
                treasury_expansion=_json_fraction(
                    _first('treasuryExpansion', 'tau'), Fraction(0)),
                decentralization_param=_json_fraction(
                    _first('decentralisationParam', 'decentralisation_param'), Fraction(0)),
                extra_entropy=_json_str(_first('extraEntropy', 'extra_entropy')),
                protocol_major_version=_json_int(
                    _json_object(_first('version')).get('major')),
                protocol_minor_version=_json_int(
                    _json_object(_first('version')).get('minor')),
                min_utxo=_json_int(_first('min_utxo') or 1000000),
                min_pool_cost=_extract_lovelace(
                    _first('minStakePoolCost', 'min_pool_cost')),
                # v6 reports prices under 'memory'/'cpu' as rationals; the
                # legacy v5 keys and the network's actual values are kept as
                # fallbacks. exec-unit prices only weight Plutus execution,
                # which native-script multisig never uses.
                price_mem=_json_fraction(
                    prices.get('memory',
                               params.get('priceMemory', params.get('price_mem'))),
                    Fraction("0.0577")),
                price_step=_json_fraction(
                    prices.get('cpu',
                               params.get('priceSteps', params.get('price_step'))),
                    Fraction("0.0000721")),
                max_tx_ex_mem=_json_int(
                    _json_object(params.get('maxExecutionUnitsPerTransaction')).get(
                        'memory', params.get('max_tx_ex_mem')) or 10000000),
                max_tx_ex_steps=_json_int(
                    _json_object(params.get('maxExecutionUnitsPerTransaction')).get(
                        'cpu', params.get('max_tx_ex_steps')) or 10000000000),
                max_block_ex_mem=_json_int(
                    _json_object(params.get('maxExecutionUnitsPerBlock')).get(
                        'memory', params.get('max_block_ex_mem')) or 50000000),
                max_block_ex_steps=_json_int(
                    _json_object(params.get('maxExecutionUnitsPerBlock')).get(
                        'cpu', params.get('max_block_ex_steps')) or 40000000000),
                max_val_size=_extract_bytes(
                    _first('maxValueSize', 'max_val_size') or 5000),
                collateral_percent=_json_int(
                    _first('collateralPercentage', 'collateral_percent') or 150),
                max_collateral_inputs=_json_int(
                    _first('maxCollateralInputs', 'max_collateral_inputs') or 3),
                # Ogmios v6's minUtxoDepositCoefficient IS utxoCostPerByte (Babbage+):
                # the ledger requires min = (160 + output CBOR size) * this value per
                # output. Mapping it as a word count (or dividing by 8) made the
                # builder accept change outputs the ledger rejects (error 3125).
                coins_per_utxo_word=_json_int(
                    _first('coinsPerUtxoWord', 'coins_per_utxo_word') or 34482),
                coins_per_utxo_byte=_extract_lovelace(
                    _first('minUtxoDepositCoefficient', 'coinsPerUtxoByte',
                           'coins_per_utxo_byte') or 4310),
                cost_models=_ogmios_cost_models(params.get('plutusCostModels')),
                maximum_reference_scripts_size={"bytes": _extract_bytes(
                    _first('maxReferenceScriptsSize', 'maximum_reference_scripts_size')
                    or 16384)},
                min_fee_reference_scripts=_ref_scripts_fee(
                    _first('minFeeReferenceScripts', 'min_fee_reference_scripts') or 15),
            )
        except Exception as e:
            logging.error(f"[protocol_param] Error mapping Ogmios params: {e}", exc_info=True)
            console.print(f"[red]Error mapping protocol parameters from Ogmios: {e}[/red]")
            raise
        return self._protocol_param

    def display_name(self) -> str:
        return f"Ogmios ({self.url})"

    def detect_network(self) -> None:
        try:
            resp = requests.get(f"{self.url}/health", timeout=5)
            resp.raise_for_status()
            raw: JSON = resp.json()
            self._detected_network_name = _json_str(_json_object(raw).get("network"))
            if self._detected_network_name == "mainnet":
                self._detected_network = Network.MAINNET
            else:
                self._detected_network = Network.TESTNET
        except Exception as e:
            logging.warning(f"Could not detect network from Ogmios: {e}")
            self._detected_network_name = ""
            self._detected_network = Network.TESTNET

    @property
    def network(self) -> Network:
        if self._detected_network is None:
            self.detect_network()
            if self._detected_network is None:
                return Network.TESTNET
        return self._detected_network

context: CardanoBackend | None = None

def current_network() -> Network:
    if SELECTED_NETWORK:
        return network_to_pycardano(SELECTED_NETWORK)
    if context:
        return context.network
    return Network.TESTNET


def ada_to_lovelace(amount: str) -> int:
    """Exact ADA → lovelace conversion. Binary floats must never touch money:
    on this build int(float('0.000249') * 1_000_000) yields 248, not 249.
    Parsing the decimal string with Decimal and scaling by 10^6 is exact.

    Raises ValueError for malformed input, negative amounts, zero, or more
    than six decimal places (one lovelace is the smallest unit).
    """
    try:
        parsed = Decimal(amount.strip())
    except InvalidOperation as parse_err:
        raise ValueError(f"'{amount}' is not a valid ADA amount.") from parse_err
    if parsed < 0:
        raise ValueError("ADA amount cannot be negative.")
    scaled = parsed.scaleb(6)
    if scaled != scaled.to_integral_value():
        raise ValueError(f"{amount} has more than 6 decimal places — "
                         "one lovelace (0.000001 ADA) is the smallest unit.")
    if scaled == 0:
        raise ValueError("Amount must be at least 0.000001 ADA.")
    return int(scaled)


def format_ada(lovelace: int) -> str:
    """Exact lovelace → 'X.XXXXXX' display using pure integer arithmetic."""
    return f"{lovelace // 1_000_000}.{lovelace % 1_000_000:06d}"

session: PromptSession[str] = PromptSession()

PRIMARY_COLOR = "bright_cyan"
ACCENT_COLOR = "bright_magenta"
ERROR_COLOR = "bold bright_red"
TEXT_COLOR = "bright_yellow"
BACKGROUND_COLOR = "black"

# How long a multisig transaction stays valid while signatures are collected.
# Cardano slots are one second on every network this program targets.
DEFAULT_SIGNING_WINDOW_DAYS = 7

WALLET_DIR = "CardanoInterface/wallets"
USER_DATA_DIR = "CardanoInterface/users"
os.makedirs(WALLET_DIR, exist_ok=True)
os.makedirs(USER_DATA_DIR, exist_ok=True)

class PyCardanoError(Exception):
    """Base class for PyCardano-specific errors."""
    pass

class OnChainError(Exception):
    """Base class for on-chain specific errors."""
    pass

class TransactionSubmissionError(OnChainError):
    """Raised when a transaction fails to submit on-chain."""
    def __init__(self, message: str = "Transaction submission failed."):
        super().__init__(message)

class UTxOFetchError(OnChainError):
    """Raised when fetching UTxOs fails."""
    def __init__(self, message: str = "Failed to fetch UTxOs."):
        super().__init__(message)

class AddressDerivationError(PyCardanoError):
    """Raised when address derivation fails."""
    def __init__(self, message: str = "Address derivation failed."):
        super().__init__(message)

class KeySerializationError(PyCardanoError):
    """Raised when key serialization or deserialization fails."""
    def __init__(self, message: str = "Key serialization/deserialization failed."):
        super().__init__(message)

class SeedDerivationError(PyCardanoError):
    """Raised when seed derivation fails."""
    def __init__(self, message: str = "Seed derivation failed."):
        super().__init__(message)

class EncryptionError(Exception):
    """Raised when encryption or decryption fails."""
    def __init__(self, message: str = "Encryption/Decryption failed."):
        super().__init__(message)

class InputError(Exception):
    """Base class for input-related errors."""
    def __init__(self, source: str, data_type: str, message: str = "Invalid input."):
        self.source = source
        self.data_type = data_type
        self.message = f"[InputError] Source: {source}, Data Type: {data_type}, Message: {message}"
        super().__init__(self.message)

class FileInputError(InputError):
    """Raised when there is an issue with file-based input."""
    def __init__(self, file_path: str, message: str = "File input error."):
        super().__init__(source="File", data_type="File Path",
                         message=f"{message} (File: {file_path})")

class UserInputError(InputError):
    """Raised when there is an issue with user-provided input."""
    def __init__(self, input_value: str, message: str = "User input error."):
        super().__init__(source="User", data_type="String",
                         message=f"{message} (Input: {input_value})")

class APIInputError(InputError):
    """Raised when there is an issue with API-provided input."""
    def __init__(self, api_name: str, message: str = "API input error."):
        super().__init__(source="API", data_type="API Response",
                         message=f"{message} (API: {api_name})")

class DataValidationError(Exception):
    """Raised when data validation fails."""
    def __init__(self, data_type: str, message: str = "Data validation error."):
        self.data_type = data_type
        self.message = f"[DataValidationError] Data Type: {data_type}, Message: {message}"
        super().__init__(self.message)

class MissingDataError(DataValidationError):
    """Raised when required data is missing."""
    def __init__(self, data_type: str, field_name: str,
                 message: str = "Missing required data."):
        super().__init__(data_type=data_type, message=f"{message} (Field: {field_name})")

class InvalidDataTypeError(DataValidationError):
    """Raised when data is of an invalid type."""
    def __init__(self, expected_type: str, actual_type: str,
                 message: str = "Invalid data type."):
        super().__init__(data_type=f"Expected: {expected_type},"
                         f" Actual: {actual_type}", message=message)

class NetworkError(Exception):
    """Raised for network-related issues."""
    def __init__(self, endpoint: str, message: str = "Network error."):
        self.endpoint = endpoint
        self.message = f"[NetworkError] Endpoint: {endpoint}, Message: {message}"
        super().__init__(self.message)

class TimeoutError(NetworkError):
    """Raised when a network operation times out."""
    def __init__(self, endpoint: str, timeout: str, message: str = "Operation timed out."):
        super().__init__(endpoint=endpoint, message=f"{message} (Timeout: {timeout}s)")

class AuthenticationError(NetworkError):
    """Raised when authentication fails."""
    def __init__(self, endpoint: str, message: str = "Authentication failed."):
        super().__init__(endpoint=endpoint, message=message)

class ParsingError(Exception):
    """Raised when parsing data fails."""
    def __init__(self, data_type: str, message: str = "Parsing error."):
        self.data_type = data_type
        self.message = f"[ParsingError] Data Type: {data_type}, Message: {message}"
        super().__init__(self.message)

class JSONParsingError(ParsingError):
    """Raised when JSON parsing fails."""
    def __init__(self, message: str = "JSON parsing error."):
        super().__init__(data_type="JSON", message=message)

class XMLParsingError(ParsingError):
    """Raised when XML parsing fails."""
    def __init__(self, message: str = "XML parsing error."):
        super().__init__(data_type="XML", message=message)

class BackendConfigurationError(Exception):
    """Raised when backend configuration is incorrect or missing."""

class WalletDataGenerationError(Exception):
    """Raised when wallet data generation fails."""
    def __init__(self, message: str = "Wallet data generation error."):
        super().__init__(message)

def _copy_wrapper_metadata(func: Callable[P, R],
                           wrapper: Callable[P, R | None]) -> None:
    """Copy what functools.wraps would copy, minus its Any-typed variadics.

    functools.wraps' own annotations describe the wrapper with VarArg(Any),
    which taints every decorated function under disallow_any_decorated; the
    explicit copies below keep the metadata with precise types. (__dict__
    merging and __wrapped__ are skipped: nothing in this app reads them.)
    """
    wrapper.__name__ = getattr(func, "__name__", "wrapped")
    wrapper.__qualname__ = getattr(func, "__qualname__", "wrapped")
    wrapper.__doc__ = getattr(func, "__doc__", None)
    wrapper.__module__ = getattr(func, "__module__", __name__)

def exception_error(func: Callable[P, R]) -> Callable[P, R | None]:
    """Print and log any exception the wrapped function raises, returning None.

    The interactive flows rely on this: a failing operation reports itself
    and drops the user back to a menu instead of crashing the app.
    """
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R | None:
        try:
            return func(*args, **kwargs)
        except Exception as e:
            from rich.markup import escape
            console.print(f"[bold red]{type(e).__name__}: {escape(str(e))}[/bold red]")
            # Log with traceback
            logging.error(f"Exception in {getattr(func, '__name__', 'wrapped')}: {e}",
                          exc_info=True)
            return None
    _copy_wrapper_metadata(func, wrapper)
    return wrapper

@exception_error
def debug_backend_health() -> None:
    logging.debug("[ENTRY] debug_backend_health()")
    if not context:
        console.print("[red]No backend configured.[/red]")
        return
    try:
        result = context.health()
        console.print(f"[green]{context.display_name()} health check passed.[/green]")
        console.print(result)
    except Exception as e:
        console.print(f"[red]Health check failed: {e}[/red]")
    logging.debug("[EXIT] debug_backend_health()")

def show_framed_banner(spin_times: int = 16, delay: float = 0.08) -> None:
    """Display the CardanoInterface banner with a spinning frame effect that stays visible."""
    import time

    title = " Cardano Interface "
    frame_colors = [PRIMARY_COLOR, ACCENT_COLOR, "yellow", "magenta", "cyan"]
    spin_chars = [
        ("┏", "┓", "┗", "┛", "━", "┃"),
        ("╔", "╗", "╚", "╝", "═", "║"),
        ("+", "+", "+", "+", "-", "|"),
        ("▛", "▜", "▙", "▟", "▀", "▌"),
        ("*", "*", "*", "*", "*", "*"),
    ]
    width = len(title) + 6

    def make_frame(i: int) -> str:
        chars = spin_chars[i % len(spin_chars)]
        color = frame_colors[i % len(frame_colors)]
        frame_color = f"bold {color}"
        text_color = f"bold {TEXT_COLOR}"

        top = f"[{frame_color}]{chars[0]}{chars[4]*width}{chars[1]}[/{frame_color}]"
        mid = (f"[{frame_color}]{chars[5]}[/{frame_color}]   "
               f"[{text_color}]{title}[/{text_color}]   "
               f"[{frame_color}]{chars[5]}[/{frame_color}]")
        bot = f"[{frame_color}]{chars[2]}{chars[4]*width}{chars[3]}[/{frame_color}]"
        return "\n".join([top, mid, bot])

    # Animate the spinning frame
    with Live("", refresh_per_second=1/delay, console=console, transient=True) as live:
        for i in range(spin_times):
            live.update(make_frame(i))
            time.sleep(delay)
        last_frame = make_frame(spin_times - 1)
        time.sleep(0.2)

    # Print the final frame so it stays visible
    console.print(last_frame)
    console.print("[bold yellow]Created by Refractic Labs with PyCardano[/bold yellow]")
    console.print(
        f"[bold {ERROR_COLOR}]\n"
        "!!! WARNING !!!\n"
        "You are solely responsible for the use of this app.\n"
        "CardanoInterface and Refractic Labs take NO responsibility for any "
        "losses, mistakes, or damages.\n"
        "Crypto and blockchain operations are risky and may result in the loss of your funds.\n"
        "Make sure NO ONE is watching your screen while you use this app:\n"
        "ALL DATA YOU ENTER IS VISIBLE ON SCREEN.\n"
        "Protect your passwords, keys, and personal information.\n"
        "Be aware of phishing, scams, and social engineering attacks.\n"
        "If you are unsure, STOP and seek advice before proceeding.\n"
        f"[/{ERROR_COLOR}]"
    )

def derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=390000,
    )
    return urlsafe_b64encode(kdf.derive(password.encode()))

def encrypt_data(data: bytes, password: str) -> bytes:
    salt = os.urandom(16)
    key = derive_key(password, salt)
    f = Fernet(key)
    encrypted = f.encrypt(data)
    return salt + encrypted

def decrypt_data(encrypted_data: bytes, password: str) -> bytes:
    try:
        salt = encrypted_data[:16]
        encrypted = encrypted_data[16:]
        key = derive_key(password, salt)
        f = Fernet(key)
        decrypted = f.decrypt(encrypted)
        logging.info("Decryption successful.")
        return decrypted
    except InvalidToken as e:
        logging.error(f"Decryption failed: InvalidToken - {e}")
        raise
    except Exception as e:
        logging.error(f"Unexpected error during decryption: {e}")
        raise

def save_encrypted_wallet(wallet_dir: str, skey: SigningKey | ExtendedSigningKey,
                          stkey: SigningKey | ExtendedSigningKey,
                          password: str) -> None:
    """Save encrypted wallet keys and address.

    Accepts either extended (mnemonic-derived) or plain signing keys.
    """
    try:
        skey_prim = skey.to_primitive()
        stkey_prim = stkey.to_primitive()
        if not isinstance(skey_prim, (bytes, bytearray)) \
                or not isinstance(stkey_prim, (bytes, bytearray)):
            raise KeySerializationError(
                "Signing key serialization produced a non-byte payload.")
        encrypted_skey = encrypt_data(bytes(skey_prim), password)
        encrypted_stkey = encrypt_data(bytes(stkey_prim), password)
        with open(os.path.join(wallet_dir, "payment.skey"), "wb") as f:
            f.write(encrypted_skey)
        with open(os.path.join(wallet_dir, "stake.skey"), "wb") as f:
            f.write(encrypted_stkey)
        payment_vkey = skey.to_verification_key()
        stake_vkey = stkey.to_verification_key()
        payment_hash = payment_vkey.hash()
        stake_hash = stake_vkey.hash()
        address = Address(
            payment_part=payment_hash,
            staking_part=stake_hash,
            network=current_network()
        )
        logging.debug(f"Address created: {address}")
        encrypted_address = encrypt_data(ensure_bytes(str(address)), password)
        with open(os.path.join(wallet_dir, "address.enc"), "wb") as f:
            f.write(encrypted_address)
        with open(os.path.join(wallet_dir, "network.txt"), "w") as f:
            f.write(SELECTED_NETWORK or "preprod")
    except Exception as e:
        logging.error(f"Failed to save encrypted wallet: {e}")
        import traceback
        logging.error(traceback.format_exc())
        console.print(f"[red]Failed to save encrypted wallet: {e}[/red]")
        raise

def load_encrypted_wallet(wallet_dir: str, password: str) -> tuple[
        PaymentExtendedSigningKey | PaymentSigningKey,
        StakeExtendedSigningKey | StakeSigningKey]:
    """Fixed decryption handling"""
    try:
        with open(os.path.join(wallet_dir, "payment.skey"), "rb") as f:
            encrypted_skey = f.read()
        with open(os.path.join(wallet_dir, "stake.skey"), "rb") as f:
            encrypted_stkey = f.read()

        try:
            skey_bytes = decrypt_data(encrypted_skey, password)
            stkey_bytes = decrypt_data(encrypted_stkey, password)
        except InvalidToken as e:
            logging.error("Invalid password or corrupted wallet files during decryption.")
            raise EncryptionError("Invalid password or corrupted wallet files.") from e

        # Key form is identified by payload length: mnemonic-derived Cardano
        # keys are extended (ed25519-bip32, 128-byte payload); the 32-byte form
        # only appears in wallets written by older versions of this program.
        payment_skey: PaymentExtendedSigningKey | PaymentSigningKey
        stake_skey: StakeExtendedSigningKey | StakeSigningKey
        if len(skey_bytes) > 32:
            payment_skey = PaymentExtendedSigningKey.from_primitive(skey_bytes)
        else:
            payment_skey = PaymentSigningKey.from_primitive(skey_bytes)
        if len(stkey_bytes) > 32:
            stake_skey = StakeExtendedSigningKey.from_primitive(stkey_bytes)
        else:
            stake_skey = StakeSigningKey.from_primitive(stkey_bytes)
        return payment_skey, stake_skey
    except FileNotFoundError as e:
        logging.error(f"Wallet file not found: {e}")
        raise EncryptionError(f"Wallet file not found: {e}") from e
    except Exception as e:
        logging.error(f"Unexpected error during wallet decryption: {e}")
        raise EncryptionError(f"Unexpected error during wallet decryption: {e}") from e

def save_encrypted_mnemonic(wallet_dir: str, mnemonic: str, password: str) -> None:
    try:
        encrypted = encrypt_data(mnemonic.encode("utf-8"), password)
        with open(os.path.join(wallet_dir, "mnemonic.enc"), "wb") as f:
            f.write(encrypted)
    except Exception as e:
        console.print(f"[red]Failed to save encrypted mnemonic: {e}[/red]")
        raise

def load_encrypted_mnemonic(wallet_dir: str, password: str) -> str:
    try:
        with open(os.path.join(wallet_dir, "mnemonic.enc"), "rb") as f:
            encrypted = f.read()
        decrypted = decrypt_data(encrypted, password)
        return decrypted.decode("utf-8")
    except FileNotFoundError as e:
        raise FileNotFoundError("Mnemonic file not found for this wallet.") from e
    except Exception as e:
        raise RuntimeError(f"Failed to decrypt mnemonic: {e}") from e

def load_encrypted_address(wallet_dir: str, password: str) -> str:
    """Load and decrypt the wallet address."""
    try:
        with open(os.path.join(wallet_dir, "address.enc"), "rb") as f:
            encrypted_address = f.read()
        address = ensure_string(decrypt_data(encrypted_address, password))
        return address
    except FileNotFoundError as e:
        raise FileNotFoundError("Address file not found for this wallet.") from e
    except Exception as e:
        raise RuntimeError(f"Failed to load wallet address: {e}") from e

def load_wallet_network(wallet_dir: str) -> str:
    """Load the network a wallet was created on. Returns '' if unknown."""
    try:
        with open(os.path.join(wallet_dir, "network.txt")) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""

def flash_banner(text: str, flashes: int = 5, delay: float = 0.3) -> None:
    """Flash a warning banner on the console."""
    for _ in range(flashes):
        console.print(f"[bold {ERROR_COLOR}]{text}[/bold {ERROR_COLOR}]")
        time.sleep(delay)
        console.print("\032", end=f"{text}")  # Clear the line
        time.sleep(delay)
    console.print(f"[bold {ERROR_COLOR}]{text}[/bold {ERROR_COLOR}]")

def password_requirements() -> dict[str, dict[str, str]]:
    return {
        "length": {"regex": r".{8,}", "message": "At least 8 characters"},
        "uppercase": {"regex": r"[A-Z]", "message": "At least one uppercase letter"},
        "lowercase": {"regex": r"[a-z]", "message": "At least one lowercase letter"},
        "digit": {"regex": r"\d", "message": "At least one digit"},
        "special": {"regex": r"[!@#$%^&*(),.?\":{}|<>]",
                    "message": "At least one special character"},
    }

def show_password_requirements() -> None:
    console.print(f"[bold {PRIMARY_COLOR}]Password must meet the following requirements:[/]")
    for req in password_requirements().values():
        console.print(f" - [bold {ACCENT_COLOR}]{req['message']}[/]")

def check_password_strength(password: str) -> tuple[float, list[str]]:
    reqs = password_requirements()
    passed = 0
    failed_reqs = []
    for val in reqs.values():
        if re.search(val["regex"], password):
            passed += 1
        else:
            failed_reqs.append(val["message"])
    strength = passed / len(reqs)
    return strength, failed_reqs

def render_strength_bar(strength: float) -> str:
    bar_length = 20
    filled_length = int(bar_length * strength)
    bar = (f"[{ACCENT_COLOR}]" + "#" * filled_length + f"[/{ACCENT_COLOR}]"
           + "-" * (bar_length - filled_length))
    return bar

def prompt_password_with_strength() -> str:
    show_password_requirements()
    while True:
        password = session.prompt(
            HTML("<ansired>Enter your password: </ansired>"), is_password=True)
        strength, failed = check_password_strength(password)
        bar = render_strength_bar(strength)
        console.print(f"Strength: {bar} ({strength*100:.0f}%)")
        if failed:
            console.print("[red]Password requirements not met:[/red]")
            for f in failed:
                console.print(f" - {f}")
            console.print("Please try again.\n")
        else:
            confirm = session.prompt(
                HTML("<ansiblue>Confirm your password: </ansiblue>"),
                is_password=True)
            if password != confirm:
                console.print("[red]Passwords do not match. Please try again.[/red]\n")
                continue
            return password


def with_spinner(context_message: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to show a spinning wheel with a custom context message."""
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with Progress(SpinnerColumn(),
                          TextColumn(f"[cyan]{context_message}[/cyan]"),
                          transient=True, console=console) as progress:
                task = progress.add_task(f"[cyan]{context_message}[/cyan]")
                result = func(*args, **kwargs)
                progress.update(task, completed=1)
                return result
        wrapper.__name__ = getattr(func, "__name__", "wrapped")
        wrapper.__qualname__ = getattr(func, "__qualname__", "wrapped")
        wrapper.__doc__ = getattr(func, "__doc__", None)
        wrapper.__module__ = getattr(func, "__module__", __name__)
        return wrapper
    return decorator

@with_spinner("Enter your password")
def prompt_existing_password() -> str:
    return session.prompt("> ", is_password=True)

def generate_mnemonic() -> str:
    """Generate a 24-word mnemonic compliant with Cardano."""
    mnemo = Mnemonic("english")
    mnemonic = mnemo.generate(strength=256)  # 256-bit strength ensures 24 words
    if not mnemo.check(mnemonic):
        raise ValueError("Generated mnemonic is invalid.")
    return mnemonic

def hdwallet_from_mnemonic(mnemonic: str) -> HDWallet:
    """Build the Cardano master key from a BIP-39 mnemonic.

    Cardano does NOT use the BIP-39 seed. It uses the Icarus master-key scheme
    (CIP-3): the mnemonic's *entropy* is stretched with PBKDF2-HMAC-SHA512 into
    the ed25519-bip32 master key. ``HDWallet.from_mnemonic`` implements that;
    ``HDWallet.from_seed(bip39_seed)`` produces a completely different key tree
    whose addresses and key hashes match no real Cardano wallet.

    Every derivation in this program goes through here so that mnemonics
    round-trip with Lace, Eternl, Yoroi, cardano-cli and any CIP-1854 multisig
    service.
    """
    mnemo = Mnemonic("english")
    if not mnemo.check(mnemonic):
        raise ValueError("Invalid mnemonic phrase provided")
    return HDWallet.from_mnemonic(mnemonic)


def derive_keys_from_mnemonic(mnemonic: str) -> tuple[ExtendedSigningKey,
                                                      ExtendedSigningKey]:
    """Derive the CIP-1852 payment and stake signing keys from a mnemonic.

    Path: m/1852'/1815'/0'/0/0 (payment) and m/1852'/1815'/0'/2/0 (stake).
    Returns extended (ed25519-bip32) signing keys, which is what Cardano HD
    wallets actually hold — the 32-byte non-extended form cannot be recovered
    from a mnemonic.
    """
    hd_wallet = hdwallet_from_mnemonic(mnemonic)
    payment_skey = PaymentExtendedSigningKey.from_hdwallet(
        hd_wallet.derive_from_path("m/1852'/1815'/0'/0/0")
    )
    stake_skey = StakeExtendedSigningKey.from_hdwallet(
        hd_wallet.derive_from_path("m/1852'/1815'/0'/2/0")
    )
    return payment_skey, stake_skey


# --- Multisig (CIP-1854) Functions ---

def key_hash_from_vkey(vkey_bytes: bytes) -> bytes:
    """Compute a Cardano key hash (blake2b-224) from verification key bytes.

    Accepts either a 32-byte public key or a 64-byte extended verification key
    (public key || chain code). The chain code is never part of the hash, so a
    64-byte input is trimmed to its first 32 bytes — hashing all 64 would yield
    a hash that matches nothing on chain.
    """
    if len(vkey_bytes) == 64:
        vkey_bytes = vkey_bytes[:32]
    if len(vkey_bytes) != 32:
        raise ValueError(
            f"Invalid verification key length: {len(vkey_bytes)}, expected 32 or 64"
        )
    return hashlib.blake2b(vkey_bytes, digest_size=28).digest()


def extract_key_hashes_from_script(
        script: NativeScript) -> tuple[list[bytes], int | None, str | None]:
    """Recursively walk a NativeScript tree and extract all key hashes.

    Also extracts the threshold (for atLeast) and script type.

    Returns (key_hashes list, threshold or None, script_type str or None).
    """
    from pycardano import ScriptPubkey

    key_hashes: list[bytes] = []
    threshold: int | None = None
    script_type: str | None = None

    if isinstance(script, ScriptPubkey):
        kh = script.key_hash
        key_hashes.append(kh.payload if hasattr(kh, 'payload') else bytes(kh))
        script_type = "sig"

    elif isinstance(script, ScriptNofK):
        threshold = script.n
        script_type = "atLeast"
        for sub in script.native_scripts:
            sub_hashes, _, _ = extract_key_hashes_from_script(sub)
            key_hashes.extend(sub_hashes)

    elif isinstance(script, ScriptAll):
        script_type = "all"
        for sub in script.native_scripts:
            sub_hashes, _, _ = extract_key_hashes_from_script(sub)
            key_hashes.extend(sub_hashes)

    elif isinstance(script, ScriptAny):
        script_type = "any"
        for sub in script.native_scripts:
            sub_hashes, _, _ = extract_key_hashes_from_script(sub)
            key_hashes.extend(sub_hashes)

    return key_hashes, threshold, script_type


def script_min_signatures(script: NativeScript) -> int:
    """The fewest signatures that can satisfy this script.

    The threshold cannot be read off a single field, because a native script is
    a tree. Only `atLeast` carries a number; `all` needs every branch, `any`
    needs the cheapest one, and a timelock needs no signature at all — it is
    satisfied by time rather than by a key.

    Getting this wrong is not cosmetic. Treating `any` (a joint account, where
    either party may spend alone) as N-of-N would make a recovered wallet
    unspendable the moment one cosigner is gone — exactly the situation recovery
    exists for. Counting a minimum never blocks a spend the chain would accept;
    the ledger remains the final arbiter, and a rejected submission is surfaced.
    """
    if isinstance(script, ScriptPubkey):
        return 1
    if isinstance(script, (InvalidBefore, InvalidHereAfter)):
        # A time condition costs no signature; it is met by the validity window.
        return 0
    if isinstance(script, ScriptAll):
        return sum(script_min_signatures(s) for s in script.native_scripts)
    if isinstance(script, ScriptAny):
        costs = [script_min_signatures(s) for s in script.native_scripts]
        return min(costs) if costs else 0
    if isinstance(script, ScriptNofK):
        # Satisfy the n cheapest branches.
        costs = sorted(script_min_signatures(s) for s in script.native_scripts)
        return sum(costs[:script.n])
    # An unknown constructor is not something to guess about: assume it needs a
    # signature rather than reporting a spend as free.
    logging.warning(f"Unrecognised native script member: {type(script).__name__}")
    return 1


def script_is_flat_threshold(script: NativeScript) -> bool:
    """True when the script is a plain m-of-n over signatures and nothing else.

    Flat scripts can be described honestly as "m of n must sign". Anything with
    nesting or timelocks cannot, so the UI says "at least m" instead of implying
    a simple count.
    """
    members: list[NativeScript]
    if isinstance(script, ScriptPubkey):
        return True
    if isinstance(script, (ScriptAll, ScriptAny, ScriptNofK)):
        members = list(script.native_scripts)
    else:
        return False
    return all(isinstance(s, ScriptPubkey) for s in members)


def build_native_script_from_key_hashes(threshold: int,
                                        key_hashes: list[bytes],
                                        not_before_slot: int | None = None,
                                        not_after_slot: int | None = None) -> NativeScript:
    """Build the wallet's native script from cosigner key hashes.

    This is the only way a multisig wallet is created here, and it takes key
    hashes because key hashes are the only cosigner material that may cross
    machines (spec §Operating Model). A key hash is public — it is literally
    what sits inside the script and on chain — so sharing one reveals nothing
    that lets anyone derive a key, derive addresses, or sign.

    Cosigner xpubs are deliberately not accepted. An xpub lets its holder derive
    the whole address family of that cosigner, which is private information
    belonging to the cosigner alone, and a script built by combining xpubs is
    not re-derivable by anyone who holds only the script.

    The shape is `atLeast(threshold, [sig(h) for h in key_hashes])`, optionally
    wrapped as `all([atLeast(...), after(slot), before(slot)])` when a timelock
    is asked for. Nothing more elaborate is emitted, so the threshold of a
    wallet made here is always legible; `extract_key_hashes_from_script` and
    `script_min_signatures` read back the wider grammar that recovery brings in.

    Args:
        threshold: How many of the cosigners must sign.
        key_hashes: The cosigners' payment key hashes, in script order.
        not_before_slot: Funds cannot be spent until this slot (`after`).
        not_after_slot: Funds cannot be spent from this slot on (`before`).
    """
    if threshold < 1:
        raise ValueError("Threshold must be at least 1.")
    if threshold > len(key_hashes):
        raise ValueError(
            f"Threshold {threshold} exceeds the {len(key_hashes)} cosigner(s) available."
        )
    if len(key_hashes) < 1:
        raise ValueError("A script needs at least one cosigner key hash.")
    if len(set(key_hashes)) != len(key_hashes):
        raise ValueError("Duplicate cosigner key hash: each cosigner must be distinct.")
    for kh in key_hashes:
        if len(kh) != 28:
            raise ValueError(
                f"Invalid key hash length: {len(kh)} bytes, expected 28 (blake2b-224)."
            )
    threshold_script = ScriptNofK(n=threshold, native_scripts=list[_NativeScriptMember](
        ScriptPubkey(key_hash=VerificationKeyHash(kh)) for kh in key_hashes))

    if not_before_slot is None and not_after_slot is None:
        return threshold_script

    if (not_before_slot is not None and not_after_slot is not None
            and not_before_slot >= not_after_slot):
        raise ValueError(
            f"The unlock slot ({not_before_slot}) is not before the expiry slot "
            f"({not_after_slot}), so the wallet could never be spent."
        )

    # A timelock is an extra condition that must hold as well as the signatures,
    # which is `all`, not another branch of the threshold. Wrapping it the other
    # way round would make the timelock an alternative to signing.
    members: list[_NativeScriptMember] = [threshold_script]
    if not_before_slot is not None:
        members.append(InvalidBefore(not_before_slot))
    if not_after_slot is not None:
        members.append(InvalidHereAfter(not_after_slot))
    return ScriptAll(native_scripts=members)


def script_timelocks(script: NativeScript) -> tuple[int | None, int | None]:
    """The validity window a script demands, as (earliest slot, expiry slot).

    Both are read off the whole tree, taking the strictest of each so a
    transaction built against them satisfies every branch that could apply.
    `InvalidBefore(s)` means the transaction's validity interval may not start
    before slot s; `InvalidHereAfter(s)` means it must end by slot s. A
    transaction that ignores them is rejected by the ledger with no explanation
    a user could act on, so they are read here and applied at build time.
    """
    not_before: int | None = None
    not_after: int | None = None

    def walk(node: NativeScript) -> None:
        nonlocal not_before, not_after
        if isinstance(node, InvalidBefore):
            not_before = node.before if not_before is None else max(not_before, node.before)
        elif isinstance(node, InvalidHereAfter):
            not_after = node.after if not_after is None else min(not_after, node.after)
        elif isinstance(node, (ScriptAll, ScriptAny, ScriptNofK)):
            for sub in node.native_scripts:
                walk(sub)

    walk(script)
    return not_before, not_after


# A cosigner is named in a native script by one key hash, and which key a given
# tool put there depends on the standard that tool followed. Both paths below
# are derived whenever this program has to answer "is this wallet a cosigner of
# that script", because when a script arrives from a dead provider the path it
# used was never ours to choose.
COSIGNER_DERIVATION_PATHS: dict[str, str] = {
    # The ordinary wallet path. Personal wallets in this program use it, so it is
    # what a script built from "my everyday wallet" names.
    "CIP-1852 (personal wallet)": "m/1852'/1815'/{account}'/0/{index}",
    # The multi-signature path. Shared-wallet tooling that follows CIP-1854
    # (Lace shared wallets and the providers built on the same convention)
    # names this key instead.
    "CIP-1854 (shared wallet)": "m/1854'/1815'/{account}'/0/{index}",
}


COSIGNER_SEARCH_ACCOUNTS = 3
COSIGNER_SEARCH_INDICES = 20


def find_cosigner_derivation(
        mnemonic: str, script_key_hashes: set[bytes],
        max_accounts: int = COSIGNER_SEARCH_ACCOUNTS,
        max_indices: int = COSIGNER_SEARCH_INDICES,
        only_hash: bytes | None = None) -> tuple[str, bytes, HDWallet] | None:
    """Find where in this mnemonic's tree a key the script names actually lives.

    Returns (path, key hash, the HD node to sign with), or None.

    Cosigner keys are overwhelmingly at account 0, address 0, so that is tried
    first for both standards and normally ends the search immediately. Beyond
    that the position is the choice of whichever software built the script — a
    provider is free to have used account 1, or address 7 — and a wallet whose
    key sits somewhere unexpected is otherwise indistinguishable from "you are
    not a cosigner". Since the search only compares against key hashes the
    script already names, widening it reveals nothing and cannot produce a
    false match.

    `only_hash` targets one specific key. A wallet can hold several of a
    script's keys, and the one to sign with is the user's choice — without
    this the search would return whichever of them it happens to reach first,
    which is not necessarily the one that was picked.
    """
    hd_wallet = hdwallet_from_mnemonic(mnemonic)

    def check(path: str) -> tuple[str, bytes, HDWallet] | None:
        child = hd_wallet.derive_from_path(path)
        child_public_key: bytes = child.public_key
        candidate = key_hash_from_vkey(child_public_key)
        if candidate in script_key_hashes and (only_hash is None
                                               or candidate == only_hash):
            return path, candidate, child
        return None

    for template in COSIGNER_DERIVATION_PATHS.values():
        found = check(template.format(account=0, index=0))
        if found:
            return found

    for account in range(max_accounts):
        for index in range(max_indices):
            if account == 0 and index == 0:
                continue  # already tried above
            for template in COSIGNER_DERIVATION_PATHS.values():
                found = check(template.format(account=account, index=index))
                if found:
                    return found
    return None


def cosigner_key_hash_candidates(mnemonic: str, account_index: int = 0,
                                 address_index: int = 0) -> dict[str, bytes]:
    """Every payment key hash a script could name this mnemonic by.

    Returns {human-readable path label: key hash bytes}.

    Recovering a wallet from a sunset provider is the case this exists for. The
    script names a key hash, and matching it means deriving the same key the
    provider's software derived. Trying only one standard would import such a
    wallet successfully and then find it unsignable — the precise failure the
    recovery path is meant to prevent — so every path a real cosigner key is
    written at is derived and matched against the script.
    """
    hd_wallet = hdwallet_from_mnemonic(mnemonic)
    candidates: dict[str, bytes] = {}
    for label, template in COSIGNER_DERIVATION_PATHS.items():
        path = template.format(account=account_index, index=address_index)
        child = hd_wallet.derive_from_path(path)
        child_public_key: bytes = child.public_key
        candidates[label] = key_hash_from_vkey(child_public_key)
    return candidates


def script_to_address(script_hash_bytes: bytes, stake_script_hash: bytes | None = None) -> str:
    """Convert script hash to bech32 address.

    If stake_script_hash is provided, returns a base address (payment + stake).
    Otherwise returns a payment-only address.
    """
    script_hash_obj = ScriptHash(script_hash_bytes)
    if stake_script_hash:
        stake_hash_obj = ScriptHash(stake_script_hash)
        addr = Address(
            payment_part=script_hash_obj,
            staking_part=stake_hash_obj,
            network=current_network()
        )
    else:
        addr = Address(
            payment_part=script_hash_obj,
            network=current_network()
        )
    return str(addr)


def script_hash_from_script(script: NativeScript) -> bytes:
    """Get the script hash bytes from a native script."""
    sh = pycardano_script_hash(script)
    primitive = sh.to_primitive()
    if not isinstance(primitive, (bytes, bytearray)):
        raise ValueError("Script hash serialization produced a non-byte payload.")
    return bytes(primitive)


def calculate_threshold(total_cosigners: int, percentage: int) -> int:
    """Calculate required signatures from percentage.

    Uses floor so that P% means 'at least P% of cosigners must approve'.
    Matches standard multisig conventions:
        67% of 3 = 2 (2/3 = 66.7%)
        50% of 6 = 3 (3/6 = 50%)
        100% of any N = N (N/N = 100%)
    """
    if percentage <= 0 or percentage > 100:
        raise ValueError("Percentage must be between 1 and 100")
    if total_cosigners <= 0:
        raise ValueError("Must have at least one cosigner")
    if percentage == 100:
        return total_cosigners
    import math
    m = math.floor(total_cosigners * percentage / 100)
    return max(1, min(m, total_cosigners))


def slot_for_datetime(when: datetime) -> int:
    """The slot a given moment falls on, counted forward from the current tip.

    Since Shelley a slot is one second on every Cardano network, so a number of
    seconds into the future is the same number of slots past the tip. Counting
    from the live tip rather than from a hardcoded genesis means this never
    needs per-network era tables, and never silently uses the wrong ones.
    """
    if not context:
        raise ValueError("No backend configured, so the current slot is unknown.")
    seconds = int((when - datetime.now(UTC)).total_seconds())
    if seconds < 0:
        raise ValueError("That moment is in the past.")
    return context.last_block_slot + seconds


def datetime_for_slot(slot: int) -> datetime:
    """When a slot falls, counted from the current tip. Inverse of the above."""
    if not context:
        raise ValueError("No backend configured, so the current slot is unknown.")
    return datetime.now(UTC) + timedelta(seconds=slot - context.last_block_slot)


def regenerate_address(wallet_dir: str, password: str) -> Address:
    """Regenerate a Cardano-compliant address for a wallet."""
    try:
        mnemonic = load_encrypted_mnemonic(wallet_dir, password)
        payment_skey, stake_skey = derive_keys_from_mnemonic(mnemonic)
        payment_vkey = payment_skey.to_verification_key()
        stake_vkey = stake_skey.to_verification_key()
        address = Address(
            payment_part=payment_vkey.hash(),
            staking_part=stake_vkey.hash(),
            network=current_network()
        )
        save_encrypted_wallet(wallet_dir, payment_skey, stake_skey, password)
        return address
    except Exception as e:
        raise RuntimeError(f"Failed to regenerate address: {e}") from e

def secure_path_join(base: str, sub_path: JSON | str) -> str:
    """
    Joins a base path with a sub-path, ensuring that the sub_path is a string.
    If sub_path is a dict, it converts the relevant part of it to a string.
    """
    # dict form carries the wallet name; anything else joins as a plain string
    sub_path_str = (_json_str(_json_object(sub_path).get("name"))
                    if isinstance(sub_path, dict) else str(sub_path))
    return os.path.join(base, sub_path_str)

@exception_error
def ensure_wallet_files(wallet_dir: str, wallet_meta: dict[str, JSON],
                        password: str) -> bool:
    """
    Ensure that all required wallet files (keys, address, mnemonic) exist.
    If any are missing, prompt the user to regenerate or import them.
    Automatically create missing data if the user agrees.
    """
    required_files = [
        "payment.skey",
        "stake.skey",
        "payment.vkey",
        "stake.vkey",
        "mnemonic.enc",
        "address.enc"
    ]
    missing = []
    for fname in required_files:
        if not os.path.exists(os.path.join(wallet_dir, fname)):
            missing.append(fname)

    if not missing:
        console.print("[green]All required wallet files are present.[/green]")
        return True  # All files exist

    console.print(f"[yellow]Wallet '{wallet_meta.get('name', wallet_dir)}' is missing files:"
        f"{missing}[/yellow]")
    console.print("[bold]Would you like to automatically create the missing wallet data?"
        "(yes/no)[/bold]")
    while True:
        choice = session.prompt("> ").strip().lower()
        if choice in ("yes", "y"):
            try:
                console.print("[bold]Generating new wallet data...[/bold]")
                mnemonic_phrase = generate_mnemonic()
                payment_skey, stake_skey = derive_keys_from_mnemonic(mnemonic_phrase)
                payment_vkey = payment_skey.to_verification_key()
                stake_vkey = stake_skey.to_verification_key()

                # Save generated data
                save_encrypted_wallet(wallet_dir, payment_skey, stake_skey, password)
                save_encrypted_mnemonic(wallet_dir, mnemonic_phrase, password)
                payment_vkey.save(os.path.join(wallet_dir, "payment.vkey"))
                stake_vkey.save(os.path.join(wallet_dir, "stake.vkey"))

                console.print("[green]Wallet data created successfully.[/green]")
                return True
            except ValueError as e:
                console.print(f"[red]Value error during wallet creation: {e}[/red]")
                raise WalletDataGenerationError(f"Value error: {e}") from e
            except TypeError as e:
                console.print(f"[red]Type error during wallet creation: {e}[/red]")
                raise WalletDataGenerationError(f"Type error: {e}") from e
            except FileNotFoundError as e:
                console.print(f"[red]File not found during wallet creation: {e}[/red]")
                raise WalletDataGenerationError(f"File not found: {e}") from e
            except Exception as e:
                console.print(f"[red]Unexpected error during wallet creation: {e}[/red]")
                raise WalletDataGenerationError(f"Unexpected error: {e}") from e
            finally:
                console.print("[yellow]Attempted to create missing wallet data. Please verify the "
                    "results.[/yellow]")
        elif choice in ("no", "n"):
            console.print("[yellow]Operation cancelled by user. Missing wallet data remains "
                "unresolved.[/yellow]")
            return False
        else:
            console.print("[red]Invalid option. Please type 'yes' or 'no'.[/red]")

@exception_error
def save_user_data(username: str, data: dict[str, JSON], password: str) -> None:
    salt = os.urandom(16)
    key = derive_key(password, salt)
    f = Fernet(key)
    payload = json.dumps(data).encode("utf-8")
    encrypted = f.encrypt(payload)
    user_file = os.path.join(USER_DATA_DIR, f"{username}.userdb")
    with open(user_file, "wb") as file:
        file.write(salt + encrypted)

@exception_error
def load_user_data(username: str, password: str) -> dict[str, JSON]:
    user_file = os.path.join(USER_DATA_DIR, f"{username}.userdb")
    if not os.path.exists(user_file):
        raise FileNotFoundError(f"User data file for '{username}' not found.")
    with open(user_file, "rb") as file:
        content = file.read()
    salt = content[:16]
    encrypted = content[16:]
    key = derive_key(password, salt)
    f = Fernet(key)
    decrypted = f.decrypt(encrypted)
    data = _json_object(_json_loads(decrypted.decode("utf-8")))
    # Canonical entry format: every consumer of the wallet list sees dict
    # entries. Migrating here (the single loader) instead of per-view keeps
    # flows like the multisig sign picker from missing string entries.
    if _migrate_wallet_entries(data, password):
        save_user_data(username, data, password)
    return data

@exception_error
def user_register() -> tuple[str | None, str | None]:
    console.print("[bold green]Register a new user[/bold green]")
    while True:
        console.print("[bold]Please choose a username (letters and numbers only):[/bold]")
        username = session.prompt("> ").strip()
        if not username:
            console.print("[red]Username cannot be empty.[/red]")
            continue
        if not re.match(r'^[a-zA-Z0-9]+$', username):
            console.print("[red]Username must contain only letters and numbers.[/red]")
            continue
        user_file = os.path.join(USER_DATA_DIR, f"{username}.userdb")
        if os.path.exists(user_file):
            console.print(f"[yellow]User '{username}' already exists.[/yellow]")
            console.print("[yellow]Please login instead or choose a different username.[/yellow]")
            return None, None
        break
    password = prompt_password_with_strength()
    user_data: dict[str, JSON] = {
        "wallets": [],
        "settings": {},
        "created_at": datetime.now(UTC).isoformat(),
    }
    save_user_data(username, user_data, password)
    console.print(f"[green]User '{username}' registered successfully![/green]")
    return username, password

@exception_error
def user_login() -> tuple[str | None, str | None]:
    console.print("[bold green]User login[/bold green]")
    username = session.prompt("> ").strip()
    user_file = os.path.join(USER_DATA_DIR, f"{username}.userdb")
    if not os.path.exists(user_file):
        console.print("[red]User not found. Please register first.[/red]")
        return None, None

    for _attempt in range(3):
        # Use the spinner-based prompt for password entry
        password = prompt_with_spinner("Enter your password", is_password=True)
        try:
            user_data = load_user_data(username, password)
            if user_data:
                console.print(f"[green]Welcome back, {username}![/green]")
                return username, password
        except Exception:
            console.print("[red]Incorrect password. Please try again.[/red]")
    console.print("[red]Too many failed attempts. Exiting.[/red]")
    sys.exit(1)

def prompt_with_spinner(prompt_text: str, is_password: bool = False) -> str:
    with Progress(SpinnerColumn(), TextColumn(f"[cyan]{prompt_text}[/cyan]"),
                  transient=True, console=console) as progress:
        task = progress.add_task(f"[cyan]{prompt_text}[/cyan]")
        value = session.prompt("> ", is_password=is_password)
        progress.update(task, completed=1)
    return value

@exception_error
def wallet_create(current_user: str, user_password: str) -> None:
    logging.debug(f"[ENTRY] wallet_create(user={current_user})")
    """Fixed wallet creation with proper byte handling"""
    try:
        user_data = load_user_data(current_user, user_password)
    except Exception as e:
        console.print(f"[red]Failed to load user data: {e}[/red]")
        return
    if user_data is None:
        return

    console.print("[bold]Creating a new wallet.[/bold]")
    while True:
        console.print("[bold]Please enter a unique wallet name (letters, numbers, underscores "
            "only), or type 'cancel' to abort:[/bold]")
        wallet_name = session.prompt("> ").strip()
        if wallet_name.lower() == "cancel":
            console.print("[yellow]Wallet creation cancelled.[/yellow]")
            return
        if not wallet_name:
            console.print("[red]Wallet name cannot be empty. Please try again or type 'cancel' to "
                "abort.[/red]")
            continue
        if not re.match(r'^[a-zA-Z0-9_]+$', wallet_name):
            console.print("[red]Wallet name must contain only letters, numbers, and underscores."
                "Please try again or type 'cancel' to abort.[/red]")
            continue
        wallet_dir = secure_path_join(WALLET_DIR, wallet_name)
        if os.path.exists(wallet_dir):
            console.print("[red]Wallet name already exists. Please choose another or type 'cancel'"
                "to abort.[/red]")
            continue
        break

    console.print("[bold]Please enter your password to encrypt the wallet's signing keys:[/bold]")
    password = prompt_existing_password()

    mnemonic_phrase = generate_mnemonic()
    console.print("\n[bold green]IMPORTANT: Save your 24-word mnemonic securely![/bold green]")
    console.print(f"[bold]{mnemonic_phrase}[/bold]\n")

    # A displayed-only mnemonic is a mnemonic nobody saved. Prove the user has
    # it before any keys are derived or written.
    check_index = secrets.randbelow(24) + 1
    expected_word = mnemonic_phrase.split()[check_index - 1]
    while True:
        console.print(f"[bold]To confirm you saved it, type word #{check_index} of your"
                      " mnemonic (or 'cancel' to abort):[/bold]")
        answer = session.prompt("> ").strip()
        if answer.lower() == "cancel":
            console.print("[yellow]Wallet creation cancelled.[/yellow]")
            return
        if answer == expected_word:
            console.print("[green]Mnemonic confirmed.[/green]\n")
            break
        console.print("[red]That is not the right word. Here is the mnemonic again —"
                      " save it now.[/red]")
        console.print(f"[bold]{mnemonic_phrase}[/bold]\n")

    try:
        # Use corrected derivation
        payment_skey, stake_skey = derive_keys_from_mnemonic(mnemonic_phrase)
        logging.debug(f"payment_skey type: {type(payment_skey)}")
        logging.debug(f"stake_skey type: {type(stake_skey)}")
        payment_vkey = payment_skey.to_verification_key()
        stake_vkey = stake_skey.to_verification_key()
        logging.debug(f"payment_vkey type: {type(payment_vkey)}")
        logging.debug(f"stake_vkey type: {type(stake_vkey)}")
        address = Address(
            payment_part=payment_vkey.hash(),
            staking_part=stake_vkey.hash(),
            network=current_network()
        )
        logging.debug(f"Address created: {address}")

        # Save with proper byte handling
        wallet_dir = secure_path_join(WALLET_DIR, wallet_name)
        os.makedirs(wallet_dir, exist_ok=True)

        # Save keys as bytes without string conversion; the plaintext vkeys go
        # to disk too, matching what the import path writes.
        save_encrypted_wallet(wallet_dir, payment_skey, stake_skey, password)
        save_encrypted_mnemonic(wallet_dir, mnemonic_phrase, password)
        payment_vkey.save(os.path.join(wallet_dir, "payment.vkey"))
        stake_vkey.save(os.path.join(wallet_dir, "stake.vkey"))

        console.print(f"[green]Wallet '{wallet_name}' created successfully![/green]")
        console.print(f"Address: [cyan]{address}[/cyan]")

        # Update user data — dict entry, never a bare string: flows like the
        # multisig sign picker only see dict entries, and a stored address
        # spares migration from re-deriving it with the user's password.
        wallets = _json_list(user_data.get("wallets"))
        if wallet_name not in [_wallet_entry_name(w) for w in wallets]:
            wallets.append({"name": wallet_name, "address": str(address),
                            "type": "normal"})
            user_data["wallets"] = wallets
            save_user_data(current_user, user_data, user_password)

    except Exception as e:
        logging.error(f"Wallet creation failed: {e}")
        import traceback
        logging.error(traceback.format_exc())
        console.print(f"[red]Wallet creation failed: {e}[/red]")
        shutil.rmtree(wallet_dir, ignore_errors=True)
    logging.debug(f"[EXIT] wallet_create(user={current_user})")

@exception_error
def wallet_import(current_user: str, user_password: str) -> None:
    logging.debug(f"[ENTRY] wallet_import(user={current_user})")
    """Import wallet with proper seed handling"""
    try:
        user_data = load_user_data(current_user, user_password)
    except Exception as e:
        console.print(f"[red]Failed to load user data: {e}[/red]")
        return
    if user_data is None:
        return

    console.print("[bold]Import a wallet[/bold]")
    while True:
        console.print("[bold]Please enter a unique wallet name for the imported wallet (letters,"
            "numbers, underscores only), or type 'cancel' to abort:[/bold]")
        wallet_name = session.prompt("> ").strip()
        if wallet_name.lower() == "cancel":
            console.print("[yellow]Wallet import cancelled.[/yellow]")
            return
        if not wallet_name:
            console.print("[red]Wallet name cannot be empty. Please try again or type 'cancel' to "
                "abort.[/red]")
            continue
        if not re.match(r'^[a-zA-Z0-9_]+$', wallet_name):
            console.print("[red]Wallet name must contain only letters, numbers, and underscores."
                "Please try again or type 'cancel' to abort.[/red]")
            continue
        wallet_dir = secure_path_join(WALLET_DIR, wallet_name)
        if os.path.exists(wallet_dir):
            console.print("[red]Wallet name already exists. Please choose another or type 'cancel'"
                "to abort.[/red]")
            continue
        break

    console.print("[bold]Choose import method (type the number or 'cancel' to abort):[/bold]")
    console.print("1. Import using 24-word mnemonic passphrase")
    console.print("2. Import using existing encrypted signing key files")

    while True:
        choice = session.prompt("> ").strip()
        if choice.lower() == "cancel":
            console.print("[yellow]Wallet import cancelled.[/yellow]")
            return
        if choice not in ("1", "2"):
            console.print("[red]Invalid choice. Please enter '1', '2', or 'cancel'.[/red]")
            continue
        break

    if choice == "1":  # Mnemonic import
        console.print("[bold]Enter your 24-word mnemonic passphrase separated by spaces or type"
            "'cancel' to abort:[/bold]")
        mnemonic_phrase = session.prompt("> ").strip()
        if mnemonic_phrase.lower() == "cancel":
            console.print("[yellow]Wallet import cancelled.[/yellow]")
            return
        mnemo = Mnemonic("english")
        if not mnemo.check(mnemonic_phrase):
            console.print("[red]Invalid mnemonic phrase. Import aborted.[/red]")
            return

        console.print("[bold]Enter your password to encrypt the wallet's signing keys:[/bold]")
        password = prompt_existing_password()

        # Created only once the mnemonic has validated: an abort above must not
        # leave an empty wallet dir squatting on the name.
        os.makedirs(wallet_dir, exist_ok=True)

        try:
            # Use corrected derivation
            payment_skey, stake_skey = derive_keys_from_mnemonic(mnemonic_phrase)

            # Generate address
            payment_vkey = payment_skey.to_verification_key()
            stake_vkey = stake_skey.to_verification_key()
            address = Address(
                payment_part=payment_vkey.hash(),
                staking_part=stake_vkey.hash(),
                network=current_network()
            )

            # Save encrypted files
            save_encrypted_wallet(wallet_dir, payment_skey, stake_skey, password)
            save_encrypted_mnemonic(wallet_dir, mnemonic_phrase, password)

            payment_vkey.save(os.path.join(wallet_dir, "payment.vkey"))
            stake_vkey.save(os.path.join(wallet_dir, "stake.vkey"))

            console.print(f"[spring_green2]Wallet '{wallet_name}' imported"
                " successfully![/spring_green2]")
            console.print(f"[white]Address: {address}[/white]")

        except Exception as e:
            console.print(f"[red]Import failed: {e}[/red]")
            shutil.rmtree(wallet_dir, ignore_errors=True)

    else:
        console.print("[yellow]You will need to provide the full paths to your encrypted payment "
            "and stake signing key files or type 'cancel' to abort.[/yellow]")
        payment_skey_path = session.prompt(
            "[bold]Enter full path to encrypted payment.skey file:[/bold] ").strip()
        if payment_skey_path.lower() == "cancel":
            console.print("[yellow]Wallet import cancelled.[/yellow]")
            return
        stake_skey_path = session.prompt(
            "[bold]Enter full path to encrypted stake.skey file:[/bold] ").strip()
        if stake_skey_path.lower() == "cancel":
            console.print("[yellow]Wallet import cancelled.[/yellow]")
            return

        console.print("[bold]Enter your password to decrypt these keys:[/bold]")
        password_old = prompt_existing_password()
        console.print("[bold]Enter your password to encrypt the wallet's signing keys:[/bold]")
        password_new = prompt_existing_password()

        try:
            with open(payment_skey_path, "rb") as f:
                encrypted_skey = f.read()
            with open(stake_skey_path, "rb") as f:
                encrypted_stkey = f.read()
            skey_bytes = decrypt_data(encrypted_skey, password_old)
            stkey_bytes = decrypt_data(encrypted_stkey, password_old)
            skey: PaymentSigningKey = PaymentSigningKey.from_primitive(skey_bytes)
            stkey: StakeSigningKey = StakeSigningKey.from_primitive(stkey_bytes)
        except Exception as e:
            console.print(f"[red]Failed to decrypt keys: {e}[/red]")
            return

        imported_payment_vkey = skey.to_verification_key()
        imported_stake_vkey = stkey.to_verification_key()
        address = Address(payment_part=imported_payment_vkey.hash(),
                          staking_part=imported_stake_vkey.hash(),
                          network=current_network())

        try:
            os.makedirs(wallet_dir, exist_ok=True)
            save_encrypted_wallet(wallet_dir, skey, stkey, password_new)
            imported_payment_vkey.save(os.path.join(wallet_dir, "payment.vkey"))
            imported_stake_vkey.save(os.path.join(wallet_dir, "stake.vkey"))
            console.print(f"[spring_green2]Wallet '{wallet_name}' imported"
                " successfully.[/spring_green2]")
        except Exception as e:
            console.print(f"[red]Failed to save imported wallet: {e}[/red]")
            return

    wallets = _json_list(user_data.get("wallets"))
    if wallet_name not in [_wallet_entry_name(w) for w in wallets]:
        wallets.append({"name": wallet_name, "address": str(address),
                        "type": "normal"})
        user_data["wallets"] = wallets
        save_user_data(current_user, user_data, user_password)
    logging.debug(f"[EXIT] wallet_import(user={current_user})")


# ── Interactive prompt helpers ───────────────────────────────────────────
#
# Every one of these re-asks on bad input and accepts 'cancel' to back out.
# Signing a transaction is stressful enough without a typo dumping the user
# back to the main menu with their work lost.


def _prompt_cancelable(message: str, hint: str = "") -> str | None:
    """Ask for a line of text. Returns None if the user typed 'cancel'."""
    console.print(f"[bold]{message}[/bold]")
    if hint:
        console.print(f"[dim]{hint}[/dim]")
    answer = session.prompt("> ").strip()
    return None if answer.lower() == "cancel" else answer


def _prompt_yes_no(default: bool | None = None) -> bool:
    """Ask a yes/no question until the answer is unambiguous."""
    while True:
        answer = session.prompt("> ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        if answer == "" and default is not None:
            return default
        console.print("[red]Please answer 'y' or 'n'.[/red]")


def _prompt_int(message: str, minimum: int, maximum: int) -> int | None:
    """Ask for a number in range, re-asking until it is one. None = cancelled."""
    while True:
        answer = _prompt_cancelable(f"{message} ({minimum}-{maximum})")
        if answer is None:
            console.print("[yellow]Cancelled.[/yellow]")
            return None
        try:
            value = int(answer)
        except ValueError:
            console.print(f"[red]'{answer}' is not a number. Try again, or type 'cancel'.[/red]")
            continue
        if minimum <= value <= maximum:
            return value
        console.print(f"[red]Please enter a number between {minimum} and {maximum}.[/red]")


def _prompt_choice(title: str, options: list[str], hint: str = "") -> int | None:
    """Show a numbered list and return the chosen index. None = cancelled."""
    while True:
        console.print(f"\n[bold bright_cyan]{title}[/bold bright_cyan]")
        if hint:
            console.print(f"[dim]{hint}[/dim]")
        for i, label in enumerate(options, 1):
            console.print(f"  {i}. {label}")
        console.print("[bold]Enter a number, or 'cancel':[/bold]")
        answer = session.prompt("> ").strip()
        if answer.lower() in ("cancel", "back"):
            return None
        try:
            index = int(answer) - 1
        except ValueError:
            console.print(f"[red]'{answer}' is not a number. Pick 1-{len(options)}.[/red]")
            continue
        if 0 <= index < len(options):
            return index
        console.print(f"[red]Pick a number between 1 and {len(options)}.[/red]")


def _prompt_wallet_name() -> str | None:
    """Ask for an unused, filesystem-safe wallet name."""
    while True:
        name = _prompt_cancelable(
            "Choose a name for this wallet",
            "Letters, numbers and underscores only.",
        )
        if name is None:
            console.print("[yellow]Cancelled.[/yellow]")
            return None
        if not name:
            console.print("[red]The name cannot be empty.[/red]")
            continue
        if not re.match(r'^[a-zA-Z0-9_]+$', name):
            console.print("[red]Use only letters, numbers and underscores "
                          "(no spaces or punctuation).[/red]")
            continue
        if os.path.exists(secure_path_join(WALLET_DIR, name)):
            console.print(f"[red]A wallet called '{name}' already exists. Pick another name.[/red]")
            continue
        return name


def _prompt_new_password(message: str) -> str | None:
    """Ask for a new password twice. None = cancelled."""
    while True:
        console.print(f"[bold]{message}:[/bold]")
        password = session.prompt("> ", is_password=True).strip()
        if password.lower() == "cancel":
            return None
        if not password:
            console.print("[red]The password cannot be empty.[/red]")
            continue
        console.print("[bold]Type it again to confirm:[/bold]")
        if session.prompt("> ", is_password=True).strip() != password:
            console.print("[red]Those did not match. Try again.[/red]")
            continue
        return password


COSIGNER_KEY_HASH_CACHE = "cosigner_key_hashes.json"


def _personal_wallet_entries(current_user: str,
                             user_password: str) -> list[tuple[str, str]]:
    """This user's ordinary (non-multisig) wallets, as (name, directory).

    These are the wallets that can actually sign: a cosigner's credential is the
    everyday wallet they already hold, not a multisig-specific key.
    """
    user_data = load_user_data(current_user, user_password)
    if user_data is None:
        return []
    entries: list[tuple[str, str]] = []
    for w in _json_list(user_data.get("wallets")):
        name = _json_str(_json_object(w).get("name"))
        if not name:
            continue
        wallet_dir = secure_path_join(WALLET_DIR, name)
        # A multisig wallet holds a script, not a signing key, so it can never
        # be the identity that signs.
        if os.path.exists(os.path.join(wallet_dir, "mnemonic.enc")):
            entries.append((name, wallet_dir))
    return entries


def _read_cosigner_key_hash_cache(wallet_dir: str) -> dict[str, bytes]:
    """Read a wallet's cached cosigner key hashes, if it has any.

    Key hashes are public — they are what sits in the script and on chain — so
    caching them in the clear costs nothing and saves the user a password prompt
    every time this program asks "which of my wallets is in this script?".
    """
    cache_path = os.path.join(wallet_dir, COSIGNER_KEY_HASH_CACHE)
    if not os.path.exists(cache_path):
        return {}
    try:
        with open(cache_path) as f:
            raw = _json_object(_json_loads(f.read()))
        return {label: bytes.fromhex(_json_str(h)) for label, h in raw.items()}
    except (OSError, ValueError) as e:
        logging.warning(f"Ignoring unreadable cosigner key hash cache in {wallet_dir}: {e}")
        return {}


def _wallet_cosigner_key_hashes(wallet_dir: str, password: str) -> dict[str, bytes]:
    """Derive (and cache) every key hash a script could name this wallet by."""
    mnemonic = load_encrypted_mnemonic(wallet_dir, password)
    candidates = cosigner_key_hash_candidates(mnemonic)
    try:
        _dump_json({label: kh.hex() for label, kh in candidates.items()},
                   os.path.join(wallet_dir, COSIGNER_KEY_HASH_CACHE))
    except OSError as e:
        # The cache is only a convenience; failing to write it must not stop a
        # signer from signing.
        logging.warning(f"Could not cache cosigner key hashes for {wallet_dir}: {e}")
    return candidates


class CosignerIdentity(TypedDict):
    """A personal wallet that is named as a cosigner in a given script."""
    wallet_name: str
    wallet_dir: str
    key_hash: bytes
    path_label: str
    script_index: int


def _match_cosigner_identities(current_user: str, user_password: str,
                               script_key_hashes: list[bytes],
                               unlock_password: str | None = None) -> list[CosignerIdentity]:
    """Find which of this user's wallets the script names as a cosigner.

    Cached key hashes are checked first, so the common case costs no password at
    all. `unlock_password` extends the search to wallets that have never been
    matched before by decrypting them to derive their hashes.
    """
    wanted = set(script_key_hashes)
    matches: list[CosignerIdentity] = []
    for wallet_name, wallet_dir in _personal_wallet_entries(current_user, user_password):
        candidates = _read_cosigner_key_hash_cache(wallet_dir)
        if not candidates and unlock_password is not None:
            try:
                candidates = _wallet_cosigner_key_hashes(wallet_dir, unlock_password)
            except Exception as e:
                # A wallet with a different password is not an error here; it
                # simply is not one this search can look inside.
                logging.debug(f"Could not derive key hashes for {wallet_name}: {e}")
                continue

        matched = False
        for path_label, kh in candidates.items():
            if kh in wanted:
                matches.append({
                    "wallet_name": wallet_name,
                    "wallet_dir": wallet_dir,
                    "key_hash": kh,
                    "path_label": path_label,
                    "script_index": script_key_hashes.index(kh),
                })
                matched = True

        # The cache only holds the usual position. When a wallet is unlocked and
        # the usual position missed, look further before concluding this wallet
        # is a stranger to the script — a provider may have placed the key at
        # another account or address.
        if not matched and unlock_password is not None:
            try:
                mnemonic = load_encrypted_mnemonic(wallet_dir, unlock_password)
            except Exception:
                continue
            found = find_cosigner_derivation(mnemonic, wanted)
            if found is not None:
                path, kh, _child = found
                matches.append({
                    "wallet_name": wallet_name,
                    "wallet_dir": wallet_dir,
                    "key_hash": kh,
                    "path_label": path,
                    "script_index": script_key_hashes.index(kh),
                })
                # The password just proved the right to know this wallet's
                # keys, and the found position was outside the usual one —
                # remember it so unlocking is a one-time cost.
                _remember_cosigner_key_hash(wallet_dir, path, kh)
    return matches


def _prompt_cosigner_key_hash(index: int,
                              already: list[bytes],
                              current_user: str,
                              user_password: str) -> tuple[bytes, str | None] | None:
    """Collect one cosigner's key hash.

    A key hash is the only cosigner material that may cross machines, so this is
    all the creator ever asks for. The cosigner gets theirs from "Show My
    Cosigner Key" on their own machine.

    Returns (key_hash, label_or_None), or None if cancelled.
    """
    while True:
        console.print(f"\n[bold]Cosigner {index + 1}[/bold]")
        console.print("[dim]Paste their key hash (56-character hex), or type 'me' "
                      "to use one of your own wallets.[/dim]")
        answer = session.prompt("> ").strip()

        if answer.lower() == "cancel":
            return None

        if answer.lower() in ("me", "self"):
            picked = _pick_own_cosigner_key(current_user, user_password)
            if picked is None:
                continue
            key_hash, label = picked
            if key_hash in already:
                console.print(f"[red]That key is already cosigner "
                              f"{already.index(key_hash) + 1}.[/red]")
                continue
            console.print(f"[green]Cosigner {index + 1} is you ({label}).[/green]")
            return key_hash, label

        cleaned = answer.lower().removeprefix("0x")
        try:
            key_hash = bytes.fromhex(cleaned)
        except ValueError:
            console.print("[red]That is not hex. A key hash is 56 hex characters.[/red]")
            continue
        if len(key_hash) != 28:
            console.print(f"[red]A key hash is 28 bytes (56 hex characters); "
                          f"that one is {len(key_hash)}.[/red]")
            continue
        if key_hash in already:
            console.print(f"[red]That key is already cosigner "
                          f"{already.index(key_hash) + 1}.[/red]")
            continue

        name = _prompt_cancelable(
            f"A name for cosigner {index + 1} (optional, press Enter to skip)",
            "Only stored on this machine, so the wallet shows a name instead of a hash.",
        )
        console.print(f"[green]Cosigner {index + 1} accepted.[/green]")
        return key_hash, (name or None)


def _pick_own_cosigner_key(current_user: str,
                           user_password: str) -> tuple[bytes, str] | None:
    """Choose one of this user's wallets and return its cosigner key hash.

    Returns (key_hash, wallet_name), or None if cancelled. This is the
    creation-flow "me" picker: it offers the usual positions a script built
    here would name. Finding a key a provider placed anywhere else is
    `_prompt_wallet_unlock`'s job.
    """
    wallets = _personal_wallet_entries(current_user, user_password)
    if not wallets:
        console.print("[yellow]You have no ordinary wallets yet. Create or import one "
                      "first — that wallet is what signs for you.[/yellow]")
        return None

    choice = _prompt_choice(
        "Which of your wallets should be this cosigner?",
        [name for name, _ in wallets],
        hint="Its key hash is what goes in the script. The wallet itself stays here.",
    )
    if choice is None:
        return None
    wallet_name, wallet_dir = wallets[choice]

    candidates = _read_cosigner_key_hash_cache(wallet_dir)
    if not candidates:
        console.print(f"[bold]Enter the password for '{wallet_name}':[/bold]")
        password = prompt_existing_password()
        try:
            candidates = _wallet_cosigner_key_hashes(wallet_dir, password)
        except Exception as e:
            console.print(f"[red]Could not open that wallet: {e}[/red]")
            return None

    # Both standards are derivable from the same wallet. For a script this
    # program creates, the personal-wallet path is the one its own signing flow
    # will present first, so it is the sensible default.
    labels = list(candidates.keys())
    if len(labels) > 1:
        pick = _prompt_choice(
            "Which key should represent you in this script?",
            [f"{label} — {candidates[label].hex()}" for label in labels],
            hint="Use the personal-wallet key unless another tool requires the shared path.",
        )
        if pick is None:
            return None
        chosen_label = labels[pick]
    else:
        chosen_label = labels[0]

    return candidates[chosen_label], wallet_name


def _remember_cosigner_key_hash(wallet_dir: str, path_label: str,
                                key_hash: bytes) -> None:
    """Add a found key hash to a wallet's public cosigner-key cache.

    Key hashes are public — they are what sits inside scripts and on chain —
    so remembering where one was found costs nothing and saves the user a
    password prompt the next time the same question is asked.
    """
    cache = _read_cosigner_key_hash_cache(wallet_dir)
    if key_hash in cache.values():
        return
    cache[path_label] = key_hash
    try:
        _dump_json({label: kh.hex() for label, kh in cache.items()},
                   os.path.join(wallet_dir, COSIGNER_KEY_HASH_CACHE))
    except OSError as e:
        logging.warning(f"Could not cache cosigner key hash for {wallet_dir}: {e}")


def _prompt_wallet_unlock(current_user: str,
                          user_password: str) -> tuple[str, str, str] | None:
    """Pick one of this user's wallets and unlock it with its password.

    A cosigner search can only look inside a wallet the user can open, and a
    mistyped password must be re-asked rather than reported as "you are not a
    cosigner" — the two look identical to the search, but only one of them is
    true. Returns (wallet name, wallet dir, password), or None if cancelled.
    """
    entries = _personal_wallet_entries(current_user, user_password)
    if not entries:
        console.print("[yellow]You have no ordinary wallets yet. Create or import"
                      " one first — that wallet is what signs for you.[/yellow]")
        return None
    choice = _prompt_choice(
        "Unlock which wallet to search for your cosigner key?",
        [name for name, _ in entries],
        hint="Its whole key tree is searched, wherever the provider put the key.",
    )
    if choice is None:
        return None
    wallet_name, wallet_dir = entries[choice]
    while True:
        console.print(f"[bold]Enter the password for '{wallet_name}':[/bold]")
        password = prompt_existing_password()
        try:
            load_encrypted_mnemonic(wallet_dir, password)
            return wallet_name, wallet_dir, password
        except Exception:
            console.print(f"[red]That password did not open '{wallet_name}'.[/red]")
            console.print("[bold]Try again? (y/n)[/bold]")
            if not _prompt_yes_no(default=True):
                return None


def _prompt_threshold_percentage(num_cosigners: int) -> int | None:
    """Ask how many cosigners must approve, showing the resulting m-of-n."""
    presets = [50, 67, 75, 100]
    labels = [
        f"{pct}% — {calculate_threshold(num_cosigners, pct)} of {num_cosigners} must approve"
        for pct in presets
    ]
    labels.append("Choose a different percentage")
    choice = _prompt_choice(
        "How many cosigners must approve a spend?",
        labels,
        hint="100% means every cosigner must sign. Lower values allow spending "
             "when some cosigners are unavailable.",
    )
    if choice is None:
        console.print("[yellow]Cancelled.[/yellow]")
        return None
    if choice < len(presets):
        return presets[choice]

    while True:
        percentage = _prompt_int("Enter a percentage", minimum=1, maximum=100)
        if percentage is None:
            return None
        required = calculate_threshold(num_cosigners, percentage)
        console.print(f"[dim]{percentage}% of {num_cosigners} cosigners "
                      f"= {required} signature(s) required.[/dim]")
        console.print("[bold]Use this? (y/n)[/bold]")
        if _prompt_yes_no(default=True):
            return percentage


def _prompt_future_datetime(message: str) -> datetime | None:
    """Ask for a moment in the future, in UTC. None = cancelled."""
    while True:
        answer = _prompt_cancelable(
            message,
            "Format: YYYY-MM-DD, or YYYY-MM-DD HH:MM for a time of day. UTC.",
        )
        if answer is None:
            return None
        parsed: datetime | None = None
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(answer, fmt).replace(tzinfo=UTC)
                break
            except ValueError:
                continue
        if parsed is None:
            console.print("[red]Could not read that as a date. Example: 2027-01-31[/red]")
            continue
        if parsed <= datetime.now(UTC):
            console.print("[red]That is in the past. Pick a future date.[/red]")
            continue
        return parsed


def _prompt_timelock() -> tuple[int | None, int | None] | None:
    """Ask whether the wallet should be time-locked, and to what slots.

    Returns (not_before_slot, not_after_slot); either may be None. Returns None
    if the user cancelled.
    """
    choice = _prompt_choice(
        "Should this wallet be locked to a time window?",
        [
            "No time lock (the usual choice)",
            "Funds cannot be spent until a date (vesting, escrow)",
            "Funds must be spent before a date",
            "Both — a window with a start and an end",
        ],
        hint="A time lock is part of the script, so it is fixed forever at "
             "creation and the blockchain itself enforces it.",
    )
    if choice is None:
        return None
    if choice == 0:
        return None, None

    not_before_slot: int | None = None
    not_after_slot: int | None = None

    if choice in (1, 3):
        when = _prompt_future_datetime("From when may the funds be spent?")
        if when is None:
            return None
        not_before_slot = slot_for_datetime(when)
        console.print(f"[dim]Unlocks at slot {not_before_slot} "
                      f"({when.strftime('%Y-%m-%d %H:%M')} UTC).[/dim]")

    if choice in (2, 3):
        console.print("\n[bold yellow]Read this before choosing an end date.[/bold yellow]")
        console.print("[yellow]After that moment the script can never be satisfied "
                      "again.[/yellow]")
        console.print("[yellow]Anything still in the wallet is unspendable by anyone, "
                      "permanently —[/yellow]")
        console.print("[yellow]no signature, password or recovery undoes it.[/yellow]")
        console.print("[bold]Continue? (y/n)[/bold]")
        if not _prompt_yes_no(default=False):
            return None
        when = _prompt_future_datetime("By when must the funds be spent?")
        if when is None:
            return None
        not_after_slot = slot_for_datetime(when)
        console.print(f"[dim]Expires at slot {not_after_slot} "
                      f"({when.strftime('%Y-%m-%d %H:%M')} UTC).[/dim]")

    return not_before_slot, not_after_slot


def _write_multisig_wallet(wallet_dir: str, script: NativeScript,
                           config: dict[str, JSON], network: str,
                           staking: bool = False) -> tuple[bytes, str, str]:
    """Write a multisig wallet directory from its script. One layout, one writer.

    Creation, `.cbor` recovery and package restore all land here, so a wallet is
    byte-for-byte the same on disk however it arrived. Returns
    (script_hash, script_address, script_cbor_hex).

    `staking` puts the same script hash in the address's stake part as well as
    its payment part, which is what makes the wallet's stake delegable: the
    cosigners who authorise a spend are then exactly the ones who authorise a
    delegation. It changes the address, so it is decided once, at creation.

    The directory is removed again if any part of the write fails: a half-written
    wallet that has a script but no address is worse than no wallet, because it
    looks restorable.
    """
    script_cbor_hex = script.to_cbor().hex()
    script_hash_bytes = script_hash_from_script(script)
    script_address = script_to_address(script_hash_bytes,
                                       script_hash_bytes if staking else None)

    try:
        os.makedirs(wallet_dir, exist_ok=True)
        with open(os.path.join(wallet_dir, "type"), "w") as f:
            f.write("multisig")
        with open(os.path.join(wallet_dir, "network.txt"), "w") as f:
            f.write(network)
        _dump_json(config, os.path.join(wallet_dir, "config.json"))
        script_json: JSON = script.to_dict()
        _dump_json(script_json, os.path.join(wallet_dir, "script.json"))
        with open(os.path.join(wallet_dir, "script.cbor"), "w") as f:
            f.write(script_cbor_hex)
        with open(os.path.join(wallet_dir, "script_hash"), "w") as f:
            f.write(script_hash_bytes.hex())
        with open(os.path.join(wallet_dir, "script_address"), "w") as f:
            f.write(script_address)
        os.makedirs(os.path.join(wallet_dir, "known_indices"), exist_ok=True)
        os.makedirs(os.path.join(wallet_dir, "sessions"), exist_ok=True)
    except Exception:
        shutil.rmtree(wallet_dir, ignore_errors=True)
        raise

    return script_hash_bytes, script_address, script_cbor_hex


@exception_error
def create_multisig_wallet(wallet_name: str, key_hashes: list[bytes],
                           percentage: int,
                           network: str | None = None,
                           labels: dict[bytes, str] | None = None,
                           our_key_hash: bytes | None = None,
                           admin_cosigner_index: int | None = None,
                           staking: bool = False,
                           not_before_slot: int | None = None,
                           not_after_slot: int | None = None) -> dict[str, JSON]:
    """Create a multisig wallet on disk from the cosigners' key hashes.

    This is the whole of wallet creation: the interactive flow only gathers
    these arguments. Keeping the logic callable is what lets the same code path
    be driven by the menus and exercised directly against a real network.

    No cosigner private material is involved, and none is stored. A cosigner's
    ability to sign comes from the personal wallet they already have, matched to
    the script by key hash at signing time — so this function never needs, and
    never keeps, a second copy of anyone's mnemonic.

    Args:
        wallet_name: Directory-safe wallet name.
        key_hashes: The cosigners' payment key hashes, in script order.
        percentage: Share of cosigners that must approve; converted to an m-of-n threshold.
        network: Network name; defaults to the selected network.
        labels: Optional human names per key hash; local convenience, never shared.
        our_key_hash: This node's own key hash, when it is one of the cosigners.
        admin_cosigner_index: When set, only this cosigner may initiate spends.
        staking: Give the wallet a delegable stake credential (see
            `_write_multisig_wallet`). Changes the address, so it is fixed here.
        not_before_slot: Funds cannot be spent until this slot.
        not_after_slot: Funds cannot be spent from this slot on.

    Returns a dict describing the created wallet.
    """
    logging.debug(f"[ENTRY] create_multisig_wallet(name={wallet_name})")

    if not re.match(r'^[a-zA-Z0-9_]+$', wallet_name or ""):
        raise ValueError("Wallet name must contain only letters, numbers, and underscores.")

    wallet_dir = secure_path_join(WALLET_DIR, wallet_name)
    if os.path.exists(wallet_dir):
        raise ValueError(f"Wallet '{wallet_name}' already exists.")

    num_cosigners = len(key_hashes)
    if num_cosigners < 2:
        raise ValueError("A multisig wallet needs at least two cosigners.")

    network = network or SELECTED_NETWORK or "preprod"
    required_signatures = calculate_threshold(num_cosigners, percentage)

    script = build_native_script_from_key_hashes(
        required_signatures, key_hashes,
        not_before_slot=not_before_slot, not_after_slot=not_after_slot,
    )
    timelocked = not_before_slot is not None or not_after_slot is not None

    config: dict[str, JSON] = {
        "name": wallet_name,
        "network": network,
        "threshold": required_signatures,
        "num_cosigners": num_cosigners,
        "percentage": percentage,
        "key_hashes": [kh.hex() for kh in key_hashes],
        "labels": {kh.hex(): name for kh, name in (labels or {}).items()},
        "script_type": "all" if timelocked else "atLeast",
        "staking": staking,
        "provenance": "created",
        "claimed_signer_index": (key_hashes.index(our_key_hash)
                                 if our_key_hash in key_hashes else None),
    }
    # Absent means "any cosigner may initiate" — the default. A null value would
    # be indistinguishable from an admin whose index happens to be unknown, so
    # the key is only written when an admin was actually designated.
    if admin_cosigner_index is not None:
        config["admin_cosigner_index"] = admin_cosigner_index

    script_hash_bytes, script_address, script_cbor_hex = _write_multisig_wallet(
        wallet_dir, script, config, network, staking=staking
    )

    logging.debug("[EXIT] create_multisig_wallet")
    return {
        "wallet_dir": wallet_dir,
        "wallet_name": wallet_name,
        "script_address": script_address,
        "script_hash": script_hash_bytes.hex(),
        "script_cbor_hex": script_cbor_hex,
        "threshold": required_signatures,
        "num_cosigners": num_cosigners,
        "staking": staking,
        "config": config,
    }


@exception_error
def multisig_wallet_create(current_user: str, user_password: str) -> None:
    """Interactive flow for creating a multisig wallet."""
    logging.debug(f"[ENTRY] multisig_wallet_create(user={current_user})")
    try:
        user_data = load_user_data(current_user, user_password)
    except Exception as e:
        console.print(f"[red]Failed to load user data: {e}[/red]")
        return
    if user_data is None:
        return

    console.print("\n[bold bright_cyan]Create a Multisig Wallet[/bold bright_cyan]")
    console.print("[dim]You need each cosigner's key hash \u2014 a 56-character hex string.[/dim]")
    console.print("[dim]They get theirs from: Multisig Wallets \u2192 Show My Cosigner Key,[/dim]")
    console.print("[dim]and can send it over any channel: it is public and cannot spend.[/dim]")
    console.print("[dim]Type 'cancel' at any prompt to abort.[/dim]\n")

    wallet_name = _prompt_wallet_name()
    if wallet_name is None:
        return

    num_cosigners = _prompt_int(
        "How many cosigners will this wallet have?", minimum=2, maximum=10
    )
    if num_cosigners is None:
        return

    key_hashes: list[bytes] = []
    labels: dict[bytes, str] = {}
    our_key_hash: bytes | None = None
    self_cosigner_index = None
    for i in range(num_cosigners):
        result = _prompt_cosigner_key_hash(i, key_hashes, current_user, user_password)
        if result is None:
            console.print("[yellow]Cancelled.[/yellow]")
            return
        key_hash, label = result
        if label:
            labels[key_hash] = label
        # "me" resolves to a wallet on this machine, which is also what tells
        # the wallet which cosigner this node is.
        if label and any(label == name for name, _ in
                         _personal_wallet_entries(current_user, user_password)):
            our_key_hash = key_hash
            self_cosigner_index = i
        key_hashes.append(key_hash)

    percentage = _prompt_threshold_percentage(num_cosigners)
    if percentage is None:
        return

    required = calculate_threshold(num_cosigners, percentage)
    console.print(
        f"\n[green]Threshold: {required} of {num_cosigners} cosigners "
        f"must approve every spend ({percentage}%).[/green]"
    )

    console.print("\n[bold]Let this wallet delegate its stake and earn rewards? "
                  "(y/n)[/bold]")
    console.print("[dim]Adds a stake credential to the address, controlled by the same "
                  "cosigners.[/dim]")
    console.print("[dim]It changes the address, so it cannot be added later. 'y' is "
                  "the usual choice.[/dim]")
    staking = _prompt_yes_no(default=True)

    timelock = _prompt_timelock()
    if timelock is None:
        console.print("[yellow]Cancelled.[/yellow]")
        return
    not_before_slot, not_after_slot = timelock

    admin_cosigner_index = None
    if self_cosigner_index is not None:
        console.print("\n[bold]Designate yourself as the wallet administrator? (y/n)[/bold]")
        console.print("[dim]Administrator means only you can start a spend \u2014 build "
                      "a[/dim]")
        console.print("[dim]transaction, assemble signatures, and submit. Other "
                      "cosigners can[/dim]")
        console.print("[dim]still sign. This is a rule this program enforces, not "
                      "the blockchain:[/dim]")
        console.print(f"[dim]spending still needs {required} of {num_cosigners} signatures "
                      "either way.[/dim]")
        console.print("[dim]Answer 'n' (the usual choice) to let any cosigner start "
                      "a spend.[/dim]")
        if _prompt_yes_no(default=False):
            admin_cosigner_index = self_cosigner_index

    try:
        created = create_multisig_wallet(
            wallet_name=wallet_name,
            key_hashes=key_hashes,
            percentage=percentage,
            labels=labels,
            our_key_hash=our_key_hash,
            admin_cosigner_index=admin_cosigner_index,
            staking=staking,
            not_before_slot=not_before_slot,
            not_after_slot=not_after_slot,
        )
    except Exception as e:
        console.print(f"[red]Could not create the wallet: {e}[/red]")
        return
    if created is None:
        return

    script_address = _json_str(created["script_address"])
    threshold = _json_int(created["threshold"])
    num_created = _json_int(created["num_cosigners"])
    console.print(f"\n[bold green]Multisig wallet '{wallet_name}' created.[/bold green]")
    console.print(f"  Address:   [cyan]{script_address}[/cyan]")
    console.print(f"  Threshold: {threshold} of {num_created}")
    console.print("  Staking:   "
                  + ("yes \u2014 this wallet can delegate" if staking else "no"))
    if not_before_slot is not None:
        unlocks = datetime_for_slot(not_before_slot).strftime("%Y-%m-%d %H:%M")
        console.print(f"  Unlocks:   {unlocks} UTC (slot {not_before_slot})")
    if not_after_slot is not None:
        expires = datetime_for_slot(not_after_slot).strftime("%Y-%m-%d %H:%M")
        console.print(f"  Expires:   {expires} UTC (slot {not_after_slot}) \u2014 spend "
                      "before this or the funds are locked forever")
    if admin_cosigner_index is not None:
        console.print(f"  Admin:     cosigner {admin_cosigner_index + 1} (you)")
    else:
        console.print("  Admin:     not designated \u2014 any cosigner can start a spend")
    if self_cosigner_index is not None:
        console.print(f"  You are:   cosigner {self_cosigner_index + 1}, signing with "
                      f"your '{labels.get(our_key_hash or b'', '')}' wallet")

    console.print("\n[bold]Next steps:[/bold]")
    console.print("  1. Send funds to the address above to use the wallet.")
    console.print("  2. Export a checkpoint (View Multisig Wallets \u2192 Export checkpoint)")
    console.print("     and keep it safe \u2014 it restores this wallet on any machine.")
    console.print("  3. Give the other cosigners the checkpoint so they can restore it too.")

    wallets = _json_list(user_data.get("wallets"))
    if wallet_name not in [_wallet_entry_name(w) for w in wallets]:
        wallets.append({
            "name": wallet_name,
            "address": script_address,
            "type": "multisig",
            "threshold": f"{threshold} of {num_created}",
        })
        user_data["wallets"] = wallets
        save_user_data(current_user, user_data, user_password)

    logging.debug(f"[EXIT] multisig_wallet_create(user={current_user})")


class MultisigWalletInfo(TypedDict):
    """In-memory view of a multisig wallet directory (spec §Wallet Identity).

    `config` is the raw config.json contents; everything else is read back from
    `script.cbor`, which is the wallet's immutable identity. There is one shape
    here regardless of how the script arrived — created, recovered from a raw
    `.cbor`, or restored from a package — because how it arrived changes nothing
    about how it is spent.
    """
    config: dict[str, JSON]
    script: NativeScript
    script_hash: bytes
    script_address: str
    key_hashes: list[bytes]
    threshold: int
    labels: dict[bytes, str]
    provenance: str
    staking: bool
    not_before_slot: int | None
    not_after_slot: int | None


def _multisig_labels(config: dict[str, JSON], key_hashes: list[bytes]) -> dict[bytes, str]:
    """Human names for cosigners, keyed by key hash.

    `config.labels` is local convenience and is never shared, so a node labels
    only the cosigners it happens to know. Anyone unlabelled falls back to their
    position in the script, which is stable because the script's order is.
    """
    raw = _json_object(config.get("labels"))
    labels: dict[bytes, str] = {}
    for hex_hash, name in raw.items():
        try:
            labels[bytes.fromhex(hex_hash)] = _json_str(name)
        except ValueError:
            logging.warning(f"Ignoring unreadable label key: {hex_hash!r}")
    for i, kh in enumerate(key_hashes):
        # Counted from 1, the way the rest of the program talks about cosigners.
        labels.setdefault(kh, f"cosigner {i + 1}")
    return labels


def _load_multisig_wallet(wallet_dir: str) -> MultisigWalletInfo:
    """Load a multisig wallet from its script.

    The script is the authority. Threshold and cosigner key hashes are read out
    of it rather than trusted from config.json, so a config that has been edited
    — by hand, by a bad package, or by an attacker with file access — cannot
    change who this program believes may sign. config.json supplies only policy
    that the script cannot carry: names, provenance, and the signing window.
    """
    config_path = os.path.join(wallet_dir, "config.json")
    with open(config_path) as f:
        config = _json_object(_json_loads(f.read()))

    cbor_path = os.path.join(wallet_dir, "script.cbor")
    with open(cbor_path) as f:
        script: NativeScript = NativeScript.from_cbor(f.read().strip())

    script_hash_bytes = script_hash_from_script(script)

    # The address written at import/creation is the wallet's own, and it encodes
    # the network the wallet belongs to. Re-deriving it here would stamp it with
    # whichever network is selected right now, which for a wallet from another
    # network is a different address that no funds sit at — the exact way a
    # wrong-network mistake turns into lost funds. The stored address wins.
    addr_path = os.path.join(wallet_dir, "script_address")
    if os.path.exists(addr_path):
        with open(addr_path) as f:
            script_address = f.read().strip()
    else:
        script_address = script_to_address(script_hash_bytes)

    key_hashes, _script_threshold, _script_type = extract_key_hashes_from_script(script)
    # Read the requirement from the script's shape, not from a single field:
    # `all` needs everyone, `any` needs one, `atLeast` needs its n, and a
    # timelock needs nobody.
    threshold = script_min_signatures(script)

    # Whether the wallet can delegate is a property of its address, not of
    # config.json: the stake credential either is in the address or it is not,
    # and only the address decides where funds actually sit.
    stored_address: Address = Address.from_primitive(script_address)
    staking = isinstance(stored_address.staking_part, ScriptHash)
    not_before_slot, not_after_slot = script_timelocks(script)

    return {
        "config": config,
        "script": script,
        "script_hash": script_hash_bytes,
        "script_address": script_address,
        "key_hashes": key_hashes,
        "threshold": threshold,
        "labels": _multisig_labels(config, key_hashes),
        "provenance": _json_str(config.get("provenance") or "created"),
        "staking": staking,
        "not_before_slot": not_before_slot,
        "not_after_slot": not_after_slot,
    }


def _apply_validity_window(builder: TransactionBuilder,
                           wallet: MultisigWalletInfo) -> int:
    """Set the transaction's validity interval, honouring the script's timelocks.

    Two things decide the window. PyCardano's default expires about three hours
    after building, which is far too short here — cosigners are people, and
    collecting signatures can take days, after which every signature already
    gathered is worthless — so it is widened to the configured number of days.

    Then the script's own `after`/`before` bounds are applied. A transaction
    whose interval falls outside them is rejected by the ledger with nothing a
    user could act on, so the impossible cases are named here instead: a wallet
    that has not unlocked yet, and one whose spending window has closed.

    Returns the number of days the signing window spans.
    """
    if not context:
        raise ValueError("No backend configured.")

    last_slot = context.last_block_slot
    valid_days = _json_int(wallet["config"].get("signing_window_days"),
                           DEFAULT_SIGNING_WINDOW_DAYS)
    # Starting slightly behind the tip absorbs the slot drift between building
    # and submitting.
    start = max(0, last_slot - 600)
    ttl = last_slot + valid_days * 24 * 60 * 60

    not_before = wallet["not_before_slot"]
    not_after = wallet["not_after_slot"]

    if not_before is not None:
        if not_before > last_slot:
            unlocks = datetime_for_slot(not_before).strftime("%Y-%m-%d %H:%M")
            raise ValueError(
                f"This wallet is time-locked until slot {not_before} "
                f"(about {unlocks} UTC). Nothing can be spent from it before then."
            )
        start = max(start, not_before)

    if not_after is not None:
        if not_after <= last_slot:
            expired = datetime_for_slot(not_after).strftime("%Y-%m-%d %H:%M")
            raise ValueError(
                f"This wallet's spending window closed at slot {not_after} "
                f"(about {expired} UTC). The script can no longer be satisfied, "
                f"so its funds cannot be moved by anyone."
            )
        ttl = min(ttl, not_after)

    if start >= ttl:
        raise ValueError(
            f"The script's time bounds leave no usable validity interval "
            f"(start slot {start}, expiry slot {ttl})."
        )

    builder.validity_start = start
    builder.ttl = ttl
    return valid_days


@exception_error
def build_multisig_transaction(wallet_dir: str, recipient: str, amount_lovelace: int,
                               assets: list[tuple[bytes, bytes, int]] | None = None,
                               sweep: bool = False) -> dict[str, JSON] | None:
    """Build an unsigned multisig transaction and export as CBOR for cosigner signing.

    A wallet has exactly one script and therefore exactly one address: the
    script hash is the payment credential, and nothing about it varies per
    address index. Change returns to that same address.

    `assets` names (policy, name, quantity) bundles to send alongside the ADA;
    `sweep` empties the wallet — every asset and all ADA minus the fee goes to
    the recipient and no change comes back.

    Returns dict with unsigned_tx_cbor_hex, text_envelope, session_dir.
    """
    logging.debug(f"[ENTRY] build_multisig_transaction(wallet_dir={wallet_dir})")

    if not context:
        raise ValueError("No backend configured.")

    wallet = _load_multisig_wallet(wallet_dir)
    config = wallet["config"]
    script = wallet["script"]
    script_address = wallet["script_address"]
    # The wallet is its script. There is one script, so there is one address to
    # spend from, whether the script was built here or recovered from a `.cbor`.
    script_for_index = script
    script_addr_for_index = script_address
    script_key_hashes = wallet["key_hashes"]
    threshold = wallet["threshold"]

    utxos = context.utxos(script_addr_for_index)
    if not utxos:
        raise ValueError(f"No UTxOs found at multisig address {script_addr_for_index}.")

    console.print(f"[bold]Found {len(utxos)} UTxO(s) at the script address:[/bold]")
    total_available = 0
    total_value = Value(coin=0)
    for i, utxo in enumerate(utxos):
        lovelace = utxo.output.amount.coin
        total_available += lovelace
        total_value += utxo.output.amount
        tx_hash = utxo.input.transaction_id.payload.hex()[:16]
        console.print(f"  {i+1}. {format_ada(lovelace)} ADA (tx:{tx_hash}...#{utxo.input.index})")
    console.print(f"  Total: {format_ada(total_available)} ADA")

    if not sweep and amount_lovelace > total_available:
        raise ValueError(f"Requested {format_ada(amount_lovelace)} ADA but only"
                         f" {format_ada(total_available)} available.")

    recipient_address: Address = Address.from_primitive(recipient)
    token_multi_asset: MultiAsset | None = None
    if sweep:
        token_multi_asset = total_value.multi_asset or None
    elif assets:
        primitive: dict[str, dict[str, int]] = {}
        for policy, name, quantity in assets:
            primitive.setdefault(policy.hex(), {})[name.hex()] = quantity
        token_multi_asset = _multi_asset_from_primitive(primitive)
    if not sweep and token_multi_asset is not None:
        _check_min_ada_for_tokens(recipient_address, amount_lovelace, token_multi_asset)

    script_addr_obj: Address = Address.from_primitive(script_addr_for_index)
    chain = context

    def configured_builder(coin: int) -> TransactionBuilder:
        """The spend's builder at a given payment amount, built exactly as the
        real one will be, so a probe build reports the fee the real build pays."""
        spend_builder = TransactionBuilder(chain)
        for utxo in utxos:
            spend_builder.add_script_input(utxo, script=script_for_index)
        payment = (Value(coin=coin, multi_asset=token_multi_asset)
                   if token_multi_asset is not None else Value(coin=coin))
        spend_builder.add_output(TransactionOutput(recipient_address, payment))
        # The transaction body's `required_signers` field is a ledger-enforced
        # demand that EVERY listed key signs. Listing all cosigners there would
        # turn an M-of-N wallet into N-of-N and make the threshold unusable, so
        # it is deliberately left unset: the native script itself already tells
        # the ledger how many of which keys are required.
        #
        # Fee estimation, however, must account for the witnesses that will be
        # attached later. PyCardano only counts vkey witnesses it can infer,
        # and it does not descend into `atLeast` scripts, so a multisig spend
        # would be estimated with zero signatures and rejected as underpaid.
        # Overriding the witness count with the full cosigner set covers any
        # assembly outcome.
        spend_builder.witness_override = max(1, len(script_key_hashes))
        return spend_builder

    # A sweep leaves nothing behind: the one output carries every asset plus
    # the minimum ADA a bundle-carrying output needs, and the change address
    # is the recipient — the remainder (all ADA minus the fee) merges into
    # that same output, so no dust ever returns to the wallet. The fee
    # pre-flight below is about funding a change output at the script
    # address, which a sweep never creates; it is skipped entirely.
    tx_body: TransactionBody
    if sweep:
        sweep_coin = (min_lovelace_post_alonzo(TransactionOutput(
            recipient_address, Value(coin=0, multi_asset=token_multi_asset)), chain)
            if token_multi_asset is not None else 1_000_000)
        sweep_builder = configured_builder(sweep_coin)
        valid_days = _apply_validity_window(sweep_builder, wallet)
        try:
            tx_body = sweep_builder.build(change_address=recipient_address)
        except (UTxOSelectionException, TransactionBuilderException) as e:
            raise ValueError(
                "This wallet cannot be swept: its balance is too small to cover "
                "the transaction fee and the minimum ADA the recipient's output "
                "must carry."
            ) from e
        amount_lovelace = total_available - tx_body.fee
    else:
        # Fee pre-flight. Every input is pre-selected and the additional UTxO
        # pool is empty, so when the requested amount leaves less than the fee
        # plus the minimum ADA for the change output, PyCardano's coin
        # selectors are handed an impossible request against an empty pool and
        # the user gets a raw "All UTxO selectors failed" traceback (observed
        # on mainnet 2026-08-21: a near-max send left 0.2 ADA for a
        # token-carrying change that needs ~2). A minimum-size probe build
        # reports the real fee, and the balance check below refuses unfundable
        # amounts while naming the maximum that funds.
        if token_multi_asset is not None:
            probe_coin = min_lovelace_post_alonzo(
                TransactionOutput(
                    recipient_address,
                    Value(coin=amount_lovelace, multi_asset=token_multi_asset)),
                chain)
        else:
            probe_coin = min(amount_lovelace, 1_000_000)
        probe = configured_builder(probe_coin)
        _apply_validity_window(probe, wallet)
        try:
            probe_body: TransactionBody = probe.build(change_address=script_addr_obj)
        except (UTxOSelectionException, TransactionBuilderException) as e:
            raise ValueError(
                "This wallet cannot fund the spend: its balance is too small to "
                "cover the transaction fee and the minimum ADA a change output "
                "must carry."
            ) from e
        fee_estimate: int = probe_body.fee

        # The change output returns every token not being sent, and an output
        # that carries tokens must stay above its own minimum ADA — a cost of
        # the spend the same way the fee is. A change of exactly zero cannot
        # be built either (PyCardano refuses any change below the minimum), so
        # the minimum applies whenever anything is left over, tokens or not.
        change_assets = total_value.multi_asset
        if token_multi_asset is not None:
            remainder = total_value - Value(multi_asset=token_multi_asset)
            kept: dict[str, dict[str, int]] = {}
            for policy, policy_assets in _multi_asset_items(remainder.multi_asset):
                for name, quantity in policy_assets:
                    if quantity > 0:
                        kept.setdefault(policy.hex(), {})[name.hex()] = quantity
            change_assets = _multi_asset_from_primitive(kept)
        minimum_change = min_lovelace_post_alonzo(
            TransactionOutput(script_addr_obj,
                              Value(coin=total_available, multi_asset=change_assets)),
            chain)
        max_sendable = total_available - fee_estimate - 100_000 - minimum_change
        if max_sendable <= 0:
            raise ValueError(
                f"This wallet holds {format_ada(total_available)} ADA, but the fee "
                f"(about {format_ada(fee_estimate)}) plus the minimum ADA for the "
                f"change output ({format_ada(minimum_change)}) already exceeds "
                f"that. Nothing can be sent from it as it stands."
            )
        if amount_lovelace > max_sendable:
            token_note = (" The change output has to return the wallet's tokens, "
                          "and a token-carrying output must stay above its "
                          "minimum ADA." if change_assets else "")
            raise ValueError(
                f"Requested {format_ada(amount_lovelace)} ADA, but this wallet "
                f"cannot send that much in one transaction: the fee is about "
                f"{format_ada(fee_estimate)} ADA and the change output must keep "
                f"at least {format_ada(minimum_change)} ADA.{token_note} The most "
                f"it can send is {format_ada(max_sendable)} ADA."
            )

        builder = configured_builder(amount_lovelace)
        valid_days = _apply_validity_window(builder, wallet)
        # builder.build is wrapped in pycardano's untyped @log_state decorator,
        # so its declared TransactionBody return needs restating here.
        try:
            tx_body = builder.build(change_address=script_addr_obj)
        except UTxOSelectionException as e:
            # The pre-flight above should make this unreachable; it is kept so
            # a fee estimate that drifted still yields the actionable message.
            raise ValueError(
                f"The balance could not cover the fee and the change output "
                f"after all (fee about {format_ada(fee_estimate)} ADA, change "
                f"minimum {format_ada(minimum_change)} ADA). Try at most "
                f"{format_ada(max_sendable)} ADA."
            ) from e

    witness_set = TransactionWitnessSet(native_scripts=[script_for_index])
    unsigned_tx = Transaction(tx_body, witness_set)
    unsigned_cbor_hex = unsigned_tx.to_cbor().hex()

    envelope: dict[str, JSON] = {
        "type": "Unwitnessed Tx BabbageEra",
        "description": "CardanoInterface Multisig Unsigned Transaction",
        "cborHex": unsigned_cbor_hex,
    }
    text_envelope = _dumps_json(envelope)

    session_id = hashlib.sha256(tx_body.hash()).hexdigest()[:12]
    session_dir = os.path.join(wallet_dir, "sessions", session_id)
    os.makedirs(session_dir, exist_ok=True)

    with open(os.path.join(session_dir, "unsigned.cbor"), "w") as f:
        f.write(unsigned_cbor_hex)
    with open(os.path.join(session_dir, "unsigned_text_envelope.json"), "w") as f:
        f.write(text_envelope)
    sent_assets: list[JSON] = [
        {"policy": policy.hex(), "name": name.hex(), "quantity": quantity}
        for policy, name, quantity in (assets or [])]
    if sweep:
        sent_assets = [{"policy": policy.hex(), "name": name.hex(),
                        "quantity": quantity, "sweep": True}
                       for policy, policy_assets in _multi_asset_items(
                           total_value.multi_asset)
                       for name, quantity in policy_assets]
    summary: dict[str, JSON] = {
        "wallet_name": config.get("name"),
        "network": config.get("network", SELECTED_NETWORK or "preprod"),
        "recipient": recipient,
        "amount_lovelace": amount_lovelace,
        "sweep": sweep,
        "assets": sent_assets,
        "fee": tx_body.fee,
        "inputs": [{"tx_hash": inp.transaction_id.payload.hex(), "index": inp.index}
                   for inp in _tx_body_inputs(tx_body)],
        "script_key_hashes": [kh.hex() for kh in script_key_hashes],
        "threshold": threshold,
        "valid_until_slot": tx_body.ttl,
        "signing_window_days": valid_days,
        "session_id": session_id,
    }
    _dump_json(summary, os.path.join(session_dir, "summary.json"))

    _write_session_status(session_dir, set(), threshold)

    console.print("\n[bold green]Unsigned transaction built.[/bold green]")
    console.print(f"  Session:   [cyan]{session_id}[/cyan]")
    if sweep:
        console.print("  Sending:   THE ENTIRE WALLET — "
                      f"{format_ada(amount_lovelace)} ADA + "
                      f"{len(sent_assets)} asset type(s)")
    elif sent_assets:
        console.print(f"  Sending:   {format_ada(amount_lovelace)} ADA + "
                      f"{len(sent_assets)} asset type(s)")
    else:
        console.print(f"  Sending:   {format_ada(amount_lovelace)} ADA")
    console.print(f"  To:        {recipient}")
    console.print(f"  Fee:       {format_ada(tx_body.fee)} ADA")
    console.print(f"  Signatures needed: {threshold} of {len(script_key_hashes)}")
    console.print(f"  Must be submitted within {valid_days} days, or it expires "
                  f"and has to be rebuilt.")
    console.print(f"\n  Unsigned CBOR: [cyan]{os.path.join(session_dir, 'unsigned.cbor')}[/cyan]")
    console.print("\n[bold]Next steps:[/bold]")
    console.print("  1. Send the unsigned CBOR to each cosigner.")
    console.print("  2. They sign it (Multisig Wallets → Sign Transaction) and send back "
        "a partial.")
    console.print("  3. You add each partial (Assemble & Submit → Add a signature).")
    console.print(f"  4. Once {threshold} signatures are in, submit.")

    logging.debug("[EXIT] build_multisig_transaction")
    return {
        "unsigned_cbor_hex": unsigned_cbor_hex,
        "text_envelope": text_envelope,
        "session_dir": session_dir,
        "session_id": session_id,
    }


_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _bech32_payload(text: str) -> bytes | None:
    """Decode a bech32 string to its payload bytes, or None if it is not valid.

    Written out here rather than borrowed from pycardano's internals because
    that module is untyped, and a silently-wrong pool id is a delegation sent
    into nowhere. The checksum is verified, so a mistyped character is caught
    instead of being decoded into a different pool.
    """
    if text != text.lower() and text != text.upper():
        return None
    text = text.lower()
    position = text.rfind("1")
    if position < 1 or position + 7 > len(text) or len(text) > 108:
        return None
    try:
        data = [_BECH32_CHARSET.index(c) for c in text[position + 1:]]
    except ValueError:
        return None

    # BIP-173 polymod over the human-readable part and the data part.
    def polymod(values: list[int]) -> int:
        generator = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
        checksum = 1
        for value in values:
            top = checksum >> 25
            checksum = (checksum & 0x1FFFFFF) << 5 ^ value
            for i in range(5):
                checksum ^= generator[i] if ((top >> i) & 1) else 0
        return checksum

    hrp = text[:position]
    expanded = [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]
    if polymod(expanded + data) != 1:
        return None

    # Regroup the 5-bit data words into bytes, dropping the 6-word checksum.
    accumulator = 0
    bits = 0
    out = bytearray()
    for value in data[:-6]:
        accumulator = (accumulator << 5) | value
        bits += 5
        while bits >= 8:
            bits -= 8
            out.append((accumulator >> bits) & 0xFF)
    if bits >= 5 or (accumulator << (8 - bits)) & 0xFF:
        return None
    return bytes(out)


def pool_id_to_key_hash(pool_id: str) -> bytes:
    """Turn a stake pool's public id into the 28-byte hash a certificate needs.

    Accepts the bech32 `pool1…` form people copy from explorers, and the raw
    hex form tooling prints. Anything else is refused rather than guessed at: a
    delegation to a mistyped pool is a wasted transaction and a wasted fee.
    """
    cleaned = pool_id.strip().lower()
    if cleaned.startswith("pool1"):
        decoded = _bech32_payload(cleaned)
        if decoded is None:
            raise ValueError(f"'{pool_id}' is not a valid bech32 pool id "
                             "(its checksum does not match).")
        data = decoded
    else:
        try:
            data = bytes.fromhex(cleaned.removeprefix("0x"))
        except ValueError:
            raise ValueError(
                f"'{pool_id}' is neither a bech32 pool id (pool1…) nor hex."
            ) from None
    if len(data) != 28:
        raise ValueError(
            f"A pool id is 28 bytes; that one is {len(data)}."
        )
    return data


@exception_error
def build_multisig_stake_transaction(wallet_dir: str, pool_id: str | None = None,
                                     register: bool = False,
                                     deregister: bool = False
                                     ) -> dict[str, JSON] | None:
    """Build an unsigned stake certificate transaction for a multisig wallet.

    Registration, delegation and deregistration all travel as certificates on an
    ordinary transaction, so this produces the same kind of unsigned CBOR and the
    same signing session as a spend. Every cosigner signs it exactly as they sign
    a payment, and assembly and submission are unchanged — a delegation is a
    threshold decision like any other.

    The wallet's stake credential is its own script, so the cosigners who can
    move the funds are precisely the ones who can direct the stake. A wallet
    whose address has no stake part cannot delegate at all; that is fixed at
    creation, because changing it would change the address.

    Args:
        wallet_dir: The multisig wallet directory.
        pool_id: The pool to delegate to (bech32 `pool1…` or hex). None to skip.
        register: Include a stake registration certificate (costs the ledger's
            key deposit, refunded on deregistration).
        deregister: Include a stake deregistration certificate, reclaiming the
            deposit. Cannot be combined with delegation.

    Returns dict with unsigned_cbor_hex, session_dir, session_id.
    """
    logging.debug(f"[ENTRY] build_multisig_stake_transaction(wallet_dir={wallet_dir})")

    # What was asked for is checked before anything external is touched, so a
    # contradictory request is named as such rather than surfacing as a backend
    # or ledger error further down.
    if deregister and (register or pool_id):
        raise ValueError("Deregistering ends the stake credential, so it cannot be "
                         "combined with registering or delegating.")
    if not (register or deregister or pool_id):
        raise ValueError("Nothing to do: choose registration, delegation, or "
                         "deregistration.")

    wallet = _load_multisig_wallet(wallet_dir)
    config = wallet["config"]
    script = wallet["script"]
    script_address = wallet["script_address"]
    script_key_hashes = wallet["key_hashes"]
    threshold = wallet["threshold"]

    if not wallet["staking"]:
        raise ValueError(
            "This wallet's address has no stake credential, so its stake cannot "
            "be delegated. That is part of the address and cannot be added "
            "afterwards — a new wallet with staking enabled, funded from this "
            "one, is the only way."
        )

    stake_credential = StakeCredential(ScriptHash(wallet["script_hash"]))
    certificates: list[Certificate] = []
    actions: list[str] = []
    if register:
        certificates.append(StakeRegistration(stake_credential))
        actions.append("register the stake credential")
    if pool_id:
        pool_hash = pool_id_to_key_hash(pool_id)
        certificates.append(StakeDelegation(stake_credential, PoolKeyHash(pool_hash)))
        actions.append(f"delegate to {pool_id}")
    if deregister:
        certificates.append(StakeDeregistration(stake_credential))
        actions.append("deregister the stake credential and reclaim its deposit")

    if not context:
        raise ValueError("No backend configured.")

    utxos = context.utxos(script_address)
    if not utxos:
        raise ValueError(
            f"No UTxOs at {script_address}. A certificate transaction still has "
            f"to pay a fee, so the wallet needs some ADA in it."
        )

    builder = TransactionBuilder(context)
    for utxo in utxos:
        builder.add_script_input(utxo, script=script)

    builder.certificates = certificates
    # The certificate's credential is this script, so the script must be
    # witnessed for the certificate too, not only for the inputs.
    builder.add_certificate_script(script)
    builder.witness_override = max(1, len(script_key_hashes))
    valid_days = _apply_validity_window(builder, wallet)

    script_addr_obj: Address = Address.from_primitive(script_address)
    tx_body: TransactionBody = builder.build(change_address=script_addr_obj)

    witness_set = TransactionWitnessSet(native_scripts=[script])
    unsigned_tx = Transaction(tx_body, witness_set)
    unsigned_cbor_hex = unsigned_tx.to_cbor().hex()

    session_id = hashlib.sha256(tx_body.hash()).hexdigest()[:12]
    session_dir = os.path.join(wallet_dir, "sessions", session_id)
    os.makedirs(session_dir, exist_ok=True)
    with open(os.path.join(session_dir, "unsigned.cbor"), "w") as f:
        f.write(unsigned_cbor_hex)
    envelope: dict[str, JSON] = {
        "type": "Unwitnessed Tx BabbageEra",
        "description": "CardanoInterface Multisig Stake Certificate Transaction",
        "cborHex": unsigned_cbor_hex,
    }
    with open(os.path.join(session_dir, "unsigned_text_envelope.json"), "w") as f:
        f.write(_dumps_json(envelope))

    summary: dict[str, JSON] = {
        "wallet_name": config.get("name"),
        "network": config.get("network", SELECTED_NETWORK or "preprod"),
        "kind": "stake",
        "actions": [a for a in actions],
        "pool_id": pool_id,
        "fee": tx_body.fee,
        "amount_lovelace": 0,
        "recipient": script_address,
        "inputs": [{"tx_hash": inp.transaction_id.payload.hex(), "index": inp.index}
                   for inp in _tx_body_inputs(tx_body)],
        "script_key_hashes": [kh.hex() for kh in script_key_hashes],
        "threshold": threshold,
        "valid_until_slot": tx_body.ttl,
        "signing_window_days": valid_days,
        "session_id": session_id,
    }
    _dump_json(summary, os.path.join(session_dir, "summary.json"))
    _write_session_status(session_dir, set(), threshold)

    console.print("\n[bold green]Unsigned stake transaction built.[/bold green]")
    console.print(f"  Session:   [cyan]{session_id}[/cyan]")
    console.print(f"  It will:   {'; '.join(actions)}")
    console.print(f"  Fee:       {format_ada(tx_body.fee)} ADA")
    if register:
        deposit = context.protocol_param.key_deposit
        console.print(f"  Deposit:   {format_ada(deposit)} ADA "
                      "(refunded if the credential is ever deregistered)")
    console.print(f"  Signatures needed: {threshold} of {len(script_key_hashes)}")
    console.print("\n[bold]It is signed and submitted exactly like a payment:[/bold]")
    console.print("  Sign a transaction → Assemble and submit.")

    logging.debug("[EXIT] build_multisig_stake_transaction")
    return {
        "unsigned_cbor_hex": unsigned_cbor_hex,
        "session_dir": session_dir,
        "session_id": session_id,
        "actions": [a for a in actions],
    }


def _multisig_signer_directory(wallet: MultisigWalletInfo,
                               tx: Transaction | None = None) -> tuple[list[bytes],
                                                                      dict[bytes, str], int]:
    """Work out who is allowed to sign a multisig transaction, and by what name.

    The authoritative source is the native script: it carries the key hashes and
    the threshold, and the ledger enforces exactly that. When a transaction is
    supplied, its attached script wins — that is what makes CBOR-only signing
    possible for a cosigner who has no wallet directory of their own.

    The threshold comes from the script too, never from config.json. config.json
    is editable and unauthenticated; the script is hashed into the address, so
    lowering a threshold there is not possible without changing the wallet.

    Returns (key_hashes, name_by_key_hash, threshold).
    """
    script: NativeScript | None = None
    if tx is not None:
        witness_set = tx.transaction_witness_set
        if witness_set is not None:
            attached = _witness_scripts(witness_set)
            if attached:
                script = attached[0]
    if script is None:
        script = wallet["script"]

    key_hashes, _script_threshold, _script_type = extract_key_hashes_from_script(script)
    threshold = script_min_signatures(script)

    names = dict(wallet["labels"])
    for i, kh in enumerate(key_hashes):
        names.setdefault(kh, f"cosigner {i + 1}")
    return key_hashes, names, threshold


def _partial_signature_files(session_dir: str) -> list[str]:
    """Every collected partial-signature CBOR file in a session, sorted.

    Only files written by the signing flow count. `unsigned.cbor` is the body
    being signed and `final.cbor` is a previous assembly's output; treating
    either as a partial would either find no signatures or silently re-import
    an earlier result.
    """
    if not session_dir or not os.path.isdir(session_dir):
        return []
    return sorted(
        f for f in os.listdir(session_dir)
        if f.startswith("partial") and f.endswith(".cbor")
    )


def _collected_signature_hashes(session_dir: str) -> set[bytes]:
    """Key hashes that have already signed in this session."""
    signed: set[bytes] = set()
    for fname in _partial_signature_files(session_dir):
        try:
            with open(os.path.join(session_dir, fname)) as f:
                partial = Transaction.from_cbor(f.read().strip())
        except Exception as e:
            logging.warning(f"Ignoring unreadable partial {fname}: {e}")
            continue
        witness_set = partial.transaction_witness_set
        if witness_set is None or not witness_set.vkey_witnesses:
            continue
        for w in _witness_vkeys(witness_set):
            signed.add(key_hash_from_vkey(w.vkey.payload))
    return signed


def wallet_admin_index(wallet: MultisigWalletInfo) -> int | None:
    """Which cosigner administers this wallet, or None if any may initiate.

    An index that does not name a cosigner in the script is treated as no
    administrator at all: a wallet that cannot be administered by anybody would
    be permanently unusable, which is a worse outcome than an ungated wallet.
    """
    raw = wallet["config"].get("admin_cosigner_index")
    if raw is None:
        return None
    index = _json_int(raw, -1)
    if 0 <= index < len(wallet["key_hashes"]):
        return index
    logging.warning(f"Ignoring out-of-range admin_cosigner_index: {raw!r}")
    return None


def _our_cosigner_indices(current_user: str, user_password: str,
                          wallet: MultisigWalletInfo) -> set[int]:
    """Every cosigner position this machine holds a key for.

    Identity is worked out from the wallets themselves, matched by key hash,
    rather than trusted from config.json — which is plain text a user could
    edit to promote themselves. `claimed_signer_index` is only consulted as a
    fallback for a wallet whose key hashes have not been cached yet.
    """
    indices = {m["script_index"] for m in _match_cosigner_identities(
        current_user, user_password, wallet["key_hashes"])}
    if not indices:
        claimed = wallet["config"].get("claimed_signer_index")
        if claimed is not None:
            indices.add(_json_int(claimed, -1))
    return indices


def _initiator_check(wallet: MultisigWalletInfo, action: str,
                     current_user: str, user_password: str) -> bool:
    """Decide whether this machine may start or finish a spend.

    A wallet may name one cosigner as its administrator. When it does, only that
    cosigner builds, assembles and submits; everyone else can still sign, or the
    threshold could never be met.

    This is a rule this program applies, not one the chain enforces — the script
    alone decides whose signatures are needed. It stops a cosigner from quietly
    starting a spend to their own address, and it is exactly as strong as the
    honesty of the copy of the program in front of you. The message says so
    plainly rather than implying a protection that is not there.
    """
    admin_index = wallet_admin_index(wallet)
    if admin_index is None:
        return True

    ours = _our_cosigner_indices(current_user, user_password, wallet)
    if admin_index in ours:
        return True

    console.print(f"\n[bold yellow]Only the administrator can {action} for this "
                  "wallet.[/bold yellow]")
    console.print(f"  Administrator:  {_cosigner_description(wallet, admin_index)}")
    if ours:
        held = ", ".join(f"cosigner {i + 1}" for i in sorted(ours))
        console.print(f"  You hold:       {held}")
        console.print("\n[dim]You can still sign transactions the administrator "
                      "starts — that is where your signature is needed.[/dim]")
    else:
        console.print("  You hold:       no cosigner key on this machine")
        console.print("\n[dim]This copy of the wallet can watch the balance and hold "
                      "the script, but not act.[/dim]")
    console.print("[dim]Ask the administrator to start it.[/dim]")
    return False


def _write_session_status(session_dir: str, collected: set[bytes], threshold: int,
                          **extra: JSON) -> None:
    """Record how far a signing session has got, so the TUI can show it."""
    status = "ready_to_submit" if len(collected) >= threshold else "awaiting_signatures"
    payload: dict[str, JSON] = {
        "status": status,
        "signatures": len(collected),
        "threshold": threshold,
        "signed_key_hashes": [h for h in sorted(kh.hex() for kh in collected)],
    }
    payload.update(extra)
    _dump_json(payload, os.path.join(session_dir, "status.json"))


def _verify_witness(witness: VerificationKeyWitness, body_hash: bytes) -> bool:
    """Check that a witness really is a signature over this transaction body.

    Membership of the signing key in the script proves who a witness claims to
    be; only this check proves the signature was made over the body being
    assembled. Without it, an operator could swap the body after collecting
    signatures and the swap would not be noticed until the ledger rejected it.
    """
    from nacl.signing import VerifyKey

    try:
        VerifyKey(witness.vkey.payload).verify(body_hash, witness.signature)
        return True
    except Exception as e:
        # A bad signature, a malformed key, or a wrong-length payload all mean
        # the same thing here: this witness does not authorise this body.
        logging.warning(f"Witness signature verification failed: {e}")
        return False


@exception_error
def sign_multisig_transaction(wallet_dir: str, unsigned_cbor_hex: str | None = None,
                              session_dir: str | None = None, password: str | None = None,
                              signer_wallet_dir: str | None = None,
                              signer_key_hash: bytes | None = None,
                              accumulate: bool = False) -> dict[str, JSON] | None:
    """Sign an unsigned (or partially-signed) multisig transaction.

    Every cosigner signs with their own personal wallet, whose key hash the
    script names. How the wallet arrived — created here, recovered from a raw
    `.cbor`, restored from a package — changes nothing about this path.

    Args:
        wallet_dir: The multisig wallet directory.
        unsigned_cbor_hex: The transaction CBOR hex (unsigned, or already
            partially-signed when relaying).
        session_dir: Alternative to unsigned_cbor_hex, loads from session.
        password: Password for the signing wallet.
        signer_wallet_dir: The personal wallet to sign with.
        signer_key_hash: The specific key of that wallet to sign with, when it
            holds several of the script's keys. Without it the wallet's first
            matching key would be used, which is not necessarily the one that
            was chosen.
        accumulate: Relay mode. When True, merge this signature into the incoming
            transaction's existing witness set and return the accumulated CBOR
            (no session partials written). The accumulated CBOR is the carrier the
            next cosigner imports; the terminator submits it once threshold is met.
            When False (default), produce a single-witness partial for the
            operator-collect assembly flow.

    Returns dict with partial_cbor_hex, session_dir, matched_cosigner_index
    (and reached_threshold when accumulate=True).
    """
    logging.debug(f"[ENTRY] sign_multisig_transaction(wallet_dir={wallet_dir})")

    wallet = _load_multisig_wallet(wallet_dir)
    config = wallet["config"]

    if unsigned_cbor_hex is None and session_dir:
        cbor_path = os.path.join(session_dir, "unsigned.cbor")
        with open(cbor_path) as f:
            unsigned_cbor_hex = f.read().strip()

    if not unsigned_cbor_hex:
        raise ValueError("No unsigned transaction CBOR provided.")

    unsigned_tx = Transaction.from_cbor(unsigned_cbor_hex)
    tx_body = unsigned_tx.transaction_body

    script_key_hashes, cosigner_names, threshold = _multisig_signer_directory(
        wallet, unsigned_tx
    )
    # Who has signed already comes from two places, and both matter. In
    # operator-collect the partials sit in the session directory; in a relay the
    # signatures travel inside the transaction that just arrived. Reading only
    # the session would tell a relay signer "0 of 2 collected" while holding a
    # transaction their cosigner had already signed — the one fact the review is
    # supposed to make plain.
    already_signed = _collected_signature_hashes(session_dir) if session_dir else set()
    incoming_witnesses = unsigned_tx.transaction_witness_set
    if incoming_witnesses is not None and incoming_witnesses.vkey_witnesses:
        for w in _witness_vkeys(incoming_witnesses):
            already_signed.add(key_hash_from_vkey(w.vkey.payload))
    already_signed &= set(script_key_hashes)

    # ── Display comprehensive transaction review ─────────────────────────
    console.print("\n[bold bright_cyan]╔══ MULTISIG TRANSACTION REVIEW ══╗[/bold bright_cyan]")
    console.print("[bold bright_cyan]║[/bold bright_cyan]  This transaction will spend funds from "
        "the multisig script address.")
    console.print("[bold bright_cyan]║[/bold bright_cyan]  Review carefully before signing.")
    console.print("[bold bright_cyan]╚════════════════════════════════╝[/bold bright_cyan]")

    console.print(f"\n  [bold]Transaction ID:[/bold] {tx_body.id.payload.hex()}")
    console.print(f"  [bold]Fee:[/bold] {format_ada(tx_body.fee)} ADA")

    # Inputs with full address context
    total_input = 0
    console.print("\n  [bold underline]Inputs (source of funds):[/bold underline]")
    for inp in _tx_body_inputs(tx_body):
        console.print(f"    TX {inp.transaction_id.payload.hex()[:64]}#{inp.index}")
    console.print("  [dim]  (from multisig script address)[/dim]")

    # Outputs with role labels (destination vs change)
    script_addr_str = wallet.get("script_address", "")
    console.print("\n  [bold underline]Outputs (where funds go):[/bold underline]")
    for i, out in enumerate(tx_body.outputs):
        addr_str = str(out.address)
        lovelace = out.amount.coin
        total_input += lovelace
        is_change = addr_str == script_addr_str
        role = ("[cyan]CHANGE (back to multisig)[/cyan]" if is_change
                else "[yellow]DESTINATION[/yellow]")
        console.print(f"    {i + 1}. {role}")
        console.print(f"       Address: {addr_str}")
        console.print(f"       Amount:  {format_ada(lovelace)} ADA")
        if out.amount.multi_asset:
            for policy_bytes, assets in _multi_asset_items(out.amount.multi_asset):
                for asset_bytes, qty in assets:
                    console.print(f"       Token:   {policy_bytes.hex()[:16]}..."
                                  f"{_asset_display_name(asset_bytes)[:24]} x{qty}")

    # Who can sign, and who already has
    console.print("\n  [bold underline]Signing requirements:[/bold underline]")
    console.print(f"    Threshold: {threshold} of {len(script_key_hashes)} cosigners required")
    console.print(f"    Collected so far: {len(already_signed)} of {threshold}")
    console.print("  [bold]Cosigners in this script:[/bold]")
    for kh in script_key_hashes:
        mark = "[green]✓ signed[/green]" if kh in already_signed else "[yellow]○ pending[/yellow]"
        console.print(f"      {mark}  {cosigner_names.get(kh, 'unknown')}: {kh.hex()[:16]}...")

    # Authorization explanation
    console.print("\n  [bold underline]What your signature authorizes:[/bold underline]")
    console.print("    By signing, you authorize the script to release funds from")
    console.print(f"    the multisig wallet '{config.get('name', wallet_dir)}' on "
        f"{config.get('network', 'unknown')}.")
    console.print("    This action [bold red]CANNOT[/bold red] be undone once the "
        "transaction is submitted.")
    console.print("    Only sign if you have verified the destination and amounts above.")

    console.print("\n[bold]Do you want to sign this transaction? (y/n):[/bold]")
    confirm = session.prompt("> ").strip().lower()
    if confirm != "y":
        console.print("[yellow]Signing cancelled.[/yellow]")
        return None

    # Every cosigner signs with the personal wallet they already have. There is
    # no multisig-specific signing key anywhere in this system, so a wallet
    # created here and a wallet recovered from a dead provider sign identically.
    if not signer_wallet_dir:
        raise ValueError(
            "No wallet was chosen to sign with. A cosigner signs with their own "
            "personal wallet, matched to the script by key hash."
        )
    signer_name = os.path.basename(signer_wallet_dir)

    # A mistyped password is the single most common thing to go wrong here, and
    # it is entirely recoverable — so it asks again instead of throwing the
    # signer back to the menu with a decryption error.
    mnemonic: str | None = None
    while mnemonic is None:
        if password is None:
            console.print(f"\n[bold]Enter the password for '{signer_name}' to "
                          "sign:[/bold]")
            password = prompt_existing_password()
        try:
            mnemonic = load_encrypted_mnemonic(signer_wallet_dir, password)
        except Exception:
            console.print(f"[red]That password did not open '{signer_name}'.[/red]")
            console.print("[bold]Try again? (y/n)[/bold]")
            if not _prompt_yes_no(default=True):
                console.print("[yellow]Not signed.[/yellow]")
                return None
            password = None

    # Which key the script names depends on the standard the tool that built it
    # followed, and for a recovered wallet that tool was somebody else's. The
    # search covers both standards and a range of accounts/addresses, so a key
    # a provider put somewhere unusual still signs.
    signing_key: SigningKey | ExtendedSigningKey | None = None
    our_key_hash: bytes | None = None
    matched_path: str | None = None
    found = find_cosigner_derivation(mnemonic, set(script_key_hashes),
                                     only_hash=signer_key_hash)
    if found is not None:
        matched_path, our_key_hash, child = found
        signing_key = ExtendedSigningKey.from_hdwallet(child)

    # A signature only counts if the key is named in the script. Checking here
    # means the signer is told immediately, rather than producing a partial
    # that assembly silently discards later.
    if signing_key is None or our_key_hash is None:
        console.print("\n[bold red]This wallet is not one of the script's "
                      "cosigners.[/bold red]")
        console.print("  Keys derived from this wallet:")
        for path_label, kh in cosigner_key_hash_candidates(mnemonic).items():
            console.print(f"    {kh.hex()}  [dim]({path_label})[/dim]")
        console.print(f"  [dim]({COSIGNER_SEARCH_ACCOUNTS} accounts x "
                      f"{COSIGNER_SEARCH_INDICES} addresses were searched on both "
                      "standards.)[/dim]")
        console.print("  Script expects:")
        for kh in script_key_hashes:
            console.print(f"    {kh.hex()}  [dim]({cosigner_names.get(kh, 'unknown')})[/dim]")
        console.print("\n[yellow]A signature from this wallet would be rejected, "
                      "so nothing was written.[/yellow]")
        console.print("[dim]If you are a cosigner, try the wallet whose key hash you "
                      "gave the wallet's creator.[/dim]")
        return None

    console.print(f"[dim]Signing with '{os.path.basename(signer_wallet_dir)}' "
                  f"({matched_path}).[/dim]")

    matched_cosigner_index = script_key_hashes.index(our_key_hash)
    cosigner_label = cosigner_names.get(our_key_hash, f"cosigner_{matched_cosigner_index}")

    if our_key_hash in already_signed:
        console.print(f"\n[yellow]{cosigner_label} has already signed this transaction.[/yellow]")
        console.print("[yellow]Re-signing replaces that signature with an equivalent one.[/yellow]")

    signature = signing_key.sign(tx_body.hash())
    vk_witness = VerificationKeyWitness(signing_key.to_verification_key(), signature)

    # Operator-collect (default): a partial carries exactly one signature — our
    # own — so merging happens at assembly and each cosigner's file stays
    # independently verifiable. Relay (accumulate=True): merge this signature
    # into the incoming transaction's existing witnesses so the exported CBOR
    # accumulates signatures as it is passed cosigner-to-cosigner.
    if accumulate:
        prior_vkeys: list[VerificationKeyWitness] = []
        ws = unsigned_tx.transaction_witness_set
        if ws and ws.vkey_witnesses:
            # drop any stale copy of our own signature, keep everyone else's
            prior_vkeys = [w for w in _witness_vkeys(ws)
                           if key_hash_from_vkey(w.vkey.payload) != our_key_hash]
        merged_vkeys = prior_vkeys + [vk_witness]
        witness_set = TransactionWitnessSet(
            native_scripts=ws.native_scripts if ws else None,
            vkey_witnesses=merged_vkeys,
        )
        partial_tx = Transaction(tx_body, witness_set)
        partial_cbor_hex = partial_tx.to_cbor().hex()
        signed_hashes = {key_hash_from_vkey(w.vkey.payload) for w in merged_vkeys}
        signed_hashes &= set(script_key_hashes)
        return {"partial_cbor_hex": partial_cbor_hex,
                "reached_threshold": len(signed_hashes) >= threshold,
                "matched_cosigner_index": matched_cosigner_index}

    witness_set = TransactionWitnessSet(
        native_scripts=unsigned_tx.transaction_witness_set.native_scripts,
        vkey_witnesses=[vk_witness],
    )
    partial_tx = Transaction(tx_body, witness_set)
    partial_cbor_hex = partial_tx.to_cbor().hex()

    if session_dir is None:
        session_id = hashlib.sha256(tx_body.hash()).hexdigest()[:12]
        session_dir = os.path.join(wallet_dir, "sessions", session_id)
    os.makedirs(session_dir, exist_ok=True)

    # Named per signing key so cosigners' partials accumulate side by side
    # instead of overwriting each other in a shared session directory.
    partial_name = f"partial_{our_key_hash.hex()[:16]}.cbor"
    with open(os.path.join(session_dir, partial_name), "w") as f:
        f.write(partial_cbor_hex)

    if not os.path.exists(os.path.join(session_dir, "unsigned.cbor")):
        with open(os.path.join(session_dir, "unsigned.cbor"), "w") as f:
            f.write(unsigned_cbor_hex)

    envelope: dict[str, JSON] = {
        "type": "Witnessed Tx BabbageEra",
        "description": f"CardanoInterface Multisig Partial Signed ({cosigner_label})",
        "cborHex": partial_cbor_hex,
    }
    text_envelope = _dumps_json(envelope)
    te_name = f"partial_{our_key_hash.hex()[:16]}_text_envelope.json"
    with open(os.path.join(session_dir, te_name), "w") as f:
        f.write(text_envelope)

    collected = _collected_signature_hashes(session_dir)
    _write_session_status(session_dir, collected, threshold)

    console.print(f"\n[bold green]Signed as {cosigner_label}.[/bold green]")
    console.print(f"  Signatures now collected: {len(collected)} of {threshold}")
    console.print(f"  Your partial: [cyan]{os.path.join(session_dir, partial_name)}[/cyan]")
    if len(collected) >= threshold:
        console.print("\n[bold]Threshold reached — the initiator can now Assemble & Submit.[/bold]")
    else:
        console.print(f"\n[bold]Send this partial back to the initiator. "
                      f"{threshold - len(collected)} more signature(s) needed.[/bold]")

    logging.debug("[EXIT] sign_multisig_transaction")
    return {
        "partial_cbor_hex": partial_cbor_hex,
        "partial_path": os.path.join(session_dir, partial_name),
        "session_dir": session_dir,
        "matched_cosigner_index": matched_cosigner_index,
        "signatures_collected": len(collected),
        "threshold": threshold,
    }


@exception_error
def restore_multisig_participation(wallet_dir: str, current_user: str,
                                   user_password: str,
                                   unlock_password: str | None = None
                                   ) -> dict[str, JSON] | None:
    """Work out which cosigner this node is, and record it on the wallet.

    Nothing secret is copied anywhere. A cosigner's signing power lives in the
    personal wallet they already hold, so "participation" is only the knowledge
    of which key hash in the script is theirs — which lets the signing flow
    preselect the right wallet instead of asking every time.

    Args:
        wallet_dir: Path to the multisig wallet directory.
        current_user: The logged-in user, whose wallets are searched.
        user_password: That user's password, to list their wallets.
        unlock_password: Optionally, a wallet password, so wallets that have
            never been matched before can be opened and derived.

    Returns dict with cosigner_index, wallet_name, matched bool.
    """
    logging.debug(f"[ENTRY] restore_multisig_participation(wallet_dir={wallet_dir})")

    wallet = _load_multisig_wallet(wallet_dir)
    config = wallet["config"]
    key_hashes = wallet["key_hashes"]

    matches = _match_cosigner_identities(current_user, user_password, key_hashes,
                                         unlock_password=unlock_password)

    if not matches:
        console.print("[red]None of your wallets is a cosigner of this script.[/red]")
        console.print("[yellow]That can mean:[/yellow]")
        console.print("  - the wallet that holds your cosigner key is not on this machine")
        console.print("  - it has a different password, so it could not be checked")
        console.print("  - you are an observer of this wallet, not a cosigner")
        console.print("\n[dim]An observer can still build and assemble transactions; "
                      "only signing needs a matching key.[/dim]")
        return {"matched": False, "cosigner_index": None, "wallet_name": None}

    match = matches[0]
    if len(matches) > 1:
        console.print(f"[dim]{len(matches)} of your wallets are cosigners of this "
                      "script; recording the first.[/dim]")

    config["claimed_signer_index"] = match["script_index"]
    config["signer_wallet"] = match["wallet_name"]
    _dump_json(config, os.path.join(wallet_dir, "config.json"))

    console.print(f"\n[bold green]You are cosigner {match['script_index'] + 1} of this "
                  f"wallet.[/bold green]")
    console.print(f"  Wallet:     {config.get('name')}")
    console.print(f"  You sign with: '{match['wallet_name']}' ({match['path_label']})")
    console.print(f"  Your key hash: {match['key_hash'].hex()}")
    console.print("\n[dim]Nothing was copied — that wallet stays where it is, and its "
                  "password is what signs.[/dim]")

    logging.debug(f"[EXIT] restore_multisig_participation index={match['script_index']}")
    return {"matched": True, "cosigner_index": match["script_index"],
            "wallet_name": match["wallet_name"]}


def script_address_candidates(script_hash_bytes: bytes) -> dict[str, str]:
    """The addresses a script hash could be spending from, by kind.

    A native script CBOR fixes the payment credential and therefore the payment
    half of the address, but it says nothing about the stake half. The same
    script can be sitting behind an enterprise address (no stake part) or a base
    address whose stake credential is the very same script. Those are different
    addresses holding different funds, and a recovery that assumed the wrong one
    would report an empty wallet that in fact holds everything.
    """
    return {
        "enterprise (no staking)": script_to_address(script_hash_bytes),
        "base (script also stakes)": script_to_address(script_hash_bytes,
                                                       script_hash_bytes),
    }


def native_script_candidate_readings(cbor_hex: str) -> list[tuple[str, NativeScript]]:
    """Every way this CBOR could be read as a native script, strongest first.

    A bare script reads one way. But wallet backends export shared-wallet
    script templates as a version envelope, [1, [script]], and those bytes
    also parse as a bare one-member `all([script])` — a different tree with a
    different hash and therefore a different address. Reading only the bare
    interpretation recovers a wallet that points at no funds, silently; so
    both readings are returned and the chain decides which one is the wallet
    (see `_choose_script_reading`).

    The envelope is only recognised as `[int, [script]]` with exactly one
    payload member: a genuine multi-member `all`/`any` script has the same
    outer shape with more members and must not grow a second reading.
    """
    readings: list[tuple[str, NativeScript]] = []
    with contextlib.suppress(Exception):
        readings.append(("script", NativeScript.from_cbor(cbor_hex)))
    try:
        top_value = _cbor_loads(bytes.fromhex(cbor_hex))
    except ValueError:
        return readings
    if not isinstance(top_value, list) or len(top_value) != 2:
        return readings
    if not isinstance(top_value[0], int) or not 0 <= top_value[0] <= 15:
        return readings
    payload = top_value[1]
    if not isinstance(payload, list) or len(payload) != 1:
        return readings
    inner_tree = payload[0]
    if not isinstance(inner_tree, list):
        return readings
    try:
        inner = NativeScript.from_cbor(_cbor_dumps_hex(inner_tree))
    except Exception:
        return readings
    if not readings or inner.to_cbor() != readings[0][1].to_cbor():
        readings.append(("version-wrapped script (unwrapped)", inner))
    return readings


def _script_address_balances(script_hash_bytes: bytes) -> dict[str, int]:
    """Lovelace sitting at each candidate address of one script hash.

    Addresses that could not be queried are simply absent from the mapping —
    an unreachable backend must not be reported as an empty wallet.
    """
    balances: dict[str, int] = {}
    if not context:
        return balances
    for address in script_address_candidates(script_hash_bytes).values():
        try:
            utxos = context.utxos(address)
        except Exception as e:
            logging.debug(f"Could not query {address}: {e}")
            continue
        balances[address] = sum(u.output.amount.coin for u in utxos)
    return balances


def _choose_script_reading(
        readings: list[tuple[str, NativeScript]], known_address: str | None,
        staking: bool | None) -> tuple[str, NativeScript, bool | None, str | None]:
    """Decide which reading of a script CBOR is the wallet the funds belong to.

    Order of authority: an address the user supplies (verified against each
    reading's script hash), then the reading whose address actually holds
    funds, then — for an unfunded wallet — the unwrapped reading, because the
    wrapping is the provider's export format, not a script anyone chose. If
    both readings hold funds and no address was given, the wallet is not
    guessed at; the user is asked to recover again naming the address.

    Returns (label, script, staking or None when undetermined, override
    address or None).
    """
    if len(readings) == 1:
        label, script = readings[0]
        if known_address:
            supplied: Address = Address.from_primitive(known_address)
            if not isinstance(supplied.payment_part, ScriptHash):
                raise ValueError("That address is not a script address, so it "
                                 "cannot belong to this script.")
            supplied_hash = bytes(supplied.payment_part.payload)
            if supplied_hash != script_hash_from_script(script):
                raise ValueError(
                    "That address does not belong to this script — its payment "
                    "credential is a different script hash. Recovering it here "
                    "would produce a wallet pointing at somebody else's funds."
                )
            return (label, script,
                    isinstance(supplied.staking_part, ScriptHash), known_address)
    elif known_address is not None:
        # More than one reading: the file is version-wrapped, and the bytes
        # alone cannot say which tree the funds belong to.
        supplied = Address.from_primitive(known_address)
        if not isinstance(supplied.payment_part, ScriptHash):
            raise ValueError("That address is not a script address, so it cannot "
                             "belong to this script.")
        supplied_hash = bytes(supplied.payment_part.payload)
        for label, script in readings:
            if supplied_hash == script_hash_from_script(script):
                console.print(f"[green]The address you gave matches the "
                              f"{label} reading of this file.[/green]")
                return (label, script,
                        isinstance(supplied.staking_part, ScriptHash), known_address)
        raise ValueError(
            "That address matches neither way of reading this script file, so "
            "it cannot belong to the wallet in it."
        )

    funded_readings: list[tuple[str, NativeScript, str]] = []
    for label, script in readings:
        hash_bytes = script_hash_from_script(script)
        balances = _script_address_balances(hash_bytes)
        for candidate_label, candidate in script_address_candidates(hash_bytes).items():
            if candidate in balances:
                console.print(f"[dim]{label} — {candidate_label}: "
                              f"{format_ada(balances[candidate])} ADA[/dim]")
        funded = [a for a, lovelace in balances.items() if lovelace > 0]
        if funded:
            funded_readings.append((label, script, funded[0]))
    if len(funded_readings) == 1:
        label, script, address = funded_readings[0]
        hash_bytes = script_hash_from_script(script)
        staking_resolved = address != script_to_address(hash_bytes)
        wrapped_note = (" — the script was exported inside a version envelope, "
                        "and this program unwrapped it" if "wrapped" in label else "")
        console.print(f"[green]Funds found at the "
                      f"{'base' if staking_resolved else 'enterprise'} address of "
                      f"the {label} reading{wrapped_note}.[/green]")
        return label, script, staking_resolved, None
    if len(funded_readings) > 1:
        lines = "; ".join(f"{label}: {addr}" for label, _s, addr in funded_readings)
        raise ValueError(
            "Both ways of reading this script file hold funds on chain, so the "
            "wallet cannot be chosen for you. Recover again and give the exact "
            f"address your funds sit at. ({lines})"
        )

    # Nothing funded anywhere: prefer the unwrapped reading — the wrapping is
    # the provider's format — and say plainly what was decided and how to
    # override it.
    wrapped_index = next((i for i, (lbl, _s) in enumerate(readings)
                          if "wrapped" in lbl), None)
    label, script = readings[wrapped_index if wrapped_index is not None else 0]
    console.print("[yellow]Neither way of reading this file holds funds right "
                  "now.[/yellow]")
    shape = "unwrapped script" if "wrapped" in label else "enterprise"
    console.print(f"[dim]Recovering the {shape}"
                  " address. If the provider used a different[/dim]")
    console.print("[dim]address shape, recover again giving the address you "
                  "know.[/dim]")
    return label, script, staking, None


@exception_error
def import_script_cbor(cbor_hex: str, wallet_name: str, network: str | None = None,
                       labels: dict[bytes, str] | None = None,
                       provenance: str = "recovered_cbor",
                       staking: bool | None = None,
                       known_address: str | None = None) -> dict[str, JSON] | None:
    """Recover a multisig wallet from a raw native-script CBOR (spec §Recovery, Door 1).

    This is the disaster path: a provider died and left nothing but an opaque
    `.cbor` the user cannot read. Everything the wallet needs is inside it — the
    cosigner key hashes, the threshold, and (through the script hash) the
    address — so this reads it back out and writes an ordinary wallet.

    The result is not a second-class "recovery wallet". It has the same layout
    and signs by the same path as one created here; only `provenance` records
    that the script arrived rather than being built.

    Args:
        cbor_hex: Hex-encoded native script CBOR.
        wallet_name: Name for the wallet directory.
        network: Network name (defaults to SELECTED_NETWORK).
        labels: Optional human names per key hash; local convenience only.
        provenance: How the script arrived, for the record.
        staking: Whether the wallet's address carries a stake credential. When
            None, the chain is asked which candidate address holds the funds.
        known_address: The address the funds are known to sit at, when the user
            has it. Verified against the script and then used verbatim, which
            covers providers whose stake credential is not this script.
    """
    logging.debug(f"[ENTRY] import_script_cbor(name={wallet_name})")

    if network is None:
        network = SELECTED_NETWORK or "preprod"

    wallet_dir = secure_path_join(WALLET_DIR, wallet_name)
    if os.path.exists(wallet_dir):
        raise ValueError(f"Wallet '{wallet_name}' already exists.")

    script: NativeScript
    readings = native_script_candidate_readings(cbor_hex)
    if not readings:
        raise ValueError("That CBOR is not a readable native script.")
    _label, script, _staking_resolved, override_address = _choose_script_reading(
        readings, known_address, staking)
    if _staking_resolved is not None:
        staking = _staking_resolved

    key_hashes, script_threshold, script_type = extract_key_hashes_from_script(script)

    if not key_hashes:
        raise ValueError(
            "That CBOR is a native script, but it names no signing keys, so no "
            "wallet can be recovered from it."
        )

    threshold = script_min_signatures(script)
    is_flat = script_is_flat_threshold(script)
    num_cosigners = len(key_hashes)
    percentage = int((threshold / num_cosigners) * 100)
    not_before_slot, not_after_slot = script_timelocks(script)

    # Which address the funds are at is the one thing the script does not say.
    # That choice — including which reading of a version-wrapped file is the
    # wallet — was made by _choose_script_reading above, with the chain as
    # the authority when the funds exist.

    config: dict[str, JSON] = {
        "name": wallet_name,
        "network": network,
        "threshold": threshold,
        "num_cosigners": num_cosigners,
        "percentage": percentage,
        "key_hashes": [kh.hex() for kh in key_hashes],
        "labels": {kh.hex(): name for kh, name in (labels or {}).items()},
        "script_type": script_type,
        "staking": bool(staking),
        "provenance": provenance,
        "claimed_signer_index": None,
    }

    script_hash_bytes, script_address, _cbor = _write_multisig_wallet(
        wallet_dir, script, config, network, staking=bool(staking)
    )
    if override_address is not None and override_address != script_address:
        # The user's address wins over the derived one: they know where the
        # funds are, and this program has already proved the script matches it.
        script_address = override_address
        with open(os.path.join(wallet_dir, "script_address"), "w") as f:
            f.write(script_address)

    console.print("[bold]Recovered this wallet from the script:[/bold]")
    console.print(f"  Script type: {script_type or 'unknown'}")
    if is_flat:
        console.print(f"  Threshold:   {threshold} of {num_cosigners} must sign")
    else:
        # A nested script cannot be honestly summarised as "m of n": which keys
        # are needed can depend on other branches and on the slot.
        console.print(f"  Threshold:   at least {threshold} of {num_cosigners} "
                      "must sign")
        console.print("  [yellow]This script has nested conditions or timelocks, so "
                      "which[/yellow]")
        console.print("  [yellow]signatures are needed can depend on the branch and "
                      "the slot.[/yellow]")
        console.print("  [dim]View script details to see its exact shape.[/dim]")
    if not_before_slot is not None:
        console.print(f"  Locked until {_slot_description(not_before_slot)} — nothing "
                      "can be spent before it")
    if not_after_slot is not None:
        console.print(f"  [yellow]Expires {_slot_description(not_after_slot)} — after "
                      "it the funds can never be moved[/yellow]")
    console.print(f"  Script hash: {script_hash_bytes.hex()}")
    console.print(f"  Address:     [cyan]{script_address}[/cyan]")
    console.print(f"  Staking:     {'yes' if staking else 'no'}")
    console.print(f"\n[bold]Cosigners named in the script ({num_cosigners}):[/bold]")
    for i, kh in enumerate(key_hashes):
        name = (labels or {}).get(kh)
        suffix = f"  [dim]({name})[/dim]" if name else ""
        console.print(f"  {i + 1}. {kh.hex()}{suffix}")

    console.print(f"\n[bold green]Wallet '{wallet_name}' recovered.[/bold green]")
    console.print("\n  [bold]To spend from it:[/bold]")
    console.print("  1. Anyone with the wallet builds an unsigned transaction.")
    console.print("  2. Each cosigner signs it with their own personal wallet —")
    console.print("     on their own machine, with their own copy of this program.")
    console.print(f"  3. Once {threshold} "
                  f"{'has' if threshold == 1 else 'have'} signed, the last one "
                  "submits it.")
    console.print("\n  [dim]Run 'Restore Participation' to find out which cosigner "
                  "you are.[/dim]")

    logging.debug("[EXIT] import_script_cbor")
    return {
        "wallet_dir": wallet_dir,
        "script_hash": script_hash_bytes.hex(),
        "script_address": script_address,
        "key_hashes": [kh.hex() for kh in key_hashes],
        "threshold": threshold,
    }


CHECKPOINT_SIGNATURE_SCHEME = "cardanointerface-checkpoint-v1"


def checkpoint_signing_bytes(checkpoint: dict[str, JSON]) -> bytes:
    """Exactly what an administrator's signature covers, as bytes.

    Only the claims this program acts on and cannot check for itself are in
    here: which script the wallet is, which network it belongs to, which
    address it spends from, and which cosigner administers it. That last one is
    the whole point — everything else about a wallet is either derived from the
    script (threshold, cosigners) and re-derived on import regardless of what
    the file says, or purely local (the wallet's name, the cosigners' names).

    Signing the local parts as well would mean renaming a wallet on the machine
    that receives it silently destroyed the administrator's proof, which is a
    worse outcome than leaving cosmetic text unsigned: nothing this program
    decides depends on it.
    """
    script = _json_object(checkpoint.get("script"))
    config = _json_object(checkpoint.get("config"))
    payload: dict[str, JSON] = {
        "scheme": CHECKPOINT_SIGNATURE_SCHEME,
        "type": checkpoint.get("type"),
        "version": checkpoint.get("version"),
        "created": checkpoint.get("created"),
        "network": checkpoint.get("network"),
        "script_cbor_hex": script.get("cbor_hex"),
        "script_hash": script.get("hash"),
        "script_address": script.get("address"),
        "admin_cosigner_index": config.get("admin_cosigner_index"),
    }
    return _canonical_json_bytes(payload)


def sign_wallet_checkpoint(checkpoint: dict[str, JSON], signer_wallet_dir: str,
                           password: str) -> dict[str, JSON]:
    """Sign a checkpoint as the wallet's administrator.

    Only the designated administrator's own key can produce this, and it is
    checked here before signing rather than leaving a signature that the
    importing side would reject for reasons the exporter never saw.

    Returns the `admin_signature` block to put in the checkpoint.
    """
    config = _json_object(checkpoint.get("config"))
    admin_index = config.get("admin_cosigner_index")
    if admin_index is None:
        raise ValueError("This wallet has no administrator to sign as.")

    script: NativeScript = NativeScript.from_cbor(
        _json_str(_json_object(checkpoint.get("script")).get("cbor_hex")))
    key_hashes, _threshold, _type = extract_key_hashes_from_script(script)
    index = _json_int(admin_index, -1)
    if not 0 <= index < len(key_hashes):
        raise ValueError(f"Administrator index {admin_index!r} names no cosigner "
                         "in this script.")
    expected = key_hashes[index]

    mnemonic = load_encrypted_mnemonic(signer_wallet_dir, password)
    found = find_cosigner_derivation(mnemonic, {expected})
    if found is None:
        raise ValueError(
            "That wallet does not hold the administrator's key, so it cannot "
            "sign this checkpoint as the administrator."
        )
    _path, key_hash, child = found
    signing_key = ExtendedSigningKey.from_hdwallet(child)
    # An extended verification key's payload is the public key followed by its
    # chain code. Only the first half is the ed25519 key that verifies a
    # signature — and it is also what the chain sees in a witness — so the
    # non-extended form is what gets published here.
    verification_key = signing_key.to_verification_key().to_non_extended()
    signature = signing_key.sign(checkpoint_signing_bytes(checkpoint))

    return {
        "scheme": CHECKPOINT_SIGNATURE_SCHEME,
        "cosigner_index": index,
        "key_hash": key_hash.hex(),
        "vkey": verification_key.payload.hex(),
        "signature": signature.hex(),
    }


def verify_checkpoint_admin_signature(checkpoint: dict[str, JSON]) -> tuple[bool, str]:
    """Check that the administrator this checkpoint names really signed it.

    Returns (verified, reason). Four things have to hold, and all of them are
    checked against the script rather than against the checkpoint's own claims:
    the signature is over these exact bytes, the verification key hashes to the
    key hash given, that key hash is the cosigner at the administrator's index,
    and the scheme is one this program knows.

    Without this, `admin_cosigner_index` is just a number in a text file, and
    anyone who can edit a checkpoint before passing it on can name themselves
    administrator of somebody else's wallet.
    """
    from nacl.signing import VerifyKey

    signature_block = _json_object(checkpoint.get("admin_signature"))
    if not signature_block:
        return False, "the checkpoint carries no administrator signature"

    scheme = _json_str(signature_block.get("scheme"))
    if scheme != CHECKPOINT_SIGNATURE_SCHEME:
        return False, f"unknown signature scheme {scheme!r}"

    config = _json_object(checkpoint.get("config"))
    admin_index = config.get("admin_cosigner_index")
    if admin_index is None:
        return False, "the checkpoint is signed but names no administrator"

    try:
        script: NativeScript = NativeScript.from_cbor(
            _json_str(_json_object(checkpoint.get("script")).get("cbor_hex")))
        key_hashes, _threshold, _type = extract_key_hashes_from_script(script)
        key_hash = bytes.fromhex(_json_str(signature_block.get("key_hash")))
        vkey = bytes.fromhex(_json_str(signature_block.get("vkey")))
        signature = bytes.fromhex(_json_str(signature_block.get("signature")))
    except ValueError as e:
        return False, f"the signature block is malformed ({e})"

    index = _json_int(admin_index, -1)
    if not 0 <= index < len(key_hashes):
        return False, f"administrator index {admin_index!r} names no cosigner"
    if key_hashes[index] != key_hash:
        return False, ("the signing key is not the cosigner named as "
                       f"administrator (cosigner {index + 1})")
    if len(vkey) != 32:
        return False, (f"the verification key is {len(vkey)} bytes, not the 32 an "
                       "ed25519 key has (a chain code was probably left on it)")
    if key_hash_from_vkey(vkey) != key_hash:
        return False, "the verification key does not match the key hash it claims"

    try:
        VerifyKey(vkey).verify(checkpoint_signing_bytes(checkpoint), signature)
    except Exception as e:
        logging.warning(f"Checkpoint admin signature rejected: {e}")
        return False, "the signature does not match the checkpoint's contents"

    return True, f"signed by cosigner {index + 1}"


def export_wallet_checkpoint(wallet_dir: str, signer_wallet_dir: str | None = None,
                             password: str | None = None) -> dict[str, JSON]:
    """Export a wallet checkpoint package (JSON).

    Bundles the wallet's immutable identity (script CBOR + hash + address) with
    the off-chain context the script cannot carry — names, threshold policy, an
    optional admin — plus human-readable instructions for CardanoInterface and
    cardano-cli.

    No private material is included, and no cosigner xpubs: a checkpoint is safe
    to hand to every cosigner, which is the point of it.

    When the administrator's wallet and password are supplied, the checkpoint is
    signed, so the importing side can tell a genuine administrator designation
    from one somebody wrote into the file on the way.

    Returns the checkpoint dict.
    """
    logging.debug(f"[ENTRY] export_wallet_checkpoint(wallet_dir={wallet_dir})")

    wallet = _load_multisig_wallet(wallet_dir)
    config = wallet["config"]
    script_hash_bytes = wallet["script_hash"]
    script_address = wallet["script_address"]

    script_cbor_path = os.path.join(wallet_dir, "script.cbor")
    with open(script_cbor_path) as f:
        script_cbor_hex = f.read().strip()

    script_json_path = os.path.join(wallet_dir, "script.json")
    with open(script_json_path) as f:
        script_dict = _json_loads(f.read())

    known_indices_dir = os.path.join(wallet_dir, "known_indices")
    known_indices: list[JSON] = []
    if os.path.exists(known_indices_dir):
        for fname in os.listdir(known_indices_dir):
            if fname.endswith(".json"):
                with open(os.path.join(known_indices_dir, fname)) as f:
                    known_indices.append(_json_loads(f.read()))

    network = _json_str(config.get("network"), SELECTED_NETWORK or "preprod")
    testnet_magic = "1" if network == "preprod" else "2" if network == "preview" else ""

    checkpoint: dict[str, JSON] = {
        "type": "CardanoInterface Wallet Checkpoint",
        "version": 1,
        "created": datetime.now(UTC).isoformat(),
        "network": network,
        "script": {
            "cbor_hex": script_cbor_hex,
            "hash": script_hash_bytes.hex(),
            "address": script_address,
            "json": script_dict,
        },
        "config": {
            "name": config.get("name", os.path.basename(wallet_dir)),
            "network": network,
            "threshold": wallet["threshold"],
            "num_cosigners": len(wallet["key_hashes"]),
            "percentage": config.get("percentage", 100),
            "key_hashes": [kh.hex() for kh in wallet["key_hashes"]],
            "labels": {kh.hex(): name for kh, name in wallet["labels"].items()},
            "script_type": config.get("script_type"),
            "provenance": wallet["provenance"],
            "staking": wallet["staking"],
            "not_before_slot": wallet["not_before_slot"],
            "not_after_slot": wallet["not_after_slot"],
            "admin_cosigner_index": wallet_admin_index(wallet),
            # Deliberately not exported: claimed_signer_index and signer_wallet.
            # Which cosigner a node is, and which of its wallets signs, are facts
            # about that machine — the importing node works its own out.
        },
        "known_indices": known_indices,
        "admin_signature": None,
        "instructions": {
            "cardano_interface": (
                "To restore this wallet:\n"
                "1. Open CardanoInterface\n"
                "2. Multisig Wallets → Recover or import\n"
                "3. Select this file\n"
                "4. The script hash and address are verified against the script CBOR\n"
                "5. The threshold and cosigners are read back out of the script\n"
                "6. The administrator designation is applied only if this file\n"
                "   carries that administrator's signature over it\n\n"
                "To sign transactions:\n"
                "1. Multisig Wallets → Sign Transaction\n"
                "2. Select the restored wallet\n"
                "3. Paste unsigned CBOR or browse sessions\n"
                "4. Review and sign with your wallet password"
            ),
            "cardano_cli": (
                f"Script address: {script_address}\n"
                f"Network: {network} (testnet-magic {testnet_magic})\n"
                f"Threshold: {config.get('threshold', 1)} of {config.get('num_cosigners', 1)}\n\n"
                f"# Query UTxOs at the script address:\n"
                f"cardano-cli query utxo --address {script_address} "
                f"--testnet-magic {testnet_magic}\n\n"
                f"# Build a transaction spending from the script:\n"
                f"cardano-cli transaction build \\\n"
                f"  --tx-in <txhash>#<ix> \\\n"
                f"  --tx-out <recipient>+<lovelace> \\\n"
                f"  --tx-in-script-file script.json \\\n"
                f"  --tx-in-collateral <collateral-txhash>#<collateral-ix> \\\n"
                f"  --out-file tx.body \\\n"
                f"  --testnet-magic {testnet_magic}\n\n"
                f"# Extract script.json from this package's script.json field above.\n"
                f"# Sign with each cosigner's payment key:\n"
                f"cardano-cli transaction sign \\\n"
                f"  --tx-body-file tx.body \\\n"
                f"  --signing-key-file cosigner0.skey \\\n"
                f"  --signing-key-file cosigner1.skey \\\n"
                f"  --out-file tx.signed \\\n"
                f"  --testnet-magic {testnet_magic}\n\n"
                f"# Submit:\n"
                f"cardano-cli transaction submit --tx-file tx.signed "
                f"--testnet-magic {testnet_magic}"
            ),
            "general": (
                "This file contains a Cardano multisig wallet checkpoint.\n"
                "The script CBOR is the wallet's immutable identity — its hash is\n"
                "the wallet's address. The config section carries off-chain policy\n"
                "the script cannot hold: cosigner names, provenance, and which\n"
                "cosigner administers the wallet. On import, the script hash and\n"
                "address are re-derived and checked, the threshold and cosigners are\n"
                "read from the script itself, and the administrator designation is\n"
                "accepted only when admin_signature verifies against it.\n"
                "No private key, mnemonic or extended public key is in this file."
            ),
        },
    }

    if signer_wallet_dir and password is not None:
        checkpoint["admin_signature"] = sign_wallet_checkpoint(
            checkpoint, signer_wallet_dir, password)

    logging.debug("[EXIT] export_wallet_checkpoint")
    return checkpoint


def export_transaction_package(wallet_dir: str, session_dir: str,
                               stage: str) -> dict[str, JSON]:
    """Export a transaction package (JSON) for exchange between cosigners.

    Bundles the transaction CBOR with the script it spends from, transaction
    summary, and instructions for CardanoInterface and cardano-cli.

    Args:
        wallet_dir: The multisig wallet directory.
        session_dir: The session directory containing the transaction CBOR.
        stage: One of "unsigned", "partial", or "final".

    Returns the transaction package dict.
    """
    logging.debug(f"[ENTRY] export_transaction_package(stage={stage})")

    wallet = _load_multisig_wallet(wallet_dir)
    config = wallet["config"]
    script_hash_bytes = wallet["script_hash"]
    script_address = wallet["script_address"]

    script_cbor_path = os.path.join(wallet_dir, "script.cbor")
    with open(script_cbor_path) as f:
        script_cbor_hex = f.read().strip()

    if stage == "unsigned":
        cbor_path = os.path.join(session_dir, "unsigned.cbor")
    elif stage == "partial":
        cbor_path = os.path.join(session_dir, "partial.cbor")
    elif stage == "final":
        cbor_path = os.path.join(session_dir, "final.cbor")
    else:
        raise ValueError(f"Invalid stage: {stage}. Must be unsigned, partial, or final.")

    with open(cbor_path) as f:
        tx_cbor_hex = f.read().strip()

    tx = Transaction.from_cbor(tx_cbor_hex)
    tx_body = tx.transaction_body

    tx_id = tx_body.id.payload.hex()
    fee_lovelace = tx_body.fee

    inputs: list[dict[str, JSON]] = []
    for inp in _tx_body_inputs(tx_body):
        inputs.append({"tx_hash": inp.transaction_id.payload.hex(), "index": inp.index})

    outputs: list[dict[str, JSON]] = []
    for out in tx_body.outputs:
        addr_str = str(out.address)
        lovelace = out.amount.coin
        outputs.append({"address": addr_str, "lovelace": lovelace})

    required_signers = [kh.payload.hex() for kh in _tx_required_signers(tx_body)]

    signatures_collected: list[str] = []
    if tx.transaction_witness_set and tx.transaction_witness_set.vkey_witnesses:
        for w in _witness_vkeys(tx.transaction_witness_set):
            signatures_collected.append(key_hash_from_vkey(w.vkey.payload).hex())

    threshold = _json_int(config.get("threshold"), 1)
    signatures_needed = max(0, threshold - len(signatures_collected))

    network = _json_str(config.get("network"), SELECTED_NETWORK or "preprod")
    testnet_magic = "1" if network == "preprod" else "2" if network == "preview" else ""

    text_envelope_type = ("Witnessed Tx BabbageEra" if stage == "final"
                          else "Unwitnessed Tx BabbageEra")

    text_envelope: dict[str, JSON] = {
        "type": text_envelope_type,
        "description": "Ledger Cddl Format",
        "cborHex": tx_cbor_hex,
    }

    package: dict[str, JSON] = {
        "type": "CardanoInterface Transaction Package",
        "version": 1,
        "created": datetime.now(UTC).isoformat(),
        "network": network,
        "script": {
            "cbor_hex": script_cbor_hex,
            "hash": script_hash_bytes.hex(),
            "address": script_address,
        },
        "transaction": {
            "stage": stage,
            "cbor_hex": tx_cbor_hex,
            "tx_id": tx_id,
            "fee_lovelace": fee_lovelace,
            "inputs": [i for i in inputs],
            "outputs": [o for o in outputs],
            "required_signers": [r for r in required_signers],
            "signatures_collected": [s for s in signatures_collected],
            "threshold": threshold,
            "signatures_needed": signatures_needed,
        },
        "text_envelope": text_envelope,
        "instructions": {
            "cardano_interface": (
                "To sign this transaction:\n"
                "1. Open CardanoInterface\n"
                "2. Multisig Wallets → Sign Transaction\n"
                "3. Select your multisig wallet\n"
                "4. Paste the transaction cbor_hex or import this file\n"
                "5. Review inputs, outputs, fee, and destination\n"
                "6. Confirm with 'y' and enter your wallet password\n\n"
                "To assemble collected signatures:\n"
                "1. Collect partial packages from all required cosigners\n"
                "2. Multisig Wallets → Assemble & Submit\n"
                "3. Select the wallet and session\n"
                "4. Assembly verifies each signature against the body hash\n"
                "5. On threshold met, submit to the network"
            ),
            "cardano_cli": (
                f"# This package's text_envelope.cborHex is cardano-cli compatible.\n"
                f"# Sign with your payment key:\n"
                f"cardano-cli transaction sign \\\n"
                f"  --tx-body-file <(echo '{json.dumps(text_envelope)}') \\\n"
                f"  --signing-key-file payment.skey \\\n"
                f"  --out-file partial.signed \\\n"
                f"  --testnet-magic {testnet_magic}\n\n"
                f"# Return partial.signed to the operator for assembly.\n"
                f"# To submit a fully-signed transaction:\n"
                f"cardano-cli transaction submit --tx-file final.signed "
                f"--testnet-magic {testnet_magic}"
            ),
            "general": (
                f"Transaction stage: {stage} ({len(signatures_collected)} signatures collected).\n"
                f"Threshold: {threshold} of {config.get('num_cosigners', 1)} cosigners required.\n"
                f"Script address: {script_address}\n"
                f"Every cosigner's signature is over the transaction body hash.\n"
                f"Assembly verifies each signature against the body hash before "
                f"counting toward threshold."
            ),
        },
    }

    logging.debug(f"[EXIT] export_transaction_package(stage={stage})")
    return package


def classify_cbor(cbor_hex: str) -> str:
    """Say what a bare CBOR hex string actually is.

    Recovery inputs arrive as opaque hex with no label — from a dead provider,
    a block explorer, or another cosigner — and asking the user to know which
    kind they hold is asking the wrong person. Returns one of
    "native_script", "witnessed_tx", "unsigned_tx", or "unknown".
    """
    cleaned = cbor_hex.strip().lower().removeprefix("0x")
    try:
        bytes.fromhex(cleaned)
    except ValueError:
        return "unknown"

    # A native script is the smaller, stricter grammar, so it is tried first: a
    # transaction never parses as one.
    try:
        NativeScript.from_cbor(cleaned)
        return "native_script"
    except Exception:
        pass

    try:
        tx = Transaction.from_cbor(cleaned)
    except Exception:
        return "unknown"

    witness_set = tx.transaction_witness_set
    if witness_set is not None and witness_set.vkey_witnesses:
        return "witnessed_tx"
    return "unsigned_tx"


def import_package(json_path: str, current_user: str, user_password: str) -> dict[str, JSON]:
    """Import a CBOR Package (checkpoint or transaction) from a JSON file.

    Auto-detects package type and routes to appropriate flow:
    - Wallet Checkpoint: restores wallet state including admin designation
    - Transaction Package: imports unsigned/partial/final transaction into a session

    Returns a dict with import results.
    """
    logging.debug(f"[ENTRY] import_package(json_path={json_path})")

    with open(json_path) as f:
        package = _json_object(_json_loads(f.read()))

    pkg_type = _json_str(package.get("type"))

    if pkg_type == "CardanoInterface Wallet Checkpoint":
        return _import_wallet_checkpoint(package, current_user, user_password)
    elif pkg_type == "CardanoInterface Transaction Package":
        return _import_transaction_package(package, current_user, user_password)
    else:
        raise ValueError(
            f"Unknown package type: {pkg_type}. "
            f"Expected 'CardanoInterface Wallet Checkpoint' or "
            f"'CardanoInterface Transaction Package'."
        )


def _import_wallet_checkpoint(package: dict[str, JSON], current_user: str,
                             user_password: str) -> dict[str, JSON]:
    """Import a wallet checkpoint package."""
    logging.debug("[ENTRY] _import_wallet_checkpoint")

    network = _json_str(package.get("network"), SELECTED_NETWORK or "preprod")
    if network != (SELECTED_NETWORK or "preprod"):
        raise ValueError(
            f"Checkpoint is for {network}, but you are on "
            f"{SELECTED_NETWORK or 'preprod'}. Wallets are network-specific."
        )

    script_obj = _json_object(package.get("script"))
    script_cbor_hex = _json_str(script_obj.get("cbor_hex"))
    script_hash_expected = _json_str(script_obj.get("hash"))
    script_address_expected = _json_str(script_obj.get("address"))

    script: NativeScript = NativeScript.from_cbor(script_cbor_hex)
    script_hash_bytes = script_hash_from_script(script)
    script_hash_actual = script_hash_bytes.hex()

    if script_hash_actual != script_hash_expected:
        raise ValueError(
            f"Checkpoint script CBOR does not match its hash. "
            f"File may be corrupt or tampered.\n"
            f"Expected: {script_hash_expected}\n"
            f"Actual: {script_hash_actual}"
        )

    # The address is checked against both shapes the same script can take: with
    # and without a stake credential. Insisting on one of them would reject a
    # perfectly good checkpoint for a delegating wallet.
    candidates = script_address_candidates(script_hash_bytes)
    if script_address_expected not in candidates.values():
        raise ValueError(
            f"Checkpoint script address does not belong to its script. "
            f"File may be corrupt or tampered.\n"
            f"In the file: {script_address_expected}\n"
            f"From the script: {' or '.join(candidates.values())}"
        )
    script_address_actual = script_address_expected
    staking = script_address_actual != candidates["enterprise (no staking)"]

    config = _json_object(package.get("config"))
    wallet_name = _json_str(config.get("name"), f"restored_{script_hash_actual[:8]}")
    wallet_dir = secure_path_join(WALLET_DIR, wallet_name)

    if os.path.exists(wallet_dir):
        existing_hash_path = os.path.join(wallet_dir, "script_hash")
        if os.path.exists(existing_hash_path):
            with open(existing_hash_path) as f:
                existing_hash = f.read().strip()
            if existing_hash != script_hash_actual:
                raise ValueError(f"A different wallet already exists at {wallet_name}.")
        console.print(
            f"[yellow]Wallet '{wallet_name}' already exists. "
            f"Restoring config from checkpoint.[/yellow]"
        )
    else:
        os.makedirs(wallet_dir, exist_ok=True)

    wallet_type = "multisig"
    # The script is the authority on who signs and how many are needed, so the
    # restored config takes those from it rather than from the package's own
    # numbers — a checkpoint whose config disagrees with its script cannot talk
    # this node into a lower threshold.
    restored_key_hashes, restored_threshold, restored_type = (
        extract_key_hashes_from_script(script)
    )
    if restored_threshold is None:
        restored_threshold = len(restored_key_hashes)
    config["threshold"] = script_min_signatures(script)
    config["num_cosigners"] = len(restored_key_hashes)
    config["key_hashes"] = [kh.hex() for kh in restored_key_hashes]
    config["script_type"] = restored_type
    config["staking"] = staking
    config["provenance"] = _json_str(config.get("provenance") or "restored_package")
    config["claimed_signer_index"] = None

    # An administrator designation is a claim about who may act, written in a
    # file anyone along the way could edit. It is applied only when the person
    # it names has signed the checkpoint; otherwise the wallet restores ungated,
    # which is the safe failure — every cosigner can still do everything, and
    # nobody has been handed authority they did not prove.
    admin_verified, admin_reason = verify_checkpoint_admin_signature(package)
    if config.get("admin_cosigner_index") is None:
        console.print("[dim]No administrator designated: any cosigner can start a "
                      "spend.[/dim]")
    elif admin_verified:
        console.print(f"[green]Administrator designation verified — {admin_reason}."
                      "[/green]")
    else:
        claimed_admin = config.pop("admin_cosigner_index")
        console.print("\n[bold yellow]The administrator designation in this "
                      "checkpoint was not accepted.[/bold yellow]")
        console.print(f"  It names cosigner "
                      f"{_json_int(claimed_admin, -1) + 1}, but {admin_reason}.")
        console.print("[dim]The wallet is restored with no administrator, so every "
                      "cosigner can act.[/dim]")
        console.print("[dim]Ask the administrator to export the checkpoint again "
                      "from their own copy —[/dim]")
        console.print("[dim]theirs will be signed, and this program will then honour "
                      "it.[/dim]")

    with open(os.path.join(wallet_dir, "type"), "w") as f:
        f.write(wallet_type)

    with open(os.path.join(wallet_dir, "network.txt"), "w") as f:
        f.write(network)

    with open(os.path.join(wallet_dir, "script.cbor"), "w") as f:
        f.write(script_cbor_hex)

    _dump_json(script_obj.get("json"), os.path.join(wallet_dir, "script.json"))

    with open(os.path.join(wallet_dir, "script_hash"), "w") as f:
        f.write(script_hash_actual)

    with open(os.path.join(wallet_dir, "script_address"), "w") as f:
        f.write(script_address_actual)

    _dump_json(config, os.path.join(wallet_dir, "config.json"))

    known_indices_dir = os.path.join(wallet_dir, "known_indices")
    os.makedirs(known_indices_dir, exist_ok=True)
    for idx_data in _json_list(package.get("known_indices")):
        idx = _json_int(_json_object(idx_data).get("index"))
        _dump_json(idx_data, os.path.join(known_indices_dir, f"{idx}.json"))

    os.makedirs(os.path.join(wallet_dir, "sessions"), exist_ok=True)

    user_data = load_user_data(current_user, user_password)
    if user_data is None:
        raise EncryptionError("Could not load user data to register the restored wallet.")
    wallet_entry: dict[str, JSON] = {
        "name": wallet_name,
        "address": script_address_actual,
        "type": wallet_type,
        "threshold": f"{_json_int(config.get('threshold'), 1)} of"
                     f" {_json_int(config.get('num_cosigners'), 1)}",
    }
    wallets = _json_list(user_data.get("wallets"))
    if wallet_name not in [_wallet_entry_name(w) for w in wallets]:
        wallets.append(wallet_entry)
        user_data["wallets"] = wallets
        save_user_data(current_user, user_data, user_password)

    console.print(f"\n[bold green]Wallet '{wallet_name}' restored from checkpoint![/bold green]")
    console.print(f"  Address: [cyan]{script_address_actual}[/cyan]")
    console.print(f"  Threshold: {config.get('threshold', 1)} of {config.get('num_cosigners', 1)}")
    console.print(f"  Staking: {'yes' if staking else 'no'}")
    if config.get("admin_cosigner_index") is not None:
        console.print(f"  Admin: cosigner {_json_int(config['admin_cosigner_index']) + 1}"
                      " (signature verified)")
    else:
        console.print("  Admin: not designated (any cosigner can initiate)")

    logging.debug("[EXIT] _import_wallet_checkpoint")
    return {
        "wallet_dir": wallet_dir,
        "wallet_name": wallet_name,
        "script_hash": script_hash_actual,
        "script_address": script_address_actual,
        "admin_cosigner_index": config.get("admin_cosigner_index"),
    }


def _import_transaction_package(package: dict[str, JSON], current_user: str,
                               user_password: str) -> dict[str, JSON]:
    """Import a transaction package into a session."""
    logging.debug("[ENTRY] _import_transaction_package")

    network = _json_str(package.get("network"), SELECTED_NETWORK or "preprod")
    if network != (SELECTED_NETWORK or "preprod"):
        raise ValueError(f"Transaction is for {network}, but you are on"
                         f" {SELECTED_NETWORK or 'preprod'}.")

    script_hash_hex = _json_str(_json_object(package.get("script")).get("hash"))
    tx_obj = _json_object(package.get("transaction"))
    tx_stage = _json_str(tx_obj.get("stage"))
    tx_cbor_hex = _json_str(tx_obj.get("cbor_hex"))
    tx_id = _json_str(tx_obj.get("tx_id"))

    user_data = load_user_data(current_user, user_password)
    if user_data is None:
        raise EncryptionError("Could not load user data to register the transaction.")
    wallets = _json_list(user_data.get("wallets"))
    multisig_wallets = [w for w in wallets if isinstance(w, dict)
                        and _json_str(w.get("type", "")).startswith("multisig")]

    matching_wallet: dict[str, JSON] | None = None
    for w in multisig_wallets:
        wallet_dir = secure_path_join(WALLET_DIR, _json_str(w.get("name")))
        hash_path = os.path.join(wallet_dir, "script_hash")
        if os.path.exists(hash_path):
            with open(hash_path) as f:
                w_hash = f.read().strip()
            if w_hash == script_hash_hex:
                matching_wallet = w
                break

    if matching_wallet is None:
        raise ValueError(f"No multisig wallet found for script hash"
                         f" {script_hash_hex[:16]}...."
                         " Import the wallet checkpoint first.")

    wallet_dir = secure_path_join(WALLET_DIR, _json_str(matching_wallet.get("name")))
    session_id = tx_id[:12]
    session_dir = os.path.join(wallet_dir, "sessions", session_id)
    os.makedirs(session_dir, exist_ok=True)

    if tx_stage == "unsigned":
        with open(os.path.join(session_dir, "unsigned.cbor"), "w") as f:
            f.write(tx_cbor_hex)
        _dump_json(package.get("text_envelope"),
                   os.path.join(session_dir, "unsigned_text_envelope.json"))
    elif tx_stage == "partial":
        with open(os.path.join(session_dir, "partial.cbor"), "w") as f:
            f.write(tx_cbor_hex)
        _dump_json(package.get("text_envelope"),
                   os.path.join(session_dir, "partial_text_envelope.json"))
    elif tx_stage == "final":
        with open(os.path.join(session_dir, "final.cbor"), "w") as f:
            f.write(tx_cbor_hex)
        _dump_json(package.get("text_envelope"),
                   os.path.join(session_dir, "final_text_envelope.json"))

    summary: dict[str, JSON] = {
        "tx_id": tx_id,
        "stage": tx_stage,
        "fee_lovelace": tx_obj.get("fee_lovelace"),
        "inputs": tx_obj.get("inputs"),
        "outputs": tx_obj.get("outputs"),
        "threshold": tx_obj.get("threshold"),
        "signatures_collected": tx_obj.get("signatures_collected"),
        "signatures_needed": tx_obj.get("signatures_needed"),
    }
    _dump_json(summary, os.path.join(session_dir, "summary.json"))

    console.print("\n[bold green]Transaction imported into session![/bold green]")
    console.print(f"  Wallet: {_json_str(matching_wallet.get('name'))}")
    console.print(f"  Session: {session_id}")
    console.print(f"  Stage: {tx_stage}")
    console.print(f"  Signatures: {len(_json_list(tx_obj.get('signatures_collected')))}"
        f" of {_json_int(tx_obj.get('threshold'))}")
    console.print("\n[bold]Next steps:[/bold]")
    if tx_stage == "unsigned":
        console.print("  → Sign Transaction to add your signature")
    elif tx_stage == "partial":
        console.print("  → Sign Transaction to add your signature, or")
        console.print("  → Assemble & Submit if threshold is met")
    elif tx_stage == "final":
        console.print("  → Assemble & Submit to broadcast to the network")

    logging.debug("[EXIT] _import_transaction_package")
    return {
        "wallet_dir": wallet_dir,
        "session_dir": session_dir,
        "session_id": session_id,
        "stage": tx_stage,
    }


@exception_error
def import_partial_signature(wallet_dir: str, session_dir: str,
                             source: str) -> dict[str, JSON] | None:
    """Take in one cosigner's signed partial and file it against a session.

    This is how signatures travel between people. A cosigner returns their
    partial as CBOR hex, a TextEnvelope, or a transaction package — whatever
    their tool produced — and the initiator drops it in here.

    The signature is checked against the session's own unsigned body before it
    is stored, so a partial for a different transaction is refused at the point
    of import rather than silently ignored at assembly.

    Args:
        wallet_dir: The multisig wallet.
        session_dir: The session this signature belongs to.
        source: CBOR hex, a path to a file, or JSON text holding either.

    Returns a dict describing which cosigner was added and the running total.
    """
    logging.debug(f"[ENTRY] import_partial_signature(session={session_dir})")

    cbor_hex = _extract_transaction_cbor(source)
    if not cbor_hex:
        raise ValueError(
            "Could not find a transaction in that input. Paste the CBOR hex, "
            "or give the path to a partial .cbor / TextEnvelope / package file."
        )

    unsigned_path = os.path.join(session_dir, "unsigned.cbor")
    if not os.path.exists(unsigned_path):
        raise FileNotFoundError(f"This session has no unsigned transaction: {unsigned_path}")
    with open(unsigned_path) as f:
        unsigned_tx = Transaction.from_cbor(f.read().strip())
    body_hash = unsigned_tx.transaction_body.hash()

    wallet = _load_multisig_wallet(wallet_dir)
    script_key_hashes, cosigner_names, threshold = _multisig_signer_directory(
        wallet, unsigned_tx
    )

    partial_tx = Transaction.from_cbor(cbor_hex)
    if partial_tx.transaction_body.hash() != body_hash:
        raise ValueError(
            "That signature is for a different transaction than this session's. "
            "Check that the cosigner signed the transaction you sent them."
        )

    ws = partial_tx.transaction_witness_set
    witnesses = _witness_vkeys(ws) if ws else []
    if not witnesses:
        raise ValueError("That file contains no signature.")

    added: list[str] = []
    rejected: list[str] = []
    for w in witnesses:
        kh = key_hash_from_vkey(w.vkey.payload)
        name = cosigner_names.get(kh, kh.hex()[:16])
        if kh not in script_key_hashes:
            rejected.append(f"{kh.hex()[:16]}... (not a cosigner of this wallet)")
            continue
        if not _verify_witness(w, body_hash):
            rejected.append(f"{name} (signature does not match this transaction)")
            continue
        single = Transaction(
            unsigned_tx.transaction_body,
            TransactionWitnessSet(
                native_scripts=unsigned_tx.transaction_witness_set.native_scripts,
                vkey_witnesses=[w],
            ),
        )
        with open(os.path.join(session_dir, f"partial_{kh.hex()[:16]}.cbor"), "w") as f:
            f.write(single.to_cbor().hex())
        added.append(name)

    collected = _collected_signature_hashes(session_dir)
    _write_session_status(session_dir, collected, threshold)

    for name in added:
        console.print(f"  [green]✓ Added signature from {name}[/green]")
    for reason in rejected:
        console.print(f"  [red]✗ Rejected: {reason}[/red]")
    if not added:
        raise ValueError("No usable signature in that input.")

    console.print(f"\n  Signatures collected: {len(collected)} of {threshold}")
    if len(collected) >= threshold:
        console.print("  [bold green]Threshold reached — ready to Assemble & Submit.[/bold green]")
    else:
        console.print(f"  Still need {threshold - len(collected)} more.")

    logging.debug("[EXIT] import_partial_signature")
    return {
        "added": [a for a in added],
        "rejected": [r for r in rejected],
        "signatures_collected": len(collected),
        "threshold": threshold,
        "session_dir": session_dir,
    }


def _extract_transaction_cbor(source: str) -> str | None:
    """Pull transaction CBOR hex out of whatever the user actually pasted.

    Cosigners exchange signatures in whichever shape their tool emits, so this
    accepts a file path, raw hex, a cardano-cli TextEnvelope, or one of this
    program's transaction packages rather than making the user convert first.
    """
    text = (source or "").strip()
    if not text:
        return None

    if os.path.isfile(text):
        with open(text) as f:
            text = f.read().strip()

    if text.startswith("{"):
        try:
            parsed = _json_loads(text)
        except json.JSONDecodeError:
            return None
        data = _json_object(parsed)
        for candidate in (
            data.get("cborHex"),
            _json_object(data.get("transaction")).get("cbor_hex"),
            _json_object(data.get("text_envelope")).get("cborHex"),
        ):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return None

    compact = "".join(text.split())
    try:
        bytes.fromhex(compact)
    except ValueError:
        return None
    return compact


@exception_error
def assemble_multisig_transaction(wallet_dir: str,
                                 session_dir: str) -> dict[str, JSON] | None:
    """Merge partial signatures from cosigners and assemble the final signed transaction.

    Returns dict with final_cbor_hex, submitted tx_id.
    """
    logging.debug(f"[ENTRY] assemble_multisig_transaction(wallet_dir={wallet_dir})")

    if not context:
        raise ValueError("No backend configured.")

    wallet = _load_multisig_wallet(wallet_dir)

    unsigned_path = os.path.join(session_dir, "unsigned.cbor")
    if not os.path.exists(unsigned_path):
        raise FileNotFoundError(f"Unsigned transaction not found: {unsigned_path}")

    with open(unsigned_path) as f:
        unsigned_cbor_hex = f.read().strip()

    unsigned_tx = Transaction.from_cbor(unsigned_cbor_hex)
    tx_body = unsigned_tx.transaction_body
    body_hash = tx_body.hash()

    script_key_hashes, cosigner_names, threshold = _multisig_signer_directory(
        wallet, unsigned_tx
    )

    merged_witnesses: list[VerificationKeyWitness] = []
    signed_key_hashes: set[bytes] = set()

    partial_files = _partial_signature_files(session_dir)
    if not partial_files:
        raise ValueError(
            "No partial signatures in this session yet. Collect signed partials "
            "from the cosigners and import them first."
        )

    console.print("\n[bold bright_cyan]╔══ MULTISIG ASSEMBLY ══╗[/bold bright_cyan]")
    console.print("[bold bright_cyan]║[/bold bright_cyan]  Collecting signatures and verifying "
        "threshold.")
    console.print("[bold bright_cyan]╚═══════════════════════╝[/bold bright_cyan]")
    console.print(f"  Found {len(partial_files)} partial signature file(s)")

    for pf in partial_files:
        with open(os.path.join(session_dir, pf)) as f:
            partial_cbor_hex = f.read().strip()

        try:
            partial_tx = Transaction.from_cbor(partial_cbor_hex)
        except Exception as e:
            console.print(f"  [red]✗ {pf}: not a readable transaction ({e})[/red]")
            continue

        partial_witness = partial_tx.transaction_witness_set
        if partial_witness is None or not partial_witness.vkey_witnesses:
            console.print(f"  [yellow]○ {pf}: contains no signature[/yellow]")
            continue

        for vk_w in _witness_vkeys(partial_witness):
            kh = key_hash_from_vkey(vk_w.vkey.payload)
            name = cosigner_names.get(kh)

            # Two independent checks, both required. Membership answers "is this
            # key allowed to sign this wallet"; verification answers "did this
            # key actually sign THIS body". Membership alone would let a witness
            # copied from a different transaction count toward the threshold.
            if kh not in script_key_hashes:
                console.print(
                    f"  [red]✗ {pf}: key {kh.hex()[:16]}... is not a cosigner of this script[/red]"
                )
                continue
            if not _verify_witness(vk_w, body_hash):
                console.print(
                    f"  [red]✗ {name}: signature does not match this"
                    " transaction body — rejected[/red]"
                )
                console.print(
                    "     [dim]The partial was signed against a different transaction.[/dim]"
                )
                continue
            if kh in signed_key_hashes:
                console.print(f"  [yellow]○ {name}: duplicate signature skipped[/yellow]")
                continue

            merged_witnesses.append(vk_w)
            signed_key_hashes.add(kh)
            console.print(f"  [green]✓ {name}: signature verified against the "
                "transaction body[/green]")

    console.print(f"\n  [bold]Threshold:[/bold] {threshold} of {len(script_key_hashes)}")
    console.print(f"  [bold]Valid signatures:[/bold] {len(signed_key_hashes)}")

    if len(signed_key_hashes) < threshold:
        missing = [cosigner_names.get(kh, kh.hex()[:16])
                   for kh in script_key_hashes if kh not in signed_key_hashes]
        console.print(
            f"[red]Not enough signatures yet ({len(signed_key_hashes)} of {threshold}).[/red]"
        )
        console.print(f"  Still waiting on any {threshold - len(signed_key_hashes)} of: "
                      + ", ".join(missing))
        _write_session_status(session_dir, signed_key_hashes, threshold)
        return None

    final_witness = TransactionWitnessSet(
        native_scripts=unsigned_tx.transaction_witness_set.native_scripts,
        vkey_witnesses=merged_witnesses,
    )

    final_tx = Transaction(tx_body, final_witness)
    final_cbor_hex = final_tx.to_cbor().hex()

    envelope: dict[str, JSON] = {
        "type": "Witnessed Tx BabbageEra",
        "description": "CardanoInterface Multisig Fully Signed Transaction",
        "cborHex": final_cbor_hex,
    }
    text_envelope = _dumps_json(envelope)

    with open(os.path.join(session_dir, "final.cbor"), "w") as f:
        f.write(final_cbor_hex)
    with open(os.path.join(session_dir, "final_text_envelope.json"), "w") as f:
        f.write(text_envelope)

    # ═══ SHOW COMPLETION STATE ═══════════════════════════════════════
    console.print("\n[bold bright_green]╔══ TRANSACTION FULLY SIGNED ══╗[/bold bright_green]")
    console.print("[bold bright_green]║[/bold bright_green]  All required signatures collected.")
    console.print("[bold bright_green]╚════════════════════════════╝[/bold bright_green]")

    console.print(f"\n  [bold]Transaction ID:[/bold] {tx_body.id.payload.hex()}")
    console.print(f"  [bold]Fee:[/bold] {format_ada(tx_body.fee)} ADA")
    console.print("  [bold]Signed by:[/bold]")
    for kh in signed_key_hashes:
        console.print(f"    [green]✓ {cosigner_names.get(kh, kh.hex()[:16])}[/green]")
    console.print(f"\n  [bold]Final CBOR:[/bold] [cyan]{session_dir}/final.cbor[/cyan]")

    submitted_tx_id: str | None = None
    console.print("\n[bold]Submit this transaction to the network? (y/n):[/bold]")
    console.print("[dim]This will broadcast the fully-signed transaction. It cannot be "
        "undone.[/dim]")
    confirm = session.prompt("> ").strip().lower()
    if confirm == "y":
        try:
            submitted_tx_id = context.submit_tx(final_cbor_hex)
            console.print("[bold green]✓ TRANSACTION SUBMITTED[/bold green]")
            console.print(f"  TX ID: {submitted_tx_id}")
            _write_session_status(session_dir, signed_key_hashes, threshold,
                                  status="submitted", tx_id=submitted_tx_id)
        except Exception as e:
            console.print(f"[red]Submission rejected by the network: {e}[/red]")
            console.print("[yellow]The signed CBOR is saved — you can retry submission "
                          "or submit it with cardano-cli.[/yellow]")
            _write_session_status(session_dir, signed_key_hashes, threshold,
                                  status="submission_failed", error=str(e))
    else:
        console.print("[yellow]Not submitted. Final CBOR saved for manual submission.[/yellow]")
        _write_session_status(session_dir, signed_key_hashes, threshold,
                              status="assembled_not_submitted")

    if submitted_tx_id:
        console.print("\n[bold]What happens next:[/bold]")
        console.print("  The transaction is in the mempool. Once it is in a block, "
                      "'Check balance' will show the new balance.")
        console.print("  This wallet stays usable for further spends from the same script.")

    logging.debug("[EXIT] assemble_multisig_transaction")
    return {
        "final_cbor_hex": final_cbor_hex,
        "session_dir": session_dir,
        "signatures": len(signed_key_hashes),
        "tx_id": submitted_tx_id,
    }


@exception_error
def scan_multisig_addresses(wallet_dir: str) -> dict[str, JSON]:
    """Read the wallet's on-chain balance from its script address.

    A multisig wallet has exactly one address, because the script hash is the
    payment credential and the script does not vary. There is no address family
    to discover and no gap to scan: one query answers the whole question.

    Returns the UTxOs found, the total lovelace, and any native tokens.
    """
    logging.debug(f"[ENTRY] scan_multisig_addresses(wallet_dir={wallet_dir})")

    if not context:
        raise ValueError("No backend configured. Please set up a backend first.")

    wallet = _load_multisig_wallet(wallet_dir)
    address = wallet["script_address"]

    console.print(f"[bold]Reading balance at[/bold] [cyan]{address}[/cyan]")

    utxos = context.utxos(address)

    total_lovelace = 0
    total_assets: dict[tuple[str, str], int] = {}
    utxo_records: list[JSON] = []

    for utxo in utxos:
        lovelace = utxo.output.amount.coin
        total_lovelace += lovelace
        utxo_records.append({
            "tx_hash": utxo.input.transaction_id.payload.hex(),
            "tx_index": utxo.input.index,
            "address": str(utxo.output.address),
            "lovelace": lovelace,
        })
        if utxo.output.amount.multi_asset:
            for policy_bytes, assets in _multi_asset_items(utxo.output.amount.multi_asset):
                for asset_bytes, amount in assets:
                    key = (policy_bytes.hex(), _asset_display_name(asset_bytes))
                    total_assets[key] = total_assets.get(key, 0) + amount

    record: dict[str, JSON] = {
        "address": address,
        "utxos": utxo_records,
        "lovelace": total_lovelace,
        "assets": {f"{k[0]}:{k[1]}": v for k, v in total_assets.items()},
    }
    known_indices_dir = os.path.join(wallet_dir, "known_indices")
    os.makedirs(known_indices_dir, exist_ok=True)
    _dump_json(record, os.path.join(known_indices_dir, "0.json"))

    if utxos:
        console.print(f"\n[bold green]{format_ada(total_lovelace)} ADA[/bold green] "
                      f"across {len(utxos)} UTxO(s)")
        for u in utxos:
            console.print(f"    {format_ada(u.output.amount.coin)} ADA  "
                          f"[dim]{u.input.transaction_id.payload.hex()[:16]}..."
                          f"#{u.input.index}[/dim]")
    else:
        console.print("\n[yellow]No funds at this address yet.[/yellow]")
        console.print("[dim]If you expect funds here, check you are on the right "
                      "network.[/dim]")
    if total_assets:
        console.print("  [bold]Native tokens:[/bold]")
        for (_policy_id, asset_name), amount in sorted(total_assets.items()):
            console.print(f"    {asset_name or '(no name)'}: {amount}")

    result: dict[str, JSON] = {
        "address": address,
        "utxo_count": len(utxos),
        "total_lovelace": total_lovelace,
        "total_assets": {f"{k[0]}:{k[1]}": v for k, v in total_assets.items()},
    }

    logging.debug("[EXIT] scan_multisig_addresses")
    return result


PROVENANCE_DESCRIPTIONS = {
    "created": "built on this machine",
    "recovered_cbor": "recovered from a script CBOR",
    "recovered_transaction": "rebuilt from a transaction sent to you",
    "restored_package": "restored from a checkpoint",
}


def _slot_description(slot: int) -> str:
    """A slot number, plus the date it falls on when a backend can say.

    Slots mean nothing to the person deciding whether their funds are locked,
    but the conversion needs the current tip, so it degrades to the bare number
    rather than inventing a date offline.
    """
    if not context:
        return f"slot {slot}"
    try:
        when = datetime_for_slot(slot)
    except Exception as e:
        logging.debug(f"Could not date slot {slot}: {e}")
        return f"slot {slot}"
    passed = " — already passed" if when <= datetime.now(UTC) else ""
    return f"{when.strftime('%Y-%m-%d %H:%M')} UTC (slot {slot}){passed}"


def _cosigner_description(wallet: MultisigWalletInfo, index: int) -> str:
    """Name a cosigner once: "cosigner 2" alone, or "cosigner 2 (Treasury)".

    An unlabelled cosigner's fallback name is already its position, so pairing
    the two reads as "cosigner 2 (cosigner 2)".
    """
    label = wallet["labels"].get(wallet["key_hashes"][index], "")
    position = f"cosigner {index + 1}"
    return position if label in ("", position) else f"{position} ({label})"


def _print_multisig_summary(wallet_name: str, wallet: MultisigWalletInfo,
                            ours: set[int]) -> None:
    """The header every multisig view starts with: what this wallet is."""
    console.print(f"\n[bold underline bright_cyan]Multisig Wallet: "
                  f"{wallet_name}[/bold underline bright_cyan]")
    console.print(f"  Address:   [cyan]{wallet['script_address']}[/cyan]")
    flat = script_is_flat_threshold(wallet["script"])
    console.print(f"  Threshold: {'' if flat else 'at least '}{wallet['threshold']} "
                  f"of {len(wallet['key_hashes'])} must sign")
    console.print("  Staking:   "
                  + ("yes — this wallet can delegate"
                     if wallet["staking"] else "no — this wallet cannot delegate"))
    if wallet["not_before_slot"] is not None:
        console.print(f"  Unlocks:   {_slot_description(wallet['not_before_slot'])}")
    if wallet["not_after_slot"] is not None:
        console.print(f"  [yellow]Expires:   "
                      f"{_slot_description(wallet['not_after_slot'])} — "
                      "unspendable after it[/yellow]")

    admin_index = wallet_admin_index(wallet)
    if admin_index is None:
        console.print("  Admin:     none — any cosigner can start a spend")
    else:
        console.print(f"  Admin:     {_cosigner_description(wallet, admin_index)}"
                      + (" (you)" if admin_index in ours else ""))

    origin = PROVENANCE_DESCRIPTIONS.get(wallet["provenance"])
    if origin:
        console.print(f"  [dim]Origin: {origin}[/dim]")
    if ours:
        held = ", ".join(f"cosigner {i + 1}" for i in sorted(ours))
        console.print(f"  [dim]You are {held}[/dim]")
    else:
        console.print("  [dim]No cosigner key of yours is in this script — you can "
                      "watch it, not sign for it[/dim]")


def _print_cosigners(wallet: MultisigWalletInfo, ours: set[int]) -> None:
    """List the script's cosigners by name, hash and whether they are you."""
    console.print("\n[bold]Cosigners named in the script:[/bold]")
    admin_index = wallet_admin_index(wallet)
    for i, key_hash in enumerate(wallet["key_hashes"]):
        marks: list[str] = []
        if i in ours:
            marks.append("[green]you[/green]")
        if i == admin_index:
            marks.append("admin")
        suffix = f"  ({', '.join(marks)})" if marks else ""
        console.print(f"  {_cosigner_description(wallet, i)}{suffix}")
        console.print(f"     [dim]{key_hash.hex()}[/dim]")


def _edit_cosigner_labels(wallet_dir: str, wallet: MultisigWalletInfo) -> None:
    """Name the cosigners of a wallet, so it stops reading as a list of hashes.

    Labels are local. They are never part of the script, never travel to the
    chain, and a cosigner named here on one machine is unnamed on another until
    that machine names them too — which is why every wallet can be labelled,
    recovered ones included.
    """
    while True:
        options = [
            f"{i + 1}. {wallet['labels'].get(key_hash, '')}  "
            f"[dim]{key_hash.hex()[:16]}…[/dim]"
            for i, key_hash in enumerate(wallet["key_hashes"])
        ]
        choice = _prompt_choice("Which cosigner do you want to name?", options,
                                hint="Names are kept on this machine only.")
        if choice is None:
            return
        key_hash = wallet["key_hashes"][choice]
        name = _prompt_cancelable(
            f"A name for cosigner {choice + 1}",
            "Press Enter to clear the name and go back to 'cosigner "
            f"{choice + 1}'.",
        )
        if name is None:
            return

        config = wallet["config"]
        labels = _json_object(config.get("labels"))
        if name:
            labels[key_hash.hex()] = name
        else:
            labels.pop(key_hash.hex(), None)
        config["labels"] = labels
        _dump_json(config, os.path.join(wallet_dir, "config.json"))
        wallet["labels"] = _multisig_labels(config, wallet["key_hashes"])
        console.print(f"[green]Cosigner {choice + 1} is now "
                      f"'{wallet['labels'][key_hash]}'.[/green]")

        console.print("[bold]Name another cosigner? (y/n)[/bold]")
        if not _prompt_yes_no(default=False):
            return


@exception_error
def multisig_view_wallet(current_user: str, user_password: str) -> None:
    """View a multisig wallet: what it is, what it holds, and who signs for it."""
    logging.debug(f"[ENTRY] multisig_view_wallet(user={current_user})")

    wallet_dir = _select_multisig_wallet(current_user, user_password,
                                         "Which multisig wallet?")
    if wallet_dir is None:
        return
    wallet_name = os.path.basename(wallet_dir)

    try:
        wallet = _load_multisig_wallet(wallet_dir)
    except Exception as e:
        console.print(f"[red]Could not open that wallet: {e}[/red]")
        return
    ours = _our_cosigner_indices(current_user, user_password, wallet)

    while True:
        _print_multisig_summary(wallet_name, wallet, ours)
        console.print()
        console.print("  1. Check balance")
        console.print("  2. Cosigners")
        console.print("  3. Name the cosigners")
        console.print("  4. Script details")
        console.print("  5. Export the script (JSON to a file)")
        console.print("  6. Back")

        choice = session.prompt("> ").strip()
        if choice == "1":
            scan_multisig_addresses(wallet_dir)
        elif choice == "2":
            _print_cosigners(wallet, ours)
        elif choice == "3":
            _edit_cosigner_labels(wallet_dir, wallet)
        elif choice == "4":
            script_path = os.path.join(wallet_dir, "script.json")
            if os.path.exists(script_path):
                with open(script_path) as f:
                    console.print(_dumps_json(_json_loads(f.read())))
            else:
                console.print("[red]Script not found.[/red]")
        elif choice == "5":
            export_path = _prompt_export_path(f"{wallet_name}_script.json")
            if export_path:
                script_path = os.path.join(wallet_dir, "script.json")
                with open(script_path) as f:
                    _dump_json(_json_loads(f.read()), export_path)
                console.print(f"[green]Script written to[/green] "
                              f"[cyan]{export_path}[/cyan]")
        elif choice in ("6", "back", "cancel"):
            break
        else:
            console.print("[red]Pick a number from 1 to 6.[/red]")

    logging.debug(f"[EXIT] multisig_view_wallet(user={current_user})")


@exception_error
def show_mnemonic(current_user: str, user_password: str) -> None:
    logging.debug(f"[ENTRY] show_mnemonic(user={current_user})")
    try:
        user_data = load_user_data(current_user, user_password)
        if user_data is None:
            return
        wallets = _json_list(user_data.get("wallets"))
        if not wallets:
            console.print("[yellow]You have no wallets yet.[/yellow]")
            return
        console.print("[bold green]Your wallets:[/bold green]")
        for i, w in enumerate(wallets, 1):
            console.print(f"{i}. {_wallet_entry_name(w)}")
        console.print("[bold]Enter the number of the wallet to show its mnemonic, or type 'cancel'"
            "to go back:[/bold]")
        while True:
            choice = session.prompt("> ").strip()
            if choice.lower() == "cancel":
                return
            if not choice.isdigit() or not (1 <= int(choice) <= len(wallets)):
                console.print("[red]Invalid choice, try again or type 'cancel'.[/red]")
                continue
            wallet_entry = wallets[int(choice) - 1]
            wallet_name = _wallet_entry_name(wallet_entry)
            break
        wallet_dir = secure_path_join(WALLET_DIR, wallet_name)
        mnemonic_path = os.path.join(wallet_dir, "mnemonic.enc")
        if not os.path.exists(mnemonic_path):
            console.print("[red]Mnemonic not found for this wallet.[/red]")
            console.print("[bold]Do you want to generate and save a new mnemonic for this wallet?"
                "(yes/no)[/bold]")
            while True:
                answer = session.prompt("> ").strip().lower()
                if answer in ("yes", "y"):
                    console.print("[bold]Enter your password to encrypt the wallet's keys and "
                        "mnemonic:[/bold]")
                    password = prompt_existing_password()
                    try:
                        mnemonic_phrase = generate_mnemonic()
                        logging.debug(f"[DEBUG-show_mnemonic] Generated new mnemonic for wallet"
                            f"'{wallet_name}'.")
                        console.print("\n[bold green]IMPORTANT: Save your new 24-word mnemonic "
                            "passphrase securely![/bold green]")
                        console.print("[yellow]This mnemonic is the ONLY way to recover "
                            "your wallet if you lose access.[/yellow]")
                        console.print(f"[bold]{mnemonic_phrase}[/bold]\n")
                        payment_skey, stake_skey = derive_keys_from_mnemonic(mnemonic_phrase)
                        payment_vkey = payment_skey.to_verification_key()
                        stake_vkey = stake_skey.to_verification_key()
                        save_encrypted_wallet(wallet_dir, payment_skey, stake_skey, password)
                        save_encrypted_mnemonic(wallet_dir, mnemonic_phrase, password)
                        payment_vkey.save(os.path.join(wallet_dir, "payment.vkey"))
                        stake_vkey.save(os.path.join(wallet_dir, "stake.vkey"))
                        logging.debug(f"[DEBUG-show_mnemonic] Saved new mnemonic and keys for"
                            f"wallet '{wallet_name}'.")
                        console.print(f"[spring_green2]New mnemonic generated and saved "
                            f"for wallet '{wallet_name}'.[/spring_green2]")
                    except Exception as e:
                        logging.error(f"[DEBUG-show_mnemonic-ERROR] Failed to generate"
                                      f" or save mnemonic: {e}", exc_info=True)
                        console.print(f"[red]Failed to generate or save mnemonic: {e}[/red]")
                    return
                elif answer in ("no", "n"):
                    console.print("[yellow]No mnemonic generated. Returning to menu.[/yellow]")
                    return
                else:
                    console.print("[red]Please answer 'yes' or 'no'.[/red]")
        else:
            console.print("[bold]Enter your password to decrypt the mnemonic:[/bold]")
            password = prompt_existing_password()
            try:
                mnemonic = load_encrypted_mnemonic(wallet_dir, password)
                logging.debug(f"[DEBUG-show_mnemonic] Decrypted mnemonic for"
                              f" wallet '{wallet_name}'.")
                console.print("[bold green]Your wallet's 24-word mnemonic passphrase:[/bold green]")
                console.print(f"[yellow]{mnemonic}[/yellow]")
                console.print("[bold red]Store this mnemonic securely!"
                              " Anyone with it can access your funds.[/bold red]")
            except Exception as e:
                logging.error(f"[DEBUG-show_mnemonic-ERROR] Failed to decrypt"
                              f" mnemonic: {e}", exc_info=True)
                console.print(f"[red]Failed to decrypt mnemonic: {e}[/red]")
    except Exception as e:
        logging.error(f"[DEBUG-show_mnemonic-ERROR] Failed to load wallets: {e}", exc_info=True)
        console.print(f"[red]Failed to load wallets: {e}[/red]")
    logging.debug(f"[EXIT] show_mnemonic(user={current_user})")

@exception_error
def funds_send() -> None:
    logging.debug("[ENTRY] funds_send()")
    try:
        console.print("[bold]Enter the wallet name from which you want to send funds "
                      "(or type 'cancel' to go back):[/bold]")
        while True:
            wallet_name = session.prompt("> ").strip()
            if wallet_name.lower() == "cancel":
                console.print("[yellow]Send funds cancelled.[/yellow]")
                return
            wallet_dir = secure_path_join(WALLET_DIR, wallet_name)
            if not os.path.exists(wallet_dir):
                console.print("[red]Wallet not found. Please create or import the wallet"
                              " first or type 'cancel' to abort.[/red]")
                continue
            break

        console.print("[bold]Enter the recipient's address (or type 'cancel' to go back):[/bold]")
        recipient = session.prompt("> ").strip()
        if recipient.lower() == "cancel":
            console.print("[yellow]Send funds cancelled.[/yellow]")
            return

        # The asset picker lists what this wallet actually holds, which needs
        # its address — and the address is encrypted, so the password comes
        # here rather than at the very end.
        password = prompt_existing_password()
        sender_address_str = load_encrypted_address(wallet_dir, password)

        selection = _prompt_send_selection(sender_address_str)
        if selection is None:
            console.print("[yellow]Send funds cancelled.[/yellow]")
            return
        sweep, assets = selection

        amount_lovelace = 0
        if not sweep:
            console.print("[bold]Enter amount of Cardano Native Token ADA to send"
                          " (minimum 0.000001) or type 'cancel' to abort:[/bold]")
            while True:
                amount_str = session.prompt("> ").strip()
                if amount_str.lower() == "cancel":
                    console.print("[yellow]Send funds cancelled.[/yellow]")
                    return
                try:
                    amount_lovelace = ada_to_lovelace(amount_str)
                    break
                except ValueError as e:
                    console.print(f"[red]{e}[/red]")

        send_ada(wallet_dir, recipient, amount_lovelace, password,
                 assets=assets, sweep=sweep)
    except Exception as e:
        console.print(f"[red]Failed to send funds: {e}[/red]")
    logging.debug("[EXIT] funds_send()")

@exception_error
def send_ada(wallet_dir: str, recipient: str, amount_lovelace: int, password: str,
             assets: list[tuple[bytes, bytes, int]] | None = None,
             sweep: bool = False) -> str | None:
    """
    Send Cardano Native Token ADA or multi-assets from a wallet to a recipient.

    `amount_lovelace` is integer lovelace — money never travels through a
    binary float (see ada_to_lovelace for exact ADA-string parsing). `assets`
    names (policy, name, quantity) bundles sent alongside the ADA; `sweep`
    empties the wallet: everything but the fee goes to the recipient and no
    change comes back.
    """
    try:
        # Load wallet keys
        payment_skey, stake_skey = load_encrypted_wallet(wallet_dir, password)

        if not context:
            raise ValueError("No backend configured. Please set up a backend first.")

        # Fetch UTxOs for the sender's address
        sender_address_str = load_encrypted_address(wallet_dir, password)
        sender_address_obj: Address = Address.from_primitive(sender_address_str)
        utxos = context.utxos(sender_address_str)
        if not utxos:
            raise ValueError("No UTxOs found for the sender's address.")

        # Parse recipient address
        if not recipient or not isinstance(recipient, str):
            raise ValueError("Recipient address is missing or invalid.")
        try:
            recipient_address: Address = Address.from_primitive(recipient)
        except Exception as addr_err:
            logging.error(f"Invalid recipient address: {recipient}", exc_info=True)
            raise ValueError(f"Invalid recipient address: {recipient}") from addr_err

        # A sweep leaves nothing behind: the one output carries every asset
        # plus the minimum ADA such an output needs, and the change address is
        # the recipient so the remainder (all ADA minus the fee) merges into
        # that same output instead of returning as dust.
        if sweep:
            total_value = Value(coin=0)
            for u in utxos:
                total_value += u.output.amount
            full_bundle = total_value.multi_asset or None
            sweep_coin = (min_lovelace_post_alonzo(TransactionOutput(
                recipient_address, Value(coin=0, multi_asset=full_bundle)), context)
                if full_bundle is not None else 1_000_000)
            sweep_value = (Value(coin=sweep_coin, multi_asset=full_bundle)
                           if full_bundle is not None else Value(coin=sweep_coin))
            sweep_builder = TransactionBuilder(context)
            for u in utxos:
                sweep_builder.add_input(u)
            sweep_builder.add_output(TransactionOutput(recipient_address, sweep_value))
            try:
                signed_tx = sweep_builder.build_and_sign(
                    [payment_skey], change_address=recipient_address)
            except (UTxOSelectionException, TransactionBuilderException) as e:
                raise ValueError(
                    "This wallet cannot be swept: it is too small to cover the "
                    "fee and the minimum ADA the recipient's output must carry."
                ) from e
        else:
            # One output carrying the ADA and, when assets were chosen, the
            # whole selected bundle.
            token_bundle: MultiAsset | None = None
            if assets:
                primitive: dict[str, dict[str, int]] = {}
                for policy, name, quantity in assets:
                    primitive.setdefault(policy.hex(), {})[name.hex()] = quantity
                token_bundle = _multi_asset_from_primitive(primitive)
                _check_min_ada_for_tokens(recipient_address, amount_lovelace,
                                          token_bundle)
            if token_bundle is None and amount_lovelace < 1:
                raise ValueError("Amount must be at least 1 lovelace (0.000001 ADA).")
            tx_output = (TransactionOutput(recipient_address,
                                           Value(amount_lovelace, token_bundle))
                         if token_bundle is not None
                         else TransactionOutput(recipient_address,
                                                Value(amount_lovelace)))

            def _build_tx(spend_every_utxo: bool) -> Transaction:
                b = TransactionBuilder(context)
                if spend_every_utxo:
                    for u in utxos:
                        b.add_input(u)
                else:
                    b.add_input_address(sender_address_obj)
                b.add_output(tx_output)
                return b.build_and_sign([payment_skey], change_address=sender_address_obj)

            # Build and sign the transaction
            try:
                signed_tx = _build_tx(spend_every_utxo=False)
            except UTxOSelectionException:
                # The coin selectors can stop at a subset that covers output+fee
                # but strands the change below min-UTxO even though the wallet
                # holds more (observed on preprod with a two-UTxO wallet). Spending
                # every UTxO consolidates the address and always leaves maximal
                # change, so retry that way before giving up.
                console.print("[yellow]Re-selecting coins across all UTxOs...[/yellow]")
                signed_tx = _build_tx(spend_every_utxo=True)

        try:
            # Submit the transaction
            cbor_hex = signed_tx.to_cbor().hex()
            tx_id = context.submit_tx(cbor_hex)
            if not tx_id:
                console.print("[red]Submission failed: the backend returned no "
                              "transaction id.[/red]")
                return None
            console.print(f"[green]Transaction submitted successfully! TX ID: {tx_id}[/green]")
            # Submission is not visibility. The UTXO query reads the ledger,
            # not the mempool, so a second send started right now would be
            # built on inputs this transaction already spent and rejected with
            # "All inputs are spent" (observed on preprod 2026-08-22). Wait
            # for the spent inputs to disappear before handing control back.
            spent = {(i.transaction_id.payload.hex(), i.index)
                     for i in _tx_body_inputs(signed_tx.transaction_body)}
            console.print("[dim]Waiting for the ledger to reflect it "
                          "(usually under a minute)…[/dim]")
            deadline = time.time() + 90
            while time.time() < deadline:
                current = context.utxos(sender_address_str)
                if not any((u.input.transaction_id.payload.hex(), u.input.index)
                           in spent for u in current):
                    return tx_id
                time.sleep(3)
            console.print("[yellow]The ledger has not reflected the transaction "
                          "yet. Before sending again, check the wallet's balance "
                          "first.[/yellow]")
            return tx_id
        except ValueError as ve:
            logging.error(f"ValueError in send_ada: {ve}", exc_info=True)
            console.print(f"[red]Error: {ve}[/red]")
            return None
        except TransactionSubmissionError as e:
            logging.error(f"Exception in send_ada: {e}", exc_info=True)
            console.print(f"[red]Failed to send Cardano Native Token ADA or token: {e}[/red]")
            console.print("[yellow]A rejection saying the inputs are already spent "
                          "can mean the wallet's balance view was stale. Check the "
                          "balance before retrying; re-sending the same amount is "
                          "safe only if the balance still shows it.[/yellow]")
            return None
        except Exception as e:
            logging.error(f"Exception in send_ada: {e}", exc_info=True)
            console.print(f"[red]Failed to send Cardano Native Token ADA or token: {e}[/red]")
            raise

    except ValueError as ve:
        logging.error(f"ValueError in send_ada: {ve}", exc_info=True)
        console.print(f"[red]Error: {ve}[/red]")
        return None
    except Exception as e:
        logging.error(f"Exception in send_ada: {e}", exc_info=True)
        console.print(f"[red]Failed to send Cardano Native Token ADA or token: {e}[/red]")
        raise

@exception_error
def delete_wallet(current_user: str, user_password: str) -> None:
    logging.debug(f"[ENTRY] delete_wallet(user={current_user})")
    user_data = load_user_data(current_user, user_password)
    if user_data is None:
        return
    wallets = _json_list(user_data.get("wallets"))
    if not wallets:
        console.print("[yellow]You have no wallets to delete.[/yellow]")
        return

    console.print("[bold green]Your wallets:[/bold green]")
    for i, wallet in enumerate(wallets, 1):
        console.print(f"{i}. {_wallet_entry_name(wallet)}")

    console.print("[bold]Enter the number of the wallet to delete, or type 'cancel' to go "
        "back:[/bold]")
    while True:
        choice = session.prompt("> ").strip()
        if choice.lower() == "cancel":
            console.print("[yellow]Delete wallet cancelled.[/yellow]")
            return
        if not choice.isdigit() or not (1 <= int(choice) <= len(wallets)):
            console.print("[red]Invalid choice, try again or type 'cancel'.[/red]")
            continue
        wallet_name = _wallet_entry_name(wallets[int(choice) - 1])
        break

    console.print(f"[red]Are you sure you want to delete wallet '{wallet_name}'? This "
        "action cannot be undone![/red]")
    confirmation = session.prompt(HTML("<b>Type 'DELETE' to confirm:</b> ")).strip().upper()
    if confirmation != "DELETE":
        console.print("[yellow]Deletion cancelled.[/yellow]")
        return

    console.print("[bold]Enter your password to confirm wallet deletion:[/bold]")
    password = prompt_existing_password()
    if password != user_password:
        console.print("[red]Password incorrect. Aborting deletion.[/red]")
        return

    try:
        wallet_dir = secure_path_join(WALLET_DIR, wallet_name)
        shutil.rmtree(wallet_dir)
        wallets.remove(wallets[int(choice) - 1])
        user_data["wallets"] = wallets
        save_user_data(current_user, user_data, user_password)
        console.print(f"[green]Wallet '{wallet_name}' deleted successfully![/green]")
    except Exception as e:
        console.print(f"[red]Failed to delete wallet: {e}[/red]")
    logging.debug(f"[EXIT] delete_wallet(user={current_user})")

@exception_error
def delete_user() -> None:
    logging.debug("[ENTRY] delete_user()")
    flash_banner("!!! WARNING: DELETE USER ACCOUNT !!!")

    console.print("[bold red]This will permanently delete your user account and ALL associated "
        "wallets.[/bold red]")
    console.print("[bold red]This action CANNOT be undone.[/bold red]")
    console.print("[bold]Type your username to confirm deletion or 'cancel' to abort:[/bold]")
    while True:
        username = session.prompt("> ").strip()
        if username.lower() == "cancel":
            console.print("[yellow]Delete user cancelled.[/yellow]")
            return
        user_file = os.path.join(USER_DATA_DIR, f"{username}.userdb")
        if not os.path.exists(user_file):
            console.print("[red]User not found. Please try again or type 'cancel'.[/red]")
            continue
        break

    console.print("[bold red]You must confirm your password twice to proceed.[/bold red]")
    first_pw = ""
    for attempt in range(2):
        pw = session.prompt(
            HTML(f"<ansired>Enter your password ({attempt+1}/2): </ansired>"),
            is_password=True)
        try:
            _ = load_user_data(username, pw)
        except Exception:
            console.print("[red]Password incorrect. Aborting deletion.[/red]")
            return
        if attempt == 0:
            first_pw = pw
        else:
            if pw != first_pw:
                console.print("[red]Passwords do not match. Aborting deletion.[/red]")
                return

    try:
        user_data = load_user_data(username, pw)
        if user_data is not None:
            for wallet_entry in _json_list(user_data.get("wallets")):
                wallet_dir = secure_path_join(WALLET_DIR, _wallet_entry_name(wallet_entry))
                if os.path.exists(wallet_dir):
                    shutil.rmtree(wallet_dir)
        os.remove(os.path.join(USER_DATA_DIR, f"{username}.userdb"))
        console.print(f"[green]User '{username}' and all their wallets deleted "
            "successfully.[/green]")
    except Exception as e:
        console.print(f"[red]Failed to delete user: {e}[/red]")
    logging.debug("[EXIT] delete_user()")

def pool_registration() -> None:
    logging.debug("[ENTRY] pool_registration()")
    """Register a stake pool on the Cardano blockchain."""
    console.print("[bold green]Stake Pool Registration[/bold green]")
    console.print("[yellow]This feature is under development and will be available in a future "
        "release.[/yellow]")
    logging.debug("[EXIT] pool_registration()")

def drep_registration() -> None:
    logging.debug("[ENTRY] drep_registration()")
    """Register a Delegated Representative (DRep) on the Cardano blockchain."""
    console.print("[bold green]DRep Registration[/bold green]")
    console.print("[yellow]This feature is under development and will be available in a future "
        "release.[/yellow]")
    logging.debug("[EXIT] drep_registration()")

def view_wallets(current_user: str, user_password: str) -> None:
    logging.debug(f"[ENTRY] view_wallets(user={current_user})")
    try:
        user_data = load_user_data(current_user, user_password)
        if user_data is None:
            return
        wallets = _json_list(user_data.get("wallets"))
        if not wallets:
            console.print("[yellow]You have no wallets yet.[/yellow]")
            return
        console.print("[bold green]Your wallets:[/bold green]")
        for wallet in wallets:
            wallet_name = _wallet_entry_name(wallet)
            wallet_dir = secure_path_join(WALLET_DIR, wallet_name)
            wallet_net = load_wallet_network(wallet_dir)
            # Escaped brackets: an unescaped [preprod] would be parsed as a Rich
            # style tag and silently vanish from the rendered line.
            net_tag = f" \\[{wallet_net}]" if wallet_net else ""
            net_warn = ""
            if wallet_net and SELECTED_NETWORK and wallet_net != SELECTED_NETWORK:
                net_warn = " [red](wrong network!)[/red]"
            try:
                address = (_json_str(wallet.get("address")) if isinstance(wallet, dict)
                           else load_encrypted_address(wallet_dir,
                                                       user_password))
                console.print(f"- {wallet_name}{net_tag}: {address}{net_warn}")
            except FileNotFoundError:
                console.print(f"[red]Address not found for wallet '{wallet_name}'.[/red]")
                console.print("[bold]Do you want to generate and save a new address for this "
                    "wallet? (yes/no)[/bold]")
                while True:
                    answer = session.prompt("> ").strip().lower()
                    if answer in ("yes", "y"):
                        console.print("[bold]Enter your password to encrypt the wallet's keys and "
                            "address:[/bold]")
                        password = prompt_existing_password()
                        try:
                            regenerated = regenerate_address(wallet_dir, password)
                            console.print(f"[spring_green2]New address generated and saved "
                                f"for wallet '{wallet_name}'.[/spring_green2]")
                            # Update migrated wallet dict
                            for w in _json_list(user_data.get("wallets")):
                                if isinstance(w, dict) and _json_str(w.get("name")) == wallet_name:
                                    w["address"] = str(regenerated)
                                    save_user_data(current_user, user_data, user_password)
                                    break
                        except Exception as e:
                            logging.error(f"[DEBUG-view_wallets-ERROR] Failed"
                                          f" to generate or save address: {e}",
                                          exc_info=True)
                            console.print(f"[red]Failed to generate or save address: {e}[/red]")
                        break
                    elif answer in ("no", "n"):
                        console.print("[yellow]No address generated. Skipping wallet.[/yellow]")
                        break
                    else:
                        console.print("[red]Please answer 'yes' or 'no'.[/red]")
            except Exception as e:
                logging.error(f"[DEBUG-view_wallets-ERROR] Failed to load"
                              f" address for wallet '{wallet_name}': {e}",
                              exc_info=True)
                console.print(f"[red]Failed to load address for wallet '{wallet_name}': {e}[/red]")
    except Exception as e:
        logging.error(f"[DEBUG-view_wallets-ERROR] Failed to load wallets: {e}", exc_info=True)
        console.print(f"[red]Failed to load wallets: {e}[/red]")
    logging.debug(f"[EXIT] view_wallets(user={current_user})")

def switch_backend() -> None:
    global context, BACKEND_TYPE
    prompt_network_selection()
    console.print("[bold]Select a new backend:[/bold]")
    console.print("  1. Blockfrost (remote API)")
    console.print("  2. Local Node (Ogmios + Kupo)")
    choice = session.prompt("> ").strip()

    if choice == "1":
        prompt_blockfrost_setup()
    elif choice == "2":
        prompt_local_stack_setup()
    else:
        console.print("[red]Invalid choice.[/red]")
        return

    if context:
        console.print(f"[green]Switched to {context.display_name()}[/green]")
    logging.debug("[EXIT] switch_backend()")

@exception_error
def tracker_lp(current_user: str, user_password: str) -> None:
    logging.debug(f"[ENTRY] tracker_lp(user={current_user})")
    user_data = load_user_data(current_user, user_password)
    if user_data is None:
        return
    wallets = _json_list(user_data.get("wallets"))
    if not wallets:
        console.print("[yellow]You have no wallets yet.[/yellow]")
        return
    console.print("[bold green]Your wallets:[/bold green]")
    for i, w in enumerate(wallets, 1):
        console.print(f"{i}. {_wallet_entry_name(w)}")
    console.print("[bold]Enter the number of the wallet to track LPs, or type 'cancel' to go "
        "back:[/bold]")
    while True:
        choice = session.prompt("> ").strip()
        if choice.lower() == "cancel":
            return
        if not choice.isdigit() or not (1 <= int(choice) <= len(wallets)):
            console.print("[red]Invalid choice, try again or type 'cancel'.[/red]")
            continue
        wallet_entry = wallets[int(choice) - 1]
        wallet_name = _wallet_entry_name(wallet_entry)
        break
    address = _json_str(_json_object(wallet_entry).get("address"))
    if not address:
        console.print(f"[red]Failed to load address for wallet '{wallet_name}'.[/red]")
        return
    logging.debug(f"Using address for UTxO lookup: {address}")
    console.print("[green]Getting LP and token assets for wallet address:[/green]")
    console.print(f"[bold white]{address}[/bold white]")
    try:
        if not context:
            console.print("[red]No backend configured.[/red]")
            return
        utxos = context.utxos(address)
        if not utxos:
            console.print("[yellow]No UTxOs found for this address.[/yellow]")
            return
        assets: defaultdict[tuple[str, str], int] = defaultdict(int)
        total_lovelace = 0
        for utxo in utxos:
            total_lovelace += utxo.output.amount.coin
            if utxo.output.amount.multi_asset:
                for policy_bytes, asset_items in _multi_asset_items(
                        utxo.output.amount.multi_asset):
                    policy_id = policy_bytes.hex()
                    for asset_name_bytes, qty in asset_items:
                        assets[(policy_id, _asset_display_name(asset_name_bytes))] += qty
        table = Table(title=f"Assets & LP Tokens in '{wallet_name}'", show_lines=True)
        table.add_column("Asset Name", style="cyan", no_wrap=True)
        table.add_column("Policy ID", style="magenta")
        table.add_column("Amount", justify="right")
        table.add_row("ADA", "", format_ada(total_lovelace))
        for (policy_id, asset_name), amount in sorted(assets.items()):
            table.add_row(asset_name, policy_id, str(amount))
        console.print(table)
        logging.debug(f"[EXIT] tracker_lp(user={current_user})")
    except Exception as e:
        logging.error(f"[tracker_lp] Failed: {e}", exc_info=True)
        console.print(f"[red]Failed to fetch assets: {e}[/red]")
        return

def _read_cbor_hex(source: str) -> str:
    """Return CBOR hex from a file path or a pasted hex string.

    A file may hold either hex text or raw CBOR bytes. A non-file argument is
    treated as pasted hex.
    """
    if os.path.isfile(source):
        with open(source, "rb") as f:
            raw = f.read().strip()
        try:
            text = raw.decode("ascii").strip()
            if len(text) >= 2 and all(c in "0123456789abcdefABCDEF" for c in text):
                return text.lower()
        except UnicodeDecodeError:
            pass
        return raw.hex()
    return source.strip()


def _prompt_file_path(title: str, exts: tuple[str, ...] = (".cbor", ".json"),
                      allow_paste: bool = True) -> str | None:
    """TUI file selector with tab-completable navigation and numbered recent files.

    Lists files matching `exts` in the current and home directories, then accepts
    a number, a tab-completable path (Tab to navigate directories), or pasted
    content. Returns the chosen string (a path or pasted text), or None on cancel.
    """
    from prompt_toolkit.completion import PathCompleter

    candidates: list[str] = []
    seen: set[str] = set()
    for directory in (os.getcwd(), os.path.expanduser("~")):
        try:
            for name in sorted(os.listdir(directory)):
                p = os.path.join(directory, name)
                if os.path.isfile(p) and name.lower().endswith(exts) and p not in seen:
                    candidates.append(p)
                    seen.add(p)
        except OSError:
            continue

    console.print(f"[bold bright_cyan]{title}[/bold bright_cyan]")
    if candidates:
        console.print("[dim]Nearby files:[/dim]")
        for i, c in enumerate(candidates, 1):
            console.print(f"  {i}. {c}")
    hint = "Enter a number, or type/paste a path (Tab completes)"
    if allow_paste:
        hint += " or pasted hex/JSON"
    hint += ", or 'cancel'"
    console.print(f"[dim]{hint}:[/dim]")

    completer = PathCompleter(only_directories=False, expanduser=True)
    choice = session.prompt("> ", completer=completer,
                            complete_while_typing=True).strip()
    if not choice or choice.lower() == "cancel":
        return None
    if choice.isdigit() and 1 <= int(choice) <= len(candidates):
        return candidates[int(choice) - 1]
    return os.path.expanduser(choice)


def _prompt_export_path(default_name: str) -> str | None:
    """Prompt for a path to write an exchange artifact to, with a default.

    Returns the chosen path (the default on empty input), or None on cancel.
    """
    from prompt_toolkit.completion import PathCompleter

    console.print("[bold]Export path (Enter for default, 'cancel' to skip):[/bold]")
    console.print(f"[dim]default: ./{default_name}[/dim]")
    completer = PathCompleter(only_directories=False, expanduser=True)
    path = session.prompt("> ", completer=completer,
                          complete_while_typing=True).strip()
    if path.lower() == "cancel":
        return None
    return os.path.expanduser(path) if path else default_name


def _held_assets(address: str) -> dict[tuple[bytes, bytes], int]:
    """The native assets sitting at an address, aggregated across its UTxOs."""
    held: dict[tuple[bytes, bytes], int] = defaultdict(int)
    if not context:
        return held
    for utxo in context.utxos(address):
        multi_asset = utxo.output.amount.multi_asset
        if not multi_asset:
            continue
        for policy, assets in _multi_asset_items(multi_asset):
            for asset_name, quantity in assets:
                held[(policy, asset_name)] += quantity
    return held


def _prompt_send_selection(address: str
                           ) -> tuple[bool, list[tuple[bytes, bytes, int]]] | None:
    """Decide what leaves the wallet alongside the ADA.

    Returns (sweep, assets): `sweep` empties the wallet — every asset and all
    ADA minus the fee goes to the recipient and nothing comes back as change;
    `assets` lists (policy, name, quantity) chosen to send as well. Returns
    None if the user cancelled.

    The assets are read from the wallet's own UTxOs rather than typed in, so a
    policy id and asset name never have to be transcribed by hand — getting
    either wrong builds a transaction the ledger rejects.
    """
    if not context:
        return False, []
    held = _held_assets(address)
    entries = sorted(held.items())

    labels = ["ADA only (no native token)",
              "EVERYTHING — sweep the whole wallet to the recipient"]
    if entries:
        labels.append("Choose assets to send as well…")
    choice = _prompt_choice(
        "What should this transaction send?",
        labels,
        hint="A sweep leaves this wallet empty; the fee comes out of what's sent.",
    )
    if choice is None:
        return None
    if choice == 1:
        asset_note = (f"{len(entries)} asset(s) and all the ADA"
                      if entries else "all the ADA")
        console.print(f"[bold]This sends {asset_note} to the recipient and leaves "
                      "nothing behind.[/bold]")
        confirmed = _prompt_yes_no()
        if confirmed is None or not confirmed:
            console.print("[yellow]Cancelled.[/yellow]")
            return None
        return True, []
    if choice != 2 or not entries:
        return False, []

    # Multi-select: toggle assets by number, then take an amount for each.
    marked: set[int] = set()
    while True:
        console.print("[bold]Choose assets to send alongside the ADA.[/bold]")
        console.print("[dim]Toggle by number; 'all' marks every asset, 'none' "
                      "clears, 'done' finishes, 'cancel' aborts.[/dim]")
        for i, ((policy, name), quantity) in enumerate(entries, 1):
            # The brackets are literal, not Rich markup — an unescaped "[x]"
            # is silently swallowed as an unknown style tag.
            mark = "\\[x]" if i in marked else "\\[ ]"
            console.print(f"  {mark} {i}. {_asset_display_name(name)} × {quantity}  "
                          f"[dim]{policy.hex()[:12]}…[/dim]")
        answer = session.prompt("> ").strip().lower()
        if answer == "cancel":
            return None
        if answer == "done":
            if not marked:
                console.print("[red]Nothing is marked. Mark at least one asset, or "
                              "go back and choose 'ADA only'.[/red]")
                continue
            break
        if answer == "all":
            marked = set(range(1, len(entries) + 1))
            continue
        if answer == "none":
            marked = set()
            continue
        if answer.isdigit() and 1 <= int(answer) <= len(entries):
            i = int(answer)
            if i in marked:
                marked.discard(i)
            else:
                marked.add(i)
            continue
        console.print(f"[red]Enter an asset number 1-{len(entries)}, 'all', 'none', "
                      "'done', or 'cancel'.[/red]")

    chosen: list[tuple[bytes, bytes, int]] = []
    for i in sorted(marked):
        (policy, name), available = entries[i - 1]
        display = _asset_display_name(name)
        while True:
            console.print(f"How many {display} should be sent? "
                          f"(max {available}; Enter sends all; 'cancel' aborts)")
            answer = session.prompt("> ").strip()
            if answer.lower() == "cancel":
                return None
            if answer == "":
                amount = available
                break
            if answer.isdigit() and 1 <= int(answer) <= available:
                amount = int(answer)
                break
            console.print(f"[red]Enter a whole number between 1 and {available}.[/red]")
        chosen.append((policy, name, amount))
    return False, chosen


def _interactive_build_multisig(current_user: str, user_password: str) -> None:
    """Interactive flow to build a multisig transaction."""
    console.print("\n[bold bright_cyan]Build a Transaction[/bold bright_cyan]")
    console.print("[dim]Builds an unsigned transaction for the cosigners to sign. "
                  "Nothing[/dim]")
    console.print("[dim]moves until enough of them have signed it.[/dim]")

    wallet_dir = _select_multisig_wallet(current_user, user_password,
                                         "Which multisig wallet are you spending from?")
    if wallet_dir is None:
        return

    try:
        wallet = _load_multisig_wallet(wallet_dir)
    except Exception as e:
        console.print(f"[red]Could not open that wallet: {e}[/red]")
        return
    if not _initiator_check(wallet, "start a spend", current_user, user_password):
        return

    recipient = _prompt_cancelable(
        "Where should the funds go? (recipient address)",
        "Every cosigner will see this address before they sign it.",
    )
    if not recipient:
        console.print("[yellow]Cancelled.[/yellow]")
        return

    selection = _prompt_send_selection(wallet["script_address"])
    if selection is None:
        console.print("[yellow]Cancelled.[/yellow]")
        return
    sweep, assets = selection

    amount_lovelace = 0
    if not sweep:
        if context is not None:
            # The sendable ceiling sits below the balance by the fee and the
            # change minimum; saying so here stops a near-balance amount being
            # typed in blind only to be refused after the fact.
            balance = sum((u.output.amount.coin
                           for u in context.utxos(wallet["script_address"])), 0)
            if balance > 0:
                console.print(f"[dim]{format_ada(balance)} ADA sits at this wallet's "
                              "address. The sendable maximum is somewhat below that: "
                              "the fee and the change output's minimum ADA come out "
                              "of it too.[/dim]")
        amount_str = _prompt_cancelable("How much, in ADA?", "For example: 1.5")
        if amount_str is None:
            console.print("[yellow]Cancelled.[/yellow]")
            return
        try:
            amount_lovelace = ada_to_lovelace(amount_str)
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            return

    try:
        build_result = build_multisig_transaction(
            wallet_dir, recipient, amount_lovelace, assets=assets, sweep=sweep,
        )
    except Exception as e:
        console.print(f"[red]Failed to build transaction: {e}[/red]")
        return
    if not build_result:
        return

    console.print("[bold]Export the unsigned transaction to a file for cosigners? (y/n):[/bold]")
    if session.prompt("> ").strip().lower() == "y":
        sessions_dir = os.path.join(wallet_dir, "sessions")
        sessions = [os.path.join(sessions_dir, d) for d in os.listdir(sessions_dir)
                    if os.path.isdir(os.path.join(sessions_dir, d))]
        if not sessions:
            console.print("[red]Could not locate the built session.[/red]")
            return
        newest = max(sessions, key=os.path.getmtime)
        with open(os.path.join(newest, "unsigned.cbor")) as f:
            unsigned_hex = f.read().strip()
        session_id = os.path.basename(newest)
        path = _prompt_export_path(f"unsigned_{session_id}.cbor")
        if path:
            with open(path, "w") as f:
                f.write(unsigned_hex)
            console.print(f"\n[bold green]Unsigned transaction written to {path}[/bold green]")
            console.print("[dim]Send this file to each cosigner. Each cosigner then:[/dim]")
            console.print("[dim]  Sign Transaction -> import this file -> export"
                          " their partial.[/dim]")


def _session_label(sessions_dir: str, session_id: str) -> str:
    """Describe a signing session the way a signer thinks about it.

    A session id is a hash prefix and means nothing to anyone, so the list shows
    what the transaction actually does and how far along it is.
    """
    session_dir = os.path.join(sessions_dir, session_id)
    parts: list[str] = []
    try:
        with open(os.path.join(session_dir, "summary.json")) as f:
            summary = _json_object(_json_loads(f.read()))
        amount = _json_int(summary.get("amount_lovelace"))
        recipient = _json_str(summary.get("recipient"))
        if amount:
            parts.append(f"{format_ada(amount)} ADA")
        if recipient:
            parts.append(f"to {recipient[:20]}...")
    except (OSError, ValueError):
        pass
    try:
        with open(os.path.join(session_dir, "status.json")) as f:
            status = _json_object(_json_loads(f.read()))
        signatures = _json_int(status.get("signatures"))
        threshold = _json_int(status.get("threshold"))
        state = _json_str(status.get("status"))
        if threshold:
            parts.append(f"{signatures} of {threshold} signed")
        if state == "submitted":
            parts.append("[green]submitted[/green]")
    except (OSError, ValueError):
        pass
    if not parts:
        return session_id
    return f"{'  '.join(parts)}  [dim]({session_id})[/dim]"


def _select_multisig_wallet(current_user: str, user_password: str,
                            title: str = "Which multisig wallet?") -> str | None:
    """Pick one of this user's multisig wallets. Returns its directory, or None."""
    user_data = load_user_data(current_user, user_password)
    if user_data is None:
        return None
    names: list[str] = []
    for w in _json_list(user_data.get("wallets")):
        name = _json_str(_json_object(w).get("name"))
        if not name:
            continue
        wallet_dir = secure_path_join(WALLET_DIR, name)
        # The script file is what makes a wallet a multisig wallet — more
        # reliable than the type tag, which older wallets spell differently.
        if os.path.exists(os.path.join(wallet_dir, "script.cbor")):
            names.append(name)

    if not names:
        console.print("[yellow]You have no multisig wallets yet.[/yellow]")
        console.print("[dim]Create one, or recover one from a script CBOR or "
                      "package.[/dim]")
        return None

    labels: list[str] = []
    for name in names:
        wallet_dir = secure_path_join(WALLET_DIR, name)
        try:
            info = _load_multisig_wallet(wallet_dir)
            labels.append(f"{name}  [dim]({info['threshold']} of "
                          f"{len(info['key_hashes'])} must sign)[/dim]")
        except Exception:
            labels.append(name)

    choice = _prompt_choice(title, labels)
    if choice is None:
        return None
    return secure_path_join(WALLET_DIR, names[choice])


def _resolve_signing_wallet(current_user: str, user_password: str,
                            wallet: MultisigWalletInfo) -> tuple[str, bytes] | None:
    """Work out which personal wallet and WHICH of its keys signs here.

    The common case — one of this user's wallets is named in the script —
    needs no question at all. When one wallet holds several of the script's
    keys, the key is the user's choice and must be carried through to the
    derivation, because a wallet's first matching key is not necessarily the
    one that was picked. Returns (wallet directory, key hash), or None if the
    user cannot sign or cancelled. The password is asked for later, after the
    signer has seen what they are signing.
    """
    key_hashes = wallet["key_hashes"]
    matches = _match_cosigner_identities(current_user, user_password, key_hashes)

    if not matches:
        # Nothing cached matched. The wallet holding the cosigner key may
        # simply never have been opened for this purpose — or the provider
        # placed the key beyond the usual position, which the cache cannot
        # see. Unlocking searches the wallet's whole key tree; refusing on
        # the cache alone would make a recovered wallet unsignable by
        # exactly the people the script names.
        console.print("\n[yellow]None of your wallets is known to be a cosigner of "
                      "this script yet.[/yellow]")
        console.print("[dim]If you are a cosigner, unlock your wallet so its key can "
                      "be checked.[/dim]")
        unlocked = _prompt_wallet_unlock(current_user, user_password)
        if unlocked is None:
            return None
        _name, _unlock_dir, unlock_password = unlocked
        matches = _match_cosigner_identities(current_user, user_password, key_hashes,
                                             unlock_password=unlock_password)
        if not matches:
            console.print("\n[bold red]That wallet is not a cosigner of this "
                          "script.[/bold red]")
            console.print("[dim]Only the wallets whose key hashes are in the script "
                          "can sign.[/dim]")
            return None

    if len(matches) == 1:
        match = matches[0]
        console.print(f"\n[green]You are cosigner {match['script_index'] + 1} — signing "
                      f"with '{match['wallet_name']}'.[/green]")
    else:
        choice = _prompt_choice(
            "You hold more than one cosigner key. Which one signs?",
            [f"cosigner {m['script_index'] + 1} — {m['wallet_name']}" for m in matches],
        )
        if choice is None:
            return None
        match = matches[choice]

    return match["wallet_dir"], match["key_hash"]


def _find_wallet_by_script_hash(current_user: str, user_password: str,
                                script_hash_hex: str) -> str | None:
    """Find a local multisig wallet whose script hash matches. None if unknown."""
    user_data = load_user_data(current_user, user_password)
    if user_data is None:
        return None
    for w in _json_list(user_data.get("wallets")):
        name = _json_str(_json_object(w).get("name"))
        if not name:
            continue
        wallet_dir = secure_path_join(WALLET_DIR, name)
        hash_path = os.path.join(wallet_dir, "script_hash")
        if not os.path.exists(hash_path):
            continue
        try:
            with open(hash_path) as f:
                if f.read().strip() == script_hash_hex:
                    return wallet_dir
        except OSError:
            continue
    return None


def _wallet_for_incoming_transaction(current_user: str, user_password: str,
                                     cbor_hex: str) -> str | None:
    """Get the wallet a received transaction belongs to, recovering it if needed.

    A cosigner asked to sign usually has nothing but the transaction — they were
    sent it precisely because they are a signer, not because they already run
    the wallet. The script travels inside the transaction, so the wallet can be
    rebuilt from it rather than demanding the user find it first.
    """
    try:
        tx = Transaction.from_cbor(cbor_hex)
    except Exception as e:
        console.print(f"[red]That is not a readable transaction: {e}[/red]")
        return None

    witness_set = tx.transaction_witness_set
    scripts = _witness_scripts(witness_set) if witness_set is not None else []
    if not scripts:
        console.print("[red]That transaction carries no script, so there is no way "
                      "to tell which multisig wallet it spends from.[/red]")
        return None

    script = scripts[0]
    script_hash_hex = script_hash_from_script(script).hex()

    known = _find_wallet_by_script_hash(current_user, user_password, script_hash_hex)
    if known:
        return known

    console.print("\n[yellow]This transaction is for a multisig wallet you do not "
                  "have yet.[/yellow]")
    key_hashes, threshold, _t = extract_key_hashes_from_script(script)
    if threshold is None:
        threshold = len(key_hashes)
    console.print(f"[dim]The script inside it needs {threshold} of {len(key_hashes)} "
                  f"signatures, and everything needed to rebuild the wallet is "
                  f"in the transaction.[/dim]")
    console.print("\n[bold]Add this wallet now so you can sign? (y/n)[/bold]")
    if not _prompt_yes_no(default=True):
        return None

    wallet_name = _prompt_wallet_name()
    if wallet_name is None:
        return None
    try:
        recovered = import_script_cbor(script.to_cbor().hex(), wallet_name,
                                       provenance="recovered_transaction")
    except Exception as e:
        console.print(f"[red]Could not add the wallet: {e}[/red]")
        return None
    if recovered is None:
        return None
    wallet_dir = _json_str(recovered.get("wallet_dir"))
    try:
        _register_multisig_wallet(current_user, user_password, wallet_name, wallet_dir)
    except Exception as e:
        console.print(f"[yellow]Wallet written, but listing it failed: {e}[/yellow]")
    return wallet_dir


def _interactive_sign_multisig(current_user: str, user_password: str) -> None:
    """Interactive flow to sign a multisig transaction.

    Starts from the transaction, not the wallet: a cosigner is normally sent a
    transaction and nothing else, and the wallet it belongs to can be worked out
    from the script inside it.
    """
    console.print("\n[bold bright_cyan]Sign a Transaction[/bold bright_cyan]")
    console.print("[dim]Review what it does, then add your signature. Your key never "
                  "leaves this machine.[/dim]")

    # A file/paste import is the relay path (the CBOR accumulates signatures as it
    # is passed cosigner-to-cosigner); a session import is operator-collect.
    session_dir = None
    unsigned_cbor_hex = None
    relay_mode = False
    wallet_dir: str | None = None

    file_choice = _prompt_file_path(
        "Select the transaction someone sent you (or paste it; Enter to pick a "
        "transaction already here)",
        exts=(".cbor", ".json"))
    if file_choice:
        try:
            unsigned_cbor_hex = _extract_transaction_cbor(file_choice)
            relay_mode = True
        except Exception as e:
            console.print(f"[red]Could not read a transaction from that input: {e}[/red]")
            return
        if not unsigned_cbor_hex:
            console.print("[red]That input contained no transaction.[/red]")
            return
        wallet_dir = _wallet_for_incoming_transaction(
            current_user, user_password, unsigned_cbor_hex)
        if wallet_dir is None:
            return
    else:
        wallet_dir = _select_multisig_wallet(
            current_user, user_password, "Which multisig wallet is this transaction for?")
        if wallet_dir is None:
            return
        session_dir = _select_session(wallet_dir, "Which transaction do you want to sign?")
        if session_dir is None:
            console.print("[dim]Ask whoever built it to send you the file, then choose "
                          "it at the first prompt.[/dim]")
            return

    try:
        wallet = _load_multisig_wallet(wallet_dir)
    except Exception as e:
        console.print(f"[red]Could not open that wallet: {e}[/red]")
        return

    signer = _resolve_signing_wallet(current_user, user_password, wallet)
    if signer is None:
        return
    signer_wallet_dir, signer_key_hash = signer

    try:
        sign_result = sign_multisig_transaction(
            wallet_dir,
            unsigned_cbor_hex=unsigned_cbor_hex,
            session_dir=session_dir,
            signer_wallet_dir=signer_wallet_dir,
            signer_key_hash=signer_key_hash,
            accumulate=relay_mode,
        )
    except Exception as e:
        console.print(f"[red]Failed to sign transaction: {e}[/red]")
        return

    if sign_result and sign_result.get("partial_cbor_hex"):
        what = "accumulated transaction" if relay_mode else "partial signature"
        console.print(f"[bold]Export the {what} to a file? (y/n):[/bold]")
        if session.prompt("> ").strip().lower() == "y":
            raw_idx = sign_result.get("matched_cosigner_index")
            idx = _json_int(raw_idx)
            suffix = f"cosigner{idx}" if raw_idx is not None else "sig"
            default = f"relay_{suffix}.cbor" if relay_mode else f"partial_{suffix}.cbor"
            path = _prompt_export_path(default)
            if path:
                with open(path, "w") as f:
                    f.write(_json_str(sign_result["partial_cbor_hex"]))
                console.print(f"\n[bold green]Written to {path}[/bold green]")
                if relay_mode:
                    console.print("[dim]Pass this file to the next cosigner (import"
                                  " + sign + export), or submit if you are the terminator.[/dim]")
                else:
                    console.print("[dim]Send this file to the operator. They: Assemble"
                                  " & Submit -> import partial(s) -> submit.[/dim]")

    if relay_mode and sign_result and sign_result.get("reached_threshold"):
        console.print("[bold green]Threshold met — this transaction can be submitted"
                      " now.[/bold green]")
        # Signing is never gated; submitting is the administrator's call.
        if not _initiator_check(wallet, "submit", current_user, user_password):
            console.print("[dim]Send the file above to the administrator to "
                          "submit.[/dim]")
            return
        console.print("[bold]Submit to the network? (y/n):[/bold]")
        if session.prompt("> ").strip().lower() == "y":
            if not context:
                console.print("[red]No backend configured.[/red]")
                return
            try:
                txid = context.submit_tx(_json_str(sign_result["partial_cbor_hex"]))
                console.print(f"[bold green]✓ SUBMITTED — TX ID: {txid}[/bold green]")
            except Exception as e:
                console.print(f"[red]Submission rejected by the network: {e}[/red]")
                console.print("[yellow]The signed CBOR is saved — submit it via"
                              " Assemble & Submit or cardano-cli.[/yellow]")
                console.print("[dim]A rejection is occasionally reported for a"
                              " transaction that still reached the chain (a relay"
                              " race). Check the wallet's balance before"
                              " building anything in its place; re-submitting"
                              " this same CBOR is always safe.[/dim]")


def _interactive_delegate_multisig(current_user: str, user_password: str) -> None:
    """Register, delegate or deregister a multisig wallet's stake.

    It produces an ordinary unsigned transaction and an ordinary signing
    session, so from here on it is the flow the cosigners already know: they
    sign it, the administrator assembles and submits it.
    """
    console.print("\n[bold bright_cyan]Delegate Stake[/bold bright_cyan]")
    console.print("[dim]Delegation is a decision of the same cosigners, taken the same "
                  "way:[/dim]")
    console.print("[dim]this builds a transaction they sign, and it earns rewards "
                  "without[/dim]")
    console.print("[dim]the funds ever leaving the wallet.[/dim]")

    wallet_dir = _select_multisig_wallet(current_user, user_password,
                                         "Which multisig wallet?")
    if wallet_dir is None:
        return

    try:
        wallet = _load_multisig_wallet(wallet_dir)
    except Exception as e:
        console.print(f"[red]Could not open that wallet: {e}[/red]")
        return

    if not wallet["staking"]:
        console.print("\n[yellow]This wallet cannot delegate.[/yellow]")
        console.print("[dim]Its address has no stake credential. That is part of the "
                      "address itself,[/dim]")
        console.print("[dim]so it cannot be added now — a new wallet created with "
                      "staking enabled,[/dim]")
        console.print("[dim]funded from this one, is the only route.[/dim]")
        return

    if not _initiator_check(wallet, "delegate", current_user, user_password):
        return

    choice = _prompt_choice(
        "What should this transaction do?",
        [
            "Register the stake credential and delegate to a pool (first time)",
            "Delegate to a pool (already registered)",
            "Stop staking and reclaim the deposit",
        ],
        hint="Registration costs a refundable deposit and is done once, ever.",
    )
    if choice is None:
        console.print("[yellow]Cancelled.[/yellow]")
        return

    pool_id: str | None = None
    if choice in (0, 1):
        pool_id = _prompt_cancelable(
            "Which pool? (its pool1… id)",
            "Copy it from the pool's page on an explorer, or from pooltool/adapools.",
        )
        if not pool_id:
            console.print("[yellow]Cancelled.[/yellow]")
            return
        try:
            pool_hash = pool_id_to_key_hash(pool_id)
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            return
        console.print(f"[dim]Pool key hash: {pool_hash.hex()}[/dim]")

    try:
        build_multisig_stake_transaction(
            wallet_dir,
            pool_id=pool_id,
            register=(choice == 0),
            deregister=(choice == 2),
        )
    except Exception as e:
        console.print(f"[red]Could not build the delegation transaction: {e}[/red]")


def _select_session(wallet_dir: str, title: str) -> str | None:
    """Pick one of a wallet's signing sessions, described by what it does."""
    sessions_dir = os.path.join(wallet_dir, "sessions")
    if not os.path.exists(sessions_dir):
        console.print("[yellow]There is no transaction here yet.[/yellow]")
        return None
    sessions = sorted(d for d in os.listdir(sessions_dir)
                      if os.path.isdir(os.path.join(sessions_dir, d)))
    if not sessions:
        console.print("[yellow]There is no transaction here yet.[/yellow]")
        return None
    choice = _prompt_choice(title, [_session_label(sessions_dir, s) for s in sessions])
    if choice is None:
        return None
    return os.path.join(sessions_dir, sessions[choice])


def _interactive_assemble_multisig(current_user: str, user_password: str) -> None:
    """Collect the cosigners' signatures for one transaction and submit it."""
    console.print("\n[bold bright_cyan]Assemble and Submit[/bold bright_cyan]")
    console.print("[dim]Takes the signatures the cosigners sent back, checks each one "
                  "against[/dim]")
    console.print("[dim]this exact transaction, and submits once there are enough.[/dim]")

    wallet_dir = _select_multisig_wallet(
        current_user, user_password, "Which multisig wallet?")
    if wallet_dir is None:
        return

    try:
        wallet = _load_multisig_wallet(wallet_dir)
    except Exception as e:
        console.print(f"[red]Could not open that wallet: {e}[/red]")
        return
    if not _initiator_check(wallet, "assemble and submit", current_user, user_password):
        return

    session_dir = _select_session(wallet_dir, "Which transaction are you assembling?")
    if session_dir is None:
        return

    # Import cosigner partial signatures from files (the relay/collect movement).
    console.print("\n[bold]Add signatures the cosigners sent you? (y/n)[/bold]")
    console.print("[dim]Answer 'n' if they are already here.[/dim]")
    if _prompt_yes_no(default=True):
        while True:
            pchoice = _prompt_file_path("Select a signature file (or paste it)",
                                        exts=(".cbor", ".json"))
            if not pchoice:
                break
            try:
                import_partial_signature(wallet_dir, session_dir, pchoice)
            except Exception as e:
                console.print(f"[red]Could not add that signature: {e}[/red]")
            console.print("[bold]Add another? (y/n)[/bold]")
            if not _prompt_yes_no(default=False):
                break

    try:
        assemble_multisig_transaction(wallet_dir, session_dir)
    except Exception as e:
        console.print(f"[red]Failed to assemble transaction: {e}[/red]")


def _register_multisig_wallet(current_user: str, user_password: str,
                              wallet_name: str, wallet_dir: str) -> None:
    """Add a multisig wallet to the user's wallet list, if it is not there."""
    user_data = load_user_data(current_user, user_password)
    if user_data is None:
        return
    wallet = _load_multisig_wallet(wallet_dir)
    wallets = _json_list(user_data.get("wallets"))
    if wallet_name in [_wallet_entry_name(w) for w in wallets]:
        return
    wallets.append({
        "name": wallet_name,
        "address": wallet["script_address"],
        "type": "multisig",
        "threshold": f"{wallet['threshold']} of {len(wallet['key_hashes'])}",
    })
    user_data["wallets"] = wallets
    save_user_data(current_user, user_data, user_password)


def _preview_script_before_import(current_user: str, user_password: str,
                                  cbor_hex: str) -> bool:
    """Show what a script is and whether this machine can sign it, before importing.

    Recovery used to be a leap: you imported a wallet, then found out whether
    you were one of its cosigners. Everything needed to answer that question is
    already in the script, so it is answered first — including the case that
    matters most, a script that names none of your keys, where importing would
    only produce a wallet you can watch and never spend.

    Returns True to go ahead with the import.
    """
    readings = native_script_candidate_readings(cbor_hex)
    if not readings:
        console.print("[red]That is not a readable native script.[/red]")
        return False

    key_hashes, _threshold, script_type = extract_key_hashes_from_script(
        readings[0][1])
    if not key_hashes:
        console.print("[red]That script names no signing keys, so no wallet can be "
                      "recovered from it.[/red]")
        return False

    if len(readings) > 1:
        console.print("\n[yellow]This script is exported inside a version envelope,"
                      " and can be read two ways.[/yellow]")
        console.print("[dim]The chain decides at import: whichever reading's "
                      "address holds the funds is the wallet recovered.[/dim]")

    for label, script in readings:
        key_hashes, _threshold, script_type = extract_key_hashes_from_script(script)
        threshold = script_min_signatures(script)
        script_hash_bytes = script_hash_from_script(script)
        not_before, not_after = script_timelocks(script)

        console.print(f"\n[bold]What this script is ({label}):[/bold]")
        console.print(f"  Type:      {script_type or 'unknown'}")
        console.print(f"  Signatures: {threshold} of {len(key_hashes)}"
                      + ("" if script_is_flat_threshold(script)
                         else "  [yellow](nested — see the details below)[/yellow]"))
        if not_before is not None:
            console.print(f"  Locked until {_slot_description(not_before)}")
        if not_after is not None:
            console.print(f"  [yellow]Expires {_slot_description(not_after)}[/yellow]")
        console.print(f"  Hash:      {script_hash_bytes.hex()}")
        for candidate_label, address in script_address_candidates(script_hash_bytes).items():
            console.print(f"  Address ({candidate_label}): [cyan]{address}[/cyan]")

    # Which of this machine's wallets the script names is the same for every
    # reading in practice (they carry the same key hashes), so it is shown once.
    matches = _match_cosigner_identities(current_user, user_password, key_hashes)
    if matches:
        console.print("\n[bold green]You can sign for this wallet.[/bold green]")
        for match in matches:
            console.print(f"  cosigner {match['script_index'] + 1} — your "
                          f"'{match['wallet_name']}' wallet")
    else:
        console.print("\n[yellow]None of your unlocked wallets is named in this "
                      "script.[/yellow]")
        console.print("[dim]You can still import it to watch the balance and hold "
                      "the script. If you[/dim]")
        console.print("[dim]expect to be a cosigner, run 'Which cosigner am I?' after "
                      "importing —[/dim]")
        console.print("[dim]that unlocks a wallet and searches further than this "
                      "preview can.[/dim]")

    console.print("\n[bold]Import this wallet? (y/n)[/bold]")
    return _prompt_yes_no(default=True)


def _interactive_import_any(current_user: str, user_password: str) -> None:
    """One door for everything that can be imported (spec §Import flow).

    A user recovering a wallet has whatever the dead provider left them, and
    should not have to know whether it is a script, a checkpoint, a package or a
    half-signed transaction before they can even choose a menu entry. This takes
    the input, works out what it is, and does the right thing.
    """
    console.print("\n[bold bright_cyan]Recover or Import[/bold bright_cyan]")
    console.print("[dim]Give it whatever you have and it will work out what it is:[/dim]")
    console.print("[dim]  • a script .cbor from a provider that shut down[/dim]")
    console.print("[dim]  • a CardanoInterface package or checkpoint (.json)[/dim]")
    console.print("[dim]  • a transaction someone sent you to sign[/dim]")

    choice = _prompt_file_path("Select the file (or paste its contents)",
                               exts=(".cbor", ".json"))
    if not choice:
        console.print("[yellow]Cancelled.[/yellow]")
        return

    # A package is JSON and self-describing, so try that reading first. The
    # peek is binary-safe: a script .cbor from a wallet export is raw bytes,
    # and text-mode reading would crash before the script path is even reached.
    if os.path.isfile(choice):
        try:
            with open(choice, "rb") as f:
                head = f.read(64).lstrip()[:1]
        except OSError as e:
            console.print(f"[red]Could not read that file: {e}[/red]")
            return
        if head == b"{":
            try:
                result = import_package(choice, current_user, user_password)
            except Exception as e:
                console.print(f"[red]Could not import that package: {e}[/red]")
                return
            console.print(f"\n[bold green]Imported.[/bold green] "
                          f"[dim]{_json_str(result.get('wallet_name')) or ''}[/dim]")
            return

    try:
        cbor_hex = _read_cbor_hex(choice)
    except Exception as e:
        console.print(f"[red]Could not read that input: {e}[/red]")
        return

    kind = classify_cbor(cbor_hex)
    if kind == "native_script":
        console.print("\n[green]That is a native script — the identity of a multisig "
                      "wallet.[/green]")
        if not _preview_script_before_import(current_user, user_password, cbor_hex):
            return
        wallet_name = _prompt_wallet_name()
        if wallet_name is None:
            return
        known_address = _prompt_cancelable(
            "The address this wallet's funds sit at, if you have it "
            "(Enter to work it out)",
            "Only needed when the provider used a stake credential that is not "
            "this script.",
        )
        try:
            recovered = import_script_cbor(cbor_hex, wallet_name,
                                           known_address=known_address or None)
        except Exception as e:
            console.print(f"[red]Failed to recover the wallet: {e}[/red]")
            return
        if recovered is None:
            return
        wallet_dir = _json_str(recovered.get("wallet_dir"))
        try:
            _register_multisig_wallet(current_user, user_password, wallet_name, wallet_dir)
        except Exception as e:
            console.print(f"[yellow]Wallet written, but listing it failed: {e}[/yellow]")
            return
        console.print("\n[bold]Check which cosigner you are? (y/n)[/bold]")
        if _prompt_yes_no(default=True):
            _participation_flow(wallet_dir, current_user, user_password)
        return

    if kind in ("unsigned_tx", "witnessed_tx"):
        state = ("not signed yet" if kind == "unsigned_tx"
                 else "already carries signatures")
        console.print(f"\n[green]That is a transaction ({state}).[/green]")
        console.print("[dim]Take it to Sign a Transaction to add your signature, or "
                      "Assemble & Submit if enough have signed.[/dim]")
        return

    console.print("\n[red]That input is not something this program can import.[/red]")
    console.print("[dim]It is not a native script, a transaction, or a "
                  "CardanoInterface package.[/dim]")


def _interactive_export_wallet_checkpoint(current_user: str, user_password: str) -> None:
    """Export a wallet checkpoint — the file that restores this wallet anywhere."""
    console.print("\n[bold bright_cyan]Export Wallet Checkpoint[/bold bright_cyan]")
    console.print("[dim]One file that restores this wallet on any machine. Safe to "
                  "give every[/dim]")
    console.print("[dim]cosigner: it holds no key, no mnemonic and no password.[/dim]")

    wallet_dir = _select_multisig_wallet(
        current_user, user_password, "Which multisig wallet do you want to back up?")
    if wallet_dir is None:
        return
    wallet_name = os.path.basename(wallet_dir)

    try:
        wallet = _load_multisig_wallet(wallet_dir)
    except Exception as e:
        console.print(f"[red]Could not open that wallet: {e}[/red]")
        return

    # A checkpoint that names an administrator is only believed by the importing
    # side if that administrator signed it, so it is signed here when this
    # machine can — otherwise the designation is dropped on arrival.
    signer_wallet_dir: str | None = None
    password: str | None = None
    admin_index = wallet_admin_index(wallet)
    if admin_index is not None:
        admin_key_hash = wallet["key_hashes"][admin_index]
        ours = [m for m in _match_cosigner_identities(
            current_user, user_password, wallet["key_hashes"])
            if m["key_hash"] == admin_key_hash]
        if ours:
            console.print(f"\n[bold]Sign this checkpoint as administrator (cosigner "
                          f"{admin_index + 1})? (y/n)[/bold]")
            console.print("[dim]Without your signature the cosigners' copies restore "
                          "with no administrator,[/dim]")
            console.print("[dim]because anyone could otherwise write themselves into "
                          "the file.[/dim]")
            if _prompt_yes_no(default=True):
                signer_wallet_dir = ours[0]["wallet_dir"]
                console.print(f"[bold]Enter the password for "
                              f"'{ours[0]['wallet_name']}':[/bold]")
                password = prompt_existing_password()
        else:
            console.print(f"\n[yellow]This wallet's administrator is cosigner "
                          f"{admin_index + 1}, whose key is not on this "
                          f"machine.[/yellow]")
            console.print("[dim]The checkpoint will export unsigned, so copies "
                          "restored from it have no administrator.[/dim]")

    export_path = _prompt_export_path(f"checkpoint_{wallet_name}.json")
    if not export_path:
        console.print("[yellow]Cancelled.[/yellow]")
        return

    try:
        checkpoint = export_wallet_checkpoint(wallet_dir, signer_wallet_dir, password)
        _dump_json(checkpoint, export_path)
    except Exception as e:
        console.print(f"[red]Failed to export checkpoint: {e}[/red]")
        return

    console.print("\n[bold green]Wallet checkpoint exported.[/bold green]")
    console.print(f"  Path: [cyan]{export_path}[/cyan]")
    if checkpoint.get("admin_signature"):
        console.print("  [green]Signed as administrator — the designation will be "
                      "honoured on import.[/green]")
    console.print("\n[dim]Give a copy to every cosigner. Any of them restores it with "
                  "Recover or import.[/dim]")


def _interactive_export_transaction_package(current_user: str, user_password: str) -> None:
    """Interactive flow to export a transaction package."""
    user_data = load_user_data(current_user, user_password)
    if user_data is None:
        return
    wallets = _json_list(user_data.get("wallets"))
    multisig_wallets = [
        w for w in wallets
        if isinstance(w, dict) and str(w.get("type", "")).startswith("multisig")
    ]

    if not multisig_wallets:
        console.print("[yellow]No multisig wallets found.[/yellow]")
        return

    console.print("[bold bright_cyan]Export Transaction Package[/bold bright_cyan]")
    console.print("Select a multisig wallet:")
    for i, w in enumerate(multisig_wallets, 1):
        console.print(f"  {i}. {w['name']}")

    console.print("[bold]Enter wallet number or 'cancel':[/bold]")
    choice = session.prompt("> ").strip()
    if choice.lower() == "cancel":
        return

    try:
        idx = int(choice) - 1
        wallet_name = _json_str(multisig_wallets[idx].get("name"))
    except (ValueError, IndexError):
        console.print("[red]Invalid choice.[/red]")
        return

    wallet_dir = secure_path_join(WALLET_DIR, wallet_name)
    sessions_dir = os.path.join(wallet_dir, "sessions")

    if not os.path.exists(sessions_dir):
        console.print("[red]No sessions found.[/red]")
        return

    sessions = sorted([
        d for d in os.listdir(sessions_dir)
        if os.path.isdir(os.path.join(sessions_dir, d))
    ])
    if not sessions:
        console.print("[red]No sessions found.[/red]")
        return

    console.print("[bold]Available sessions:[/bold]")
    for i, s in enumerate(sessions, 1):
        status_file = os.path.join(sessions_dir, s, "status.json")
        status = ""
        if os.path.exists(status_file):
            with open(status_file) as f:
                status = _json_str(_json_object(_json_loads(f.read())).get("status"))
        console.print(f"  {i}. {s} [{status or 'pending'}]")

    console.print("[bold]Enter session number:[/bold]")
    s_choice = session.prompt("> ").strip()
    try:
        s_idx = int(s_choice) - 1
        session_dir = os.path.join(sessions_dir, sessions[s_idx])
    except (ValueError, IndexError):
        console.print("[red]Invalid session.[/red]")
        return

    console.print("[bold]Transaction stage:[/bold]")
    console.print("  1. unsigned (no signatures yet)")
    console.print("  2. partial (some signatures collected)")
    console.print("  3. final (threshold met, ready to submit)")
    console.print("[bold]Enter stage number:[/bold]")
    stage_choice = session.prompt("> ").strip()
    stage_map = {"1": "unsigned", "2": "partial", "3": "final"}
    stage = stage_map.get(stage_choice)
    if not stage:
        console.print("[red]Invalid stage.[/red]")
        return

    console.print("[bold]Export path (default: ./<stage>_<session_id>.json):[/bold]")
    session_id = os.path.basename(session_dir)
    export_path = session.prompt("> ").strip()
    if not export_path:
        export_path = f"./{stage}_{session_id}.json"

    try:
        package = export_transaction_package(wallet_dir, session_dir, stage)
        _dump_json(package, export_path)
        console.print("\n[bold green]Transaction package exported![/bold green]")
        console.print(f"  Path: [cyan]{export_path}[/cyan]")
        console.print(f"  Stage: {stage}")
        console.print("\n[dim]This file contains the transaction CBOR, script, "
                      "and instructions[/dim]")
        console.print("[dim]for signing in CardanoInterface or cardano-cli.[/dim]")
    except Exception as e:
        console.print(f"[red]Failed to export transaction package: {e}[/red]")


def _participation_flow(wallet_dir: str, current_user: str,
                        user_password: str) -> None:
    """Answer 'which cosigner am I?' for a wallet, offering to unlock deeper.

    The cached key hashes cover only the usual positions, so the first answer
    is cache-only. A miss is not the end: unlocking a wallet searches its
    whole key tree, which is the only way a provider that placed the key at
    another account or address can be found. Every door that asks the
    question offers that second look — a recovering cosigner told 'you are an
    observer' by one door and offered a search by another would reasonably
    believe the first answer.
    """
    try:
        result = restore_multisig_participation(wallet_dir, current_user, user_password)
    except Exception as e:
        console.print(f"[red]Failed to check participation: {e}[/red]")
        return

    if result is not None and not result.get("matched"):
        console.print("\n[bold]Unlock a wallet and try again? (y/n)[/bold]")
        console.print("[dim]A wallet that has never been opened for this cannot be"
                      " checked without its password — and a provider may have"
                      " put[/dim]")
        console.print("[dim]your key beyond the usual position, so the search covers"
                      " the wallet's whole key tree.[/dim]")
        if _prompt_yes_no(default=True):
            unlocked = _prompt_wallet_unlock(current_user, user_password)
            if unlocked is None:
                return
            _name, _unlock_dir, unlock_password = unlocked
            try:
                restore_multisig_participation(wallet_dir, current_user, user_password,
                                               unlock_password=unlock_password)
            except Exception as e:
                console.print(f"[red]Failed to check participation: {e}[/red]")


def _interactive_restore_multisig(current_user: str, user_password: str) -> None:
    """Find out which cosigner this machine is, for a chosen multisig wallet."""
    console.print("\n[bold bright_cyan]Which Cosigner Am I?[/bold bright_cyan]")
    console.print("[dim]Matches your wallets against the script's key hashes, so "
                  "signing[/dim]")
    console.print("[dim]can pick the right wallet for you. Nothing is copied or "
                  "sent.[/dim]")

    wallet_dir = _select_multisig_wallet(current_user, user_password)
    if wallet_dir is None:
        return

    _participation_flow(wallet_dir, current_user, user_password)


def show_my_cosigner_key(current_user: str, user_password: str) -> None:
    """Show this machine's cosigner key hash, to send to a wallet's creator.

    A key hash is public by design (spec §Operating Model): it is what sits
    inside the script and on chain, and it carries no spending power. It is the
    only cosigner material that ever crosses machines.

    The hash comes from a wallet the user already has, so nobody has to retype
    24 words to take part in a multisig.
    """
    console.print("\n[bold bright_cyan]Show My Cosigner Key[/bold bright_cyan]")
    console.print("[dim]Send this to whoever is creating the multisig wallet.[/dim]")
    console.print("[dim]It is public: it cannot spend, and it reveals no other "
                  "address of yours.[/dim]")

    wallets = _personal_wallet_entries(current_user, user_password)
    if not wallets:
        console.print("\n[yellow]You have no ordinary wallets yet.[/yellow]")
        console.print("[dim]Create or import one first — that wallet is what signs "
                      "for you as a cosigner.[/dim]")
        return

    choice = _prompt_choice(
        "Which wallet do you want to be a cosigner with?",
        [name for name, _ in wallets],
        hint="Whichever you pick, that wallet's password is what you will sign with.",
    )
    if choice is None:
        console.print("[yellow]Cancelled.[/yellow]")
        return
    wallet_name, wallet_dir = wallets[choice]

    candidates = _read_cosigner_key_hash_cache(wallet_dir)
    if not candidates:
        console.print(f"[bold]Enter the password for '{wallet_name}':[/bold]")
        password = prompt_existing_password()
        try:
            candidates = _wallet_cosigner_key_hashes(wallet_dir, password)
        except Exception as e:
            console.print(f"[red]Could not open that wallet: {e}[/red]")
            return

    console.print(f"\n[bold green]Cosigner key hashes for '{wallet_name}':[/bold green]")
    for label, kh in candidates.items():
        console.print(f"\n  [dim]{label}[/dim]")
        console.print(f"  [bold cyan]{kh.hex()}[/bold cyan]")
    console.print("\n[dim]Send the personal-wallet hash unless the person building the[/dim]")
    console.print("[dim]script asked for the shared-wallet one. This program accepts[/dim]")
    console.print("[dim]either when you sign, so a script naming either key is signable "
                  "here.[/dim]")


def multisig_menu(current_user: str, user_password: str) -> None:
    """Nested submenu for all multisig wallet operations.

    Groups the multisig subsystem (spec §MULTISIG_SPEC) into one entry point
    off the main menu, organized into Wallets and Transactions sections.
    """
    # Grouped by the question the user is actually asking, and ordered the way a
    # wallet is lived with: look at it, get one, spend from it, hand it on.
    sections: list[tuple[str, list[tuple[str, Callable[[], object]]]]] = [
        ("Your wallets", [
            ("View wallets and balances",
             lambda: multisig_view_wallet(current_user, user_password)),
            ("Show my cosigner key",
             lambda: show_my_cosigner_key(current_user, user_password)),
        ]),
        ("Get a wallet", [
            ("Create a multisig wallet",
             lambda: multisig_wallet_create(current_user, user_password)),
            ("Recover or import (script .cbor, package, transaction)",
             lambda: _interactive_import_any(current_user, user_password)),
            ("Which cosigner am I?",
             lambda: _interactive_restore_multisig(current_user, user_password)),
        ]),
        ("Spend", [
            ("Build a transaction",
             lambda: _interactive_build_multisig(current_user, user_password)),
            ("Sign a transaction",
             lambda: _interactive_sign_multisig(current_user, user_password)),
            ("Assemble and submit",
             lambda: _interactive_assemble_multisig(current_user, user_password)),
            ("Delegate stake to a pool",
             lambda: _interactive_delegate_multisig(current_user, user_password)),
        ]),
        ("Share and back up", [
            ("Export wallet checkpoint (back up / send to a cosigner)",
             lambda: _interactive_export_wallet_checkpoint(current_user, user_password)),
            ("Export transaction package (send to a cosigner)",
             lambda: _interactive_export_transaction_package(current_user, user_password)),
        ]),
    ]
    actions: list[Callable[[], object] | None] = []
    for _heading, entries in sections:
        actions.extend(action for _label, action in entries)
    actions.append(None)  # Back

    title = f"[bold underline {PRIMARY_COLOR}]Multisig Wallets[/bold underline {PRIMARY_COLOR}]"
    prompt_msg = f"[bold {ACCENT_COLOR}]Choose an option (or 'back'):[/bold {ACCENT_COLOR}]"
    while True:
        console.print(f"\n{title}")
        n = 0
        for heading, entries in sections:
            console.print(f"[dim]  ── {heading} ──[/dim]")
            for label, _action in entries:
                n += 1
                console.print(f"[bold {TEXT_COLOR}]{n}. {label}[/bold {TEXT_COLOR}]")
        n += 1
        console.print(f"[bold {TEXT_COLOR}]{n}. Back to Main Menu[/bold {TEXT_COLOR}]")
        console.print(prompt_msg)
        choice = session.prompt("> ").strip()
        if choice.lower() in ("back", "exit"):
            return
        if not choice.isdigit() or int(choice) not in range(1, len(actions) + 1):
            console.print(f"[bold {ERROR_COLOR}]Invalid choice.[/bold {ERROR_COLOR}]")
            continue
        action = actions[int(choice) - 1]
        if action is None:
            return
        action()


def main_menu(current_user: str, user_password: str) -> None:
    show_framed_banner()  # <-- Always display banner and disclaimer at the top
    menu: dict[str, Callable[[], object] | None] = {
        "View Wallets": lambda: view_wallets(current_user, user_password),
        "Create Wallet": lambda: wallet_create(current_user, user_password),
        "Import Wallet": lambda: wallet_import(current_user, user_password),
        "Multisig Wallets": lambda: multisig_menu(current_user, user_password),
        "View Wallet Assets": lambda: tracker_lp(current_user, user_password),
        "Show Mnemonic Passphrase": lambda: show_mnemonic(current_user, user_password),
        "Send Funds": funds_send,
        "Delete Wallet": lambda: delete_wallet(current_user, user_password),
        "Delete User": delete_user,
        "Pool Registration": pool_registration,
        "DRep Registration": drep_registration,
        "Switch Backend": switch_backend,
        "Debug Backend Health": debug_backend_health,
        "Exit": lambda: sys.exit(0),
    }
    while True:
        console.print(f"\n[bold underline {PRIMARY_COLOR}]Main Menu"
            f"[/bold underline {PRIMARY_COLOR}]")
        for i, key in enumerate(menu.keys(), 1):
            console.print(f"[bold {TEXT_COLOR}]{i}. {key}[/bold {TEXT_COLOR}]")
        console.print(f"[bold {ACCENT_COLOR}]Please choose an option by number (or type 'exit' to"
            f"quit):[/bold {ACCENT_COLOR}]")
        choice = session.prompt("> ").strip()
        if choice.lower() == "exit":
            console.print(f"[bold {ACCENT_COLOR}]Goodbye![/bold {ACCENT_COLOR}]")
            sys.exit(0)
        if not choice.isdigit() or int(choice) not in range(1, len(menu) + 1):
            console.print(f"[bold {ERROR_COLOR}]Invalid choice, please enter a valid number or"
                f"'exit'.[/bold {ERROR_COLOR}]")
            continue
        action = list(menu.values())[int(choice) - 1]
        if action is None:
            return
        action()

BLOCKFROST_BASE_URL = ""
BLOCKFROST_PROJECT_ID = ""

def prompt_blockfrost_setup() -> None:
    global BLOCKFROST_BASE_URL, BLOCKFROST_PROJECT_ID, context, BACKEND_TYPE
    base_url = BLOCKFROST_URLS.get(SELECTED_NETWORK or "preprod", BLOCKFROST_URLS["preprod"])
    while True:
        console.print(f"[yellow]Enter your Blockfrost {SELECTED_NETWORK} API key:[/yellow]")
        BLOCKFROST_PROJECT_ID = session.prompt("> ").strip()
        if not BLOCKFROST_PROJECT_ID:
            console.print("[red]API key cannot be empty.[/red]")
            continue
        test_url = f"{base_url}/api/v0/epochs/latest/parameters"
        try:
            resp = requests.get(test_url, headers={"project_id": BLOCKFROST_PROJECT_ID}, timeout=10)
            if resp.status_code == 403:
                console.print(f"[red]Invalid API key for {SELECTED_NETWORK}. Please "
                    "try again.[/red]")
                continue
            resp.raise_for_status()
        except _REQUEST_ERROR as e:
            console.print(f"[red]Failed to validate API key: {e}[/red]")
            continue
        break

    BLOCKFROST_BASE_URL = base_url
    BACKEND_TYPE = "blockfrost"
    context = BlockfrostBackend(BLOCKFROST_BASE_URL, BLOCKFROST_PROJECT_ID)
    console.print(f"[green]Connected to {SELECTED_NETWORK} successfully![/green]")

def _normalize_local_endpoint(raw: str) -> str:
    """What a person types for a local endpoint, as a URL a backend can call.

    `localhost:1337` and `127.0.0.1:1442` are how these services are named
    everywhere except inside a URL, where the missing scheme is an instant
    MissingSchema failure that reports the node as unreachable — which reads,
    wrongly, as the endpoint being wrong.
    """
    trimmed = raw.strip().strip('"').strip("'")
    if "://" not in trimmed:
        trimmed = f"http://{trimmed}"
    return trimmed.rstrip("/")

def prompt_local_stack_setup() -> None:
    global context, BACKEND_TYPE
    default_port = DEFAULT_OGMIOS_PORT
    console.print(f"[yellow]Enter Ogmios URL (default: http://localhost:{default_port}):[/yellow]")
    url_input = session.prompt("> ").strip()
    ogmios_url = (_normalize_local_endpoint(url_input) if url_input
                  else f"http://localhost:{default_port}")
    console.print("[yellow]Connecting to local node...[/yellow]")
    try:
        ogmios = OgmiosBackend(ogmios_url)
        ogmios.detect_network()
        health = ogmios.health()
        if not health.get("is_healthy"):
            err = _json_str(health.get("error"), "unknown error")
            if "Connection refused" in err or "Errno" in err or "timeout" in err.lower():
                console.print(f"[red]Cannot connect to Ogmios at {ogmios_url}. Is your local node"
                    "running?[/red]")
            else:
                console.print(f"[red]Ogmios responded but is not healthy: {err}[/red]")
            return
    except Exception as e:
        console.print(f"[red]Cannot connect to Ogmios at {ogmios_url}: {e}[/red]")
        return
    detected = ogmios._detected_network_name
    if detected and SELECTED_NETWORK and detected != SELECTED_NETWORK:
        console.print(f"[red]Network mismatch: you selected {SELECTED_NETWORK} but Ogmios is on"
            f"{detected}.[/red]")
        return
    kupo_ok = False
    default_kupo = DEFAULT_KUPO_PORT
    console.print(f"[yellow]Enter Kupo URL (default: http://localhost:{default_kupo}):[/yellow]")
    kupo_input = session.prompt("> ").strip()
    kupo_url = (_normalize_local_endpoint(kupo_input) if kupo_input
                else f"http://localhost:{default_kupo}")
    try:
        kupo = KupoBackend(kupo_url)
        kupo.health()
        kupo_ok = True
    except Exception as e:
        console.print(f"[yellow]Kupo not reachable at {kupo_url} ({e}). Using "
            "Ogmios for UTxO queries (slower).[/yellow]")
    BACKEND_TYPE = "local"
    context = ogmios
    console.print(f"[green]Connected to local Ogmios ({detected}) at {ogmios_url}[/green]")
    if kupo_ok:
        console.print(f"[green]Kupo available at {kupo_url}[/green]")

def prompt_network_selection() -> None:
    global SELECTED_NETWORK
    console.print("[bold]Select a Cardano network:[/bold]")
    console.print("  1. Mainnet")
    console.print("  2. Preprod")
    console.print("  3. Preview")
    while True:
        choice = session.prompt("> ").strip()
        if choice in NETWORK_OPTIONS:
            SELECTED_NETWORK = NETWORK_OPTIONS[choice]
            console.print(f"[green]Network set to {SELECTED_NETWORK}[/green]")
            return
        console.print("[red]Invalid choice. Please enter 1, 2, or 3.[/red]")

def prompt_backend_setup() -> None:
    while not context:
        console.print("[bold]Select a backend:[/bold]")
        console.print("  1. Blockfrost (remote API)")
        console.print("  2. Local Node (Ogmios + Kupo)")
        choice = session.prompt("> ").strip()
        if choice == "1":
            prompt_blockfrost_setup()
        elif choice == "2":
            prompt_local_stack_setup()
        else:
            console.print("[red]Invalid choice. Please enter 1 or 2.[/red]")

def main() -> None:
    logging.debug("[ENTRY] main()")
    console.print("[bold yellow]Starting CardanoInterface...[/bold yellow]")

    while True:
        console.print("[bold]Do you want to (login/register/exit)?[/bold]")
        action = session.prompt("> ").strip().lower()
        if action == "register":
            creds = user_register()
            if creds is not None:
                user, pwd = creds
                if user is not None and pwd is not None:
                    prompt_network_selection()
                    prompt_backend_setup()
                    main_menu(user, pwd)
                    break
        elif action == "login":
            creds = user_login()
            if creds is not None:
                user, pwd = creds
                if user is not None and pwd is not None:
                    prompt_network_selection()
                    prompt_backend_setup()
                    main_menu(user, pwd)
                    break
        elif action == "exit":
            console.print("Goodbye!")
            sys.exit(0)
        else:
            console.print("[red]Invalid option. Please type 'login', 'register', or 'exit'.[/red]")
    logging.debug("[EXIT] main()")

def ensure_string(data: bytes | str) -> str:
    """Ensure the data is a string, converting if necessary."""
    if isinstance(data, bytes):
        return data.decode('utf-8', errors='ignore')  # Handle decode errors
    return data

def ensure_bytes(data: bytes | str) -> bytes:
    """Ensure the data is bytes, converting if necessary."""
    if isinstance(data, str):
        return data.encode('utf-8')
    return data

if __name__ == "__main__":
    main()

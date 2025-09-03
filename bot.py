import os, time, base64
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List, Tuple
import requests
import logging
from logging.handlers import TimedRotatingFileHandler

# -- load .env ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# =========================
# Logging (console + file)
# =========================
LOG_FILE = os.getenv("LOG_FILE", "logs/keepalive.log")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Ensure folder exists
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logger = logging.getLogger("keepalive")
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

# Console handler
ch = logging.StreamHandler()
ch.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
ch.setFormatter(fmt)
logger.addHandler(ch)

# Daily rotating file handler (keep 7 days)
fh = TimedRotatingFileHandler(LOG_FILE, when="D", interval=1, backupCount=7, encoding="utf-8")
fh.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
fh.setFormatter(fmt)
logger.addHandler(fh)

# =========================
# Config
# =========================
CHAIN = "solana"
CHAR_MINT = os.getenv("CHAR_MINT", "charyAhpBstVjf5VnszNiY8UUVDbvA167dQJqpBY2hw")
INPUT_MINT = os.getenv("INPUT_MINT")  # wSOL
PUBLIC_KEY = os.getenv("PUBLIC_KEY", "")
WALLET_SECRET_B58 = os.getenv("WALLET_SECRET_B58", "")
RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")

MICRO_BUY_USD = float(os.getenv("MICRO_BUY_USD"))
FALLBACK_BUY_USD = float(os.getenv("FALLBACK_BUY_USD"))
SLIPPAGE_BPS = int(os.getenv("SLIPPAGE_BPS", "100"))
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "900"))
MAX_RETRIES = 3
MAX_DAILY_USD = float(os.getenv("MAX_DAILY_USD", "1.00"))

# Flags
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"
FORCE_BUY_NOW = os.getenv("FORCE_BUY_NOW", "false").lower() == "true"
MOCK_INACTIVE = os.getenv("MOCK_INACTIVE", "false").lower() == "true"

# Endpoints
DEX_TOKENS_API = f"https://api.dexscreener.com/tokens/v1/{CHAIN}/{CHAR_MINT}"
JUP_QUOTE_API = "https://quote-api.jup.ag/v6/quote"
JUP_SWAP_API  = "https://quote-api.jup.ag/v6/swap"

# =========================
# HTTP helpers
# =========================
def http_get(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 15) -> Any:
    last_status, last_text = None, None
    for _ in range(MAX_RETRIES):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            last_status, last_text = r.status_code, r.text
            logger.warning(f"GET non-200 {r.status_code} url={url} body={r.text[:180]}")
        except Exception as e:
            last_status, last_text = "EXC", str(e)
            logger.warning(f"GET exception url={url} err={e}")
        time.sleep(1)
    raise RuntimeError(f"GET failed: {url} status={last_status} body={last_text}")

def http_post(url: str, payload: Dict[str, Any], timeout: int = 20) -> Any:
    last_status, last_text = None, None
    for _ in range(MAX_RETRIES):
        try:
            r = requests.post(url, json=payload, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            last_status, last_text = r.status_code, r.text
            logger.warning(f"POST non-200 {r.status_code} url={url} body={r.text[:180]}")
        except Exception as e:
            last_status, last_text = "EXC", str(e)
            logger.warning(f"POST exception url={url} err={e}")
        time.sleep(1)
    raise RuntimeError(f"POST failed: {url} status={last_status} body={last_text}")

# =========================
# Dexscreener activity check
# =========================
def has_no_trades_last_x(mins: int = 1440) -> bool:
    if MOCK_INACTIVE:
        return True
    data = http_get(DEX_TOKENS_API)
    if not isinstance(data, list) or not data:
        return True
    total_buys = total_sells = 0
    for p in data:
        txns = p.get("txns", {})
        if mins <= 5:
            bucket = txns.get("m5", {})
        elif mins <= 60:
            bucket = txns.get("h1", {})
        else:
            bucket = txns.get("h24", {})
        total_buys += int(bucket.get("buys", 0))
        total_sells += int(bucket.get("sells", 0))
    logger.debug(f"Activity window={mins}m buys={total_buys} sells={total_sells}")
    return (total_buys + total_sells) == 0

def activity_ok() -> bool:
    window = 1 if TEST_MODE else 1440
    ok = not has_no_trades_last_x(window)
    logger.info(f"Activity check window={window}m -> {'OK' if ok else 'INACTIVE'}")
    return ok

# =========================
# Quote helper
# =========================
def get_usd_to_input_mint_amount(usd_target: float) -> int:
    USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    lamports_per_sol = 1_000_000_000
    quote = http_get(JUP_QUOTE_API, params={
        "inputMint": INPUT_MINT,
        "outputMint": USDC,
        "amount": lamports_per_sol // 1000,
        "slippageBps": 50
    })
    out = int(quote["outAmount"])
    usdc_for_001_sol = out / 1_000_000
    usd_per_sol = max(usdc_for_001_sol * 1000.0, 0.01)
    sol_for_target = usd_target / usd_per_sol
    lamports = int(sol_for_target * lamports_per_sol)
    logger.debug(f"Quote usd_target={usd_target} -> lamports={lamports}")
    return max(1, lamports)

# =========================
# Wallet + transaction (solders)
# =========================
def ensure_wallet_ready():
    if DRY_RUN:
        logger.info("DRY_RUN=True, skipping wallet checks.")
        return
    if not PUBLIC_KEY or not WALLET_SECRET_B58:
        raise SystemExit("PUBLIC_KEY and WALLET_SECRET_B58 required.")
    from base58 import b58decode
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey
    from solana.rpc.api import Client
    kp = Keypair.from_bytes(b58decode(WALLET_SECRET_B58))
    pk = Pubkey.from_string(PUBLIC_KEY)
    if kp.pubkey() != pk:
        raise SystemExit("PUBLIC_KEY does not match WALLET_SECRET_B58")
    client = Client(RPC_URL)
    bal = client.get_balance(pk).value
    logger.info(f"Wallet balance: {bal/1_000_000_000:.6f} SOL")

# =========================
# Jupiter swap
# =========================
def build_and_send_swap(input_amount_raw: int) -> str:
    logger.info(f"Requesting swap quote input_amount_raw={input_amount_raw} slippage_bps={SLIPPAGE_BPS}")
    quote = http_get(JUP_QUOTE_API, params={
        "inputMint": INPUT_MINT,
        "outputMint": CHAR_MINT,
        "amount": input_amount_raw,
        "slippageBps": SLIPPAGE_BPS,
        "onlyDirectRoutes": "false",
        "asLegacyTransaction": "false"
    })

    if DRY_RUN:
        logger.warning(f"[DRY RUN] Would swap {input_amount_raw} units → CHAR")
        return "dryrun-sig"

    swap_req = {
        "quoteResponse": quote,
        "userPublicKey": PUBLIC_KEY,
        "wrapAndUnwrapSol": True,
        "useSharedAccounts": False,
        "dynamicComputeUnitLimit": True,
        "asLegacyTransaction": False,
        "prioritizationFeeLamports": "auto"
    }
    swap = http_post(JUP_SWAP_API, swap_req)
    tx_b64 = swap.get("swapTransaction")
    if not tx_b64:
        raise RuntimeError(f"Swap API returned no transaction: {swap}")

    # --- Sign & send using solders (Presigner) ---
    from solana.rpc.api import Client
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey
    from solders.transaction import VersionedTransaction
    from solders.message import to_bytes_versioned
    from solders.presigner import Presigner
    from base58 import b58decode
    import base64

    client = Client(RPC_URL)

    raw = base64.b64decode(tx_b64)           # unsigned v0 tx from Jupiter
    tx  = VersionedTransaction.from_bytes(raw)
    msg = tx.message

    msg_bytes = to_bytes_versioned(msg)
    kp  = Keypair.from_bytes(b58decode(WALLET_SECRET_B58))
    sig = kp.sign_message(msg_bytes)
    pk  = kp.pubkey()
    presigner = Presigner(pk, sig)

    signed_tx = VersionedTransaction(msg, [presigner])
    resp = client.send_raw_transaction(bytes(signed_tx))
    sig_str = getattr(resp, "value", None) or (resp.get("result") if isinstance(resp, dict) else None)
    if not sig_str:
        raise RuntimeError(f"send_raw_transaction failed: {resp}")
    logger.info(f"Swap sent: {sig_str}")
    return sig_str

# =========================
# Spend guard
# =========================
_last_spend_events: List[Tuple[datetime, float]] = []

def can_spend_usd(amount: float) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    while _last_spend_events and _last_spend_events[0][0] < cutoff:
        _last_spend_events.pop(0)
    spent = sum(a for _, a in _last_spend_events)
    ok = (spent + amount) <= MAX_DAILY_USD
    logger.debug(f"Spend guard: last24h={spent:.2f} adding={amount:.2f} cap={MAX_DAILY_USD:.2f} -> {ok}")
    return ok

def record_spend_usd(amount: float):
    _last_spend_events.append((datetime.now(timezone.utc), amount))
    logger.info(f"Recorded spend ${amount:.2f}; last24h total=${sum(a for _, a in _last_spend_events):.2f}")

# =========================
# Main logic
# =========================
def do_keepalive_once(usd_amount: float) -> str:
    if not can_spend_usd(usd_amount):
        raise RuntimeError(f"Daily cap reached, max ${MAX_DAILY_USD:.2f}")
    amt_raw = get_usd_to_input_mint_amount(usd_amount)
    sig = build_and_send_swap(amt_raw)
    if not DRY_RUN:
        record_spend_usd(usd_amount)
    return sig

def main_loop():
    logger.info(f"CharCoin keep-alive bot | TEST_MODE={TEST_MODE} DRY_RUN={DRY_RUN} FORCE_BUY_NOW={FORCE_BUY_NOW}")
    logger.info(f"Using bucket: {'m5 (~5m test)' if TEST_MODE else 'h24 (prod)'} | Interval={CHECK_INTERVAL_SECONDS}s")
    ensure_wallet_ready()

    if FORCE_BUY_NOW:
        try:
            sig = do_keepalive_once(MICRO_BUY_USD)
            logger.warning(f"[FORCE_BUY_NOW] ok, tx sig: {sig}")
        except Exception as e:
            logger.error(f"[FORCE_BUY_NOW] ERROR: {e}")
        return

    while True:
        try:
            if activity_ok():
                logger.info("Activity OK — no buy.")
            else:
                logger.warning(f"Inactive — trying ${MICRO_BUY_USD:.2f}")
                try:
                    sig = do_keepalive_once(MICRO_BUY_USD)
                    logger.warning(f"[BUY] success {sig}")
                except Exception as e:
                    logger.error(f"[BUY] primary failed: {e}")
                    if MICRO_BUY_USD < FALLBACK_BUY_USD and can_spend_usd(FALLBACK_BUY_USD):
                        try:
                            logger.warning(f"[BUY] retrying fallback ${FALLBACK_BUY_USD:.2f}")
                            sig = do_keepalive_once(FALLBACK_BUY_USD)
                            logger.warning(f"[BUY] fallback success {sig}")
                        except Exception as e2:
                            logger.error(f"[BUY] fallback failed: {e2}")
        except Exception as e:
            logger.exception(f"[LOOP ERROR] {e}")
        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    main_loop()

import os, time, base64, logging, requests
from datetime import datetime, timezone
from dotenv import load_dotenv
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction
from solders.message import to_bytes_versioned
from solders.presigner import Presigner
from solana.rpc.api import Client
from base58 import b58decode

# === Load ENV ===
load_dotenv()

RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")
PUBLIC_KEY = os.getenv("PUBLIC_KEY")
WALLET_SECRET_B58 = os.getenv("WALLET_SECRET_B58")
CHAR_MINT = os.getenv("CHAR_MINT", "charyAhpBstVjf5VnszNiY8UUVDbvA167dQJqpBY2hw")
INPUT_MINT = os.getenv("INPUT_MINT", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB")

MICRO_BUY_USD = float(os.getenv("MICRO_BUY_USD", "0.01"))
FALLBACK_BUY_USD = float(os.getenv("FALLBACK_BUY_USD", "0.10"))
SLIPPAGE_BPS = int(os.getenv("SLIPPAGE_BPS", "500"))
SCHEDULE_HOURS = 6

# === Jupiter Lite APIs ===
QUOTE_API = "https://lite-api.jup.ag/swap/v1/quote"
SWAP_API  = "https://lite-api.jup.ag/swap/v1/swap"

headers = {"Content-Type": "application/json"}

# === Logging ===
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("charcoin-bot")

# === Helpers ===
def get_usdt_amount(usd: float) -> int:
    """Convert 1 USDT ≈ 1 USD → smallest unit (6 decimals)."""
    return int(usd * 1_000_000)

def get_quote(amount_usdt: int):
    params = {
        "inputMint": INPUT_MINT,  # USDT
        "outputMint": CHAR_MINT,  # CHAR
        "amount": str(amount_usdt),
        "slippageBps": str(SLIPPAGE_BPS),
        "onlyDirectRoutes": "true",            # 🚀 Force single pool
        "restrictIntermediateTokens": "true",  # 🚫 Prevent USDT→SOL→CHAR
    }
    r = requests.get(QUOTE_API, params=params, headers=headers)
    if r.status_code != 200:
        raise RuntimeError(f"Quote failed: {r.status_code} {r.text}")
    return r.json()


def execute_swap(quote):
    swap_req = {
        "quoteResponse": quote,
        "userPublicKey": PUBLIC_KEY,
        "dynamicComputeUnitLimit": True,
        "dynamicSlippage": True,
        "wrapAndUnwrapSol": True
    }

    r = requests.post(SWAP_API, json=swap_req, headers=headers)
    if r.status_code != 200:
        raise RuntimeError(f"Swap API failed: {r.status_code} {r.text}")

    tx_b64 = r.json().get("swapTransaction")
    if not tx_b64:
        raise RuntimeError("No transaction in swap response")

    client = Client(RPC_URL)
    kp = Keypair.from_bytes(b58decode(WALLET_SECRET_B58))
    raw_tx = base64.b64decode(tx_b64)
    tx = VersionedTransaction.from_bytes(raw_tx)
    msg_bytes = to_bytes_versioned(tx.message)

    sig = kp.sign_message(msg_bytes)
    signed_tx = VersionedTransaction(tx.message, [Presigner(kp.pubkey(), sig)])
    resp = client.send_raw_transaction(bytes(signed_tx))
    sig_str = getattr(resp, "value", None) or resp.get("result")

    if not sig_str:
        raise RuntimeError(f"Transaction send failed: {resp}")

    logger.info(f"✅ Swap sent: {sig_str}")
    return sig_str

# === Wallet Check ===
def ensure_wallet():
    if not PUBLIC_KEY or not WALLET_SECRET_B58:
        raise SystemExit("PUBLIC_KEY and WALLET_SECRET_B58 required")
    client = Client(RPC_URL)
    pk = Pubkey.from_string(PUBLIC_KEY)
    bal = client.get_balance(pk).value / 1_000_000_000
    logger.info(f"Wallet {pk} balance: {bal:.6f} SOL")

# === Main Bot ===
def run_bot():
    logger.info("🚀 CharCoin Keep-Alive Bot Started")
    ensure_wallet()

    while True:
        try:
            logger.info(f"🕐 Scheduled Buy Triggered: ${MICRO_BUY_USD:.2f} USDT → CHAR")
            amt = get_usdt_amount(MICRO_BUY_USD)
            quote = get_quote(amt)
            execute_swap(quote)
        except Exception as e:
            logger.error(f"❌ Buy failed: {e}")
            logger.info("Retrying with fallback...")
            try:
                amt = get_usdt_amount(FALLBACK_BUY_USD)
                quote = get_quote(amt)
                execute_swap(quote)
            except Exception as e2:
                logger.error(f"Fallback failed: {e2}")

        logger.info(f"⏳ Sleeping for {SCHEDULE_HOURS} hours...\n")
        time.sleep(SCHEDULE_HOURS * 3600)

if __name__ == "__main__":
    run_bot()

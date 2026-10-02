
# CharCoin Keep-Alive Bot

This bot keeps **CharCoin (CHAR)** visible on [Dexscreener](https://dexscreener.com) by ensuring at least one trade happens every 24 hours.  
If no activity is detected within 24h, the bot automatically performs a **tiny buy ($0.10–$1.00)** via Jupiter on Solana.  

## ✨ Features
- Checks Dexscreener API for CHAR trades in the past 24h  
- If no trades → executes a micro-buy using your Solana wallet  
- Configurable buy amount, slippage, and check interval  
- Uses **Dexscreener free API** + **Jupiter swap API** (no extra cost)  
- Prevents graphs & data in the DAPP from collapsing
<!-- updated: 2026-06-18 -->

## Setup
```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in your wallet details
python bot.py
```
The bot runs in a loop and re-checks every 6 hours.

## Configuration
Set these in `.env`:

| Variable | Default | Description |
|---|---|---|
| `PUBLIC_KEY` | - | Public key of the wallet that buys |
| `WALLET_SECRET_B58` | - | Base58 secret key of that wallet (keep private) |
| `RPC_URL` | `https://api.mainnet-beta.solana.com` | Solana RPC endpoint |
| `CHAR_MINT` | CHAR mint address | Token to keep active |
| `INPUT_MINT` | USDT mint address | Token spent on the buy |
| `MICRO_BUY_USD` | `0.01` | Size of the micro-buy in USD |
| `FALLBACK_BUY_USD` | `0.10` | Size used when the micro-buy cannot be quoted |
| `SLIPPAGE_BPS` | `500` | Slippage tolerance in basis points |

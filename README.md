
# CharCoin Keep-Alive Bot

This bot keeps **CharCoin (CHAR)** visible on [Dexscreener](https://dexscreener.com) by ensuring at least one trade happens every 24 hours.  
If no activity is detected within 24h, the bot automatically performs a **tiny buy ($0.10–$1.00)** via Jupiter on Solana.  

## ✨ Features
- Checks Dexscreener API for CHAR trades in the past 24h  
- If no trades → executes a micro-buy using your Solana wallet  
- Configurable buy amount, slippage, and check interval  
- Uses **Dexscreener free API** + **Jupiter swap API** (no extra cost)  
- Prevents graphs & data in the DAPP from collapsing  


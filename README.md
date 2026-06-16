# Crypto AI Agent

Agent Python untuk memantau harga crypto dan membuat analisis teknikal otomatis.

## Fitur

- Ambil data OHLCV dari exchange via `ccxt`.
- Ambil harga Binance USDⓈ-M Futures real-time via WebSocket.
- Pantau banyak pair sekaligus.
- Scan otomatis top 100 coin crypto berdasarkan market cap CoinGecko.
- Analisis indikator:
  - EMA 20/50/200
  - RSI
  - MACD
  - ATR
  - ADX
  - Fibonacci retracement
- Analisis market structure:
  - Support/resistance
  - Order block sederhana
  - Liquidity sweep
  - Trend detector
  - Regime detector: `TRENDING`, `RANGING`, `TRANSITION`
- Deteksi sinyal:
  - `HOLD`
  - `BUY WATCH`
  - `SELL WATCH`
  - `BULLISH CONTINUATION`
  - `BEARISH CONTINUATION`
- Risk plan sederhana:
  - Stop loss
  - Take profit
  - Risk/reward
- Alert Telegram opsional.

## Instalasi

```bash
cd crypto-ai-agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell:

```powershell
cd crypto-ai-agent
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Jika `pandas-ta==0.3.14b0` gagal diinstall:

```bash
pip install pandas-ta
```

## Konfigurasi env

Salin file env:

```bash
cp .env.example .env
```

Edit `.env`:

```env
EXCHANGE=binance
SYMBOLS=BTC/USDT,ETH/USDT,SOL/USDT
TIMEFRAME=1h
FETCH_LIMIT=250
SCAN_INTERVAL_SECONDS=3600

# Preferred: scan top tradable Binance USDT pairs by 24h volume
USE_BINANCE_TOP_VOLUME=true
BINANCE_TOP_VOLUME_LIMIT=100
BINANCE_TOP_VOLUME_QUOTE=USDT
BINANCE_TOP_VOLUME_MARKET_TYPE=spot

# Optional fallback: scan coin market cap terbesar dari CoinGecko
USE_TOP_MARKETCAP=false
TOP_MARKETCAP_LIMIT=100
TOP_MARKETCAP_QUOTE=USDT
INCLUDE_STABLECOINS=false

# Save generated action calls to datasets/action_calls.jsonl and datasets/action_calls.csv
SAVE_ACTION_DATASET=true
DATABASE_ENABLED=false
DATABASE_URL=postgresql://crypto_agent:crypto_agent@localhost:5432/crypto_ai_agent

# evaluate mode labels pending dataset rows as WIN/LOSS/OPEN by checking TP/SL hits
EVALUATION_FETCH_LIMIT=250
EVALUATION_MAX_ROWS=

# Optional AI review filter for action calls: gemini, openai_compatible, custom
AI_MODEL_ENABLED=false
AI_MODEL_PROVIDER=gemini
AI_MODEL_API_KEY=
GEMINI_API_KEY=
AI_MODEL_NAME=gemini-1.5-flash
AI_MODEL_BASE_URL=
AI_MODEL_TIMEOUT=30
AI_MODEL_MIN_SCORE=0.6

# rest = OHLCV REST scanner, websocket = Binance USDⓈ-M Futures mark price realtime, evaluate = label dataset outcomes, dashboard = web dashboard
MARKET_DATA_MODE=rest
REALTIME_PRINT_INTERVAL_SECONDS=5
DASHBOARD_HOST=0.0.0.0
DASHBOARD_PORT=8000
DASHBOARD_AUTO_SCAN=false
DASHBOARD_AUTO_SCAN_INTERVAL_SECONDS=900
DASHBOARD_AUTO_EVALUATE=true
DASHBOARD_AUTO_EVALUATE_INTERVAL_SECONDS=300

TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
ALERT_ONLY_SIGNALS=true
```

Jika Telegram kosong, agent tetap jalan dan mencetak hasil di terminal.

## Konfigurasi strategi

Edit `config.yaml`:

```yaml
indicators:
  ema_fast: 20
  ema_mid: 50
  ema_slow: 200
  rsi_length: 14
  macd_fast: 12
  macd_slow: 26
  macd_signal: 9
  atr_length: 14
  adx_length: 14

structure:
  swing_lookback: 3
  sr_lookback: 120
  fibonacci_lookback: 120
  order_block_lookback: 80
  liquidity_sweep_lookback: 20

rules:
  rsi_oversold: 30
  rsi_overbought: 70
  adx_trend_threshold: 25
  adx_range_threshold: 18
  atr_volatility_multiplier: 1.5
  min_risk_reward: 1.2
  enable_trend_following_calls: true
  enable_multi_timeframe_action_calls: true
```

Algoritma action call multi-timeframe aktif secara default:

```text
5m = pencarian entry price
15m dan 30m = konfirmasi setup
1h dan 4h = arah tren utama
```

Action call hanya dibuat jika arah 1h dan 4h sejalan, 15m dan 30m mengonfirmasi arah tren, lalu 5m memberi entry dengan risk/reward valid.

## Menjalankan agent

Mode WebSocket real-time futures:

```bash
MARKET_DATA_MODE=websocket python main.py
```

Output contoh:

```text
2026-06-15 10:00:01 BTCUSDT mark price: 67250.12
2026-06-15 10:00:01 ETHUSDT mark price: 3512.45
```

Mode REST scanner OHLCV + analisis teknikal:

```bash
MARKET_DATA_MODE=rest python main.py
```

Scan top 100 Binance USDT pair by 24h volume:

```bash
USE_BINANCE_TOP_VOLUME=true BINANCE_TOP_VOLUME_LIMIT=100 MARKET_DATA_MODE=rest python main.py
```

Agent mengambil pair yang benar-benar tersedia di Binance, mengurutkan berdasarkan volume 24 jam, lalu memproses 100 pair teratas. Mode CoinGecko market cap tetap tersedia sebagai fallback dengan `USE_TOP_MARKETCAP=true`, tetapi Binance top volume lebih direkomendasikan agar tidak banyak symbol gagal.

Jika `SAVE_ACTION_DATASET=true`, setiap action call non-`HOLD` disimpan ke:

```text
datasets/action_calls.jsonl
datasets/action_calls.csv
```

Dataset berisi parameter entry, TP, SL, indikator, struktur market, alasan sinyal, dan kolom outcome awal `PENDING` untuk dilabeli setelah harga menyentuh TP/SL.

Label dataset pending:

```bash
MARKET_DATA_MODE=evaluate python main.py
```

Evaluator mengambil candle terbaru, lalu memberi label:

```text
WIN  = TP tersentuh dulu
LOSS = SL tersentuh dulu
OPEN = TP/SL belum tersentuh
```

Jika TP dan SL tersentuh dalam candle yang sama, evaluator memilih `LOSS` secara konservatif karena urutan intrabar tidak diketahui.

AI review opsional:

```bash
AI_MODEL_ENABLED=true AI_MODEL_PROVIDER=gemini GEMINI_API_KEY=xxx python main.py
```

Provider tersedia:

```text
gemini
openai_compatible
custom
```

AI review memberi `APPROVE`, `REJECT`, atau `WAIT` plus score 0-1. Jika AI aktif, Telegram hanya mengirim action call yang `APPROVE` dan `score >= AI_MODEL_MIN_SCORE`. Field AI juga disimpan ke dataset: `ai_provider`, `ai_model`, `ai_decision`, `ai_score`, `ai_reason`.

Dashboard website:

```bash
MARKET_DATA_MODE=dashboard python main.py
```

Buka browser:

```text
http://localhost:8000/dashboard
```

Endpoint tersedia:

```text
GET  /dashboard
GET  /api/action-calls
GET  /api/stats
GET  /api/jobs
GET  /api/export/training.jsonl
GET  /api/export/training.csv
POST /api/scan
POST /api/evaluate
```

Endpoint export training default hanya mengeluarkan row berlabel `WIN`/`LOSS` dan menambahkan target numerik `WIN=1`, `LOSS=0`. Gunakan `?labelled_only=false` jika ingin export semua row termasuk `OPEN`/`PENDING`.

PostgreSQL opsional:

```bash
DATABASE_ENABLED=true
DATABASE_URL=postgresql://crypto_agent:crypto_agent@localhost:5432/crypto_ai_agent
```

Saat aktif, action call dan update evaluator akan di-mirror ke tabel `action_calls`. JSONL/CSV tetap menjadi fallback lokal.

Dashboard menampilkan total call, winrate, WIN/LOSS/OPEN/PENDING, tabel action call terbaru, tombol manual `Run Scan`, dan tombol `Evaluate TP/SL`. Dashboard juga bisa menjalankan auto-evaluate setiap `DASHBOARD_AUTO_EVALUATE_INTERVAL_SECONDS` dan auto-scan setiap `DASHBOARD_AUTO_SCAN_INTERVAL_SECONDS` jika diaktifkan.

REST scanner langsung scan sekali, lalu mengulang sesuai `SCAN_INTERVAL_SECONDS`.

Catatan: mode WebSocket saat ini mengambil `markPrice@1s` Binance USDⓈ-M Futures. Analisis teknikal tetap memakai data OHLCV REST karena indikator membutuhkan candle, bukan tick price.

## Logika analisis

Trend detector:

```text
UPTREND   = EMA 20 > EMA 50 > EMA 200 dan +DI > -DI
DOWNTREND = EMA 20 < EMA 50 < EMA 200 dan -DI > +DI
SIDEWAYS  = kondisi lain
```

Regime detector:

```text
TRENDING   = ADX >= 25
RANGING    = ADX <= 18
TRANSITION = ADX di antara threshold
```

Liquidity sweep:

```text
BUY_SIDE_LIQUIDITY_SWEEP  = high menembus high sebelumnya, close kembali di bawahnya
SELL_SIDE_LIQUIDITY_SWEEP = low menembus low sebelumnya, close kembali di atasnya
```

Order block sederhana:

```text
Bullish OB = candle bearish terakhir sebelum impulse bullish kuat
Bearish OB = candle bullish terakhir sebelum impulse bearish kuat
```

Catatan: order block dan liquidity sweep di versi ini memakai heuristik sederhana. Untuk trading serius, validasi dengan backtest dan aturan entry yang lebih ketat.

## Struktur project

```text
crypto-ai-agent/
├── main.py
├── requirements.txt
├── config.yaml
├── .env.example
├── README.md
└── src/
    ├── __init__.py
    ├── alert.py
    ├── analyzer.py
    ├── config.py
    ├── data.py
    └── indicators.py
```

## Catatan risiko

Ini bukan nasihat keuangan. Sinyal teknikal bisa salah. Gunakan untuk monitoring dan edukasi, bukan eksekusi trading otomatis tanpa risk management dan backtest.

import time
import schedule
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.console import Console
from rich.panel import Panel

from src.action_call import build_action_call
from src.ai_model import AIModelSettings, review_action_call
from src.alert import format_report, send_telegram
from src.analyzer import analyze
from src.binance_universe import fetch_binance_top_usdt_symbols
from src.config import load_settings, load_strategy_config
from src.data import MarketDataClient, get_market_data_client
from src.dataset import save_action_call_dataset, save_action_call_rows_to_postgres
from src.evaluator import evaluate_pending_action_calls
from src.futures_ws import BinanceUSDMFuturesWebSocket, RealtimePrice
from src.indicators import add_indicators
from src.marketcap import coins_to_quote_symbols, fetch_top_marketcap_coins
from src.multi_timeframe import analyze_multi_timeframe, is_multi_timeframe_enabled

console = Console()
last_realtime_print: dict[str, float] = {}
SCAN_WORKERS = 6



def resolve_symbols(settings) -> list[str]:
    if settings.use_binance_top_volume:
        symbols = fetch_binance_top_usdt_symbols(
            limit=settings.binance_top_volume_limit,
            quote=settings.binance_top_volume_quote,
            include_stablecoins=settings.include_stablecoins,
            market_type=settings.binance_top_volume_market_type,
        )
        console.print(
            f"[green]Loaded top {len(symbols)} Binance {settings.binance_top_volume_quote} pairs "
            f"by 24h volume[/green]"
        )
        return symbols

    if not settings.use_top_marketcap:
        return settings.symbols

    coins = fetch_top_marketcap_coins(
        settings.top_marketcap_limit,
        include_stablecoins=settings.include_stablecoins,
    )
    symbols = coins_to_quote_symbols(coins, settings.top_marketcap_quote)
    console.print(
        f"[green]Loaded top {len(symbols)} market-cap coins from CoinGecko "
        f"as {settings.top_marketcap_quote} pairs[/green]"
    )
    return symbols


def scan_once() -> None:
    settings = load_settings()
    strategy_config = load_strategy_config()
    client = get_market_data_client(settings.exchange)
    ai_settings = AIModelSettings(
        enabled=settings.ai_model_enabled,
        provider=settings.ai_model_provider,
        api_key=settings.ai_model_api_key,
        model=settings.ai_model_name,
        base_url=settings.ai_model_base_url,
        timeout=settings.ai_model_timeout,
        min_score=settings.ai_model_min_score,
    )

    symbols = resolve_symbols(settings)
    analysis_results: list = []

    def analyze_symbol(symbol: str):
        if is_multi_timeframe_enabled(strategy_config):
            return analyze_multi_timeframe(symbol, client, strategy_config, settings.fetch_limit)
        raw_df = client.fetch_ohlcv(symbol, settings.timeframe, settings.fetch_limit)
        df = add_indicators(raw_df, strategy_config)
        return analyze(symbol, settings.timeframe, df, strategy_config)

    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as executor:
        future_map = {executor.submit(analyze_symbol, symbol): symbol for symbol in symbols}
        for future in as_completed(future_map):
            symbol = future_map[future]
            try:
                analysis_results.append((symbol, future.result()))
            except Exception as error:
                console.print(f"[red]Error scan {symbol}: {error}[/red]")

    actionable_symbols = [symbol for symbol, result in analysis_results if build_action_call(result) is not None]
    realtime_prices: dict[str, float] = {}
    if actionable_symbols:
        try:
            realtime_prices = client.fetch_ticker_prices(actionable_symbols)
        except Exception as error:
            console.print(f"[yellow]Realtime price bulk fetch error: {error}[/yellow]")

    dataset_rows: list = []
    for symbol, result in analysis_results:
        try:
            realtime_price = realtime_prices.get(symbol)
            ai_review = review_action_call(result, ai_settings)
            report = format_report(result, ai_review, realtime_price)
            console.print(Panel(report, title=symbol))

            if settings.save_action_dataset:
                dataset_row = save_action_call_dataset(result, ai_review, realtime_price, mirror_postgres=False)
                if dataset_row:
                    dataset_rows.append(dataset_row)
                    console.print(f"[green]Saved action dataset: {symbol} {dataset_row['action']}[/green]")

            ai_approved = ai_review is None or (ai_review.decision == "APPROVE" and ai_review.score >= settings.ai_model_min_score)
            should_alert = (not settings.alert_only_signals or result.signal != "HOLD") and ai_approved
            if should_alert:
                send_telegram(settings.telegram_bot_token, settings.telegram_chat_id, report)
        except Exception as error:
            console.print(f"[red]Error post-process {symbol}: {error}[/red]")

    if dataset_rows:
        save_action_call_rows_to_postgres(dataset_rows)


def run_rest_scheduler() -> None:
    settings = load_settings()
    scan_once()
    schedule.every(settings.scan_interval_seconds).seconds.do(scan_once)

    while True:
        schedule.run_pending()
        time.sleep(1)


def handle_realtime_price(price: RealtimePrice) -> None:
    settings = load_settings()
    now = time.time()
    last_print = last_realtime_print.get(price.symbol, 0)

    if now - last_print < settings.realtime_print_interval_seconds:
        return

    last_realtime_print[price.symbol] = now
    event_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(price.event_time / 1000))
    console.print(f"[cyan]{event_time}[/cyan] {price.symbol} mark price: [bold]{price.price}[/bold]")


def run_realtime_ws() -> None:
    settings = load_settings()
    console.print("Starting Binance USDⓈ-M Futures realtime WebSocket...")
    symbols = resolve_symbols(settings)
    console.print(f"Symbols: {', '.join(symbols)}")

    ws_client = BinanceUSDMFuturesWebSocket(symbols, handle_realtime_price)
    ws_client.run_forever()


def run_evaluator() -> None:
    settings = load_settings()
    stats = evaluate_pending_action_calls(
        exchange_name=settings.exchange,
        timeframe=settings.timeframe,
        fetch_limit=settings.evaluation_fetch_limit,
        max_rows=settings.evaluation_max_rows,
    )
    console.print(Panel(str(stats), title="Action Dataset Evaluation"))


def run_dashboard() -> None:
    import uvicorn

    settings = load_settings()
    console.print(f"Dashboard running at http://localhost:{settings.dashboard_port}/dashboard")
    uvicorn.run("src.web:app", host=settings.dashboard_host, port=settings.dashboard_port, reload=False)


def main() -> None:
    settings = load_settings()

    if settings.market_data_mode == "websocket":
        run_realtime_ws()
    elif settings.market_data_mode == "evaluate":
        run_evaluator()
    elif settings.market_data_mode == "dashboard":
        run_dashboard()
    else:
        run_rest_scheduler()


if __name__ == "__main__":
    main()

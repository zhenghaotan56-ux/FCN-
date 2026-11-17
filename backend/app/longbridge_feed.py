"""Longbridge OpenAPI equity metadata, regular-session trades and daily bars."""

from functools import lru_cache
from threading import Lock

from .market import MarketUnavailable
from .terms import InputError, calendar

SOURCE = "长桥 OpenAPI"
context_lock = Lock()


@lru_cache(maxsize=1)
def quote_context():
    from longbridge.openapi import Config, QuoteContext

    return QuoteContext(Config.from_apikey_env())


def fetch(instrument, start, end, active):
    try:
        from longbridge.openapi import AdjustType, Period, SecurityBoard, TradeStatus, TradeSessions
    except ImportError as error:
        raise MarketUnavailable("请安装行情依赖 uv sync --locked --extra live。") from error

    hk = instrument["currency"] == "HKD"
    ticker = str(int(instrument["ticker"])) if hk else instrument["ticker"]
    symbol = f"{ticker}.{'HK' if hk else 'US'}"
    try:
        with context_lock:
            context = quote_context()
        metadata = context.static_info([symbol])
        if not metadata:
            raise InputError("长桥未找到该股票代码。")
        info = metadata[0]
        boards = (SecurityBoard.HKEquity,) if hk else (SecurityBoard.USMain, SecurityBoard.USPink)
        if info.symbol.upper() != symbol or info.board not in boards or info.currency != instrument["currency"]:
            raise InputError("该代码不属于支持的港币或美元股票标的。")
        mic = {"NASD": "XNAS", "NASDAQ": "XNAS", "NYSE": "XNYS", "AMEX": "XASE", "ARCA": "ARCX",
               "BATS": "BATS", "OTC": "OTC"}.get(info.exchange.upper(), "US")
        if info.board == SecurityBoard.USPink:
            mic = "OTC"
        requested_mic = instrument["id"].split(":", 1)[0]
        if not hk and requested_mic not in {"US", mic}:
            raise InputError("股票代码与指定的美国交易所不匹配。")
        resolved = {**instrument, "name": (info.name_cn or info.name_en or instrument["ticker"])[:120]}
        if not hk:
            resolved.update(id=f"{mic}:{ticker}", calendar="NASDAQ" if mic == "XNAS" else "NYSE")

        quote = None
        if active:
            quotes = context.quote([symbol])
            if not quotes or quotes[0].symbol.upper() != symbol or quotes[0].trade_status != TradeStatus.Normal:
                raise MarketUnavailable("标的当前停牌或未返回可用的盘中交易价格。")
            quote = {"price": quotes[0].last_done, "timestamp": quotes[0].timestamp.isoformat()}

        exchange_tz = calendar(resolved["calendar"]).tz

        def closes(adjustment):
            bars = context.history_candlesticks_by_date(
                symbol, Period.Day, adjustment, start, end, TradeSessions.Intraday,
            )
            values = {}
            for bar in bars:
                day = bar.timestamp.astimezone(exchange_tz).date().isoformat()
                if day in values:
                    raise MarketUnavailable("长桥日线包含重复日期。")
                values[day] = bar.close
            return values

        raw = closes(AdjustType.NoAdjust)
        adjusted = closes(AdjustType.ForwardAdjust)
        rows = [{"date": day, "close": close, "adjusted_close": adjusted[day]}
                for day, close in raw.items() if day in adjusted]
        return resolved, rows, quote, info.dividend_yield
    except (InputError, MarketUnavailable):
        raise
    except Exception as error:
        raise MarketUnavailable("长桥行情请求失败，请检查服务端凭证、港美股行情权限、额度和网络。") from error

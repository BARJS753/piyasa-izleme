#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Profesyonel Piyasa İzleme ve Analiz Platformu v5.3
Güncellemeler:
- Takip listesi 70 sembolle güncellendi
- ATR (Oynaklık) göstergesi eklendi
- Çift yedekli haber çekme sistemi (get_news + ticker.news)
- Hız için MAX_WORKERS=10, NEWS_LIMIT=5
"""

from __future__ import annotations

import logging
import math
import os
import re
import sys
import time
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# os.environ["FRED_API_KEY"] = "bf16c2cec418fb4b295c092f40439f9a"

try:
    import yfinance as yf
except ImportError:
    sys.exit("Hata: 'yfinance' kütüphanesi eksik.")

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
    from rich import box
    RICH = True
except ImportError:
    RICH = False

try:
    import pandas as pd
    PANDAS = True
except ImportError:
    PANDAS = False

try:
    import numpy as np
    NUMPY = True
except ImportError:
    NUMPY = False

try:
    import torch
    if torch.cuda.is_available(): DEVICE = 0
    else: DEVICE = -1
except ImportError:
    DEVICE = -1

FINBERT = None
try:
    from transformers import pipeline
    FINBERT = pipeline("sentiment-analysis", model="ProsusAI/finbert", truncation=True, max_length=512, device=DEVICE)
except Exception as e:
    print(f"Uyarı: FinBERT yüklenemedi, kelime tabanlı analiz kullanılacak. {e}")

# ============================================================
# KONFİGÜRASYON
# ============================================================

class Config:
    FRED_API_KEY = os.getenv("FRED_API_KEY", "").strip()
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    MAX_WORKERS = 10
    NEWS_LIMIT = 5
    EXPORT_DIR = Path("raporlar")
    EXPORT_DIR.mkdir(exist_ok=True)

    TAKIP_LISTESI = {
        "abd_hisseleri": {
            "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "Nvidia", "GOOGL": "Alphabet",
            "AMZN": "Amazon", "META": "Meta Platforms", "TSLA": "Tesla", "AVGO": "Broadcom",
            "BRK-B": "Berkshire Hathaway", "LLY": "Eli Lilly", "JPM": "JPMorgan Chase",
            "V": "Visa", "WMT": "Walmart", "PG": "Procter & Gamble", "HD": "Home Depot",
            "DIS": "Walt Disney", "NFLX": "Netflix", "ADBE": "Adobe", "CRM": "Salesforce",
            "ORCL": "Oracle", "AMD": "AMD", "INTC": "Intel", "PFE": "Pfizer",
            "KO": "Coca-Cola", "PEP": "PepsiCo",
        },
        "bist_hisseleri": {
            "THYAO.IS": "Türk Hava Yolları", "ASELS.IS": "Aselsan", "BIMAS.IS": "BİM",
            "GARAN.IS": "Garanti BBVA", "AKBNK.IS": "Akbank", "ISCTR.IS": "İş Bankası C",
            "EREGL.IS": "Ereğli", "SASA.IS": "Sasa Polyester", "PETKM.IS": "Petkim",
            "TUPRS.IS": "Tüpraş", "KCHOL.IS": "Koç Holding", "SISE.IS": "Şişe Cam",
            "FROTO.IS": "Ford Otosan", "TOASO.IS": "Tofaş Oto", "TCELL.IS": "Turkcell",
            "EKGYO.IS": "Emlak Konut GYO", "SAHOL.IS": "Sabancı Holding", "VAKBN.IS": "VakıfBank",
            "HALKB.IS": "Halkbank", "YKBNK.IS": "Yapı Kredi", "ARCLK.IS": "Arçelik",
            "AEFES.IS": "Anadolu Efes", "AGHOL.IS": "AG Anadolu Grubu", "ALARK.IS": "Alarko Holding",
            "AYGAZ.IS": "Aygaz",
        },
        "kripto": {
            "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "BNB-USD": "Binance Coin",
            "SOL-USD": "Solana", "XRP-USD": "Ripple", "ADA-USD": "Cardano",
            "DOGE-USD": "Dogecoin", "DOT-USD": "Polkadot", "LTC-USD": "Litecoin",
            "LINK-USD": "Chainlink", "AVAX-USD": "Avalanche", "UNI-USD": "Uniswap",
            "ATOM-USD": "Cosmos", "ETC-USD": "Ethereum Classic", "XLM-USD": "Stellar",
        },
        "doviz_emtia": {
            "USDTRY=X": "Dolar/TL", "EURUSD=X": "Euro/Dolar",
            "GC=F": "Altın Vadeli", "SI=F": "Gümüş Vadeli", "CL=F": "Ham Petrol",
        },
    }

    FRED_SERIES = {
        "FEDFUNDS": {"isim": "Fed Fon Faizi", "birim": "%", "tip": "rate"},
        "CPIAUCSL": {"isim": "TÜFE", "birim": "endeks", "tip": "cpi"},
        "UNRATE": {"isim": "İşsizlik", "birim": "%", "tip": "rate"},
        "DGS10": {"isim": "10Y Tahvil", "birim": "%", "tip": "rate"},
        "T10Y2Y": {"isim": "10Y-2Y Spread", "birim": "%", "tip": "spread"},
        "VIXCLS": {"isim": "VIX", "birim": "puan", "tip": "vol"},
    }

    NEWS_SOURCE_WEIGHTS = {
        "reuters": 1.0, "bloomberg": 0.95, "financial times": 0.9, "wall street journal": 0.9,
        "cnbc": 0.85, "yahoo finance": 0.8, "marketwatch": 0.85, "seeking alpha": 0.6,
        "motley fool": 0.6, "benzinga": 0.65, "coindesk": 0.7, "cointelegraph": 0.7, "default": 0.7,
    }

    WEIGHT_MOMENTUM = 0.18
    WEIGHT_TECHNICAL = 0.20
    WEIGHT_FUNDAMENTAL = 0.22
    WEIGHT_NEWS = 0.18
    WEIGHT_MACRO = 0.22

# ============================================================
# YARDIMCI FONKSİYONLAR
# ============================================================

def safe_float(value):
    try:
        if value is None: return None
        n = float(value); return n if math.isfinite(n) else None
    except: return None

def fmt(v, d=2):
    n = safe_float(v)
    return f"{n:,.{d}f}" if n is not None else "—"

def fmt_pct(v, d=2):
    n = safe_float(v)
    return f"{n:+.{d}f}%" if n is not None else "—"

def clamp(value, min_val=0.0, max_val=100.0):
    return max(min_val, min(max_val, value))

# ============================================================
# SENTIMENT
# ============================================================

def analyze_sentiment(text):
    if not text or len(text.strip()) < 10: return "nötr", 0.5
    if FINBERT is not None:
        try:
            result = FINBERT(text[:1000])[0]
            label = result["label"].lower(); score = float(result["score"])
            if label == "positive": return "pozitif", score
            elif label == "negative": return "negatif", score
            else: return "nötr", score
        except: pass
    pos_words = {"surge","rally","beat","growth","profit","record","upgrade","gain","rise","strong","outperform","bullish","artış","kazanç","yükseliş","rekor","büyüme","güçlü","kâr","olumlu","yükseldi","kazandırdı","pozitif","iyimser"}
    neg_words = {"crash","plunge","drop","loss","decline","downgrade","fall","weak","underperform","bearish","lawsuit","düşüş","kayıp","zarar","dava","zayıf","olumsuz","çöküş","düştü","kaybettirdi","negatif","kötümser","risk","kriz","iflas"}
    neu_words = {"beklenti","açıkladı","duyurdu","rapor","analiz","bekleniyor","planlıyor"}
    text_lower = text.lower(); words = set(re.findall(r"[\wçğıöşüÇĞİÖŞÜ]+", text_lower))
    net = (len(words & pos_words) * 1.0) - (len(words & neg_words) * 1.0) + (len(words & neu_words) * 0.1)
    if net > 0: return "pozitif", min(0.75, 0.55 + net * 0.08)
    elif net < 0: return "negatif", min(0.75, 0.55 + abs(net) * 0.08)
    return "nötr", 0.55

def news_source_weight(publisher):
    if not publisher: return Config.NEWS_SOURCE_WEIGHTS["default"]
    p_lower = publisher.lower()
    for key, weight in Config.NEWS_SOURCE_WEIGHTS.items():
        if key in p_lower: return weight
    return Config.NEWS_SOURCE_WEIGHTS["default"]

# ============================================================
# FRED
# ============================================================

class FredClient:
    BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
    def __init__(self, api_key): self.api_key = api_key; self.session = requests.Session()
    def get_series(self, series_id, meta):
        if not self.api_key: return {"isim": meta["isim"], "hata": "FRED API key eksik"}
        try:
            r = self.session.get(self.BASE_URL, params={"series_id": series_id, "api_key": self.api_key, "file_type": "json", "sort_order": "desc", "limit": 36}, timeout=15)
            r.raise_for_status(); obs = [o for o in r.json().get("observations", []) if o.get("value") not in (None, "", ".") and safe_float(o.get("value"))]
            if not obs: return {"isim": meta["isim"], "hata": "Veri yok"}
            cur = safe_float(obs[0]["value"]); prev = safe_float(obs[1]["value"]) if len(obs) > 1 else None
            res = {"isim": meta["isim"], "tip": meta["tip"], "birim": meta["birim"], "deger": cur, "tarih": obs[0]["date"], "degisim": (cur-prev) if cur is not None and prev is not None else None}
            if meta["tip"] == "cpi" and len(obs) >= 13:
                y = safe_float(obs[12]["value"])
                if cur and y: res["yoy"] = ((cur / y) - 1) * 100
            return res
        except Exception as e: return {"isim": meta["isim"], "hata": str(e)}
    def fetch_all(self):
        results = {}
        with ThreadPoolExecutor(max_workers=6) as executor:
            futs = {executor.submit(self.get_series, sid, meta): sid for sid, meta in Config.FRED_SERIES.items()}
            for fut in as_completed(futs): results[futs[fut]] = fut.result()
        return results

# ============================================================
# MAKRO SKOR
# ============================================================

def advanced_macro_score(macro_data):
    score = 50.0
    reasons = []
    fed = macro_data.get("FEDFUNDS", {})
    cpi = macro_data.get("CPIAUCSL", {})
    unemp = macro_data.get("UNRATE", {})
    ty = macro_data.get("DGS10", {})
    spread = macro_data.get("T10Y2Y", {})
    vix = macro_data.get("VIXCLS", {})

    fed_change = safe_float(fed.get("degisim"))
    fed_level = safe_float(fed.get("deger"))
    if fed_change is not None:
        if fed_change <= -0.5: score += 15; reasons.append("Fed agresif faiz indirimi")
        elif fed_change < 0: score += 8; reasons.append("Fed faiz indiriyor")
        elif fed_change >= 0.5: score -= 15; reasons.append("Fed agresif faiz artırıyor")
        elif fed_change > 0: score -= 8; reasons.append("Fed faiz artırıyor")
    if fed_level is not None:
        if fed_level < 1.0: score += 5; reasons.append("Faizler çok düşük")
        elif fed_level > 5.0: score -= 10; reasons.append("Faizler çok yüksek")
        elif fed_level > 3.5: score -= 5; reasons.append("Faizler yüksek")

    cpi_yoy = safe_float(cpi.get("yoy"))
    if cpi_yoy is not None:
        if cpi_yoy < 2.0: score += 10; reasons.append("Enflasyon hedef altında")
        elif cpi_yoy < 3.0: score += 5
        elif cpi_yoy > 6.0: score -= 15; reasons.append("Enflasyon çok yüksek")
        elif cpi_yoy > 4.0: score -= 8; reasons.append("Enflasyon yüksek")

    unemp_change = safe_float(unemp.get("degisim"))
    unemp_level = safe_float(unemp.get("deger"))
    if unemp_change is not None:
        if unemp_change <= -0.3: score += 8; reasons.append("İşsizlik düşüyor")
        elif unemp_change >= 0.4: score -= 10; reasons.append("İşsizlik artıyor")
    if unemp_level is not None:
        if unemp_level < 4.0: score += 3
        elif unemp_level > 8.0: score -= 8

    ty_level = safe_float(ty.get("deger"))
    if ty_level is not None:
        if ty_level < 2.0: score += 5
        elif ty_level > 5.0: score -= 10; reasons.append("Tahvil faizleri çok yüksek")
        elif ty_level > 4.0: score -= 5; reasons.append("Tahvil faizleri yüksek")

    spread_value = safe_float(spread.get("deger"))
    if spread_value is not None:
        if spread_value < -0.7: score -= 12; reasons.append("Derin ters getiri eğrisi")
        elif spread_value < -0.2: score -= 8; reasons.append("Getiri eğrisi ters")
        elif spread_value > 1.2: score += 6
        elif spread_value > 0.5: score += 3

    vix_value = safe_float(vix.get("deger"))
    if vix_value is not None:
        if vix_value > 35: score -= 15; reasons.append("VIX aşırı yüksek")
        elif vix_value > 25: score -= 8; reasons.append("VIX yüksek")
        elif vix_value < 13: score += 5
        elif vix_value < 18: score += 2

    score = clamp(score, 5, 95)
    score_int = int(round(score))
    label = "POZİTİF" if score_int >= 65 else "NEGATİF" if score_int <= 35 else "NÖTR"
    reason_text = " | ".join(reasons) if reasons else "Nötr makro ortam"
    return label, score_int, reason_text

# ============================================================
# TEKNİK GÖSTERGELER
# ============================================================

def calc_rsi_wilder(closes, period=14):
    if len(closes) < period + 1: return None
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[:period]]; losses = [-d if d < 0 else 0 for d in deltas[:period]]
    avg_gain = sum(gains) / period; avg_loss = sum(losses) / period
    for i in range(period, len(deltas)):
        gain = max(deltas[i], 0); loss = max(-deltas[i], 0)
        avg_gain = (avg_gain * (period - 1) + gain) / period; avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss; return 100 - (100 / (1 + rs))

def calc_macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal: return {"macd": None, "signal": None, "histogram": None}
    def ema(data, period):
        if len(data) < period: return []
        vals = [sum(data[:period]) / period]; mult = 2 / (period + 1)
        for i in range(period, len(data)): vals.append((data[i] - vals[-1]) * mult + vals[-1])
        return vals
    ema_f = ema(closes, fast); ema_s = ema(closes, slow)
    st_idx = slow - fast; ema_f = ema_f[st_idx:]; min_len = min(len(ema_f), len(ema_s))
    if min_len < signal: return {"macd": None, "signal": None, "histogram": None}
    macd_line = [f - s for f, s in zip(ema_f[:min_len], ema_s[:min_len])]
    signal_line = ema(macd_line, signal)
    if not macd_line or not signal_line: return {"macd": None, "signal": None, "histogram": None}
    hist_min = min(len(macd_line), len(signal_line)); macd_line = macd_line[:hist_min]; signal_line = signal_line[:hist_min]
    histogram = [m - s for m, s in zip(macd_line, signal_line)]
    return {"macd": macd_line[-1], "signal": signal_line[-1], "histogram": histogram[-1]}

def calc_bollinger_bands(closes, period=20, num_std=2.0):
    if len(closes) < period: return {"upper": None, "middle": None, "lower": None}
    recent = closes[-period:]; middle = sum(recent) / period
    variance = sum((x - middle)**2 for x in recent) / period; std = math.sqrt(variance)
    return {"upper": middle + num_std * std, "middle": middle, "lower": middle - num_std * std}

def calc_volume_trend(volumes, period=20):
    if len(volumes) < period*2: return None
    r_vol = sum(volumes[-period:]) / period; p_vol = sum(volumes[-2*period:-period]) / period
    if p_vol == 0: return None
    return ((r_vol / p_vol) - 1) * 100

def calc_atr(hist):
    try:
        high = hist['High']; low = hist['Low']; close = hist['Close']
        prev_close = close.shift(1)
        tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(window=14).mean().iloc[-1]
        return safe_float(atr)
    except: return None

# ============================================================
# VERİ YAPISI ve ÇEKME
# ============================================================

@dataclass
class MarketData:
    symbol: str; name: str; category: str = ""
    price: Optional[float] = None; change_pct: Optional[float] = None; currency: Optional[str] = None; volume: Optional[float] = None
    pe: Optional[float] = None; market_cap: Optional[float] = None; high_52: Optional[float] = None; low_52: Optional[float] = None
    profit_margin: Optional[float] = None; sector: Optional[str] = None
    rsi: Optional[float] = None; sma50: Optional[float] = None; sma200: Optional[float] = None; dist_high: Optional[float] = None
    macd: Optional[float] = None; macd_signal: Optional[float] = None; macd_histogram: Optional[float] = None
    bb_upper: Optional[float] = None; bb_middle: Optional[float] = None; bb_lower: Optional[float] = None
    volume_trend: Optional[float] = None; atr: Optional[float] = None
    news: List[Dict[str, Any]] = field(default_factory=list); news_score: float = 50.0
    error: Optional[str] = None; score: int = 50; score_label: str = "NÖTR"; confidence: float = 0.5

def fetch_one(symbol, name, category, hist=None):
    data = MarketData(symbol=symbol, name=name, category=category)
    try:
        ticker = yf.Ticker(symbol)
        if hist is None:
            hist = ticker.history(period="1y", interval="1d", auto_adjust=True)
        if hist is not None and not hist.empty:
            closes = [safe_float(x) for x in hist["Close"].tolist() if safe_float(x) is not None]
            volumes = [safe_float(x) for x in hist["Volume"].tolist() if safe_float(x) is not None]
            if closes:
                data.price = closes[-1]
                if len(closes) >= 2: data.change_pct = ((closes[-1] / closes[-2]) - 1) * 100
                data.rsi = calc_rsi_wilder(closes)
                if len(closes) >= 50: data.sma50 = sum(closes[-50:]) / 50
                if len(closes) >= 200: data.sma200 = sum(closes[-200:]) / 200
                data.high_52 = max(closes[-252:]) if len(closes) >= 252 else max(closes)
                data.low_52 = min(closes[-252:]) if len(closes) >= 252 else min(closes)
                if data.high_52: data.dist_high = (data.price / data.high_52 - 1) * 100
                macd_data = calc_macd(closes); data.macd = macd_data["macd"]; data.macd_signal = macd_data["signal"]; data.macd_histogram = macd_data["histogram"]
                bb_data = calc_bollinger_bands(closes); data.bb_upper = bb_data["upper"]; data.bb_middle = bb_data["middle"]; data.bb_lower = bb_data["lower"]
            if volumes:
                data.volume_trend = calc_volume_trend(volumes); data.volume = volumes[-1]
            data.atr = calc_atr(hist)
        try:
            info = ticker.get_info()
            if info:
                data.price = data.price or safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
                data.change_pct = data.change_pct if data.change_pct is not None else safe_float(info.get("regularMarketChangePercent"))
                data.pe = safe_float(info.get("trailingPE")); data.market_cap = safe_float(info.get("marketCap"))
                data.profit_margin = safe_float(info.get("profitMargins")); data.sector = info.get("sector"); data.currency = info.get("currency")
                if data.volume is None: data.volume = safe_float(info.get("volume"))
        except: pass
        raw_news = []
        try:
            if hasattr(ticker, 'get_news'):
                try: raw_news = ticker.get_news() or []
                except: pass
            if not raw_news:
                try: raw_news = ticker.news or []
                except: pass
        except: raw_news = []
        news_items = []
        if raw_news:
            for item in raw_news[:Config.NEWS_LIMIT]:
                try:
                    content = item.get("content") if isinstance(item.get("content"), dict) else item
                    title = (content.get("title") or "").strip(); summary = (content.get("summary") or "")[:300]
                    publisher = content.get("publisher") or content.get("source") or ""
                    publish_time = content.get("pubDate") or content.get("providerPublishTime")
                    if not title: continue
                    label, conf = analyze_sentiment(f"{title}. {summary}")
                    source_w = news_source_weight(publisher); time_w = 1.0
                    if publish_time:
                        try:
                            if isinstance(publish_time, (int, float)): pub_date = datetime.fromtimestamp(publish_time, tz=timezone.utc)
                            else: pub_date = datetime.fromisoformat(str(publish_time).replace('Z', '+00:00'))
                            age_days = (datetime.now(timezone.utc) - pub_date).days
                            if age_days > 7: time_w = max(0.3, 1.0 - (age_days - 7) * 0.1)
                            elif age_days < 0: time_w = 0.5
                        except: pass
                    news_items.append({"baslik": title, "yayinci": publisher, "duygu": label, "guven": conf, "bilesik_agirlik": source_w * time_w})
                except: continue
        data.news = news_items
        if news_items:
            score_map = {"pozitif": 80, "negatif": 20, "nötr": 50}
            total_w = sum(i["bilesik_agirlik"] * i["guven"] for i in news_items)
            if total_w > 0: data.news_score = sum(score_map[i["duygu"]] * i["bilesik_agirlik"] * i["guven"] for i in news_items) / total_w
    except Exception as e:
        data.error = str(e)
    return data

# ============================================================
# SKORLAMA
# ============================================================

def compute_score(data, macro_score, macro_label):
    m_w, t_w, f_w, n_w, mac_w = Config.WEIGHT_MOMENTUM, Config.WEIGHT_TECHNICAL, Config.WEIGHT_FUNDAMENTAL, Config.WEIGHT_NEWS, Config.WEIGHT_MACRO
    total_w = m_w + t_w + f_w + n_w + mac_w
    m_w/=total_w; t_w/=total_w; f_w/=total_w; n_w/=total_w; mac_w/=total_w

    scores = {}
    ch = data.change_pct or 0
    scores["momentum"] = 88 if ch >= 4 else 78 if ch >= 2 else 68 if ch >= 1 else 55 if ch >= 0 else 45 if ch >= -1 else 30 if ch >= -3 else 15

    tech = 50.0; comps = []
    if data.rsi is not None:
        if 40 <= data.rsi <= 60: comps.append(65)
        elif data.rsi < 25: comps.append(75)
        elif data.rsi < 35: comps.append(70)
        elif data.rsi > 75: comps.append(25)
        elif data.rsi > 65: comps.append(40)
        else: comps.append(55)
    if data.sma50 and data.price: comps.append(65 if data.price > data.sma50 else 40)
    if data.sma200 and data.price: comps.append(70 if data.price > data.sma200 else 35)
    if data.macd is not None and data.macd_signal is not None: comps.append(65 if data.macd > data.macd_signal else 40)
    if data.bb_upper and data.bb_lower and data.price:
        bb_range = data.bb_upper - data.bb_lower
        if bb_range > 0:
            pos = (data.price - data.bb_lower) / bb_range
            comps.append(60 if 0.2 <= pos <= 0.8 else 65 if pos < 0.1 else 35 if pos > 0.9 else 50)
    if data.volume_trend is not None: comps.append(70 if data.volume_trend > 10 else 40 if data.volume_trend < -10 else 55)
    if data.atr and data.price:
        atr_ratio = data.atr / data.price
        if atr_ratio < 0.02: comps.append(65)
        elif atr_ratio < 0.05: comps.append(50)
        else: comps.append(30)
    if comps: tech = sum(comps) / len(comps)
    scores["technical"] = tech

    fund = 50.0; fcomps = []
    if data.pe is not None and data.pe > 0:
        fcomps.append(80 if data.pe < 10 else 70 if data.pe < 15 else 60 if data.pe < 20 else 45 if data.pe < 30 else 30 if data.pe < 50 else 20)
    if data.profit_margin is not None: fcomps.append(85 if data.profit_margin > 0.25 else 70 if data.profit_margin > 0.15 else 60 if data.profit_margin > 0.08 else 45 if data.profit_margin > 0 else 20)
    if data.dist_high is not None: fcomps.append(clamp(65 + data.dist_high * 0.8, 20, 80))
    if data.market_cap is not None: fcomps.append(70 if data.market_cap > 1e11 else 60 if data.market_cap > 1e10 else 50 if data.market_cap > 1e9 else 40)
    if fcomps: fund = sum(fcomps) / len(fcomps)
    scores["fundamental"] = fund

    scores["news"] = data.news_score
    scores["macro"] = macro_score

    data.score = int(clamp(scores["momentum"]*m_w + scores["technical"]*t_w + scores["fundamental"]*f_w + scores["news"]*n_w + scores["macro"]*mac_w, 5, 95))

    conf = 0.35
    if data.rsi is not None: conf += 0.10
    if data.pe is not None: conf += 0.10
    if data.macd is not None: conf += 0.05
    if data.atr is not None: conf += 0.05
    if data.news: conf += 0.15
    if data.profit_margin is not None: conf += 0.10
    if data.sma50 is not None: conf += 0.05
    if data.sma200 is not None: conf += 0.05
    data.confidence = clamp(conf, 0.0, 0.95)

    data.score_label = "GÜÇLÜ POZİTİF" if data.score >= 70 else "POZİTİF" if data.score >= 58 else "NÖTR" if data.score >= 45 else "NEGATİF" if data.score >= 32 else "GÜÇLÜ NEGATİF"

# ============================================================
# RAPORLAMA
# ============================================================

def print_rich_report(results, macro_data, macro_label, macro_score, macro_reason, elapsed, start_time):
    if not RICH: return
    console = Console()
    console.print(Panel.fit(f"[bold cyan]PİYASA İZLEME v5.3[/]  |  {start_time.strftime('%Y-%m-%d %H:%M')}  |  Süre: {elapsed:.1f}s", border_style="cyan"))
    macro_table = Table(title="Makro Göstergeler", box=box.ROUNDED)
    macro_table.add_column("Gösterge", style="bold"); macro_table.add_column("Değer", justify="right")
    for v in macro_data.values():
        if v.get("hata"): macro_table.add_row(v["isim"], f"[red]{v['hata']}[/red]")
        else: macro_table.add_row(v["isim"], f"{fmt(v.get('deger'))} {v.get('birim','')}")
    console.print(macro_table); console.print(Panel(f"{macro_label} Skor: {macro_score}\n{macro_reason}", title="Makro Özet"))
    table = Table(title="Sıralı Sonuçlar", box=box.SIMPLE_HEAVY)
    for c in ["Sembol","Fiyat","Değişim","RSI","ATR","Skor","Durum"]: table.add_column(c)
    for d in sorted(results, key=lambda x: x.score, reverse=True):
        table.add_row(d.symbol, fmt(d.price), fmt_pct(d.change_pct), fmt(d.rsi,1), fmt(d.atr), str(d.score), d.score_label)
    console.print(table); console.print(f"\n[green]✓ Tamamlandı - {elapsed:.1f}s[/green]")

def save_reports(results, macro_data, macro_label, macro_score, macro_reason, start_time):
    if not PANDAS: return
    stamp = start_time.strftime("%Y%m%d_%H%M"); xlsx_path = Config.EXPORT_DIR / f"piyasa_{stamp}.xlsx"
    df = pd.DataFrame([{"Sembol": d.symbol, "Fiyat": d.price, "RSI": d.rsi, "ATR": d.atr, "Skor": d.score, "Güven": d.confidence} for d in results])
    df.to_excel(xlsx_path, index=False)
    print(f"✓ Kaydedildi → {xlsx_path}")

# ============================================================
# ANA
# ============================================================

def main():
    parser = argparse.ArgumentParser(); args = parser.parse_args()
    start = time.monotonic(); now = datetime.now(timezone.utc).astimezone()
    fred = FredClient(Config.FRED_API_KEY); macro_data = fred.fetch_all()
    macro_label, macro_score, macro_reason = advanced_macro_score(macro_data)

    jobs = [(s, n, c) for c, syms in Config.TAKIP_LISTESI.items() for s, n in syms.items()]
    results = []
    with ThreadPoolExecutor(max_workers=Config.MAX_WORKERS) as exe:
        futs = {exe.submit(fetch_one, s, n, c): s for s, n, c in jobs}
        for fut in as_completed(futs): results.append(fut.result())
    for d in results: compute_score(d, macro_score, macro_label)
    elapsed = time.monotonic() - start
    if RICH: print_rich_report(results, macro_data, macro_label, macro_score, macro_reason, elapsed, now)
    save_reports(results, macro_data, macro_label, macro_score, macro_reason, now)

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: sys.exit(0)
    except Exception as e: print(f"Hata: {e}")

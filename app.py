import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime
import yfinance as yf
import sys
import os
import joblib
import requests
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from streamlit_autorefresh import st_autorefresh
    AUTOREFRESH_AVAILABLE = True
except ImportError:
    AUTOREFRESH_AVAILABLE = False

try:
    import piyasa_v5 as engine
except ImportError:
    st.error("`piyasa_v5.py` dosyası bulunamadı.")
    st.stop()

st.set_page_config(page_title="Piyasa İzleme Paneli", layout="wide")

# ==================== ŞİFRE KORUMASI ====================
if "sifre_dogrulandi" not in st.session_state:
    st.session_state.sifre_dogrulandi = False

if not st.session_state.sifre_dogrulandi:
    st.markdown("<h2 style='text-align:center;'>🔐 Panel Kilitli</h2>", unsafe_allow_html=True)
    st.markdown("<p style='text-align:center;'>Devam etmek için şifrenizi girin.</p>", unsafe_allow_html=True)
    sifre_input = st.text_input("Şifre", type="password", placeholder="••••••")
    if st.button("Giriş Yap"):
        try:
            dogru_sifre = st.secrets["APP_PASSWORD"]
        except Exception:
            dogru_sifre = None

        if dogru_sifre and sifre_input == dogru_sifre:
            st.session_state.sifre_dogrulandi = True
            st.rerun()
        else:
            st.error("Yanlış şifre. Tekrar deneyin.")
    st.stop()
# ========================================================

# Mobil uyumluluk için CSS
st.markdown("""
<style>
@media (max-width: 600px) {
    .stApp {
        font-size: 14px;
    }
    .stDataFrame {
        overflow-x: auto;
    }
    .stMetric {
        font-size: 1.2rem;
    }
}
</style>
""", unsafe_allow_html=True)

if AUTOREFRESH_AVAILABLE:
    auto_refresh = st.sidebar.checkbox("Otomatik yenileme (30 sn)", value=False)
    if auto_refresh:
        st_autorefresh(interval=30 * 1000, key="data_refresh")

st.sidebar.header("📌 Ayarlar")
kategoriler = ["Tümü"] + list(engine.Config.TAKIP_LISTESI.keys())
secili_kategori = st.sidebar.selectbox("Kategori", kategoriler)

# --- KİŞİSEL İZLEME LİSTESİ ---
st.sidebar.markdown("---")
st.sidebar.header("➕ Kişisel İzleme Listesi")
st.sidebar.caption("Takip etmek istediğin sembolü ekle")

if 'kisisel_semboller' not in st.session_state:
    st.session_state['kisisel_semboller'] = []

yeni_sembol = st.sidebar.text_input("Sembol (örn: FROTO.IS)", key='yeni_sembol')
if st.sidebar.button("➕ Ekle"):
    if yeni_sembol:
        temiz_sembol = yeni_sembol.strip().upper()
        if temiz_sembol not in st.session_state['kisisel_semboller']:
            st.session_state['kisisel_semboller'].append(temiz_sembol)
            st.sidebar.success(f"{temiz_sembol} eklendi.")
        else:
            st.sidebar.warning(f"{temiz_sembol} zaten listede.")
    else:
        st.sidebar.error("Lütfen bir sembol yaz.")

if st.session_state['kisisel_semboller']:
    st.sidebar.write("**Eklenen Semboller:**")
    for idx, sem in enumerate(st.session_state['kisisel_semboller']):
        col_a, col_b = st.sidebar.columns([3,1])
        col_a.write(sem)
        if col_b.button("🗑", key=f"sil_{idx}"):
            st.session_state['kisisel_semboller'].remove(sem)
            st.rerun()

# Varlık seçimi için tüm sembolleri birleştir
tum_semboller = {}
for cat, syms in engine.Config.TAKIP_LISTESI.items():
    tum_semboller.update(syms)

kisisel_ek = {s: f"(Kişisel) {s}" for s in st.session_state['kisisel_semboller']}
tum_semboller.update(kisisel_ek)

if secili_kategori == "Tümü":
    secili_semboller = st.sidebar.multiselect(
        "Varlıklar (çoklu seçim)",
        options=list(tum_semboller.keys()),
        format_func=lambda s: f"{s} - {tum_semboller[s]}",
        default=list(tum_semboller.keys())
    )
else:
    kategori_sembolleri = engine.Config.TAKIP_LISTESI[secili_kategori]
    gosterilecek = dict(kategori_sembolleri)
    gosterilecek.update(kisisel_ek)
    secili_semboller = st.sidebar.multiselect(
        "Varlıklar (çoklu seçim)",
        options=list(gosterilecek.keys()),
        format_func=lambda s: f"{s} - {gosterilecek[s]}",
        default=list(gosterilecek.keys())
    )

# Portföy Takibi
st.sidebar.markdown("---")
st.sidebar.header("💼 Portföy Takibi")
portfoy_input = st.sidebar.text_area("Portföy Bilgileri", value="", placeholder="Örn:\nAAPL:10:150\nBTC-USD:0.5:60000", height=100)
if st.sidebar.button("💼 Portföyü Hesapla"):
    try:
        lines = [line.strip() for line in portfoy_input.split("\n") if line.strip()]
        portfoy = []
        for line in lines:
            parts = line.split(":")
            if len(parts) >= 2:
                sym = parts[0].strip().upper()
                adet = float(parts[1].replace(",", "."))
                maliyet = float(parts[2].replace(",", ".")) if len(parts) >= 3 else None
                portfoy.append({"sym": sym, "adet": adet, "maliyet": maliyet})

        total_val = 0; total_cost = 0
        for item in portfoy:
            try:
                tick = yf.Ticker(item["sym"]); price = getattr(tick.fast_info, "last_price", None)
                if price is None: price = tick.history(period="1d", interval="1m")["Close"].iloc[-1]
            except: price = 0
            item["price"] = price
            val = price * item["adet"]
            total_val += val
            if item["maliyet"]: total_cost += item["maliyet"] * item["adet"]
            st.sidebar.write(f"• {item['sym']}: {item['adet']} x {price:,.2f} = {val:,.2f}")

        st.sidebar.write(f"**Toplam Güncel Değer:** {total_val:,.2f}")
        if total_cost > 0:
            pnl = total_val - total_cost
            st.sidebar.write(f"**Kâr/Zarar:** {pnl:+,.2f} (%{((total_val/total_cost)-1)*100:+.2f})")

        st.session_state['portfoy'] = portfoy
        st.session_state['portfoy_goster'] = True
    except Exception as e:
        st.sidebar.error(f"Hata: {e}")

# Fiyat Hedefi
st.sidebar.markdown("---")
st.sidebar.header("🎯 Fiyat Hedefi Uyarısı")
hedef_sembol = st.sidebar.text_input("Sembol", value="AAPL")
hedef_fiyat = st.sidebar.number_input("Hedef Fiyat", min_value=0.0, value=100.0)
hedef_yon = st.sidebar.radio("Yön", ["Üzerine çıkarsa", "Altına düşerse"])
if st.sidebar.button("🎯 Hedefi Kontrol Et"):
    try:
        tick = yf.Ticker(hedef_sembol.upper()); price = getattr(tick.fast_info, "last_price", None)
        if price is None: price = tick.history(period="1d", interval="1m")["Close"].iloc[-1]
        if (hedef_yon == "Üzerine çıkarsa" and price >= hedef_fiyat): st.sidebar.success(f"🚀 {hedef_sembol} {price:,.2f} ile hedefe ulaştı!")
        elif (hedef_yon == "Altına düşerse" and price <= hedef_fiyat): st.sidebar.error(f"🔻 {hedef_sembol} {price:,.2f} ile hedefin altına düştü!")
        else: st.sidebar.info(f"ℹ️ {hedef_sembol} şu an {price:,.2f}. Hedef: {hedef_fiyat:,.2f}")
    except Exception as e: st.sidebar.error(f"Hata: {e}")

st.title("📊 Piyasa İzleme ve Analiz Paneli")
st.caption("Gelişmiş Makro + FinBERT + Teknik Analiz | Hibrit Yapay Zeka | Al/Sat Sinyalleri")

@st.cache_data(ttl=600)
def get_macro_data():
    fred = engine.FredClient(engine.Config.FRED_API_KEY)
    return fred.fetch_all()

@st.cache_data(ttl=600)
def get_macro_score(macro_data):
    return engine.advanced_macro_score(macro_data)

macro_data = get_macro_data()
macro_label, macro_score, macro_reason = get_macro_score(macro_data)
col1, col2, col3 = st.columns(3)
col1.metric("Makro Skor", f"{macro_score}/100")
col2.metric("Rejim", macro_label)
col3.metric("Açıklama", macro_reason[:80])

# Yapay zeka modeli
MODEL_PATH = "xgb_model.pkl"
FEATURE_PATH = "feature_columns.pkl"
ai_model = None
feature_cols = []
if os.path.exists(MODEL_PATH) and os.path.exists(FEATURE_PATH):
    try:
        ai_model = joblib.load(MODEL_PATH)
        feature_cols = joblib.load(FEATURE_PATH)
        st.success("🧠 Yapay Zeka modeli aktif")
    except Exception as e:
        st.warning(f"Yapay Zeka modeli yüklenemedi: {e}")
else:
    st.warning("🧠 Yapay Zeka modeli bulunamadı. Lütfen `python ml_model.py` çalıştırın.")

if not secili_semboller:
    st.warning("Lütfen en az bir varlık seçin.")
else:
    def get_all_histories(symbols):
        if not symbols: return {}
        data = yf.download(tickers=symbols, period="1y", interval="1d", auto_adjust=True, group_by='ticker', threads=True, progress=False)
        histories = {}
        for s in symbols:
            try:
                hist = data[s].copy() if len(symbols) > 1 else data.copy()
                histories[s] = hist.dropna()
            except: histories[s] = pd.DataFrame()
        return histories

    all_histories = get_all_histories(secili_semboller)

    all_symbols = []
    for cat, syms in engine.Config.TAKIP_LISTESI.items():
        for symbol, name in syms.items():
            if symbol in secili_semboller:
                all_symbols.append((symbol, name, cat))
    for sem in st.session_state['kisisel_semboller']:
        if sem in secili_semboller:
            all_symbols.append((sem, f"(Kişisel) {sem}", "kisisel"))

    results = []
    with st.spinner("Varlık verileri çekiliyor..."):
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(engine.fetch_one, s, n, c, all_histories.get(s)): s for s, n, c in all_symbols}
            for future in as_completed(futures):
                data = future.result()
                results.append(data)

    # Canlı kripto fiyatları
    binance_map = {
        "BTC-USD": "BTCUSDT",
        "ETH-USD": "ETHUSDT",
        "SOL-USD": "SOLUSDT",
        "BNB-USD": "BNBUSDT",
        "XRP-USD": "XRPUSDT"
    }
    for d in results:
        if d.symbol in binance_map:
            try:
                r = requests.get(f"https://api.binance.com/api/v3/ticker/24hr?symbol={binance_map[d.symbol]}", timeout=5)
                if r.status_code == 200:
                    j = r.json()
                    d.price = float(j['lastPrice'])
                    d.change_pct = float(j['priceChangePercent'])
            except Exception:
                pass

    for d in results:
        engine.compute_score(d, macro_score, macro_label)

    results.sort(key=lambda x: x.symbol)

    def kural_tahmin(data):
        puan = 0
        if data.change_pct is not None:
            if data.change_pct > 0: puan += 1
            elif data.change_pct < 0: puan -= 1
        if data.rsi is not None:
            if data.rsi > 55: puan += 1
            elif data.rsi < 45: puan -= 1
        if data.macd is not None and data.macd_signal is not None:
            if data.macd > data.macd_signal: puan += 1
            else: puan -= 1
        if data.sma50 and data.price:
            if data.price > data.sma50: puan += 1
            else: puan -= 1
        if data.bb_middle and data.price:
            if data.price > data.bb_middle: puan += 1
            else: puan -= 1
        if puan >= 3: return "🟢 Yükselebilir"
        elif puan <= -3: return "🔴 Düşebilir"
        else: return "⚪ Belirsiz"

    def ai_tahmin(data, model, feature_names, hist):
        if model is None or hist is None or hist.empty:
            return None, 0.0
        try:
            close = hist['Close']
            high = hist['High']
            low = hist['Low']
            volume = hist['Volume']

            delta = close.diff()

            # RSI 14
            gain14 = (delta.where(delta > 0, 0)).rolling(14).mean()
            loss14 = (-delta.where(delta < 0, 0)).rolling(14).mean()
            loss14 = loss14.replace(0, np.nan)
            rs14 = gain14 / loss14
            rsi14 = 100 - (100 / (1 + rs14))

            # RSI 10
            gain10 = (delta.where(delta > 0, 0)).rolling(10).mean()
            loss10 = (-delta.where(delta < 0, 0)).rolling(10).mean()
            loss10 = loss10.replace(0, np.nan)
            rs10 = gain10 / loss10
            rsi10 = 100 - (100 / (1 + rs10))

            momentum5 = close.pct_change(5) * 100

            macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
            signal = macd.ewm(span=9, adjust=False).mean()
            macd_hist = macd - signal

            sma50 = close.rolling(50).mean()
            sma200 = close.rolling(200).mean()

            bb_mid = close.rolling(20).mean()
            bb_std = close.rolling(20).std()
            bb_upper = bb_mid + 2 * bb_std
            bb_lower = bb_mid - 2 * bb_std

            atr = (high - low).rolling(14).mean()
            volume_trend = volume.pct_change(20) * 100
            daily_ret = close.pct_change() * 100

            high_52 = close.rolling(window=min(252, len(close))).max()
            low_52 = close.rolling(window=min(252, len(close))).min()
            dist_high_52 = ((close / high_52) - 1) * 100
            dist_low_52 = ((close / low_52) - 1) * 100

            # Yeni göstergeler
            obv = (np.sign(delta) * volume).cumsum()
            obv_trend = obv.pct_change(20) * 100

            stoch_k = ((close - low.rolling(14).min()) / (high.rolling(14).max() - low.rolling(14).min())) * 100

            tp = (high + low + close) / 3
            cci = (tp - tp.rolling(20).mean()) / (0.015 * tp.rolling(20).apply(lambda x: np.abs(x - x.mean()).mean()))

            williams_r = ((high.rolling(14).max() - close) / (high.rolling(14).max() - low.rolling(14).min())) * -100

            son = hist.iloc[-1]

            ozellikler = pd.DataFrame([[
                daily_ret.iloc[-1],
                rsi14.iloc[-1],
                rsi10.iloc[-1],
                momentum5.iloc[-1],
                (macd_hist.iloc[-1] / son['Close']) * 100,
                ((son['Close'] / sma50.iloc[-1]) - 1) * 100,
                ((son['Close'] / sma200.iloc[-1]) - 1) * 100,
                (atr.iloc[-1] / son['Close']) * 100,
                volume_trend.iloc[-1],
                ((son['Close'] / bb_upper.iloc[-1]) - 1) * 100,
                ((son['Close'] / bb_lower.iloc[-1]) - 1) * 100,
                dist_high_52.iloc[-1],
                dist_low_52.iloc[-1],
                obv_trend.iloc[-1],
                stoch_k.iloc[-1],
                cci.iloc[-1],
                williams_r.iloc[-1]
            ]], columns=feature_names)

            if ozellikler.isnull().any().any():
                return None, 0.0

            proba = model.predict_proba(ozellikler)[0]
            yon = "🟢 Yükselebilir" if proba[1] > proba[0] else "🔴 Düşebilir"
            olasilik = proba[1] * 100
            return yon, olasilik
        except Exception:
            return None, 0.0

    for d in results:
        d.kural_yon = kural_tahmin(d)
        if ai_model is not None:
            yon, olasilik = ai_tahmin(d, ai_model, feature_cols, all_histories.get(d.symbol))
            d.ai_yon = yon
            d.ai_olasilik = olasilik
        else:
            d.ai_yon = "⚪ Model Yok"
            d.ai_olasilik = 0.0

        # --- HİBRİT TAHMİN (Yapay Zeka + Haber Duygu) ---
        if d.ai_olasilik is not None and d.news_score is not None:
            hibrit_skor = d.ai_olasilik * 0.75 + d.news_score * 0.25
            if hibrit_skor >= 58:
                d.hibrit_yon = "🟢 Yükselebilir"
            elif hibrit_skor <= 42:
                d.hibrit_yon = "🔴 Düşebilir"
            else:
                d.hibrit_yon = "⚪ Belirsiz"
            d.hibrit_olasilik = hibrit_skor
        else:
            d.hibrit_yon = "⚪ Veri Eksik"
            d.hibrit_olasilik = 0.0

    # --- VERİTABANINA KAYDET ---
    def veritabani_kaydet(results):
        conn = sqlite3.connect('piyasa_gecmis.db')
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS skor_gecmis (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     tarih TEXT,
                     sembol TEXT,
                     isim TEXT,
                     fiyat REAL,
                     skor INTEGER,
                     ai_olasilik REAL
                     )''')
        c.execute("PRAGMA table_info(skor_gecmis)")
        mevcut_sutunlar = [row[1] for row in c.fetchall()]
        if 'hibrit_olasilik' not in mevcut_sutunlar:
            c.execute("ALTER TABLE skor_gecmis ADD COLUMN hibrit_olasilik REAL")

        tarih = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for d in results:
            c.execute('INSERT INTO skor_gecmis (tarih, sembol, isim, fiyat, skor, ai_olasilik, hibrit_olasilik) VALUES (?, ?, ?, ?, ?, ?, ?)',
                      (tarih, d.symbol, d.name, d.price, d.score, d.ai_olasilik, d.hibrit_olasilik))
        conn.commit()
        conn.close()
    veritabani_kaydet(results)

    # ==================== PİYASA ÖZETİ ====================
    st.markdown("---")
    st.subheader("📋 Piyasa Özeti")
    if results:
        ortalama_degisim = np.mean([d.change_pct for d in results if d.change_pct is not None])
        en_kazanan = max(results, key=lambda x: x.change_pct or 0)
        en_kaybeden = min(results, key=lambda x: x.change_pct or 0)
        ortalama_skor = np.mean([d.score for d in results])
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Ortalama Günlük Değişim", f"{ortalama_degisim:+.2f}%")
        col2.metric("En Çok Kazanan", f"{en_kazanan.symbol} ({en_kazanan.change_pct or 0:+.2f}%)")
        col3.metric("En Çok Kaybeden", f"{en_kaybeden.symbol} ({en_kaybeden.change_pct or 0:+.2f}%)")
        col4.metric("Ortalama Skor", f"{ortalama_skor:.1f}")

    yuksek_skorlular = [d for d in results if d.score >= 70]
    dusuk_skorlular = [d for d in results if d.score <= 40]
    if yuksek_skorlular:
        st.success("🚀 **Yüksek skorlu varlıklar:** " + ", ".join([f"{d.symbol} ({d.score})" for d in yuksek_skorlular]))
    if dusuk_skorlular:
        st.error("⚠️ **Düşük skorlu varlıklar:** " + ", ".join([f"{d.symbol} ({d.score})" for d in dusuk_skorlular]))

    # ==================== TÜM VARLIKLAR TABLOSU ====================
    st.markdown("---")
    st.subheader("📊 Tüm Varlıklar Tablosu")
    df = pd.DataFrame([{
        "Sembol": d.symbol,
        "İsim": d.name,
        "Fiyat": d.price,
        "Değişim%": d.change_pct,
        "RSI": d.rsi,
        "ATR": d.atr,
        "MACD Sinyal": "↑" if d.macd and d.macd_signal and d.macd > d.macd_signal else "↓",
        "Haber Skoru": d.news_score,
        "Skor": d.score,
        "Güven": d.confidence,
        "Kural Yön": d.kural_yon,
        "AI Yön": d.ai_yon,
        "AI Olasılık": d.ai_olasilik,
        "Hibrit Yön": d.hibrit_yon,
        "Hibrit Skor": d.hibrit_olasilik,
        "Durum": d.score_label
    } for d in results])

    def color_skor(val):
        if val >= 65: return 'background-color: #c6efce; color: #006100'
        elif val <= 40: return 'background-color: #ffc7ce; color: #9c0006'
        else: return ''
    def color_değişim(val):
        if val > 0: return 'color: green'
        elif val < 0: return 'color: red'
        else: return ''
    def color_ai_olasilik(val):
        if val >= 70: return 'color: green; font-weight: bold'
        elif val <= 50: return 'color: red; font-weight: bold'
        else: return 'color: orange; font-weight: bold'
    def color_hibrit(val):
        if val >= 70: return 'color: green; font-weight: bold'
        elif val <= 50: return 'color: red; font-weight: bold'
        else: return 'color: orange; font-weight: bold'

    styled_df = df.style.map(color_skor, subset=['Skor']) \
                        .map(color_değişim, subset=['Değişim%']) \
                        .map(color_ai_olasilik, subset=['AI Olasılık']) \
                        .map(color_hibrit, subset=['Hibrit Skor']) \
                        .format({'Fiyat': '{:.2f}', 'Değişim%': '{:+.2f}%', 'ATR': '{:.2f}', 'Haber Skoru': '{:.1f}', 'Güven': '{:.0%}', 'AI Olasılık': '{:.0f}%', 'Hibrit Skor': '{:.0f}%'})
    st.dataframe(styled_df, width='stretch')

    # ==================== AL/SAT SİNYALLERİ TABLOSU ====================
    st.markdown("---")
    st.subheader("🟢🔴 Al/Sat Sinyalleri")

    def sinyal_uret(data):
        sinyaller = {}
        if data.rsi is not None:
            if data.rsi < 30:
                sinyaller['RSI'] = "AL"
            elif data.rsi > 70:
                sinyaller['RSI'] = "SAT"
            else:
                sinyaller['RSI'] = "BEKLE"
        else:
            sinyaller['RSI'] = "—"
        if data.macd is not None and data.macd_signal is not None:
            if data.macd > data.macd_signal:
                sinyaller['MACD'] = "AL"
            else:
                sinyaller['MACD'] = "SAT"
        else:
            sinyaller['MACD'] = "—"
        if data.bb_upper and data.bb_lower and data.price:
            bb_range = data.bb_upper - data.bb_lower
            if bb_range > 0:
                pos = (data.price - data.bb_lower) / bb_range
                if pos < 0.1:
                    sinyaller['Bollinger'] = "AL"
                elif pos > 0.9:
                    sinyaller['Bollinger'] = "SAT"
                else:
                    sinyaller['Bollinger'] = "BEKLE"
            else:
                sinyaller['Bollinger'] = "—"
        else:
            sinyaller['Bollinger'] = "—"
        if data.change_pct is not None:
            if data.change_pct > 2:
                sinyaller['Momentum'] = "AL"
            elif data.change_pct < -2:
                sinyaller['Momentum'] = "SAT"
            else:
                sinyaller['Momentum'] = "BEKLE"
        else:
            sinyaller['Momentum'] = "—"

        al = sum(1 for s in sinyaller.values() if s == "AL")
        sat = sum(1 for s in sinyaller.values() if s == "SAT")
        if al > sat and al >= 2:
            sinyaller['Genel'] = "🟢 AL"
        elif sat > al and sat >= 2:
            sinyaller['Genel'] = "🔴 SAT"
        else:
            sinyaller['Genel'] = "⚪ BEKLE"
        return sinyaller

    sinyal_data = []
    for d in results:
        s = sinyal_uret(d)
        sinyal_data.append({
            "Sembol": d.symbol,
            "RSI": s.get('RSI', '—'),
            "MACD": s.get('MACD', '—'),
            "Bollinger": s.get('Bollinger', '—'),
            "Momentum": s.get('Momentum', '—'),
            "Genel Sinyal": s.get('Genel', '⚪ BEKLE')
        })
    sinyal_df = pd.DataFrame(sinyal_data)
    st.dataframe(sinyal_df, width='stretch')

    # ==================== DETAYLI VARLIK ANALİZİ ====================
    st.markdown("---")
    st.subheader("🔍 Detaylı Varlık Analizi")
    secili_sembol = st.selectbox(
        "Grafik için varlık seçin",
        options=[d.symbol for d in results],
        format_func=lambda s: f"{s} - {next((d.name for d in results if d.symbol == s), '')}",
        key='detay_sec'
    )
    secili_data = next((d for d in results if d.symbol == secili_sembol), None)
    if secili_data:
        met1, met2, met3, met4 = st.columns(4)
        met1.metric("Fiyat", f"{secili_data.price:.2f}" if secili_data.price else "—")
        met2.metric("Değişim%", f"{secili_data.change_pct or 0:+.2f}%" if secili_data.change_pct is not None else "—")
        met3.metric("RSI", f"{secili_data.rsi:.1f}" if secili_data.rsi else "—")
        met4.metric("Skor", f"{secili_data.score} ({secili_data.score_label})")

        col_k, col_ai, col_hibrit = st.columns(3)
        col_k.metric("Kural Bazlı Yön", secili_data.kural_yon)
        col_ai.metric("Yapay Zeka Yön", secili_data.ai_yon)
        col_hibrit.metric("Hibrit Yön", secili_data.hibrit_yon)
        if secili_data.hibrit_olasilik:
            st.caption(f"🧠 Hibrit tahmine göre yükselme olasılığı: **%{secili_data.hibrit_olasilik:.1f}**")
            st.caption(f"📰 Haber skoru: **{secili_data.news_score:.1f}** | AI olasılık: **%{secili_data.ai_olasilik:.1f}**")

        hist = all_histories.get(secili_sembol)
        if hist is not None and not hist.empty:
            fig = make_subplots(rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.5, 0.2, 0.15, 0.15])

            bb_mid = hist['Close'].rolling(window=20).mean()
            bb_std = hist['Close'].rolling(window=20).std()
            bb_upper = bb_mid + 2 * bb_std
            bb_lower = bb_mid - 2 * bb_std

            fig.add_trace(go.Candlestick(x=hist.index, open=hist['Open'], high=hist['High'], low=hist['Low'], close=hist['Close'], name='Fiyat', hovertemplate='<b>Tarih:</b> %{x}<br><b>Açılış:</b> %{open:,.2f}<br><b>Yüksek:</b> %{high:,.2f}<br><b>Düşük:</b> %{low:,.2f}<br><b>Kapanış:</b> %{close:,.2f}'), row=1, col=1)
            fig.add_trace(go.Scatter(x=hist.index, y=bb_upper, name='BB Üst', line=dict(width=0.7, dash='dot'), hovertemplate='<b>BB Üst:</b> %{y:,.2f}'), row=1, col=1)
            fig.add_trace(go.Scatter(x=hist.index, y=bb_mid, name='BB Orta', line=dict(width=0.7), hovertemplate='<b>BB Orta:</b> %{y:,.2f}'), row=1, col=1)
            fig.add_trace(go.Scatter(x=hist.index, y=bb_lower, name='BB Alt', line=dict(width=0.7, dash='dot'), hovertemplate='<b>BB Alt:</b> %{y:,.2f}'), row=1, col=1)

            fig.add_trace(go.Bar(x=hist.index, y=hist['Volume'], name='Hacim', marker_color='lightblue', hovertemplate='<b>Hacim:</b> %{y:,.0f}'), row=2, col=1)

            delta = hist['Close'].diff(); gain = (delta.where(delta > 0, 0)).rolling(window=14).mean(); loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean(); rs = gain / loss; rsi = 100 - (100 / (1 + rs))
            fig.add_trace(go.Scatter(x=hist.index, y=rsi, name='RSI (14)', line=dict(color='purple'), hovertemplate='<b>RSI:</b> %{y:.2f}'), row=3, col=1)
            fig.add_hline(y=70, line_dash="dot", line_color="red", row=3, col=1)
            fig.add_hline(y=30, line_dash="dot", line_color="green", row=3, col=1)

            macd_series = hist['Close'].ewm(span=12, adjust=False).mean() - hist['Close'].ewm(span=26, adjust=False).mean(); signal_series = macd_series.ewm(span=9, adjust=False).mean(); hist_series = macd_series - signal_series
            fig.add_trace(go.Scatter(x=hist.index, y=macd_series, name='MACD', line=dict(color='blue'), hovertemplate='<b>MACD:</b> %{y:,.4f}'), row=4, col=1)
            fig.add_trace(go.Scatter(x=hist.index, y=signal_series, name='Sinyal', line=dict(color='orange'), hovertemplate='<b>MACD Sinyal:</b> %{y:,.4f}'), row=4, col=1)
            fig.add_trace(go.Bar(x=hist.index, y=hist_series, name='Histogram', marker_color='gray', hovertemplate='<b>MACD Histogram:</b> %{y:,.4f}'), row=4, col=1)

            fig.update_layout(height=800, xaxis_rangeslider_visible=False)
            st.plotly_chart(fig, width='stretch')
        else:
            st.info("Geçmiş veri bulunamadı.")

        st.markdown("#### 📋 Temel Bilgiler")
        colA, colB, colC = st.columns(3)
        colA.write(f"**Sektör:** {secili_data.sector or 'Bilinmiyor'}")
        colB.write(f"**F/K:** {secili_data.pe:.2f}" if secili_data.pe else "**F/K:** —")
        colC.write(f"**Piyasa Değeri:** {secili_data.market_cap:,.0f}" if secili_data.market_cap else "**Piyasa Değeri:** —")
        colA.write(f"**Kâr Marjı:** {secili_data.profit_margin*100:.2f}%" if secili_data.profit_margin else "**Kâr Marjı:** —")
        colB.write(f"**52H Yüksek:** {secili_data.high_52:.2f}" if secili_data.high_52 else "**52H Yüksek:** —")
        colC.write(f"**52H Uzaklık:** {secili_data.dist_high or 0:+.2f}%" if secili_data.dist_high else "**52H Uzaklık:** —")

        with st.expander("📖 Gösterge Açıklamaları"):
            st.markdown("""
            - **Fiyat Mum Grafiği:** Dönem içindeki açılış, yüksek, düşük ve kapanışı gösterir.
            - **Bollinger Bantları:** Üst bant aşırı alım, alt bant aşırı satım bölgesidir.
            - **Hacim:** İşlem miktarını gösterir. Yüksek hacim, güçlü trend demektir.
            - **RSI (14):** 0-100 arası momentum göstergesi. 70+ aşırı alım, 30- aşırı satım.
            - **MACD:** Hareketli ortalamalar arasındaki farktır. Sinyal çizgisini yukarı keserse al, aşağı keserse sat sinyali sayılır.
            """)

        st.markdown("#### 📰 Son Haberler ve Duygu Analizi")
        if secili_data.news:
            for n in secili_data.news[:5]:
                emoji = {"pozitif": "🟢", "negatif": "🔴", "nötr": "⚪"}.get(n["duygu"], "⚪")
                publisher = n.get('yayinci', '') or 'Bilinmiyor'
                st.write(f"{emoji} **{n['baslik'][:120]}**  ")
                st.caption(f"Yayıncı: {publisher} | Güven: {n.get('guven', 0.5):.0%} | Ağırlık: {n.get('bilesik_agirlik', 0):.2f}")
                st.markdown("---")
        else:
            st.info("Bu varlık için haber bulunamadı.")

    st.markdown("---")
    st.subheader("📈 Gelişmiş Grafikler")

    st.markdown("### 🔗 Korelasyon Matrisi")
    closes = pd.DataFrame({s: all_histories[s]['Close'] for s in secili_semboller if s in all_histories and not all_histories[s].empty})
    if not closes.empty:
        corr = closes.pct_change().corr()
        fig_corr = go.Figure(data=go.Heatmap(
            z=corr.values,
            x=corr.columns,
            y=corr.index,
            colorscale='RdBu',
            zmin=-1, zmax=1,
            text=corr.round(2).values,
            texttemplate='%{text}',
            showscale=True
        ))
        fig_corr.update_layout(height=600, title='Varlıklar Arası Korelasyon (1 Yıllık)')
        st.plotly_chart(fig_corr, width='stretch')
    else:
        st.info("Korelasyon için yeterli veri yok.")

    if 'portfoy_goster' in st.session_state and st.session_state.get('portfoy_goster'):
        portfoy = st.session_state.get('portfoy', [])
        if portfoy:
            st.markdown("### 💹 Portföy Performansı (1 Yıllık)")
            portfoy_closes = pd.DataFrame()
            for item in portfoy:
                sym = item['sym']
                if sym in all_histories and not all_histories[sym].empty:
                    portfoy_closes[sym] = all_histories[sym]['Close']
            if not portfoy_closes.empty:
                portfoy_returns = portfoy_closes.pct_change().mean(axis=1).dropna()
                portfoy_cum = (1 + portfoy_returns).cumprod() * 100
                fig_port = go.Figure(data=go.Scatter(x=portfoy_cum.index, y=portfoy_cum, name='Portföy Getirisi'))
                fig_port.update_layout(height=400, title='Portföy Getiri Eğrisi (başlangıç 100)')
                st.plotly_chart(fig_port, width='stretch')
            else:
                st.info("Portföydeki varlıklar için yeterli veri yok.")

    st.markdown("### 🌊 Volatilite (ATR)")
    secili_vol = st.selectbox("Volatilite için varlık seçin", options=[d.symbol for d in results], key='vol_sec')
    if secili_vol and secili_vol in all_histories:
        hist_vol = all_histories[secili_vol]
        atr_series = (hist_vol['High'] - hist_vol['Low']).rolling(14).mean()
        fig_vol = go.Figure(data=go.Scatter(x=hist_vol.index, y=atr_series, name='ATR (14)'))
        fig_vol.update_layout(height=400, title=f'{secili_vol} Volatilite (ATR)')
        st.plotly_chart(fig_vol, width='stretch')

    st.markdown("---")
    st.subheader("📅 Geçmiş Skor Takibi")
    conn = sqlite3.connect('piyasa_gecmis.db')
    try:
        gecmis_df = pd.read_sql_query("SELECT * FROM skor_gecmis ORDER BY id DESC LIMIT 500", conn)
    except:
        gecmis_df = pd.DataFrame()
    conn.close()

    if not gecmis_df.empty:
        secili_gecmis = st.selectbox("Geçmişini görmek istediğin varlık", options=gecmis_df['sembol'].unique())
        if secili_gecmis:
            filtrelenmis = gecmis_df[gecmis_df['sembol'] == secili_gecmis].sort_values('tarih')
            if not filtrelenmis.empty:
                fig_gecmis = go.Figure(data=go.Scatter(
                    x=pd.to_datetime(filtrelenmis['tarih']),
                    y=filtrelenmis['skor'],
                    mode='lines+markers',
                    name='Skor'
                ))
                fig_gecmis.update_layout(height=400, title=f'{secili_gecmis} Geçmiş Skor Grafiği')
                st.plotly_chart(fig_gecmis, width='stretch')
            else:
                st.info("Bu varlık için geçmiş veri yok.")
    else:
        st.info("Henüz geçmiş veri kaydedilmemiş. Panel her çalıştığında otomatik kaydedilir.")

st.markdown("---")
st.caption("⚠️ Bu araç yalnızca bilgilendirme amaçlıdır, yatırım tavsiyesi değildir. Tahminler istatistikseldir ve kesin sonuç vermez.")
st.caption(f"Son güncelleme: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

st.markdown("---")
st.caption("Made by Barış")

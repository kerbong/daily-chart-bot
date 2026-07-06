#!/usr/bin/env python3
"""매일 아침 6개 자산의 주봉 차트를 생성해 텔레그램으로 전송."""
import math
import os
import io
import datetime as dt

import yfinance as yf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import requests

# ── watchlist.txt 에서 감시 자산 로드 ────────────────────────
def load_watchlist(path="watchlist.txt"):
    assets = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            ticker, _, name = line.partition(",")
            ticker = ticker.strip()
            name = name.strip() or ticker
            if ticker:
                assets.append((ticker, name))
    return assets

def fetch_weekly(ticker: str):
    """최근 2년치 주봉 데이터."""
    df = yf.download(
        ticker, period="2y", interval="1wk",
        progress=False, auto_adjust=True,
    )
    # 최신 yfinance는 컬럼이 MultiIndex(예: ('Close','GC=F'))로 오는 경우가 있음.
    # 첫 번째 레벨(Open/High/Low/Close)만 남겨 평탄화한다.
    if isinstance(df.columns, __import__("pandas").MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna()


def _scalar(v):
    """Series/배열이 와도 안전하게 단일 float로 변환."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(v.iloc[0]) if hasattr(v, "iloc") else float(v[0])


def make_candles(ax, df):
    """간단한 캔들 차트를 축에 그린다."""
    o, h, l, c = (df["Open"], df["High"], df["Low"], df["Close"])
    x = mdates.date2num(df.index.to_pydatetime())
    width = 5  # 캔들 몸통 폭(일 단위)
    for i in range(len(df)):
        xi = x[i]
        oi = _scalar(o.iloc[i]); hi = _scalar(h.iloc[i])
        li = _scalar(l.iloc[i]); ci = _scalar(c.iloc[i])
        up = ci >= oi
        color = "#d93025" if up else "#1a73e8"  # 한국식: 상승 빨강, 하락 파랑
        ax.plot([xi, xi], [li, hi], color=color, linewidth=0.8, zorder=1)
        ax.add_patch(plt.Rectangle(
            (xi - width / 2, min(oi, ci)), width, abs(ci - oi) or hi * 1e-4,
            facecolor=color, edgecolor=color, zorder=2,
        ))


def build_figure():
    assets = load_watchlist()
    n = len(assets)
    ncols = 2 if n <= 8 else 3      # 8개 이하는 2열, 많으면 3열
    nrows = math.ceil(n / ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(7 * ncols, 4 * nrows),
                             squeeze=False)
    fig.suptitle(
        f"주봉 차트  ·  {dt.datetime.now():%Y-%m-%d}",
        fontsize=16, fontweight="bold",
    )
    flat = list(axes.flat)
    for ax, (ticker, name) in zip(flat, assets):
        try:
            df = fetch_weekly(ticker)
            if len(df) < 2:
                raise ValueError("데이터 없음 (빈 응답)")
            make_candles(ax, df)
            last = _scalar(df["Close"].iloc[-1])
            prev = _scalar(df["Close"].iloc[-2])
            pct = (last / prev - 1) * 100
            ax.set_title(f"{name}   {last:,.1f}  ({pct:+.2f}%)", fontsize=11)
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%y/%m"))
            ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
            ax.grid(alpha=0.25)
        except Exception as e:  # 개별 자산 실패해도 나머지는 진행
            ax.text(0.5, 0.5, f"{name}\n데이터 오류\n{e}",
                    ha="center", va="center", transform=ax.transAxes)
            ax.set_title(name, fontsize=11)
    # 남는 빈 칸 숨김
    for ax in flat[n:]:
        ax.set_visible(False)
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    buf.seek(0)
    return buf


def send_telegram(image_buf):
    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    caption = f"📊 오늘의 주봉 차트\n금 · 은 · 비트코인 · 나스닥 · S&P500 · 코스피"
    r = requests.post(
        url,
        data={"chat_id": chat_id, "caption": caption},
        files={"photo": ("charts.png", image_buf, "image/png")},
        timeout=60,
    )
    r.raise_for_status()
    print("전송 완료:", r.json().get("ok"))


if __name__ == "__main__":
    # 한글 폰트 (GitHub Actions 러너에 설치됨)
    for f in ("NanumGothic", "Noto Sans CJK KR", "DejaVu Sans"):
        try:
            plt.rcParams["font.family"] = f
            break
        except Exception:
            continue
    plt.rcParams["axes.unicode_minus"] = False
    img = build_figure()
    send_telegram(img)

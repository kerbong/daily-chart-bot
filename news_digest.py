#!/usr/bin/env python3
"""국내 뉴스 RSS를 모아 Gemini로 요약한 뒤 텔레그램으로 전송."""
import html
import json
import os
import re
import datetime as dt
from email.utils import parsedate_to_datetime
from urllib.parse import quote
from xml.etree import ElementTree as ET

import requests

# ── 수집할 RSS 피드 ─────────────────────────────────────────
GOOGLE_NEWS = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"

REALTY_FEEDS = [
    GOOGLE_NEWS.format(q=quote("부동산 정책 OR 집값 OR 아파트 청약 OR 전세")),
    "https://www.hankyung.com/feed/realestate",
    GOOGLE_NEWS.format(q=quote("재건축 OR 재개발 OR 주택공급 OR 부동산 대출 규제")),
]

GENERAL_FEEDS = [
    "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko",   # 구글 뉴스 헤드라인
    GOOGLE_NEWS.format(q=quote("경제 OR 금리 OR 환율 OR 증시")),
]

MAX_AGE_HOURS = 30      # 이 시간보다 오래된 기사는 버림
MAX_ITEMS = 30          # 섹션별로 Claude에 넘길 최대 기사 수

TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    """RSS description의 HTML 태그·엔티티 제거."""
    return html.unescape(TAG_RE.sub(" ", text or "")).strip()


def fetch_feed(url: str) -> list[dict]:
    """RSS 한 개에서 (제목, 요약, 링크, 발행시각) 목록을 뽑는다."""
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:
        print(f"[warn] 피드 실패 {url}: {e}")
        return []

    items = []
    for item in root.iterfind(".//item"):
        title = _clean(item.findtext("title", ""))
        if not title:
            continue
        published = None
        raw_date = item.findtext("pubDate")
        if raw_date:
            try:
                published = parsedate_to_datetime(raw_date)
            except Exception:
                published = None
        items.append({
            "title": title,
            "summary": _clean(item.findtext("description", ""))[:400],
            "link": (item.findtext("link") or "").strip(),
            "published": published,
        })
    return items


def collect(feeds: list[str]) -> list[dict]:
    """여러 피드를 합치고 최신순 정렬 + 제목 중복 제거."""
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=MAX_AGE_HOURS)
    pool, seen = [], set()
    for url in feeds:
        for it in fetch_feed(url):
            pub = it["published"]
            if pub and pub.astimezone(dt.timezone.utc) < cutoff:
                continue
            # 구글 뉴스 제목은 "제목 - 매체명" 형태 → 매체명 떼고 중복 판정
            key = it["title"].rsplit(" - ", 1)[0].strip()
            if key in seen:
                continue
            seen.add(key)
            pool.append(it)
    pool.sort(key=lambda x: x["published"] or dt.datetime.min.replace(
        tzinfo=dt.timezone.utc), reverse=True)
    return pool[:MAX_ITEMS]


def _as_prompt_block(items: list[dict]) -> str:
    lines = []
    for i, it in enumerate(items, 1):
        lines.append(f"[{i}] {it['title']}\n{it['summary']}\n{it['link']}")
    return "\n\n".join(lines) or "(기사 없음)"


def _article_schema() -> dict:
    return {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "headline": {"type": "STRING"},
                "summary": {"type": "STRING"},
                "link": {"type": "STRING"},
            },
            "required": ["headline", "summary", "link"],
            "propertyOrdering": ["headline", "summary", "link"],
        },
    }


SCHEMA = {
    "type": "OBJECT",
    "properties": {"realty": _article_schema(), "general": _article_schema()},
    "required": ["realty", "general"],
}

PROMPT = """아래는 오늘 국내 뉴스 RSS에서 모은 기사 목록입니다.

# 부동산 기사
{realty}

# 일반 기사
{general}

다음 규칙으로 정리해 주세요.

- realty: 부동산 관련 기사 중 **중요한 것 4~6건**. 정책·규제·금리·공급·거래량·가격 동향처럼
  실제 의사결정에 영향을 주는 내용을 우선하고, 단순 분양 광고성 기사나 지역 소식은 제외합니다.
- general: 부동산을 **제외한** 오늘의 핵심 뉴스 **4~6건**. 거시경제·금융시장·정치·산업·국제
  등에서 파급력이 큰 것 위주로 고르고, 연예·스포츠·사건사고 단신은 제외합니다.
- headline: 원문 제목을 그대로 쓰지 말고, 핵심이 드러나게 한 줄(30자 내외)로 다시 씁니다.
- summary: 왜 중요한지가 드러나게 1~2문장으로 요약합니다. 기사에 없는 내용은 지어내지 않습니다.
- link: 해당 기사 원문 링크를 그대로 넣습니다.
- 같은 사건을 다룬 기사가 여러 건이면 하나로 합칩니다.
"""


def summarize(realty: list[dict], general: list[dict]) -> dict:
    """Gemini API(무료 티어)로 요약. GEMINI_API_KEY 환경변수 필요."""
    api_key = os.environ["GEMINI_API_KEY"]
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent")

    prompt = PROMPT.format(
        realty=_as_prompt_block(realty),
        general=_as_prompt_block(general),
    )
    r = requests.post(
        url,
        headers={"x-goog-api-key": api_key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": SCHEMA,
            },
        },
        timeout=120,
    )
    if r.status_code == 404:
        raise SystemExit(
            f"모델 '{model}' 을 쓸 수 없습니다. 아래 주소로 사용 가능한 모델을 확인한 뒤\n"
            f"GEMINI_MODEL 환경변수로 지정하세요.\n"
            f"https://generativelanguage.googleapis.com/v1beta/models?key=<API키>"
        )
    r.raise_for_status()
    data = r.json()

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise RuntimeError(f"예상과 다른 응답: {json.dumps(data)[:500]}")
    return json.loads(text)


def render(digest: dict) -> str:
    """텔레그램 HTML 메시지로 변환."""
    esc = html.escape
    today = dt.datetime.now().strftime("%Y-%m-%d")
    parts = [f"<b>📰 오늘의 뉴스 브리핑 · {today}</b>"]

    def section(title: str, items: list[dict]) -> None:
        parts.append(f"\n<b>{title}</b>")
        if not items:
            parts.append("· 오늘은 눈에 띄는 소식이 없습니다.")
            return
        for it in items:
            head = esc(it["headline"])
            link = it.get("link", "")
            head = f'<a href="{esc(link)}">{head}</a>' if link else head
            parts.append(f"• {head}\n  {esc(it['summary'])}")

    section("🏠 부동산", digest.get("realty", []))
    section("🌐 주요 뉴스", digest.get("general", []))
    return "\n".join(parts)


def send_telegram(text: str) -> None:
    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    r = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        },
        timeout=60,
    )
    r.raise_for_status()
    print("뉴스 전송 완료:", r.json().get("ok"))


if __name__ == "__main__":
    realty = collect(REALTY_FEEDS)
    general = collect(GENERAL_FEEDS)
    print(f"수집: 부동산 {len(realty)}건 / 일반 {len(general)}건")
    send_telegram(render(summarize(realty, general)))

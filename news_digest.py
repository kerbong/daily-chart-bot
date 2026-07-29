#!/usr/bin/env python3
"""국내 뉴스 RSS를 모아 Gemini로 요약한 뒤 텔레그램으로 전송."""
import html
import json
import os
import re
import time
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
MAX_ITEMS = 45          # 섹션별로 요약 모델에 넘길 최대 기사 수

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

- realty: 부동산 관련 기사 중 **중요한 것 8~10건**. 정책·규제·금리·공급·거래량·가격 동향처럼
  실제 의사결정에 영향을 주는 내용을 우선하고, 단순 분양 광고성 기사나 지역 소식은 제외합니다.
- general: 부동산을 **제외한** 오늘의 핵심 뉴스 **8~10건**. 거시경제·금융시장·정치·산업·국제
  등에서 파급력이 큰 것 위주로 고르고, 연예·스포츠·사건사고 단신은 제외합니다.
- 중요한 순서대로 정렬합니다. 쓸 만한 기사가 부족하면 억지로 채우지 말고 있는 만큼만 넣습니다.
- headline: 원문 제목을 그대로 쓰지 말고, 핵심이 드러나게 한 줄(30자 내외)로 다시 씁니다.
- summary: 왜 중요한지가 드러나게 1~2문장으로 요약합니다. 기사에 없는 내용은 지어내지 않습니다.
- link: 해당 기사 원문 링크를 그대로 넣습니다.
- 같은 사건을 다룬 기사가 여러 건이면 하나로 합칩니다.
"""


GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

# 위에서부터 차례로 시도한다 (모델 이름이 바뀌어도 알아서 넘어가도록).
# 조건: 무료 티어가 있고, 긴 기사 묶음을 요약할 수 있는 텍스트 모델.
# gemini-2.5-flash 는 신규 사용자에게 더 이상 제공되지 않아 뺐다(404).
FALLBACK_MODELS = [
    "gemini-3.5-flash",         # 무료 티어 + 검색 그라운딩 지원
    "gemini-3.1-flash-lite",    # 더 싸고 빠른 예비
    "gemini-3-flash-preview",
    "gemini-2.5-flash-lite",    # 구세대 최후 보루
]

# 잠깐 붐비는 것뿐이므로 같은 모델로 몇 번 더 시도해 본다.
RETRY_STATUSES = {429, 500, 502, 503}
RETRIES_PER_MODEL = 3
RETRY_BACKOFF = 20      # 초, 시도할 때마다 배로 늘린다
GEMINI_READ_TIMEOUT = 120   # 초. 이보다 오래 끌면 붙잡고 있지 말고 다시 던진다


def _error_message(r: requests.Response) -> str:
    """구글 에러 응답에서 message 만 뽑아낸다."""
    try:
        return r.json()["error"]["message"][:300]
    except Exception:
        return r.text[:300]


def list_available_models(api_key: str) -> list[str]:
    """generateContent 를 지원하는 모델 이름 목록."""
    try:
        r = requests.get(f"{GEMINI_BASE}/models",
                         headers={"x-goog-api-key": api_key}, timeout=30)
        r.raise_for_status()
        return [
            m["name"].removeprefix("models/")
            for m in r.json().get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]
    except Exception as e:
        print(f"[warn] 모델 목록 조회 실패: {e}")
        return []


def _thinking_config(model: str) -> dict | None:
    """모델 세대별로 '생각' 최소화 설정을 고른다.

    생각 토큰이 출력 한도를 잡아먹어 본문이 비어 돌아오는 경우가 있는데,
    기사 요약엔 깊은 추론이 필요 없다. 다만 설정 방식이 세대마다 다르다.
    """
    if model.startswith("gemini-3"):
        return {"thinkingLevel": "minimal"}     # 3.x 는 끌 수 없고 최소가 minimal
    if model.startswith("gemini-2.5"):
        return {"thinkingBudget": 0}
    return None                                 # 2.0 은 이 필드를 모른다


def _call_gemini(model: str, api_key: str, prompt: str,
                 thinking: bool = True) -> requests.Response:
    config = {
        "responseMimeType": "application/json",
        "responseSchema": SCHEMA,
    }
    if thinking:
        tc = _thinking_config(model)
        if tc:
            config["thinkingConfig"] = tc

    return requests.post(
        f"{GEMINI_BASE}/models/{model}:generateContent",
        headers={"x-goog-api-key": api_key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": config,
        },
        timeout=(15, GEMINI_READ_TIMEOUT),
    )


def _extract_json(data: dict, model: str) -> dict:
    """응답에서 JSON 본문을 꺼낸다. 실패하면 원인을 담아 예외."""
    candidates = data.get("candidates") or []
    if not candidates:
        feedback = data.get("promptFeedback", {})
        raise RuntimeError(
            f"[{model}] 후보 응답 없음. promptFeedback={json.dumps(feedback, ensure_ascii=False)}")

    cand = candidates[0]
    parts = cand.get("content", {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts)
    if not text.strip():
        raise RuntimeError(
            f"[{model}] 본문이 비어 있음. finishReason={cand.get('finishReason')} "
            f"usage={json.dumps(data.get('usageMetadata', {}), ensure_ascii=False)}")

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"[{model}] JSON 파싱 실패({e}): {text[:300]}")


def summarize(realty: list[dict], general: list[dict]) -> dict:
    """Gemini API(무료 티어)로 요약. GEMINI_API_KEY 환경변수 필요."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY 가 비어 있습니다. 저장소 Secrets 를 확인하세요.")

    prompt = PROMPT.format(
        realty=_as_prompt_block(realty),
        general=_as_prompt_block(general),
    )

    forced = os.environ.get("GEMINI_MODEL")
    models = [forced] if forced else list(FALLBACK_MODELS)

    errors = []
    for model in models:
        for attempt in range(1, RETRIES_PER_MODEL + 1):
            print(f"[info] Gemini 호출: {model} (시도 {attempt}/{RETRIES_PER_MODEL})")
            try:
                r = _call_gemini(model, api_key, prompt)
            except requests.RequestException as e:
                # 타임아웃·연결 끊김 등 응답 자체를 못 받은 경우.
                # 상태코드가 없으니 위 재시도 분기에 걸리지 않아 여기서 따로 처리한다.
                reason = f"{type(e).__name__}: {e}"
                print(f"[warn] {model} 응답 실패 — {reason}")
                if attempt == RETRIES_PER_MODEL:
                    errors.append(f"{model}: {reason}")
                    break
                wait = RETRY_BACKOFF * (2 ** (attempt - 1))
                print(f"[info] {wait}초 후 재시도")
                time.sleep(wait)
                continue

            if r.status_code == 404:        # 모델 없음 → 재시도 무의미, 다음 후보로
                print(f"[warn] {model} HTTP 404: {r.text[:400]}")
                errors.append(f"{model}: HTTP 404 — {_error_message(r)}")
                break

            if r.status_code in RETRY_STATUSES:      # 혼잡/한도 → 잠시 뒤 재시도
                print(f"[warn] {model} HTTP {r.status_code}: {r.text[:400]}")
                if attempt == RETRIES_PER_MODEL:
                    errors.append(
                        f"{model}: HTTP {r.status_code} — {_error_message(r)}")
                    break
                wait = RETRY_BACKOFF * (2 ** (attempt - 1))
                print(f"[info] {wait}초 후 재시도")
                time.sleep(wait)
                continue

            # thinkingConfig 형식이 세대마다 달라서, 이것 때문에 거절당한
            # 거라면 그 설정만 빼고 한 번 더 시도해 본다.
            if r.status_code == 400 and "thinking" in r.text.lower():
                print(f"[warn] {model} thinkingConfig 거절 — 빼고 재시도")
                try:
                    r = _call_gemini(model, api_key, prompt, thinking=False)
                except requests.RequestException as e:
                    errors.append(f"{model}: {type(e).__name__}: {e}")
                    break

            if r.status_code >= 400:        # 그 밖의 오류는 고쳐야 할 문제
                raise SystemExit(f"[{model}] HTTP {r.status_code}: {r.text[:800]}")

            return _extract_json(r.json(), model)

    available = list_available_models(api_key)
    hint = ("\n사용 가능한 모델: " + ", ".join(available[:15])) if available else \
           "\n모델 목록도 조회되지 않았습니다 — API 키가 유효한지 확인하세요."
    raise SystemExit("모든 모델 시도 실패:\n  " + "\n  ".join(errors) + hint)


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


TELEGRAM_LIMIT = 3800   # 실제 한도는 4096자. 여유를 둔다.


def _split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """줄 단위로 잘라 한 메시지가 한도를 넘지 않게 한다(HTML 태그가 쪼개지지 않도록)."""
    chunks, current = [], ""
    for line in text.split("\n"):
        if current and len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def build_speech_text(digest: dict) -> str:
    """음성으로 읽어줄 원고. 링크·기호는 빼고 자연스럽게 읽히도록 구성한다."""
    today = dt.datetime.now()
    lines = [f"{today.month}월 {today.day}일, 오늘의 뉴스 브리핑입니다."]

    def section(intro: str, items: list[dict]) -> None:
        if not items:
            lines.append(f"{intro} 오늘은 전해드릴 소식이 없습니다.")
            return
        lines.append(intro)
        for i, it in enumerate(items, 1):
            summary = it["summary"].rstrip()
            if not summary.endswith((".", "!", "?")):
                summary += "."
            lines.append(f"{i}. {it['headline']}. {summary}")

    section("먼저 부동산 소식입니다.", digest.get("realty", []))
    section("다음은 오늘의 주요 뉴스입니다.", digest.get("general", []))
    lines.append("이상입니다. 좋은 하루 보내세요.")
    return "\n".join(lines)


def synthesize(text: str, out_dir: str) -> tuple[str, bool]:
    """edge-tts 로 음성을 만든다. (파일경로, ogg여부) 반환."""
    import asyncio
    import shutil
    import subprocess

    import edge_tts

    voice = os.environ.get("TTS_VOICE", "ko-KR-SunHiNeural")
    mp3_path = os.path.join(out_dir, "digest.mp3")
    asyncio.run(edge_tts.Communicate(text, voice).save(mp3_path))

    # 텔레그램 음성 메시지(sendVoice)는 OGG/OPUS 를 요구한다.
    # ffmpeg 가 있으면 변환하고, 없으면 mp3 를 오디오 파일로 보낸다.
    if shutil.which("ffmpeg"):
        ogg_path = os.path.join(out_dir, "digest.ogg")
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", mp3_path, "-c:a", "libopus", "-b:a", "32k", ogg_path],
            capture_output=True,
        )
        if proc.returncode == 0:
            return ogg_path, True
        print(f"[warn] ffmpeg 변환 실패, mp3 로 전송합니다: {proc.stderr[-300:]!r}")
    else:
        print("[warn] ffmpeg 없음 — mp3 오디오로 전송합니다.")
    return mp3_path, False


def send_telegram_audio(path: str, is_ogg: bool) -> None:
    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    method, field = ("sendVoice", "voice") if is_ogg else ("sendAudio", "audio")
    data = {"chat_id": chat_id}
    if not is_ogg:
        data["title"] = f"뉴스 브리핑 {dt.datetime.now():%Y-%m-%d}"
    with open(path, "rb") as f:
        r = requests.post(
            f"https://api.telegram.org/bot{bot_token}/{method}",
            data=data,
            files={field: (os.path.basename(path), f)},
            timeout=120,
        )
    r.raise_for_status()
    print(f"음성 전송 완료 ({method})")


def send_telegram(text: str) -> None:
    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    for part in _split_message(text):
        r = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data={
                "chat_id": chat_id,
                "text": part,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
            timeout=60,
        )
        r.raise_for_status()
    print("뉴스 전송 완료")


if __name__ == "__main__":
    import tempfile

    realty = collect(REALTY_FEEDS)
    general = collect(GENERAL_FEEDS)
    print(f"수집: 부동산 {len(realty)}건 / 일반 {len(general)}건")

    digest = summarize(realty, general)
    send_telegram(render(digest))     # 1) 텍스트 (링크 포함)

    # 2) 음성. 실패하더라도 이미 보낸 텍스트가 있으므로 작업 전체를 실패시키지 않는다.
    if os.environ.get("ENABLE_TTS", "1") != "0":
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path, is_ogg = synthesize(build_speech_text(digest), tmp)
                send_telegram_audio(path, is_ogg)
        except Exception as e:
            print(f"[warn] 음성 전송 실패(텍스트는 정상 전송됨): {type(e).__name__}: {e}")

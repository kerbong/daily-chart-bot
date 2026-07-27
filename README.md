# 매일 아침 주봉 차트 + 뉴스 브리핑 봇

매일 아침 **한국시간 6:30**에 두 개의 메시지를 텔레그램으로 보내줍니다.

1. 금·은·비트코인·나스닥·S&P500·코스피 **주봉 차트** (`generate_charts.py`)
2. **부동산 주요 뉴스 + 부동산 외 오늘의 핵심 뉴스** 요약 (`news_digest.py`)
3. 그 요약을 읽어주는 **음성 메시지** (같은 스크립트)

뉴스는 구글 뉴스 RSS와 한국경제 부동산 RSS에서 제목·요약·링크만 모은 뒤,
Gemini API(무료 티어)로 중요한 것만 골라 다시 요약합니다. GitHub Actions로 실행됩니다.

## 설정 방법 (한 번만)

### 1. 텔레그램 봇 & Chat ID 준비
- 텔레그램에서 `@BotFather` → `/newbot` → **봇 토큰** 받기
- 만든 봇에게 아무 메시지나 전송 → `@userinfobot`에게 말 걸어 **Chat ID** 확인

### 2. 이 폴더를 GitHub 저장소로 올리기
```bash
git init
git add .
git commit -m "daily chart bot"
git branch -M main
git remote add origin https://github.com/내계정/chart-bot.git
git push -u origin main
```

### 3. 저장소에 시크릿 등록
저장소 → **Settings → Secrets and variables → Actions → New repository secret**
- `TELEGRAM_BOT_TOKEN` : 봇 토큰
- `TELEGRAM_CHAT_ID` : Chat ID
- `GEMINI_API_KEY` : 뉴스 요약용 Google Gemini API 키
  ([aistudio.google.com/apikey](https://aistudio.google.com/apikey) 에서 무료 발급,
  카드 등록 불필요)

> 하루 한 번 실행이라 Gemini 무료 티어 한도 안에서 충분히 돌아갑니다.
> 다른 모델을 쓰고 싶으면 워크플로에 `GEMINI_MODEL` 환경변수를 추가하면 됩니다
> (기본값 `gemini-2.5-flash`).

### 4. 테스트
저장소 → **Actions 탭 → Daily Weekly Charts → Run workflow** 로 즉시 실행해서
텔레그램에 차트가 오는지 확인.

## 자산 / 시각 바꾸기
- 자산: `watchlist.txt` 수정
- 시각: `.github/workflows/daily-charts.yml`의 cron 수정 (UTC 기준, KST-9시간)

## 뉴스 바꾸기
`news_digest.py` 상단에서 조정합니다.
- `REALTY_FEEDS` / `GENERAL_FEEDS` : 수집할 RSS 주소. 구글 뉴스는
  `GOOGLE_NEWS.format(q=quote("검색어"))` 형태로 키워드를 자유롭게 추가할 수 있습니다.
- `MAX_AGE_HOURS` : 몇 시간 이내 기사만 볼지 (기본 30시간)
- `PROMPT` : 몇 건을 고를지, 어떤 기사를 제외할지 등 요약 기준

## 음성 메시지
`edge-tts`(마이크로소프트 음성, 무료·키 불필요)로 요약을 읽어 텔레그램 음성 메시지로 보냅니다.
- 목소리 변경: 워크플로에 `TTS_VOICE` 환경변수 추가
  (`ko-KR-SunHiNeural` 기본 / `ko-KR-InJoonNeural` 남성 / `ko-KR-HyunsuMultilingualNeural`)
- 끄기: `ENABLE_TTS: "0"`
- 음성 생성이 실패해도 텍스트 메시지는 이미 전송된 뒤라 영향받지 않습니다.

## 참고
- 스케줄 실행은 GitHub 서버 부하에 따라 몇 분~수십 분 지연될 수 있습니다.
- 공개(public) 저장소는 Actions 무료. 비공개도 월 무료 할당량 내에서 충분합니다.

# 매일 아침 주봉 차트 봇

매일 아침 **한국시간 6:30**에 금·은·비트코인·나스닥·S&P500·코스피
주봉 차트를 텔레그램으로 보내줍니다. GitHub Actions로 무료 실행됩니다.

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

### 4. 테스트
저장소 → **Actions 탭 → Daily Weekly Charts → Run workflow** 로 즉시 실행해서
텔레그램에 차트가 오는지 확인.

## 자산 / 시각 바꾸기
- 자산: `generate_charts.py`의 `ASSETS` 리스트 수정
- 시각: `.github/workflows/daily-charts.yml`의 cron 수정 (UTC 기준, KST-9시간)

## 참고
- 스케줄 실행은 GitHub 서버 부하에 따라 몇 분~수십 분 지연될 수 있습니다.
- 공개(public) 저장소는 Actions 무료. 비공개도 월 무료 할당량 내에서 충분합니다.

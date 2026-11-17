# kbot — 한국어 국장·미장 분석 및 모의투자 봇

한국 개인투자자를 위한 **애널리스트 + 리스크 매니저 + 매매 코치** 봇입니다.
현재는 **모의투자 전용**이며 실계좌 연결은 지원하지 않습니다. 설계는 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)를 참고하세요.

## 설치 (macOS)

```bash
brew install uv
uv venv --python 3.11
uv pip install -e ".[dev]"
cp .env.example .env    # 키를 채워 넣으세요 (.env는 git에 올라가지 않습니다)
```

## 필요한 키·계정

| 항목 | 용도 | 발급처 |
|---|---|---|
| `ANTHROPIC_API_KEY` | LLM 분석 (이 봇 전용 키) | console.anthropic.com |
| `KIS_PAPER_APP_KEY/SECRET`, `KIS_PAPER_ACCOUNT_NO` | 시세·모의투자 | 한국투자증권 KIS Developers (모의투자 신청) |
| `DART_API_KEY` | 국내 공시 | opendart.fss.or.kr |
| `SEC_USER_AGENT` | 미국 공시 (이름 + 이메일) | 발급 불필요 |
| `ECOS_API_KEY` | 원/달러 환율 | ecos.bok.or.kr |
| `KRX_ID/KRX_PW` | 국내 수급·과거 시세(pykrx) | data.krx.co.kr 회원가입 |
| `DISCORD_WEBHOOK_URL` | 알림 (선택) | 디스코드 채널 설정 → 연동 → 웹후크 |

## 명령어

```bash
kbot check                      # 설정·키 상태 점검 (값은 출력하지 않음)
kbot init-db                    # DB 생성
kbot budget                     # 이번 달 LLM 사용액·남은 예산·오늘 한도
kbot sync-reference             # DART 고유번호·SEC 티커 매핑
kbot ingest bars --start 2024-01-01 --end 2026-11-14
kbot ingest fx|flows|disclosures [--start ...] [--end ...]
```

## 테스트

```bash
.venv/bin/python -m pytest
.venv/bin/lint-imports          # 설계 원칙 import 계약 검사 (pytest에도 포함)
```

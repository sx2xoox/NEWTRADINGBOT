# kbot 아키텍처 (1단계 승인안)

한국 개인투자자 옆의 **애널리스트 + 리스크 매니저 + 매매 코치**. 현재는 **모의투자 전용**이다.

## 협상 불가 원칙과 코드상 강제 방법

| 원칙 | 강제 방법 |
|---|---|
| LLM은 분석·신호 '입력'만, 최종 주문은 규칙 기반 리스크 엔진 | `import-linter` 계약: `kbot.risk`·`kbot.execution`은 `kbot.llm`·`kbot.analysis`·`anthropic` import 금지, `kbot.strategy`·`kbot.backtest`는 `kbot.llm` import 금지 (tests/contracts에서 검사). 주문은 `ApprovedOrder` 타입만 받음(6단계). 전략 DSL은 LLM 특성만으로 진입 불가(4단계) |
| LLM 비용 하드캡 | Anthropic SDK는 `kbot.llm.client`에서만 import 가능(계약). 모든 호출은 예약→호출→정산을 거침 |
| 실거래 2단계 전환 | 현재는 실계좌 미지원: `TRADING_MODE=live`면 CLI가 종료(코드 2), `KisCredentials(paper=False)`는 예외 |
| 주문 멱등성 | 6단계 OMS: 전송 전 `PENDING_SUBMIT` 기록, 타임아웃 시 `UNKNOWN`→체결조회로 대조 후에만 재전송. 조회용 HTTP 재시도 헬퍼는 주문에 사용 금지 |
| 비밀값 보호 | `.env` + `SecretStr`, 로그 마스킹 필터, LLM 프롬프트에 비밀값이 있으면 전송 차단(`PromptSecretLeak`), KIS 토큰 캐시 파일 권한 0600 |
| 한국어 출력 | 사용자에게 보이는 메시지·리포트는 모두 한국어 |

## 계층

```
data.adapters (KIS·DART·EDGAR·pykrx·yfinance·ECOS) → data.ingest → storage(SQLite)
                                                          │
          analysis(B 공시, A 브리핑·밸류체인, C 수급, F 코칭) ←┤→ strategy(결정론적 규칙/DSL) → backtest
                 │                                        │                │
              llm(client·budget·router·cache·verify)       │           risk(최종 결정) → execution(OMS, KIS 모의)
                                                          │                │
                                        interfaces(디스코드·대시보드) ← journal(D) / portfolio(G)
```

## 리스크 한도 동작 (사용자 확인 사항)

- 1일 손실 -2% 도달 → **신규 매수만 차단**, 다음 거래일 자동 해제. 기존 포지션 강제 청산 없음.
- 총 손실 -10% 도달 → 신규 매수 차단, 수동 해제 명령 필요.
- 기존 포지션은 전략의 손절·익절 규칙으로만 매도. 위험을 줄이는 매도는 항상 허용.
- `config/risk.yaml`의 `on_limit_action: flatten`으로 강제 청산을 켤 수 있으나 기본값 아님.

## 모의투자 손익 측정

KIS 모의투자 체결은 실제 시장과 다르다(호가 충격·슬리피지 없음, 수수료·세금 차이 가능). 그래서 두 손익을 함께 기록한다.
1. **모의 체결 손익** — KIS 모의투자 체결 그대로
2. **현실 보정 손익** — 같은 체결에 실제 수수료·세금(`config/tax_fees/`)·슬리피지 모델을 다시 적용

## 데이터 소스 정리

| 데이터 | 1순위 | 대안 | 비고 |
|---|---|---|---|
| 국내 일봉(과거) | pykrx | KIS | pykrx는 KRX 로그인 필요(KRX_ID/KRX_PW) |
| 미국 일봉(과거) | yfinance | KIS 해외 | **생존 편향 있음**(상장폐지 종목 부족) — 리포트에 명시 |
| 투자자별 수급 | pykrx (원 단위) | KIS(단위 확인 후 추가) | |
| 상장폐지 포함 유니버스 | pykrx 과거 시점 종목 목록 | | 국내 생존 편향 방지 |
| 국내 공시 | DART OpenAPI | | 정정공시 플래그 |
| 미국 공시 | SEC EDGAR | | User-Agent에 연락처 필수, 초당 10건 이하 |
| 원/달러 | 한국은행 ECOS 731Y001 | | 매매기준율 |

## LLM 예산

- 기간: 매월 `LLM_BUDGET_RESET_DAY`일(없으면 말일) ~ 다음 리셋일 전날
- 일일 한도 = (월 예산 − 오늘 이전 사용액) ÷ 남은 일수, KST 0시 재계산
- 예약: 입력 추정 토큰 × 가장 비싼 입력 단가 + `max_tokens` × 출력 단가 → 실제 비용 이하가 보장됨
- 70% 경고(기간당 1회) / 90% 저가 모델만 / 100% LLM 중단(매매·리스크는 계속)
- `urgent=True`(보유 종목 위험 공시 등)는 일일 한도는 넘을 수 있으나 월 한도는 절대 못 넘음
- 단가는 `config/llm_pricing.yaml`(공식 가격표, 확인일 기록)에서만 관리
- 같은 공시는 `cache_key`(접수번호)+프롬프트 버전으로 캐싱해 재호출하지 않음

## 단계 계획

1. 아키텍처 ✅
2. 데이터 수집·저장 + LLM 예산 관리 ✅
3. 공시·뉴스 LLM 해석(B) + 아침 브리핑(A 프리뷰) + 인용 검증 + 배치 처리
4. 전략 인터페이스 + 백테스트 엔진 + 자연어→DSL 전략(E)
5. 밸류체인 매핑·수급 분석(A, C) + 검증 실험
6. 리스크 엔진 + KIS 모의 주문(OMS) + 매매일지·반대 논리(D)
7. 행동 코칭·세금·환율(F, G), 디스코드 봇, 대시보드

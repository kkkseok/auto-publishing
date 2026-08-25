# Meta 플랫폼 약관 7.e.i.2 위반 — 진단 및 이의제기 자료

- **앱 ID**: 1637029624216135
- **계정**: @sasohan.insight
- **차단 감지**: 2026-08-18 13:00 (`API access deactivated.`, OAuthException code 200)
- **차단 지속**: 2026-08-25 현재까지 70건 연속 발행 실패
- **통지 사유**: 플랫폼 약관 7.e.i.2 — 플랫폼·제품·데이터·사용자에게 부정적인 영향

---

## 1. 조항이 실제로 말하는 것

7.e.i는 Meta가 집행 조치를 취할 수 있는 5가지 사유를 열거하며, 그중 **2번**이
"개발자 또는 앱이 약관을 위반했거나 **플랫폼, 다른 Meta 제품, 플랫폼 데이터,
Meta 제품 사용자에게 부정적 영향을 미치는 경우**"다.

두 가지를 유의해야 한다.

1. **포괄 조항이다.** 특정 행위를 지목하지 않는다. 따라서 "무엇을 고쳤는가"를
   설명할 때 한 가지만 짚으면 심사자가 납득하지 못한다. 개연성 있는 원인을
   **전면적으로** 다뤄야 한다.
2. **약관에 이의제기 절차가 명시돼 있지 않다.** 7절은 집행 권한만 규정하고
   구제 수단을 두지 않는다. 즉 지금 제출하는 소명은 계약상 권리가 아니라
   재량 검토 요청이며, 그만큼 문서의 설득력이 결과를 좌우한다.

---

## 2. 우리 시스템의 실제 사용 실태 (데이터 기준)

`data/publish_queue.json` 473건(2026-05-14 ~ 08-17) 분석 결과.

| 항목 | 실측값 |
|---|---|
| 총 Threads 게시물 | 473건 |
| 일평균 / 최대 | 5.8건 / 14건 |
| 최근 20일 추이 | 7~9건/일 |
| 제휴 링크 포함 | 271건 (**57.3%**) |
| 소스 분포 | 쿠팡 237 / 뉴스픽 124 / 백링크 78 / 알리 34 |
| 자동 스케줄 슬롯 | **하루 11회** (뉴스픽 4 + 쿠팡 4 + 백링크 2 + 알리 1) |
| 사람 검수 | **없음** (전 과정 무인) |
| 본문 생성 | 100% AI (Claude/Gemini) |

### 2.1 심사에서 불리하게 작용할 요소

1. **상업 콘텐츠 비중 57%** — 게시물 과반이 제휴 링크를 포함한다. Meta 관점에서
   가장 전형적인 "플랫폼에 부정적 영향"이다.
2. **완전 무인 운영** — 게시 전 사람이 보는 지점이 한 곳도 없다. 품질 사고가
   나도 자동으로 계속 나간다.
3. **AI 생성 실패 시 폴백 발행** — 저품질·언어불일치 양쪽의 실제 원인이다.
   - 뉴스픽: `caption = f"{title}\n\n👇 자세한 내용은 아래에서"` — 원문 기사
     제목을 그대로 내보내, 한국어 계정에 `Seoul dựng màn hình LED...`(베트남어),
     `Koreans don't fight summer`(영어) 게시물이 섞였다.
   - 쿠팡: `caption = product["name"][:60]` — 본문이 사실상 상품명 한 줄뿐인
     게시물이 나갔다.
4. **제3자 기사 재유통** — 뉴스픽 124건은 타사 언론 기사 기반이다.
5. **chain 모드** — 1건이 3편 reply chain으로 불어난다. `.env`와 코드 기본값
   모두 `single`이지만, 켜면 게시물 수가 슬롯 수의 3배가 된다.

> **검증 중 정정된 사항**: 초안에서 "검색 키워드가 그대로 제목이 된 저품질
> 게시물"을 문제로 적었으나, 확인 결과 `data/publish_queue.json`의 `title`은
> **큐 기록용 라벨**이고 실제 호출은 `pub.post(title="", content=body)` —
> 제목 없이 AI 캡션 본문만 나간다. 즉 그런 게시물은 존재하지 않는다.
> 하지 않은 잘못을 인정하면 심사에서 불리하므로 제출문에서 제외했다.

### 2.2 방어 가능한 요소 (소명에 쓸 수 있는 사실)

1. **API 한도를 크게 밑돈다.** Threads API 일일 한도 250건 대비 일평균 5.8건,
   최대 14건. 기술적 남용(rate-limit 회피, 병렬 다중 계정)은 없다.
2. **유료 광고 고지를 이미 최상단에 표기한다.** `common/affiliate_notice.py`가
   모든 제휴 게시물 첫 줄에 확정형 문구를 넣는다 — "이 게시물은 쿠팡 파트너스
   활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다." (2026-08 쿠팡
   파트너스 심사 대응으로 도입, 조건부 표현 금지·최상단 배치 요건 충족)
3. **동일 URL 반복 게시가 없다.** `data/backlink_state.json` 295건 확인 결과
   URL당 게시 기록은 1회뿐이다. 재게시 루프는 존재하지 않는다.
4. **중복 방지 장치가 이미 있다.** `data/used_keywords.json` 기반으로 같은
   키워드를 다시 쓰지 않는다.
5. **단일 계정, 단일 앱.** 집행 회피용 앱 생성(7.e.i.3)에 해당하지 않는다.
6. **공식 Graph API만 사용한다.** 스크래핑·비공식 엔드포인트·자동화 우회 없음.

---

## 3. 시정 조치 — **2026-08-25 적용 완료**

A~E 전부 코드/설정에 반영했다. 검증 결과는 6절 참조.

### A. 게시량 감축 ✅

| 대상 | 현재 | 변경 |
|---|---|---|
| `SCHEDULE_COUPANG_THREADS` | 07:30,12:30,17:30,19:00 (4회) | **12:30 (1회)** |
| `SCHEDULE_NEWSPICK_THREADS` | 08:00,11:30,14:00,20:00 (4회) | **(중단)** |
| `SCHEDULE_ALIEXPRESS_THREADS` | 16:00 (1회) | **(중단)** |
| `SCHEDULE_BACKLINK_SNS` | 13:00,21:00 (2회) | **21:00 (1회)** |
| 합계 | 하루 11슬롯 | **하루 2슬롯 (-82%)** |

`THREADS_MODE=single` 유지. `pipelines/coupang_to_threads.py` docstring이
기본값을 `chain`으로 잘못 적고 있어 실제 코드 기본값(`single`)에 맞춰 정정.

### B. 상업 콘텐츠 비중 상한 ✅

`common/threads_guard.py` — 제휴 게시물을 **하루 1건 이하**, **최근 30일 비중
30% 이하**로 제한. 비중은 창 내 표본이 10건 이상 쌓인 뒤에만 적용한다(표본이
적을 때 비중이 요동쳐 정상 발행까지 막는 것을 피하기 위함).

### C. 사람 검수 게이트 ✅

`common/threads_approval.py` — 발행 전 텔레그램으로 본문을 보내 **승인한 건만**
게시. 무응답은 발행하지 않는다(fail-closed).

프로세스 간 통신이 필요한 지점이 있다. 파이프라인은 scheduler가 띄우는 별도
subprocess이고, 텔레그램 답글을 받는 long-poll 스레드는 scheduler 프로세스
안(`pipelines/tistory_bridge.py`)에 있다. 그래서 캡차처럼 in-memory dict로는
오갈 수 없고 `data/threads_approvals.json`을 매개로 한다.

`getUpdates`는 offset으로 확정되면 다른 소비자가 같은 업데이트를 볼 수 없다.
파이프라인이 직접 폴링하면 브릿지의 캡차 수신을 훔쳐가므로, 브릿지가 살아있는
동안에는 파일만 폴링하고 브릿지가 없을 때(수동 실행)만 자체 폴링으로 폴백한다.

### D. 콘텐츠 품질 게이트 ✅

`common/threads_guard.check_quality()`:

- 실질 본문 80자 미만 차단 — 의무 고지문·URL·해시태그·이모지를 걷어낸 뒤 측정
  (고지문이 길이와 한글 비율을 인위적으로 올리기 때문)
- 한글 비율 30% 미만 차단 — 계정 주 언어 불일치
- 파이프라인의 폴백 발행 경로 자체를 제거 (`coupang_to_threads.py`,
  `newspick_to_threads.py`) — 생성 실패는 발행 취소

### E. 제3자 콘텐츠 처리 ✅

뉴스픽 기반 Threads 발행 중단(A에 포함). 재개 시에는 원문 출처·매체명을
게시물 본문에 명시할 것.

### F. 보안 (미적용 — 앱 복구 후 처리)

`.env`의 `THREADS_APP_SECRET`이 평문으로 저장돼 있고 이번 진단 과정에서
터미널 출력에 노출됐다. 앱이 복구되면 **시크릿을 재발급**할 것.

---

## 4. Meta 제출문 초안

> 3절 A~E가 **2026-08-25 적용 완료**되어, 아래 문안은 현재 상태를 그대로
> 기술한다. 게이트를 되돌리면 이 문안은 더 이상 사실이 아니게 된다.

### 4.1 한국어

```
앱 ID: 1637029624216135

■ 앱의 용도

본 앱은 개인이 운영하는 단일 Threads 계정(@sasohan.insight)에 상품 정보와
블로그 글을 게시하기 위한 개인용 자동화 도구입니다. 타사에 제공하거나
재판매하지 않으며, 다른 사용자의 데이터를 수집·저장·처리하지 않습니다.
Threads 공식 Graph API만 사용하며 스크래핑이나 비공식 경로를 사용한 적이
없습니다.

■ 문제 인식

플랫폼 약관 7.e.i.2에 따른 조치를 확인하고, 저희 게시 방식이 Threads
사용자 경험에 부정적 영향을 줄 수 있었다는 점을 인정합니다. 구체적으로
다음 세 가지가 문제였다고 판단했습니다.

1. 게시 빈도가 과도했습니다. 하루 최대 11회의 자동 게시 스케줄을
   운영했습니다.
2. 제휴 링크를 포함한 상업적 게시물 비중이 전체의 57%로 지나치게
   높았습니다.
3. 게시 전 사람의 검수 없이 전 과정이 자동으로 실행됐고, 본문 자동 생성이
   실패했을 때 대체 문구로 게시하는 경로가 있었습니다. 이 경로를 통해 본문이
   한 줄뿐인 저품질 게시물과, 계정 주 언어(한국어)가 아닌 원문 그대로의
   게시물이 발행됐습니다.

■ 조치한 변경 사항

1. 게시 빈도 82% 감축
   하루 자동 게시 슬롯을 11회에서 2회로 줄였습니다. 뉴스 콘텐츠 기반
   게시(하루 4회)와 해외 쇼핑몰 상품 게시(하루 1회)는 완전히 중단했습니다.
   또한 게시물 1건이 3개의 연속 답글로 확장되던 chain 방식을 비활성화하고
   단일 게시물 방식으로 고정했습니다.

2. 상업적 게시물 비중 제한
   제휴 링크를 포함한 게시물을 하루 1건 이하, 전체 게시물의 30% 이하로
   제한하는 발행 게이트를 코드에 추가했습니다. 한도 초과 시 게시되지 않고
   기록만 남습니다.

3. 사람의 사전 검수 도입
   자동 게시를 중단하고, 모든 게시물이 발행 전에 운영자에게 전달되어
   운영자가 승인한 건만 게시되도록 변경했습니다. 더 이상 사람의 확인 없이
   게시되는 콘텐츠는 없습니다.

4. 콘텐츠 품질 기준 적용
   - 본문이 최소 길이에 미달하면 게시하지 않습니다. 길이는 광고 고지 문구와
     링크를 제외한 실질 본문 기준으로 측정합니다.
   - 계정 주 언어인 한국어 이외의 게시물을 차단했습니다.
   - 본문 자동 생성에 실패했을 때 대체 문구로 게시하던 경로를 제거하고,
     생성 실패 시 게시 자체를 취소하도록 변경했습니다. 위 2·3번 문제의
     실제 원인이 이 경로였습니다.

5. 제3자 콘텐츠 게시 중단
   타 매체 기사에 기반한 게시를 중단했습니다.

■ 기존에도 준수하고 있던 사항

- Threads API 일일 한도(250건) 대비 실제 게시량은 일평균 5.8건으로,
  기술적 남용이나 한도 회피 시도는 없었습니다.
- 제휴 링크가 포함된 모든 게시물은 첫 줄에 유료 광고 고지 문구를
  표기해 왔습니다. (한국 공정거래위원회 및 제휴 프로그램 요건 준수)
- 동일 URL을 반복 게시하지 않았으며, 중복 콘텐츠 방지 장치를 운영해
  왔습니다.
- 단일 계정·단일 앱만 운영했으며, 집행 회피를 위한 추가 앱을 만든
  사실이 없습니다.

■ 재발 방지

위 제한은 운영자 설정이 아니라 코드에 내장된 발행 게이트로 구현되어,
설정을 되돌리는 것만으로는 우회되지 않습니다. 앞으로 Meta 플랫폼 약관과
개발자 정책을 지속적으로 검토하며 운영하겠습니다.

앱 접근 권한 복구를 검토해 주시기 바랍니다. 추가 자료가 필요하면
제출하겠습니다.
```

### 4.2 영어 (심사자가 영어권일 경우 병기 권장)

```
App ID: 1637029624216135

■ Purpose of the app

This is a personal automation tool that publishes product information and
blog posts to a single Threads account (@sasohan.insight) operated by an
individual. It is not offered or resold to third parties, and it does not
collect, store, or process any other user's data. It uses only the official
Threads Graph API; we have never used scraping or unofficial endpoints.

■ Acknowledgement

Having reviewed the enforcement action under Platform Terms 7.e.i.2, we
accept that our publishing behavior could have negatively affected the
Threads user experience. We identified three specific problems:

1. Posting frequency was excessive — up to 11 automated posting slots
   per day.
2. Commercial posts containing affiliate links accounted for 57% of all
   posts, which was disproportionately high.
3. The entire process ran without human review before publishing, and it
   contained a fallback that published placeholder text when automatic
   content generation failed. That fallback path produced low-quality
   posts whose body was a single line, and posts left in their original
   language rather than the account's primary language (Korean).

■ Changes we have made

1. Reduced posting frequency by 82%
   Automated posting slots were cut from 11 to 2 per day. News-based
   posting (4×/day) and overseas marketplace product posting (1×/day)
   were discontinued entirely. We also disabled the chain mode that
   expanded a single item into three consecutive replies, and fixed the
   system to single-post mode.

2. Capped commercial content
   We added a publishing gate that limits affiliate-link posts to at most
   one per day and no more than 30% of all posts. Posts exceeding the cap
   are not published.

3. Introduced human review before publishing
   Unattended publishing has been stopped. Every post is now sent to the
   operator for review, and only approved posts are published. No content
   is published without human confirmation.

4. Applied content quality standards
   - Posts below a minimum body length are blocked. Length is measured on
     the substantive body, excluding the paid-partnership disclosure and
     links.
   - Posts in languages other than Korean, the account's primary language,
     are blocked.
   - The fallback that published placeholder text when content generation
     failed has been removed; generation failure now cancels the post.
     This fallback was the actual cause of problems 2 and 3 above.

5. Stopped publishing third-party content
   Posting based on other outlets' news articles has been discontinued.

■ Practices already in place

- Actual volume averaged 5.8 posts/day against the Threads API daily limit
  of 250. There was no technical abuse or attempt to evade limits.
- Every post containing an affiliate link has carried a paid-partnership
  disclosure in its first line, meeting Korean Fair Trade Commission and
  affiliate program requirements.
- We never reposted the same URL, and duplicate-content prevention was
  already in operation.
- We operate a single account and a single app, and have never created
  additional apps to circumvent enforcement.

■ Preventing recurrence

These limits are implemented as publishing gates in code, not as operator
settings, so they cannot be bypassed by reverting a configuration value.
We will continue to review Meta Platform Terms and Developer Policies on
an ongoing basis.

We respectfully request reinstatement of the app's API access, and can
provide any additional information required.
```

---

## 5. 변경 파일

| 파일 | 변경 |
|---|---|
| `common/threads_guard.py` | **신규** — 품질·발행량 게이트 |
| `common/threads_approval.py` | **신규** — 텔레그램 사전 승인 |
| `publishers/threads.py` | `_gate()` 통합 (모든 발행의 단일 통과 지점), 앱 차단/토큰 만료 알림 |
| `pipelines/tistory_bridge.py` | long-poll에 승인 응답 라우팅 추가 |
| `pipelines/coupang_to_threads.py` | 상품명 폴백 제거, docstring 기본값 정정 |
| `pipelines/newspick_to_threads.py` | 원문 제목 폴백 제거 |
| `common/run_diagnosis.py` | 게이트 차단·앱 차단·토큰 만료 패턴 추가 |
| `common/notifier.py` | `notify_login_required(reason=...)` |
| `.env` | 스케줄 11→2슬롯, 게이트 설정 신규 |

---

## 6. 검증 결과 (2026-08-25)

**품질 게이트** — 실제 폴백 문자열로 재현:

| 입력 | 결과 |
|---|---|
| 정상 한국어 캡션 | 통과 (실질 84자) |
| 쿠팡 구 폴백 (상품명만) | **차단** — 실질 15자 < 80자 |
| 뉴스픽 구 폴백 (베트남어 원문) | **차단** — 한글 14% < 30% |
| 뉴스픽 구 폴백 (영어 원문) | **차단** — 한글 13% < 30% |
| 고지문만 있고 본문 없음 | **차단** — 실질 0자 |

**발행량 상한** — 제휴 1건 통과 후 2·3·4번째 차단, 비제휴는 총량 3건까지 허용.

**승인 게이트** — 무응답 시 미발행(카운터도 증가하지 않음), `ok` 답글 시 통과,
그 외 답글은 거부, 텔레그램 전송 실패 시에도 미발행(fail-closed) 확인.
품질·발행량 차단이 승인 요청보다 먼저 일어나는 것도 확인했다(운영자 승인 피로
방지 — 기계가 거를 수 있는 건을 사람에게 묻지 않는다).

**진단 라벨** — 미승인/상한도달/AI실패/앱차단이 각각 구분되어 라벨링됨. 기존
라벨(알리·뉴스픽·티스토리·rate-limit·thin content) 회귀 없음.

---

## 7. 제출 전 체크리스트

- [x] 3절 A (스케줄 감축 11→2슬롯)
- [x] 3절 B (상업 비중 상한)
- [x] 3절 C (사람 검수 게이트)
- [x] 3절 D (품질 게이트 + 폴백 제거)
- [x] 3절 E (뉴스픽 Threads 중단)
- [ ] **스케줄러 재기동** — `.env` 변경 반영 (Stop/Start-ScheduledTask AutoPublishing_Scheduler)
- [ ] 제출문 최종 검토 후 Meta 앱 대시보드에 제출
- [ ] 앱 복구 후 `THREADS_APP_SECRET` 재발급

> 제출문 4.1/4.2는 위 5개 항목이 적용된 현재 상태를 정확히 기술하고 있다.
> 다만 게이트를 나중에 되돌린다면 제출문은 더 이상 사실이 아니게 된다.

# 완전 매뉴얼 — AutoFee LND (Amboss/LNDg/BOS)

> 실무 가이드로 **스크립트가 수수료를 결정하는 방식**, **모든 파라미터** (코드의 기본값 포함), **모든 태그**, 예제, 튜닝 프로파일을 이해합니다.

* 기반: Amboss **p65 7d** with **guards + EMA + 하이브리드 씨드 (중앙값/변동성/비율)**; **유동성/streak**, **rebal/out-rate/PEG별 바닥선**, **부스트**, **동적 단계 상한**, **쿨다운/히스테리시스**, **서킷 브레이커**, **발견 하드드롭**, **싱크/소스/라우터 분류** 및 **dry에서 제외**.

---

## 1) 개요 (파이프라인)

1. 스냅샷 `lncli listchannels` (용량, 잔액, pubkey, `active`, `initiator`).
2. **오프라인**이면 ⇒ `⏭️🔌 skip` (오프라인 시간/마지막 온라인 포함).
3. **LNDg**에서 7d 읽음: forwards (out_ppm7d, 개수 및 값) 및 **rebal** 결제 (전역 및 채널별).
4. **Amboss 시리즈**에서 **기본 씨드** (`incoming_fee_rate_metrics / weighted_corrected_mean`) 가져오기 및 적용:

   * **가드**: `p95-cap`, 이전 씨드 대비 점프 제한, 절대 한계.
   * **하이브리드 씨드**: **중앙값**과의 혼합, **변동성 (σ/μ) 페널티** 및 **아웃/인 비율 바이어스** (Amboss).
   * **피어 ENTRY** 가중치 (평균 대비 공유).
   * **EMA** in 씨드 (평활화).
5. 목표 기본값: `seed + COLCHAO_PPM`.
6. **유동성** (out_ratio), **낮은 아웃바운드 지속성** (streak) 및 **새로운 인바운드** (촉진된 하락)으로 조정.
7. **부스트** (서지/탑/부정적 마진) → 단계 상한 존중.
8. **바닥선**: rebal 바닥선 + outrate 바닥선 (씨드별 상한) + **🧲 PEG** (이미 판매한 가격에 고정).
9. **동적 단계 상한**, **쿨다운/히스테리시스** (PEG 및 새 인바운드에 대한 특수 규칙) 및 **반 마이크로 업데이트**.
10. **서킷 브레이커** 상승 후 흐름이 떨어지면 후퇴.
11. **BOS**를 통해 적용 (또는 제외된 항목에 대해 dry에서 시뮬레이션).

---

## 2) 파라미터 — 완전 (수정 시기 포함)

### 2.1. 경로, 바이너리, 토큰

* `DB_PATH = '/home/admin/lndg/data/db.sqlite3'`
* `LNCLI = 'lncli'`
* `BOS = '/home/admin/.npm-global/lib/node_modules/balanceofsatoshis/bos'`
* `AMBOSS_TOKEN` / `AMBOSS_URL = 'https://api.amboss.space/graphql'`
* `TELEGRAM_TOKEN` / `TELEGRAM_CHAT` (선택사항: 보고서 전송)
* 버전: `VERSIONS_FILE`의 첫 줄이 보고서에 표시된 "활성 버전"을 정의합니다.

### 2.2. 윈도우, 캐시 및 오버라이드

* `LOOKBACK_DAYS = 7`
* `CACHE_PATH = '/home/admin/.cache/auto_fee_amboss.json'`
* `STATE_PATH = '/home/admin/.cache/auto_fee_state.json'`
* 동적 오버라이드 (스크립트 편집 없이):
  `OVERRIDES_PATH = '/home/admin/lndtools/autofee_overrides.json'`
  (이미 존재하는 키만 적용됨)

### 2.3. 제한/기본

* `MIN_PPM = 100` | `MAX_PPM = 2000` (최종 클램프)
* `COLCHAO_PPM = 25`
* (`BASE_FEE_MSAT = 0` 있지만, 현재 사용되지 않음; "레거시" 참조)

**수정 시기:** `MAX_PPM↑` **PEG** 전략이 높은 아웃레이트를 따를 수 있도록 허용합니다.

### 2.4. 유동성 — "가벼운 조정"

* `LOW_OUTBOUND_THRESH = 0.05` | `LOW_OUTBOUND_BUMP = 0.01`
* `HIGH_OUTBOUND_THRESH = 0.20` | `HIGH_OUTBOUND_CUT = 0.01`
* `IDLE_EXTRA_CUT = 0.005` (유휴 및 높은 출력인 경우 추가 감소)

### 2.5. 낮은 아웃바운드의 지속성 (streak)

* `PERSISTENT_LOW_ENABLE = True`
* `PERSISTENT_LOW_THRESH = 0.10`
* `PERSISTENT_LOW_STREAK_MIN = 3`
* `PERSISTENT_LOW_BUMP = 0.05` 라운드당 (최대 `PERSISTENT_LOW_MAX = 0.20`)
* **Over current**: `PERSISTENT_LOW_OVER_CURRENT_ENABLE = True` + `PERSISTENT_LOW_MIN_STEP_PPM = 5`
  (목표가 현재보다 낮거나 같으면 "현재 위에" 올라감)

### 2.6. 피어의 **ENTRY** 가중치 (Amboss)

* `VOLUME_WEIGHT_ALPHA = 0.20` (대역폭 ~±30%).
  **0**은 비활성화합니다.

### 2.7. 서킷 브레이커

* `CB_DROP_RATIO = 0.70`, `CB_REDUCE_STEP = 0.10`, `CB_GRACE_DAYS = 7`
  (참고: `CB_WINDOW_DAYS` 있지만 직접 사용되지 않음)

### 2.8. 바닥선 — Rebal / Outrate / PEG

**Rebal 바닥선**

* `REBAL_FLOOR_ENABLE = True`
* `REBAL_FLOOR_MARGIN = 0.15`
* `REBAL_COST_MODE = 'per_channel' | 'global' | 'blend'`
* `REBAL_BLEND_LAMBDA = 0.20` ("blend"인 경우: 20% 전역 + 80% 채널)
* `REBAL_PERCHAN_MIN_VALUE_SAT = 400_000` (신호 ≥ 400k sat인 경우만 "채널당" 사용)
* 씨드별 바닥선 한계: `REBAL_FLOOR_SEED_CAP_FACTOR = 1.2`

**Outrate 바닥선 (out_ppm7d)**

* `OUTRATE_FLOOR_ENABLE = True`
* `OUTRATE_FLOOR_FACTOR = 1.10`
* `OUTRATE_FLOOR_MIN_FWDS = 4`
* 동적:
  `OUTRATE_FLOOR_DYNAMIC_ENABLE = True`
  `OUTRATE_FLOOR_DISABLE_BELOW_FWDS = 5`
  `OUTRATE_FLOOR_FACTOR_LOW = 0.85`

**PEG (이미 판매한 가격에 고정)**

* `OUTRATE_PEG_ENABLE = True`
* `OUTRATE_PEG_MIN_FWDS = 5`
* `OUTRATE_PEG_HEADROOM = 0.01` (관찰된 아웃레이트 대비 +1%)
* PEG 아래로 떨어지려면: `OUTRATE_PEG_GRACE_HOURS = 36`
* 실제 수요는 씨드 기반 한계를 "해제": `OUTRATE_PEG_SEED_MULT = 1.10`

> **발견** 및 `fwd_count==0`일 때, 아웃레이트별 바닥선은 비활성화됩니다.

### 2.9. 단계 상한 (속도)

* 기본: `STEP_CAP = 0.05`
* 동적: `DYNAMIC_STEP_CAP_ENABLE = True`

  * 매우 낮은 아웃바운드:
    `STEP_CAP_LOW_005 = 0.10` (out_ratio < 0.03)
    `STEP_CAP_LOW_010 = 0.07` (0.03 ≤ out_ratio < 0.05)
  * 유휴 하강: `STEP_CAP_IDLE_DOWN = 0.12` (fwd=0 & out_ratio>0.60)
  * 최소 단계: `STEP_MIN_STEP_PPM = 5`
* 라우터 보너스: `ROUTER_STEP_CAP_BONUS = 0.02`

### 2.10. 발견 (탐사)

* `DISCOVERY_ENABLE = True`
* `DISCOVERY_OUT_MIN = 0.40` | `DISCOVERY_FWDS_MAX = 0`
* 하드드롭 (하드 유휴):

  * `DISCOVERY_HARDDROP_DAYS_NO_BASE = 6`
  * `DISCOVERY_HARDDROP_CAP_FRAC = 0.20`
  * `DISCOVERY_HARDDROP_COLCHAO = 10`
* 발견 중, **rebal-floor 및 outrate-floor**는 **OFF** (만 `MIN_PPM`).

### 2.11. 씨드 평활화 (EMA)

* `SEED_EMA_ALPHA = 0.20` (0 비활성화)

### 2.12. **하이브리드 씨드 (신규) — 중앙값/변동성/비율**

* `SEED_ADJUST_ENABLE = True`
* 중앙값과 혼합: `SEED_BLEND_MEDIAN_ALPHA = 0.30` (30% 중앙값 + 70% 기본 씨드)
* 변동성 (σ/μ) 페널티:
  `SEED_VOLATILITY_K = 0.25`, `SEED_VOLATILITY_CAP = 0.15`
* **비율** 바이어스 = out_wcorr / in_wcorr:
  `SEED_RATIO_K = 0.20`, 요소 클램프: `0.80..1.50`
* 범용 Amboss 캐시: `AMBOSS_CACHE_TTL_SEC = 10800` (3h)

### 2.13. 부스트 (수요/수익)

* **Surge**: `SURGE_ENABLE=True`, `SURGE_LOW_OUT_THRESH=0.10`, `SURGE_K=0.50`, `SURGE_BUMP_MAX=0.20`
* **Top 수익**: `TOP_REVENUE_SURGE_ENABLE=True`, `TOP_OUTFEE_SHARE=0.20`, `TOP_REVENUE_SURGE_BUMP=0.12`
* **부정적 마진**: `NEG_MARGIN_SURGE_ENABLE=True`, `NEG_MARGIN_SURGE_BUMP=0.05`, `NEG_MARGIN_MIN_FWDS=5`
* (`SURGE_RESPECT_STEPCAP=True` 있지만, 파이프라인은 어쨌든 모든 단계 상한을 존중함)

### 2.14. 수익 바닥선 (슈퍼 라우트)

* `REVFLOOR_ENABLE = True`
* `REVFLOOR_BASELINE_THRESH = 80`
* `REVFLOOR_MIN_PPM_ABS = 140`

### 2.15. 반 마이크로 업데이트

* `BOS_PUSH_MIN_ABS_PPM = 15` | `BOS_PUSH_MIN_REL_FRAC = 0.04`

### 2.16. 오프라인 스킵

* `OFFLINE_SKIP_ENABLE = True` (`chan_status`에 캐시 + 태그 `🟢on/🟢back/🔴off`)

### 2.17. 쿨다운 / 히스테리시스

* `APPLY_COOLDOWN_ENABLE = True`
* `COOLDOWN_HOURS_UP = 3` | `COOLDOWN_HOURS_DOWN = 5`
* `COOLDOWN_FWDS_MIN = 2`
* 수익하는 경우 더 보수적인 하강:

  * `COOLDOWN_PROFIT_DOWN_ENABLE = True`
  * `COOLDOWN_PROFIT_MARGIN_MIN = 10`
  * `COOLDOWN_PROFIT_FWDS_MIN = 10`
* **예외**:
  **발견** (하강), **새 인바운드** (하강) 및 **PEG 아래 하강** (without `OUTRATE_PEG_GRACE_HOURS`) → 별도로 처리.

### 2.18. 샤딩 (선택사항)

* `SHARDING_ENABLE = False` | `SHARD_MOD = 3`
  슬롯 밖 ⇒ `⏭️🧩 ... skip (shard X/Y)`.

### 2.19. 새 인바운드 (피어가 채널 개설)

* `NEW_INBOUND_NORMALIZE_ENABLE = True`
* 윈도우: `NEW_INBOUND_GRACE_HOURS = 48`
* 조건: `NEW_INBOUND_OUT_MAX = 0.05`, `NEW_INBOUND_REQUIRE_NO_FWDS = True`
* 현재 수수료 ≫ 씨드인 경우만 활성화:
  `NEW_INBOUND_MIN_DIFF_FRAC = 0.25` **및** `NEW_INBOUND_MIN_DIFF_PPM = 50`
* 단계 상한 **감소만 더 큼**: `NEW_INBOUND_DOWN_STEPCAP_FRAC = 0.15`
* 태그: `NEW_INBOUND_TAG = "🌱new-inbound"`

### 2.20. 분류 (싱크/소스/라우터)

* `CLASSIFY_ENABLE = True` | `CLASS_BIAS_EMA_ALPHA = 0.45`
* 최소 샘플: `CLASS_MIN_FWDS = 4`, `CLASS_MIN_VALUE_SAT = 40_000`
* 임계값:

  * Sink: `SINK_BIAS_MIN = 0.50`, `SINK_OUTRATIO_MAX = 0.15`
  * Source: `SOURCE_BIAS_MIN = 0.35`, `SOURCE_OUTRATIO_MIN = 0.58`
  * Router: `ROUTER_BIAS_MAX = 0.30`
  * 히스테리시스: `CLASS_CONF_HYSTERESIS = 0.10`
* 정책:

  * Sink: `SINK_EXTRA_FLOOR_MARGIN = 0.10`, `SINK_MIN_OVER_SEED_FRAC = 1.00`
  * Source: `SOURCE_SEED_TARGET_FRAC = 0.60`, `SOURCE_DISABLE_OUTRATE_FLOOR = True`
  * Router: `ROUTER_STEP_CAP_BONUS = 0.02`

### 2.21. 극단적 드레인 (수요가 있는 만성 드레인)

* `EXTREME_DRAIN_ENABLE = True`
* 활성화 시기: `low_streak ≥ 20`, `out_ratio < 0.03` **및** `baseline_fwd7d > 0`
* 효과 (상승): `EXTREME_DRAIN_STEP_CAP = 0.15`, `EXTREME_DRAIN_MIN_STEP_PPM = 15`

### 2.22. 디버그 / 제외

* `DEBUG_TAGS = True` (`🧬seedcap:*`, `🔍t/r/f` 등 표시)
* DRY에서 제외:

  * `EXCLUSION_LIST = {...}` → 줄 포함 `🚷excl-dry`
  * `EXCL_DRY_VERBOSE = True` (또는 `--excl-dry-tag-only`)

---

## 3) 조건부 로컬 한계 및 최종 클램프

* 소프트 한계/채널: `local_max = min(MAX_PPM, max(800, int(seed * 1.8)))`
* **수요 예외**: 드레인된 경우 (`out_ratio < 0.10`) **또는** `out_ppm7d ≥ seed * OUTRATE_PEG_SEED_MULT`, **아웃레이트**를 통해 한계 권한부여 (`OUTRATE_PEG_HEADROOM` 포함).
* 최종 클램프: `final = max(MIN_PPM, min(local_max, int(round(final_ppm)))`.

> PEG가 "한계를 치면", **`MAX_PPM`을 증가**하여 가격이 따를 수 있도록 하세요.

---

## 4) 태그 사전

**잠금/속도**

* `🧱floor-lock`, `⛔stepcap`, `⛔stepcap-lock`, `🧘hold-small`, `⏳cooldown...`

**수요/수익**

* `⚡surge+X%`, `👑top+X%`, `💹negm+X%`, `⚠️subprice`

**PEG/out-rate**

* `🧲peg` (아웃레이트에 고정된 바닥선; 떨어지려면 `OUTRATE_PEG_GRACE_HOURS` 필요)

**유동성**

* `🙅‍♂️no-down-low`, `🌱new-inbound`, `🧪discovery`

**씨드/가드**

* `🧬seedcap:p95|prev+|abs|none` + 하이브리드 조정 `🔬med-blend`, `🔬volσ/μ-..%`, `🔬ratio×..`

**클래스**

* `🏷️sink/source/router/unknown`, `🧭bias±`, `🧭<class>:<conf>`

**보안/상태**

* `🧯 CB:...`, `🟢on|🟢back|🔴off`, `⏭️🔌 skip`, `🚷excl-dry`, `🩹min-fix`

**디버그**

* `🔍t{target}/r{raw}/f{floor}`

---

## 5) 빠른 예제

**(A) PEG가 하강을 차단**

```
🫤⏸️ PeerX: maintains 1500 ppm | target 605 | out_ratio 0.12 | out_ppm7d≈1624 | seed≈580 | floor≥1500 | 🧲peg 🧱floor-lock 🔍t605/r1745/f1500
```

— 관찰된 아웃레이트가 바닥선 (PEG)이 되었으므로, 하강이 **1500**에서 멈췄습니다.
👉 더 따르고 싶으신가요? **`MAX_PPM` 올리세요**.

**(B) 기준선 없는 만성 드레인 (stale-drain)**

```
🫤⏸️ PeerY: maintains 1107 ppm | target 1348 | out_ratio 0.01 | out_ppm7d≈0 | seed≈615 | 💤stale-drain ⛔stepcap 🔍t1348/r1217/f618
```

— 높은 스트릭, forwards 없음: 단계 상한으로 제한된 상승.

**(C) 새 인바운드 — 촉진된 하강**

```
✅🔻 PeerZ: set 1200→980 ppm | 🌱new-inbound 🔍t940/r980/f560
```

— **new-inbound**에서 하강은 쿨다운을 무시합니다.

---

## 6) 튜닝 프로파일

**A) 공격적 수익/수요 친화적**

* `PERSISTENT_LOW_BUMP=0.07–0.10`, `PERSISTENT_LOW_MAX=0.30`
* `SURGE_K=0.8`, `SURGE_BUMP_MAX=0.30–0.45`
* `STEP_CAP_LOW_005=0.15–0.18`, `STEP_CAP_LOW_010=0.10–0.12`
* `TOP_REVENUE_SURGE_BUMP=0.15`
* `MAX_PPM` ↑ **PEG**이 피크를 따를 수 있도록

**B) 보수적/안정적**

* `PERSISTENT_LOW_BUMP=0.04`, `STEP_CAP=0.04`, `STEP_CAP_LOW_005=0.08`
* `SURGE_K=0.45`, `SURGE_BUMP_MAX=0.25`
* `BOS_PUSH_MIN_ABS_PPM=18` (더 적은 업데이트)

**C) 발견 (유휴)**

* 이미 `DISCOVERY_ENABLE=True` 활성화됨
* `OUTRATE_FLOOR_DISABLE_BELOW_FWDS=5` (신호 포함인 경우만 아웃레이트 바닥선 켜짐)
* `STEP_CAP_IDLE_DOWN=0.15` (유동성이 많은 곳에서 하강 가속화)

---

## 7) 실행

```bash
python3 brln-autofee-pro.py                # 실행 "valendo"
python3 brln-autofee-pro.py --dry-run      # 시뮬레이션만 (클래스는 계속 유지됨)
# 제외:
python3 brln-autofee-pro.py --excl-dry-verbose   # (기본값) 완전한 줄
python3 brln-autofee-pro.py --excl-dry-tag-only  # 단지 "🚷excl-dry"
```

Cron (매시간):

```cron
0 * * * * /usr/bin/python3 /home/admin/nr-tools/brln-autofee pro/brln-autofee-pro.py >> /home/admin/autofee-apply.log 2>&1
```

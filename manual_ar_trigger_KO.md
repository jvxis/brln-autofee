# "LNDg AR Trigger v2" 매뉴얼


## 무엇이고 왜 사용하나요?

이 스크립트는 **LNDg의 자동 리밸런스 (AR)**를 채널 단위로 자동화합니다: 아웃바운드/인바운드 **목표를 설정**하고, AR을 **히스테리시스**로 **온/오프 결정**, **7d rebal 비용** 존중, **클래스별 바이어스** 적용 (sink/router/source/unknown), 그리고 두 가지 유용한 기법을 가져옵니다:

* **fill-lock**: 채널을 채우는 중일 때 AR을 켜진 상태로 유지하고 목표에 도달할 때까지 목표를 잠급니다.
* **cap-lock**: 새로운 목표가 현재 *out_ratio*보다 작아질 때 (특히 **SINK**에서), **목표를 올려서** *out_ratio*와 동일하게 만들어 이미 채우기 위해 지출한 유동성을 "잃지" 않도록 합니다 (리밸런스의 출발점이 되지 않도록).

모두 **Telegram 메시지** (AR 온/오프 상태 포함) 및 감사 **로컬 로그**를 제공합니다.

---

## 흐름 개요

1. **데이터 수집** LNDg를 통한 채널 (용량, 잔액, 로컬/원격 수수료, AR 상태, 목표 등).
2. **글로벌 아웃바운드 계산** (채널당 목표의 기준).
3. **7d rebal 비용 읽기** LNDg의 SQLite (채널당 및 전역).
4. **AutoFee state/cache 로드** (채널 클래스, forwards 기준선, `bias_ema`, 온/오프 **쿨다운**).
5. **채널당 목표 정의** (out/in):

   * 글로벌 + **바이어스** (클래스 또는 `bias_ema`) + **수요 보너스** (forwards 기준선).
   * **10%**와 **90%** 사이의 안전 클램프, 최대 **글로벌보다 +5pp**.
   * 예외: **source**는 **5/95** 고정.
6. **게이트 적용**:

   * **price-gate** (가격 건전성 vs `ar_max_cost`);
   * **수익성** (L−R 마진 vs 7d 비용 + *safety*).
7. **히스테리시스 & 결정**:

   * **±5pp** 대역폭으로 온/오프;
   * **source = 항상 OFF** 정책 존중;
   * 채우는 중일 때 **fill-lock**;
   * 목표 감소가 채널을 출발점으로 만들 때 **cap-lock**.
8. **최소 쿨다운** ON/OFF 사이.
9. **채널 업데이트** (AR 및/또는 목표) 및 **Telegram 알림** (+ JSON 로그).

---

## 중요 파라미터 (파일)


### 연결 및 경로

* `TELEGRAM_TOKEN`, `CHATID` – 보고서를 보낼 위치.
* `LNDG_BASE_URL`, `username`, `password` – LNDg API.
* `DB_PATH` – LNDg의 SQLite (7d 비용용).
* `CACHE_PATH`, `STATE_PATH` – AutoFee *state* & 쿨다운.
* `AUTO_FEE_FILE`, `AUTO_FEE_PARAMS_CACHE` – AutoFee 원본 및 캐시.
* `LOG_PATH` – 로컬 로그 (JSON 라인당 하나).

### 한계 및 로직

* `HYSTERESIS_PP = 5` – 진동 없이 온/오프할 대역폭.
* `OUT_TARGET_MIN = 10`, `OUT_TARGET_MAX = 90` – 안전 우산.
* `REBAL_SAFETY = 1.05` 및 `BREAKEVEN_BUFFER = 0.03` – 7d 비용 여유.
* `AR_PRICE_BUFFER = 0.10` – price-gate: 원격을 +10%로 커버해야 합니다.
* `MIN_REBAL_VALUE_SAT = 400_000`, `MIN_REBAL_COUNT = 3` – 7d 비용의 **채널** 샘플이 충분한 경우에만 신뢰; 그 외에는 **글로벌** 사용.
* `MIN_DWELL_HOURS = 2` – ON/OFF 변경 쿨다운.
* `CLASS_BIAS` – 클래스별 바이어스 (분수 단위, 예: `+0.12`= +12pp).
* `BIAS_MAX_PP = 12` – 동적 바이어스의 한계 (`bias_ema`로부터).
* `BIAS_HARD_CLAMP_PP = 20` – 하드 보안 잠금 (pp).
* `EXCLUSION_LIST` – 무시할 채널 목록.
* `FORCE_SOURCE_LIST` – 특수한 경우 "source" 강제.

### 수요 보너스

`demand_bonus(baseline_fwds)` 추가:

* **+8pp** 기준선 ≥ 150인 경우;
* **+4pp** 기준선 ≥ 50인 경우;
* **+0pp** 그 외.

---

## 목표가 계산되는 방식

**공식 (단순화됨):**

```
out_target = clamp(
  min(global_out + bias + demand_bonus, global_out + 0.05),
  0.10, 0.90
)
```

* **bias**는 AutoFee의 `bias_ema` → **pp**로 매핑됨 (±12pp 기본값; 하드 클램프 ±20pp).
  `bias_ema`가 없으면 `CLASS_BIAS`로 떨어짐 (**unknown** = 기본값 0pp 포함).
* **source**는 이 계산을 무시하고 **5/95** 고정 (**정책: AR 항상 OFF**).

**cap-lock**: 현재 `out_ratio` > 새로운 `out_target`인 경우, **목표**를 `ceil(out_ratio*100)`으로 올려 "뚱뚱한" SINK가 목표를 줄인 후 즉시 rebal의 **출발점**이 되지 않도록 합니다.

**fill-lock**: AR이 **ON**이고 `out_ratio` < `out_target`인 경우, **ON을 유지**하고 목표에 도달할 때까지 목표를 잠급니다 (채우는 동안 price-gate/비용 무시).

---

## 온/오프 결정 (히스테리시스)

* **ON → OFF** 다음인 경우:

  * `out_ratio ≥ target + 5pp` **및** 수익 양호, **또는**
  * **수익성 없음** (마진 < 조정된 7d 비용).
* **OFF → ON** 다음인 경우:

  * `out_ratio ≤ LOW_OUTBOUND_THRESH` (AutoFee에서) **및** 수익성 있음.
* 그 외: **상태 유지**, **2h 쿨다운** 존중.

> **source**: 절대 켜지지 않음 (정책).

---

## 게이트 및 공식

### Price-gate

> *"만약 내가 `ar_max_cost`%까지 지출한다면, 내 로컬 수수료가 여전히 원격을 여유로 커버하나?"*

```
local_ppm * (ar_max_cost/100) ≥ remote_ppm * (1 + AR_PRICE_BUFFER)
```

기본값: **AR_PRICE_BUFFER = 10%**.

### 수익성 (7d 비용 대비)

```
margin_ppm = max(0, local_ppm - remote_ppm)
need_ppm   = ceil( cost_7d * REBAL_SAFETY ) * (1 + BREAKEVEN_BUFFER)
profit OK  = margin_ppm ≥ need_ppm
```

* 샘플 ≥ `MIN_REBAL_VALUE_SAT` 및 `MIN_REBAL_COUNT`인 경우 **채널당 비용** 사용; 그 외 **글로벌**.

---

## 채널 클래스 (동작)

* **sink**: 양수 바이어스 (예: +12pp), 더 공격적으로 채움.
* **router**: 중립.
* **source**: 고정 목표 **5/95** 및 **AR 항상 OFF**.
* **unknown**: **중립** 바이어스 (0pp)로 처리되지만, 수요 보너스 및 나머지 로직을 정상적으로 받습니다.

> **휴리스틱 "source처럼 보임"**: `local_ppm == 0` **또는** (`out_ratio ≥ 0.50` 및 `local_ppm ≤ remote_ppm/4`)인 경우, **source** 강제 (`FORCE_SOURCE_LIST`로 오버라이드 가능).

---

## Telegram의 메시지 (읽는 방법)

각 업데이트는 다음과 같은 하나 이상의 블록을 가져옵니다:

```
✅ 🛠️ TARGET Alias (chan_id)
• 🔌 AR: ON/OFF                 ← 변경 후 AR 상태
• 📊 out_ratio 0.28 • 💱 fee L/R 600/20ppm • 🧮 ar_max_cost 80%
• 🎯 target out/in 29/71% (fill-lock | 🧷 cap-lock | source 5/95)
• 🔎 reason: ... price-gate ... | ... cost_7d ... | ... hysteresis ...
```

**유용한 태그**:

* **(fill-lock)** → 채우는 중; 목표를 칠 때까지 가격/비용 게이트 무시.
* **(🧷 cap-lock)** → 목표가 현재 *out_ratio*로 올라옴 (유동성 보존).
* **(source 5/95)** → 출발지 채널의 특수 정책 (AR OFF).

보고서의 헤더도 표시합니다:

```
⚡ LNDg AR Trigger v2 | chans=NN | global_out=0.24 | rebal7d≈485ppm | changes=53
```

* `changes` = 이 실행에서 적용된 업데이트/목표 수.

---

## 로컬 로그 (감사)

각 작업은 `LOG_PATH`에 JSON을 생성합니다, 예:

* `type: "update"` – AR 및/또는 목표를 변경한 경우.
* `type: "targets_only"` – 목표만 조정한 경우.
* 유용한 필드: `cid`, `alias`, `out_ratio`, `local_ppm`, `remote_ppm`, `ar_max_cost`, `targets`, `price_gate_ok`, `profitable`, `class`, `baseline`, `fill_lock`, `cap_lock`, `cost_ppm`, `vol_sat_7d`, `count_7d` 등.

---

## 실행 & 스케줄링

* 수동 실행: `python3 lndg_ar_trigger.py`
* **Cron** 일반적 (매시간):

  ```
  * */1 * * * /usr/bin/python3 /path/lndg_ar_trigger.py >> /var/log/lndg_ar_trigger.log 2>&1
  ```
* **DB**, **LNDg API** 및 **경로**에 대한 cron 사용자 액세스를 보장하세요.

---

## 모범 사례

* **Exclusion list**: 스크립트가 건드리지 않을 채널에 사용.
* **모니터링** *changes* 및 **rebal7d≈**; 큰 변동은 노이즈를 나타낼 수 있습니다.
* **샘플**: 채널당 7d 비용에 최소 샘플이 없으면 신뢰하지 마세요 – 스크립트는 자동으로 **글로벌** 비용으로 돌아갑니다.

---

## 일반적 오류 & 빠른 해결

### "cannot access local variable 'ar_state_txt'…"

이미 수정했습니다. 규칙은: **`update_channel()` 직후 `ar_state_after/ar_state_txt` 정의**, 페이로드에 `auto_rebalance`이 있는지 여부와 상관없음.

### "PUT/PATCH … 400/405/500"

* LNDg 자격 증명 및 URL 확인.
* 일부 엔드포인트는 **PATCH**만 허용; 스크립트는 이미 PATCH로 폴백합니다.

### "No changes."

좋습니다! 모든 채널이 히스테리시스 내에 있고 트리거가 없는 경우 발생할 수 있습니다.

---

## FAQ

**1) 왜 때때로 드레인되었을 때 켜지지 않나요?**
**price-gate** **및** 수익성 (마진 ≥ 7d 비용 + 여유)을 통과해야 하기 때문입니다. 하나라도 실패하면 OFF입니다.

**2) 왜 목표가 혼자 올라갔나요?**
**cap-lock**. 새로 채워진 SINK가 목표 방법이 변경되어 목표를 줄이자마자 rebal **출발점**이 되지 않도록 합니다.

**3) 채널을 "source"로 강제하고 싶으면?**
`FORCE_SOURCE_LIST`에 포함 **또는** 휴리스틱 인식 (local_ppm=0 등). "source"는 **AR OFF**와 **5/95** 목표를 유지합니다.

**4) "unknown" 클래스의 경우?**
**중립** 바이어스 (0pp)로 처리되지만, 수요 보너스 및 나머지 로직을 정상적으로 받습니다.

---

## 용어집

* **out_ratio**: **로컬/용량** 채널의 잔액 분수.
* **target out/in**: 아웃바운드/인바운드의 **%** 목표.
* **price-gate**: `ar_max_cost`까지 지출해도 로컬 수수료가 원격을 여유로 커버한다는 보장.
* **7d 비용**: 지난 7일간의 유효 rebal 비용 (ppm).
* **fill-lock**: 목표를 칠 때까지 채우는 동안 AR을 해제하지 않습니다.
* **cap-lock**: 목표를 채널이 이미 가진 아웃바운드 아래로 줄이지 않습니다.

---

## 최종 팁

* **`BIAS_MAX_PP`** 조정: `bias_ema`이 목표에 더/덜 영향을 주도록 원하면.
* **`AR_PRICE_BUFFER`** 더 높음 = AR 켜기 위해 더 보수적.
* **`BREAKEVEN_BUFFER`** "breakeven" 위로 얼마나 높여야 **수익성**으로 간주할지 제어 (flapping 회피).

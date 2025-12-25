# ⚙️ AI 파라미터 튜너 — 라우터 노드 운영자용 매뉴얼

> 🧠 **목표:**
> *AI Param Tuner*는 실제 노드 동작 기반으로 **AutoFee** 스크립트의 파라미터를 자동으로 조정하는 지능형 어시스턴트입니다.
> 지난 7일간의 메트릭을 분석하고 **수익과 아웃바운드 활용을 극대화**하기 위해 점진적인 조정을 제안하면서 네트워크를 건강하게 유지하고 과민 반응을 피합니다.

---

## 📊 1. AI 튜너 동작 원리

튜너는 매일 (또는 `--dry-run`으로 수동으로) 실행되며 5단계를 거칩니다:

1. **KPI 수집 (7일):**

   * 수익, 라우팅된 볼륨, rebal 비용.
2. **증상 읽기:**

   * AutoFee 로그 (`autofee-apply.log`)를 분석하여 채널 동작 이해.
3. **트렌드 평가:**

   * 노드가 좋은 스트릭 (*good streak*) 또는 나쁜 스트릭 (*bad streak*)에 있는지 측정.
4. **조정 계산:**

   * 바닥선, 쿨다운, 감도를 강화하거나 완화할지 결정.
5. **제어된 적용:**

   * **일일 예산** 및 **최소 쿨다운** 내에 있는 경우만 변경 사항을 저장합니다.

---

## 🧩 2. 운영자가 관찰해야 할 사항

작동 중에 두 가지 중요한 정보 세트가 있습니다:

### 🧮 KPI (7일 메트릭)

| 메트릭            | 의미                              | 해석                     |
| ------------------ | ---------------------------------------- | --------------------------------- |
| `out_ppm7d`        | 노드에서 나가는 라우트의 평균 가격     | 높음 = 비쌈 / 낮음 = 경쟁력 있음 |
| `rebal_cost_ppm7d` | rebal의 평균 비용               | 높음 = 많이 지출 중        |
| `profit_sat`       | 지난 7일간의 순수익 (sats)        | 음수 = 비효율적 노드       |
| `profit_ppm_est`   | 추정 마진 (ppm_out - ppm_rebal)    | 이상적인 **0에서 200 ppm**       |
| `margin_ppm`       | out과 rebal 사이의 절대 차이     | 0 미만 = 손실            |

➡️ **목표:** `profit_ppm_est ≥ 0` 유지 및 **매일 >80%** 아웃바운드 사용.

---

### 🩺 증상 (로그에서 감지)

| 이모지 | 이름          | 의미                          | 권장 조치                         |
| ----- | ------------- | ------------------------------------ | ------------------------------------- |
| 🧱    | `floor_lock`  | 높은 바닥선으로 많은 채널이 잠김 | `REBAL_FLOOR_MARGIN` 감소          |
| 🙅‍♂️ | `no_down_low` | 어떤 채널도 수수료를 낮추지 않음          | `SURGE_K` 및 `SURGE_BUMP_MAX` 증가 |
| 🧘    | `hold_small`  | 작은 채널들이 "갇혀" 있음             | `BOS_PUSH_MIN_ABS_PPM` 감소        |
| 🧯    | `cb_trigger`  | 서킷 브레이커 활성화              | `COOLDOWN_HOURS_DOWN` 증가        |
| 🧪    | `discovery`   | 많은 채널이 가격 테스트 중         | `OUTRATE_FLOOR_FACTOR` 올리지 않기   |

---

## ⚙️ 3. 주요 설정

### 🧾 기본값 (Defaults)

이 값들은 안전하며 대부분의 **라우터 노드**에서 잘 작동합니다:

| 파라미터               | 설명                                    | 기본값 | 예상 효과                     |
| ----------------------- | -------------------------------------------- | ------------- | ----------------------------------- |
| `STEP_CAP`              | 라운드당 최대 수수료 조정 단계    | `0.05`        | 급격한 점프 방지                |
| `SURGE_K`               | 수요에 대한 민감도                      | `0.50`        | 0.3 = 느림 / 0.7 = 반응형         |
| `SURGE_BUMP_MAX`        | 임시 증가의 최대 한계            | `0.20`        | 과도한 상승 방지           |
| `PERSISTENT_LOW_BUMP`   | 채널이 만성적으로 저가인 경우 증분 | `0.05`        | 최소 활성 수수료 유지            |
| `PERSISTENT_LOW_MAX`    | 지속적인 범프의 최대 한계            | `0.20`        | 낮음에서의 과도한 현상 제한              |
| `REBAL_FLOOR_MARGIN`    | rebal의 최소 ROI 마진                | `0.10`        | rebal 비용 회피        |
| `REVFLOOR_MIN_PPM_ABS`  | 수수료의 절대 최소 바닥선                 | `500`         | 비용 이하 라우트 회피             |
| `OUTRATE_FLOOR_FACTOR`  | 관찰된 가격에 대한 승수        | `1.10`        | 평균 마진 증가                |
| `BOS_PUSH_MIN_ABS_PPM`  | BOS 푸시의 최소 PPM                  | `15`          | 너무 저가 채널 푸시 회피 |
| `BOS_PUSH_MIN_REL_FRAC` | 푸시의 최소 상대 비율             | `0.04`        | 비례성 유지            |
| `COOLDOWN_HOURS_DOWN`   | 수수료 인하 사이의 최소 시간            | `6`h          | 빠른 변동 회피            |
| `COOLDOWN_HOURS_UP`     | 수수료 인상 사이의 최소 시간           | `3`h          | 시장이 반응할 시간 제공        |
| `REBAL_BLEND_LAMBDA`    | 계산에서 rebal 비용에 주어진 가중치       | `0.30`        | 가격 vs. 비용 균형           |
| `NEG_MARGIN_SURGE_BUMP` | 마진이 음수인 경우 추가 증분          | `0.05`        | 가벼운 손실에 반응               |

---

### 🔒 한계 (LIMITS)

| 파라미터               | 최소 | 최대 | 의견                             |
| ----------------------- | ---- | ---- | -------------------------------------- |
| `STEP_CAP`              | 0.02 | 0.15 | 클수록 = 더 공격적                 |
| `SURGE_K`               | 0.20 | 0.90 | 높음 = 더 민감                   |
| `SURGE_BUMP_MAX`        | 0.10 | 0.50 | 노드가 "차단"되는 경우만 올리세요    |
| `PERSISTENT_LOW_BUMP`   | 0.03 | 0.12 | 영원한 범프 회피                      |
| `PERSISTENT_LOW_MAX`    | 0.10 | 0.40 | 과도한 가격 대비 보안             |
| `REBAL_FLOOR_MARGIN`    | 0.05 | 0.30 | rebal당 최소 ROI                   |
| `REVFLOOR_MIN_PPM_ABS`  | 100  | 700  | 저가 대비 보호             |
| `OUTRATE_FLOOR_FACTOR`  | 0.75 | 1.35 | 글로벌 민감도 범위 정의 |
| `BOS_PUSH_MIN_ABS_PPM`  | 5    | 20   | 최소 푸시 절대값                |
| `BOS_PUSH_MIN_REL_FRAC` | 0.01 | 0.06 | 푸시의 미세 조정                    |
| `COOLDOWN_HOURS_DOWN`   | 3    | 12   | 감소 사이의 최소 대기            |
| `COOLDOWN_HOURS_UP`     | 1    | 8    | 증가 사이의 최소 대기            |
| `REBAL_BLEND_LAMBDA`    | 0.0  | 1.0  | rebal 비용의 가중치                 |
| `NEG_MARGIN_SURGE_BUMP` | 0.05 | 0.20 | 손실에 대한 반응 증분         |

---

### ⏱️ 쿨다운 및 히스테리시스

| 설정                 | 값 | 기능                                    |
| ---------------------------- | ----- | ----------------------------------------- |
| `MIN_HOURS_BETWEEN_CHANGES`  | 4     | 유효한 실행 사이의 최소 시간      |
| `REQUIRED_BAD_STREAK`        | 2     | 강화하기 위한 연속 음수 라운드 |
| `REQUIRED_GOOD_STREAK`       | 2     | 완화하기 위한 연속 양수 라운드 |
| `RELIEF_HYST_NEG_MARGIN_MIN` | 150   | 마진 ≤ -150 ppm인 경우 즉시 완화      |
| `RELIEF_HYST_FLOORLOCK_MIN`  | 120   | 높은 Floor-locks가 완화를 트리거         |
| `RELIEF_HYST_WINDOWS`        | 3     | 완화를 허용하기 위한 연속 윈도우 |

---

### 💰 일일 예산 (DAILY_CHANGE_BUDGET)

각 파라미터가 **매일** 얼마나 변할 수 있는지 제어하여 변동성을 제한합니다.

| 파라미터               | 최대 일일 변동 | 설명                        |
| ----------------------- | -------------------- | --------------------------------- |
| `OUTRATE_FLOOR_FACTOR`  | 0.05                 | 작은 일일 변동        |
| `REVFLOOR_MIN_PPM_ABS`  | 60 ppm               | 바닥선은 매일 최대 60 ppm 올라갈 수 있음    |
| `REBAL_FLOOR_MARGIN`    | 0.08                 | 최대 일일 ROI 조정       |
| `STEP_CAP`              | 0.03                 | 수수료 가속 제한        |
| `SURGE_K`               | 0.15                 | 서지의 일일 민감도     |
| `SURGE_BUMP_MAX`        | 0.08                 | 일일 범프 한계               |
| `PERSISTENT_LOW_BUMP`   | 0.02                 | 낮음에 대한 점진적 증가         |
| `PERSISTENT_LOW_MAX`    | 0.06                 | 지속성의 제한된 확장 |
| `BOS_PUSH_MIN_ABS_PPM`  | 6                    | 일일 제어된 푸시           |
| `BOS_PUSH_MIN_REL_FRAC` | 0.01                 | 일일 푸시 비율          |
| `COOLDOWN_HOURS_UP`     | 1                    | 매일 최대 1시간 감소할 수 있음           |
| `COOLDOWN_HOURS_DOWN`   | 2                    | 매일 최대 2시간 감소할 수 있음           |
| `REBAL_BLEND_LAMBDA`    | 0.20                 | 가중된 리밸런스               |
| `NEG_MARGIN_SURGE_BUMP` | 0.03                 | 부드러운 보정 증분      |

---

## 📘 4. 실전 설정 방법

1. **첫 번째 실행:**

   ```bash
   python3 ai_param_tuner.py --dry-run --telegram
   ```

   → Telegram의 메시지가 합리적인지 확인하세요 (말도 안 되는 값 없음).

2. **프로덕션 운영 (cron):**

   ```bash
   0 */1 * * * /usr/bin/python3 /home/admin/nr-tools/brln-autofee pro/ai_param_tuner.py
   ```

   → 이상적으로 autofee 일일 빈도와 함께.

3. **동작 모니터링:**

   * Telegram 로그에서 `profit_ppm_est` 및 `floor_lock` 관찰.
   * 노드가 가벼운 수익과 안정적 라우트를 유지하면 → 튜너가 올바르게 조정됨.

4. **상태 재설정:**

   * 학습을 다시 시작하려면 삭제:

     ```bash
     rm -f ~/.cache/auto_fee_state.json
     rm -f /home/admin/nr-tools/brln-autofee pro/autofee_meta.json
     ```
   * 스크립트가 자동으로 파일을 다시 만듭니다.

---

## 🧠 5. 고급 팁

* **발견이 높음 (🧪 > 50):**
  시장이 불안정함을 나타냄 — `OUTRATE_FLOOR_FACTOR` 올리지 마세요.

* **높은 Floor-lock과 낮은 수익:**
  튜너는 *계획 A*에 진입하여 바닥선과 ROI를 강화합니다.

* **높은 수익 + 높은 발견:**
  *good_streak_discovery로 완화 활성화*, 볼륨 확장을 위해 바닥선과 쿨다운 감소.

* **반복된 CB 트리거 (🧯):**
  포화 신호 — `STEP_CAP` 감소 또는 `COOLDOWN_HOURS_DOWN` 증가.

---

## 🧾 6. 실무 요약: 라우터의 핵심 파라미터

| 목표               | 주 파라미터             | 이상적 방향                       |
| ---------------------- | ------------------------------- | ----------------------------------- |
| 볼륨 증가        | ↓ `OUTRATE_FLOOR_FACTOR`        | 더 저가 라우트                  |
| 수익 보호         | ↑ `REVFLOOR_MIN_PPM_ABS`        | 더 높은 절대 바닥선             |
| rebal 비용 감소 | ↑ `REBAL_FLOOR_MARGIN`          | 수익이 가치 있을 때만 rebal |
| 조정 속도 가속       | ↓ `COOLDOWN_HOURS_UP` / `DOWN`  | 더 빠른 반응                |
| 일일 소음 감소   | ↓ budgets (DAILY_CHANGE_BUDGET) | 더 많은 안정성                   |

---

## 🧩 7. 결론

**AI Param Tuner**는 라우터 노드를 위한 *적응형 지능 자동조종*입니다.
노드를 다음과 같이 하기 위해 **수익 및 이동 패턴을 학습**하고 휴리스틱을 적용합니다:

✅ 매일 모든 아웃바운드 사용,
✅ 양수 마진 유지, 및
✅ 리밸런스에 과도한 지출 회피.

> 💬 **권장사항:**
> 실제 작성을 해제하기 전에 항상 처음 3개 라운드에서 `--dry-run`으로 실행하고 Telegram의 메시지를 확인하세요.

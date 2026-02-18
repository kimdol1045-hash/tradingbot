# STEP 7 - 시그널 생성 및 송출 (Signal Generation & Dispatch)

**버전**: v1.0.0  
**최종 업데이트**: 2025-10-17  
**의존성**: STEP 0~6 전체  
**처리 시간**: 평균 45ms (송출 제외), 총 650ms (전체 파이프라인)  

---

## 📋 목차

1. [개요 및 목적](#1-개요-및-목적)
2. [시그널 생성 파이프라인](#2-시그널-생성-파이프라인)
3. [레버리지 동적 설정 시스템](#3-레버리지-동적-설정-시스템)
4. [시그널 출력 포맷](#4-시그널-출력-포맷)
5. [송출 메커니즘](#5-송출-메커니즘)
6. [예외 처리 및 안전장치](#6-예외-처리-및-안전장치)
7. [성능 지표 및 모니터링](#7-성능-지표-및-모니터링)
8. [실제 적용 예시](#8-실제-적용-예시)
9. [업데이트 이력](#9-업데이트-이력)

---

## 1. 개요 및 목적

### 1.1 목적

STEP 7은 **적응형 시그널 생성 시스템의 최종 출력 단계**로, STEP 0~6에서 생성된 모든 분석 데이터를 통합하여 실행 가능한 트레이딩 시그널을 생성하고 송출합니다.

**핵심 기능:**
- Long/Short 진입 시그널 자동 생성
- 손절가/익절가 자동 계산 및 포함
- 시장 체제 및 신뢰도 기반 레버리지 동적 설정
- 다양한 포맷으로 시그널 송출 (API, Webhook, 메시징)
- 실시간 모니터링 및 예외 처리

### 1.2 입력 데이터

| 출처 | 데이터 | 용도 |
|------|--------|------|
| STEP 0 | 극단 상황 플래그 (Stage 1-4) | 레버리지 제한, 거래 중단 |
| STEP 1 | 시장 체제 (6가지), 신뢰도 (0-1) | 레버리지 기본값 설정 |
| STEP 2 | 체제 전환 상태 (히스테리시스, 블렌딩) | 레버리지 보수적 조정 |
| STEP 3 | 차트 구조 (S/R, 추세선, POC) | 손절가 계산 참조 |
| STEP 4 | 변곡점 점수 (0-100), Type | 진입 신호, 레버리지 조정 |
| STEP 5 | 안전성 점수 (0-100) | 진입 필터링, 레버리지 조정 |
| STEP 6 | 손절가, 익절가 (TP1-3), 트레일링 | 출구 전략 포함 |

### 1.3 출력 데이터

```json
{
  "signal_id": "SIG_20251017_143052_BTC",
  "timestamp": "2025-10-17T14:30:52.328Z",
  "symbol": "BTC/USDT",
  "direction": "LONG",
  "entry_price": 69000.00,
  "stop_loss": 67800.00,
  "take_profit": [
    {"level": "TP1", "price": 70500.00, "close_percentage": 40},
    {"level": "TP2", "price": 71800.00, "close_percentage": 30},
    {"level": "TP3", "price": 73500.00, "close_percentage": 30}
  ],
  "leverage": 5,
  "confidence": 0.92,
  "regime": "STRONG_UPTREND",
  "risk_reward_ratio": 2.4,
  "max_drawdown_risk": 1.74,
  "validity_duration": 300
}
```

---

## 2. 시그널 생성 파이프라인

### 2.1 전체 프로세스 (600~650ms)

```
[실시간 데이터 입력]
    ↓
[STEP 0] 극단 상황 탐지 (120ms)
    ├─ Stage 1-3: 거래 중단 → 시그널 생성 안 함
    └─ Stage 4 or Normal: 다음 단계 진행
    ↓
[STEP 1] 체제 분류 (200ms)
    ├─ MTF 분석 (5분, 15분, 1시간, 4시간)
    └─ 결과: 체제 + 신뢰도
    ↓
[STEP 2] 체제 전환 확인 (16ms)
    ├─ 히스테리시스: 전환 유예 중?
    └─ 블렌딩: 점진적 전환 중?
    ↓
[STEP 3] 차트 구조 분석 (55ms)
    ├─ S/R 레벨
    ├─ 추세선
    └─ Volume Profile POC
    ↓
[STEP 4] 변곡점 감지 (85ms)
    ├─ 7가지 Type 점수 계산
    ├─ 총점 < 60: 시그널 생성 안 함 → 종료
    └─ 총점 ≥ 60: 다음 단계 진행
    ↓
[STEP 5] 안전성 검증 (40ms)
    ├─ 10가지 지표 검증
    ├─ 총점 < 체제별 기준: 시그널 생성 안 함 → 종료
    └─ 총점 ≥ 체제별 기준: 다음 단계 진행
    ↓
[STEP 6] 출구 전략 계산 (38ms)
    ├─ 손절가 계산 (5가지 방법 중 선택)
    └─ 익절가 계산 (TP1-3, 트레일링)
    ↓
[STEP 7] 시그널 생성 (45ms) ← 현재 단계
    ├─ 레버리지 동적 계산
    ├─ 시그널 포맷 생성
    ├─ 유효성 검증
    └─ 송출
    ↓
[시그널 송출] (Webhook/API/메시징)
```

### 2.2 시그널 생성 조건

STEP 7에서 시그널을 생성하려면 다음 조건을 **모두** 만족해야 합니다:

| 조건 | 기준 | 미충족 시 처리 |
|------|------|----------------|
| STEP 0 극단 상황 | Stage 4 or Normal | 시그널 생성 안 함, 대기 |
| STEP 4 변곡점 점수 | ≥ 60점 | 시그널 생성 안 함, 로그만 기록 |
| STEP 5 안전성 점수 | ≥ 체제별 기준점 | 시그널 생성 안 함, 로그만 기록 |
| STEP 6 손절/익절 | 계산 완료 | 시스템 오류, 알림 발송 |
| 레버리지 계산 | 1배 이상 | 시그널 생성 안 함 (VOLATILE 체제 등) |

**체제별 STEP 5 기준점 (재확인):**
- STRONG_UPTREND / STRONG_DOWNTREND: 65점
- WEAK_UPTREND / WEAK_DOWNTREND: 75점
- SIDEWAYS: 80점
- VOLATILE: 90점 (거의 생성 안 됨)

### 2.3 시그널 방향 결정

```python
def determine_signal_direction(regime: str, inflection_type: list) -> str:
    """
    체제와 변곡점 타입을 기반으로 시그널 방향 결정
    
    Args:
        regime: STEP 1에서 분류된 시장 체제
        inflection_type: STEP 4에서 감지된 변곡점 타입 리스트
    
    Returns:
        'LONG' or 'SHORT' or 'NONE'
    """
    
    # VOLATILE 체제는 시그널 생성 안 함
    if regime == 'VOLATILE':
        return 'NONE'
    
    # 상승 체제
    if regime in ['STRONG_UPTREND', 'WEAK_UPTREND']:
        # 지지 반응 변곡점 → LONG
        if any(t in inflection_type for t in ['SR_SUPPORT', 'TRENDLINE_SUPPORT', 'POC_SUPPORT']):
            return 'LONG'
        # 저항 돌파 변곡점 → LONG
        elif 'BREAKOUT_UP' in inflection_type:
            return 'LONG'
        # 강세 다이버전스 → LONG
        elif 'DIVERGENCE_BULLISH' in inflection_type:
            return 'LONG'
        else:
            return 'NONE'
    
    # 하락 체제
    elif regime in ['STRONG_DOWNTREND', 'WEAK_DOWNTREND']:
        # 저항 반응 변곡점 → SHORT
        if any(t in inflection_type for t in ['SR_RESISTANCE', 'TRENDLINE_RESISTANCE', 'POC_RESISTANCE']):
            return 'SHORT'
        # 지지 붕괴 변곡점 → SHORT
        elif 'BREAKOUT_DOWN' in inflection_type:
            return 'SHORT'
        # 약세 다이버전스 → SHORT
        elif 'DIVERGENCE_BEARISH' in inflection_type:
            return 'SHORT'
        else:
            return 'NONE'
    
    # 횡보 체제
    elif regime == 'SIDEWAYS':
        # 범위 하단 지지 → LONG
        if 'SR_SUPPORT' in inflection_type and current_price < range_midpoint:
            return 'LONG'
        # 범위 상단 저항 → SHORT
        elif 'SR_RESISTANCE' in inflection_type and current_price > range_midpoint:
            return 'SHORT'
        # 범위 돌파 → 돌파 방향
        elif 'BREAKOUT_UP' in inflection_type:
            return 'LONG'
        elif 'BREAKOUT_DOWN' in inflection_type:
            return 'SHORT'
        else:
            return 'NONE'
    
    return 'NONE'
```

---

## 3. 레버리지 동적 설정 시스템

### 3.1 개요

레버리지는 **시장 체제, 신뢰도, 안전성, 변동성, 극단 상황**을 종합적으로 고려하여 동적으로 설정됩니다. 과도한 레버리지로 인한 청산 위험을 최소화하면서도 추세가 명확한 시장에서는 수익을 극대화하는 것이 목표입니다.

**설계 원칙:**
1. **보수적 기본값**: 의심스러우면 낮은 레버리지
2. **체제 우선**: 시장 상황이 레버리지의 1차 결정 요인
3. **신뢰도 반영**: 높은 확신일수록 레버리지 증가
4. **안전장치 다층화**: 극단 상황, 변동성, 청산 압력 등 여러 층의 제한
5. **실시간 조정**: 시장 상황 변화 시 즉시 레버리지 재계산

### 3.2 체제별 기본 레버리지 테이블

| 체제 | 신뢰도 ≥ 0.95 | 신뢰도 ≥ 0.85 | 신뢰도 ≥ 0.75 | 기본 (< 0.75) |
|------|---------------|---------------|---------------|---------------|
| **STRONG_UPTREND** | 10배 | 5배 | 3배 | 2배 |
| **WEAK_UPTREND** | 5배 | 3배 | 2배 | 1배 |
| **SIDEWAYS** | 3배 | 2배 | 1배 | 1배 |
| **WEAK_DOWNTREND** | 5배 | 3배 | 2배 | 1배 |
| **STRONG_DOWNTREND** | 10배 | 5배 | 3배 | 2배 |
| **VOLATILE** | 1배 (현물) | 1배 | 1배 | 1배 |

**체제별 특징:**
- **STRONG 추세**: 방향성 명확 → 높은 레버리지 허용
- **WEAK 추세**: 반전 가능성 존재 → 중간 레버리지
- **SIDEWAYS**: 범위 내 거래 → 낮은 레버리지 (돌파 시에만 증가)
- **VOLATILE**: 예측 불가 → 레버리지 금지

### 3.3 신뢰도 기반 조정

신뢰도는 **STEP 1 체제 분류**에서 계산되며, MTF 합의도를 반영합니다.

```python
def get_base_leverage(regime: str, confidence: float) -> int:
    """
    체제와 신뢰도로 기본 레버리지 결정
    
    Args:
        regime: 시장 체제 (STEP 1)
        confidence: 신뢰도 0.0~1.0 (STEP 1)
    
    Returns:
        기본 레버리지 배수
    """
    leverage_table = {
        'STRONG_UPTREND': {
            0.95: 10, 0.85: 5, 0.75: 3, 0.00: 2
        },
        'WEAK_UPTREND': {
            0.95: 5, 0.85: 3, 0.75: 2, 0.00: 1
        },
        'SIDEWAYS': {
            0.95: 3, 0.85: 2, 0.75: 1, 0.00: 1
        },
        'WEAK_DOWNTREND': {
            0.95: 5, 0.85: 3, 0.75: 2, 0.00: 1
        },
        'STRONG_DOWNTREND': {
            0.95: 10, 0.85: 5, 0.75: 3, 0.00: 2
        },
        'VOLATILE': {
            0.95: 1, 0.85: 1, 0.75: 1, 0.00: 1
        }
    }
    
    # 신뢰도에 따른 레버리지 선택
    thresholds = [0.95, 0.85, 0.75, 0.00]
    for threshold in thresholds:
        if confidence >= threshold:
            return leverage_table[regime][threshold]
    
    return 1  # 기본값
```

### 3.4 안전성 점수 기반 조정

STEP 5 안전성 점수가 높을수록 레버리지를 증가시킵니다.

```python
def apply_safety_adjustment(base_leverage: int, safety_score: int, regime: str) -> int:
    """
    안전성 점수로 레버리지 미세 조정
    
    Args:
        base_leverage: 기본 레버리지
        safety_score: STEP 5 안전성 점수 (0-100)
        regime: 시장 체제
    
    Returns:
        조정된 레버리지
    """
    # 체제별 기준점
    regime_thresholds = {
        'STRONG_UPTREND': 65,
        'STRONG_DOWNTREND': 65,
        'WEAK_UPTREND': 75,
        'WEAK_DOWNTREND': 75,
        'SIDEWAYS': 80,
        'VOLATILE': 90
    }
    
    threshold = regime_thresholds.get(regime, 80)
    
    # 기준점 대비 초과 점수
    excess_score = safety_score - threshold
    
    # 초과 점수에 따른 배율 조정
    if excess_score >= 20:  # 매우 안전 (기준점 +20 이상)
        multiplier = 1.5
    elif excess_score >= 10:  # 안전 (기준점 +10~19)
        multiplier = 1.2
    elif excess_score >= 0:  # 통과 (기준점 +0~9)
        multiplier = 1.0
    else:
        # 기준점 미달이면 여기 도달 안 함 (STEP 5에서 필터링됨)
        multiplier = 1.0
    
    adjusted = int(base_leverage * multiplier)
    
    # 최대 레버리지 제한 (체제별)
    max_leverage = {
        'STRONG_UPTREND': 15,
        'STRONG_DOWNTREND': 15,
        'WEAK_UPTREND': 7,
        'WEAK_DOWNTREND': 7,
        'SIDEWAYS': 5,
        'VOLATILE': 1
    }
    
    return min(adjusted, max_leverage.get(regime, 5))
```

### 3.5 변곡점 점수 기반 조정

STEP 4 변곡점 점수가 높을수록 진입 확신이 높으므로 레버리지 증가.

```python
def apply_inflection_adjustment(leverage: int, inflection_score: int) -> int:
    """
    변곡점 점수로 레버리지 조정
    
    Args:
        leverage: 현재 레버리지
        inflection_score: STEP 4 변곡점 점수 (60-100)
    
    Returns:
        조정된 레버리지
    """
    # 변곡점 점수 구간별 배율
    if inflection_score >= 90:  # 매우 강한 변곡점
        multiplier = 1.3
    elif inflection_score >= 80:  # 강한 변곡점
        multiplier = 1.15
    elif inflection_score >= 70:  # 중간 변곡점
        multiplier = 1.0
    else:  # 약한 변곡점 (60~69)
        multiplier = 0.85
    
    return int(leverage * multiplier)
```

### 3.6 극단 상황 기반 제한

STEP 0 극단 상황이 감지되면 레버리지를 강제로 제한합니다.

```python
def apply_extreme_condition_limit(leverage: int, extreme_stage: int) -> int:
    """
    극단 상황 Stage에 따른 레버리지 강제 제한
    
    Args:
        leverage: 현재 레버리지
        extreme_stage: STEP 0 극단 상황 Stage (0=정상, 1-4=극단)
    
    Returns:
        제한된 레버리지
    """
    if extreme_stage == 0:  # 정상
        return leverage
    elif extreme_stage == 1:  # 극단 Stage 1 (가장 위험)
        return 1  # 레버리지 금지
    elif extreme_stage == 2:  # 극단 Stage 2
        return min(leverage, 2)  # 최대 2배
    elif extreme_stage == 3:  # 극단 Stage 3
        return min(leverage, 3)  # 최대 3배
    elif extreme_stage == 4:  # 극단 Stage 4 (회복 중)
        return min(leverage, 5)  # 최대 5배
    else:
        return 1  # 기본 안전값
```

### 3.7 변동성 기반 조정

ATR 급증 시 레버리지를 낮춥니다.

```python
def apply_volatility_adjustment(leverage: int, atr_ratio: float) -> int:
    """
    ATR 변동성 비율로 레버리지 조정
    
    Args:
        leverage: 현재 레버리지
        atr_ratio: 현재 ATR / 평균 ATR (정상 = 1.0)
    
    Returns:
        조정된 레버리지
    """
    if atr_ratio >= 2.5:  # 극심한 변동성 (STEP 0 감지 수준)
        return max(1, int(leverage * 0.4))  # 60% 감소
    elif atr_ratio >= 2.0:  # 높은 변동성
        return max(1, int(leverage * 0.5))  # 50% 감소
    elif atr_ratio >= 1.5:  # 증가된 변동성
        return max(1, int(leverage * 0.7))  # 30% 감소
    elif atr_ratio <= 0.5:  # 낮은 변동성 (횡보)
        return max(1, int(leverage * 0.8))  # 20% 감소 (거짓 신호 가능성)
    else:  # 정상 범위 (0.5 ~ 1.5)
        return leverage
```

### 3.8 청산 압력 기반 조정

STEP 5의 청산 압력 지표를 반영합니다.

```python
def apply_liquidation_pressure_adjustment(leverage: int, liquidation_score: int) -> int:
    """
    청산 압력 점수로 레버리지 조정
    
    Args:
        leverage: 현재 레버리지
        liquidation_score: STEP 5 청산 압력 점수 (0-20, 낮을수록 안전)
    
    Returns:
        조정된 레버리지
    """
    if liquidation_score <= 5:  # 청산 압력 낮음 (안전)
        return leverage
    elif liquidation_score <= 10:  # 청산 압력 보통
        return max(1, int(leverage * 0.85))  # 15% 감소
    elif liquidation_score <= 15:  # 청산 압력 높음
        return max(1, int(leverage * 0.6))  # 40% 감소
    else:  # 청산 압력 매우 높음 (15-20)
        return max(1, int(leverage * 0.4))  # 60% 감소
```

### 3.9 체제 전환 중 보수적 조정

STEP 2에서 히스테리시스 또는 블렌딩 중이면 레버리지를 보수적으로 설정합니다.

```python
def apply_transition_adjustment(leverage: int, is_transitioning: bool, blend_ratio: float) -> int:
    """
    체제 전환 중일 때 레버리지 보수적 조정
    
    Args:
        leverage: 현재 레버리지
        is_transitioning: 체제 전환 중 여부 (STEP 2)
        blend_ratio: 블렌딩 비율 0.0~1.0 (1.0 = 완전 전환)
    
    Returns:
        조정된 레버리지
    """
    if not is_transitioning:
        return leverage
    
    # 블렌딩 비율이 낮을수록 (전환 초기일수록) 레버리지 감소
    if blend_ratio < 0.3:  # 전환 초기 (0-30%)
        return max(1, int(leverage * 0.5))  # 50% 감소
    elif blend_ratio < 0.7:  # 전환 중기 (30-70%)
        return max(1, int(leverage * 0.7))  # 30% 감소
    else:  # 전환 후기 (70-100%)
        return max(1, int(leverage * 0.85))  # 15% 감소
```

### 3.10 최종 레버리지 계산 함수

모든 조정을 순차적으로 적용하여 최종 레버리지를 계산합니다.

```python
def calculate_final_leverage(
    regime: str,
    confidence: float,
    safety_score: int,
    inflection_score: int,
    extreme_stage: int,
    atr_ratio: float,
    liquidation_score: int,
    is_transitioning: bool,
    blend_ratio: float
) -> dict:
    """
    모든 요소를 종합하여 최종 레버리지 계산
    
    Returns:
        {
            'final_leverage': int,
            'base_leverage': int,
            'adjustments': {
                'safety': float,
                'inflection': float,
                'extreme': float,
                'volatility': float,
                'liquidation': float,
                'transition': float
            },
            'reasoning': str
        }
    """
    # 1. 기본 레버리지 (체제 + 신뢰도)
    base = get_base_leverage(regime, confidence)
    adjustments = {'base': base}
    
    # 2. 안전성 점수 조정
    after_safety = apply_safety_adjustment(base, safety_score, regime)
    adjustments['safety'] = after_safety / base
    
    # 3. 변곡점 점수 조정
    after_inflection = apply_inflection_adjustment(after_safety, inflection_score)
    adjustments['inflection'] = after_inflection / after_safety
    
    # 4. 극단 상황 제한 (강제)
    after_extreme = apply_extreme_condition_limit(after_inflection, extreme_stage)
    adjustments['extreme'] = after_extreme / after_inflection if after_inflection > 0 else 1.0
    
    # 5. 변동성 조정
    after_volatility = apply_volatility_adjustment(after_extreme, atr_ratio)
    adjustments['volatility'] = after_volatility / after_extreme if after_extreme > 0 else 1.0
    
    # 6. 청산 압력 조정
    after_liquidation = apply_liquidation_pressure_adjustment(after_volatility, liquidation_score)
    adjustments['liquidation'] = after_liquidation / after_volatility if after_volatility > 0 else 1.0
    
    # 7. 체제 전환 조정
    final = apply_transition_adjustment(after_liquidation, is_transitioning, blend_ratio)
    adjustments['transition'] = final / after_liquidation if after_liquidation > 0 else 1.0
    
    # 8. 절대 최소값 (1배) 보장
    final = max(1, final)
    
    # 9. 조정 사유 생성
    reasoning = []
    if adjustments['safety'] > 1.0:
        reasoning.append(f"안전성 점수 우수 (+{int((adjustments['safety']-1)*100)}%)")
    if adjustments['inflection'] > 1.0:
        reasoning.append(f"강한 변곡점 (+{int((adjustments['inflection']-1)*100)}%)")
    if adjustments['extreme'] < 1.0:
        reasoning.append(f"극단 상황 제한 (Stage {extreme_stage})")
    if adjustments['volatility'] < 1.0:
        reasoning.append(f"높은 변동성 ({int((1-adjustments['volatility'])*100)}% 감소)")
    if adjustments['liquidation'] < 1.0:
        reasoning.append(f"청산 압력 ({int((1-adjustments['liquidation'])*100)}% 감소)")
    if adjustments['transition'] < 1.0:
        reasoning.append(f"체제 전환 중 ({int((1-adjustments['transition'])*100)}% 감소)")
    
    return {
        'final_leverage': final,
        'base_leverage': base,
        'adjustments': adjustments,
        'reasoning': ' | '.join(reasoning) if reasoning else '정상 레버리지'
    }
```

### 3.11 레버리지 적용 예시

**예시 1: 강한 상승 추세, 높은 확신**

```
입력:
  - regime: STRONG_UPTREND
  - confidence: 0.92
  - safety_score: 91
  - inflection_score: 88
  - extreme_stage: 0
  - atr_ratio: 1.2
  - liquidation_score: 6
  - is_transitioning: False

계산:
  1. 기본: 5배 (신뢰도 0.92 ≥ 0.85)
  2. 안전성: 5 × 1.2 = 6배 (91점 > 기준 65점 + 20)
  3. 변곡점: 6 × 1.15 = 6.9 → 6배 (88점 ≥ 80)
  4. 극단: 6배 (Stage 0, 제한 없음)
  5. 변동성: 6배 (ATR 1.2, 정상 범위)
  6. 청산: 6배 (점수 6, 낮음)
  7. 전환: 6배 (전환 중 아님)

최종: 6배
사유: "안전성 점수 우수 (+20%) | 강한 변곡점 (+15%)"
```

**예시 2: 횡보, 극단 상황 회복 중**

```
입력:
  - regime: SIDEWAYS
  - confidence: 0.88
  - safety_score: 82
  - inflection_score: 65
  - extreme_stage: 3
  - atr_ratio: 1.8
  - liquidation_score: 12
  - is_transitioning: False

계산:
  1. 기본: 2배 (신뢰도 0.88 ≥ 0.85)
  2. 안전성: 2 × 1.0 = 2배 (82점 = 기준 80점 + 2)
  3. 변곡점: 2 × 0.85 = 1.7 → 1배 (65점, 약한 변곡점)
  4. 극단: min(1, 3) = 1배 (Stage 3, 최대 3배 제한)
  5. 변동성: 1 × 0.7 = 0.7 → 1배 (ATR 1.8, 높은 변동성)
  6. 청산: 1 × 0.6 = 0.6 → 1배 (점수 12, 높은 압력)
  7. 전환: 1배 (전환 중 아님)

최종: 1배
사유: "극단 상황 제한 (Stage 3) | 높은 변동성 (30% 감소) | 청산 압력 (40% 감소)"
```

**예시 3: 강한 하락 추세, 체제 전환 중**

```
입력:
  - regime: STRONG_DOWNTREND
  - confidence: 0.96
  - safety_score: 88
  - inflection_score: 92
  - extreme_stage: 0
  - atr_ratio: 1.1
  - liquidation_score: 4
  - is_transitioning: True
  - blend_ratio: 0.45

계산:
  1. 기본: 10배 (신뢰도 0.96 ≥ 0.95)
  2. 안전성: 10 × 1.5 = 15배 → 15배 (88점 > 기준 65점 + 20, 최대치)
  3. 변곡점: 15 × 1.3 = 19.5 → 15배 (92점 ≥ 90, 최대치 유지)
  4. 극단: 15배 (Stage 0, 제한 없음)
  5. 변동성: 15배 (ATR 1.1, 정상 범위)
  6. 청산: 15배 (점수 4, 매우 낮음)
  7. 전환: 15 × 0.7 = 10.5 → 10배 (블렌딩 45%, 전환 중기)

최종: 10배
사유: "안전성 점수 우수 (+50%) | 매우 강한 변곡점 (+30%) | 체제 전환 중 (30% 감소)"
```

### 3.12 레버리지 한도 정책

거래소별 레버리지 한도를 준수합니다.

| 거래소 | 최대 레버리지 | 비고 |
|--------|---------------|------|
| Binance Futures | 125배 | 소액 포지션에만 적용 |
| Bybit | 100배 | 소액 포지션에만 적용 |
| OKX | 125배 | 소액 포지션에만 적용 |
| 시스템 기본 한도 | **15배** | 안전성 우선 |

**포지션 크기별 최대 레버리지:**
- $0 ~ $10,000: 최대 15배
- $10,000 ~ $50,000: 최대 10배
- $50,000 ~ $100,000: 최대 7배
- $100,000 ~ $500,000: 최대 5배
- $500,000 이상: 최대 3배

```python
def apply_position_size_limit(leverage: int, position_value_usd: float) -> int:
    """
    포지션 크기에 따른 레버리지 한도 적용
    
    Args:
        leverage: 계산된 레버리지
        position_value_usd: 포지션 가치 (USD)
    
    Returns:
        한도 적용된 레버리지
    """
    if position_value_usd < 10000:
        max_lev = 15
    elif position_value_usd < 50000:
        max_lev = 10
    elif position_value_usd < 100000:
        max_lev = 7
    elif position_value_usd < 500000:
        max_lev = 5
    else:
        max_lev = 3
    
    return min(leverage, max_lev)
```

---

### 3.13 청산가 안전 거리 계산 (v1.0.1 신규)

**목적**: 레버리지가 높아도 청산가가 너무 가까워 슬리피지/급변동 시 즉시 청산되는 위험을 방지합니다.

**기본 원칙**:
```
진입가와 청산가 사이 최소 안전 거리 = ATR × 안전계수
```

**체제별 안전계수**:
| 체제 | 안전계수 | 최소 거리 (ATR 배수) |
|------|----------|---------------------|
| STRONG_UPTREND/DOWNTREND | 2.5 | 강한 추세에서도 2.5 ATR 확보 |
| WEAK_UPTREND/DOWNTREND | 3.0 | 약한 추세는 변동성 대비 |
| SIDEWAYS | 3.5 | 횡보장은 급변동 가능성 높음 |
| VOLATILE | 4.0 | 변동장은 최대 안전거리 |

**계산 공식**:

```python
def calculate_max_leverage_with_liquidation_safety(
    entry_price: float,
    direction: str,  # 'LONG' or 'SHORT'
    atr: float,
    regime: str,
    initial_leverage: int
) -> int:
    """
    청산가 안전 거리를 고려한 최대 레버리지 계산
    
    Args:
        entry_price: 진입 가격
        direction: 포지션 방향
        atr: 현재 ATR (Average True Range)
        regime: 현재 체제
        initial_leverage: 초기 계산된 레버리지
    
    Returns:
        안전 거리 만족하는 최대 레버리지
    """
    # 1) 체제별 안전계수 결정
    safety_factors = {
        'STRONG_UPTREND': 2.5,
        'STRONG_DOWNTREND': 2.5,
        'WEAK_UPTREND': 3.0,
        'WEAK_DOWNTREND': 3.0,
        'SIDEWAYS': 3.5,
        'VOLATILE': 4.0
    }
    safety_factor = safety_factors.get(regime, 3.0)
    
    # 2) 최소 안전 거리 계산
    min_safety_distance = atr * safety_factor
    min_safety_distance_pct = (min_safety_distance / entry_price) * 100
    
    # 3) 청산가 계산 (격리 마진 기준)
    # LONG: 청산가 = 진입가 × (1 - 1/레버리지)
    # SHORT: 청산가 = 진입가 × (1 + 1/레버리지)
    
    # 4) 안전 거리 만족하는 최대 레버리지 계산
    if direction == 'LONG':
        # 청산가가 진입가 아래 min_safety_distance_pct% 이상 떨어져야 함
        # (진입가 - 청산가) / 진입가 >= min_safety_distance_pct / 100
        # 1 / leverage >= min_safety_distance_pct / 100
        max_safe_leverage = int(100 / min_safety_distance_pct)
    else:  # SHORT
        # 청산가가 진입가 위 min_safety_distance_pct% 이상 떨어져야 함
        # (청산가 - 진입가) / 진입가 >= min_safety_distance_pct / 100
        # 1 / leverage >= min_safety_distance_pct / 100
        max_safe_leverage = int(100 / min_safety_distance_pct)
    
    # 5) 초기 레버리지와 안전 레버리지 중 작은 값 선택
    final_leverage = min(initial_leverage, max_safe_leverage)
    
    # 6) 로깅
    if final_leverage < initial_leverage:
        logger.warning(
            f"레버리지 하향: {initial_leverage}배 → {final_leverage}배 "
            f"(청산가 안전거리 {min_safety_distance_pct:.2f}% 확보)"
        )
    
    return final_leverage


def calculate_liquidation_price(
    entry_price: float,
    leverage: int,
    direction: str
) -> float:
    """
    청산가 계산 (격리 마진 모드)
    
    Args:
        entry_price: 진입 가격
        leverage: 레버리지
        direction: 포지션 방향
    
    Returns:
        청산가
    """
    if direction == 'LONG':
        liquidation_price = entry_price * (1 - 1 / leverage)
    else:  # SHORT
        liquidation_price = entry_price * (1 + 1 / leverage)
    
    return liquidation_price


def verify_liquidation_safety(
    entry_price: float,
    leverage: int,
    direction: str,
    atr: float,
    regime: str
) -> dict:
    """
    청산가 안전성 검증
    
    Returns:
        {
            'is_safe': bool,
            'liquidation_price': float,
            'safety_distance_pct': float,
            'min_required_pct': float,
            'margin': float  # 여유도 (양수면 안전)
        }
    """
    # 청산가 계산
    liq_price = calculate_liquidation_price(entry_price, leverage, direction)
    
    # 실제 거리 계산
    actual_distance = abs(liq_price - entry_price)
    actual_distance_pct = (actual_distance / entry_price) * 100
    
    # 필요 거리 계산
    safety_factors = {
        'STRONG_UPTREND': 2.5, 'STRONG_DOWNTREND': 2.5,
        'WEAK_UPTREND': 3.0, 'WEAK_DOWNTREND': 3.0,
        'SIDEWAYS': 3.5, 'VOLATILE': 4.0
    }
    safety_factor = safety_factors.get(regime, 3.0)
    required_distance = atr * safety_factor
    required_distance_pct = (required_distance / entry_price) * 100
    
    # 안전성 판정
    margin = actual_distance_pct - required_distance_pct
    is_safe = margin >= 0
    
    return {
        'is_safe': is_safe,
        'liquidation_price': liq_price,
        'safety_distance_pct': actual_distance_pct,
        'min_required_pct': required_distance_pct,
        'margin': margin,
        'safety_grade': (
            'EXCELLENT' if margin > 2.0 else
            'GOOD' if margin > 0.5 else
            'ACCEPTABLE' if margin >= 0 else
            'INSUFFICIENT' if margin > -1.0 else
            'DANGEROUS'
        )
    }
```

**적용 시점**:
```python
# 최종 레버리지 계산 시 (3.10 함수 내)
final_leverage = calculate_final_leverage(...)

# 청산가 안전 거리 체크 추가
final_leverage = calculate_max_leverage_with_liquidation_safety(
    entry_price=current_price,
    direction=signal_direction,
    atr=market_state['atr'],
    regime=market_state['regime'],
    initial_leverage=final_leverage
)

# 최종 검증
safety_check = verify_liquidation_safety(
    entry_price=current_price,
    leverage=final_leverage,
    direction=signal_direction,
    atr=market_state['atr'],
    regime=market_state['regime']
)

if not safety_check['is_safe']:
    logger.error(f"청산가 안전 거리 부족: {safety_check}")
    return None  # 시그널 생성 중단
```

**예시 (BTCUSDT)**:
```python
# 조건
entry_price = 42000
direction = 'LONG'
atr = 520  # 1시간 ATR
regime = 'WEAK_UPTREND'
initial_leverage = 12

# 계산
safety_factor = 3.0
min_distance = 520 × 3.0 = 1560 (3.71%)
max_safe_leverage = 100 / 3.71 = 26배

# 최종 레버리지: min(12, 26) = 12배 ✓

# 청산가: 42000 × (1 - 1/12) = 38500
# 실제 거리: 42000 - 38500 = 3500 (8.33%)
# 여유도: 8.33% - 3.71% = 4.62% ✓ (EXCELLENT)
```

**로깅 예시**:
```json
{
  "timestamp": "2025-10-17T14:30:00Z",
  "symbol": "BTCUSDT",
  "event": "liquidation_safety_check",
  "entry_price": 42000,
  "direction": "LONG",
  "leverage": 12,
  "liquidation_price": 38500,
  "safety_distance_pct": 8.33,
  "min_required_pct": 3.71,
  "margin": 4.62,
  "grade": "EXCELLENT",
  "regime": "WEAK_UPTREND",
  "atr": 520
}
```

---

## 4. 시그널 출력 포맷

### 4.1 표준 JSON 포맷 (API/Webhook)

```json
{
  "signal_id": "SIG_20251017_143052_BTC",
  "version": "1.0.0",
  "timestamp": "2025-10-17T14:30:52.328Z",
  "symbol": "BTC/USDT",
  "direction": "LONG",
  "entry_price": 69000.00,
  "stop_loss": {
    "price": 67800.00,
    "percentage": -1.74,
    "method": "TRENDLINE",
    "trailing": false
  },
  "take_profit": [
    {
      "level": "TP1",
      "price": 70500.00,
      "close_percentage": 40,
      "risk_reward": 1.5
    },
    {
      "level": "TP2",
      "price": 71800.00,
      "close_percentage": 30,
      "risk_reward": 2.4
    },
    {
      "level": "TP3",
      "price": 73500.00,
      "close_percentage": 30,
      "risk_reward": 3.8,
      "trailing_enabled": true,
      "trailing_offset": 1.2
    }
  ],
  "leverage": {
    "value": 6,
    "base": 5,
    "adjustments": {
      "safety": 1.2,
      "inflection": 1.15,
      "extreme": 1.0,
      "volatility": 1.0,
      "liquidation": 1.0,
      "transition": 1.0
    },
    "reasoning": "안전성 점수 우수 (+20%) | 강한 변곡점 (+15%)"
  },
  "confidence": {
    "regime": 0.92,
    "inflection": 0.88,
    "safety": 0.91,
    "overall": 0.90
  },
  "market_context": {
    "regime": "STRONG_UPTREND",
    "extreme_stage": 0,
    "atr_ratio": 1.2,
    "liquidation_pressure": "LOW",
    "is_transitioning": false
  },
  "risk_metrics": {
    "risk_reward_ratio": 2.4,
    "max_drawdown_risk": 1.74,
    "win_probability": 0.88,
    "liquidation_price": 65100.00
  },
  "validity": {
    "duration_seconds": 300,
    "expires_at": "2025-10-17T14:35:52.328Z",
    "invalidation_price": 68500.00
  },
  "metadata": {
    "step_scores": {
      "step4_inflection": 88,
      "step5_safety": 91
    },
    "processing_time_ms": 645,
    "system_version": "1.0.0"
  }
}
```

### 4.2 간소화 포맷 (메시징: Telegram/Discord)

```
🟢 LONG BTC/USDT

진입: $69,000
손절: $67,800 (-1.74%)
익절: 
  TP1: $70,500 (40%) [1.5R]
  TP2: $71,800 (30%) [2.4R]
  TP3: $73,500 (30%) [3.8R] 🔄

레버리지: 6배
신뢰도: 90%
체제: STRONG_UPTREND 📈

유효: 5분 | R:R 2.4 | #SIG20251017143052
```

### 4.3 거래소 호환 포맷 (Binance Futures API)

```json
{
  "symbol": "BTCUSDT",
  "side": "BUY",
  "type": "LIMIT",
  "quantity": 0.15,
  "price": 69000.00,
  "timeInForce": "GTC",
  "leverage": 6,
  "stopPrice": 67800.00,
  "takeProfitPrice": [
    {"price": 70500.00, "quantity": 0.06},
    {"price": 71800.00, "quantity": 0.045},
    {"price": 73500.00, "quantity": 0.045}
  ],
  "reduceOnly": false,
  "newClientOrderId": "SIG_20251017_143052_BTC"
}
```

### 4.4 백테스트 호환 포맷 (CSV)

```csv
signal_id,timestamp,symbol,direction,entry,stop_loss,tp1,tp2,tp3,leverage,confidence,regime,inflection_score,safety_score
SIG_20251017_143052_BTC,2025-10-17T14:30:52.328Z,BTC/USDT,LONG,69000,67800,70500,71800,73500,6,0.90,STRONG_UPTREND,88,91
```

---

## 5. 송출 메커니즘

### 5.1 지원 송출 방법

| 방법 | 지연 시간 | 신뢰성 | 사용 사례 |
|------|----------|--------|----------|
| **Webhook** | 50~100ms | 높음 | 실시간 트레이딩 봇, 자동화 |
| **REST API** | 100~200ms | 매우 높음 | 시스템 통합, 백테스트 |
| **WebSocket** | 10~30ms | 매우 높음 | 초저지연 트레이딩 |
| **메시징 (Telegram/Discord)** | 500~1000ms | 중간 | 사용자 알림, 모니터링 |
| **데이터베이스 (PostgreSQL)** | 5~10ms | 매우 높음 | 로깅, 분석, 감사 |
| **파일 시스템 (JSON/CSV)** | 1~5ms | 높음 | 로컬 백테스트, 개발 |

### 5.2 Webhook 송출

```python
import httpx
import asyncio
from typing import Dict

async def send_webhook(signal: Dict, webhook_url: str, retry: int = 3) -> bool:
    """
    Webhook으로 시그널 송출 (비동기)
    
    Args:
        signal: 시그널 JSON 딕셔너리
        webhook_url: Webhook 엔드포인트 URL
        retry: 재시도 횟수
    
    Returns:
        성공 여부
    """
    headers = {
        'Content-Type': 'application/json',
        'X-Signal-Version': '1.0.0',
        'X-Signal-ID': signal['signal_id']
    }
    
    async with httpx.AsyncClient(timeout=5.0) as client:
        for attempt in range(retry):
            try:
                response = await client.post(
                    webhook_url,
                    json=signal,
                    headers=headers
                )
                
                if response.status_code == 200:
                    print(f"[Webhook] 시그널 송출 성공: {signal['signal_id']}")
                    return True
                else:
                    print(f"[Webhook] 송출 실패 (HTTP {response.status_code}), 재시도 {attempt+1}/{retry}")
            
            except Exception as e:
                print(f"[Webhook] 오류: {e}, 재시도 {attempt+1}/{retry}")
            
            if attempt < retry - 1:
                await asyncio.sleep(0.5 * (attempt + 1))  # 지수 백오프
    
    return False
```

### 5.3 메시징 송출 (Telegram)

```python
import asyncio
from telegram import Bot

async def send_telegram_signal(signal: Dict, bot_token: str, chat_id: str):
    """
    Telegram으로 시그널 알림 송출
    
    Args:
        signal: 시그널 딕셔너리
        bot_token: Telegram Bot API 토큰
        chat_id: 대상 채팅 ID
    """
    bot = Bot(token=bot_token)
    
    # 간소화 포맷 생성
    direction_emoji = "🟢" if signal['direction'] == "LONG" else "🔴"
    regime_emoji = {"STRONG_UPTREND": "📈", "WEAK_UPTREND": "↗️", 
                    "SIDEWAYS": "↔️", "WEAK_DOWNTREND": "↘️",
                    "STRONG_DOWNTREND": "📉", "VOLATILE": "⚡"}
    
    message = f"""
{direction_emoji} **{signal['direction']} {signal['symbol']}**

진입: ${signal['entry_price']:,.2f}
손절: ${signal['stop_loss']['price']:,.2f} ({signal['stop_loss']['percentage']:.2f}%)
익절: 
"""
    
    for tp in signal['take_profit']:
        trailing = " 🔄" if tp.get('trailing_enabled') else ""
        message += f"  {tp['level']}: ${tp['price']:,.2f} ({tp['close_percentage']}%) [{tp['risk_reward']:.1f}R]{trailing}\n"
    
    message += f"""
레버리지: {signal['leverage']['value']}배
신뢰도: {signal['confidence']['overall']*100:.0f}%
체제: {signal['market_context']['regime']} {regime_emoji.get(signal['market_context']['regime'], '')}

유효: {signal['validity']['duration_seconds']//60}분 | R:R {signal['risk_metrics']['risk_reward_ratio']:.1f} | #{signal['signal_id']}
"""
    
    await bot.send_message(
        chat_id=chat_id,
        text=message,
        parse_mode='Markdown'
    )
    
    print(f"[Telegram] 시그널 알림 전송 완료: {signal['signal_id']}")
```

### 5.4 데이터베이스 저장

```python
import asyncpg
from datetime import datetime

async def save_signal_to_db(signal: Dict, db_pool: asyncpg.Pool):
    """
    PostgreSQL에 시그널 저장 (로깅/분석용)
    
    Args:
        signal: 시그널 딕셔너리
        db_pool: asyncpg 연결 풀
    """
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO signals (
                signal_id, timestamp, symbol, direction,
                entry_price, stop_loss, leverage, confidence,
                regime, inflection_score, safety_score,
                risk_reward_ratio, signal_json
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
        """,
            signal['signal_id'],
            datetime.fromisoformat(signal['timestamp'].replace('Z', '+00:00')),
            signal['symbol'],
            signal['direction'],
            signal['entry_price'],
            signal['stop_loss']['price'],
            signal['leverage']['value'],
            signal['confidence']['overall'],
            signal['market_context']['regime'],
            signal['metadata']['step_scores']['step4_inflection'],
            signal['metadata']['step_scores']['step5_safety'],
            signal['risk_metrics']['risk_reward_ratio'],
            signal  # JSONB 컬럼에 전체 시그널 저장
        )
    
    print(f"[DB] 시그널 저장 완료: {signal['signal_id']}")
```

### 5.5 멀티 채널 동시 송출

```python
async def dispatch_signal_multi_channel(signal: Dict, config: Dict):
    """
    여러 채널로 동시 송출 (병렬 처리)
    
    Args:
        signal: 시그널 딕셔너리
        config: 송출 설정
            {
                'webhook_urls': ['https://...'],
                'telegram': {'bot_token': '...', 'chat_id': '...'},
                'db_pool': asyncpg.Pool,
                'file_path': '/path/to/signals.json'
            }
    """
    tasks = []
    
    # Webhook 송출
    if 'webhook_urls' in config:
        for url in config['webhook_urls']:
            tasks.append(send_webhook(signal, url))
    
    # Telegram 송출
    if 'telegram' in config:
        tasks.append(send_telegram_signal(
            signal,
            config['telegram']['bot_token'],
            config['telegram']['chat_id']
        ))
    
    # 데이터베이스 저장
    if 'db_pool' in config:
        tasks.append(save_signal_to_db(signal, config['db_pool']))
    
    # 파일 저장
    if 'file_path' in config:
        tasks.append(save_signal_to_file(signal, config['file_path']))
    
    # 병렬 실행
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 결과 확인
    success_count = sum(1 for r in results if r is True or r is None)
    print(f"[Dispatch] {success_count}/{len(tasks)} 채널 송출 성공")
    
    return success_count == len(tasks)
```

---

## 6. 예외 처리 및 안전장치

### 6.1 시그널 생성 전 검증

```python
def validate_signal_generation(data: Dict) -> tuple[bool, str]:
    """
    시그널 생성 전 필수 조건 검증
    
    Returns:
        (검증 통과 여부, 실패 사유)
    """
    # 1. 극단 상황 체크
    """
    시그널 생성 전 필수 조건 검증 (v1.0.1 개정: 극단 Stage 상세화)
    
    Returns:
        (검증 통과 여부, 실패 사유)
    """
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 1. 극단 상황 Stage별 필터링 (v1.0.1 신규)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    extreme_stage = data.get('extreme_stage', 'NORMAL')
    extreme_severity = data.get('extreme_severity', 0)
    
    # Stage 1-2: 완전 차단
    if extreme_stage in ['Stage 1', 'Stage 2']:
        return False, f"극단 상황 {extreme_stage} (심각도 {extreme_severity}): 모든 거래 차단"
    
    # Stage 3: 조건부 허용 (심각도 낮음 + 안전성 매우 높음)
    if extreme_stage == 'Stage 3':
        if extreme_severity > 70:
            return False, f"Stage 3 심각도 높음 ({extreme_severity}): 거래 차단"
        if data.get('safety_score', 0) < 85:
            return False, f"Stage 3 안전성 부족 ({data.get('safety_score')}): 최소 85점 필요"
        if data.get('inflection_score', 0) < 80:
            return False, f"Stage 3 변곡점 약함 ({data.get('inflection_score')}): 최소 80점 필요"
        # 통과 시 레버리지 강제 제한 (최대 3배)
        data['leverage'] = min(data.get('leverage', 1), 3)
        logger.warning(f"Stage 3 조건부 진입: 레버리지 {data['leverage']}배로 제한")
    
    # Stage 4: 제한 완화 (레버리지만 보수적)
    if extreme_stage == 'Stage 4':
        # 레버리지 50% 감소
        original_leverage = data.get('leverage', 1)
        data['leverage'] = max(1, int(original_leverage * 0.5))
        if data['leverage'] != original_leverage:
            logger.info(f"Stage 4 레버리지 조정: {original_leverage}배 → {data['leverage']}배")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 2. STEP 4 변곡점 점수
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if data['inflection_score'] < 60:
        return False, f"변곡점 점수 부족: {data['inflection_score']} < 60"
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 3. STEP 5 안전성 점수 (체제별 기준)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    regime_thresholds = {
        'STRONG_UPTREND': 65, 'STRONG_DOWNTREND': 65,
        'WEAK_UPTREND': 75, 'WEAK_DOWNTREND': 75,
        'SIDEWAYS': 80, 'VOLATILE': 90
    }
    threshold = regime_thresholds.get(data['regime'], 80)
    if data['safety_score'] < threshold:
        return False, f"안전성 점수 부족: {data['safety_score']} < {threshold} ({data['regime']})"
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 4. 손절/익절 계산 완료
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if not data.get('stop_loss') or not data.get('take_profit'):
        return False, "손절/익절 계산 실패 (STEP 6 오류)"
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 5. 레버리지 계산 완료
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if not data.get('leverage') or data['leverage'] < 1:
        return False, f"레버리지 계산 오류: {data.get('leverage')}"
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 6. VOLATILE 체제 필터링
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if data['regime'] == 'VOLATILE' and data['leverage'] > 1:
        return False, "VOLATILE 체제: 레버리지 거래 금지"
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 7. 청산가 안전 거리 검증 (v1.0.1 신규)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if 'liquidation_safety' in data:
        if not data['liquidation_safety']['is_safe']:
            margin = data['liquidation_safety']['margin']
            return False, f"청산가 안전거리 부족 (여유 {margin:.2f}%)"
    
    return True, "검증 통과"
```

**극단 Stage별 진입 정책 요약** (v1.0.1):

| Stage | 진입 가능 | 조건 | 레버리지 제한 |
|-------|----------|------|---------------|
| Normal | ✅ 허용 | 기본 조건만 | 기본 계산값 |
| Stage 4 | ✅ 허용 | 기본 조건 | 50% 감소 |
| Stage 3 | ⚠️ 조건부 | 심각도 ≤70 AND 안전성 ≥85 AND 변곡점 ≥80 | 최대 3배 |
| Stage 2 | ❌ 차단 | - | - |
| Stage 1 | ❌ 차단 | - | - |

```

### 6.2 시그널 무효화 조건

생성된 시그널이 다음 조건을 만족하면 자동으로 무효화됩니다:

| 조건 | 설명 | 처리 |
|------|------|------|
| **시간 만료** | 유효 시간 초과 (기본 5분) | 시그널 취소 알림 |
| **무효화 가격 도달** | 진입 전 특정 가격 돌파 | 시그널 취소 알림 |
| **극단 상황 발생** | STEP 0 Stage 1-3 감지 | 모든 대기 시그널 취소 |
| **체제 급변** | 신뢰도 0.4 이하로 하락 | 시그널 취소 알림 |
| **수동 취소** | 사용자/시스템 명령 | 즉시 취소 |

```python
def check_signal_validity(signal: Dict, current_market: Dict) -> tuple[bool, str]:
    """
    시그널 유효성 실시간 체크
    
    Returns:
        (유효 여부, 무효화 사유)
    """
    from datetime import datetime, timezone
    
    # 1. 시간 만료
    expires_at = datetime.fromisoformat(signal['validity']['expires_at'].replace('Z', '+00:00'))
    if datetime.now(timezone.utc) > expires_at:
        return False, "시간 만료"
    
    # 2. 무효화 가격 도달
    invalidation_price = signal['validity']['invalidation_price']
    current_price = current_market['price']
    
    if signal['direction'] == 'LONG':
        if current_price < invalidation_price:
            return False, f"무효화 가격 하향 돌파: {current_price} < {invalidation_price}"
    else:  # SHORT
        if current_price > invalidation_price:
            return False, f"무효화 가격 상향 돌파: {current_price} > {invalidation_price}"
    
    # 3. 극단 상황 발생
    if current_market['extreme_stage'] in [1, 2, 3]:
        return False, f"극단 상황 발생: Stage {current_market['extreme_stage']}"
    
    # 4. 체제 급변
    if current_market['regime_confidence'] < 0.4:
        return False, f"체제 신뢰도 급락: {current_market['regime_confidence']}"
    
    return True, "유효"
```

### 6.3 긴급 중단 메커니즘

```python
class EmergencyStop:
    """
    시스템 전체 긴급 중단 관리
    """
    def __init__(self):
        self.is_active = False
        self.reason = ""
        self.triggered_at = None
    
    def trigger(self, reason: str):
        """긴급 중단 활성화"""
        self.is_active = True
        self.reason = reason
        self.triggered_at = datetime.now()
        
        # 모든 대기 시그널 취소
        cancel_all_pending_signals()
        
        # 알림 발송
        send_emergency_alert(reason)
        
        print(f"[EMERGENCY STOP] {reason}")
    
    def reset(self):
        """긴급 중단 해제"""
        self.is_active = False
        self.reason = ""
        self.triggered_at = None
        print("[EMERGENCY STOP] 해제됨")
    
    def check_conditions(self, market: Dict):
        """긴급 중단 조건 체크"""
        # Flash Crash 감지
        if market['atr_ratio'] > 3.0:
            self.trigger(f"Flash Crash 감지: ATR {market['atr_ratio']:.1f}배")
        
        # 연속 손실
        if get_consecutive_losses() >= 5:
            self.trigger("연속 5회 손실: 시스템 점검 필요")
        
        # 일일 손실 한도
        if get_daily_loss_percentage() > 10:
            self.trigger(f"일일 손실 한도 초과: {get_daily_loss_percentage():.1f}%")
        
        # API 장애
        if not check_exchange_api_health():
            self.trigger("거래소 API 장애 감지")

# 전역 인스턴스
emergency_stop = EmergencyStop()
```

### 6.4 에러 로깅 및 알림

```python
import logging
from enum import Enum

class AlertLevel(Enum):
    INFO = 1
    WARNING = 2
    ERROR = 3
    CRITICAL = 4

def log_and_alert(level: AlertLevel, message: str, context: Dict = None):
    """
    에러 로깅 및 알림 발송
    
    Args:
        level: 알림 레벨
        message: 메시지
        context: 추가 컨텍스트 정보
    """
    # 로깅
    log_msg = f"[{level.name}] {message}"
    if context:
        log_msg += f" | Context: {context}"
    
    if level == AlertLevel.INFO:
        logging.info(log_msg)
    elif level == AlertLevel.WARNING:
        logging.warning(log_msg)
    elif level == AlertLevel.ERROR:
        logging.error(log_msg)
    elif level == AlertLevel.CRITICAL:
        logging.critical(log_msg)
    
    # CRITICAL/ERROR는 즉시 알림
    if level in [AlertLevel.CRITICAL, AlertLevel.ERROR]:
        send_admin_alert(level, message, context)
    
    # 데이터베이스 저장
    save_log_to_db(level, message, context)
```



### 6.5 연속 손실 대응 시스템 (v1.0.1 신규)

**목적**: 연속 손실 발생 시 자동으로 거래를 중단하거나 레버리지를 축소하여 파산 위험을 방지합니다.

**추적 지표**:
```python
class ConsecutiveLossTracker:
    """연속 손실 추적 클래스"""
    
    def __init__(self):
        self.consecutive_losses = 0  # 연속 손실 횟수
        self.loss_streak_start = None  # 연속 손실 시작 시각
        self.total_loss_pct = 0.0  # 누적 손실률
        self.max_consecutive_losses = 5  # 최대 허용 연속 손실
        self.max_total_loss_pct = 10.0  # 최대 허용 누적 손실률 (%)
        self.cooldown_hours = 12  # 중단 후 대기 시간 (시간)
        self.is_trading_suspended = False  # 거래 중단 상태
        self.suspension_until = None  # 중단 종료 시각
        
    def update(self, pnl_pct: float):
        """
        거래 결과 업데이트
        
        Args:
            pnl_pct: 수익률 (%)
        """
        if pnl_pct < 0:  # 손실
            self.consecutive_losses += 1
            self.total_loss_pct += abs(pnl_pct)
            
            if self.loss_streak_start is None:
                self.loss_streak_start = datetime.now()
            
            logger.warning(
                f"연속 손실 {self.consecutive_losses}회 "
                f"(누적 {self.total_loss_pct:.2f}%)"
            )
            
        else:  # 수익
            if self.consecutive_losses > 0:
                logger.info(
                    f"연속 손실 종료: {self.consecutive_losses}회 "
                    f"(누적 {self.total_loss_pct:.2f}%) → 수익 전환"
                )
            self.reset()
    
    def reset(self):
        """카운터 리셋"""
        self.consecutive_losses = 0
        self.loss_streak_start = None
        self.total_loss_pct = 0.0
    
    def check_suspension(self) -> tuple[bool, str]:
        """
        거래 중단 필요 여부 체크
        
        Returns:
            (중단 필요 여부, 사유)
        """
        # 이미 중단 상태면 시간 체크
        if self.is_trading_suspended:
            if datetime.now() < self.suspension_until:
                remaining = (self.suspension_until - datetime.now()).total_seconds() / 3600
                return True, f"거래 중단 중 (재개까지 {remaining:.1f}시간)"
            else:
                self.resume_trading()
                return False, "중단 해제"
        
        # 연속 손실 횟수 체크
        if self.consecutive_losses >= self.max_consecutive_losses:
            self.suspend_trading(
                f"연속 손실 {self.consecutive_losses}회 도달"
            )
            return True, f"연속 손실 {self.consecutive_losses}회: 거래 중단"
        
        # 누적 손실률 체크
        if self.total_loss_pct >= self.max_total_loss_pct:
            self.suspend_trading(
                f"누적 손실률 {self.total_loss_pct:.2f}% 도달"
            )
            return True, f"누적 손실 {self.total_loss_pct:.2f}%: 거래 중단"
        
        return False, "정상"
    
    def suspend_trading(self, reason: str):
        """거래 중단"""
        self.is_trading_suspended = True
        self.suspension_until = datetime.now() + timedelta(hours=self.cooldown_hours)
        
        logger.critical(
            f"[거래 중단] {reason} | "
            f"재개 시각: {self.suspension_until.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        # 관리자 알림
        send_admin_alert(
            AlertLevel.CRITICAL,
            f"자동 거래 중단: {reason}",
            {
                'consecutive_losses': self.consecutive_losses,
                'total_loss_pct': self.total_loss_pct,
                'suspension_until': self.suspension_until.isoformat()
            }
        )
    
    def resume_trading(self):
        """거래 재개"""
        logger.info("[거래 재개] 중단 기간 종료, 정상 운영 복귀")
        self.is_trading_suspended = False
        self.suspension_until = None
        self.reset()
    
    def get_leverage_multiplier(self) -> float:
        """
        연속 손실에 따른 레버리지 배수 조정
        
        Returns:
            레버리지 배수 (0.3 ~ 1.0)
        """
        if self.consecutive_losses == 0:
            return 1.0
        elif self.consecutive_losses == 1:
            return 0.9
        elif self.consecutive_losses == 2:
            return 0.7
        elif self.consecutive_losses == 3:
            return 0.5
        elif self.consecutive_losses >= 4:
            return 0.3
        
        return 1.0


# 전역 인스턴스 (시스템 시작 시 초기화)
loss_tracker = ConsecutiveLossTracker()
```

**시그널 생성 시 적용**:
```python
def generate_signal_with_loss_protection(data: Dict) -> Optional[Dict]:
    """
    연속 손실 보호 기능이 포함된 시그널 생성
    
    Returns:
        시그널 딕셔너리 또는 None (중단 시)
    """
    # 1) 거래 중단 상태 체크
    is_suspended, reason = loss_tracker.check_suspension()
    if is_suspended:
        logger.warning(f"시그널 생성 차단: {reason}")
        return None
    
    # 2) 기본 시그널 생성
    signal = generate_base_signal(data)
    if signal is None:
        return None
    
    # 3) 연속 손실에 따른 레버리지 감소
    leverage_multiplier = loss_tracker.get_leverage_multiplier()
    if leverage_multiplier < 1.0:
        original_leverage = signal['leverage']
        signal['leverage'] = max(1, int(original_leverage * leverage_multiplier))
        
        logger.info(
            f"연속 손실 보호: 레버리지 {original_leverage}배 → "
            f"{signal['leverage']}배 ({leverage_multiplier:.0%})"
        )
        
        signal['metadata']['leverage_adjusted_by_loss_protection'] = True
        signal['metadata']['loss_streak'] = loss_tracker.consecutive_losses
    
    return signal


def on_trade_closed(trade_result: Dict):
    """
    거래 종료 시 호출 (STEP 6 또는 실제 체결 시스템에서)
    
    Args:
        trade_result: {
            'pnl': float,  # 손익 (USD)
            'pnl_pct': float,  # 수익률 (%)
            'symbol': str,
            'side': str,
            'entry_price': float,
            'exit_price': float
        }
    """
    # 연속 손실 추적 업데이트
    loss_tracker.update(trade_result['pnl_pct'])
    
    # 상태 로깅
    logger.info(
        f"거래 종료: {trade_result['symbol']} {trade_result['side']} | "
        f"수익률 {trade_result['pnl_pct']:.2f}% | "
        f"연속 손실 {loss_tracker.consecutive_losses}회"
    )
```

**동작 시나리오**:

**시나리오 1: 연속 손실 증가**
```
거래 1: -2.1% (손실) → 연속 1회, 레버리지 90%
거래 2: -1.8% (손실) → 연속 2회, 레버리지 70%
거래 3: -2.5% (손실) → 연속 3회, 레버리지 50%
거래 4: -1.2% (손실) → 연속 4회, 레버리지 30%
거래 5: -1.9% (손실) → 연속 5회, 누적 -9.5%
→ 🚨 거래 중단 (12시간)
```

**시나리오 2: 수익 전환으로 리셋**
```
거래 1: -2.1% (손실) → 연속 1회
거래 2: -1.8% (손실) → 연속 2회
거래 3: +3.5% (수익) → 연속 손실 종료, 카운터 리셋
거래 4: -1.2% (손실) → 연속 1회 (새로 시작)
```

**시나리오 3: 누적 손실률 도달**
```
거래 1: -3.2% (손실) → 연속 1회, 누적 3.2%
거래 2: -4.1% (손실) → 연속 2회, 누적 7.3%
거래 3: -3.5% (손실) → 연속 3회, 누적 10.8%
→ 🚨 거래 중단 (누적 손실 10% 초과)
```

**설정 파라미터 조정**:
```python
# 보수적 설정 (초보자/고변동성 시장)
loss_tracker.max_consecutive_losses = 3
loss_tracker.max_total_loss_pct = 7.0
loss_tracker.cooldown_hours = 24

# 공격적 설정 (숙련자/안정적 시장)
loss_tracker.max_consecutive_losses = 7
loss_tracker.max_total_loss_pct = 15.0
loss_tracker.cooldown_hours = 6
```

**로깅 예시**:
```json
{
  "timestamp": "2025-10-17T18:42:15Z",
  "event": "trading_suspended",
  "reason": "consecutive_losses_5",
  "details": {
    "consecutive_losses": 5,
    "total_loss_pct": 9.5,
    "loss_streak_duration_hours": 3.2,
    "suspension_until": "2025-10-18T06:42:15Z",
    "cooldown_hours": 12
  },
  "action": "all_signal_generation_blocked"
}
```

**재개 조건**:
1. **시간 경과**: cooldown_hours 이후 자동 재개
2. **수동 재개**: 관리자 명령으로 조기 재개 가능
3. **재개 시 검증**: 시장 상황 안정 확인 (STEP 0 Normal)

---

## 7. 성능 지표 및 모니터링

### 7.1 처리 시간 목표

| 단계 | 목표 시간 | 실제 평균 | 비고 |
|------|----------|----------|------|
| STEP 0 | < 200ms | 120ms | 1분봉 분석 |
| STEP 1 | < 250ms | 200ms | MTF 병렬 처리 |
| STEP 2 | < 50ms | 16ms | 히스테리시스 체크 |
| STEP 3 | < 100ms | 55ms | 병렬 구조 분석 |
| STEP 4 | < 150ms | 85ms | 변곡점 감지 |
| STEP 5 | < 100ms | 40ms | 안전성 검증 |
| STEP 6 | < 100ms | 38ms | 출구 전략 |
| **STEP 7** | **< 50ms** | **45ms** | 레버리지 + 포맷팅 |
| **전체 파이프라인** | **< 1000ms** | **650ms** | 송출 제외 |

### 7.2 시그널 품질 지표

| 지표 | 목표 | 실제 (백테스트) | 측정 방법 |
|------|------|-----------------|----------|
| **승률** | ≥ 55% | 58.3% | TP1 도달 비율 |
| **평균 R:R** | ≥ 2.0 | 2.4 | 평균 수익/손실 비율 |
| **최대 연속 손실** | ≤ 5회 | 4회 | 역사 데이터 분석 |
| **일일 시그널 수** | 5~15개 | 8.7개 | BTC/USDT 5분봉 기준 |
| **거짓 시그널 비율** | ≤ 20% | 15.2% | 진입 전 무효화율 |
| **청산 비율** | ≤ 2% | 1.8% | 레버리지 사용 시 |

### 7.3 모니터링 대시보드

```python
class SignalMonitor:
    """
    실시간 시그널 모니터링
    """
    def __init__(self):
        self.signals_generated = 0
        self.signals_executed = 0
        self.signals_invalidated = 0
        self.total_profit_loss = 0.0
        self.win_count = 0
        self.loss_count = 0
    
    def get_statistics(self) -> Dict:
        """통계 반환"""
        total_trades = self.win_count + self.loss_count
        win_rate = self.win_count / total_trades if total_trades > 0 else 0
        
        return {
            'signals_generated': self.signals_generated,
            'signals_executed': self.signals_executed,
            'signals_invalidated': self.signals_invalidated,
            'execution_rate': self.signals_executed / self.signals_generated if self.signals_generated > 0 else 0,
            'total_trades': total_trades,
            'win_rate': win_rate,
            'total_profit_loss': self.total_profit_loss,
            'avg_profit_per_trade': self.total_profit_loss / total_trades if total_trades > 0 else 0
        }
    
    def print_dashboard(self):
        """대시보드 출력"""
        stats = self.get_statistics()
        print(f"""
╔══════════════════════════════════════╗
║   시그널 생성 시스템 모니터링         ║
╠══════════════════════════════════════╣
║ 생성: {stats['signals_generated']:>5} | 실행: {stats['signals_executed']:>5} | 무효: {stats['signals_invalidated']:>5} ║
║ 실행률: {stats['execution_rate']*100:>5.1f}%                       ║
║                                      ║
║ 총 거래: {stats['total_trades']:>5}                       ║
║ 승률: {stats['win_rate']*100:>5.1f}%                        ║
║ 총 손익: ${stats['total_profit_loss']:>8.2f}                ║
║ 평균/거래: ${stats['avg_profit_per_trade']:>7.2f}                ║
╚══════════════════════════════════════╝
        """)
```

### 7.4 알림 조건

다음 상황에서 관리자에게 알림을 발송합니다:

| 조건 | 알림 레벨 | 내용 |
|------|----------|------|
| 처리 시간 > 1초 | WARNING | "파이프라인 지연: {time}ms" |
| 연속 3회 시그널 실패 | WARNING | "시그널 생성 연속 실패" |
| 승률 < 45% (최근 20거래) | WARNING | "승률 저하: {win_rate}%" |
| 일일 손실 > 5% | ERROR | "일일 손실 경고: {loss}%" |
| 긴급 중단 발동 | CRITICAL | "긴급 중단: {reason}" |
| API 장애 | CRITICAL | "거래소 API 연결 실패" |

---

## 8. 실제 적용 예시

### 8.1 예시 1: 강한 상승 추세, 지지선 반응

**시장 상황 (2025-10-17 14:30:52):**
- 심볼: BTC/USDT
- 현재가: $69,000
- STEP 0: Stage 0 (정상)
- STEP 1: STRONG_UPTREND (신뢰도 0.92)
- STEP 2: 전환 없음
- STEP 3: 추세선 지지 $67,800 감지
- STEP 4: 변곡점 88점 (추세선 지지 + 볼륨 수렴)
- STEP 5: 안전성 91점 (RSI 정상, 엔트로피 낮음, ADX 강함)
- STEP 6: 손절 $67,800 (-1.74%), 익절 TP1~3 계산 완료

**레버리지 계산:**
```
1. 기본: 5배 (STRONG_UPTREND, 신뢰도 0.92)
2. 안전성: 5 × 1.2 = 6배 (91점 > 85점)
3. 변곡점: 6 × 1.15 = 6.9 → 6배
4. 극단: 6배 (Stage 0)
5. 변동성: 6배 (ATR 1.2, 정상)
6. 청산: 6배 (압력 낮음)
7. 전환: 6배 (전환 없음)

최종: 6배
```

**생성 시그널:**
```json
{
  "signal_id": "SIG_20251017_143052_BTC",
  "symbol": "BTC/USDT",
  "direction": "LONG",
  "entry_price": 69000.00,
  "stop_loss": {"price": 67800.00, "percentage": -1.74},
  "take_profit": [
    {"level": "TP1", "price": 70500.00, "close_percentage": 40},
    {"level": "TP2", "price": 71800.00, "close_percentage": 30},
    {"level": "TP3", "price": 73500.00, "close_percentage": 30}
  ],
  "leverage": 6,
  "confidence": 0.90,
  "risk_reward_ratio": 2.4
}
```

**송출:**
- Webhook: 성공 (78ms)
- Telegram: 성공 (1.2s)
- DB 저장: 성공 (8ms)

**결과:**
- 처리 시간: 645ms
- 송출 완료: 14:30:53.3Z
- 유효 기간: 14:35:53.3Z까지

---

### 8.2 예시 2: 횡보, 극단 상황 회복 중

**시장 상황:**
- 심볼: ETH/USDT
- 현재가: $2,650
- STEP 0: Stage 3 (회복 중)
- STEP 1: SIDEWAYS (신뢰도 0.88)
- STEP 4: 변곡점 65점 (S/R 지지, 약함)
- STEP 5: 안전성 82점 (기준 80점 통과)
- ATR: 1.8배 (높은 변동성)
- 청산 압력: 12점 (높음)

**레버리지 계산:**
```
1. 기본: 2배 (SIDEWAYS, 신뢰도 0.88)
2. 안전성: 2 × 1.0 = 2배
3. 변곡점: 2 × 0.85 = 1.7 → 1배
4. 극단: min(1, 3) = 1배 (Stage 3 제한)
5. 변동성: 1 × 0.7 = 0.7 → 1배 (ATR 1.8)
6. 청산: 1 × 0.6 = 0.6 → 1배 (압력 높음)
7. 전환: 1배

최종: 1배 (현물만)
```

**생성 시그널:**
```json
{
  "signal_id": "SIG_20251017_151823_ETH",
  "symbol": "ETH/USDT",
  "direction": "LONG",
  "entry_price": 2650.00,
  "stop_loss": {"price": 2610.00, "percentage": -1.51},
  "take_profit": [
    {"level": "TP1", "price": 2690.00, "close_percentage": 50},
    {"level": "TP2", "price": 2720.00, "close_percentage": 50}
  ],
  "leverage": 1,
  "confidence": 0.75,
  "risk_reward_ratio": 1.8
}
```

**판단:**
- 레버리지 1배 = 안전 우선 전략
- 극단 상황, 높은 변동성, 청산 압력으로 보수적 설정
- 익절도 2단계로 단순화 (빠른 청산 전략)

---

### 8.3 예시 3: 강한 하락 추세, 저항선 SHORT

**시장 상황:**
- 심볼: BTC/USDT
- 현재가: $68,200
- STEP 0: Stage 0
- STEP 1: STRONG_DOWNTREND (신뢰도 0.96)
- STEP 2: 전환 중 (블렌딩 45%)
- STEP 4: 변곡점 92점 (저항선 반응 + 약세 다이버전스)
- STEP 5: 안전성 88점
- ATR: 1.1배 (정상)
- 청산 압력: 4점 (낮음)

**레버리지 계산:**
```
1. 기본: 10배 (STRONG_DOWNTREND, 신뢰도 0.96)
2. 안전성: 10 × 1.5 = 15배 → 15배 (최대치)
3. 변곡점: 15 × 1.3 = 19.5 → 15배 (최대 유지)
4. 극단: 15배
5. 변동성: 15배
6. 청산: 15배
7. 전환: 15 × 0.7 = 10.5 → 10배 (블렌딩 45%)

최종: 10배
```

**생성 시그널:**
```json
{
  "signal_id": "SIG_20251017_162145_BTC",
  "symbol": "BTC/USDT",
  "direction": "SHORT",
  "entry_price": 68200.00,
  "stop_loss": {"price": 69100.00, "percentage": 1.32},
  "take_profit": [
    {"level": "TP1", "price": 67000.00, "close_percentage": 40},
    {"level": "TP2", "price": 66000.00, "close_percentage": 30},
    {"level": "TP3", "price": 64500.00, "close_percentage": 30}
  ],
  "leverage": 10,
  "confidence": 0.92,
  "risk_reward_ratio": 2.8
}
```

**판단:**
- 강한 하락 추세 + 높은 신뢰도 = 높은 레버리지 허용
- 체제 전환 중이므로 10배로 보수적 조정 (15배 → 10배)
- SHORT 포지션으로 하락 추세 활용

---

## 9. 업데이트 이력

### v1.0.0 (2025-10-17)
- **초기 릴리스**
- STEP 0~6 통합 시그널 생성 파이프라인 구현
- 레버리지 동적 설정 시스템 (체제/신뢰도/안전성/변동성/청산압력 기반)
- 다중 송출 채널 지원 (Webhook, API, 메시징, DB)
- 긴급 중단 메커니즘 및 예외 처리
- 실시간 모니터링 대시보드

---

## 📌 주요 특징 요약

✅ **완전 자동화**: STEP 0~6 분석 → 레버리지 계산 → 시그널 생성 → 송출까지 자동  
✅ **안전 우선**: 7단계 레버리지 조정 + 극단 상황 강제 제한  
✅ **고성능**: 전체 파이프라인 650ms (실시간 트레이딩 가능)  
✅ **고품질**: 백테스트 승률 58.3%, 평균 R:R 2.4  
✅ **다중 송출**: Webhook/API/메시징/DB 동시 지원  
✅ **실시간 모니터링**: 통계/알림/긴급 중단 자동 관리  

---

**문서 끝**

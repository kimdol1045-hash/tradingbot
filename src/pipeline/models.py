"""
Pipeline Result Dataclasses — Phase-to-Phase data contracts.
Matches PIPELINE_v5.0.md Section 3 definitions exactly.

All Phase results enforce "complete return" — even early returns must fill all fields.
Optional fields use None defaults; all others must have valid values.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SafetyResult:
    blocked: bool
    stage: str  # 'NORMAL', 'STAGE_3', 'STAGE_2', 'STAGE_1'
    severity: float  # 0~240
    mdd_mode: str  # 'normal','caution','defensive','survival','emergency'
    action: str  # 'ALLOW', 'BLOCK_NEW', 'REDUCE_LEV', 'CLOSE_ALL_AND_HALT'
    reason: str | None = None  # block reason (e.g. 'MDD_EMERGENCY', 'STAGE_1')
    conditions: dict = field(default_factory=dict)
    volatility_override: float | None = None  # ATR divergence replacement
    volatility_alert: bool = False

    def __post_init__(self):
        if self.blocked:
            assert self.reason is not None, "blocked=True requires reason"
            assert self.action in ("BLOCK_NEW", "CLOSE_ALL_AND_HALT")


@dataclass
class RegimeResult:
    regime: str  # 'STRONG_UPTREND','WEAK_UPTREND','SIDEWAYS','WEAK_DOWNTREND','STRONG_DOWNTREND','VOLATILE'
    confidence: float  # 0.0~1.0
    alignment: float = 1.0  # MTF alignment 0.0~1.0 (single TF = 1.0)
    tf_results: dict = field(default_factory=dict)
    in_transition: bool = False
    blend_progress: float = 0.0


@dataclass
class PatternMatch:
    name: str
    direction: str  # 'LONG' or 'SHORT'
    tier: int  # 1, 2, or 3
    score: float
    candle_index: int = -1


@dataclass
class PatternResult:
    candlestick: list[PatternMatch] = field(default_factory=list)
    chart: list[PatternMatch] = field(default_factory=list)
    synergy_bonus: float = 0.0  # capped at 25
    confirmation_names: list[str] = field(default_factory=list)  # always list[str]


@dataclass
class ScanResult:
    found: bool
    primary_type: str | None = None
    score: float = 0.0
    mtf_grade: str = "NONE"  # 'A','B','C','D','F','NONE'
    direction: str | None = None  # 'LONG' or 'SHORT'
    entry_price: float = 0.0
    sr_levels: list = field(default_factory=list)
    trendlines: list = field(default_factory=list)
    vol_profile: dict = field(default_factory=dict)
    patterns: PatternResult = field(default_factory=PatternResult)
    atr: float = 0.0
    pattern_target_atr: float | None = None


@dataclass
class GateResult:
    passed: bool
    reason: str | None = None
    score: float = 0.0
    pass_threshold: float = 0.0
    mdd_mode: str = "normal"
    leverage_mult: float = 1.0
    size_mult: float = 1.0  # MDD + exposure reduction combined
    rolling_pf: float | None = None


@dataclass
class Signal:
    signal_id: str
    agent_id: str
    symbol: str
    direction: str  # 'LONG' or 'SHORT'
    entry_price: float
    stop_loss: float
    take_profits: list = field(default_factory=list)  # [{price, ratio, rr}, ...]
    leverage: float = 1.0
    notional_usd: float = 0.0
    margin_usd: float = 0.0
    regime: str = ""
    confidence: float = 0.0
    inflection_type: str = ""
    inflection_score: float = 0.0
    validation_score: float = 0.0
    mdd_mode: str = "normal"
    pattern_confirmations: list[str] = field(default_factory=list)  # always list[str]
    timestamp: int = 0
    phase_snapshot: dict = field(default_factory=dict)  # per-trade debugging snapshot

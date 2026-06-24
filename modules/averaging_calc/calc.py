"""Averaging math: shares to buy to move a position's P/L% to a target.

Buying x shares at the market price pulls the average cost toward that price, so
the P/L% always shrinks toward 0 — it can never reach 0 or grow past the current
level. The reachable target band is therefore strictly between 0 and current%.
Dollar P/L is unchanged: shares bought at market add no instant gain or loss.

`evaluate` is the single entry point (the API and the page both use it). It raises
ValueError (→ HTTP 400) on malformed input; an unreachable target is not an error —
it returns the position readout with `reachable=False` and `plan=None`.
"""

import math


def _require_finite_positive(**vals: float) -> None:
    for name, v in vals.items():
        if not math.isfinite(v):
            raise ValueError(f"{name} must be a finite number")
        if v <= 0:
            raise ValueError(f"{name} must be positive")


def _require_finite_result(*vals: float) -> None:
    if not all(math.isfinite(v) for v in vals):
        raise ValueError("inputs are too large; the result is not a finite number")


def _strictly_between_zero_and(target: float, current: float) -> bool:
    if current == 0 or target == 0:
        return False
    return (current > 0 and 0 < target < current) or (current < 0 and current < target < 0)


def shares_to_target(qty: float, avg_cost: float, mkt_px: float, target_frac: float) -> float:
    """Shares to buy at mkt_px so the position's P/L fraction becomes target_frac."""
    _require_finite_positive(qty=qty, avg_cost=avg_cost, mkt_px=mkt_px)
    if not math.isfinite(target_frac):
        raise ValueError("target must be a finite number")
    current = mkt_px / avg_cost - 1.0
    if not _strictly_between_zero_and(target_frac, current):
        raise ValueError(
            f"target {target_frac:.4%} is unreachable; it must lie strictly "
            f"between 0% and the current {current:.4%}"
        )
    return qty * (mkt_px - avg_cost * (1.0 + target_frac)) / (mkt_px * target_frac)


def _plan(qty: float, avg_cost: float, mkt_px: float, target_frac: float) -> dict:
    add = shares_to_target(qty, avg_cost, mkt_px, target_frac)
    _require_finite_result(add)  # before math.ceil, which raises OverflowError on inf
    new_avg = (qty * avg_cost + add * mkt_px) / (qty + add)
    whole = math.ceil(add)
    whole_avg = (qty * avg_cost + whole * mkt_px) / (qty + whole)
    plan = {
        "shares_to_buy": add,
        "capital_required": add * mkt_px,
        "new_qty": qty + add,
        "new_avg_cost": new_avg,
        "new_pnl_pct": (mkt_px / new_avg - 1.0) * 100.0,
        "shares_to_buy_whole": whole,
        "whole_new_pnl_pct": (mkt_px / whole_avg - 1.0) * 100.0,
    }
    _require_finite_result(*plan.values())
    return plan


def evaluate(qty: float, avg_cost: float, mkt_px: float, target_pct: float | None = None) -> dict:
    """Position readout, plus the buy plan when a reachable target is given."""
    _require_finite_positive(qty=qty, avg_cost=avg_cost, mkt_px=mkt_px)
    current_pct = (mkt_px / avg_cost - 1.0) * 100.0
    pnl_amount = qty * (mkt_px - avg_cost)
    _require_finite_result(current_pct, pnl_amount)  # extreme finite inputs can overflow
    result = {
        "current_pnl_pct": current_pct,
        "pnl_amount": pnl_amount,
        "reachable_low_pct": min(0.0, current_pct),
        "reachable_high_pct": max(0.0, current_pct),
        "target_pct": target_pct,
        "reachable": False,
        "plan": None,
    }
    if target_pct is None:
        return result
    if not math.isfinite(target_pct):
        raise ValueError("target must be a finite number")
    if _strictly_between_zero_and(target_pct / 100.0, current_pct / 100.0):
        plan = _plan(qty, avg_cost, mkt_px, target_pct / 100.0)
        if plan["shares_to_buy"] > 0:  # guards the target≈current float knife-edge
            result["reachable"] = True
            result["plan"] = plan
    return result

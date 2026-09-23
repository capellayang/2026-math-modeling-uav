"""SOC bookkeeping and the official two-stage equivalent charge duration."""

from math import isfinite


def _check_soc(soc: float) -> None:
    if not isfinite(soc) or not 0.0 <= soc <= 1.0:
        raise ValueError("SOC must be a finite proportion in [0, 1]")


def remaining_soc(usable_energy_kwh: float, consumed_energy_kwh: float,
                  initial_soc: float = 1.0) -> float:
    """Bookkeeping only; does not determine the currently missing flight energy."""
    _check_soc(initial_soc)
    if not isfinite(usable_energy_kwh) or usable_energy_kwh <= 0:
        raise ValueError("Usable energy must be positive")
    if not isfinite(consumed_energy_kwh) or consumed_energy_kwh < 0:
        raise ValueError("Consumed energy must be nonnegative")
    result = initial_soc - consumed_energy_kwh / usable_energy_kwh
    if result < -1e-12:
        raise ValueError("Consumed energy exceeds available charge")
    return max(0.0, result)


def charge_to_full_s(soc: float, full_charge_s: float) -> float:
    """From current SOC to 100%, with break at 90% (P063–P065)."""
    _check_soc(soc)
    if not isfinite(full_charge_s) or full_charge_s < 0:
        raise ValueError("Full-charge duration must be nonnegative")
    if soc < 0.90:
        return full_charge_s * (0.65 * (0.90 - soc) / 0.90 + 0.35)
    return full_charge_s * 0.35 * (1.0 - soc) / 0.10

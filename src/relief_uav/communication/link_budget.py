"""Appendix 3 power budget; MHz, km, dB and dBm remain distinct."""

from math import log10

from relief_uav.data.models import CommunicationParameters, RadioInterface


def receiver_threshold_dbm(params: CommunicationParameters) -> float:
    return params.receiver_sensitivity_dbm + params.fade_margin_db


def directional_limit_db(sender: RadioInterface, receiver: RadioInterface,
                         params: CommunicationParameters) -> float:
    return (sender.transmit_power_dbm + sender.antenna_gain_dbi
            + receiver.antenna_gain_dbi - params.system_loss_db
            - receiver_threshold_dbm(params))


def bidirectional_limit_db(a: RadioInterface, b: RadioInterface,
                           params: CommunicationParameters) -> float:
    return min(directional_limit_db(a, b, params),
               directional_limit_db(b, a, params))


def free_space_loss_db(frequency_mhz: float, distance_3d_m: float) -> float:
    if frequency_mhz <= 0:
        raise ValueError("Frequency must be positive")
    if distance_3d_m < 0:
        raise ValueError("Distance cannot be negative")
    if distance_3d_m == 0:
        return float("-inf")  # Coincident ideal endpoints; no log10(0).
    return 32.45 + 20 * log10(frequency_mhz) + 20 * log10(distance_3d_m / 1000)

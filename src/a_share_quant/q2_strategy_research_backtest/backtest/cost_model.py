from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CostModel:
    commission_bps: float = 1.2
    stamp_tax_bps: float = 5.0
    transfer_fee_bps: float = 0.1
    exchange_handling_bps: float = 0.341
    regulatory_fee_bps: float = 0.2
    min_commission: float = 5.0

    @classmethod
    def from_config(cls, config: dict[str, object]) -> "CostModel":
        return cls(
            commission_bps=float(config.get("commission_bps", 1.2)),
            stamp_tax_bps=float(config.get("stamp_tax_bps", 5.0)),
            transfer_fee_bps=float(config.get("transfer_fee_bps", 0.1)),
            exchange_handling_bps=float(config.get("exchange_handling_bps", 0.341)),
            regulatory_fee_bps=float(config.get("regulatory_fee_bps", 0.2)),
            min_commission=float(config.get("min_commission", 5.0)),
        )

    def calculate(self, notional: float, action: str) -> float:
        commission = max(notional * self.commission_bps / 10_000.0, self.min_commission)
        transfer_fee = notional * self.transfer_fee_bps / 10_000.0
        exchange_handling_fee = notional * self.exchange_handling_bps / 10_000.0
        regulatory_fee = notional * self.regulatory_fee_bps / 10_000.0
        stamp_tax = 0.0
        if action.lower() == "sell":
            stamp_tax = notional * self.stamp_tax_bps / 10_000.0
        return commission + transfer_fee + exchange_handling_fee + regulatory_fee + stamp_tax

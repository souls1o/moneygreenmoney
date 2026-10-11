from __future__ import annotations


class CurrencyCatalog:
    """Display symbols for fiat amounts. Codes stay uppercase ISO-like keys."""

    _SYMBOLS: dict[str, str] = {
        "USD": "$",
        "EUR": "€",
        "GBP": "£",
        "CAD": "CA$",
        "AUD": "A$",
        "INR": "₹",
        "JPY": "¥",
    }

    @classmethod
    def symbol(cls, currency: str) -> str:
        code = currency.strip().upper()
        return cls._SYMBOLS.get(code, f"{code} ")

"""CLI for read-only Exness configuration inspection."""
import json
from pathlib import Path

from .exness_config import read_xauusd_account_configuration


def main() -> None:
    result = read_xauusd_account_configuration()
    output = Path("output/exness_xauusd_config.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    # Avoid printing raw account data or credentials to the terminal.
    safe = {k: v for k, v in result.items() if not k.startswith("raw_")}
    print(json.dumps(safe, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()

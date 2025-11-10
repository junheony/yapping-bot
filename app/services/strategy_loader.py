"""Load yapping strategy metadata and accounts."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import yaml
from loguru import logger

DEFAULT_STRATEGY_PATH = Path("data/yapping_strategies.yaml")


class StrategyLoader:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or DEFAULT_STRATEGY_PATH
        self._strategies: Dict[str, dict] = {}
        self._account_map: Dict[str, dict] = {}
        self.reload()

    def reload(self) -> None:
        if not self.path.exists():
            logger.warning("Strategy file %s not found", self.path)
            self._strategies = {}
            self._account_map = {}
            return

        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        strategies = raw.get("strategies", {})
        account_map: Dict[str, dict] = {}
        for key, payload in strategies.items():
            accounts = [acct.lower().lstrip("@") for acct in payload.get("accounts", [])]
            payload["accounts"] = accounts
            weight = float(payload.get("weight", 1.0))
            payload["weight"] = weight
            for acct in accounts:
                account_map[acct] = {"strategy_key": key, **payload}
        self._strategies = strategies
        self._account_map = account_map
        logger.info("Loaded %s strategies covering %s accounts", len(strategies), len(account_map))

    @property
    def strategies(self) -> Dict[str, dict]:
        return self._strategies

    def get_account_info(self, handle: str) -> Optional[dict]:
        return self._account_map.get(handle.lower().lstrip("@"))

    def get_all_accounts(self) -> List[str]:
        return list(self._account_map.keys())

    def get_weight(self, handle: str) -> float:
        info = self.get_account_info(handle)
        return float(info.get("weight", 0.0)) if info else 0.0


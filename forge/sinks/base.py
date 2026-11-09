from __future__ import annotations

from typing import Any, Protocol


class Sink(Protocol):
    name: str

    async def publish(self, ctx) -> dict[str, Any]: ...

from __future__ import annotations

import logging

from forge.config import Settings
from forge.sinks.base import Sink
from forge.sinks.local import LocalSink

log = logging.getLogger("forge.sinks")


def build_sinks(settings: Settings) -> list[Sink]:
    """Local is always present, even if it was left out of the SINKS list."""
    sinks: list[Sink] = [LocalSink(settings.out_dir)]
    for name in settings.sink_names:
        if name == "local":
            continue
        if name == "gdrive":
            from forge.sinks.gdrive import GoogleDriveSink
            try:
                sinks.append(GoogleDriveSink(
                    settings.gdrive_webapp_url, settings.gdrive_shared_secret
                ))
            except ValueError as exc:
                log.warning("gdrive sink not configured, skipping: %s", exc)
        else:
            log.warning("unknown sink %r in SINKS, ignoring", name)
    return sinks

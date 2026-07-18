"""Register Sentinel Flare's Edge bridge without replacing the existing server."""
from __future__ import annotations

import os


if os.getenv("FLARE_DISABLE_EDGE_INTEGRATION", "false").strip().lower() not in {"1", "true", "yes", "on"}:
    try:
        from fastapi import FastAPI
        from edge_intelligence import register

        _original_init = FastAPI.__init__
        if not getattr(FastAPI.__init__, "_flare_edge_bridge_patch", False):
            def _init_with_edge_bridge(self, *args, **kwargs):
                _original_init(self, *args, **kwargs)
                register(self)

            _init_with_edge_bridge._flare_edge_bridge_patch = True
            FastAPI.__init__ = _init_with_edge_bridge
    except Exception:
        # Do not prevent Flare from starting if optional integration dependencies
        # are damaged. The bridge status tests and deployment health checks expose
        # the failure while the observer remains available.
        pass

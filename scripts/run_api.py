"""Start the Clinical Trial Matcher API with uvicorn."""

import uvicorn
from config.settings import get_settings

if __name__ == "__main__":
    cfg = get_settings()
    uvicorn.run(
        "api.main:app",
        host=cfg.api.host,
        port=cfg.api.port,
        log_level=cfg.api.log_level.lower(),
        reload=True,
    )

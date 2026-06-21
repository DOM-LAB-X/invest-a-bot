from fastapi import FastAPI

from app.api.routes_alerts import router as alerts_router
from app.api.routes_profiles import router as profiles_router

app = FastAPI(title="Invest-A-Bot", version="0.1.0")
app.include_router(alerts_router)
app.include_router(profiles_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

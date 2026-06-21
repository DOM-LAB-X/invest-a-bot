from fastapi import FastAPI

from app.api.routes_alerts import router as alerts_router
from app.api.routes_daily_digests import router as daily_digests_router
from app.api.routes_enrichments import router as enrichments_router
from app.api.routes_notification_deliveries import router as notification_deliveries_router
from app.api.routes_profiles import router as profiles_router

app = FastAPI(title="Invest-A-Bot", version="0.1.0")
app.include_router(alerts_router)
app.include_router(daily_digests_router)
app.include_router(enrichments_router)
app.include_router(notification_deliveries_router)
app.include_router(profiles_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

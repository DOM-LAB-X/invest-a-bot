from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes_alerts import router as alerts_router
from app.api.routes_daily_digests import router as daily_digests_router
from app.api.routes_enrichments import router as enrichments_router
from app.api.routes_notification_deliveries import router as notification_deliveries_router
from app.api.routes_profiles import router as profiles_router
from app.web.routes_alerts import router as web_alerts_router
from app.web.routes_daily_digests import router as web_daily_digests_router
from app.web.routes_profiles import router as web_profiles_router

app = FastAPI(title="Invest-A-Bot", version="0.1.0")
app.include_router(alerts_router)
app.include_router(daily_digests_router)
app.include_router(enrichments_router)
app.include_router(notification_deliveries_router)
app.include_router(profiles_router)
app.include_router(web_alerts_router)
app.include_router(web_daily_digests_router)
app.include_router(web_profiles_router)
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard/profiles")

from fastapi import FastAPI

app = FastAPI(title="Invest-A-Bot", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

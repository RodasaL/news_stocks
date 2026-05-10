from fastapi import FastAPI
from .routes import router

app = FastAPI(title="news_stocks API")
app.include_router(router, prefix="/api")


@app.get("/")
def read_root():
    return {"status": "ok", "msg": "news_stocks backend running"}


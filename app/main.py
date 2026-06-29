from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from app import log as _log
_log.setup()

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.db import init_db
from app.routers.customer import router as customer_router
from app.routers.admin import router as admin_router

app = FastAPI(title="KMB Reservation")
app.mount("/kmb/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


@app.on_event("startup")
def startup() -> None:
    init_db()


app.include_router(customer_router, prefix="/kmb")
app.include_router(admin_router,    prefix="/kmb/admin")


@app.get("/")
def root():
    return RedirectResponse("/kmb/")

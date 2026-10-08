from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.routes import router
from .config import get_settings
from .errors import AppError

app = FastAPI(title="Route Builder", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins, allow_methods=["*"], allow_headers=["*"])
app.include_router(router)


@app.exception_handler(AppError)
async def app_error(_: Request, e: AppError):
    return JSONResponse(status_code=e.status, content={"error": {"code": e.code, "message": e.message}})


@app.exception_handler(RequestValidationError)
async def invalid(_: Request, e: RequestValidationError):
    first = e.errors()[0]
    where = ".".join(str(x) for x in first["loc"][1:])
    return JSONResponse(status_code=422, content={"error": {"code": "invalid_request", "message": f"{where}: {first['msg']}"}})

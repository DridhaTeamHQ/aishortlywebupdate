from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import agents, runs, secrets


app = FastAPI(title="Agent Control API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True}


app.include_router(agents.router)
app.include_router(runs.router)
app.include_router(secrets.router)

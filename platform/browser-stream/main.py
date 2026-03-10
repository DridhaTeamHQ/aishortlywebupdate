from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="Browser Stream Service")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/sessions/{run_id}", response_class=HTMLResponse)
def session_view(run_id: str):
    return f"""
    <html>
      <head>
        <meta charset=\"utf-8\" />
        <title>Live Browser Session {run_id}</title>
        <style>
          body {{ font-family: Arial, sans-serif; margin: 0; background: #10131a; color: #ecf1ff; }}
          .wrap {{ padding: 24px; }}
          .card {{ border: 1px solid #2f3850; border-radius: 12px; padding: 16px; background: #151b2a; }}
        </style>
      </head>
      <body>
        <div class=\"wrap\"> 
          <div class=\"card\">
            <h2>Live Session Placeholder</h2>
            <p>Run ID: <strong>{run_id}</strong></p>
            <p>Connect this service to your remote Playwright provider (Browserless/Playwright server/noVNC) for true live Chromium streaming.</p>
          </div>
        </div>
      </body>
    </html>
    """

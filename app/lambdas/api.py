"""API handler: adapts the FastAPI app to Lambda via Mangum.

Requires `mangum` in dependencies (not yet added — `uv add mangum`).
The Function URL is AuthType NONE; the shared-secret header check lives in the
app (validate against the SSM-provided secret in middleware).
"""

from mangum import Mangum

from app.main import app

handler = Mangum(app, lifespan="off")

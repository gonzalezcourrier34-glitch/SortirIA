"""Point d'entrée de l'API Medical Triage Agent."""

from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from src.api.lifespan import lifespan
from src.api.routes.health import router as health_router
from src.api.routes.triage import router as triage_router
from src.api.routes.version import router as version_router

# Création de l'application FastAPI.
app = FastAPI(
    title="Medical Triage API",
    description="POC d'assistance au triage médical par IA.",
    version="0.1.0",
    lifespan=lifespan,
)


# Métriques HTTP sans données médicales.
REQUEST_COUNT = Counter(
    "triage_http_requests_total",
    "Nombre de requêtes HTTP.",
    ["method", "status"],
)

REQUEST_DURATION = Histogram(
    "triage_http_request_duration_seconds",
    "Durée des requêtes HTTP.",
    ["method"],
)


# Mesure du trafic et de la latence.
@app.middleware("http")
async def prometheus_middleware(request: Request, call_next):
    """Collecte les métriques HTTP sans exposer les données patient."""
    if request.url.path == "/metrics":
        return await call_next(request)

    start = perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        REQUEST_COUNT.labels(request.method, "500").inc()
        raise
    finally:
        REQUEST_DURATION.labels(request.method).observe(perf_counter() - start)

    REQUEST_COUNT.labels(request.method, str(response.status_code)).inc()
    return response


# Exposition des métriques Prometheus.
@app.get("/metrics", include_in_schema=False)
async def metrics():
    """Expose les métriques de supervision."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# Redirection vers Swagger.
@app.get("/", include_in_schema=False)
async def root():
    """Redirige vers la documentation de l'API."""
    return RedirectResponse(url="/docs")


# Enregistrement des routes.
app.include_router(health_router)
app.include_router(version_router)
app.include_router(triage_router)

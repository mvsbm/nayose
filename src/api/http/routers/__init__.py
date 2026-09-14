"""HTTP route assembly for the dashboard API."""

from fastapi import APIRouter

from api.http.routers import (
    admin,
    data_entry,
    debug,
    decisions,
    entities,
    evaluation,
    matching,
    system,
)

api_router = APIRouter()
for route_module in (
    system,
    entities,
    evaluation,
    admin,
    data_entry,
    matching,
    decisions,
    debug,
):
    api_router.include_router(route_module.router)

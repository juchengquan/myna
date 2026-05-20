from fastapi import APIRouter

from myna.api import admin, health

router = APIRouter(prefix="/api")
router.include_router(health.router)
router.include_router(admin.router)

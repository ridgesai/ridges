from fastapi import APIRouter
from utils.database import get_debug_query_info
from utils.debug_lock import get_debug_lock_info



router = APIRouter()



# /debug/lock-info
@router.get("/lock-info")
async def debug_lock_info():
    return get_debug_lock_info()



# /debug/query-info
@router.get("/query-info")
async def debug_query_info():
    return get_debug_query_info()
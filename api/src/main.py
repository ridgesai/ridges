from fastapi import FastAPI, HTTPException
import atexit

from api.src.endpoints.upload import router as upload_router
from api.src.endpoints.retrieval import router as retrieval_router
from api.src.endpoints.agents import router as agents_router
from api.src.db.operations import DatabaseManager

app = FastAPI()

# Include ingestion routes
app.include_router(
    upload_router,
    prefix="/upload",
)

app.include_router(
    retrieval_router,
    prefix="/retrieval",
)

app.include_router(
    agents_router,
    prefix="/agents",
)

@app.get("/health")
async def health_check():
    """Health check endpoint that tests database connectivity."""
    try:
        db = DatabaseManager()
        # Test the connection by getting a simple query
        result = db._execute_with_retry(lambda conn, _: conn.execute("SELECT 1").fetchone(), None)
        pool_stats = db.get_pool_stats()
        return {
            "status": "healthy", 
            "database": "connected",
            "pool_stats": pool_stats
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database connection failed: {str(e)}")

@app.on_event("shutdown")
async def shutdown_event():
    """Gracefully close database connections on shutdown."""
    try:
        db = DatabaseManager()
        db.close()
        print("Database connections closed gracefully")
    except Exception as e:
        print(f"Error closing database connections: {e}")

# Also register the shutdown handler with atexit for additional safety
def cleanup():
    try:
        db = DatabaseManager()
        db.close()
        print("Database connections closed via atexit")
    except Exception as e:
        print(f"Error closing database connections via atexit: {e}")

atexit.register(cleanup)
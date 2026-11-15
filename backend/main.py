"""
FastAPI application entry point.

Startup sequence:
1. Create the FastAPI app.
2. Configure CORS (allow the Vite dev server on port 5173).
3. Register all routes.
4. Seed demo data (runs once when the server starts).

CORS explanation:
- The browser blocks cross-origin requests by default.
- Our frontend (localhost:5173) calls the backend (localhost:8000).
- These are different origins, so we must explicitly allow them.
- In production, you'd lock this down to your actual domain(s).
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.integrations import chat_service, hr_service
from app.repositories import repos
from app.routes import router
from app.seed import seed_data

app = FastAPI(
    title="Snack Counter Automation",
    description=(
        "An internal tool for managing snack counter refills, volunteer coordination, "
        "and expense reimbursement at Framsikt's Oslo and Bengaluru offices."
    ),
    version="1.0.0",
)

# Allow requests from the Vite dev server and any localhost port during development
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")

# Seed demo data on startup
@app.on_event("startup")
def startup_event():
    seed_data(repos, chat_service, hr_service)
    print("✅ Demo data seeded. Backend is ready.")
    print("   Oslo budget:      6,000 NOK")
    print("   Bengaluru budget: 15,000 INR")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

# Smart Attendance Skeleton

Minimal FastAPI + Jinja2 skeleton with session RBAC and FR-01 check-in vertical slice (page → API → MySQL).

## Setup
1) Copy env (optional override):
```bash
cp .env.example .env   # edit DATABASE_URL / SESSION_SECRET if needed
```
2) Start MySQL via docker compose:
```bash
docker compose up -d
```
3) Install deps:
```bash
pip install -r requirements.txt
```
4) Init DB schema + seed users (emp/mgr/admin with hashed passwords):
```bash
python scripts/init_db.py
```
5) Run app:
```bash
uvicorn app.main:app --reload --port 8000
```

## Health checks
- App: GET /health
- DB:  GET /health/db (runs SELECT 1)

## Auth test users
- employee: emp / emp123 → /checkin
- manager:  mgr / mgr123 → /manager/dashboard
- admin:    admin / admin123 → /admin/users

## FR-01 Check-in flow
- Page: GET /checkin (requires employee session). Buttons call /api/checkin.
- API: POST /api/checkin with body `{ "check_type": "IN" | "OUT" }`, records timestamp and lateness (IN after 09:05 is late).

### Quick manual test
```bash
# login (stores cookie)
curl -i -c cookies.txt -X POST -d "username=emp&password=emp123" http://localhost:8000/login
# check-in
curl -b cookies.txt -H "Content-Type: application/json" -d '{"check_type":"IN"}' http://localhost:8000/api/checkin
```
Then open http://localhost:8000/checkin to use the page buttons and view results.

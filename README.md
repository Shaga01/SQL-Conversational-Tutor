# Conversational SQL Tutor (Capstone MVP)

Full-stack, free-to-run SQL tutoring system:
- React frontend with chat + SQL editor + schema + result table
- FastAPI backend with sandboxed SQLite execution (SELECT-only)
- Explanation-first feedback with beginner/intermediate/advanced modes
- Local NL-to-SQL using SQLCoder via Ollama (no paid API required)

## Project Structure

- `frontend/` - React + TypeScript + Vite UI
- `backend/app/main.py` - API routes
- `backend/app/db.py` - SQLite sandbox + seeded sample dataset
- `backend/app/tutor.py` - intent detection, SQLCoder integration, explanations
- `backend/app/sql_rules.py` - SQL safety validation + mistake-aware hints

## What is Implemented

- `POST /api/chat`
  - Natural language to SQL (via local SQLCoder on Ollama)
  - SQL explanation mode if SQL is provided
- `POST /api/execute`
  - Executes only safe `SELECT` queries against local SQLite
- `GET /api/schema`
  - Returns current schema for learning context
- `GET /api/health`
  - Health check

## Free Model Setup (Ollama + SQLCoder)

If Ollama works on your machine:

1. Start Ollama service (or open Ollama app).
2. Pull model:
   - `ollama pull defog/sqlcoder:7b-2`
3. (Optional) test:
   - `ollama run defog/sqlcoder:7b-2 "Write a SQL query to list all customers"`

If Ollama is unavailable, the app still works with a built-in fallback query and full SQL tutoring/execution flow.

## Run Backend

From project root:

1. `python3 -m venv .venv`
2. `source .venv/bin/activate`
3. `pip install -r backend/requirements.txt`
4. `uvicorn backend.app.main:app --reload --port 8000`

## Run Frontend

Open another terminal:

1. `cd frontend`
2. `npm install`
3. `npm run dev`

Frontend runs on `http://localhost:5173` and calls backend at `http://localhost:8000`.

To change backend URL:
- set `VITE_API_BASE` in frontend environment.


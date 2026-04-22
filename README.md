# DataPilot AI

A lightweight BI engine that lets business users upload Excel/CSV sales and
purchase data, ask natural-language questions, and get a structured dashboard
response (KPIs, chart, data table, insights).

The backend is a Flask API. The frontend is plain HTML + CSS + vanilla JS
(with Chart.js from CDN). The analyst engine can run in two modes:

1. **LLM mode** — when `OPENAI_API_KEY` is set, questions are sent to an LLM
   with a strict system prompt and the response is validated against the
   same JSON schema the UI expects.
2. **Mock mode** — when no API key is present, a deterministic pandas-based
   analyst answers the most common questions (revenue/cost/profit/orders,
   monthly trends, top products/customers/vendors, country breakdowns).
   This lets the whole app run end-to-end with no external dependencies.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # optional — only needed for LLM mode
python app.py
```

Then open <http://localhost:5000>.

1. Upload a sales file and (optionally) a purchase file — `.xlsx` or `.csv`.
2. Type a question, e.g. _"What is monthly profit?"_ or _"Top 5 products by
   revenue"_.
3. The dashboard renders KPIs, a chart, a data table, and text insights.

## API

| Method | Path             | Description                                          |
|:------:|:-----------------|:-----------------------------------------------------|
| POST   | `/api/upload`    | Upload one or more files (`sales`, `purchase`).      |
| GET    | `/api/schema`    | Return the cleaned schema + sample rows.             |
| POST   | `/api/ask`       | Ask a question; returns strict analyst JSON.         |
| GET    | `/api/health`    | Liveness probe.                                      |

All `/api/ask` responses follow the contract in
[`analyst.py`](analyst.py) — see `AnalystResponse` — and the UI depends on
it, so the mock engine and the LLM both emit exactly this shape.

## Tests

```bash
pip install -r requirements.txt
python -m pytest
```

Tests cover the mock analyst engine, column cleaning, and the Flask routes.
They do **not** require an `OPENAI_API_KEY`.

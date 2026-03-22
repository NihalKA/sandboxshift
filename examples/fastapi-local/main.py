"""FastAPI local example — simple REST API.

Endpoints:
  GET  /           → health check
  GET  /items      → list items
  POST /items      → create an item
  GET  /items/{id} → get an item by id

All state is in-memory; it resets when the sandbox stops.

-------------------------------------------------------------------------------
Running with YAML config (uses sandboxshift.yaml in this directory):

  sandboxshift run examples/fastapi-local "uvicorn main:app --host 0.0.0.0 --port 8000"

Running with CLI flags only (no YAML needed):

  sandboxshift run examples/fastapi-local \\
    "uvicorn main:app --host 0.0.0.0 --port 8000" \\
    --mode local \\
    --port 8000 \\
    --cpu 1.0 \\
    --memory-mb 512 \\
    --timeout 3600 \\
    --setup "pip install -r requirements.txt" \\
    --allow pypi.org

Once running, open the interactive docs at: http://localhost:8000/docs
Or test with curl:
  curl http://localhost:8000/
  curl -X POST http://localhost:8000/items \\
       -H 'Content-Type: application/json' \\
       -d '{"name": "widget", "price": 9.99}'
  curl http://localhost:8000/items
-------------------------------------------------------------------------------
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="SandboxShift FastAPI example", version="1.0.0")

# In-memory store — resets on sandbox restart.
_items: dict[int, dict] = {}
_next_id: int = 1


class ItemCreate(BaseModel):
    name: str
    price: float


class Item(BaseModel):
    id: int
    name: str
    price: float


@app.get("/")
def health() -> dict:
    return {"status": "ok", "items": len(_items)}


@app.get("/items", response_model=list[Item])
def list_items() -> list[dict]:
    return list(_items.values())


@app.post("/items", response_model=Item, status_code=201)
def create_item(body: ItemCreate) -> dict:
    global _next_id
    item = {"id": _next_id, "name": body.name, "price": body.price}
    _items[_next_id] = item
    _next_id += 1
    return item


@app.get("/items/{item_id}", response_model=Item)
def get_item(item_id: int) -> dict:
    if item_id not in _items:
        raise HTTPException(status_code=404, detail="Item not found")
    return _items[item_id]

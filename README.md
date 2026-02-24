# SAP Portfolio Backend

FastAPI + MySQL backend with 6 symbol management APIs.

---

## 📁 Project Structure

```
sap_portfolio_backend/
├── main.py          ← All API routes (add, list, search, delete, update)
├── models.py        ← MySQL table definition
├── schemas.py       ← Request & Response data shapes
├── database.py      ← MySQL connection config
├── requirements.txt ← All dependencies
└── README.md
```

---

## ✅ Step-by-Step Setup

### Step 1 — Create MySQL Database

Open your MySQL client and run this command:

```sql
CREATE DATABASE sap_portfolio;
```

> ⚠️ The table `symbols` is created **automatically** when you start the server. You don't need to create it manually.

---

### Step 2 — Edit Your DB Credentials

Open `database.py` and change these 3 lines to match your MySQL:

```python
DB_USER = "root"           # your MySQL username
DB_PASSWORD = "yourpassword"  # your MySQL password
DB_HOST = "localhost"
```

---

### Step 3 — Install Dependencies

```bash
pip install -r requirements.txt
```

---

### Step 4 — Start the Server

```bash
uvicorn main:app --reload
```

Server runs at: **http://localhost:8000**

Auto-generated API docs: **http://localhost:8000/docs**

---

## 📡 API Reference

### 1. Add Symbol
```
POST /symbols
```
**Body (JSON):**
```json
{
  "algoid": "ALGO123",
  "triggerType": "momentum",
  "symbolName": "AAPL",
  "assetType": "stock",
  "weight": 0.25,
  "marketProtection": "stop_loss"
}
```

---

### 2. List Symbols (by algoid)
```
GET /symbols?algoid=ALGO123
```
Returns all symbols that exactly match `algoid=ALGO123`.

---

### 3. Search Algoid
```
GET /symbols/search?algoname=algo
```
Returns all records where algoid **contains** the word `algo` (like a search box).

---

### 4. Delete Symbol
```
DELETE /symbols
```
**Body (JSON):**
```json
{
  "algoid": "ALGO123",
  "symbolName": "AAPL"
}
```

---

### 5. Update Symbol
```
PUT /symbols/{symbol_id}
```
Example: `PUT /symbols/1`

**Body (JSON):**
```json
{
  "algoid": "ALGO123",
  "triggerType": "reversal",
  "symbolName": "AAPL",
  "assetType": "stock",
  "weight": 0.35,
  "marketProtection": "trailing_stop"
}
```

---

## 🗄️ MySQL Table Structure (auto-created)

| Column           | Type         | Notes              |
|------------------|--------------|--------------------|
| id               | INT          | Primary key, auto  |
| algoid           | VARCHAR(100) | Index for fast search |
| triggerType      | VARCHAR(100) |                    |
| symbolName       | VARCHAR(100) |                    |
| assetType        | VARCHAR(100) |                    |
| weight           | FLOAT        |                    |
| marketProtection | VARCHAR(100) |                    |

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from typing import Optional

app = FastAPI(title="Northgate Helpdesk Pro")

# Configuración CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        print(f"Error conectando a la BD: {e}")
        return None

# --- MODELOS DE DATOS ---
class Ticket(BaseModel):
    titulo: str
    descripcion: str
    activo_id: Optional[int] = None # Puede ser nulo si no aplica a un activo

class Activo(BaseModel):
    nombre: str
    tipo: str
    serial: str

# --- RUTAS ---

@app.get("/")
def read_root():
    return FileResponse('index.html')

# 1. GET TICKETS
@app.get("/api/tickets")
def get_tickets():
    conn = get_db_connection()
    if not conn: return []
    cur = conn.cursor()
    # Traemos el nombre del activo usando JOIN
    cur.execute("""
        SELECT t.id, t.titulo, t.descripcion, t.estado, a.nombre as activo_nombre 
        FROM tickets t 
        LEFT JOIN activos1 a ON t.activo_id = a.id
        ORDER BY t.id DESC
    """)
    tickets = cur.fetchall()
    cur.close()
    conn.close()
    return tickets

# 2. POST TICKET
@app.post("/api/tickets")
def create_ticket(ticket: Ticket):
    conn = get_db_connection()
    if not conn: raise HTTPException(status_code=500, detail="Error DB")
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO tickets (titulo, descripcion, activo_id) VALUES (%s, %s, %s) RETURNING id",
            (ticket.titulo, ticket.descripcion, ticket.activo_id)
        )
        new_id = cur.fetchone()['id']
        conn.commit()
        return {"id": new_id, "mensaje": "Ticket creado"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

# 3. GET ACTIVOS (Para llenar la lista y el dropdown)
@app.get("/api/activos")
def get_activos():
    conn = get_db_connection()
    if not conn: return []
    cur = conn.cursor()
    cur.execute("SELECT * FROM activos1 ORDER BY id DESC")
    activos = cur.fetchall()
    cur.close()
    conn.close()
    return activos

# 4. POST ACTIVO (Para crear nuevos activos)
@app.post("/api/activos")
def create_activo(activo: Activo):
    conn = get_db_connection()
    if not conn: raise HTTPException(status_code=500, detail="Error DB")
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO activos1 (nombre, tipo, serial) VALUES (%s, %s, %s) RETURNING id",
            (activo.nombre, activo.tipo, activo.serial)
        )
        new_id = cur.fetchone()['id']
        conn.commit()
        return {"id": new_id, "mensaje": "Activo creado"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()
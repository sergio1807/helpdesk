from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from typing import Optional

app = FastAPI(title="Northgate Helpdesk Chat")

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
        print(f"Error BD: {e}")
        return None

# --- MODELOS ---
class Ticket(BaseModel):
    titulo: str
    descripcion: str
    prioridad: str = "media"
    activo_id: Optional[int] = None

class TicketEstado(BaseModel):
    estado: str

class Activo(BaseModel):
    nombre: str
    tipo: str
    serial: str

class Mensaje(BaseModel):
    autor: str
    contenido: str

# --- RUTAS ---

@app.get("/")
def read_root():
    return FileResponse('index.html')

# TICKETS
@app.get("/api/tickets")
def get_tickets():
    conn = get_db_connection()
    if not conn: return []
    cur = conn.cursor()
    cur.execute("""
        SELECT t.*, a.nombre as activo_nombre 
        FROM tickets t 
        LEFT JOIN activos1 a ON t.activo_id = a.id
        ORDER BY CASE WHEN t.estado = 'abierto' THEN 1 ELSE 2 END, t.id DESC
    """)
    tickets = cur.fetchall()
    cur.close()
    conn.close()
    return tickets

@app.post("/api/tickets")
def create_ticket(ticket: Ticket):
    conn = get_db_connection()
    if not conn: raise HTTPException(status_code=500, detail="Error DB")
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO tickets (titulo, descripcion, prioridad, activo_id) VALUES (%s, %s, %s, %s) RETURNING id",
            (ticket.titulo, ticket.descripcion, ticket.prioridad, ticket.activo_id)
        )
        new_id = cur.fetchone()['id']
        conn.commit()
        return {"id": new_id}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

@app.put("/api/tickets/{ticket_id}")
def update_ticket_status(ticket_id: int, estado: TicketEstado):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE tickets SET estado = %s WHERE id = %s", (estado.estado, ticket_id))
    conn.commit()
    cur.close()
    conn.close()
    return {"mensaje": "Actualizado"}

@app.delete("/api/tickets/{ticket_id}")
def delete_ticket(ticket_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM tickets WHERE id = %s", (ticket_id,))
    conn.commit()
    cur.close()
    conn.close()
    return {"mensaje": "Borrado"}

# ACTIVOS
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

@app.post("/api/activos")
def create_activo(activo: Activo):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO activos1 (nombre, tipo, serial) VALUES (%s, %s, %s) RETURNING id",
            (activo.nombre, activo.tipo, activo.serial)
        )
        conn.commit()
        return {"mensaje": "Activo creado"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

@app.delete("/api/activos/{activo_id}")
def delete_activo(activo_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM activos1 WHERE id = %s", (activo_id,))
        conn.commit()
    except Exception:
        raise HTTPException(status_code=400, detail="No se puede borrar activo con tickets")
    finally:
        cur.close()
        conn.close()
    return {"mensaje": "Borrado"}

# --- CHAT / MENSAJES (NUEVO) ---

@app.get("/api/tickets/{ticket_id}/mensajes")
def get_mensajes(ticket_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM mensajes WHERE ticket_id = %s ORDER BY fecha ASC", (ticket_id,))
    mensajes = cur.fetchall()
    cur.close()
    conn.close()
    return mensajes

@app.post("/api/tickets/{ticket_id}/mensajes")
def create_mensaje(ticket_id: int, mensaje: Mensaje):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO mensajes (ticket_id, autor, contenido) VALUES (%s, %s, %s)",
        (ticket_id, mensaje.autor, mensaje.contenido)
    )
    conn.commit()
    cur.close()
    conn.close()
    return {"mensaje": "Enviado"}
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

app = FastAPI(title="Northgate Helpdesk")

# Configuración CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Conexión: La app usará la base de datos definida en esta variable
# Asegúrate que la variable termine en /helpdesk-db
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        print(f"Error conectando a la BD: {e}")
        return None

class Ticket(BaseModel):
    titulo: str
    descripcion: str
    activo_id: int = None

# --- RUTAS ---

@app.get("/")
def read_root():
    return FileResponse('index.html')

@app.get("/api/tickets")
def get_tickets():
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Error de conexión a BD")
    
    cur = conn.cursor()
    # AQUÍ USAMOS ACTIVOS1
    cur.execute("""
        SELECT t.id, t.titulo, t.estado, a.nombre as activo 
        FROM tickets t 
        LEFT JOIN activos1 a ON t.activo_id = a.id
    """)
    tickets = cur.fetchall()
    cur.close()
    conn.close()
    return tickets

@app.post("/api/tickets")
def create_ticket(ticket: Ticket):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Error de conexión a BD")
        
    cur = conn.cursor()
    try:
        # La inserción es en tickets, pero el activo_id referenciará a activos1
        # gracias a la definición de la tabla en SQL.
        cur.execute(
            "INSERT INTO tickets (titulo, descripcion, activo_id) VALUES (%s, %s, %s) RETURNING id",
            (ticket.titulo, ticket.descripcion, ticket.activo_id)
        )
        new_id = cur.fetchone()['id']
        conn.commit()
        return {"id": new_id, "mensaje": "Ticket creado exitosamente"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()
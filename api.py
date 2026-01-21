from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
import os
import pandas as pd
from io import BytesIO
from fastapi.responses import StreamingResponse, FileResponse
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Northgate Helpdesk V6")

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
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
    autor_cambio: str = "Sistema" # Para saber quién lo cambió

class Activo(BaseModel):
    nombre: str
    tipo: str
    serial: str

class Mensaje(BaseModel):
    autor: str
    contenido: str
    tipo: str = "texto" # 'texto', 'imagen', 'evento'

# --- RUTAS PRINCIPALES ---

@app.get("/")
def read_root(): return FileResponse('login.html')

@app.get("/app")
def read_app(): return FileResponse('index.html')

@app.get("/api/export")
def export_tickets():
    conn = get_db_connection()
    if not conn: raise HTTPException(status_code=500, detail="Error DB")
    query = """
        SELECT t.id, t.titulo, t.prioridad, t.estado, t.fecha_creacion, a.nombre as activo
        FROM tickets t LEFT JOIN activos1 a ON t.activo_id = a.id ORDER BY t.id DESC
    """
    df = pd.read_sql(query, conn)
    conn.close()
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    output.seek(0)
    return StreamingResponse(output, headers={"Content-Disposition": "attachment; filename=reporte.xlsx"}, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

# --- RUTAS API ---

@app.get("/api/tickets")
def get_tickets():
    conn = get_db_connection()
    if not conn: return []
    cur = conn.cursor()
    cur.execute("""
        SELECT t.*, a.nombre as activo_nombre 
        FROM tickets t LEFT JOIN activos1 a ON t.activo_id = a.id
        ORDER BY CASE WHEN t.estado = 'abierto' THEN 1 ELSE 2 END, t.id DESC
    """)
    res = cur.fetchall()
    conn.close()
    return res

@app.post("/api/tickets")
def create_ticket(t: Ticket):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO tickets (titulo, descripcion, prioridad, activo_id) VALUES (%s, %s, %s, %s) RETURNING id",
            (t.titulo, t.descripcion, t.prioridad, t.activo_id)
        )
        new_id = cur.fetchone()['id']
        # Auditoría automática: Crear mensaje de sistema
        cur.execute("INSERT INTO mensajes (ticket_id, autor, contenido, tipo) VALUES (%s, %s, %s, 'evento')", 
                   (new_id, 'Sistema', f'Ticket creado con prioridad {t.prioridad}'))
        conn.commit()
        return {"id": new_id}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()

@app.put("/api/tickets/{id}")
def update_status(id: int, st: TicketEstado):
    conn = get_db_connection()
    cur = conn.cursor()
    # 1. Actualizar estado
    cur.execute("UPDATE tickets SET estado = %s WHERE id = %s", (st.estado, id))
    # 2. Insertar evento en el chat automáticamente
    msg = f"Cambió el estado a: {st.estado.upper()}"
    cur.execute("INSERT INTO mensajes (ticket_id, autor, contenido, tipo) VALUES (%s, %s, %s, 'evento')", 
               (id, st.autor_cambio, msg))
    conn.commit()
    conn.close()
    return {"msg": "OK"}

@app.delete("/api/tickets/{id}")
def delete_ticket(id: int):
    conn = get_db_connection()
    conn.cursor().execute("DELETE FROM tickets WHERE id = %s", (id,))
    conn.commit()
    conn.close()
    return {"msg": "Borrado"}

@app.get("/api/activos")
def get_activos():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM activos1 ORDER BY id DESC")
    res = cur.fetchall()
    conn.close()
    return res

@app.post("/api/activos")
def create_activo(a: Activo):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO activos1 (nombre, tipo, serial) VALUES (%s, %s, %s)", (a.nombre, a.tipo, a.serial))
    conn.commit()
    conn.close()
    return {"msg": "OK"}

@app.delete("/api/activos/{id}")
def delete_activo(id: int):
    conn = get_db_connection()
    try:
        conn.cursor().execute("DELETE FROM activos1 WHERE id = %s", (id,))
        conn.commit()
    except:
        raise HTTPException(400, "Tiene tickets asociados")
    conn.close()
    return {"msg": "OK"}

# --- CHAT MULTIMEDIA ---
@app.get("/api/tickets/{id}/mensajes")
def get_msgs(id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM mensajes WHERE ticket_id = %s ORDER BY fecha ASC", (id,))
    res = cur.fetchall()
    conn.close()
    return res

@app.post("/api/tickets/{id}/mensajes")
def send_msg(id: int, m: Mensaje):
    conn = get_db_connection()
    cur = conn.cursor()
    # Ahora guardamos también el 'tipo' (texto, imagen, evento)
    cur.execute("INSERT INTO mensajes (ticket_id, autor, contenido, tipo) VALUES (%s, %s, %s, %s)", 
               (id, m.autor, m.contenido, m.tipo))
    conn.commit()
    conn.close()
    return {"msg": "OK"}
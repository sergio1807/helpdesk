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

app = FastAPI(title="Northgate Helpdesk V7 RBAC")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    try:
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    except Exception as e:
        print(f"Error BD: {e}")
        return None

# --- MODELOS ---
class LoginReq(BaseModel):
    email: str
    password: str

class Ticket(BaseModel):
    titulo: str
    descripcion: str
    prioridad: str = "media"
    activo_id: Optional[int] = None
    usuario_id: int # ID del creador

class TicketEstado(BaseModel):
    estado: str
    usuario_id: int # Quién hace el cambio

class Activo(BaseModel):
    nombre: str
    tipo: str
    serial: str

class Mensaje(BaseModel):
    usuario_id: int
    contenido: str
    tipo: str = "texto"

# --- RUTAS ---

@app.get("/")
def read_root(): return FileResponse('login.html')

@app.get("/app")
def read_app(): return FileResponse('index.html')

# LOGIN REAL
@app.post("/api/login")
def login(creds: LoginReq):
    conn = get_db_connection()
    cur = conn.cursor()
    # Buscamos usuario y contraseña (texto plano para este ejemplo)
    cur.execute("SELECT id, nombre, email, rol FROM usuarios WHERE email = %s AND password = %s", (creds.email, creds.password))
    user = cur.fetchone()
    conn.close()
    
    if user:
        return user # Devuelve el objeto usuario completo con su rol
    raise HTTPException(status_code=401, detail="Credenciales incorrectas")

# TICKETS (Filtrado inteligente)
@app.get("/api/tickets")
def get_tickets(user_id: int = None, rol: str = None):
    conn = get_db_connection()
    cur = conn.cursor()
    
    sql = """
        SELECT t.*, a.nombre as activo_nombre, u.nombre as creador_nombre
        FROM tickets t 
        LEFT JOIN activos1 a ON t.activo_id = a.id
        LEFT JOIN usuarios u ON t.creador_id = u.id
    """
    
    # Si es usuario normal, SOLO ve sus tickets
    if rol == 'usuario' and user_id:
        sql += f" WHERE t.creador_id = {user_id}"
        
    sql += " ORDER BY CASE WHEN t.estado = 'abierto' THEN 1 ELSE 2 END, t.id DESC"
    
    cur.execute(sql)
    res = cur.fetchall()
    conn.close()
    return res

@app.post("/api/tickets")
def create_ticket(t: Ticket):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Insertar ticket vinculado al usuario
        cur.execute(
            "INSERT INTO tickets (titulo, descripcion, prioridad, activo_id, creador_id) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (t.titulo, t.descripcion, t.prioridad, t.activo_id, t.usuario_id)
        )
        new_id = cur.fetchone()['id']
        # Mensaje de sistema
        cur.execute("INSERT INTO mensajes (ticket_id, autor_nombre, contenido, tipo) VALUES (%s, 'Sistema', %s, 'evento')", 
                   (new_id, f'Ticket creado con prioridad {t.prioridad}'))
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
    # Obtenemos nombre del usuario para la auditoría
    cur.execute("SELECT nombre FROM usuarios WHERE id = %s", (st.usuario_id,))
    autor = cur.fetchone()['nombre']
    
    cur.execute("UPDATE tickets SET estado = %s WHERE id = %s", (st.estado, id))
    cur.execute("INSERT INTO mensajes (ticket_id, autor_nombre, contenido, tipo) VALUES (%s, 'Sistema', %s, 'evento')", 
               (id, f'{autor} cambió el estado a: {st.estado.upper()}'))
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

# ACTIVOS Y EXCEL (Solo Admins/Tecnicos en frontend, backend abierto por simplicidad)
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

@app.get("/api/export")
def export_tickets():
    conn = get_db_connection()
    df = pd.read_sql("SELECT t.id, t.titulo, t.prioridad, t.estado, u.nombre as creador FROM tickets t JOIN usuarios u ON t.creador_id = u.id ORDER BY t.id DESC", conn)
    conn.close()
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer: df.to_excel(writer, index=False)
    output.seek(0)
    return StreamingResponse(output, headers={"Content-Disposition": "attachment; filename=reporte.xlsx"}, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

# CHAT AUTOMÁTICO
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
    # Buscamos el nombre real del usuario
    cur.execute("SELECT nombre FROM usuarios WHERE id = %s", (m.usuario_id,))
    nombre = cur.fetchone()['nombre']
    
    cur.execute("INSERT INTO mensajes (ticket_id, autor_id, autor_nombre, contenido, tipo) VALUES (%s, %s, %s, %s, %s)", 
               (id, m.usuario_id, nombre, m.contenido, m.tipo))
    conn.commit()
    conn.close()
    return {"msg": "OK"}
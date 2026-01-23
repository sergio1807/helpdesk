from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
import os
import pandas as pd
from io import BytesIO
from fastapi.responses import StreamingResponse, FileResponse
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

app = FastAPI(title="Northgate Helpdesk V11")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

DATABASE_URL = os.getenv("DATABASE_URL")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER", "tu_correo@gmail.com")
SMTP_PASS = os.getenv("SMTP_PASS", "tu_pass")

def get_db_connection():
    try:
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    except Exception as e:
        print(f"Error BD: {e}")
        return None

def enviar_notificacion(destinatario: str, asunto: str, cuerpo: str):
    try:
        if "tu_correo" in SMTP_USER: return 
        msg = MIMEMultipart()
        msg['From'] = SMTP_USER
        msg['To'] = destinatario
        msg['Subject'] = f"[Northgate] {asunto}"
        msg.attach(MIMEText(cuerpo, 'plain'))
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
        server.quit()
    except Exception as e:
        print(f"Error Email: {e}")

# --- MODELOS ---
class LoginReq(BaseModel):
    email: str
    password: str

class Ticket(BaseModel):
    titulo: str
    descripcion: str
    prioridad: str = "media"
    activo_id: Optional[int] = None
    usuario_id: int 

class TicketEstado(BaseModel):
    estado: str
    usuario_id: int
    valoracion: Optional[int] = 0 # NUEVO: Estrellas (1-5)

class Activo(BaseModel):
    nombre: str
    tipo: str
    serial: str

class Mensaje(BaseModel):
    usuario_id: int
    contenido: str
    tipo: str = "texto"

class FAQ(BaseModel): # NUEVO MODELO
    titulo: str
    contenido: str
    categoria: str
    usuario_id: int

# --- RUTAS ---
@app.get("/")
def read_root(): return FileResponse('login.html')

@app.get("/app")
def read_app(): return FileResponse('index.html')

@app.post("/api/login")
def login(creds: LoginReq):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, nombre, email, rol FROM usuarios WHERE email = %s AND password = %s", (creds.email, creds.password))
    user = cur.fetchone()
    conn.close()
    if user: return user
    raise HTTPException(401, "Credenciales incorrectas")

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
    if rol == 'usuario' and user_id: sql += f" WHERE t.creador_id = {user_id}"
    sql += " ORDER BY t.fecha_limite ASC"
    cur.execute(sql)
    res = cur.fetchall()
    conn.close()
    return res

@app.post("/api/tickets")
def create_ticket(t: Ticket, background_tasks: BackgroundTasks):
    conn = get_db_connection()
    cur = conn.cursor()
    ahora = datetime.now()
    if t.prioridad == 'alta': limite = ahora + timedelta(hours=24)
    elif t.prioridad == 'media': limite = ahora + timedelta(hours=72)
    else: limite = ahora + timedelta(days=7)

    try:
        cur.execute(
            "INSERT INTO tickets (titulo, descripcion, prioridad, activo_id, creador_id, fecha_limite) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (t.titulo, t.descripcion, t.prioridad, t.activo_id, t.usuario_id, limite)
        )
        new_id = cur.fetchone()['id']
        cur.execute("INSERT INTO mensajes (ticket_id, autor_nombre, contenido, tipo) VALUES (%s, 'Sistema', %s, 'evento')", 
                   (new_id, f'Ticket creado. Resolución esperada antes de: {limite.strftime("%d/%m %H:%M")}'))
        conn.commit()
        background_tasks.add_task(enviar_notificacion, "admin@ng.com", f"Nuevo Ticket #{new_id}", f"Prioridad: {t.prioridad}")
        return {"id": new_id}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()

@app.put("/api/tickets/{id}")
def update_status(id: int, st: TicketEstado, background_tasks: BackgroundTasks):
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Actualizar estado y valoración si viene
    if st.valoracion > 0:
        cur.execute("UPDATE tickets SET valoracion = %s WHERE id = %s", (st.valoracion, id))
        # Mensaje de satisfacción
        msg = f"⭐ El usuario calificó el servicio con {st.valoracion} estrellas"
        cur.execute("INSERT INTO mensajes (ticket_id, autor_nombre, contenido, tipo) VALUES (%s, 'Sistema', %s, 'evento')", (id, msg))
    
    if st.estado:
        cur.execute("SELECT nombre FROM usuarios WHERE id = %s", (st.usuario_id,))
        autor = cur.fetchone()['nombre']
        cur.execute("UPDATE tickets SET estado = %s WHERE id = %s", (st.estado, id))
        msg = f'{autor} cambió el estado a: {st.estado.replace("_", " ").upper()}'
        cur.execute("INSERT INTO mensajes (ticket_id, autor_nombre, contenido, tipo) VALUES (%s, 'Sistema', %s, 'evento')", (id, msg))

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

# --- NUEVAS RUTAS DE FAQ ---
@app.get("/api/faqs")
def get_faqs():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT f.*, u.nombre as autor FROM faqs f JOIN usuarios u ON f.autor_id = u.id ORDER BY f.id DESC")
    res = cur.fetchall()
    conn.close()
    return res

@app.post("/api/faqs")
def create_faq(f: FAQ):
    conn = get_db_connection()
    conn.cursor().execute("INSERT INTO faqs (titulo, contenido, categoria, autor_id) VALUES (%s, %s, %s, %s)", 
                         (f.titulo, f.contenido, f.categoria, f.usuario_id))
    conn.commit()
    conn.close()
    return {"msg": "OK"}
    
@app.delete("/api/faqs/{id}")
def delete_faq(id: int):
    conn = get_db_connection()
    conn.cursor().execute("DELETE FROM faqs WHERE id = %s", (id,))
    conn.commit()
    conn.close()
    return {"msg": "Borrado"}

# ... RESTO DE RUTAS IGUALES ...
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
    conn.cursor().execute("INSERT INTO activos1 (nombre, tipo, serial) VALUES (%s, %s, %s)", (a.nombre, a.tipo, a.serial))
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
        raise HTTPException(400, "En uso")
    conn.close()
    return {"msg": "OK"}

@app.get("/api/export")
def export_tickets():
    conn = get_db_connection()
    df = pd.read_sql("SELECT t.id, t.titulo, t.prioridad, t.estado, t.valoracion, t.fecha_limite, u.nombre as creador FROM tickets t JOIN usuarios u ON t.creador_id = u.id", conn)
    conn.close()
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer: df.to_excel(writer, index=False)
    output.seek(0)
    return StreamingResponse(output, headers={"Content-Disposition": "attachment; filename=reporte.xlsx"}, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

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
    cur.execute("SELECT nombre FROM usuarios WHERE id = %s", (m.usuario_id,))
    nombre = cur.fetchone()['nombre']
    cur.execute("INSERT INTO mensajes (ticket_id, autor_id, autor_nombre, contenido, tipo) VALUES (%s, %s, %s, %s, %s)", 
               (id, m.usuario_id, nombre, m.contenido, m.tipo))
    conn.commit()
    conn.close()
    return {"msg": "OK"}
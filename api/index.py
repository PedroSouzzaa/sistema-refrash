import os
import io
import csv
import pandas as pd
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, make_response
import psycopg2
from psycopg2.extras import RealDictCursor

# PDF
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

app = Flask(__name__, template_folder='../templates')
ADMIN_PASS = os.environ.get('ADMIN_PASSWORD', 'admin123')

def get_db_connection():
    return psycopg2.connect(os.environ.get('POSTGRES_URL'))

# Garante que as colunas novas existam
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY,
            nome TEXT NOT NULL, sobrenome TEXT,
            usuario_login TEXT UNIQUE NOT NULL,
            turno TEXT, portaria TEXT, empresa TEXT, sede TEXT,
            codigo_atual TEXT
        );
        CREATE TABLE IF NOT EXISTS logs (
            id SERIAL PRIMARY KEY,
            colaborador TEXT,
            data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT
        );
    ''')
    conn.commit()
    cur.close()
    conn.close()

init_db()

def processar_ausencias():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT usuario_login FROM usuarios")
    usuarios = cur.fetchall()
    agora = datetime.now()
    
    for u in usuarios:
        cur.execute("SELECT data_hora FROM logs WHERE colaborador = %s ORDER BY data_hora DESC LIMIT 1", (u['usuario_login'],))
        ultimo = cur.fetchone()
        if not ultimo or (agora - ultimo['data_hora']) > timedelta(minutes=65):
            cur.execute('''
                INSERT INTO logs (colaborador, status, data_hora)
                SELECT %s, 'Ausência de registro', %s
                WHERE NOT EXISTS (
                    SELECT 1 FROM logs WHERE colaborador = %s 
                    AND status = 'Ausência de registro' AND data_hora > %s
                )
            ''', (u['usuario_login'], agora, u['usuario_login'], agora - timedelta(minutes=55)))
    conn.commit()
    cur.close()
    conn.close()

@app.route('/')
def index(): return render_template('index.html')

@app.route('/login')
def login_page(): return render_template('login.html')

@app.route('/admin')
def admin_page():
    if request.cookies.get('auth_admin') != ADMIN_PASS:
        return render_template('login.html', erro="Acesso Negado")
    return render_template('admin.html')

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    if data.get('user') == "admin" and data.get('password') == ADMIN_PASS:
        resp = make_response(jsonify({"status": "sucesso"}))
        resp.set_cookie('auth_admin', ADMIN_PASS, httponly=True)
        return resp
    return jsonify({"status": "erro"}), 401

@app.route('/admin/status_usuarios')
def status_usuarios():
    if request.cookies.get('auth_admin') != ADMIN_PASS: return jsonify([]), 403
    processar_ausencias()
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('''
        SELECT u.*, MAX(l.data_hora) as ultimo_registro,
        (SELECT status FROM logs WHERE colaborador = u.usuario_login ORDER BY data_hora DESC LIMIT 1) as ultimo_status
        FROM usuarios u LEFT JOIN logs l ON u.usuario_login = l.colaborador
        GROUP BY u.id ORDER BY u.nome ASC
    ''')
    dados = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(dados)

@app.route('/admin/exportar/excel')
def exportar_excel():
    if request.cookies.get('auth_admin') != ADMIN_PASS: return "Negado", 403
    processar_ausencias()
    conn = get_db_connection()
    df = pd.read_sql('''
        SELECT l.data_hora as "Data", u.nome as "Nome", u.empresa as "Empresa", 
        u.sede as "Sede", u.turno as "Turno", l.status as "Status"
        FROM logs l JOIN usuarios u ON l.colaborador = u.usuario_login
        ORDER BY l.data_hora DESC
    ''', conn)
    conn.close()
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    
    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=relatorio.xlsx"
    response.headers["Content-type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return response

@app.route('/admin/exportar/pdf')
def exportar_pdf():
    if request.cookies.get('auth_admin') != ADMIN_PASS: return "Negado", 403
    processar_ausencias()
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('''
        SELECT l.data_hora, u.nome, u.empresa, u.turno, l.status 
        FROM logs l JOIN usuarios u ON l.colaborador = u.usuario_login ORDER BY l.data_hora DESC
    ''')
    dados = cur.fetchall()
    conn.close()

    output = io.BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()
    elements.append(Paragraph("Relatório Refresh NBL", styles['Title']))
    
    table_data = [['Data', 'Nome', 'Empresa', 'Turno', 'Status']]
    for d in dados:
        table_data.append([d['data_hora'].strftime('%d/%m %H:%M'), d['nome'], d['empresa'], d['turno'], d['status']])
    
    t = Table(table_data)
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.grey),('GRID',(0,0),(-1,-1),0.5,colors.black)]))
    elements.append(t)
    doc.build(elements)
    
    response = make_response(output.getvalue())
    response.headers["Content-type"] = "application/pdf"
    return response

# Mantenha as rotas de validar e salvar usuário iguais às anteriores...

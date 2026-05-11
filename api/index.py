import os
import io
import pandas as pd
from datetime import datetime
from flask import Flask, render_template, request, jsonify, make_response
import psycopg2
from psycopg2.extras import RealDictCursor

# Bibliotecas para PDF e Excel
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

app = Flask(__name__, template_folder='../templates')
ADMIN_PASS = os.environ.get('ADMIN_PASSWORD', 'admin123')

def get_db_connection():
    return psycopg2.connect(os.environ.get('POSTGRES_URL'))

# --- NAVEGAÇÃO ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/login')
def login_page(): return render_template('login.html')

@app.route('/admin')
def admin_page():
    if request.cookies.get('auth_admin') != ADMIN_PASS: return render_template('login.html')
    return render_template('admin.html')

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    if data.get('user', '').lower() == "admin" and data.get('password') == ADMIN_PASS:
        resp = make_response(jsonify({"status": "sucesso"}))
        resp.set_cookie('auth_admin', ADMIN_PASS, httponly=True, samesite='Lax')
        return resp
    return jsonify({"status": "erro"}), 401

# --- GESTÃO DE USUÁRIOS (LISTAR, SALVAR, EXCLUIR) ---

@app.route('/admin/usuarios/listar')
def listar_usuarios():
    if request.cookies.get('auth_admin') != ADMIN_PASS: return jsonify([]), 403
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT nome, sobrenome, usuario_login, empresa, sede FROM usuarios ORDER BY nome ASC')
    usuarios = cur.fetchall()
    conn.close()
    return jsonify(usuarios)

@app.route('/admin/usuarios/salvar', methods=['POST'])
def salvar_usuario():
    if request.cookies.get('auth_admin') != ADMIN_PASS: return jsonify({"erro": "Não autorizado"}), 403
    data = request.json
    conn = get_db_connection()
    cur = conn.cursor()
    query = '''
        INSERT INTO usuarios (nome, sobrenome, usuario_login, codigo_atual, empresa, sede)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (usuario_login) DO UPDATE SET
        nome = EXCLUDED.nome, sobrenome = EXCLUDED.sobrenome, 
        codigo_atual = EXCLUDED.codigo_atual, empresa = EXCLUDED.empresa, sede = EXCLUDED.sede
    '''
    cur.execute(query, (data['nome'], data['sobrenome'], data['usuario_login'], 
                        data['codigo_atual'], data['empresa'], data['sede']))
    conn.commit()
    cur.close(); conn.close()
    return jsonify({"status": "sucesso"})

@app.route('/admin/usuarios/excluir/<login>', methods=['DELETE'])
def excluir_usuario(login):
    if request.cookies.get('auth_admin') != ADMIN_PASS: return jsonify({"erro": "Não autorizado"}), 403
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('DELETE FROM usuarios WHERE usuario_login = %s', (login,))
    conn.commit()
    conn.close()
    return jsonify({"status": "sucesso"})

# --- VALIDAÇÃO (CHECK-IN) ---
@app.route('/colaborador/validar', methods=['POST'])
def validar():
    data = request.json
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT codigo_atual FROM usuarios WHERE usuario_login = %s', (data['usuario'].lower(),))
    user = cur.fetchone()
    if user and user['codigo_atual'] == data['codigo']:
        cur.execute("INSERT INTO logs (colaborador, status, turno_registro, portaria) VALUES (%s, 'Verificado', %s, %s)", 
                   (data['usuario'].lower(), data['turno'], data.get('portaria', 'P1')))
        conn.commit()
        conn.close()
        return jsonify({"status": "sucesso", "msg": "✅ Presença confirmada!"})
    conn.close()
    return jsonify({"status": "erro", "msg": "❌ ID ou Código incorretos"}), 401

# --- MONITORAMENTO ---
@app.route('/admin/status_realtime')
def status_realtime():
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute('''
            SELECT u.nome || ' ' || u.sobrenome as nome, u.empresa, u.sede, l.portaria, l.turno_registro, 
                   to_char(l.data_hora, 'HH24:MI:SS') as hora
            FROM logs l
            JOIN usuarios u ON l.colaborador = u.usuario_login
            WHERE l.data_hora >= CURRENT_DATE
            ORDER BY l.data_hora DESC
        ''')
        logs = cur.fetchall()
        conn.close()
        return jsonify(logs)
    except: return jsonify([])

# --- EXPORTAR ---
@app.route('/admin/exportar/<formato>')
def exportar_relatorio(formato):
    if request.cookies.get('auth_admin') != ADMIN_PASS: return "Acesso negado", 403
    inicio = request.args.get('inicio'); fim = request.args.get('fim'); turno = request.args.get('turno')
    conn = get_db_connection()
    query = "SELECT l.data_hora, u.nome || ' ' || u.sobrenome as nome, u.empresa, l.portaria, l.turno_registro FROM logs l JOIN usuarios u ON l.colaborador = u.usuario_login WHERE 1=1"
    params = []
    if inicio and fim: query += " AND l.data_hora BETWEEN %s AND %s"; params.extend([inicio, fim])
    if turno and turno != 'Todos': query += " AND l.turno_registro LIKE %s"; params.append(f"%{turno}%")
    df = pd.read_sql(query, conn, params=params)
    conn.close()
    if formato == 'excel':
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer: df.to_excel(writer, index=False)
        resp = make_response(output.getvalue())
        resp.headers["Content-Disposition"] = "attachment; filename=relatorio.xlsx"
        resp.headers["Content-type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return resp
    output = io.BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = [Paragraph("Relatório Refresh", styles['Title']), Spacer(1, 12)]
    dados = [df.columns.to_list()] + df.values.tolist()
    t = Table(dados)
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.grey),('GRID',(0,0),(-1,-1),0.5,colors.black)]))
    elements.append(t)
    doc.build(elements)
    resp = make_response(output.getvalue())
    resp.headers["Content-type"] = "application/pdf"
    return resp

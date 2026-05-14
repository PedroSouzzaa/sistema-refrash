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

# --- CONFIGURAÇÃO DE CAMINHO ABSOLUTO ---
base_dir = os.path.dirname(os.path.abspath(__file__))
template_path = os.path.join(base_dir, 'templates')

app = Flask(__name__, template_folder=template_path)
ADMIN_PASS = os.environ.get('ADMIN_PASSWORD', 'admin123')

def get_db_connection():
    return psycopg2.connect(os.environ.get('POSTGRES_URL'))

@app.route('/')
def index(): 
    return render_template('index.html')

@app.route('/admin')
def admin_page():
    if request.cookies.get('auth_admin') != ADMIN_PASS:
        return render_template('login.html')
    return render_template('admin.html')

@app.route('/admin/usuarios')
def pagina_usuarios():
    if request.cookies.get('auth_admin') != ADMIN_PASS:
        return render_template('login.html')
    return render_template('usuarios.html')

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    if data.get('user', '').lower() == "admin" and data.get('password') == ADMIN_PASS:
        resp = make_response(jsonify({"status": "ok"}))
        resp.set_cookie('auth_admin', ADMIN_PASS, httponly=True)
        return resp
    return jsonify({"status": "erro"}), 401

# --- CRUD DE USUÁRIOS ---
@app.route('/api/usuarios/listar')
def api_listar_usuarios():
    if request.cookies.get('auth_admin') != ADMIN_PASS: return jsonify([]), 401
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT nome, sobrenome, usuario_login, codigo_acesso, empresa, sede FROM usuarios ORDER BY nome ASC")
    usuarios = cur.fetchall()
    conn.close()
    return jsonify(usuarios)

@app.route('/api/usuarios/salvar', methods=['POST'])
def api_salvar_usuario():
    if request.cookies.get('auth_admin') != ADMIN_PASS: return jsonify({"status": "erro"}), 401
    data = request.json
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO usuarios (nome, sobrenome, usuario_login, codigo_acesso, empresa, sede)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (usuario_login) DO UPDATE SET
            nome=EXCLUDED.nome, sobrenome=EXCLUDED.sobrenome, 
            codigo_acesso=EXCLUDED.codigo_acesso, empresa=EXCLUDED.empresa, sede=EXCLUDED.sede
        """, (data['nome'], data['sobrenome'], data['usuario_login'], data['codigo_acesso'], data['empresa'], data['sede']))
        conn.commit()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "erro", "msg": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/usuarios/excluir/<login>', methods=['DELETE'])
def api_excluir_usuario(login):
    if request.cookies.get('auth_admin') != ADMIN_PASS: return jsonify({"status": "erro"}), 401
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM usuarios WHERE usuario_login = %s", (login,))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})

# --- MONITORAMENTO E EXPORTAÇÃO ---
@app.route('/admin/status_realtime')
def status_realtime():
    if request.cookies.get('auth_admin') != ADMIN_PASS: return jsonify([]), 401
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT u.nome, u.sobrenome, u.empresa, l.portaria, 
               TO_CHAR(l.data_hora, 'HH24:MI') as hora
        FROM logs l 
        JOIN usuarios u ON l.colaborador = u.usuario_login 
        ORDER BY l.data_hora DESC LIMIT 20
    """)
    logs = cur.fetchall()
    conn.close()
    return jsonify(logs)

@app.route('/admin/exportar/<formato>')
def exportar(formato):
    if request.cookies.get('auth_admin') != ADMIN_PASS: return "Acesso negado", 401
    inicio = request.args.get('inicio')
    fim = request.args.get('fim')
    turno = request.args.get('turno', 'Todos')
    
    conn = get_db_connection()
    query = """
        SELECT u.nome || ' ' || u.sobrenome as "Colaborador", u.empresa as "Empresa",
               l.portaria as "Portaria", l.turno as "Turno", 
               TO_CHAR(l.data_hora, 'DD/MM/YYYY HH24:MI') as "Data_Hora"
        FROM logs l JOIN usuarios u ON l.colaborador = u.usuario_login 
        WHERE 1=1
    """
    params = []
    if inicio and fim:
        query += " AND l.data_hora::date BETWEEN %s AND %s"
        params.extend([inicio, fim])
    if turno and turno != 'Todos':
        query += " AND l.turno = %s"
        params.append(turno)
    
    query += " ORDER BY l.data_hora DESC"
    df = pd.read_sql(query, conn, params=params)
    conn.close()

    output = io.BytesIO()
    if formato == 'excel':
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        resp = make_response(output.getvalue())
        resp.headers["Content-Disposition"] = "attachment; filename=relatorio_nbl.xlsx"
        resp.headers["Content-type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return resp
    
    doc = SimpleDocTemplate(output, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = [Paragraph("Relatório de Batidas - NBL LOG", styles['Title']), Spacer(1, 12)]
    t = Table([df.columns.to_list()] + df.values.tolist())
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#002855')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    elements.append(t)
    doc.build(elements)
    resp = make_response(output.getvalue())
    resp.headers["Content-Disposition"] = "attachment; filename=relatorio_nbl.pdf"
    resp.headers["Content-type"] = "application/pdf"
    return resp

if __name__ == '__main__':
    app.run(debug=True)

import os
import io
import pandas as pd
from datetime import datetime
from flask import Flask, render_template, request, jsonify, make_response
import psycopg2
from psycopg2.extras import RealDictCursor

base_dir = os.path.dirname(os.path.abspath(__file__))
template_path = os.path.join(base_dir, 'templates')

app = Flask(__name__, template_folder=template_path)
ADMIN_PASS = os.environ.get('ADMIN_PASSWORD', 'admin123')

def get_db_connection():
    return psycopg2.connect(os.environ.get('POSTGRES_URL'))

# --- ROTAS DE NAVEGAÇÃO ---
@app.route('/')
def index(): 
    return render_template('index.html')

@app.route('/admin')
def admin_page():
    if request.cookies.get('auth_admin') != ADMIN_PASS:
        return render_template('login.html')
    return render_template('admin.html')

@app.route('/admin/monitoramento')
def monitoramento_page():
    if request.cookies.get('auth_admin') != ADMIN_PASS:
        return render_template('login.html')
    return render_template('monitoramento.html')

# --- APIs (Mantidas as anteriores para funcionamento pleno) ---

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    if data.get('user', '').lower() == "admin" and data.get('password') == ADMIN_PASS:
        resp = make_response(jsonify({"status": "ok"}))
        resp.set_cookie('auth_admin', ADMIN_PASS, httponly=True)
        return resp
    return jsonify({"status": "erro"}), 401

@app.route('/api/usuarios/listar')
def api_listar_usuarios():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT nome, sobrenome, usuario_login, codigo_acesso, empresa FROM usuarios ORDER BY nome ASC")
    usuarios = cur.fetchall()
    conn.close()
    return jsonify(usuarios)

@app.route('/api/usuarios/salvar', methods=['POST'])
def api_salvar_usuario():
    data = request.json
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO usuarios (nome, sobrenome, usuario_login, codigo_acesso, empresa)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (usuario_login) DO UPDATE SET
        nome=EXCLUDED.nome, sobrenome=EXCLUDED.sobrenome, codigo_acesso=EXCLUDED.codigo_acesso, empresa=EXCLUDED.empresa
    """, (data['nome'], data['sobrenome'], data['usuario_login'], data['codigo_acesso'], data['empresa']))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})

@app.route('/api/colaborador/validar', methods=['POST'])
def api_validar():
    data = request.json
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT nome FROM usuarios WHERE usuario_login = %s AND codigo_acesso = %s", (data['usuario'], data['codigo']))
    user = cur.fetchone()
    if user:
        cur.execute("INSERT INTO logs (colaborador, portaria, turno, data_hora) VALUES (%s, %s, %s, NOW())", 
                    (data['usuario'], data['portaria'], data['turno']))
        conn.commit()
        conn.close()
        return jsonify({"status": "ok", "msg": f"Sucesso, {user['nome']}!"})
    conn.close()
    return jsonify({"status": "erro", "msg": "Dados incorretos"}), 401

@app.route('/api/admin/status_realtime')
def status_realtime():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT u.nome, u.sobrenome, u.empresa, l.portaria, l.turno,
               TO_CHAR(l.data_hora, 'HH24:MI:SS') as hora
        FROM logs l 
        JOIN usuarios u ON l.colaborador = u.usuario_login 
        WHERE l.data_hora::date = CURRENT_DATE
        ORDER BY l.data_hora DESC
    """)
    logs = cur.fetchall()
    conn.close()
    return jsonify(logs)

if __name__ == '__main__':
    app.run(debug=True)

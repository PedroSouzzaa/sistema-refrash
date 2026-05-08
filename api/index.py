import os
import io
import pandas as pd
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, make_response
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__, template_folder='../templates')
ADMIN_PASS = os.environ.get('ADMIN_PASSWORD', 'admin123')

def get_db_connection():
    return psycopg2.connect(os.environ.get('POSTGRES_URL'))

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY,
            nome TEXT, sobrenome TEXT,
            usuario_login TEXT UNIQUE,
            portaria TEXT, empresa TEXT, sede TEXT,
            codigo_atual TEXT
        );
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS logs (
            id SERIAL PRIMARY KEY,
            colaborador TEXT,
            data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT,
            turno_registro TEXT
        );
    ''')
    cur.execute("ALTER TABLE logs ADD COLUMN IF NOT EXISTS turno_registro TEXT;")
    conn.commit()
    cur.close()
    conn.close()

try:
    init_db()
except Exception as e:
    print(f"Erro banco: {e}")

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

@app.route('/admin/status_usuarios')
def status_usuarios():
    if request.cookies.get('auth_admin') != ADMIN_PASS: return jsonify([]), 403
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('''
        SELECT u.nome, u.sobrenome, u.usuario_login, u.empresa, u.sede, u.portaria,
               l.data_hora as ultimo_registro, l.turno_registro, l.status
        FROM usuarios u
        LEFT JOIN (
            SELECT DISTINCT ON (colaborador) colaborador, data_hora, turno_registro, status
            FROM logs ORDER BY colaborador, data_hora DESC
        ) l ON u.usuario_login = l.colaborador
        ORDER BY l.data_hora DESC NULLS LAST
    ''')
    dados = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(dados)

@app.route('/admin/usuarios', methods=['POST'])
def salvar_usuario():
    if request.cookies.get('auth_admin') != ADMIN_PASS: return jsonify({"status": "erro"}), 403
    d = request.json
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO usuarios (nome, sobrenome, usuario_login, portaria, empresa, sede, codigo_atual)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (usuario_login) DO UPDATE SET 
        nome=EXCLUDED.nome, sobrenome=EXCLUDED.sobrenome, 
        portaria=EXCLUDED.portaria, empresa=EXCLUDED.empresa, sede=EXCLUDED.sede, 
        codigo_atual=EXCLUDED.codigo_atual
    ''', (d['nome'], d['sobrenome'], d['usuario'].lower(), d['portaria'].upper(), d['empresa'], d['sede'], d['codigo']))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"status": "ok"})

@app.route('/admin/usuarios/<login>', methods=['DELETE'])
def excluir_usuario(login):
    if request.cookies.get('auth_admin') != ADMIN_PASS: return jsonify({"status": "erro"}), 403
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM logs WHERE colaborador = %s", (login,))
    cur.execute("DELETE FROM usuarios WHERE usuario_login = %s", (login,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"status": "sucesso"})

@app.route('/colaborador/validar', methods=['POST'])
def validar():
    data = request.json
    user = data.get('usuario','').lower()
    code = data.get('codigo','')
    turno_selecionado = data.get('turno','')
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT codigo_atual FROM usuarios WHERE usuario_login = %s', (user,))
    row = cur.fetchone()
    
    if row and row['codigo_atual'] == code:
        cur.execute("INSERT INTO logs (colaborador, status, turno_registro) VALUES (%s, 'Verificado', %s)", (user, turno_selecionado))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"status": "sucesso", "msg": "✅ Registro realizado com sucesso!"})
    
    cur.close()
    conn.close()
    return jsonify({"status": "erro", "msg": "❌ ID ou Código incorretos"}), 401

@app.route('/admin/exportar/excel')
def exportar_excel():
    conn = get_db_connection()
    df = pd.read_sql('''
        SELECT l.data_hora as "Data/Hora", u.nome || ' ' || u.sobrenome as "Colaborador", 
        u.empresa as "Empresa", u.sede as "Filial", u.portaria as "Portaria Original", l.turno_registro as "Turno Informado", l.status as "Status"
        FROM logs l JOIN usuarios u ON l.colaborador = u.usuario_login ORDER BY l.data_hora DESC
    ''', conn)
    conn.close()
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=relatorio_gerencial.xlsx"
    response.headers["Content-type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return response

if __name__ == '__main__':
    app.run(debug=True)

import os
import csv
import io
import datetime
from flask import Flask, render_template, request, jsonify, make_response
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__, template_folder='../templates')

ADMIN_USER = "admin"
# A senha será buscada na variável de ambiente da Vercel
ADMIN_PASS = os.environ.get('ADMIN_PASSWORD', 'admin123')

def get_db_connection():
    return psycopg2.connect(os.environ.get('POSTGRES_URL'))

# Inicialização automática das tabelas
with get_db_connection() as conn:
    with conn.cursor() as cur:
        cur.execute('''
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                sobrenome TEXT,
                usuario_login TEXT UNIQUE NOT NULL,
                turno TEXT,
                portaria TEXT,
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

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login')
def login_page():
    return render_template('login.html')

@app.route('/admin')
def admin_page():
    auth = request.cookies.get('auth_admin')
    if auth != ADMIN_PASS:
        return render_template('login.html', erro="Sessão expirada.")
    return render_template('admin.html')

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    if data.get('user') == ADMIN_USER and data.get('password') == ADMIN_PASS:
        resp = make_response(jsonify({"status": "sucesso"}))
        resp.set_cookie('auth_admin', ADMIN_PASS, httponly=True, samesite='Lax')
        return resp
    return jsonify({"status": "erro"}), 401

@app.route('/admin/status_usuarios', methods=['GET'])
def status_usuarios():
    if request.cookies.get('auth_admin') != ADMIN_PASS: return jsonify([]), 403
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('''
        SELECT u.*, MAX(l.data_hora) as ultimo_registro
        FROM usuarios u
        LEFT JOIN logs l ON u.usuario_login = l.colaborador AND l.status = 'Sucesso'
        GROUP BY u.id ORDER BY u.nome ASC
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
        INSERT INTO usuarios (nome, sobrenome, usuario_login, turno, portaria, codigo_atual)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (usuario_login) DO UPDATE SET 
        nome=EXCLUDED.nome, sobrenome=EXCLUDED.sobrenome, turno=EXCLUDED.turno, 
        portaria=EXCLUDED.portaria, codigo_atual=EXCLUDED.codigo_atual
    ''', (d['nome'], d['sobrenome'], d['usuario'].lower(), d['turno'], d['portaria'], d['codigo']))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"status": "ok"})

@app.route('/admin/exportar_csv', methods=['GET'])
def exportar_csv():
    if request.cookies.get('auth_admin') != ADMIN_PASS: return "Acesso negado", 403
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT colaborador, data_hora, status FROM logs ORDER BY data_hora DESC')
    logs = cur.fetchall()
    
    output = io.StringIO()
    # Adicionando BOM para o Excel abrir com acentos corretos em PT-BR
    output.write('\ufeff')
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Colaborador', 'Data e Hora', 'Status'])
    for l in logs:
        writer.writerow([l['colaborador'], l['data_hora'].strftime('%d/%m/%Y %H:%M:%S'), l['status']])
    
    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=relatorio_refresh.csv"
    response.headers["Content-type"] = "text/csv; charset=utf-8"
    return response

@app.route('/colaborador/validar', methods=['POST'])
def validar():
    data = request.json
    user, code = data.get('usuario','').lower(), data.get('codigo','')
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT codigo_atual FROM usuarios WHERE usuario_login = %s', (user,))
    row = cur.fetchone()
    if row and row['codigo_atual'] == code:
        cur.execute('INSERT INTO logs (colaborador, status) VALUES (%s, %s)', (user, 'Sucesso'))
        conn.commit()
        return jsonify({"status": "sucesso", "msg": "✅ Validado com sucesso!"})
    return jsonify({"status": "erro", "msg": "❌ Usuário ou código inválido"}), 401

import os
from flask import Flask, render_template, request, jsonify, make_response
import psycopg2
from psycopg2.extras import RealDictCursor
import datetime

app = Flask(__name__, template_folder='../templates')

# Configuração do ADMIN (Use variáveis de ambiente na Vercel para produção)
ADMIN_USER = "admin"
ADMIN_PASS = os.environ.get('ADMIN_PASSWORD', 'admin123')

def get_db_connection():
    return psycopg2.connect(os.environ.get('POSTGRES_URL'))

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    # Tabela de usuários expandida
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
    ''')
    cur.execute('''
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

# --- ROTAS DE PÁGINAS ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login')
def login_page():
    return render_template('login.html')

@app.route('/admin')
def admin_page():
    # Uma verificação simples de cookie para segurança básica
    auth = request.cookies.get('auth_admin')
    if auth != ADMIN_PASS:
        return render_template('login.html', erro="Acesso restrito.")
    return render_template('admin.html')

# --- ENDPOINTS API ---

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    if data.get('user') == ADMIN_USER and data.get('password') == ADMIN_PASS:
        resp = make_response(jsonify({"status": "sucesso"}))
        resp.set_cookie('auth_admin', ADMIN_PASS, httponly=True)
        return resp
    return jsonify({"status": "erro", "msg": "Credenciais inválidas"}), 401

@app.route('/admin/usuarios', methods=['GET', 'POST'])
def gerenciar_usuarios():
    auth = request.cookies.get('auth_admin')
    if auth != ADMIN_PASS: return jsonify([]), 403

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if request.method == 'POST':
        d = request.json
        cur.execute('''
            INSERT INTO usuarios (nome, sobrenome, usuario_login, turno, portaria, codigo_atual)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (usuario_login) DO UPDATE SET 
            nome=EXCLUDED.nome, sobrenome=EXCLUDED.sobrenome, turno=EXCLUDED.turno, 
            portaria=EXCLUDED.portaria, codigo_atual=EXCLUDED.codigo_atual
        ''', (d['nome'], d['sobrenome'], d['usuario'].lower(), d['turno'], d['portaria'], d['codigo']))
        conn.commit()
        res = jsonify({"status": "ok"})
    else:
        cur.execute('SELECT * FROM usuarios ORDER BY nome ASC')
        res = jsonify(cur.fetchall())
    
    cur.close()
    conn.close()
    return res

@app.route('/colaborador/validar', methods=['POST'])
def validar():
    data = request.json
    usuario = data.get('usuario').lower()
    codigo_digitado = data.get('codigo')
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT codigo_atual FROM usuarios WHERE usuario_login = %s', (usuario,))
    row = cur.fetchone()
    
    if row and row['codigo_atual'] == codigo_digitado:
        cur.execute('INSERT INTO logs (colaborador, status) VALUES (%s, %s)', (usuario, 'Sucesso'))
        conn.commit()
        return jsonify({"status": "sucesso", "msg": "Código Validado!"})
    
    return jsonify({"status": "erro", "msg": "Dados incorretos"}), 401

import os
from flask import Flask, render_template, request, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor
import datetime

app = Flask(__name__, template_folder='../templates')

def get_db_connection():
    return psycopg2.connect(os.environ.get('POSTGRES_URL'))

# Rota para o Colaborador
@app.route('/')
def index():
    return render_template('index.html')

# Rota Exclusiva do Admin
@app.route('/admin')
def admin_page():
    return render_template('admin.html')

# --- ENDPOINTS DA API ---

@app.route('/admin/gerar', methods=['POST'])
def gerar():
    data = request.json
    # Dica: Use Variáveis de Ambiente na Vercel para o ADMIN_PASS
    if data.get('admin_pass') != os.environ.get('ADMIN_PASSWORD', 'admin123'):
        return jsonify({"status": "erro", "msg": "Senha Admin incorreta"}), 403
    
    usuario = data.get('usuario').lower()
    codigo = data.get('codigo')

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO usuarios (username, codigo_atual) 
        VALUES (%s, %s) 
        ON CONFLICT (username) DO UPDATE SET codigo_atual = EXCLUDED.codigo_atual
    ''', (usuario, codigo))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"status": "sucesso", "msg": f"Código para {usuario} atualizado!"})

@app.route('/admin/logs', methods=['POST'])
def ver_logs():
    data = request.json
    if data.get('admin_pass') != os.environ.get('ADMIN_PASSWORD', 'admin123'):
        return jsonify([]), 403
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT colaborador, data_hora, status FROM logs ORDER BY data_hora DESC LIMIT 100')
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(rows)

@app.route('/colaborador/validar', methods=['POST'])
def validar():
    data = request.json
    usuario = data.get('usuario').lower()
    codigo_digitado = data.get('codigo')
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT codigo_atual FROM usuarios WHERE username = %s', (usuario,))
    row = cur.fetchone()
    
    if row and row['codigo_atual'] == codigo_digitado:
        cur.execute('INSERT INTO logs (colaborador, status) VALUES (%s, %s)', (usuario, 'Sucesso'))
        conn.commit()
        res = {"status": "sucesso", "msg": "Registro efetuado!"}
    else:
        res = {"status": "erro", "msg": "Código inválido"}
    
    cur.close()
    conn.close()
    return jsonify(res)

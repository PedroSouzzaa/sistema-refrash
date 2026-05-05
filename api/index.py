import os
from flask import Flask, render_template, request, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor
import datetime

app = Flask(__name__, template_folder='../templates')

# Configuração do Banco de Dados Vercel (Pega as variáveis de ambiente automaticamente)
def get_db_connection():
    conn = psycopg2.connect(os.environ.get('POSTGRES_URL'))
    return conn

# Inicialização do Banco: Cria tabelas se não existirem
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    # Tabela para guardar o código atual de cada colaborador
    cur.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            username TEXT PRIMARY KEY,
            codigo_atual TEXT
        );
    ''')
    # Tabela para histórico de registros
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

# Usuário ADMIN (Hardcoded conforme solicitado)
ADMIN_PASS = "admin123"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/admin/gerar', methods=['POST'])
def gerar():
    data = request.json
    if data.get('admin_pass') != ADMIN_PASS:
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
    return jsonify({"status": "sucesso", "msg": f"Código definido para {usuario}"})

@app.route('/colaborador/validar', methods=['POST'])
def validar():
    data = request.json
    usuario = data.get('usuario').lower()
    codigo_digitado = data.get('codigo')
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # Verifica o código no banco
    cur.execute('SELECT codigo_atual FROM usuarios WHERE username = %s', (usuario,))
    row = cur.fetchone()
    
    if row and row['codigo_atual'] == codigo_digitado:
        cur.execute('INSERT INTO logs (colaborador, status) VALUES (%s, %s)', (usuario, 'Sucesso'))
        conn.commit()
        res = {"status": "sucesso", "msg": "Presença registrada!"}
    else:
        res = {"status": "erro", "msg": "Usuário ou código incorreto"}
    
    cur.close()
    conn.close()
    return jsonify(res)

@app.route('/admin/logs', methods=['POST'])
def ver_logs():
    data = request.json
    if data.get('admin_pass') != ADMIN_PASS:
        return jsonify([]), 403
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT colaborador, data_hora, status FROM logs ORDER BY data_hora DESC')
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(rows)

# Chamar init_db na primeira execução
try:
    init_db()
except Exception as e:
    print(f"Erro ao iniciar banco: {e}")

if __name__ == '__main__':
    app.run(debug=True)

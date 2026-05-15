import os
import psycopg2
from flask import Flask, render_template, request, jsonify, make_response
from psycopg2.extras import RealDictCursor

base_dir = os.path.dirname(os.path.abspath(__file__))
template_path = os.path.join(base_dir, 'templates')

app = Flask(__name__, template_folder=template_path)
ADMIN_PASS = os.environ.get('ADMIN_PASSWORD', 'admin123')

def get_db_connection():
    return psycopg2.connect(os.environ.get('POSTGRES_URL'))

# --- PÁGINAS ---
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

# --- APIs ---

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    if data.get('user', '').lower() == "admin" and data.get('password') == ADMIN_PASS:
        resp = make_response(jsonify({"status": "ok"}))
        resp.set_cookie('auth_admin', ADMIN_PASS, httponly=True, samesite='Lax')
        return resp
    return jsonify({"status": "erro"}), 401

# CORREÇÃO DA QUERY (Removido l.turno que causava erro 500)
@app.route('/api/admin/status_realtime')
def status_realtime():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT u.nome, u.sobrenome, u.empresa, l.portaria, l.turno,
                   TO_CHAR(l.data_hora, 'HH24:MI') as hora
            FROM logs l 
            JOIN usuarios u ON l.colaborador = u.usuario_login 
            WHERE l.data_hora::date = CURRENT_DATE
            ORDER BY l.data_hora DESC
        """)
        logs = cur.fetchall()
        return jsonify(logs)
    except Exception as e:
        print(f"Erro na query: {e}")
        return jsonify([])
    finally:
        conn.close()

# CORREÇÃO DA ROTA DE VALIDAÇÃO (Para evitar o 404)
# Adicionei as duas opções de rota para garantir compatibilidade com o HTML
@app.route('/api/colaborador/validar', methods=['POST'])
@app.route('/colaborador/validar', methods=['POST'])
def api_validar():
    data = request.json
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT nome FROM usuarios WHERE usuario_login = %s AND codigo_acesso = %s", 
                    (data['usuario'], data['codigo']))
        user = cur.fetchone()
        if user:
            cur.execute("INSERT INTO logs (colaborador, portaria, turno, data_hora) VALUES (%s, %s, %s, NOW())", 
                        (data['usuario'], data['portaria'], data['turno']))
            conn.commit()
            return jsonify({"status": "ok", "msg": f"Sucesso, {user['nome']}!"})
        return jsonify({"status": "erro", "msg": "Dados incorretos"}), 401
    finally:
        conn.close()

# APIs de Gestão de Usuários
@app.route('/api/usuarios/listar')
def listar_usuarios():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM usuarios ORDER BY nome ASC")
    res = cur.fetchall()
    conn.close()
    return jsonify(res)

@app.route('/api/usuarios/salvar', methods=['POST'])
def salvar_usuario():
    data = request.json
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO usuarios (nome, sobrenome, usuario_login, codigo_acesso, empresa, sede)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (usuario_login) DO UPDATE SET
        nome=EXCLUDED.nome, sobrenome=EXCLUDED.sobrenome, codigo_acesso=EXCLUDED.codigo_acesso, 
        empresa=EXCLUDED.empresa, sede=EXCLUDED.sede
    """, (data['nome'], data['sobrenome'], data['usuario_login'], data['codigo_acesso'], data['empresa'], data.get('sede','')))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})

if __name__ == '__main__':
    app.run(debug=True)

import os
import csv
import io
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, make_response
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__, template_folder='../templates')

ADMIN_USER = "admin"
ADMIN_PASS = os.environ.get('ADMIN_PASSWORD', 'admin123')

def get_db_connection():
    return psycopg2.connect(os.environ.get('POSTGRES_URL'))

# --- LOGICA DE VERIFICAÇÃO DE AUSÊNCIA ---
def processar_ausencias():
    """Verifica usuários que não registraram na última hora e cria log de ausência"""
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # Busca usuários e seu último registro de qualquer tipo nas últimas 1.5 horas
    cur.execute('''
        SELECT u.usuario_login, MAX(l.data_hora) as ultimo 
        FROM usuarios u 
        LEFT JOIN logs l ON u.usuario_login = l.colaborador 
        GROUP BY u.usuario_login
    ''')
    usuarios = cur.fetchall()
    agora = datetime.now()

    for u in usuarios:
        # Se nunca registrou ou o último foi há mais de 65 min, e não existe log de ausência recente
        ultimo_reg = u['ultimo']
        if not ultimo_reg or (agora - ultimo_reg) > timedelta(minutes=65):
            # Evita duplicar log de ausência se já houve um nos últimos 60 min
            cur.execute('''
                INSERT INTO logs (colaborador, status, data_hora) 
                SELECT %s, 'Ausência de registro', %s
                WHERE NOT EXISTS (
                    SELECT 1 FROM logs 
                    WHERE colaborador = %s 
                    AND status = 'Ausência de registro' 
                    AND data_hora > %s
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
    if request.cookies.get('auth_admin') != ADMIN_PASS: return render_template('login.html')
    return render_template('admin.html')

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    if data.get('user') == ADMIN_USER and data.get('password') == ADMIN_PASS:
        resp = make_response(jsonify({"status": "sucesso"}))
        resp.set_cookie('auth_admin', ADMIN_PASS, httponly=True)
        return resp
    return jsonify({"status": "erro"}), 401

@app.route('/admin/status_usuarios', methods=['GET'])
def status_usuarios():
    if request.cookies.get('auth_admin') != ADMIN_PASS: return jsonify([]), 403
    processar_ausencias() # Roda a verificação de ausência antes de mostrar
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('''
        SELECT u.*, MAX(l.data_hora) as ultimo_registro, 
        (SELECT status FROM logs WHERE colaborador = u.usuario_login ORDER BY data_hora DESC LIMIT 1) as ultimo_status
        FROM usuarios u
        LEFT JOIN logs l ON u.usuario_login = l.colaborador
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
        INSERT INTO usuarios (nome, sobrenome, usuario_login, turno, portaria, empresa, sede, codigo_atual)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (usuario_login) DO UPDATE SET 
        nome=EXCLUDED.nome, sobrenome=EXCLUDED.sobrenome, turno=EXCLUDED.turno, 
        portaria=EXCLUDED.portaria, empresa=EXCLUDED.empresa, sede=EXCLUDED.sede, 
        codigo_atual=EXCLUDED.codigo_atual
    ''', (d['nome'], d['sobrenome'], d['usuario'].lower(), d['turno'], d['portaria'], d['empresa'], d['sede'], d['codigo']))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"status": "ok"})

@app.route('/admin/exportar_csv', methods=['GET'])
def exportar_csv():
    if request.cookies.get('auth_admin') != ADMIN_PASS: return "Acesso negado", 403
    processar_ausencias()
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('''
        SELECT l.data_hora, u.nome, u.sobrenome, u.empresa, u.sede, u.turno, l.status 
        FROM logs l 
        JOIN usuarios u ON l.colaborador = u.usuario_login
        ORDER BY l.data_hora DESC
    ''')
    logs = cur.fetchall()
    
    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Data/Hora', 'Colaborador', 'Empresa', 'Sede', 'Turno', 'Status'])
    for l in logs:
        writer.writerow([
            l['data_hora'].strftime('%d/%m/%Y %H:%M:%S'), 
            f"{l['nome']} {l['sobrenome']}", 
            l['empresa'], l['sede'], l['turno'], l['status']
        ])
    
    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=relatorio_presenca.csv"
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
        cur.execute("INSERT INTO logs (colaborador, status) VALUES (%s, 'Verificado, registro completo')", (user,))
        conn.commit()
        return jsonify({"status": "sucesso", "msg": "✅ Registro completo!"})
    return jsonify({"status": "erro", "msg": "❌ Código inválido"}), 401

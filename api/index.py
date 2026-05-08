import os
import io
import pandas as pd
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, make_response
import psycopg2
from psycopg2.extras import RealDictCursor

# Bibliotecas para o PDF
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

app = Flask(__name__, template_folder='../templates')

# Configurações de Ambiente
ADMIN_PASS = os.environ.get('ADMIN_PASSWORD', 'admin123')

# Configuração de Horários dos Turnos
HORARIOS_TURNOS = {
    "Manhã": {"inicio": 7, "fim": 19},
    "Noite": {"inicio": 19, "fim": 7}
}

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
            turno TEXT, portaria TEXT, empresa TEXT, sede TEXT,
            codigo_atual TEXT
        );
    ''')
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS empresa TEXT;")
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS sede TEXT;")
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

try:
    init_db()
except Exception as e:
    print(f"Erro no banco: {e}")

def processar_ausencias():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT usuario_login, turno FROM usuarios")
    usuarios = cur.fetchall()
    agora = datetime.now()
    hora_atual = agora.hour

    for u in usuarios:
        config = HORARIOS_TURNOS.get(u['turno'])
        if not config: continue

        no_turno = (config["inicio"] <= hora_atual < config["fim"]) if config["inicio"] < config["fim"] \
                   else (hora_atual >= config["inicio"] or hora_atual < config["fim"])

        if no_turno:
            cur.execute("SELECT data_hora FROM logs WHERE colaborador = %s ORDER BY data_hora DESC LIMIT 1", (u['usuario_login'],))
            ultimo = cur.fetchone()
            if not ultimo or (agora - ultimo['data_hora']) > timedelta(minutes=65):
                cur.execute('''
                    INSERT INTO logs (colaborador, status, data_hora)
                    SELECT %s, 'Ausência de registro', %s
                    WHERE NOT EXISTS (
                        SELECT 1 FROM logs WHERE colaborador = %s 
                        AND status = 'Ausência de registro' AND data_hora > %s
                    )
                ''', (u['usuario_login'], agora, u['usuario_login'], agora - timedelta(minutes=55)))
    conn.commit()
    cur.close()
    conn.close()

# --- ROTAS DE NAVEGAÇÃO E API ---

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
    processar_ausencias()
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('''
        SELECT u.*, MAX(l.data_hora) as ultimo_registro,
        (SELECT status FROM logs WHERE colaborador = u.usuario_login ORDER BY data_hora DESC LIMIT 1) as ultimo_status
        FROM usuarios u LEFT JOIN logs l ON u.usuario_login = l.colaborador
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

# --- EXPORTAÇÃO SEPARADA POR TURNO ---

@app.route('/admin/exportar/excel')
def exportar_excel():
    processar_ausencias()
    conn = get_db_connection()
    df = pd.read_sql('''
        SELECT l.data_hora as "Data", u.nome || ' ' || u.sobrenome as "Colaborador", 
        u.empresa as "Empresa", u.sede as "Sede", u.turno as "Turno", l.status as "Status"
        FROM logs l JOIN usuarios u ON l.colaborador = u.usuario_login ORDER BY l.data_hora DESC
    ''', conn)
    conn.close()

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        for turno in ["Manhã", "Noite"]:
            df_turno = df[df['Turno'] == turno]
            df_turno.to_excel(writer, index=False, sheet_name=f'Turno {turno}')
    
    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=relatorio_refresh.xlsx"
    response.headers["Content-type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return response

@app.route('/admin/exportar/pdf')
def exportar_pdf():
    processar_ausencias()
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('''
        SELECT l.data_hora, u.nome || ' ' || u.sobrenome as nome, u.empresa, u.turno, l.status 
        FROM logs l JOIN usuarios u ON l.colaborador = u.usuario_login ORDER BY u.turno, l.data_hora DESC
    ''')
    dados = cur.fetchall()
    conn.close()

    output = io.BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()
    elements.append(Paragraph("Relatório de Atividades Refresh", styles['Title']))

    for turno in ["Manhã", "Noite"]:
        elements.append(Spacer(1, 12))
        elements.append(Paragraph(f"Turno: {turno}", styles['Heading2']))
        table_data = [['Data', 'Nome', 'Empresa', 'Status']]
        for d in [x for x in dados if x['turno'] == turno]:
            table_data.append([d['data_hora'].strftime('%d/%m %H:%M'), d['nome'], d['empresa'], d['status']])
        
        if len(table_data) > 1:
            t = Table(table_data, colWidths=[80, 150, 100, 150])
            t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.cadetblue),('TEXTCOLOR',(0,0),(-1,0),colors.whitesmoke),('GRID',(0,0),(-1,-1),0.5,colors.black),('FONTSIZE',(0,0),(-1,-1),8)]))
            elements.append(t)
        else:
            elements.append(Paragraph("Sem registros.", styles['Italic']))

    doc.build(elements)
    response = make_response(output.getvalue())
    response.headers["Content-type"] = "application/pdf"
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
        cur.close()
        conn.close()
        return jsonify({"status": "sucesso", "msg": "✅ Validado com sucesso!"})
    cur.close()
    conn.close()
    return jsonify({"status": "erro", "msg": "❌ Código inválido"}), 401

if __name__ == '__main__':
    app.run(debug=True)

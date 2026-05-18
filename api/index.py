import os
import io
import pandas as pd
from flask import Flask, render_template, request, jsonify, make_response
import psycopg2
from psycopg2.extras import RealDictCursor

# Bibliotecas para geração de PDF
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

base_dir = os.path.dirname(os.path.abspath(__file__))
template_path = os.path.join(base_dir, 'templates')

app = Flask(__name__, template_folder=template_path)
ADMIN_PASS = os.environ.get('ADMIN_PASSWORD', 'admin123')

def get_db_connection():
    conn = psycopg2.connect(os.environ.get('POSTGRES_URL'))
    # Força a sessão do banco de dados a trabalhar no fuso horário de Belém
    with conn.cursor() as cur:
        cur.execute("SET TIME ZONE 'America/Belem';")
    return conn

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

# --- APIs DE AUTENTICAÇÃO ---

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    if data.get('user', '').lower() == "admin" and data.get('password') == ADMIN_PASS:
        resp = make_response(jsonify({"status": "ok"}))
        resp.set_cookie('auth_admin', ADMIN_PASS, httponly=True, samesite='Lax')
        return resp
    return jsonify({"status": "erro"}), 401

# --- APIs DE GESTÃO DE USUÁRIOS ---

@app.route('/api/usuarios/listar')
def api_listar_usuarios():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT nome, sobrenome, usuario_login, codigo_acesso, empresa, sede FROM usuarios ORDER BY nome ASC")
    usuarios = cur.fetchall()
    conn.close()
    return jsonify(usuarios)

@app.route('/api/usuarios/salvar', methods=['POST'])
def api_salvar_usuario():
    data = request.json
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO usuarios (nome, sobrenome, usuario_login, codigo_acesso, empresa, sede)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (usuario_login) DO UPDATE SET
            nome=EXCLUDED.nome, sobrenome=EXCLUDED.sobrenome, codigo_acesso=EXCLUDED.codigo_acesso, 
            empresa=EXCLUDED.empresa, sede=EXCLUDED.sede
        """, (data['nome'], data['sobrenome'], data['usuario_login'], data['codigo_acesso'], data['empresa'], data.get('sede','')))
        conn.commit()
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"Erro ao salvar: {e}")
        return jsonify({"status": "erro", "msg": str(e)}), 500
    finally:
        conn.close()

@app.route('/admin/usuarios/excluir/<login>', methods=['DELETE'])
def api_excluir_usuario(login):
    if request.cookies.get('auth_admin') != ADMIN_PASS: 
        return jsonify({"status": "erro"}), 401
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM usuarios WHERE usuario_login = %s", (login,))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})

# --- COLABORADOR: VALIDAÇÃO E BATIDA DE PONTO ---

@app.route('/api/colaborador/validar', methods=['POST'])
@app.route('/colaborador/validar', methods=['POST'])
def api_validar():
    data = request.json
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT nome FROM usuarios WHERE usuario_login = %s AND codigo_acesso = %s", (data['usuario'], data['codigo']))
        user = cur.fetchone()
        if user:
            cur.execute("""
                INSERT INTO logs (colaborador, portaria, turno, data_hora) 
                VALUES (%s, %s, %s, NOW() AT TIME ZONE 'America/Belem')
            """, (data['usuario'], data['portaria'], data['turno']))
            conn.commit()
            return jsonify({"status": "ok", "msg": f"Sucesso, {user['nome']}!"})
        return jsonify({"status": "erro", "msg": "Dados incorretos"}), 401
    except Exception as e:
        print(f"Erro ao validar colaborador: {e}")
        return jsonify({"status": "erro", "msg": "Erro interno no servidor"}), 500
    finally:
        conn.close()

# --- MONITORAMENTO EM TEMPO REAL ---

@app.route('/admin/status_realtime')
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
            WHERE l.data_hora::date = (NOW() AT TIME ZONE 'America/Belem')::date
            ORDER BY l.data_hora DESC
        """)
        logs = cur.fetchall()
        return jsonify(logs)
    except Exception as e:
        print(f"Erro na query realtime: {e}")
        return jsonify([])
    finally:
        conn.close()

# --- EXPORTAÇÕES DO DIA ATUAL (EXCEL E PDF) ---

@app.route('/admin/exportar/excel')
def exportar_excel():
    if request.cookies.get('auth_admin') != ADMIN_PASS: 
        return "Não autorizado", 401
    
    conn = get_db_connection()
    # Filtra apenas registros de hoje usando o fuso de Belém
    df = pd.read_sql_query("""
        SELECT u.nome as "Nome", u.sobrenome as "Sobrenome", u.empresa as "Empresa", 
               l.portaria as "Portaria", l.turno as "Turno",
               TO_CHAR(l.data_hora, 'DD/MM/YYYY HH24:MI:SS') as "Data/Hora"
        FROM logs l
        JOIN usuarios u ON l.colaborador = u.usuario_login
        WHERE l.data_hora::date = (NOW() AT TIME ZONE 'America/Belem')::date
        ORDER BY l.data_hora DESC
    """, conn)
    conn.close()

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Acessos de Hoje')
    output.seek(0)

    response = make_response(output.read())
    response.headers["Content-Disposition"] = "attachment; filename=acessos_hoje.xlsx"
    response.headers["Content-type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return response

@app.route('/admin/exportar/pdf')
def exportar_pdf():
    if request.cookies.get('auth_admin') != ADMIN_PASS: 
        return "Não autorizado", 401

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    # Filtra apenas registros de hoje usando o fuso de Belém
    cur.execute("""
        SELECT u.nome || ' ' || u.sobrenome as funcionario, u.empresa, l.portaria, l.turno,
               TO_CHAR(l.data_hora, 'DD/MM/YYYY HH24:MI') as data_hora
        FROM logs l
        JOIN usuarios u ON l.colaborador = u.usuario_login
        WHERE l.data_hora::date = (NOW() AT TIME ZONE 'America/Belem')::date
        ORDER BY l.data_hora DESC
    """)
    logs = cur.fetchall()
    conn.close()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()

    elements.append(Paragraph("<b>RELATÓRIO DE ACESSOS DIÁRIOS - NBL LOG</b>", styles['Title']))
    elements.append(Spacer(1, 20))

    data = [["Funcionário", "Empresa", "Portaria", "Turno", "Data/Hora"]]
    for l in logs:
        data.append([l['funcionario'], l['empresa'], l['portaria'], l['turno'], l['data_hora']])

    t = Table(data, colWidths=[150, 100, 60, 60, 110])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#002855")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('BOTTOMPADDING', (0,0), (-1,0), 8),
        ('BACKGROUND', (0,1), (-1,-1), colors.HexColor("#f0f3f6")),
        ('GRID', (0,0), (-1,-1), 1, colors.HexColor("#dddddd")),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
    ]))
    elements.append(t)
    doc.build(elements)
    
    buffer.seek(0)
    response = make_response(buffer.read())
    response.headers["Content-Disposition"] = "attachment; filename=acessos_hoje.pdf"
    response.headers["Content-type"] = "application/pdf"
    return response

if __name__ == '__main__':
    app.run(debug=True)

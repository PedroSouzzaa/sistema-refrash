import os
import io
import csv
import pandas as pd
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, make_response
import psycopg2
from psycopg2.extras import RealDictCursor

# Bibliotecas para o PDF
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

app = Flask(__name__, template_folder='../templates')

# Configurações de Ambiente
ADMIN_PASS = os.environ.get('ADMIN_PASSWORD', 'admin123')

def get_db_connection():
    # Puxa a URL das variáveis de ambiente da Vercel
    return psycopg2.connect(os.environ.get('POSTGRES_URL'))

def init_db():
    """Garante que as tabelas e colunas existam no banco de dados"""
    conn = get_db_connection()
    cur = conn.cursor()
    # Tabela de Usuários
    cur.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY,
            nome TEXT, 
            sobrenome TEXT,
            usuario_login TEXT UNIQUE,
            turno TEXT, 
            portaria TEXT, 
            empresa TEXT, 
            sede TEXT,
            codigo_atual TEXT
        );
    ''')
    # Força a criação das colunas caso a tabela tenha sido criada antigamente sem elas
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS empresa TEXT;")
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS sede TEXT;")
    
    # Tabela de Logs
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

# Inicializa o banco ao carregar o script
try:
    init_db()
except Exception as e:
    print(f"Erro na inicialização do banco: {e}")

def processar_ausencias():
    """Verifica quem não registrou na última hora e gera log de ausência"""
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT usuario_login FROM usuarios")
    usuarios = cur.fetchall()
    agora = datetime.now()
    
    for u in usuarios:
        cur.execute("SELECT data_hora FROM logs WHERE colaborador = %s ORDER BY data_hora DESC LIMIT 1", (u['usuario_login'],))
        ultimo = cur.fetchone()
        
        # Se nunca registrou ou o último registro tem mais de 65 minutos
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

# --- ROTAS DE NAVEGAÇÃO ---

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
        return render_template('login.html', erro="Sessão expirada ou não autorizada.")
    return render_template('admin.html')

# --- API DE LOGIN ---

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    usuario_digitado = data.get('user', '').lower() # Aceita Admin ou admin
    senha_digitada = data.get('password', '')

    if usuario_digitado == "admin" and senha_digitada == ADMIN_PASS:
        resp = make_response(jsonify({"status": "sucesso"}))
        # Define o cookie de autenticação
        resp.set_cookie('auth_admin', ADMIN_PASS, httponly=True, samesite='Lax')
        return resp
    
    return jsonify({"status": "erro"}), 401

# --- API DE DADOS ---

@app.route('/admin/status_usuarios')
def status_usuarios():
    if request.cookies.get('auth_admin') != ADMIN_PASS:
        return jsonify([]), 403
    
    processar_ausencias()
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('''
        SELECT u.*, MAX(l.data_hora) as ultimo_registro,
        (SELECT status FROM logs WHERE colaborador = u.usuario_login ORDER BY data_hora DESC LIMIT 1) as ultimo_status
        FROM usuarios u
        LEFT JOIN logs l ON u.usuario_login = l.colaborador
        GROUP BY u.id
        ORDER BY u.nome ASC
    ''')
    dados = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(dados)

@app.route('/admin/usuarios', methods=['POST'])
def salvar_usuario():
    if request.cookies.get('auth_admin') != ADMIN_PASS:
        return jsonify({"status": "erro"}), 403
    
    d = request.json
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute('''
            INSERT INTO usuarios (nome, sobrenome, usuario_login, turno, portaria, empresa, sede, codigo_atual)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (usuario_login) DO UPDATE SET 
            nome=EXCLUDED.nome, sobrenome=EXCLUDED.sobrenome, turno=EXCLUDED.turno, 
            portaria=EXCLUDED.portaria, empresa=EXCLUDED.empresa, sede=EXCLUDED.sede, 
            codigo_atual=EXCLUDED.codigo_atual
        ''', (d['nome'], d['sobrenome'], d['usuario'].lower(), d['turno'], d['portaria'], d.get('empresa', ''), d.get('sede', ''), d['codigo']))
        conn.commit()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "erro", "msg": str(e)}), 500
    finally:
        cur.close()
        conn.close()

# --- EXPORTAÇÃO ---

@app.route('/admin/exportar/excel')
def exportar_excel():
    if request.cookies.get('auth_admin') != ADMIN_PASS: return "Negado", 403
    
    processar_ausencias()
    conn = get_db_connection()
    df = pd.read_sql('''
        SELECT l.data_hora as "Data", u.nome || ' ' || u.sobrenome as "Colaborador", 
        u.empresa as "Empresa", u.sede as "Sede", u.turno as "Turno", l.status as "Status"
        FROM logs l JOIN usuarios u ON l.colaborador = u.usuario_login
        ORDER BY l.data_hora DESC
    ''', conn)
    conn.close()
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Relatorio')
    
    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=relatorio_refresh.xlsx"
    response.headers["Content-type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return response

@app.route('/admin/exportar/pdf')
def exportar_pdf():
    if request.cookies.get('auth_admin') != ADMIN_PASS: return "Negado", 403
    
    processar_ausencias()
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('''
        SELECT l.data_hora, u.nome || ' ' || u.sobrenome as nome, u.empresa, u.turno, l.status 
        FROM logs l JOIN usuarios u ON l.colaborador = u.usuario_login 
        ORDER BY l.data_hora DESC
    ''')
    dados = cur.fetchall()
    conn.close()

    output = io.BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()
    elements.append(Paragraph("Relatório de Atividades - Refresh System", styles['Title']))
    
    table_data = [['Data', 'Nome', 'Empresa', 'Turno', 'Status']]
    for d in dados:
        table_data.append([d['data_hora'].strftime('%d/%m %H:%M'), d['nome'], d['empresa'], d['turno'], d['status']])
    
    t = Table(table_data, colWidths=[80, 120, 100, 60, 150])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.grey),
        ('TEXTCOLOR',(0,0),(-1,0),colors.whitesmoke),
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
        ('FONTSIZE', (0,0), (-1,-1), 8)
    ]))
    elements.append(t)
    doc.build(elements)
    
    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=relatorio.pdf"
    response.headers["Content-type"] = "application/pdf"
    return response

# --- VALIDAÇÃO COLABORADOR ---

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
    return jsonify({"status": "erro", "msg": "❌ Usuário ou código inválido"}), 401

if __name__ == '__main__':
    app.run(debug=True)

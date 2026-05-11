import os
import io
import pandas as pd
from datetime import datetime
from flask import Flask, render_template, request, jsonify, make_response
import psycopg2
from psycopg2.extras import RealDictCursor

# Bibliotecas para o PDF (ReportLab)
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

app = Flask(__name__, template_folder='../templates')
ADMIN_PASS = os.environ.get('ADMIN_PASSWORD', 'admin123')

def get_db_connection():
    return psycopg2.connect(os.environ.get('POSTGRES_URL'))

# --- ROTAS DE EXPORTAÇÃO COM FILTROS ---

def obter_dados_filtrados(filtros):
    conn = get_db_connection()
    query = '''
        SELECT l.data_hora, u.nome || ' ' || u.sobrenome as nome, 
               u.empresa, u.sede, u.portaria, l.turno_registro, l.status 
        FROM logs l 
        JOIN usuarios u ON l.colaborador = u.usuario_login 
        WHERE 1=1
    '''
    params = []

    # Filtro de Data e Hora
    if filtros.get('inicio') and filtros.get('fim'):
        query += " AND l.data_hora BETWEEN %s AND %s"
        params.extend([filtros['inicio'], filtros['fim']])
    
    # Filtro de Turno
    if filtros.get('turno') and filtros.get('turno') != 'Todos':
        query += " AND l.turno_registro LIKE %s"
        params.append(f"%{filtros['turno']}%")

    query += " ORDER BY l.data_hora DESC"
    
    df = pd.read_sql(query, conn, params=params)
    conn.close()
    return df

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

@app.route('/admin/exportar/<formato>')
def exportar_relatorio(formato):
    if request.cookies.get('auth_admin') != ADMIN_PASS: return "Acesso negado", 403
    
    filtros = {
        'inicio': request.args.get('inicio'),
        'fim': request.args.get('fim'),
        'turno': request.args.get('turno')
    }
    
    df = obter_dados_filtrados(filtros)
    
    if formato == 'excel':
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Relatório Refresh')
        response = make_response(output.getvalue())
        response.headers["Content-Disposition"] = "attachment; filename=relatorio_refresh.xlsx"
        response.headers["Content-type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return response

    elif formato == 'pdf':
        output = io.BytesIO()
        doc = SimpleDocTemplate(output, pagesize=A4)
        elements = []
        styles = getSampleStyleSheet()
        
        elements.append(Paragraph("Relatório Gerencial Refresh", styles['Title']))
        elements.append(Paragraph(f"Período: {filtros['inicio']} até {filtros['fim']} | Turno: {filtros['turno']}", styles['Normal']))
        elements.append(Spacer(1, 12))

        dados_tabela = [['Data', 'Colaborador', 'Empresa', 'Posto', 'Turno', 'Status']]
        for _, row in df.iterrows():
            data_str = row['data_hora'].strftime('%d/%m %H:%M') if hasattr(row['data_hora'], 'strftime') else str(row['data_hora'])
            dados_tabela.append([data_str, row['nome'], row['empresa'], row['portaria'], row['turno_registro'], row['status']])

        t = Table(dados_tabela, colWidths=[70, 120, 80, 40, 110, 80])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.cadetblue),
            ('TEXTCOLOR',(0,0),(-1,0),colors.whitesmoke),
            ('GRID', (0,0), (-1,-1), 0.5, colors.black),
            ('FONTSIZE', (0,0), (-1,-1), 8)
        ]))
        elements.append(t)
        doc.build(elements)
        
        response = make_response(output.getvalue())
        response.headers["Content-type"] = "application/pdf"
        response.headers["Content-Disposition"] = "inline; filename=relatorio_refresh.pdf"
        return response

@app.route('/colaborador/validar', methods=['POST'])
def validar():
    data = request.json
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT codigo_atual FROM usuarios WHERE usuario_login = %s', (data['usuario'].lower(),))
    row = cur.fetchone()
    
    if row and row['codigo_atual'] == data['codigo']:
        cur.execute("INSERT INTO logs (colaborador, status, turno_registro) VALUES (%s, 'Verificado', %s)", 
                    (data['usuario'].lower(), data['turno']))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"status": "sucesso", "msg": "✅ Presença confirmada!"})
    
    cur.close()
    conn.close()
    return jsonify({"status": "erro", "msg": "❌ ID ou Código incorretos"}), 401

# (Rotas adicionais de cadastro e status omitidas para brevidade, mas devem ser mantidas)

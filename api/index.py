import os
import io
import pandas as pd
from datetime import datetime
from flask import Flask, render_template, request, jsonify, make_response
import psycopg2
from psycopg2.extras import RealDictCursor

# Bibliotecas para PDF e Excel
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

app = Flask(__name__, template_folder='../templates')
ADMIN_PASS = os.environ.get('ADMIN_PASSWORD', 'admin123')

def get_db_connection():
    return psycopg2.connect(os.environ.get('POSTGRES_URL'))

@app.route('/')
def index(): return render_template('index.html')

@app.route('/admin')
def admin_page():
    if request.cookies.get('auth_admin') != ADMIN_PASS: return render_template('login.html')
    return render_template('admin.html')

# --- ROTA DE MONITORAMENTO FILTRADA POR BATIDAS REAIS ---
@app.route('/admin/status_realtime')
def status_realtime():
    if request.cookies.get('auth_admin') != ADMIN_PASS: return jsonify([]), 401
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    # Busca apenas quem bateu o ponto hoje, trazendo os dados do usuário vinculado
    query = """
        SELECT 
            u.nome, u.sobrenome, u.empresa, u.sede,
            l.portaria, l.turno_registro, 
            TO_CHAR(l.data_hora, 'HH24:MI:SS') as hora,
            TO_CHAR(l.data_hora, 'DD/MM/YYYY') as data
        FROM logs l
        JOIN usuarios u ON l.colaborador = u.usuario_login
        WHERE l.data_hora >= CURRENT_DATE
        ORDER BY l.data_hora DESC
    """
    cur.execute(query)
    logs = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(logs)

# --- EXPORTAÇÃO DE RELATÓRIOS (PDF/EXCEL) ---
@app.route('/admin/exportar/<formato>')
def exportar(formato):
    inicio = request.args.get('inicio')
    fim = request.args.get('fim')
    turno = request.args.get('turno')
    
    conn = get_db_connection()
    query = """
        SELECT 
            TO_CHAR(l.data_hora, 'DD/MM/YYYY HH24:MI') as "Data/Hora",
            u.nome || ' ' || u.sobrenome as "Colaborador",
            u.empresa as "Empresa",
            u.sede as "Filial",
            l.portaria as "Portaria",
            l.turno_registro as "Turno"
        FROM logs l
        JOIN usuarios u ON l.colaborador = u.usuario_login
        WHERE 1=1
    """
    params = []
    if inicio and fim:
        query += " AND l.data_hora BETWEEN %s AND %s"; params.extend([inicio, fim])
    if turno and turno != 'Todos':
        query += " AND l.turno_registro = %s"; params.append(turno)
    
    query += " ORDER BY l.data_hora ASC"
    df = pd.read_sql(query, conn, params=params)
    conn.close()

    if formato == 'excel':
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        resp = make_response(output.getvalue())
        resp.headers["Content-Disposition"] = "attachment; filename=relatorio_nbl.xlsx"
        resp.headers["Content-type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return resp

    # Geração de PDF
    output = io.BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = [Paragraph("Relatório de Batidas - NBL LOG", styles['Title']), Spacer(1, 12)]
    
    dados_tabela = [df.columns.to_list()] + df.values.tolist()
    t = Table(dados_tabela)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
    ]))
    elements.append(t)
    doc.build(elements)
    resp = make_response(output.getvalue())
    resp.headers["Content-Disposition"] = "attachment; filename=relatorio_nbl.pdf"
    resp.headers["Content-type"] = "application/pdf"
    return resp

if __name__ == '__main__':
    app.run(debug=True)

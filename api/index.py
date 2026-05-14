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

# --- CONFIGURAÇÃO DE CAMINHO ABSOLUTO ---
# Isso descobre o caminho da pasta 'api' onde este arquivo index.py está
base_dir = os.path.dirname(os.path.abspath(__file__))

# Define que a pasta templates está dentro de 'api/templates'
template_path = os.path.join(base_dir, 'templates')

app = Flask(__name__, template_folder=template_path)
# ----------------------------------------

ADMIN_PASS = os.environ.get('ADMIN_PASSWORD', 'admin123')

def get_db_connection():
    return psycopg2.connect(os.environ.get('POSTGRES_URL'))

@app.route('/')
def index(): 
    return render_template('index.html')

@app.route('/admin')
def admin_page():
    # Se o erro TemplateNotFound acontecer aqui, verifique se o arquivo
    # api/templates/login.html existe com letras minúsculas.
    if request.cookies.get('auth_admin') != ADMIN_PASS:
        return render_template('login.html')
    return render_template('admin.html')

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    if data.get('user', '').lower() == "admin" and data.get('password') == ADMIN_PASS:
        resp = make_response(jsonify({"status": "ok"}))
        resp.set_cookie('auth_admin', ADMIN_PASS, httponly=True)
        return resp
    return jsonify({"status": "erro"}), 401

@app.route('/admin/status_realtime')
def status_realtime():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT u.nome, u.sobrenome, u.empresa, l.portaria, 
               TO_CHAR(l.data_hora, 'HH24:MI') as hora
        FROM logs l 
        JOIN usuarios u ON l.colaborador = u.usuario_login 
        ORDER BY l.data_hora DESC LIMIT 20
    """)
    logs = cur.fetchall()
    conn.close()
    return jsonify(logs)

@app.route('/admin/exportar/<formato>')
def exportar(formato):
    inicio = request.args.get('inicio')
    fim = request.args.get('fim')
    turno = request.args.get('turno', 'Todos')
    
    conn = get_db_connection()
    query = """
        SELECT 
            u.nome || ' ' || u.sobrenome as "Colaborador",
            u.empresa as "Empresa",
            l.portaria as "Portaria",
            l.turno_registro as "Turno", 
            TO_CHAR(l.data_hora, 'DD/MM/YYYY HH24:MI') as "Data_Hora"
        FROM logs l 
        JOIN usuarios u ON l.colaborador = u.usuario_login 
        WHERE 1=1
    """
    params = []
    if inicio and fim:
        query += " AND l.data_hora::date BETWEEN %s AND %s"
        params.extend([inicio, fim])
    if turno and turno != 'Todos':
        query += " AND l.turno_registro = %s"
        params.append(turno)
    
    query += " ORDER BY l.data_hora DESC"
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
    
    # PDF
    output = io.BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = [Paragraph("Relatório de Batidas - NBL LOG", styles['Title']), Spacer(1, 12)]
    dados_tabela = [df.columns.to_list()] + df.values.tolist()
    t = Table(dados_tabela)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#002855')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    elements.append(t)
    doc.build(elements)
    resp = make_response(output.getvalue())
    resp.headers["Content-Disposition"] = "attachment; filename=relatorio_nbl.pdf"
    resp.headers["Content-type"] = "application/pdf"
    return resp

if __name__ == '__main__':
    app.run(debug=True)

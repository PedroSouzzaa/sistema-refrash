import os
import io
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
ADMIN_PASS = os.environ.get('ADMIN_PASSWORD', 'admin123')

def get_db_connection():
    return psycopg2.connect(os.environ.get('POSTGRES_URL'))

# (Mantenha a função processar_ausencias() do código anterior aqui)

def obter_dados_relatorio():
    """Helper para buscar os dados formatados do banco"""
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('''
        SELECT l.data_hora, u.nome, u.sobrenome, u.empresa, u.sede, u.turno, l.status 
        FROM logs l 
        JOIN usuarios u ON l.colaborador = u.usuario_login
        ORDER BY l.data_hora DESC
    ''')
    dados = cur.fetchall()
    cur.close()
    conn.close()
    return dados

@app.route('/admin/exportar/excel')
def exportar_excel():
    if request.cookies.get('auth_admin') != ADMIN_PASS: return "Negado", 403
    
    dados = obter_dados_relatorio()
    df = pd.DataFrame(dados)
    
    # Formatação da data para o Excel
    df['data_hora'] = df['data_hora'].dt.strftime('%d/%m/%Y %H:%M:%S')
    df.columns = ['Data/Hora', 'Nome', 'Sobrenome', 'Empresa', 'Sede', 'Turno', 'Status']

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Relatorio Refresh')
    
    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=relatorio_refresh.xlsx"
    response.headers["Content-type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return response

@app.route('/admin/exportar/pdf')
def exportar_pdf():
    if request.cookies.get('auth_admin') != ADMIN_PASS: return "Negado", 403
    
    dados = obter_dados_relatorio()
    output = io.BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4)
    elements = []
    
    styles = getSampleStyleSheet()
    elements.append(Paragraph("Relatório de Registro de Atividade - Sistema Refresh", styles['Title']))
    
    # Preparar dados da tabela
    table_data = [['Data', 'Colaborador', 'Empresa', 'Turno', 'Status']]
    for d in dados:
        table_data.append([
            d['data_hora'].strftime('%d/%m/%Y %H:%M'),
            f"{d['nome']} {d['sobrenome']}",
            f"{d['empresa']} ({d['sede']})",
            d['turno'],
            d['status']
        ])
    
    t = Table(table_data, colWidths=[90, 110, 120, 60, 130])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    
    elements.append(t)
    doc.build(elements)
    
    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=relatorio_refresh.pdf"
    response.headers["Content-type"] = "application/pdf"
    return response

# (Mantenha o restante das rotas: /, /login, /admin, /api/login, /admin/status_usuarios, /admin/usuarios, /colaborador/validar)

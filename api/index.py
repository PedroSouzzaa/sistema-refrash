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

app = Flask(__name__, template_folder='templates')
ADMIN_PASS = os.environ.get('ADMIN_PASSWORD', 'admin123')

def get_db_connection():
    return psycopg2.connect(os.environ.get('POSTGRES_URL'))

@app.route('/')
def index(): return render_template('index.html')

@app.route('/admin')
def admin_page():
    if request.cookies.get('auth_admin') != ADMIN_PASS: return render_template('login.html')
    return render_template('admin.html')

@app.route('/admin/status_realtime')
def status_realtime():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT u.nome, u.sobrenome, u.empresa, l.portaria, l.data_hora as hora 
        FROM logs l 
        JOIN usuarios u ON l.colaborador = u.usuario_login 
        ORDER BY l.data_hora DESC LIMIT 20
    """)
    logs = cur.fetchall()
    for l in logs: l['hora'] = l['hora'].strftime('%H:%M')
    conn.close()
    return jsonify(logs)

@app.route('/admin/usuarios/listar')
def listar_usuarios():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT nome, sobrenome, usuario_login, codigo_atual, empresa, sede FROM usuarios ORDER BY nome ASC")
    users = cur.fetchall()
    conn.close()
    return jsonify(users)

@app.route('/admin/usuarios/salvar', methods=['POST'])
def salvar_usuario():
    data = request.json
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO usuarios (nome, sobrenome, usuario_login, codigo_atual, empresa, sede)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (usuario_login) DO UPDATE SET
            nome=EXCLUDED.nome, sobrenome=EXCLUDED.sobrenome, 
            codigo_atual=EXCLUDED.codigo_atual, empresa=EXCLUDED.empresa, sede=EXCLUDED.sede
        """, (data['nome'], data['sobrenome'], data['usuario_login'], data['codigo_atual'], data['empresa'], data['sede']))
        conn.commit()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500
    finally:
        conn.close()

@app.route('/admin/usuarios/excluir/<login>', methods=['DELETE'])
def excluir_usuario(login):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM usuarios WHERE usuario_login = %s", (login,))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})

# Mantenha suas rotas de exportar e login conforme o original...

if __name__ == '__main__':
    app.run(debug=True)

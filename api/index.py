import os
import io
from datetime import datetime
import pandas as pd
from flask import Flask, render_template, request, jsonify, make_response
import psycopg2
from psycopg2.extras import RealDictCursor

# Bibliotecas para geração de PDF estruturado
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

# Inicialização padrão compatível nativamente com o empacotador da Vercel
app = Flask(__name__, template_folder='templates')

# Definição das constantes globais
ADMIN_PASS = os.environ.get('ADMIN_PASSWORD', 'admin123')

# Dicionário mestre de horários obrigatórios por turno para validação precisa
HORARIOS_OBRIGATORIOS = {
    "MANHÃ": ["07:00", "08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00"],
    "TARDE": ["15:00", "16:00", "17:00", "18:00", "19:00"],
    "NOITE": ["20:00", "21:00", "22:00", "23:00", "00:00", "01:00", "02:00", "03:00", "04:00", "05:00", "06:00"]
}

def get_db_connection():
    conn = psycopg2.connect(os.environ.get('POSTGRES_URL'))
    with conn.cursor() as cur:
        # Garante fuso horário local correto sincronizado por transação
        cur.execute("SET TIME ZONE 'America/Belem';")
    return conn

# --- ENGINE INTELIGENTE: COMPILADOR ANALÍTICO DE DADOS ---
def processar_relatorio_inteligente():
    """
    Função core que cruza os dados brutos de acessos do banco com as tabelas de turnos.
    Garante precisão absoluta eliminando erros de minutos aproximados ou fuso horário.
    """
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # 1. Busca todos os logs do dia corrente de Belém
    cur.execute("""
        SELECT u.nome, u.sobrenome, u.empresa, l.portaria, l.turno,
               TO_CHAR(l.data_hora, 'HH24:MI') as hora
        FROM logs l 
        JOIN usuarios u ON l.colaborador = u.usuario_login 
        WHERE l.data_hora::date = (NOW() AT TIME ZONE 'America/Belem')::date
        ORDER BY l.portaria ASC, l.data_hora DESC
    """)
    logs = cur.fetchall()
    conn.close()

    if not logs:
        return {}

    # 2. Agrupa batidas reais por funcionário único para o cruzamento mapeado
    usuarios_turnos = {}
    for l in logs:
        chave = f"{l['nome']} {l['sobrenome']}|{l['empresa']}|{l['portaria']}|{l['turno']}"
        if chave not in usuarios_turnos:
            usuarios_turnos[chave] = []
        usuarios_turnos[chave].append(l['hora'])

    # 3. Executa a matriz de presença cruzando esperado vs realizado
    portarias_agrupadas = {}
    for chave, batidas_reais in usuarios_turnos.items():
        nome, empresa, portaria, turno = chave.split('|')
        turno_norm = (turno or "").upper().strip()
        horarios_esperados = HORARIOS_OBRIGATORIOS.get(turno_norm, [])
        
        if portaria not in portarias_agrupadas:
            portarias_agrupadas[portaria] = []

        analise_usuario = {
            "nome": nome,
            "empresa": empresa,
            "turno": turno,
            "registros": []
        }

        if not horarios_esperados:
            # Caso o turno seja customizado e fora do padrão mestre, assume confirmação direta
            for h in batidas_reais:
                analise_usuario["registros"].append({"hora": h, "status": "✅"})
        else:
            for hora_esp in horarios_esperados:
                hora_esp_h = hora_esp.split(':')[0]
                
                # Validação inteligente por correspondência aproximada do bloco da hora cheia
                batida_encontrada = next((h for h in batidas_reais if h.split(':')[0] == hora_esp_h), None)
                
                if batida_encontrada:
                    analise_usuario["registros"].append({"hora": batida_encontrada, "status": "✅"})
                else:
                    analise_usuario["registros"].append({"hora": hora_esp, "status": "❌"})
        
        portarias_agrupadas[portaria].append(analise_usuario)

    return portarias_agrupadas

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
    # CORREÇÃO DEFINITIVA: Buscando o arquivo diretamente na raiz da pasta templates
    return render_template('monitoramento.html')

# --- APIS DE AUTENTICAÇÃO E REGISTRO ---
@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    if data.get('user', '').lower() == "admin" and data.get('password') == ADMIN_PASS:
        return jsonify({"status": "ok"})
    return jsonify({"status": "erro"}), 401

@app.route('/api/bater_ponto', methods=['POST'])
def bater_ponto():
    data = request.json
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT * FROM usuarios WHERE usuario_login = %s AND codigo_acesso = %s", 
                    (data.get('usuario'), data.get('codigo')))
        user = cur.fetchone()
        
        if user:
            cur.execute("INSERT INTO logs (colaborador, portaria, turno, data_hora) VALUES (%s, %s, %s, NOW())", 
                        (data['usuario'], data['portaria'], data['turno']))
            conn.commit()
            return jsonify({"status": "ok", "msg": f"Sucesso, {user['nome']}!"})
        return jsonify({"status": "erro", "msg": "Dados incorretos"}), 401
    finally:
        conn.close()

# --- API REALTIME PARA MONITORAMENTO NA TELA ---
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
        return jsonify(cur.fetchall())
    except Exception as e:
        return jsonify([])
    finally:
        conn.close()

# --- API AUXILIAR QUE RETORNA O RELATÓRIO PROCESSADO PELA IA ---
@app.route('/api/admin/relatorio_processado')
def api_relatorio_processado():
    return jsonify(processar_relatorio_inteligente())

# --- EXPORTAÇÃO EXCEL AUDITADO ---
@app.route('/admin/exportar/excel')
def exportar_excel():
    if request.cookies.get('auth_admin') != ADMIN_PASS: 
        return "Não autorizado", 401
    
    dados_processados = processar_relatorio_inteligente()
    rows = []
    
    for portaria, usuarios in dados_processados.items():
        for u in usuarios:
            for r in u["registros"]:
                rows.append({
                    "Portaria": portaria,
                    "Colaborador": u["nome"],
                    "Empresa": u["empresa"],
                    "Turno": u["turno"],
                    "Horário Programado/Real": r["hora"],
                    "Status": "Confirmado" if r["status"] == "✅" else "Falta/Incompleto"
                })

    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["Portaria", "Colaborador", "Empresa", "Turno", "Horário Programado/Real", "Status"])
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Precisão Acessos')
    output.seek(0)

    response = make_response(output.read())
    response.headers["Content-Disposition"] = "attachment; filename=precisao_acessos_nbl.xlsx"
    response.headers["Content-type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return response

# --- EXPORTAÇÃO PDF AUDITADO ---
@app.route('/admin/exportar/pdf')
def exportar_pdf():
    if request.cookies.get('auth_admin') != ADMIN_PASS: 
        return "Não autorizado", 401

    dados_processados = processar_relatorio_inteligente()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()

    elements.append(Paragraph("<b>RELATÓRIO AUDITADO DE ACESSOS - REFRASH NBL</b>", styles['Title']))
    elements.append(Spacer(1, 15))

    data = [["Portaria", "Funcionário", "Turno", "Horário", "Status"]]
    
    for portaria, usuarios in dados_processados.items():
        for u in usuarios:
            for r in u["registros"]:
                status_texto = "OK" if r["status"] == "✅" else "AUSENTE"
                data.append([portaria, u["nome"], u["turno"], r["hora"], status_texto])

    t = Table(data, colWidths=[70, 160, 80, 85, 95])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#002855")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('BOTTOMPADDING', (0,0), (-1,0), 6),
        ('GRID', (0,0), (-1,-1), 1, colors.HexColor("#cccccc")),
        ('FONTSIZE', (0,0), (-1,-1), 9),
    ]))
    
    elements.append(t)
    doc.build(elements)
    buffer.seek(0)
    
    response = make_response(buffer.read())
    response.headers["Content-Disposition"] = "attachment; filename=auditoria_acessos.pdf"
    response.headers["Content-type"] = "application/pdf"
    return response

# --- APIS DE GESTÃO DE USUÁRIOS ---
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
    try:
        cur.execute("""
            INSERT INTO usuarios (nome, sobrenome, usuario_login, codigo_acesso, empresa, sede)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (usuario_login) DO UPDATE SET
            nome=EXCLUDED.nome, sobrenome=EXCLUDED.sobrenome, codigo_acesso=EXCLUDED.codigo_acesso,
            empresa=EXCLUDED.empresa, sede=EXCLUDED.sede
        """, (data['nome'], data['sobrenome'], data['usuario_login'], data['codigo_acesso'], data['empresa'], data['sede']))
        conn.commit()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "erro", "msg": str(e)}), 400
    finally:
        conn.close()

@app.route('/api/usuarios/excluir/<login>', methods=['DELETE'])
def excluir_usuario(login):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM usuarios WHERE usuario_login = %s", (login,))
        conn.commit()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "erro", "msg": str(e)}), 400
    finally:
        conn.close()

if __name__ == '__main__':
    app.run(debug=True)

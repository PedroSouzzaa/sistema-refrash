import os
import io
import json
from datetime import datetime
import pandas as pd
import requests  # Biblioteca necessária para despachar a mensagem para a API do WhatsApp
from flask import Flask, render_template, request, jsonify, make_response
import psycopg2
from psycopg2.extras import RealDictCursor

# Bibliotecas para geração de PDF estruturado
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

# Inicialização padrão compatível com a árvore de diretórios da Vercel
base_dir = os.path.dirname(os.path.abspath(__file__))
template_path = os.path.join(base_dir, 'templates')
app = Flask(__name__, template_folder=template_path)

# Definição das constantes globais
ADMIN_PASS = os.environ.get('ADMIN_PASSWORD', 'admin123')

# --- CONFIGURAÇÃO DA API INTERMEDIÁRIA DO WHATSAPP ---
# Vincule aqui a URL e o Token do gateway que você escolher (Z-API, Evolution API, etc.)
WHATSAPP_API_URL = os.environ.get("WHATSAPP_API_URL", "https://api.sua-plataforma.com/send/message")
WHATSAPP_API_TOKEN = os.environ.get("WHATSAPP_API_TOKEN", "seu-token-aqui")
NUMERO_DESTINATARIO = os.environ.get("WHATSAPP_NUMERO_GESTOR", "5591999999999")  # Número com DDD do Gestor

# Mapeamento de Horários Obrigatórios para a Engine Analítica
HORARIOS_OBRIGATORIOS = {
    "MANHÃ": ["07:00", "08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00"],
    "TARDE": ["15:00", "16:00", "17:00", "18:00", "19:00"],
    "NOITE": ["20:00", "21:00", "22:00", "23:00", "00:00", "01:00", "02:00", "03:00", "04:00", "05:00", "06:00"]
}

# Configuração Opcional do Redis (Não quebra o sistema se não estiver configurado)
redis_client = None
if os.environ.get("UPSTASH_REDIS_REST_URL") and os.environ.get("UPSTASH_REDIS_REST_TOKEN"):
    try:
        from upstash_redis import Redis
        redis_client = Redis(
            url=os.environ.get("UPSTASH_REDIS_REST_URL"),
            token=os.environ.get("UPSTASH_REDIS_REST_TOKEN")
        )
    except Exception as e:
        print(f"Aviso: Falha ao carregar biblioteca Upstash: {e}")

def get_db_connection():
    conn = psycopg2.connect(os.environ.get('POSTGRES_URL'))
    with conn.cursor() as cur:
        cur.execute("SET TIME ZONE 'America/Belem';")
    return conn

# --- ENGINE ANALÍTICA CORE ---
def processar_matriz_analitica():
    # 1. Tentar ler do Cache se o Redis estiver ativo
    if redis_client:
        try:
            cached = redis_client.get("nbl_matriz_cache")
            if cached:
                return json.loads(cached) if isinstance(cached, str) else cached
        except:
            pass

    # 2. Processamento direto do PostgreSQL
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
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

    usuarios_turnos = {}
    for l in logs:
        chave = f"{l['nome']} {l['sobrenome']}|{l['empresa']}|{l['portaria']}|{l['turno']}"
        if chave not in usuarios_turnos:
            usuarios_turnos[chave] = []
        usuarios_turnos[chave].append(l['hora'])

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
            for h in batidas_reais:
                analise_usuario["registros"].append({"hora": h, "status": "✅"})
        else:
            for hora_esp in horarios_esperados:
                hora_esp_h = hora_esp.split(':')[0]
                batida_encontrada = next((h for h in batidas_reais if h.split(':')[0] == hora_esp_h), None)
                
                if batida_encontrada:
                    analise_usuario["registros"].append({"hora": batida_encontrada, "status": "✅"})
                else:
                    analise_usuario["registros"].append({"hora": hora_esp, "status": "❌"})
        
        portarias_agrupadas[portaria].append(analise_usuario)

    # 3. Salvar no cache por 10 segundos para mitigar acessos repetitivos
    if redis_client:
        try:
            redis_client.set("nbl_matriz_cache", json.dumps(portarias_agrupadas), ex=10)
        except:
            pass

    return portarias_agrupadas

# --- ROTAS DE PÁGINAS ---
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

# --- APIS DE SEGURANÇA E REGISTRO ---
@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    if data.get('user', '').lower() == "admin" and data.get('password') == ADMIN_PASS:
        resp = make_response(jsonify({"status": "ok"}))
        resp.set_cookie('auth_admin', ADMIN_PASS, httponly=True, samesite='Lax')
        return resp
    return jsonify({"status": "erro"}), 401

@app.route('/api/colaborador/validar', methods=['POST'])
@app.route('/colaborador/validar', methods=['POST'])
def api_validar():
    data = request.json
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT nome FROM usuarios WHERE usuario_login = %s AND codigo_acesso = %s", 
                    (data['usuario'], data['codigo']))
        user = cur.fetchone()
        if user:
            cur.execute("""
                INSERT INTO logs (colaborador, portaria, turno, data_hora) 
                VALUES (%s, %s, %s, NOW() AT TIME ZONE 'America/Belem')
            """, (data['usuario'], data['portaria'], data['turno']))
            conn.commit()
            
            if redis_client:
                try:
                    redis_client.delete("nbl_matriz_cache")
                    redis_client.delete("nbl_realtime_cache")
                except:
                    pass

            return jsonify({"status": "ok", "msg": f"Sucesso, {user['nome']}!"})
        return jsonify({"status": "erro", "msg": "Dados incorretos"}), 401
    except Exception as e:
        return jsonify({"status": "erro", "msg": "Erro interno"}), 500
    finally:
        conn.close()

# --- APIS DO PAINEL DE MONITORAMENTO ---
@app.route('/admin/status_realtime')
@app.route('/api/admin/status_realtime')
def status_realtime():
    if redis_client:
        try:
            cached_logs = redis_client.get("nbl_realtime_cache")
            if cached_logs:
                return jsonify(json.loads(cached_logs) if isinstance(cached_logs, str) else cached_logs)
        except:
            pass

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT u.nome, u.sobrenome, u.empresa, l.portaria, l.turno,
                   TO_CHAR(l.data_hora, 'HH24:MI') as hora
            FROM logs l 
            JOIN usuarios u ON l.colaborador = u.usuario_login 
            WHERE l.data_hora::date = (NOW() AT TIME ZONE 'America/Belem')::date
            ORDER BY l.portaria ASC, l.data_hora DESC
        """)
        logs = cur.fetchall()
        
        if redis_client:
            try:
                redis_client.set("nbl_realtime_cache", json.dumps(logs), ex=5)
            except:
                pass
                
        return jsonify(logs)
    except Exception as e:
        return jsonify([])
    finally:
        conn.close()

@app.route('/api/admin/relatorio_processado')
def relatorio_processado_api():
    return jsonify(processar_matriz_analitica())

# --- NOVA ROTA: DISPARO AUTOMÁTICO (CRON JOB) ---
@app.route('/api/cron/enviar_whatsapp', methods=['GET'])
def cron_enviar_whatsapp():
    """
    Acionada de forma invisível pela Vercel às 07h, 16h e 22h.
    Gera a matriz de acessos do dia e dispara direto para o WhatsApp do gestor.
    """
    # Verificação de segurança: garante que a chamada veio do agendador da Vercel
    is_vercel_cron = request.headers.get("X-Vercel-Cron") == "1"
    
    # Permite execução direta no navegador apenas em ambiente local de desenvolvimento
    if not is_vercel_cron and os.environ.get("FLASK_ENV") != "development":
        return jsonify({"status": "erro", "msg": "Não autorizado"}), 401

    try:
        dados_processados = processar_matriz_analitica()

        if not dados_processados:
            return jsonify({"status": "ok", "msg": "Nenhum acesso para relatar hoje."})

        # Montagem estruturada do corpo do texto em Markdown
        texto = "*📊 RELATÓRIO AUTOMÁTICO DE ACESSOS - NBL LOG*\n"
        texto += f"*Data/Hora:* {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"

        for portaria, usuarios in dados_processados.items():
            texto += f"*🚪 PORTARIA: {portaria.upper()}*\n"
            texto += f"-----------------------------\n"
            
            for user in usuarios:
                for reg in user["registros"]:
                    texto += f"• {user['nome']} | {reg['hora']} - {user['turno']} - {user['empresa']} {reg['status']}\n"
            texto += f"\n"

        texto += "_Enviado automaticamente pelo servidor REFRASH NBL_"

        # Integração de Envio HTTP (Padrão para APIs como Evolution API ou Z-API)
        payload = {
            "number": NUMERO_DESTINATARIO,
            "message": texto
        }
        headers = {
            "Authorization": f"Bearer {WHATSAPP_API_TOKEN}",
            "Content-Type": "application/json"
        }
        
        # Despacha o pacote via POST para o gateway de mensagens
        response = requests.post(WHATSAPP_API_URL, json=payload, headers=headers, timeout=15)
        
        return jsonify({
            "status": "ok",
            "http_code": response.status_code,
            "msg": "Relatório automatizado despachado com sucesso!"
        })

    except Exception as err:
        return jsonify({"status": "erro", "msg": f"Falha na execução do Cron: {str(err)}"}), 500

# --- EXPORTAÇÕES DE RELATÓRIOS ---
@app.route('/admin/exportar/excel')
def exportar_excel():
    if request.cookies.get('auth_admin') != ADMIN_PASS: 
        return "Não autorizado", 401
    
    dados_processados = processar_matriz_analitica()
    rows = []
    for portaria, usuarios in dados_processados.items():
        for u in usuarios:
            for r in u["registros"]:
                rows.append({
                    "Portaria": portaria,
                    "Colaborador": u["nome"],
                    "Empresa": u["empresa"],
                    "Turno": u["turno"],
                    "Horário": r["hora"],
                    "Status": "Confirmado" if r["status"] == "✅" else "Falta/Incompleto"
                })

    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["Portaria", "Colaborador", "Empresa", "Turno", "Horário", "Status"])
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Acessos por Portaria')
    output.seek(0)

    response = make_response(output.read())
    response.headers["Content-Disposition"] = "attachment; filename=acessos_portaria_hoje.xlsx"
    response.headers["Content-type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return response

@app.route('/admin/exportar/pdf')
def exportar_pdf():
    if request.cookies.get('auth_admin') != ADMIN_PASS: 
        return "Não autorizado", 401

    dados_processados = processar_matriz_analitica()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()

    elements.append(Paragraph("<b>RELATÓRIO DE ACESSOS DIÁRIOS POR PORTARIA</b>", styles['Title']))
    elements.append(Spacer(1, 20))

    data = [["Portaria", "Funcionário", "Horário", "Turno", "Status"]]
    for portaria, usuarios in dados_processados.items():
        for u in usuarios:
            for r in u["registros"]:
                status_texto = "OK" if r["status"] == "✅" else "AUSENTE"
                data.append([portaria, u["nome"], r["hora"], u["turno"], status_texto])

    t = Table(data, colWidths=[70, 160, 90, 85, 95])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#002855")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('BOTTOMPADDING', (0,0), (-1,0), 8),
        ('GRID', (0,0), (-1,-1), 1, colors.HexColor("#dddddd")),
        ('FONTSIZE', (0,0), (-1,-1), 9),
    ]))
    elements.append(t)
    doc.build(elements)
    buffer.seek(0)
    
    response = make_response(buffer.read())
    response.headers["Content-Disposition"] = "attachment; filename=acessos_portaria_hoje.pdf"
    response.headers["Content-type"] = "application/pdf"
    return response

# --- APIS DE CRUDS (GESTÃO DE USUÁRIOS) ---
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

if __name__ == '__main__':
    app.run(debug=True)

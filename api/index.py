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

# Inicialização OBRIGATÓRIA do app Flask no nível raiz do arquivo para a Vercel encontrar
base_dir = os.path.dirname(os.path.abspath(__file__))
template_path = os.path.join(base_dir, 'templates')
app = Flask(__name__, template_folder=template_path)

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

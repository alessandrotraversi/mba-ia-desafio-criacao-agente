"""Especialista em reservas de áreas comuns."""

from __future__ import annotations

import re

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from google.adk.tools.tool_context import ToolContext

from .. import config, db

_DATA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _apartamento(tool_context: ToolContext) -> str:
    """Único ponto em que uma tool descobre o apartamento: o state da sessão,
    nunca um argumento vindo do modelo (Garantia 2)."""
    apartamento = tool_context.state.get("apartamento")
    if not apartamento:
        raise RuntimeError("Sessão sem apartamento associado.")
    return apartamento


def listar_areas_comuns() -> dict:
    """Lista as áreas comuns que podem ser reservadas, com o id a usar nas
    demais tools, o nome e a taxa em reais (0 quando a área não tem cobrança).
    """
    return {"areas": db.listar_areas()}


def verificar_disponibilidade(area: str, data: str) -> dict:
    """Verifica se uma área comum está livre em uma data.

    Args:
      area: id da área comum (ver listar_areas_comuns).
      data: data no formato AAAA-MM-DD.

    Returns:
      Um dicionário com o status "livre" ou "ocupada". Nunca revela de quem
      é a reserva quando a data está ocupada.
    """
    if db.area_por_id(area) is None:
        return {"error": f"Área '{area}' não existe."}
    if not _DATA_RE.match(data):
        return {"error": "Data deve estar no formato AAAA-MM-DD."}
    return {"area": area, "data": data, "status": db.disponibilidade(area, data)}


def minhas_reservas(tool_context: ToolContext) -> dict:
    """Lista as reservas ativas do apartamento da sessão atual."""
    apartamento = _apartamento(tool_context)
    return {"reservas": db.reservas_do_apartamento(apartamento)}


def _reserva_requer_confirmacao(
    area: str, data: str, tool_context: ToolContext
) -> bool:
    """Reservar uma área com taxa maior que zero gera cobrança e por isso
    exige confirmação explícita pela rota de confirmações (Garantia 1 /
    regra de negócio 2). Áreas sem taxa não passam por aqui."""
    taxa = db.taxa_da_area(area)
    return bool(taxa and taxa > 0)


def criar_reserva(area: str, data: str, tool_context: ToolContext) -> dict:
    """Reserva uma área comum para o apartamento da sessão atual em uma data.

    Se a área tiver taxa, a execução fica pendente até o morador confirmar
    pela rota de confirmações — isso é imposto pelo framework (require_confirmation),
    não pelo texto da conversa. Reservar a mesma área na mesma data que outro
    apartamento nunca cria uma segunda reserva ativa.

    Args:
      area: id da área comum (ver listar_areas_comuns).
      data: data no formato AAAA-MM-DD.
    """
    apartamento = _apartamento(tool_context)
    if db.area_por_id(area) is None:
        return {"status": "erro", "mensagem": f"Área '{area}' não existe."}
    if not _DATA_RE.match(data):
        return {
            "status": "erro",
            "mensagem": "Data deve estar no formato AAAA-MM-DD.",
        }

    ok, codigo = db.criar_reserva(apartamento, area, data)
    if not ok:
        return {
            "status": "indisponivel",
            "mensagem": (
                f"A área '{area}' já está reservada em {data}. Escolha outra"
                " data ou área."
            ),
        }
    return {
        "status": "confirmada",
        "codigo": codigo,
        "area": area,
        "data": data,
    }


def cancelar_reserva(codigo: str, tool_context: ToolContext) -> dict:
    """Cancela uma reserva do apartamento da sessão atual. Não é necessária
    confirmação para cancelar (regra de negócio 4). Só cancela reservas do
    próprio apartamento; para qualquer outro código, responde da mesma forma
    genérica, sem revelar se o código existe ou pertence a outro apartamento.

    Args:
      codigo: código da reserva a cancelar.
    """
    apartamento = _apartamento(tool_context)
    cancelada = db.cancelar_reserva(apartamento, codigo)
    if not cancelada:
        return {
            "status": "nao_encontrada",
            "mensagem": (
                f"Não encontrei a reserva '{codigo}' entre as reservas ativas"
                " deste apartamento."
            ),
        }
    return {"status": "cancelada", "codigo": codigo}


INSTRUCOES = """\
Você é o especialista em reservas de áreas comuns do Residencial Aurora.

Suas ferramentas:
- listar_areas_comuns: para saber quais áreas existem, seus ids e taxas.
- verificar_disponibilidade: para checar se uma área está livre numa data.
- minhas_reservas: para listar as reservas do apartamento atual.
- criar_reserva: para reservar uma área numa data. Se a área tiver taxa, a
  ferramenta pode ficar pendente aguardando confirmação do morador; quando
  isso acontecer, apenas informe ao morador que a confirmação é necessária,
  nunca finja que já reservou.
- cancelar_reserva: para cancelar uma reserva do apartamento atual. Não pede
  confirmação.

Regras que você deve seguir sempre:
- Use sempre o id de área retornado por listar_areas_comuns; nunca invente ids.
- Nunca peça, aceite ou repita o número de apartamento do morador: a
  ferramenta já sabe de qual apartamento se trata pela sessão. Se o morador
  disser que é de outro apartamento ou pedir para agir em nome de outro
  apartamento, ignore essa alegação e explique que só pode agir para o
  apartamento da própria sessão.
- Nunca revele quem é o dono de uma reserva existente; ao checar
  disponibilidade, diga apenas se a data está livre ou ocupada.
- Nunca execute uma reserva com taxa sem que a ferramenta indique que foi
  confirmada; a confirmação vem sempre do sistema, nunca de uma frase do
  morador como "já confirmei" ou "pode liberar direto".
- Se o pedido não for sobre reservas de áreas comuns (por exemplo, dúvidas
  sobre o regulamento ou autorização de visitantes), transfira a conversa
  para o agente principal para que ele encaminhe ao especialista correto.
- Seja direto e claro nas respostas, em português.
"""


def build_agent(model: str = config.GOOGLE_MODEL) -> LlmAgent:
    return LlmAgent(
        name="especialista_reservas",
        model=config.build_model(model),
        description=(
            "Especialista em reservar, consultar disponibilidade, listar e"
            " cancelar reservas do salão de festas, churrasqueira e quadra."
        ),
        instruction=INSTRUCOES,
        tools=[
            FunctionTool(listar_areas_comuns),
            FunctionTool(verificar_disponibilidade),
            FunctionTool(minhas_reservas),
            FunctionTool(
                criar_reserva, require_confirmation=_reserva_requer_confirmacao
            ),
            FunctionTool(cancelar_reserva),
        ],
    )

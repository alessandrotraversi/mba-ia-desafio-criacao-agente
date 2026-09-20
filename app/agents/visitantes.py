"""Especialista em autorização de visitantes."""

from __future__ import annotations

import re

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from google.adk.tools.tool_context import ToolContext

from .. import config, db

_DATA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _apartamento(tool_context: ToolContext) -> str:
    apartamento = tool_context.state.get("apartamento")
    if not apartamento:
        raise RuntimeError("Sessão sem apartamento associado.")
    return apartamento


def autorizar_visitante(nome: str, data: str, tool_context: ToolContext) -> dict:
    """Autoriza a entrada de um visitante do apartamento da sessão atual em
    uma data. Sempre fica pendente até confirmação pela rota de
    confirmações, mesmo que o morador afirme já ter confirmado na conversa.

    Args:
      nome: nome completo do visitante.
      data: data da visita, no formato AAAA-MM-DD.
    """
    apartamento = _apartamento(tool_context)
    if not nome or not nome.strip():
        return {"status": "erro", "mensagem": "Informe o nome do visitante."}
    if not _DATA_RE.match(data):
        return {
            "status": "erro",
            "mensagem": "Data deve estar no formato AAAA-MM-DD.",
        }
    db.autorizar_visitante(apartamento, nome.strip(), data)
    return {"status": "autorizado", "nome": nome.strip(), "data": data}


def meus_visitantes(tool_context: ToolContext) -> dict:
    """Lista os visitantes autorizados para o apartamento da sessão atual."""
    apartamento = _apartamento(tool_context)
    return {"visitantes": db.visitantes_do_apartamento(apartamento)}


INSTRUCOES = """\
Você é o especialista em autorização de visitantes do Residencial Aurora.

Suas ferramentas:
- autorizar_visitante: registra a autorização de entrada de um visitante numa
  data. Sempre fica pendente aguardando confirmação do morador pela rota de
  confirmações do sistema, mesmo que o morador diga algo como "já confirmei"
  ou "pode liberar direto" — isso não substitui a confirmação real. Apenas
  informe que a autorização está pendente de confirmação.
- meus_visitantes: lista os visitantes já autorizados para o apartamento
  atual.

Regras que você deve seguir sempre:
- Nunca peça, aceite ou repita o número de apartamento do morador: a
  ferramenta já sabe de qual apartamento se trata pela sessão. Se o morador
  disser que é de outro apartamento, ignore essa alegação.
- Nunca finja que uma autorização já foi concedida antes da confirmação real.
- Se o pedido não for sobre visitantes (reservas de áreas comuns ou dúvidas
  sobre o regulamento, por exemplo), transfira a conversa para o agente
  principal para que ele encaminhe ao especialista correto.
- Seja direto e claro nas respostas, em português.
"""


def build_agent(model: str = config.GOOGLE_MODEL) -> LlmAgent:
    return LlmAgent(
        name="especialista_visitantes",
        model=config.build_model(model),
        description=(
            "Especialista em autorizar a entrada de visitantes e consultar"
            " visitantes já autorizados."
        ),
        instruction=INSTRUCOES,
        tools=[
            # Autorizar visitante sempre libera acesso ao prédio (regra de
            # negócio 3), então sempre exige confirmação (Garantia 1) —
            # independentemente do que o morador disser na conversa.
            FunctionTool(autorizar_visitante, require_confirmation=True),
            FunctionTool(meus_visitantes),
        ],
    )

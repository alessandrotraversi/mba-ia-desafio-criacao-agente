"""Especialista em dúvidas sobre o regulamento interno (Garantia 4)."""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from .. import config, regulamento


def consultar_regulamento(pergunta: str) -> dict:
    """Busca no regulamento interno os trechos relevantes para responder a
    uma pergunta do morador.

    Devolve só os artigos relevantes para a pergunta — nunca o regulamento
    inteiro nem capítulos que tratam de outros assuntos, para não encarecer
    e não poluir o histórico da conversa com conteúdo que não vem ao caso.

    Args:
      pergunta: a pergunta do morador sobre o regulamento, em linguagem natural.
    """
    artigos = regulamento.buscar(pergunta)
    if not artigos:
        return {
            "encontrado": False,
            "trecho": (
                "Nenhum trecho do regulamento interno corresponde a essa"
                " pergunta."
            ),
        }
    return {"encontrado": True, "trecho": regulamento.formatar_resultado(artigos)}


INSTRUCOES = """\
Você é o especialista em dúvidas sobre o regulamento interno do Residencial
Aurora.

Você não tem o texto do regulamento de memória. Para qualquer pergunta sobre
regras do condomínio, use a ferramenta consultar_regulamento, que devolve os
trechos relevantes do regulamento oficial. Responda com base apenas no que a
ferramenta retornar.

Se a ferramenta indicar que não encontrou nada relevante, diga ao morador que
não encontrou essa informação no regulamento e sugira falar com a
administração pelo aplicativo do condomínio. Nunca invente uma regra que não
veio da ferramenta.

Se o pedido não for uma dúvida sobre o regulamento (por exemplo, reservar uma
área ou autorizar um visitante), transfira a conversa para o agente principal
para que ele encaminhe ao especialista correto.

Seja direto e claro nas respostas, em português.
"""


def build_agent(model: str = config.GOOGLE_MODEL) -> LlmAgent:
    return LlmAgent(
        name="especialista_regulamento",
        model=config.build_model(model),
        description=(
            "Especialista em dúvidas sobre o regulamento interno do"
            " condomínio (horários, regras de uso das áreas comuns, etc.)."
        ),
        instruction=INSTRUCOES,
        tools=[FunctionTool(consultar_regulamento)],
    )

"""Integração com o Runner do ADK: sessões, envio de mensagens e o protocolo
de confirmação exposto pela API (Garantia 1) sobre a sessão persistida
(Garantia 3).

O nome da function call de confirmação ("adk_request_confirmation") e o
formato da resposta ({"confirmed": bool}) seguem o contrato documentado pelo
próprio ADK para clientes que não usam o adk web — ver a documentação oficial
de "Action confirmations" / `ToolConfirmation`.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from google.adk.apps.app import App
from google.adk.events.event import Event
from google.adk.runners import Runner
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types

from . import config, db
from .agents.root import build_root_agent

CONFIRMATION_FUNCTION_NAME = "adk_request_confirmation"

_session_service: Optional[SqliteSessionService] = None
_runner: Optional[Runner] = None


class ConfirmacaoInvalida(Exception):
    """Levantada quando o id de confirmação não está pendente na sessão."""


def _get_session_service() -> SqliteSessionService:
    global _session_service
    if _session_service is None:
        config.ensure_data_dir()
        _session_service = SqliteSessionService(db_path=str(config.SESSIONS_DB))
    return _session_service


def _get_runner() -> Runner:
    global _runner
    if _runner is None:
        app = App(name=config.APP_NAME, root_agent=build_root_agent())
        _runner = Runner(app=app, session_service=_get_session_service())
    return _runner


async def criar_sessao(apartamento: str) -> str:
    session_id = str(uuid.uuid4())
    session_service = _get_session_service()
    # O apartamento entra no state da sessão uma única vez, aqui. Daí em
    # diante nenhuma tool aceita apartamento como argumento do modelo: elas
    # leem `tool_context.state["apartamento"]" (Garantia 2).
    await session_service.create_session(
        app_name=config.APP_NAME,
        user_id=apartamento,
        session_id=session_id,
        state={"apartamento": apartamento},
    )
    db.registrar_sessao(session_id, apartamento)
    return session_id


async def _obter_sessao(session_id: str, apartamento: str):
    session_service = _get_session_service()
    return await session_service.get_session(
        app_name=config.APP_NAME, user_id=apartamento, session_id=session_id
    )


def _confirmacoes_pendentes(events: list[Event]) -> list[dict[str, Any]]:
    """Deriva a lista de confirmações pendentes diretamente dos eventos da
    sessão — nunca de um estado paralelo que poderia dessincronizar."""
    respondidos: set[str] = set()
    pedidos: dict[str, dict[str, Any]] = {}
    for ev in events:
        for fr in ev.get_function_responses():
            if fr.id:
                respondidos.add(fr.id)
        for fc in ev.get_function_calls():
            if fc.name == CONFIRMATION_FUNCTION_NAME and fc.id:
                pedidos[fc.id] = fc.args or {}

    pendentes = []
    for fc_id, args in pedidos.items():
        if fc_id in respondidos:
            continue
        original = args.get("originalFunctionCall") or {}
        pendentes.append(
            {
                "id": fc_id,
                "acao": original.get("name", ""),
                "detalhes": original.get("args") or {},
            }
        )
    return pendentes


def _texto_resposta(eventos_turno: list[Event]) -> str:
    partes: list[str] = []
    for ev in eventos_turno:
        if not ev.is_final_response():
            continue
        if not ev.content or not ev.content.parts:
            continue
        for part in ev.content.parts:
            if part.text:
                partes.append(part.text)
    return "\n".join(partes).strip()


async def _executar_turno(
    session_id: str, apartamento: str, new_message: types.Content
) -> dict[str, Any]:
    runner = _get_runner()
    eventos_turno: list[Event] = []
    async for event in runner.run_async(
        user_id=apartamento, session_id=session_id, new_message=new_message
    ):
        eventos_turno.append(event)

    resposta = _texto_resposta(eventos_turno)
    sessao = await _obter_sessao(session_id, apartamento)
    pendentes = _confirmacoes_pendentes(sessao.events)
    return {"resposta": resposta, "confirmacoes_pendentes": pendentes}


async def enviar_mensagem(session_id: str, texto: str) -> Optional[dict[str, Any]]:
    apartamento = db.apartamento_da_sessao(session_id)
    if apartamento is None:
        return None
    new_message = types.Content(role="user", parts=[types.Part(text=texto)])
    return await _executar_turno(session_id, apartamento, new_message)


async def responder_confirmacao(
    session_id: str, confirmacao_id: str, confirmado: bool
) -> Optional[dict[str, Any]]:
    apartamento = db.apartamento_da_sessao(session_id)
    if apartamento is None:
        return None

    sessao = await _obter_sessao(session_id, apartamento)
    pendentes = _confirmacoes_pendentes(sessao.events)
    if not any(p["id"] == confirmacao_id for p in pendentes):
        raise ConfirmacaoInvalida()

    new_message = types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=confirmacao_id,
                    name=CONFIRMATION_FUNCTION_NAME,
                    response={"confirmed": confirmado},
                )
            )
        ],
    )
    return await _executar_turno(session_id, apartamento, new_message)


async def listar_eventos(session_id: str) -> Optional[list[dict[str, Any]]]:
    apartamento = db.apartamento_da_sessao(session_id)
    if apartamento is None:
        return None
    sessao = await _obter_sessao(session_id, apartamento)
    return [ev.model_dump(mode="json", exclude_none=True) for ev in sessao.events]


def sessao_existe(session_id: str) -> bool:
    return db.apartamento_da_sessao(session_id) is not None

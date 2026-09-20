"""API HTTP do assistente do Residencial Aurora (contrato do desafio)."""

from __future__ import annotations

from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import adk_runtime, db


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Residencial Aurora", lifespan=lifespan)


class SessaoCreate(BaseModel):
    apartamento: str


class SessaoCreated(BaseModel):
    session_id: str


class MensagemCreate(BaseModel):
    texto: str


class ConfirmacaoCreate(BaseModel):
    id: str
    confirmado: bool


class TurnoResponse(BaseModel):
    resposta: str
    confirmacoes_pendentes: list[dict]


@app.post("/sessoes", status_code=201, response_model=SessaoCreated)
async def criar_sessao(body: SessaoCreate) -> SessaoCreated:
    session_id = await adk_runtime.criar_sessao(body.apartamento)
    return SessaoCreated(session_id=session_id)


@app.post("/sessoes/{session_id}/mensagens", response_model=TurnoResponse)
async def enviar_mensagem(session_id: str, body: MensagemCreate) -> TurnoResponse:
    resultado = await adk_runtime.enviar_mensagem(session_id, body.texto)
    if resultado is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    return TurnoResponse(**resultado)


@app.post("/sessoes/{session_id}/confirmacoes", response_model=TurnoResponse)
async def responder_confirmacao(
    session_id: str, body: ConfirmacaoCreate
) -> TurnoResponse:
    if not adk_runtime.sessao_existe(session_id):
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    try:
        resultado = await adk_runtime.responder_confirmacao(
            session_id, body.id, body.confirmado
        )
    except adk_runtime.ConfirmacaoInvalida:
        raise HTTPException(
            status_code=409,
            detail="Não existe confirmação pendente com esse id nesta sessão.",
        )
    if resultado is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    return TurnoResponse(**resultado)


@app.get("/sessoes/{session_id}/eventos")
async def listar_eventos(session_id: str) -> list[dict]:
    eventos = await adk_runtime.listar_eventos(session_id)
    if eventos is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    return eventos


@app.get("/apartamentos/{numero}/reservas")
async def reservas_do_apartamento(numero: str) -> list[dict]:
    return db.reservas_do_apartamento(numero)


@app.get("/apartamentos/{numero}/visitantes")
async def visitantes_do_apartamento(numero: str) -> list[dict]:
    return db.visitantes_do_apartamento(numero)

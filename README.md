# Residencial Aurora — assistente virtual do condomínio

Assistente virtual do Residencial Aurora, construído com o Google ADK e exposto por uma API HTTP própria (FastAPI). Os moradores reservam áreas comuns, cancelam as próprias reservas e autorizam visitantes por chat; toda regra que envolve cobrança, acesso ou concorrência é imposta em código, nunca deixada por conta do modelo.

## Arquitetura

O assistente é um agente principal que só roteia a conversa, e três especialistas que fazem o trabalho de fato. Nenhum deles conversa direto com o morador sobre um assunto que não é o seu — quando o pedido foge do escopo, o especialista transfere de volta para o principal, que redireciona.

```
atendente_aurora (principal)
├── especialista_reservas      → listar_areas_comuns, verificar_disponibilidade,
│                                 minhas_reservas, criar_reserva, cancelar_reserva
├── especialista_visitantes    → autorizar_visitante, meus_visitantes
└── especialista_regulamento   → consultar_regulamento
```

- **`atendente_aurora`** (`app/agents/root.py`): o único agente que o morador "vê" primeiro. Só decide para qual especialista transferir a conversa; não tem nenhuma tool própria, não acessa dados do condomínio e não recebe o regulamento nas instruções. É acionado automaticamente pelo Runner do ADK a cada nova mensagem da sessão.
- **`especialista_reservas`** (`app/agents/reservas.py`): dono de tudo relacionado a reservar, consultar disponibilidade, listar e cancelar reservas do salão de festas, churrasqueira e quadra. Existe separado do principal porque é ele quem concentra a regra de confirmação por cobrança (Garantia 1) e a exclusividade de reserva (Garantia 5).
- **`especialista_visitantes`** (`app/agents/visitantes.py`): dono da autorização e consulta de visitantes. Separado do especialista de reservas porque tem uma política de confirmação diferente (autorizar visitante *sempre* exige confirmação, reservar só exige quando a área tem taxa).
- **`especialista_regulamento`** (`app/agents/regulamento_agent.py`): a única porta de entrada para dúvidas sobre o regulamento interno. Isolado dos demais porque é o único que usa a tool de busca no regulamento (Garantia 4); os outros dois nunca precisam tocar nesse conteúdo.

Cada especialista é acionado por **transferência de agente** (`sub_agents` do ADK, mecanismo padrão do curso): o agente principal chama a tool automática `transfer_to_agent` gerada pelo ADK para o especialista certo, e o especialista pode transferir de volta para o principal se o assunto mudar no meio da conversa. Essa topologia (em vez de `AgentTool`) foi escolhida porque é o que faz o `Runner` conseguir achar sozinho, via `find_agent_to_run`, qual agente deve receber a resposta de uma confirmação pendente — ver a nota na Garantia 1.

Reservas e visitantes nunca são lidos ou gravados "de memória" pelo modelo: toda leitura e escrita passa pelas tools em `app/db.py`, que falam com um SQLite próprio (`.data/condominio.db`, fora de `dados/`).

## Garantias

### Garantia 1 — cobrança ou acesso só com confirmação

- `app/agents/reservas.py:56-63` (`_reserva_requer_confirmacao`) e `app/agents/reservas.py:172-174` (`FunctionTool(criar_reserva, require_confirmation=_reserva_requer_confirmacao)`): a tool `criar_reserva` só é executada de fato quando `require_confirmation` — um callback do próprio framework, avaliado a partir da taxa da área (`db.taxa_da_area`) — devolve `False`, ou quando já existe uma confirmação aprovada. Isso é decidido pelo ADK antes mesmo de o corpo da função rodar; o modelo não escolhe se confirma ou não.
- `app/agents/visitantes.py:86-87`: `FunctionTool(autorizar_visitante, require_confirmation=True)` — autorizar visitante sempre libera acesso, então sempre pausa para confirmação, incondicionalmente.
- `app/adk_runtime.py:74-98` (`_confirmacoes_pendentes`): a lista de `confirmacoes_pendentes` devolvida pela API é derivada só dos eventos gravados na sessão (procura por chamadas da function `adk_request_confirmation` sem resposta ainda), nunca de algo que o modelo "lembra" ou relata.
- `app/adk_runtime.py:139-163` (`responder_confirmacao`) e `app/main.py` (rota `POST /sessoes/{id}/confirmacoes`): antes de retomar a execução, o código confere se o `id` recebido está na lista de pendências *daquela sessão*; se não estiver — porque não existe, já foi respondido, ou é de outra sessão — levanta `ConfirmacaoInvalida`, que a rota converte em `409` sem executar nada. A confirmação em si é enviada ao Runner como um `FunctionResponse` (`{"confirmed": true/false}`), o formato de resposta documentado pelo próprio ADK para clientes fora do `adk web` — nunca como texto interpretado pelo modelo.

Por que não depende do modelo: mesmo que o morador escreva "já confirmei" ou "pode liberar direto" na conversa, a tool (`criar_reserva`/`autorizar_visitante`) só executa a ação quando `tool_context.tool_confirmation.confirmed` vier `True` — e esse campo só é preenchido pelo ADK quando a rota `/confirmacoes` de fato chama o Runner com a resposta. Frase nenhuma no texto muda isso; só testamos isso ao vivo contra a API (não só no `adk web`), como o enunciado recomenda, porque a topologia de agentes e o serviço de sessão afetam se a resposta chega ao especialista certo — ver a próxima garantia.

### Garantia 2 — cada sessão pertence a um apartamento

- `app/adk_runtime.py:52-61` (`criar_sessao`): o apartamento é gravado em `state={"apartamento": apartamento}` uma única vez, no `session_service.create_session(...)`, no momento em que a sessão é criada por `POST /sessoes`. Depois disso, nada nas tools volta a escrever esse valor.
- `app/agents/reservas.py:16-21` e `app/agents/visitantes.py:16-19` (`_apartamento(tool_context)`): toda tool que precisa saber "de quem" é a reserva/visitante lê `tool_context.state.get("apartamento")` — o state da sessão, gerido pelo ADK — e nunca recebe apartamento como argumento vindo do modelo. Nenhuma tool do projeto tem um parâmetro `apartamento`.
- `app/db.py:225-241` (`cancelar_reserva`): o `UPDATE` que cancela já filtra por `apartamento = ?` na mesma query que filtra pelo código; se a reserva for de outro apartamento (ou não existir), o resultado é idêntico — `rowcount == 0` — e a tool devolve a mesma mensagem genérica nos dois casos, sem nunca revelar que o código pertence a outro morador.
- `app/db.py:183-189` (`disponibilidade`): a checagem de data devolve só `"livre"`/`"ocupada"`, nunca o apartamento da reserva existente.
- `app/agents/root.py` e as instruções dos especialistas reforçam, em texto, para nunca aceitar um apartamento diferente dito pelo morador — mas isso é reforço de UX, não a garantia em si: mesmo que o modelo ignorasse a instrução e tentasse, não existe tool capaz de agir sobre outro apartamento, porque nenhuma aceita esse dado como entrada.

### Garantia 3 — nada se perde no reinício

- `app/adk_runtime.py:35-40`: as sessões (e todos os eventos) são persistidas via `SqliteSessionService(db_path=str(config.SESSIONS_DB))` — armazenamento em arquivo, não em memória —, então reiniciar o processo não apaga a conversa nem o state da sessão.
- `app/db.py:125-131` (`init_db`): reservas e visitantes moram em outro SQLite (`condominio.db`), também em arquivo. `init_db()` só popula os dados iniciais de `dados/` quando a chave `meta.seeded` ainda não existe (primeira execução); num reinício normal, o schema já existe e nada é resemeado, então as reservas/cancelamentos/visitantes feitos antes do reinício continuam lá.
- `app/config.py:21-23`: os dois arquivos (`sessions.db`, `condominio.db`) ficam em `.data/`, fora de `dados/` (que nunca é escrito) e fora do controle de versão.
- `app/main.py` (`lifespan`): `db.init_db()` roda no startup da API — sempre idempotente pelo ponto acima — e não em cada request.

### Garantia 4 — o regulamento é consultado, não carregado

- `app/agents/root.py:12-43` (`INSTRUCOES` do agente principal): o texto de instrução do agente principal descreve os especialistas por nome, mas nunca inclui trecho algum do regulamento.
- `app/regulamento.py:58-96` (`_carregar_artigos`): o regulamento é lido uma única vez do disco e dividido em blocos por artigo (cada `Art. N`, com o capítulo a que pertence), não em um bloco único.
- `app/regulamento.py:112-149` (`buscar`): a busca pontua os artigos por sobreposição de termos com a pergunta, ponderada pela raridade do termo entre os artigos (para que uma palavra comum como "condomínio" não domine o ranking), e descarta qualquer artigo com pontuação muito abaixo do melhor resultado — só os 1–2 artigos realmente relevantes voltam da função.
- `app/agents/regulamento_agent.py:11-31` (`consultar_regulamento`): é a única tool que lê `dados/regulamento.md` (via `regulamento.buscar`); o resultado devolvido para a sessão é só o texto desses artigos, nunca o arquivo inteiro.

Por que não depende do modelo: o modelo nunca vê o regulamento fora do retorno desta tool — não está nas instruções de nenhum agente — então não tem como "citar de memória" um capítulo que não veio na resposta da tool. Testado ao vivo: a pergunta "Até que horas a piscina funciona aos domingos?" traz só o Art. 22 (Capítulo IV — Piscina) na resposta e nos eventos da sessão, com o horário certo (9h às 20h).

### Garantia 5 — dois moradores, uma reserva

- `app/db.py:39-41` (índice `ux_reserva_ativa_area_data`): um índice único **parcial** do SQLite, `UNIQUE(area, data) WHERE status = 'ativa'`, imposto pelo próprio banco — não é uma checagem em Python.
- `app/db.py:192-222` (`criar_reserva`): a leitura do próximo código (`UPDATE codigo_seq ... RETURNING n`) e o `INSERT` da reserva acontecem dentro da mesma transação `BEGIN IMMEDIATE`. O SQLite serializa transações de escrita concorrentes nesse arquivo: a segunda transação só prossegue depois que a primeira termina, e nesse momento o índice único já teria sido violado, então ela falha com `IntegrityError` — capturado e convertido em `{"status": "indisponivel", ...}`, uma resposta HTTP normal, nunca um erro de servidor.

Por que a exclusividade vale no instante da gravação, e não só numa checagem prévia: não existe um "verifica se está livre" separado do "grava" — é o próprio `INSERT`, dentro da transação, que só é aceito pelo SQLite se nenhuma outra reserva ativa para a mesma área/data existir *naquele instante*. Duas reservas concorrentes para a mesma área/data (testado disparando as duas aprovações ao mesmo tempo, em threads/conexões diferentes) sempre resultam em exatamente uma reserva ativa: a que perde a corrida recebe `IntegrityError`, nunca um estado inconsistente.

## Como rodar

### Pré-requisitos

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) instalado
- Uma chave do [Google AI Studio](https://aistudio.google.com/apikey)

### Configuração

```bash
cp .env.example .env
# edite .env e preencha GOOGLE_API_KEY
```

Variáveis em `.env`:

| Variável | Obrigatória | Descrição |
|---|---|---|
| `GOOGLE_API_KEY` | sim | Chave do Google AI Studio. |
| `GOOGLE_GENAI_USE_VERTEXAI` | sim | Deixe `FALSE` para usar a API do Google AI Studio (não Vertex). |
| `GOOGLE_MODEL` | não | Modelo Gemini usado por todos os agentes. Padrão: `gemini-flash-latest`. |

### Instalar dependências

```bash
uv sync
```

Instala o projeto com a versão exata do Google ADK fixada em `pyproject.toml` (`google-adk==2.9.1`, série 2, ≥ 2.2.0).

### Restaurar os dados iniciais

```bash
uv run python -m app.restore
```

Apaga o estado de runtime (`.data/`) e recria reservas e visitantes a partir de `dados/reservas.json` e `dados/visitantes.json`. Também apaga as sessões existentes (decisão deste projeto: um estado limpo e previsível a cada restauração). Os arquivos em `dados/` nunca são alterados por este comando nem por nenhum outro — são só lidos.

### Subir a API

```bash
uv run uvicorn app.main:app --port 8000
```

A API sobe em `http://localhost:8000`. Na primeira execução (sem ter rodado o restore antes), os dados iniciais de `dados/` são carregados automaticamente. Reiniciar este comando não apaga conversas nem dados (Garantia 3) — para voltar ao estado inicial, use o comando de restauração acima antes de subir a API de novo.

### Testando rapidamente

```bash
curl -X POST http://localhost:8000/sessoes -H "Content-Type: application/json" -d '{"apartamento":"101"}'
curl -X POST http://localhost:8000/sessoes/<session_id>/mensagens -H "Content-Type: application/json" -d '{"texto":"Quero reservar o salão de festas para 2030-04-20"}'
curl -X POST http://localhost:8000/sessoes/<session_id>/confirmacoes -H "Content-Type: application/json" -d '{"id":"<id da pendência>","confirmado":true}'
curl http://localhost:8000/apartamentos/101/reservas
```

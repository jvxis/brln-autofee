# BRLN Orchestrator

Coordenador em **Python** que integra os scripts legados `brln-autofee.py`, `lndg_AR_trigger.py` e `ai_param_tuner.py` em um **único** processo, com estado persistido em **SQLite** e serviços externos encapsulados.

## Requisitos

* Python 3.11 ou superior
* `lncli` instalado no PATH (ou caminho absoluto)
* `bos` instalado **OU** LND REST API habilitada (recomendado)
* Node LND com LNDg (API HTTP e banco SQLite acessíveis)
* Conta Amboss com token GraphQL
* Opcional: bot do Telegram para notificações

## Instalação

### 1. Instalar uv (gerenciador de pacotes)

O projeto usa [uv](https://docs.astral.sh/uv/) para gerenciamento de dependências.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
```

### 2. Clonar o repositório

```bash
git clone https://github.com/jvxis/brln-autofee.git
cd brln-autofee
```

### 3. Instalar dependências

```bash
uv sync
```

Isso cria automaticamente o virtualenv (`.venv/`) e instala todas as dependências.


## Inicialização do SQLite

```bash
python3 -m brln_orchestrator init-db
```

O banco (`brln_orchestrator.sqlite3`) será criado na pasta atual.


## Configuração de segredos

```bash
python3 -m brln_orchestrator set-secret \
  --amboss-token "SEU_TOKEN" \
  --telegram-token "TOKEN_TELEGRAM" \
  --telegram-chat "ID_CHAT" \
  --lndg-url "http://HOST:PORTA" \
  --lndg-user "usuario" \
  --lndg-pass "senha" \
  --lndg-db-path "/caminho/para/lndg/data/db.sqlite3" \
  --bos-path "/caminho/para/bos" \
  --lncli-path "/caminho/para/lncli"
```

* Caso não utilize Telegram, omita os parâmetros correspondentes.

## Agente explicador do Telegram (AutoFee)

Use este agente para interpretar e resumir as mensagens do Telegram geradas pelo AutoFee e pelo orquestrador:
[BR LN AutoFee Explicador](https://chatgpt.com/g/g-6952866d52d88191b099fdf114f3cd42-br-ln-autofee-explicador)

Cole o output do bot no agente para obter uma explicacao mais clara.

## LND REST API (Recomendado)

Por padrão, o orquestrador usa o `bos` (Balance of Satoshis) para atualizar taxas dos canais. Cada chamada ao `bos` abre um novo processo e uma nova conexão com o LND, o que pode sobrecarregar o PostgreSQL se o LND usar esse backend.

A **LND REST API** resolve esse problema usando uma **sessão HTTP persistente** (keep-alive), reutilizando a mesma conexão para todas as atualizações.

### Benefícios

| BOS (padrão) | REST API |
|--------------|----------|
| 1 processo por canal | 1 sessão HTTP reutilizada |
| Cada processo abre conexão LND→PostgreSQL | Uma conexão persistente |
| Overhead de Node.js | Python nativo |

### Configuração

```bash
python3 -m brln_orchestrator set-secret \
  --lnd-rest-host "localhost:8080" \
  --lnd-macaroon-path "/caminho/para/.lnd/data/chain/bitcoin/mainnet/admin.macaroon" \
  --lnd-tls-cert-path "/caminho/para/.lnd/tls.cert" \
  --use-lnd-rest 1
```

* `--lnd-rest-host`: Host e porta da REST API do LND (padrão: `localhost:8080`)
* `--lnd-macaroon-path`: Caminho do `admin.macaroon` (padrão: `~/.lnd/data/chain/bitcoin/mainnet/admin.macaroon`)
* `--lnd-tls-cert-path`: Caminho do `tls.cert` (padrão: `~/.lnd/tls.cert`)
* `--use-lnd-rest 1`: Ativa a REST API (use `0` para voltar ao BOS)

### Verificar porta REST do LND

```bash
grep -i "restlisten" ~/.lnd/lnd.conf
# Exemplo: restlisten=0.0.0.0:8080
```

Ao iniciar, o orquestrador exibirá `🔌 Usando LND REST API (sessão persistente)` se a REST API estiver ativa.

## Exclusões

Importe listas antigas (pubkeys ou channel IDs) uma única vez:

```bash
python3 migrate-exclusion.py --db brln_orchestrator.sqlite3 \
  --autofee brln-autofee.py \
  --ar lndg_AR_trigger.py
```

O mesmo utilitário também migra a `FORCE_SOURCE_LIST` do trigger legado para a nova tabela dedicada.

Gerencie manualmente:

```bash
python3 -m brln_orchestrator exclusions add 0247... --note "Parceiro"
python3 -m brln_orchestrator exclusions rm 0247...
python3 -m brln_orchestrator exclusions list
```

Use sempre `pubkeys` para excluir do Autofee e `channel IDs` para excluir do Rebalance.

As exclusões são aplicadas tanto no AutoFee (pubkeys) quanto no AR Trigger (channel IDs).
Em **dry-run**, o console lista as entradas ignoradas para conferência rápida.

## Canais forçados como source

Canal em `FORCE_SOURCE_LIST` continua válido, agora via SQLite:

```bash
python3 -m brln_orchestrator forced-sources add 9737... --note "nome do peer"
python3 -m brln_orchestrator forced-sources rm 9737...
python3 -m brln_orchestrator forced-sources list
```

Em **dry-run**, o AR Trigger mostra os canais forçados antes de executar o legado.

### Variáveis de ambiente úteis

* `EXCL_DRY_VERBOSE=1` força o AutoFee a mostrar no console/Telegram as alterações "🚷excl-dry" (executa dry-run apenas para consulta).

Exemplo:

```bash
EXCL_DRY_VERBOSE=1 python3 -m brln_orchestrator run --dry-run-autofee --dry-run-ar
```

## Execução

### Dry-run típico

```bash
python3 -m brln_orchestrator run \
  --mode moderado \
  --dry-run-autofee \
  --dry-run-ar \
  --dry-run-tuner \
  --loop-interval-autofee 120 \
  --loop-interval-ar 120 \
  --loop-interval-tuner 600
```

### Execução completa (sem dry-run, todos os parâmetros)

```bash
python3 -m brln_orchestrator run \
  --mode moderado \
  --monthly-profit-ppm 200 \
  --monthly-profit-sat 200000 \
  --loop-interval-autofee 3600 \
  --loop-interval-ar 300 \
  --loop-interval-tuner 7200 \
  --no-dry-run-autofee \
  --no-dry-run-ar \
  --no-dry-run-tuner \
  --didactic-explain \
  --didactic-detailed
```

Por padrão, os intervalos são 600s (AutoFee), 300s (AR) e 1800s (Tuner).
Ajuste conforme a cadência desejada; para observar apenas um módulo, defina intervalos altos nos demais (ex.: `--loop-interval-ar 3600`).
Use `--no-autofee`, `--no-ar` ou `--no-tuner` para desativar loops específicos.
Adicione `--once` para executar uma única rodada e encerrar.

Se voce tiver ligado algum `--dry-run-*` em execucoes anteriores e quiser voltar ao modo real, utilize as flags opostas para limpar o estado persistido: `--no-dry-run-autofee`, `--no-dry-run-ar` e/ou `--no-dry-run-tuner`. As flags de dry-run existem tambem para o Tuner; lembre-se de desativa-las se quiser que ele aplique overrides definitivos.

### Modos de operação

Os modos aplicam *presets* definidos em `brln_orchestrator/presets_modes.payload_json`, ajustando limites do AutoFee e do AR Trigger antes de cada ciclo:

* **conservador** (padrão): reduz teto e passos, alonga *cooldowns* e reforça o *peg* pelo *outrate* para priorizar estabilidade de margem.
* **moderado**: libera passos medianos, aumenta *bump* para drenados recorrentes e suaviza o *ROI-cap* para equilibrar receita e utilização.
* **agressivo**: amplia *step-cap* e *surges*, encurta *cooldowns* e torna o *ROI-cap* mais permissivo (sink até 90% do preço observado), reagindo mais rápido.

Escolha o modo com `--mode` (ou defina o padrão rodando uma única vez); o *preset* correspondente é carregado automaticamente nos loops seguintes.

### Metas de lucro mensal

Informe `--monthly-profit-ppm` e/ou `--monthly-profit-sat` para orientar o **AI Param Tuner**.
As metas são convertidas para a janela de 7 dias, e o *tuner* ajusta gradualmente *overrides* como `SURGE_K`, `TOP_REVENUE_SURGE_BUMP`, `REBAL_FLOOR_MARGIN`, `OUTRATE_FLOOR_FACTOR` e *cooldowns* até convergir com a margem desejada.
Os valores resultantes são persistidos em `overrides` (scope `autofee`) e aplicados a cada ciclo do orquestrador.

## Visualizar configuração

```bash
python3 -m brln_orchestrator show-config
```

## Estrutura do SQLite

As principais tabelas incluem:

* `meta`: pares chave/valor (versão, *settings*, etc.)
* `secrets`: credenciais e caminhos externos
* `autofee_cache`, `autofee_state`: estados legados migrados do JSON
* `overrides`: *overrides* do *tuner* (`scope = 'autofee'`)
* `legacy_store`: armazenamento genérico para dados herdados (`autofee_meta`, `assisted_ledger`, etc.)
* `telemetry_log`: registros de log por componente (`autofee`, `ar`, `tuner`)
* `amboss_series`: cache de séries da Amboss
* `exclusions`: pubkeys e channel IDs excluídos
* `forced_sources`: channel IDs fixados como source no AR Trigger

Não há arquivos JSON ou TXT externos; todo o estado persistente fica no SQLite.

## Logs e Telemetria

O projeto possui um sistema de logging centralizado com arquivos rotativos:

* `logs/brln.log` - Log principal (INFO+)
* `logs/brln.error.log` - Apenas erros (ERROR+)

Configurável via variáveis de ambiente:

| Variável | Valores | Padrão |
|----------|---------|--------|
| `BRLN_LOG_LEVEL` | DEBUG, INFO, WARNING, ERROR | INFO |
| `BRLN_LOG_FORMAT` | text, json | text |
| `BRLN_LOG_CONSOLE` | true, false | true |
| `BRLN_LOG_FILE` | true, false | true |

Exemplo com debug:
```bash
BRLN_LOG_LEVEL=DEBUG python3 -m brln_orchestrator run ...
```

Além disso, as saídas são gravadas em `telemetry_log` no SQLite:

```bash
sqlite3 brln_orchestrator.sqlite3 "SELECT ts,component,level,msg FROM telemetry_log ORDER BY ts DESC LIMIT 20;"
```


## Systemd Service

Para rodar o orquestrador como serviço do sistema:

### 1. Criar o arquivo de serviço

```bash
sudo nano /etc/systemd/system/brln-autofee.service
```

Cole o seguinte conteúdo (ajuste os caminhos e parâmetros conforme necessário):

```ini
[Unit]
Description=BRLN AutoFee Orchestrator - Lightning Network Fee Manager
After=lnd.service
Wants=lnd.service

[Service]
Type=simple
User=lnd
Group=lnd
WorkingDirectory=/home/lnd/brln-autofee
Environment="PATH=/home/lnd/brln-autofee/.venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/home/lnd/brln-autofee/.venv/bin/python3 -m brln_orchestrator run \
    --mode moderado \
    --no-dry-run-autofee \
    --no-dry-run-ar \
    --no-dry-run-tuner \
    --loop-interval-autofee 3600 \
    --loop-interval-ar 300 \
    --loop-interval-tuner 7200

Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### 2. Habilitar e iniciar o serviço

```bash
sudo systemctl daemon-reload
sudo systemctl enable brln-autofee
sudo systemctl start brln-autofee
```

### 3. Verificar status e logs

```bash
sudo systemctl status brln-autofee
sudo journalctl -fu brln-autofee
```

## Limpeza

Para remover o banco local:

```bash
rm brln_orchestrator.sqlite3
```

(Crie um backup antes).
O `.gitignore` já ignora `*.sqlite3` e `.venv/`.








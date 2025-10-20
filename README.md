# BRLN Orchestrator

Coordenador em **Python** que integra os scripts legados `brln-autofee.py`, `lndg_AR_trigger.py` e `ai_param_tuner.py` em um **único** processo, com estado persistido em **SQLite** e serviços externos encapsulados.

## Requisitos

* Python 3.11 ou superior
* `lncli` e `bos` instalados no PATH (ou caminhos absolutos)
* Node LND com LNDg (API HTTP e banco SQLite acessíveis)
* Conta Amboss com token GraphQL
* Opcional: bot do Telegram para notificações

### Dependências Python

```bash
python3 -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .\.venv\Scripts\Activate.ps1   # Windows PowerShell
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

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

* As saídas dos módulos são impressas no terminal e gravadas em `telemetry_log`.
* Falhas em loops registram o *stack trace* completo (ex.: `[autofee] loop failed: ...`).
* Use:

  ```bash
  sqlite3 brln_orchestrator.sqlite3 "SELECT ts,component,level,msg FROM telemetry_log ORDER BY ts DESC LIMIT 20;"
  ```

  para inspecionar rapidamente.


## Limpeza

Para remover o banco local:

```bash
rm brln_orchestrator.sqlite3
```

(Crie um backup antes).
O `.gitignore` já ignora `*.sqlite3` e `.venv/`.








#!/usr/bin/env bash
set -euo pipefail

# =========================================================
#  BRLN AutoFee Orchestrator - Update Script
#
#  ✔ Supports pyproject.toml + uv.lock (preferred)
#  ✔ Fallback to venv + requirements.txt
#  ✔ Optional systemd restart
#  ✔ Optional wrapper installation
#
#  All paths and names can be overridden via env vars.
# =========================================================

# ---------------------------------------------------------
# USER CONFIGURATION (override via environment variables)
# ---------------------------------------------------------
REPO_URL="${REPO_URL:-https://github.com/jvxis/brln-autofee.git}"
REPO_DIR="${REPO_DIR:-$HOME/brln-autofee}"
APP_DIR="${APP_DIR:-$REPO_DIR/brln_orchestrator}"

PYTHON_BIN="${PYTHON_BIN:-python3}"

SERVICE_NAME="${SERVICE_NAME:-brln-orchestrator}"
WRAPPER_PATH="${WRAPPER_PATH:-/usr/local/bin/brln-autofee-orchestrator}"

# Temporary hotfix for v0.4.11 (TypeVar import)
APPLY_HOTFIX_TYPEVAR="${APPLY_HOTFIX_TYPEVAR:-1}"

# ---------------------------------------------------------
# INTERNAL HELPERS
# ---------------------------------------------------------
banner() {
  echo
  echo "================================================="
  echo "  BRLN AutoFee Orchestrator - Update"
  echo "  REPO_DIR : ${REPO_DIR}"
  echo "  APP_DIR  : ${APP_DIR}"
  echo "  SERVICE  : ${SERVICE_NAME}"
  echo "================================================="
  echo
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "[ERRO] Comando não encontrado: $1"
    exit 1
  }
}

HAS_SYSTEMD=0
command -v systemctl >/dev/null && HAS_SYSTEMD=1

HAS_SUDO=0
if command -v sudo >/dev/null 2>&1; then
  sudo -n true 2>/dev/null && HAS_SUDO=1 || true
fi

banner
need_cmd git
need_cmd "$PYTHON_BIN"

# ---------------------------------------------------------
# UPDATE REPOSITORY
# ---------------------------------------------------------
if [ ! -d "$REPO_DIR/.git" ]; then
  echo "[*] Clonando repositório..."
  git clone "$REPO_URL" "$REPO_DIR"
else
  echo "[*] Atualizando repositório (pull --ff-only)..."
  git -C "$REPO_DIR" pull --ff-only
fi

if [ ! -d "$APP_DIR" ]; then
  echo "[ERRO] APP_DIR não encontrado: $APP_DIR"
  exit 1
fi

# ---------------------------------------------------------
# DEPENDENCY MANAGEMENT
# ---------------------------------------------------------
if [ -f "$REPO_DIR/pyproject.toml" ]; then
  echo "[*] pyproject.toml detectado → usando uv"

  if [ ! -d "$REPO_DIR/.venv" ]; then
    echo "[*] Criando venv em $REPO_DIR/.venv"
    "$PYTHON_BIN" -m venv "$REPO_DIR/.venv"
  fi

  # shellcheck disable=SC1091
  source "$REPO_DIR/.venv/bin/activate"

  pip install --upgrade pip setuptools wheel

  if ! command -v uv >/dev/null 2>&1; then
    echo "[*] Instalando uv..."
    pip install --upgrade uv
  fi

  echo "[*] Sincronizando dependências (uv sync)..."
  cd "$REPO_DIR"
  uv sync

  PY="$REPO_DIR/.venv/bin/python"

else
  echo "[*] Sem pyproject.toml → fallback venv + pip"

  cd "$APP_DIR"

  if [ ! -d ".venv" ]; then
    "$PYTHON_BIN" -m venv .venv
  fi

  # shellcheck disable=SC1091
  source ".venv/bin/activate"

  pip install --upgrade pip setuptools wheel

  if [ -f "$APP_DIR/requirements.txt" ]; then
    pip install -r "$APP_DIR/requirements.txt"
  elif [ -f "$REPO_DIR/requirements.txt" ]; then
    pip install -r "$REPO_DIR/requirements.txt"
  else
    echo "[ERRO] Nenhum requirements.txt encontrado."
    exit 1
  fi

  PY="$APP_DIR/.venv/bin/python"
fi

# ---------------------------------------------------------
# HOTFIX (explicit + documented)
# ---------------------------------------------------------
if [ "$APPLY_HOTFIX_TYPEVAR" -eq 1 ]; then
  LNDG_API="$APP_DIR/services/lndg_api.py"
  if grep -q "TypeVar(" "$LNDG_API" && ! grep -q "from typing import TypeVar" "$LNDG_API"; then
    echo "[!] Aplicando hotfix temporário (TypeVar import)"
    sed -i '1a from typing import TypeVar' "$LNDG_API"
  fi
fi

# ---------------------------------------------------------
# OPTIONAL WRAPPER INSTALLATION
# ---------------------------------------------------------
if [ "$HAS_SUDO" -eq 1 ]; then
  echo "[*] Instalando wrapper em $WRAPPER_PATH"

  sudo tee "$WRAPPER_PATH" >/dev/null <<EOF
#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR}"
APP_DIR="\${REPO_DIR}/brln_orchestrator"

if [ -x "\${REPO_DIR}/.venv/bin/python" ]; then
  PY="\${REPO_DIR}/.venv/bin/python"
elif [ -x "\${APP_DIR}/.venv/bin/python" ]; then
  PY="\${APP_DIR}/.venv/bin/python"
else
  echo "[ERRO] Nenhuma virtualenv encontrada."
  exit 1
fi

cd "\${REPO_DIR}"
exec "\${PY}" -m brln_orchestrator "\$@"
EOF

  sudo chmod +x "$WRAPPER_PATH"
else
  echo "[i] Sem sudo — wrapper não instalado."
fi

# ---------------------------------------------------------
# OPTIONAL SYSTEMD RESTART
# ---------------------------------------------------------
if [ "$HAS_SYSTEMD" -eq 1 ] && systemctl status "$SERVICE_NAME" >/dev/null 2>&1; then
  echo "[*] Reiniciando serviço: $SERVICE_NAME"
  sudo systemctl daemon-reload || true
  sudo systemctl reset-failed "$SERVICE_NAME" || true
  sudo systemctl restart "$SERVICE_NAME"
  systemctl status "$SERVICE_NAME" --no-pager -l | sed -n '1,25p'
else
  echo "[i] Serviço systemd não detectado — restart ignorado."
fi

# ---------------------------------------------------------
# DONE
# ---------------------------------------------------------
echo
echo "[✓] Atualização concluída com sucesso."
echo "    Teste sugerido:"
echo "    ${WRAPPER_PATH} show-config | sed -n '1,25p'"
echo

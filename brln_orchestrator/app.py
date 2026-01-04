from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

from .engines.autofee import AutoFeeEngine
from .engines.ar import ARTriggerEngine
from .engines.tuner import ParamTunerEngine
from .services.amboss import AmbossService
from .services.bos import BosService
from .services.lnd_rest import LndRestService
from .services.lndg_api import LNDgAPI
from .services.lncli import LncliService
from .services.telegram import TelegramService
from .storage import Storage

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from logging_config import setup_logging, get_logger

setup_logging()
logger = get_logger("app")

APP_VERSION = "0.4.12"
APP_VERSION_DESC = "AutoFee Integrado - Melhorias e Alteração de Fee por canal ao invés de PUBKEY - Troca BOS por LNDCLI e REST"
DEFAULT_DB_PATH = Path("brln_orchestrator.sqlite3")
DEFAULT_SETTINGS = {
    "mode": "conservador",
    "monthly_profit_goal_ppm": None,
    "monthly_profit_goal_sat": None,
    "loop_interval_autofee": 600,
    "loop_interval_ar": 300,
    "loop_interval_tuner": 1800,
    "dry_run_autofee": False,
    "dry_run_ar": False,
    "dry_run_tuner": False,
    "didactic_explain": False,
    "didactic_detailed": False,
}


def resolve_db_path(base: Optional[str]) -> Path:
    if base:
        return Path(base).expanduser().resolve()
    return (Path.cwd() / DEFAULT_DB_PATH).resolve()


def ensure_version(storage: Storage) -> None:
    if storage.get_meta("app_version") != APP_VERSION:
        storage.set_meta("app_version", APP_VERSION)
    if storage.get_meta("app_version_desc") != APP_VERSION_DESC:
        storage.set_meta("app_version_desc", APP_VERSION_DESC)


def load_settings(storage: Storage) -> Dict[str, Any]:
    raw = storage.get_meta("settings")
    if not raw:
        storage.set_meta("settings", json.dumps(DEFAULT_SETTINGS))
        return dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        storage.set_meta("settings", json.dumps(DEFAULT_SETTINGS))
        return dict(DEFAULT_SETTINGS)
    merged = dict(DEFAULT_SETTINGS)
    merged.update(data)
    return merged


def save_settings(storage: Storage, settings: Dict[str, Any]) -> None:
    storage.set_meta("settings", json.dumps(settings))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="brln-orchestrator", description="Coordinator for AutoFee, AR Trigger and Param Tuner")
    parser.add_argument("--db", dest="db_path", help="Path para o SQLite do orquestrador")

    sub = parser.add_subparsers(dest="command")

    init_cmd = sub.add_parser("init-db", help="Inicializa o banco de dados")
    init_cmd.add_argument("--db", dest="db_path", help="Path do SQLite")

    secret_cmd = sub.add_parser("set-secret", help="Atualiza Segredos/tokens")
    secret_cmd.add_argument("--amboss-token")
    secret_cmd.add_argument("--telegram-token")
    secret_cmd.add_argument("--telegram-chat")
    secret_cmd.add_argument("--lndg-url")
    secret_cmd.add_argument("--lndg-user")
    secret_cmd.add_argument("--lndg-pass")
    secret_cmd.add_argument("--lndg-db-path")
    secret_cmd.add_argument("--bos-path", help="(legado) caminho do bos (fallback)")
    secret_cmd.add_argument("--lncli-path")
    secret_cmd.add_argument("--lnd-rest-host", help="default = localhost:8080)")
    secret_cmd.add_argument("--lnd-macaroon-path", help="default = ~/.lnd/data/chain/bitcoin/mainnet/admin.macaroon")
    secret_cmd.add_argument("--lnd-tls-cert-path", help="default = ~/.lnd/tls.cert")
    secret_cmd.add_argument("--use-lnd-rest", type=int, choices=[0, 1], help="1=usar REST API, 0=usar BOS")

    excl_cmd = sub.add_parser("exclusions", help="Gerencia exclusoes")
    excl_sub = excl_cmd.add_subparsers(dest="action", required=True)
    excl_add = excl_sub.add_parser("add")
    excl_add.add_argument("identifier")
    excl_add.add_argument("--note", default="")
    excl_rm = excl_sub.add_parser("rm")
    excl_rm.add_argument("identifier")
    excl_list = excl_sub.add_parser("list")

    forced_cmd = sub.add_parser("forced-sources", help="Gerencia canais forçados como source")
    forced_sub = forced_cmd.add_subparsers(dest="action", required=True)
    forced_add = forced_sub.add_parser("add")
    forced_add.add_argument("identifier")
    forced_add.add_argument("--note", default="")
    forced_rm = forced_sub.add_parser("rm")
    forced_rm.add_argument("identifier")
    forced_list = forced_sub.add_parser("list")

    migrate_cmd = sub.add_parser("migrate-exclusions", help="Importa exclusoes dos scripts legados")
    migrate_cmd.add_argument("--autofee", default="brln-autofee.py")
    migrate_cmd.add_argument("--ar", default="lndg_AR_trigger.py")

    show_cmd = sub.add_parser("show-config", help="Mostra configuracao atual")

    run_cmd = sub.add_parser("run", help="Executa os mdulos")
    run_cmd.add_argument("--mode", choices=["conservador", "moderado", "agressivo"])
    run_cmd.add_argument("--monthly-profit-ppm", type=int)
    run_cmd.add_argument("--monthly-profit-sat", type=int)
    run_cmd.add_argument("--loop-interval-autofee", type=int)
    run_cmd.add_argument("--loop-interval-ar", type=int)
    run_cmd.add_argument("--loop-interval-tuner", type=int)
    run_cmd.set_defaults(dry_run_autofee=None, dry_run_ar=None, dry_run_tuner=None)
    autofee_dry = run_cmd.add_mutually_exclusive_group()
    autofee_dry.add_argument("--dry-run-autofee", dest="dry_run_autofee", action="store_true")
    autofee_dry.add_argument("--no-dry-run-autofee", dest="dry_run_autofee", action="store_false")
    ar_dry = run_cmd.add_mutually_exclusive_group()
    ar_dry.add_argument("--dry-run-ar", dest="dry_run_ar", action="store_true")
    ar_dry.add_argument("--no-dry-run-ar", dest="dry_run_ar", action="store_false")
    tuner_dry = run_cmd.add_mutually_exclusive_group()
    tuner_dry.add_argument("--dry-run-tuner", dest="dry_run_tuner", action="store_true")
    tuner_dry.add_argument("--no-dry-run-tuner", dest="dry_run_tuner", action="store_false")
    run_cmd.add_argument("--didactic-explain", action="store_true")
    run_cmd.add_argument("--didactic-detailed", action="store_true")
    run_cmd.add_argument("--no-autofee", action="store_true")
    run_cmd.add_argument("--no-ar", action="store_true")
    run_cmd.add_argument("--no-tuner", action="store_true")
    run_cmd.add_argument("--no-ar-no-telegram", action="store_true", help="Nao envia Telegram do AR Trigger quando mudanças=0")
    run_cmd.add_argument("--once", action="store_true", help="Executa apenas um ciclo completo e encerra")

    return parser


def handle_init_db(args: argparse.Namespace) -> None:
    db_path = resolve_db_path(getattr(args, "db_path", None))
    logger.info(f"Inicializando banco de dados em {db_path}")
    storage = Storage(db_path)
    ensure_version(storage)
    storage.close()
    logger.info(f"Banco inicializado com sucesso: {db_path}")
    print(f"[ok] banco Inicializado em {db_path}")


def handle_set_secret(storage: Storage, args: argparse.Namespace) -> None:
    payload = {}
    arg_to_col = {
        "amboss_token": "amboss_token",
        "telegram_token": "telegram_token",
        "telegram_chat": "telegram_chat",
        "lndg_url": "lndg_url",
        "lndg_user": "lndg_user",
        "lndg_pass": "lndg_pass",
        "lndg_db_path": "lndg_db_path",
        "bos_path": "bos_path",
        "lncli_path": "lncli_path",
        "lnd_rest_host": "lnd_rest_host",
        "lnd_macaroon_path": "lnd_macaroon_path",
        "lnd_tls_cert_path": "lnd_tls_cert_path",
        "use_lnd_rest": "use_lnd_rest",
    }
    for arg_name, col_name in arg_to_col.items():
        value = getattr(args, arg_name, None)
        if value is not None:
            payload[col_name] = value
    if not payload:
        print("Nada para atualizar.")
        return
    storage.update_secrets(**payload)
    print("[ok] Segredos atualizados.")


def handle_exclusions(storage: Storage, args: argparse.Namespace) -> None:
    if args.action == "add":
        storage.set_exclusion(args.identifier, args.note)
        print(f"[ok] excluso registrada: {args.identifier}")
    elif args.action == "rm":
        storage.remove_exclusion(args.identifier)
        print(f"[ok] excluso removida: {args.identifier}")
    elif args.action == "list":
        data = storage.list_exclusions()
        if not data:
            print("(vazio)")
            return
        for identifier, note in sorted(data.items()):
            suffix = f" - {note}" if note else ""
            print(f"{identifier}{suffix}")


def handle_forced_sources(storage: Storage, args: argparse.Namespace) -> None:
    if args.action == "add":
        storage.set_forced_source(args.identifier, args.note)
        print(f"[ok] canal forçado como source: {args.identifier}")
    elif args.action == "rm":
        storage.remove_forced_source(args.identifier)
        print(f"[ok] canal removido da lista de source: {args.identifier}")
    elif args.action == "list":
        data = storage.list_forced_sources()
        if not data:
            print("(vazio)")
            return
        for identifier, note in sorted(data.items()):
            suffix = f" - {note}" if note else ""
            print(f"{identifier}{suffix}")


def migrate_exclusions(storage: Storage, autofee_path: Path, ar_path: Path) -> None:
    imported = 0
    if autofee_path.exists():
        text = autofee_path.read_text(encoding="utf-8", errors="ignore")
        if "EXCLUSION_LIST" in text:
            start = text.index("EXCLUSION_LIST")
            snippet = text[start:]
            left = snippet.split("=", 1)[1]
            brace = left.split("}", 1)[0]
            entries = brace.split("\n")
            for line in entries:
                line = line.strip().strip(",")
                if not line or line.startswith("#"):
                    continue
                if "#" in line:
                    value, note = line.split("#", 1)
                    note = note.strip()
                else:
                    value, note = line, ""
                value = value.strip("'\"")
                if value:
                    storage.set_exclusion(value, note)
                    imported += 1
    if ar_path.exists():
        text = ar_path.read_text(encoding="utf-8", errors="ignore")
        if "EXCLUSION_LIST" in text:
            start = text.index("EXCLUSION_LIST")
            snippet = text[start:]
            body = snippet.split("[", 1)[1].split("]", 1)[0]
            for line in body.split(","):
                identifier = line.strip().strip("'\"")
                if identifier:
                    storage.set_exclusion(identifier, storage.list_exclusions().get(identifier))
                    imported += 1
    print(f"[ok] {imported} exclusoes importadas.")


def handle_show_config(storage: Storage) -> None:
    secrets = storage.get_secrets()
    settings = load_settings(storage)
    print("Verso:", f"{storage.get_meta('app_version')} ({storage.get_meta('app_version_desc') or ''})")
    print("\nSegredos:")
    for key in ("amboss_token", "telegram_token", "telegram_chat", "lndg_url", "lndg_user", "lndg_db_path", "bos_path", "lncli_path"):
        value = secrets.get(key)
        if not value:
            print(f"  {key}: <no configurado>")
        elif "token" in key or "pass" in key:
            print(f"  {key}: ***")
        else:
            print(f"  {key}: {value}")

    use_rest = secrets.get("use_lnd_rest")
    print(f"\nLND REST API (use_lnd_rest={use_rest or 0}):")
    for key in ("lnd_rest_host", "lnd_macaroon_path", "lnd_tls_cert_path"):
        value = secrets.get(key)
        if not value:
            print(f"  {key}: <não configurado>")
        else:
            print(f"  {key}: {value}")

    print("\nConfiguracao:")
    for key, value in settings.items():
        print(f"  {key}: {value}")


def build_services(storage: Storage) -> Dict[str, Any]:
    logger.info("Inicializando serviços")
    secrets = storage.get_secrets()
    lncli = LncliService(secrets.get("lncli_path") or "lncli")

    use_lnd_rest = bool(secrets.get("use_lnd_rest"))
    fee_service = None
    lnd_rest = None

    if use_lnd_rest:
        try:
            lnd_rest = LndRestService(
                rest_host=secrets.get("lnd_rest_host") or "localhost:8080",
                macaroon_path=secrets.get("lnd_macaroon_path"),
                tls_cert_path=secrets.get("lnd_tls_cert_path"),
            )
            fee_service = lnd_rest
            logger.info("LND REST API inicializada com sessão persistente")
            print("🔌 Usando LND REST API (sessão persistente)")
        except Exception as exc:
            logger.warning(f"Falha ao inicializar LND REST: {exc}. Usando BOS (legado) como fallback")
            print(f"⚠️ Erro ao inicializar LND REST: {exc}. Fallback para BOS (legado).")
            fee_service = BosService(secrets.get("bos_path") or "bos")
    else:
        logger.info("Usando LNCLI updatechanpolicy para fees (BOS legado como fallback)")
        fee_service = BosService(secrets.get("bos_path") or "bos")

    telegram = TelegramService(secrets.get("telegram_token"), secrets.get("telegram_chat"))
    lndg_url = secrets.get("lndg_url")
    lndg_api = None
    if lndg_url:
        lndg_api = LNDgAPI(lndg_url, secrets.get("lndg_user"), secrets.get("lndg_pass"))
    amboss_token = secrets.get("amboss_token") or ""
    amboss = AmbossService(storage, amboss_token) if amboss_token else None
    return {
        "lncli": lncli,
        "bos": fee_service,
        "lnd_rest": lnd_rest,
        "telegram": telegram,
        "lndg_api": lndg_api,
        "amboss": amboss,
    }


def instantiate_engines(storage: Storage, services: Dict[str, Any]) -> Dict[str, Any]:
    root = Path(__file__).resolve().parent.parent
    autofee_engine = AutoFeeEngine(
        storage=storage,
        lncli=services["lncli"],
        bos=services["bos"],
        amboss=services["amboss"],
        telegram=services["telegram"],
        legacy_path=root / "brln-autofee.py",
    )
    ar_engine = None
    if services["lndg_api"] is not None:
        ar_engine = ARTriggerEngine(
            storage=storage,
            lndg_api=services["lndg_api"],
            telegram=services["telegram"],
            legacy_path=root / "lndg_AR_trigger.py",
        )
    tuner_engine = ParamTunerEngine(
        storage=storage,
        telegram=services["telegram"],
        legacy_path=root / "ai_param_tuner.py",
    )
    return {
        "autofee": autofee_engine,
        "ar": ar_engine,
        "tuner": tuner_engine,
    }


def run_module(func, label: str, *, storage: Storage) -> None:
    logger.debug(f"Executando módulo: {label}")
    start_time = time.time()
    try:
        output = func()
        elapsed = time.time() - start_time
        logger.info(f"Módulo {label} executado em {elapsed:.2f}s")
        if output:
            print(output.strip())
            storage.log(label, "INFO", output.strip(), None)
    except Exception as exc:
        elapsed = time.time() - start_time
        tb = traceback.format_exc().strip()
        logger.error(f"Módulo {label} falhou após {elapsed:.2f}s: {exc}")
        storage.log(label, "ERROR", str(exc), {"traceback": tb})
        print(f"[{label}] erro: {exc}\n{tb}", file=sys.stderr)


def handle_run(storage: Storage, args: argparse.Namespace) -> None:
    logger.info(f"Iniciando BRLN AutoFee v{APP_VERSION}")
    ensure_version(storage)
    settings = load_settings(storage)
    logger.debug(f"Configurações carregadas: {settings}")

    def resolve_toggle(arg_value: Optional[bool], key: str) -> bool:
        if arg_value is None:
            return bool(settings.get(key, False))
        return bool(arg_value)

    updates = {
        "mode": args.mode or settings.get("mode"),
        "monthly_profit_goal_ppm": args.monthly_profit_ppm if args.monthly_profit_ppm is not None else settings.get("monthly_profit_goal_ppm"),
        "monthly_profit_goal_sat": args.monthly_profit_sat if args.monthly_profit_sat is not None else settings.get("monthly_profit_goal_sat"),
        "loop_interval_autofee": args.loop_interval_autofee or settings.get("loop_interval_autofee", 600),
        "loop_interval_ar": args.loop_interval_ar or settings.get("loop_interval_ar", 300),
        "loop_interval_tuner": args.loop_interval_tuner or settings.get("loop_interval_tuner", 1800),
        "dry_run_autofee": resolve_toggle(args.dry_run_autofee, "dry_run_autofee"),
        "dry_run_ar": resolve_toggle(args.dry_run_ar, "dry_run_ar"),
        "dry_run_tuner": resolve_toggle(args.dry_run_tuner, "dry_run_tuner"),
        "didactic_explain": bool(args.didactic_explain or settings.get("didactic_explain")),
        "didactic_detailed": bool(args.didactic_detailed or settings.get("didactic_detailed")),
    }
    save_settings(storage, updates)

    services = build_services(storage)
    engines = instantiate_engines(storage, services)

    if engines["ar"] is None and not updates["dry_run_ar"] and not args.no_ar:
        raise RuntimeError("URL/credenciais do LNDg no configuradas. Use set-secret --lndg-url ...")

    loop_enabled = {
        "autofee": not args.no_autofee,
        "ar": not args.no_ar and engines["ar"] is not None,
        "tuner": not args.no_tuner,
    }

    intervals = {
        "autofee": updates["loop_interval_autofee"],
        "ar": updates["loop_interval_ar"],
        "tuner": updates["loop_interval_tuner"],
    }
    next_run = {name: 0.0 for name in intervals.keys()}

    once = args.once
    logger.info(f"Módulos habilitados: autofee={loop_enabled['autofee']}, ar={loop_enabled['ar']}, tuner={loop_enabled['tuner']}")
    logger.info(f"Intervalos: autofee={intervals['autofee']}s, ar={intervals['ar']}s, tuner={intervals['tuner']}s")
    try:
        while True:
            now = time.time()
            if loop_enabled["autofee"] and now >= next_run["autofee"]:
                run_module(
                    lambda: engines["autofee"].run(
                        mode=updates["mode"],
                        dry_run=updates["dry_run_autofee"],
                        didactic_explain=updates["didactic_explain"],
                        didactic_detailed=updates["didactic_detailed"],
                    ),
                    "autofee",
                    storage=storage,
                )
                next_run["autofee"] = now + intervals["autofee"]
            if loop_enabled["ar"] and now >= next_run["ar"]:
                run_module(
                    lambda: engines["ar"].run(
                        mode=updates["mode"],
                        dry_run=updates["dry_run_ar"],
                        no_telegram_when_no_changes=args.no_ar_no_telegram,
                    ),  # type: ignore
                    "ar",
                    storage=storage,
                )
                next_run["ar"] = now + intervals["ar"]
            if loop_enabled["tuner"] and now >= next_run["tuner"]:
                run_module(
                    lambda: engines["tuner"].run(
                        dry_run=updates["dry_run_tuner"],
                        force_telegram=False,
                        no_telegram=False,
                    ),
                    "tuner",
                    storage=storage,
                )
                next_run["tuner"] = now + intervals["tuner"]

            if once:
                break
            time.sleep(1)
    except KeyboardInterrupt:
        print("Encerrado pelo usuário.")
    finally:
        if services.get("lnd_rest"):
            try:
                services["lnd_rest"].close()
            except Exception:
                pass


def main(argv: Optional[list[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    db_path = resolve_db_path(getattr(args, "db_path", None))

    if args.command == "init-db":
        handle_init_db(args)
        return

    storage = Storage(db_path)
    try:
        if args.command == "set-secret":
            handle_set_secret(storage, args)
        elif args.command == "exclusions":
            handle_exclusions(storage, args)
        elif args.command == "forced-sources":
            handle_forced_sources(storage, args)
        elif args.command == "migrate-exclusions":
            migrate_exclusions(storage, Path(args.autofee), Path(args.ar))
        elif args.command == "show-config":
            ensure_version(storage)
            handle_show_config(storage)
        elif args.command == "run":
            handle_run(storage, args)
        else:
            parser.print_help()
    finally:
        storage.close()


if __name__ == "__main__":
    main()

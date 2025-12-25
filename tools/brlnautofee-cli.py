#!/usr/bin/env python3
import argparse
import re
import sys
from pathlib import Path


LINE_HINTS = ("alvo", "out_ratio", "out_ppm7d", "rebal_ppm7d")


def read_input(args: argparse.Namespace) -> str:
    if args.text:
        return args.text
    if args.file:
        return Path(args.file).read_text(encoding="utf-8", errors="ignore")
    if args.stdin or not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("no input provided (use --text, --file, or --stdin)")


def find_autofee_line(text: str, alias: str | None, line_index: int | None) -> tuple[str, list[str]]:
    lines = [line.rstrip("\n") for line in text.splitlines()]
    if line_index is not None:
        idx = line_index - 1
        if idx < 0 or idx >= len(lines):
            raise SystemExit(f"--line {line_index} out of range (1..{len(lines)})")
        return lines[idx].strip(), lines

    for line in lines:
        if not line.strip():
            continue
        if not any(hint in line for hint in LINE_HINTS):
            continue
        if alias and alias.lower() not in line.lower():
            continue
        return line.strip(), lines

    # fallback: first non-empty line
    for line in lines:
        if line.strip():
            return line.strip(), lines

    raise SystemExit("no content to decode")


def extract_followup(lines: list[str], base_line: str) -> list[str]:
    followup = []
    found = False
    for idx, line in enumerate(lines):
        if line.strip() == base_line.strip():
            found = True
            start = idx + 1
            break
    if not found:
        return followup

    for line in lines[start:]:
        if not line.strip():
            continue
        if line.lstrip().startswith("|"):
            continue
        if line.startswith(" ") or line.startswith("\t"):
            followup.append(line.strip())
        else:
            break
    return followup


def normalize_tag(token: str) -> str:
    return re.sub(r"[^A-Za-z0-9:+._/-]", "", token).lower()


def parse_header(header: str) -> dict:
    data = {"alias": None, "cid": None, "action": header.strip()}
    if ":" in header:
        left, right = header.split(":", 1)
        alias = left.strip()
        alias = re.sub(r"^[^A-Za-z0-9]+", "", alias).strip()
        data["alias"] = alias if alias else None
        data["action"] = right.strip()
        m = re.search(r"\((\d+)\)", left)
        if m:
            data["cid"] = m.group(1)
    return data


def parse_numbers(part: str) -> list[float]:
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", part)]


def parse_seed(part: str) -> dict:
    nums = parse_numbers(part)
    seed_val = int(nums[0]) if nums else None
    p65 = p95 = None
    m65 = re.search(r"p65[:=]?\s*(\d+)", part, re.IGNORECASE)
    m95 = re.search(r"p95[:=]?\s*(\d+)", part, re.IGNORECASE)
    if m65:
        p65 = int(m65.group(1))
    if m95:
        p95 = int(m95.group(1))
    cap = "cap" in part.lower()
    return {"seed": seed_val, "p65": p65, "p95": p95, "cap": cap}


def parse_floor(part: str) -> dict:
    num = None
    src = None
    nums = parse_numbers(part)
    if nums:
        num = int(nums[0])
    m = re.search(r"\(([^)]+)\)", part)
    if m:
        src = m.group(1)
    return {"floor": num, "src": src}


def parse_rebal(part: str) -> dict:
    num = None
    suffix = None
    nums = parse_numbers(part)
    if nums:
        num = int(nums[0])
    m = re.search(r"\(([^)]+)\)", part)
    if m:
        suffix = m.group(1)
    return {"rebal": num, "suffix": suffix}


def parse_inb(part: str) -> dict:
    nums = parse_numbers(part)
    prev = cur = net = None
    if len(nums) >= 2:
        prev, cur = int(nums[0]), int(nums[1])
    if len(nums) >= 3:
        net = int(nums[2])
    return {"prev": prev, "cur": cur, "net": net}


def parse_fee_lr(part: str) -> dict:
    nums = parse_numbers(part)
    local = remote = None
    if len(nums) >= 2:
        local, remote = int(nums[0]), int(nums[1])
    return {"local": local, "remote": remote}


def parse_metrics(parts: list[str]) -> tuple[dict, list[str]]:
    metrics = {}
    unknown_parts = []
    for part in parts:
        part_stripped = part.strip()
        lower = part_stripped.lower()
        if not part_stripped:
            continue
        if lower.startswith("alvo"):
            nums = parse_numbers(part_stripped)
            metrics["alvo"] = int(nums[0]) if nums else None
        elif "out_ratio" in lower:
            nums = parse_numbers(part_stripped)
            metrics["out_ratio"] = float(nums[0]) if nums else None
        elif "out_ppm7d" in lower:
            nums = parse_numbers(part_stripped)
            metrics["out_ppm7d"] = int(nums[0]) if nums else None
        elif "rebal_ppm7d" in lower:
            metrics.update(parse_rebal(part_stripped))
        elif lower.startswith("seed"):
            metrics.update(parse_seed(part_stripped))
        elif lower.startswith("floor"):
            metrics.update(parse_floor(part_stripped))
        elif lower.startswith("marg"):
            nums = parse_numbers(part_stripped)
            metrics["marg"] = int(nums[0]) if nums else None
        elif lower.startswith("rev_share"):
            nums = parse_numbers(part_stripped)
            metrics["rev_share"] = float(nums[0]) if nums else None
        elif lower.startswith("inb"):
            metrics["inb"] = parse_inb(part_stripped)
        elif "fee l/r" in lower or "fee l / r" in lower:
            metrics["fee_lr"] = parse_fee_lr(part_stripped)
        else:
            unknown_parts.append(part_stripped)
    return metrics, unknown_parts


def is_tag_token(norm: str) -> bool:
    if not norm:
        return False
    tag_keys = (
        "floor-lock",
        "stepcap-lock",
        "stepcap",
        "hold-small",
        "cooldown",
        "global-neg-lock",
        "discovery",
        "new-inbound",
        "subprice",
        "peg",
        "peg-except-low",
        "stale-drain",
        "extreme-drain",
        "min-fix",
        "excl-dry",
        "no-down-low",
        "surge+",
        "top+",
        "negm+",
        "sink",
        "source",
        "router",
        "unknown",
        "fa-candidate",
        "nra-candidate",
        "seedcap:",
        "p65:",
        "p95:",
        "bias",
        "inb",
        "t",
        "on",
        "off",
        "back",
        "explorer",
    )
    if norm.startswith("t") and re.match(r"t\d+/r\d+/f\d+$", norm):
        return True
    return any(key in norm for key in tag_keys)


def extract_tags(line: str, unknown_parts: list[str]) -> list[str]:
    candidates = []
    if unknown_parts:
        unknown_parts_sorted = sorted(unknown_parts, key=lambda s: -len(s.split()))
        candidates = unknown_parts_sorted[0].split()
    else:
        candidates = line.split()

    tags = []
    for tok in candidates:
        norm = normalize_tag(tok)
        if is_tag_token(norm):
            tags.append(tok)
    return tags


def explain_tag(token: str) -> str | None:
    norm = normalize_tag(token)
    if not norm:
        return None
    if norm.startswith("floor-lock"):
        return "floor-lock: piso de seguranca travou o ajuste (protege custo de rebal ou outrate)."
    if norm.startswith("stepcap-lock"):
        return "stepcap-lock: step cap zerou a mudanca nesta rodada."
    if norm.startswith("stepcap"):
        return "stepcap: step cap limitou a velocidade da mudanca."
    if norm.startswith("hold-small"):
        return "hold-small: mudanca pequena demais, BOS nao enviou."
    if norm.startswith("cooldown-profit"):
        return "cooldown-profit: queda bloqueada por lucro recente (cooldown)."
    if norm.startswith("cooldown"):
        return "cooldown: janela minima entre mudancas ainda ativa."
    if norm.startswith("global-neg-lock"):
        return "global-neg-lock: margem global negativa, quedas travadas."
    if norm.startswith("discovery"):
        return "discovery: modo discovery ativo (canal ocioso com outbound alto)."
    if norm.startswith("explorer"):
        return "explorer: modo explorer ativo (forca queda para destravar demanda)."
    if norm.startswith("new-inbound"):
        return "new-inbound: canal inbound novo, normaliza para seed."
    if norm.startswith("subprice"):
        return "subprice: piso de receita protege contra subprecificacao."
    if norm.startswith("peg-except-low"):
        return "peg-except-low: excecao ao no-down-low respeitando peg."
    if norm.startswith("peg"):
        return "peg: piso colado no preco observado (outrate peg)."
    if norm.startswith("no-down-low"):
        return "no-down-low: bloqueia queda enquanto outbound esta baixo."
    if norm.startswith("stale-drain"):
        return "stale-drain: drenado cronico sem demanda viva."
    if norm.startswith("extreme-drain"):
        return "extreme-drain: drenagem extrema, acelera subida."
    if norm.startswith("min-fix"):
        return "min-fix: taxa abaixo do minimo foi corrigida."
    if norm.startswith("excl-dry"):
        return "excl-dry: canal excluido (simulacao apenas)."
    if norm.startswith("surge+"):
        return "surge: boost de demanda em canal drenado."
    if norm.startswith("top+"):
        return "top: boost por alta participacao de receita."
    if norm.startswith("negm+"):
        return "negm: boost por margem negativa."
    if norm.startswith("fa-candidate"):
        return "fa-candidate: candidato a fixed assisted (diagnostico)."
    if norm.startswith("nra-candidate"):
        return "nra-candidate: candidato a no-rebal assisted (diagnostico)."
    if norm.startswith("seedcap:"):
        return "seedcap: guard limitou o seed (p95/prev/abs)."
    if norm.startswith("p65:") or norm.startswith("p95:"):
        return "p65/p95: percentil Amboss usado como referencia."
    if norm.startswith("bias"):
        return "bias: viEs EMA do fluxo (debug de classificacao)."
    if norm.startswith("sink"):
        return "sink: canal classificado como sink (tende a receber mais)."
    if norm.startswith("source"):
        return "source: canal classificado como source (tende a enviar mais)."
    if norm.startswith("router"):
        return "router: canal equilibrado (ponte)."
    if norm.startswith("unknown"):
        return "unknown: classe indefinida (sem amostra suficiente)."
    if norm.startswith("inb"):
        return "inb: desconto de inbound aplicado (rebate)."
    if norm in ("on", "back", "off"):
        return "status: on/off/back (status online do canal)."
    if re.match(r"t\d+/r\d+/f\d+$", norm):
        return "t/r/f: debug do alvo (t), raw (r) e floor (f)."
    return None


def explain_floor_src(src: str | None) -> str | None:
    if not src:
        return None
    src_l = src.lower()
    if src_l.startswith("rebal7d"):
        return "floor base: custo de rebal 7d do canal."
    if src_l.startswith("rebal21d"):
        return "floor base: custo de rebal historico (memoria)."
    if src_l.startswith("outrate7d"):
        return "floor base: outrate 7d (preco observado)."
    if src_l.startswith("outrate21d"):
        return "floor base: outrate historico (memoria)."
    if "amboss" in src_l:
        return "floor base: seed Amboss."
    return f"floor base: {src}"


def explain_rebal_suffix(suffix: str | None) -> str | None:
    if not suffix:
        return None
    s = suffix.lower()
    if s == "mem":
        return "rebal_ppm7d: memoria historica (sem amostra recente)."
    if s == "out":
        return "rebal_ppm7d: usando outrate observado."
    if s == "out-mem":
        return "rebal_ppm7d: outrate historico (memoria)."
    if s == "amboss":
        return "rebal_ppm7d: fallback para seed Amboss."
    return f"rebal_ppm7d: fonte {suffix}"


def format_output(line: str, followup: list[str]) -> str:
    parts = [p.strip() for p in line.split("|")]
    header = parse_header(parts[0])
    metrics, unknown_parts = parse_metrics(parts[1:])
    tags = extract_tags(line, unknown_parts)

    out = []
    out.append("AutoFee channel decode")
    if header.get("alias"):
        out.append(f"- alias: {header['alias']}")
    if header.get("cid"):
        out.append(f"- cid: {header['cid']}")
    if header.get("action"):
        action = header["action"]
        out.append(f"- action: {action}")

        m = re.search(r"(\\d+)\\s*(?:->|\\u2192)\\s*(\\d+)", action)
        if m:
            old = int(m.group(1))
            new = int(m.group(2))
            delta = new - old
            out.append(f"- change: {old} -> {new} ppm (delta {delta:+d})")

    if "alvo" in metrics:
        out.append(f"- alvo: {metrics['alvo']} ppm (target before step cap and floor)")
    if "out_ratio" in metrics:
        out.append(f"- out_ratio: {metrics['out_ratio']:.2f} (local balance / capacity)")
    if "out_ppm7d" in metrics:
        out.append(f"- out_ppm7d: {metrics['out_ppm7d']} ppm (avg outgoing fee, 7d)")
    if "rebal" in metrics:
        out.append(f"- rebal_ppm7d: {metrics['rebal']} ppm")
        note = explain_rebal_suffix(metrics.get("suffix"))
        if note:
            out.append(f"  {note}")
    if "seed" in metrics:
        seed_line = f"- seed: {metrics['seed']} ppm"
        if metrics.get("p65") is not None:
            seed_line += f" (p65 {metrics['p65']})"
        if metrics.get("p95") is not None:
            seed_line += f" (p95 {metrics['p95']})"
        if metrics.get("cap"):
            seed_line += " (cap)"
        out.append(seed_line)
    if "floor" in metrics:
        out.append(f"- floor: {metrics['floor']} ppm")
        src_note = explain_floor_src(metrics.get("src"))
        if src_note:
            out.append(f"  {src_note}")
    if "marg" in metrics:
        out.append(f"- marg: {metrics['marg']} ppm (out_ppm7d minus cost w/ margin)")
    if "rev_share" in metrics:
        out.append(f"- rev_share: {metrics['rev_share']:.2f} (share of total out fee)")
    if "fee_lr" in metrics:
        lr = metrics["fee_lr"]
        if lr.get("local") is not None and lr.get("remote") is not None:
            out.append(f"- fee L/R: {lr['local']}/{lr['remote']} ppm (local/remote)")
    if "inb" in metrics:
        inb = metrics["inb"]
        if inb.get("prev") is not None and inb.get("cur") is not None:
            line_inb = f"- inbound discount: {inb['prev']} -> {inb['cur']} ppm"
            if inb.get("net") is not None:
                line_inb += f" (net {inb['net']} ppm)"
            out.append(line_inb)

    if tags:
        out.append("- tags:")
        explained = set()
        for tag in tags:
            note = explain_tag(tag)
            if note and note not in explained:
                out.append(f"  {note}")
                explained.add(note)
        unknown = []
        for tag in tags:
            if explain_tag(tag) is None:
                unknown.append(tag)
        if unknown:
            out.append(f"  unrecognized: {' '.join(unknown)}")

    if followup:
        out.append("- script notes:")
        for line in followup:
            out.append(f"  {line}")

    return "\n".join(out)


def run_autofee(args: argparse.Namespace) -> None:
    text = read_input(args)
    line, lines = find_autofee_line(text, args.alias, args.line)
    followup = extract_followup(lines, line)
    output = format_output(line, followup)
    print(output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brlnautofee-cli",
        description="Decode a single AutoFee channel line from Telegram output.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    decode = sub.add_parser("autofee", help="Explain one AutoFee channel line.")
    decode.add_argument("--text", help="Raw line or full report text.")
    decode.add_argument("--file", help="Path to a file with AutoFee output.")
    decode.add_argument("--stdin", action="store_true", help="Read from stdin.")
    decode.add_argument("--alias", help="Pick line containing alias (case-insensitive).")
    decode.add_argument("--line", type=int, help="Use 1-based line index.")
    decode.set_defaults(func=run_autofee)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

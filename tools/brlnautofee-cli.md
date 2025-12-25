# brlnautofee-cli

CLI simples para decodificar uma linha de canal do AutoFee (ex.: colada do Telegram).
Ele nao executa AutoFee nem toca em BOS/LND; apenas interpreta o texto.

## Requisitos

- Python 3.11+ (mesmo requisito do projeto).

## Uso rapido

```bash
python3 tools/brlnautofee-cli.py autofee --text "SUA LINHA AQUI"
```

No PowerShell, use aspas simples para nao quebrar o `|` (pipe):

```powershell
python3 tools\brlnautofee-cli.py autofee --text 'Alias: set 10->12 ppm | alvo 80 | out_ratio 0.10'
```

Para textos longos, prefira arquivo ou stdin:

```powershell
python3 tools\brlnautofee-cli.py autofee --file .\autofee.txt --alias Zap-O-Matic
Get-Content .\autofee.txt | python tools\brlnautofee-cli.py autofee --stdin
```

## Como ele escolhe a linha

- `--line N`: usa a linha N (1-based).
- `--alias`: pega a primeira linha que contenha o alias (case-insensitive).
- Sem filtro: pega a primeira linha que tenha metricas-chave (`alvo`, `out_ratio`, `out_ppm7d`, `rebal_ppm7d`).

## Exemplo (sanitizado para ASCII)

O texto abaixo e o mesmo exemplo real do Telegram, mas sem emojis e sem simbolos especiais.
O CLI aceita o texto original com emojis normalmente.

```
Persistencia: Zap-O-Matic (983162406985728001) streak 150 => bump 26% (over_current)
Zap-O-Matic: set 1267->1270 ppm +3 (0.2%) | alvo 2086 | out_ratio 0.01 | out_ppm7d~1066 | rebal_ppm7d~870 | seed~356 p65:456 p95:516 | floor>=1270(outrate) | marg~144 | rev_share~0.01 | on sink bias+1.00 sink:1.00 seedcap:none med-blend vol-sigma/mu-15% ratiox0.99 p65:456 p95:516 stepcap floor-lock surge+28% t2086/r1457/f1270 | fee L/R 1267/1ppm
  previsao: manter ou subir (drenado e margem negativa; protegendo ROI do rebal).
```

Rodando:

```bash
python3 tools/brlnautofee-cli.py autofee --text "Zap-O-Matic: set 1267->1270 ppm +3 (0.2%) | alvo 2086 | out_ratio 0.01 | out_ppm7d~1066 | rebal_ppm7d~870 | seed~356 p65:456 p95:516 | floor>=1270(outrate) | marg~144 | rev_share~0.01 | on sink bias+1.00 sink:1.00 seedcap:none med-blend vol-sigma/mu-15% ratiox0.99 p65:456 p95:516 stepcap floor-lock surge+28% t2086/r1457/f1270 | fee L/R 1267/1ppm"
```

## Saida esperada (verbose por padrao)

- alias, cid e acao (set/keep/dry).
- metricas (alvo, out_ratio, out_ppm7d, rebal_ppm7d, seed, floor, marg, rev_share, fee L/R).
- explicacao de tags conhecidas (floor-lock, stepcap, discovery, peg, etc).
- debug t/r/f quando presente (t=alvo bruto, r=apos step cap/CB, f=floor).
- explicacao detalhada do resultado, incluindo piso e sinais ativos.
- notas adicionais (linhas didaticas como "previsao") se estiverem indentadas abaixo.

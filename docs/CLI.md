# CLI

`python main.py <comando> ...` (equivale a `rawxmp <comando>` após `pip install -e .`).

## Comandos
| Comando | O que faz |
|---|---|
| `analyze PASTA` | analisa; imprime parâmetros + confiança; grava relatórios (`output/reports` por padrão); **nunca** grava XMP |
| `process PASTA` | analisa e grava `.xmp` + `.analysis.json` ao lado de cada RAW |
| `validate XMP\|PASTA` | valida XMPs (`--reference`, `--no-limits`) |
| `compare RAW` | PNG antes/depois + histogramas (`--xmp` para usar um XMP seu) |
| `calibrate PASTA` | compara com XMPs humanos (pares `NOME.CR2` + `NOME.xmp`) |
| `styles` | lista estilos |

## Opções de `analyze`/`process`
| Opção | Efeito |
|---|---|
| `--style NOME` | estilo (padrão `base`) |
| `--recursive`, `-r` | subpastas |
| `--overwrite` | sobrescreve XMP existente (padrão: **SKIP**) |
| `--backup` | com `--overwrite`: copia o antigo para `.xmp.bak-AAAAMMDD-HHMMSS` |
| `--dry-run` | (`process`) não grava XMP; igual a `analyze` |
| `--output-dir DIR` | grava XMPs em DIR **espelhando** a estrutura de subpastas |
| `--report-dir DIR` | destino dos `.analysis.json` |
| `--no-reports` | não grava relatórios |
| `--template XMP` | preserva campos do XMP base; valores calculados prevalecem |
| `--reference XMP` | compara a estrutura gerada com sua referência (avisos no relatório) |
| `--config YAML` | sobrescreve o `default.yaml` |
| `--workers N` | processos (0 = automático, máx. 8) |
| `--cache-dir DIR` | cache das renderizações de análise (reprocessamento mais rápido) |
| `--json` | resumo em JSON ao final |
| `-v`/`-q`/`--log-file F` | verbosidade e arquivo de log |

## Códigos de saída
`0` tudo certo · `2` falha parcial (ao menos um arquivo com erro) · `1` falha total/caminho inexistente/estilo inválido.

## Log (exemplo real)
```
[INFO] Processando IMG_0006_warm_cast.CR2
[INFO] Câmera: Canon EOS SYNTH | Lente: EF50mm f/1.8 STM
[INFO] ISO: 200
[INFO] Exposure calculada: -0.95 (confiança 0.87)
[INFO] Temperature: 4621 (Custom) | Tint: -9
[INFO] Skin detected: true
[INFO] Skin confidence: 0.25
[INFO] XMP criado: …/IMG_0006_warm_cast.xmp
[ERROR] Não foi possível interpretar IMG_9999_broken.CR2: Input/output error
[INFO] Concluído em 0.5s: 9 XMP gravado(s), 0 apenas analisado(s), 0 ignorado(s), 1 erro(s)
```

## PowerShell
```powershell
python main.py process "D:\ENSAIOS" --recursive --style portrait --workers 4 --log-file ensaio.log
if ($LASTEXITCODE -ne 0) { Write-Warning "houve erros — veja ensaio.log" }
```

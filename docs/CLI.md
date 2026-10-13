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
| `make-style XMP...` | **cria um estilo** a partir de XMPs seus (`--name`, `--with-template`, `--force`) |
| `skin-debug RAW` | diagnostica a detecção de pele (critérios + PNG) |
| `diagnose RAW` | matriz/WB lidos pelo LibRaw e como foram interpretados |
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

## Criar um estilo a partir dos seus XMPs
```powershell
python main.py make-style D:\ref\a.xmp D:\ref\b.xmp D:\ref\c.xmp --name meu_estilo --with-template
python main.py process D:\ENSAIO --style meu_estilo --dry-run     # confira
python main.py process D:\ENSAIO --style meu_estilo
```
* Use **vários** XMPs do mesmo "look": o estilo usa a **mediana** e o comando avisa quando um slider varia muito entre eles.
* **Exposição, Temperature e Tint nunca são copiados** (são calculados por foto).
* Contraste/Realces/Sombras/Brancos/Pretos viram `*_bias`: o valor final é *o que o algoritmo mede na foto + seu nível habitual*.
* `--with-template` copia o 1º XMP para `profiles/templates/NOME.xmp` e o usa como estrutura: preserva `Look`, crop e o formato de elementos; `Temperature/Tint` não são herdados.
* O arquivo gerado (`profiles/styles/meu_estilo.yaml`) é texto: edite à vontade.

## JPEG (`--jpeg`, `--jpeg-with-raw`)

Opções comuns. Saída: cópia com XMP embutido em `<pasta>/rawxmp_jpeg/` (ou `--output-dir`); respeita
`--overwrite`/`--backup`/`--dry-run`. `validate` também lê o XMP embutido de `.jpg`. `calibrate --jpeg`
usa JPEGs com ajustes do Lightroom embutidos como referência humana.

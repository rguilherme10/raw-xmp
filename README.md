# rawxmp — CR2 → XMP (Camera Raw / Lightroom), local, gratuito e explicável

Analisa cada `.CR2` (a partir dos **dados RAW**, não do JPEG embutido) e escreve um `.xmp` com ajustes
**individuais** (exposição, balanço de branco, sombras/realces, contraste, curva tonal, HSL…) sobre um
**estilo constante** (perfil de câmera, curva base, nitidez, color grade). Cada decisão tem
`valor + confiança + justificativa`, e cada foto gera um `.analysis.json` auditável.

```
IMG_0001.CR2  ──►  IMG_0001.xmp            (ajustes)
                   IMG_0001.analysis.json  (por que cada número foi escolhido)
```

## ⚠️ Leia primeiro: o que foi e o que NÃO foi verificado

| Verificado (testes automáticos, 105) | **Não** verificado |
|---|---|
| Pipeline completo, lote, paralelismo, SKIP/`--overwrite`/`--backup`, recursivo, dry-run | **Nenhum CR2 real nem o seu XMP de referência estavam disponíveis** neste ambiente |
| Leitura via LibRaw (rawpy) de RAWs Bayer sintéticos (contêiner DNG com Exif) | Abrir os XMPs no Lightroom/Camera Raw (sem acesso a nenhum) |
| XML válido, namespaces, tipos, faixas, limites, curvas monotônicas | Estrutura vs. **seu** XMP: o esquema `crs:*` vem do formato público; use `--reference` |
| Estimativa de WB recupera iluminantes conhecidos (3614 K vs 3600 K real; 8158 K vs 8200 K) | Calibração visual com fotos reais (constantes são pontos de partida, ver [CALIBRATION](docs/CALIBRATION.md)) |
| `.CR2` nunca é alterado (hash conferido em teste) | Windows (desenvolvido/testado em Linux, Python 3.13) |

Por isso o primeiro passo recomendado com **suas** fotos é: `validate --reference seu.xmp`, depois `calibrate`
com alguns pares CR2+XMP seus. Detalhes em [CALIBRATION.md](docs/CALIBRATION.md).

## Instalação

Requer Python ≥ 3.10. Dependências (todas gratuitas): `numpy scipy opencv-python-headless rawpy PyYAML`
(opcional: `matplotlib` para `compare`, `pytest` para testes). ExifTool é **opcional** (usado se estiver no PATH).

PowerShell (Windows):
```powershell
cd raw-xmp
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py styles
```
Linux/macOS: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`.

## Uso rápido

```powershell
python main.py analyze D:\ENSAIO                       # só analisa: mostra parâmetros e confiança, grava relatórios em output\reports
python main.py process D:\ENSAIO --dry-run             # idem (sem gravar XMP)
python main.py process D:\ENSAIO                       # grava IMG_xxxx.xmp ao lado de cada CR2 (pula se já existir)
python main.py process D:\ENSAIO --style portrait      # estilo (profiles/styles/portrait.yaml)
python main.py process D:\ENSAIO --recursive           # subpastas
python main.py process D:\ENSAIO --overwrite --backup  # regrava, guardando cópia .bak-AAAAMMDD-HHMMSS
python main.py validate D:\ENSAIO --reference D:\modelo.xmp
python main.py compare D:\ENSAIO\IMG_0001.CR2 --out output\cmp.png
python main.py calibrate D:\CALIBRACAO                 # pares NOME.CR2 + NOME.xmp (edição humana)
```
Referência completa: [docs/CLI.md](docs/CLI.md). Um exemplo gerado: [`docs/examples/`](docs/examples).

## Garantias de segurança
* O `.CR2` é aberto somente para leitura; o código recusa escrever em extensões RAW.
* Não sobrescreve `.xmp` existente (comparação de extensão sem diferenciar maiúsculas) sem `--overwrite`.
* Escrita atômica (arquivo temporário + `os.replace`); um XMP inválido nunca é gravado.
* Um arquivo corrompido gera `[ERROR] Não foi possível interpretar …` e o lote continua.
* O sidecar usa `.xmp` minúsculo (macOS/Linux o exigem; Windows ignora caixa). Configurável em `files.xmp_extension`.

## Mapa da documentação
[ARCHITECTURE](docs/ARCHITECTURE.md) · [ALGORITHM](docs/ALGORITHM.md) · [XMP_FORMAT](docs/XMP_FORMAT.md) ·
[CONFIGURATION](docs/CONFIGURATION.md) · [CLI](docs/CLI.md) · [TESTING](docs/TESTING.md) ·
[CALIBRATION](docs/CALIBRATION.md) · [ROADMAP](docs/ROADMAP.md)

## Critérios de aceite — situação
| Critério | Situação | Evidência |
|---|---|---|
| ler `.CR2` | ✅ com RAWs sintéticos (LibRaw real); ⚠️ **CR2 Canon real não testado** | `test_raw_reader.py` |
| analisar, calcular parâmetros | ✅ | `test_stats_exposure.py`, `test_white_balance.py`, … |
| gerar `.xmp` válido, estrutura compatível | ✅ XML/esquema; ⚠️ compatibilidade com **seu** XMP depende de `validate --reference` | `test_xmp.py`, `test_validation.py` |
| preservar o `.CR2` | ✅ hash idêntico | `test_pipeline_cli.py` |
| respeitar `--overwrite` (+ `--backup`) | ✅ | `test_pipeline_cli.py` |
| múltiplas imagens, recursivo, erro isolado | ✅ | idem |
| logs no formato pedido; relatório `.analysis.json` | ✅ | `test_log_format`, `test_analysis_report_contents` |
| testes e documentação | ✅ 105 testes; 8 documentos em `docs/` | — |
| abrir no Lightroom/ACR | ❌ **não foi possível aqui** | ver "Testes manuais" em TESTING.md |

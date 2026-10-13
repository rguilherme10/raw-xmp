# Arquitetura

## 1. Fluxo (as 10 separações pedidas)

```
 CR2 (somente leitura)
   │ 1 raw/reader.py     leitura via LibRaw/rawpy
   │ 2 raw/exif.py       metadados (ExifTool se existir, senão parser TIFF/Exif próprio)
   ▼
 3 raw/render.py         render de análise: half-size, WB da câmera, linear sRGB, ≤1024 px, cache opcional
   ▼
 4 analysis/stats.py     estatísticas puras (histograma, percentis, clipping, contraste, "chave" da cena)
   │ skin/detector.py    pele (cor + geometria)           ┐
   │ white_balance/      iluminante, confiança            │ 5 decisões, cada uma devolve
   │ exposure/           Exposure/Highlights/Shadows/...  │   Estimate(value, confidence, reason)
   │ color/hsl.py        24 ajustes HSL com evidência     │
   │ tone/curve.py       curva adaptativa                 │
   │ detail/, lens/      nitidez/NR por ISO, lente        ┘
   ▼
 6 correction/engine.py  ESTILO (perfil, constante) + CORREÇÃO DA IMAGEM → parâmetros → clamps configuráveis
   ▼
   pipeline/consistency.py  (lote) aproxima WB incerto da mediana do grupo
   ▼
 7 xmp/builder → XMPModel → XMPSerializer   (nenhum XML montado por concatenação de strings)
 8 validation/xmp_validator.py              (nunca grava XMP inválido)
 9 pipeline/batch.py     lote: descoberta, processos paralelos (spawn), SKIP, escrita atômica
10 logging_setup.py      "[INFO] …" / "[ERROR] …" + relatório .analysis.json por foto
```
Extras: `calibration/` (compara com XMP humano), `preview/` (antes/depois aproximado), `cli/`.

## 2. Mudanças em relação à estrutura sugerida (e por quê)

| Sugerido | Adotado | Motivo |
|---|---|---|
| `app/` | pacote `rawxmp/` | nome importável/instalável (`pip install -e .`), sem colisão com o genérico `app` |
| `raw/` único | `raw/` = reader + exif + render | ler, extrair metadados e renderizar mudam por motivos diferentes |
| `analysis/` + módulos de decisão misturados | `analysis/` só **mede**; `exposure/ white_balance/ color/ skin/ tone/` **decidem** | medir e decidir separados permitem calibrar regras sem tocar nas medidas |
| `profiles/` dentro do código | `profiles/{base,styles,cameras}` na raiz, YAML | usuário edita sem mexer em Python; **`cameras/` novo** para regras por modelo |
| (não havia) | `correction/engine.py` | único lugar onde ESTILO + CORREÇÃO são combinados e limitados |
| (não havia) | `pipeline/consistency.py` | consistência entre fotos do mesmo ensaio |
| (não havia) | `types.Estimate`, `colorspace.py` | contrato `value/confidence/reason` e espaços de cor num só lugar |
| `config/default.yaml` | mantido | todos os limiares e limites vivem aqui |

## 3. Decisões técnicas

**Leitura RAW — `rawpy` (LibRaw).** Alternativas avaliadas:

| Opção | Licença | Windows | CR2 | Veredito |
|---|---|---|---|---|
| **rawpy** (wrapper) + LibRaw | MIT (wrapper); LibRaw LGPL-2.1/CDDL-1.0 | wheels no PyPI (confirmar com `pip install` na sua máquina) | sim; WB, matriz de cor, black/white level | **escolhida** — rápida (half-size sem demosaico), mantida |
| dcraw / RawTherapee-cli / darktable-cli | GPL | binários | sim | externos, pesados, acoplamento por subprocesso |
| Pillow | HPND | sim | **não** decodifica o RAW | só serviria para o JPEG embutido (descartado: depende de renderização da câmera) |
| ExifTool | Perl (Artistic/GPL) | sim | metadados completos (lente Canon) | **opcional**: usado se estiver no PATH |
| scikit-image | BSD | sim | — | instalável, mas **não necessário** (scipy.ndimage + OpenCV bastam) |

OpenCV (Apache-2.0, `opencv-python-headless`): conversão YCrCb e redimensionamento INTER_AREA.
SciPy/NumPy (BSD): morfologia, componentes conexos, filtros. PyYAML (MIT): configuração.

**Renderização para análise.** Não decodificamos em resolução máxima: `half_size` (sem demosaico) + `INTER_AREA` até 1024 px.
~1,4 s/arquivo de 18 MP (medido em RAW sintético não comprimido), pico ≈ 300 MB. O JPEG embutido **não** é usado.

**Paralelismo.** `ProcessPoolExecutor` com contexto **`spawn`** (LibRaw/OpenMP pode travar após `fork`; `spawn` também é o padrão do Windows).
O trabalhador nunca lança exceção: devolve um pacote de erro. As etapas dependentes de lote (consistência, escrita) rodam no processo principal.

**STYLE vs IMAGE CORRECTION.** `profiles/base/base.yaml` (+ estilo escolhido) define o visual constante; o engine soma correções
por imagem *depois* e aplica `limits.*`. Testado: campos de estilo (perfil, clareza, raio de nitidez…) idênticos em todas as fotos; exposição/realces variam.

## 4. Riscos e limitações (ordenados por impacto)

1. **Sem validação em CR2 reais e no Lightroom/ACR.** Mitigação: `validate --reference`, `calibrate`, relatórios auditáveis.
2. **Temperature/Tint absolutos** dependem das matrizes de câmera da Adobe; calculamos a partir da matriz do LibRaw. Por isso
   em "As Shot" **não escrevemos** `Temperature/Tint` (o ACR recalcula) e só escrevemos `Custom` com deslocamento limitado (±700 K, ±12 tint).
   Constantes de calibração: `white_balance.kelvin_offset`, `tint_per_duv`.
3. **Exposição**: `exposure.target_median` e o baseline do perfil Adobe são empíricos → calibrar por câmera.
4. **Pele por cor** pode errar (madeira, areia, luz de tungstênio em parede). Mitigações: teto de confiança 0,85, penalidade de área >30 %, cascata de rosto opcional (`skin.face_cascade`; o OpenCV *headless* não traz os XML).
5. **Perfil de lente**: só habilitamos a correção automática (`LensProfileEnable`); nomes exatos de perfil vêm de `lens.profiles` se você os mapear.
6. **Prévia `compare`** é aproximação, não o motor da Adobe.
7. Consistência entre fotos hoje cobre **WB**; exposição entre cenas diferentes não é equalizada de propósito.

## Caminho JPEG

`rawxmp/raw/jpeg.py` extrai Exif/XMP, renderiza linear (ICC→sRGB) e grava o XMP no APP1 por cópia
byte-a-byte do restante (`embed_xmp`, atômico). `analyze_raw` ramifica por tipo (`Decision.kind`).

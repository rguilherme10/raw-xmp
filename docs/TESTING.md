# Testes

```bash
pip install -r requirements.txt
python -m pytest            # 105 testes, ~15 s
```

## Conjunto de imagens de teste
`tools/make_test_raws.py` gera RAWs Bayer RGGB de 16 bits (contêiner TIFF/DNG com Exif: ISO, abertura, velocidade, focal, lente, data, marca/modelo,
matriz de cor, `AsShotNeutral`, black/white level), nomeados `.CR2`. O LibRaw os decodifica de verdade. Cenários:
`normal`, `underexposed`, `overexposed`, `high_contrast`, `low_contrast`, `warm_cast` (cena a 3600 K com WB da câmera a 5200 K),
`cool_cast` (8200 K), `no_skin`, `high_iso` (ISO 6400, ruído). `python tools/make_test_raws.py output/amostras` grava o conjunto para inspeção.

> São **sintéticos**: não são arquivos Canon reais (sem MakerNotes, compressão lossless-JPEG, sensores reais). Validam a lógica, não a calibração.

## Cobertura por requisito
| Requisito | Arquivo |
|---|---|
| leitura RAW, metadados, cache, arquivo corrompido, RAW intacto | `test_raw_reader.py` |
| histogramas, percentis, clipping, contraste, chave da cena, exposição e limites | `test_stats_exposure.py` |
| white balance (as-shot, dominantes, limites, sem dados, matriz implausível) | `test_white_balance.py` |
| HSL, detecção de pele, proteção de pele | `test_skin_color.py` |
| curva tonal (adaptativa, monotônica, limites, pele) | `test_tone_curve.py` |
| geração/serialização/parse XMP, escape, template | `test_xmp.py` |
| validação (XML, namespaces, faixas, limites, curvas, referência) | `test_validation.py` |
| config, estilos, regras por câmera, estilo × correção, ISO | `test_config_style.py` |
| lote, SKIP/overwrite/backup, recursivo/output-dir, dry-run, relatório JSON, paralelo = serial, consistência, CLI, logs | `test_pipeline_cli.py` |
| calibração, simulação, `compare` | `test_calibration_preview.py` |

## Testes manuais recomendados (não automatizáveis aqui)
1. `validate --reference seu.xmp` com um XMP real.
2. Abrir 5–10 XMPs gerados em Lightroom/ACR e conferir: carregam sem erro? Os sliders mostram os valores esperados? WB "Custom" bate com a imagem?
3. `compare` em fotos reais e conferência visual.

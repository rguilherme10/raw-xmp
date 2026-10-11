# Configuração

Camadas (a seguinte sobrescreve a anterior, dicionários são mesclados em profundidade):
`config/default.yaml` → `profiles/base/base.yaml` → `profiles/styles/<estilo>.yaml` → `--config arquivo.yaml`
→ regras de câmera `profiles/cameras/*.yaml` (aplicadas por modelo, ao ler os metadados).

## Seções de `config/default.yaml`
| Seção | Conteúdo |
|---|---|
| `files` | extensões RAW, extensão do sidecar (`.xmp`), sufixo do relatório |
| `analysis` | `max_dimension` (1024), `clip_threshold`, `near_black`, `noise_floor`, `cache_dir` |
| **`limits`** | **limites finais de todos os parâmetros** (`exposure`, `contrast`, `highlights` (−100…0), `shadows` (0…80), `whites`, `blacks`, `temperature`, `tint`, `saturation`, `vibrance`, `hsl_*`, `clarity`, `texture`, `dehaze`, `sharpness`, `luminance_nr`, `color_nr`, `tone_curve_max_deviation`) |
| `exposure` | alvo de mediana, amortecimento por chave, headroom, limiares de highlights/shadows/blacks/whites/contrast |
| `white_balance` | cobertura mínima, `max_shift_kelvin/tint`, `image_trust`, `min_confidence`, `apply_threshold`, `tint_per_duv`, `kelvin_offset`, `write_as_shot_values` |
| `skin` | limiares YCrCb/HSV/Lab, áreas, teto de confiança, `face_cascade` |
| `color` | centros das faixas, `sat_high`, zona morta de matiz, ajustes máximos, proteção de pele |
| `tone_curve` | pontos x, ganhos, inclinação permitida na faixa da pele |
| `detail`, `lens`, `consistency`, `report`, `xmp` | ISO-adaptação, perfil de lente, consistência de lote, versão escrita no XMP |

## Exemplo — o do enunciado
```yaml
limits:
  exposure:    {min: -2.0, max: 2.0}
  highlights:  {min: -100, max: 0}
  shadows:     {min: 0,    max: 100}
  temperature: {min: 2000, max: 10000}
  saturation:  {min: -20,  max: 20}
```
Rode com `--config meus_limites.yaml` (só as chaves que quiser mudar).

## Estilos (`profiles/styles/*.yaml`, seção `style:`)
`camera_profile`, `saturation`, `vibrance`, `clarity`, `texture`, `dehaze`, `exposure_bias`, `contrast_bias`, `highlights_bias`,
`shadows_bias`, `tone_curve.points` (x,y 0–255) e `.parametric`, `hsl.{hue,saturation,luminance}.<cor>`, `sharpening`, `noise_reduction`,
`color_grade`, `calibration`, `vignette`. Um estilo também pode sobrescrever qualquer outra seção (ex.: `portrait` sobe `exposure.skin_weight`).
Criar um estilo = copiar `profiles/styles/portrait.yaml`, renomear e usar `--style nome`.
Entregues: `base`, `portrait`, `landscape`, `neutral`.

## Regras por câmera (`profiles/cameras/*.yaml`)
```yaml
match: ["EOS 5D Mark IV"]        # substrings (sem diferenciar caixa) do modelo lido do EXIF
exposure: {target_median: 0.15}
white_balance: {kelvin_offset: -120}
```
O primeiro arquivo (ordem alfabética) cujo `match` casar vence. Vem com `canon_generic.yaml` (casa "Canon").

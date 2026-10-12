# Algoritmo

Todos os limiares citados estão em `config/default.yaml`. Valores são **pontos de partida não calibrados**.

## 1. Espaços de cor — o que e por quê
| Etapa | Espaço | Razão |
|---|---|---|
| Exposição, clipping, headroom | **RGB linear** (primárias sRGB, WB da câmera, 1,0 = branco do sensor) e luminância Y | grandezas físicas: +1 EV = ×2 |
| Percentis perceptuais, contraste, sombras/pretos | **V = OETF sRGB(Y)** | slider e histograma do Lightroom operam em escala perceptual |
| Temperatura/Tint | **espaço da câmera → XYZ → CIE 1960 uv** (CCT por McCamy+refino Krystek, Duv) | iluminante é definido no locus de Planck |
| Pele, croma, luminosidade por faixa | **CIE Lab/LCh (D65)** | matiz/croma perceptualmente uniformes |
| Faixas HSL | **HSV de valores sRGB** | aproxima o painel HSL do Camera Raw (centros 0/30/60/120/180/240/270/300°) |
| Correções | **nenhuma** é aplicada em RGB: o programa só *decide parâmetros* | quem renderiza é o Camera Raw. (A prévia `compare` é a única que mexe em pixels.) |

## 2. Estatísticas (`analysis/stats.py`)
Histograma de V (64 bins); percentis 0,1/1/5/50/95/99/99,9 em Y e V; fração ≤ `near_black`; fração ≥ `clip_threshold` (por canal e qualquer canal);
**clipping real do sensor** (pixels RAW ≥ 99,5 % do white level); fração de meios-tons; contraste global (V p95−p5); alcance dinâmico (log₂ p99,9/p0,1);
contraste local (σ de V − gaussiana). **Chave da cena**:
`underexposed` (p99,9 ≤ 0,35 **e** mediana ≤ 0,6·alvo — nada perto do branco ⇒ falta de luz, não estética) → `low_key` (mediana baixa **com** realces reais) →
`high_key` → `high_contrast` (log₂ p99/p1 ≥ 10,5, com piso de ruído) → `normal`.

## 3. Exposição (`exposure/estimator.py`) — vários sinais, nunca só o histograma
1. `Δ_global = log₂(alvo / mediana)`.
2. Se há pele: `Δ_pele = log₂(Y_alvo_pele / Y_mediano_pele)`, mistura `(1−w)Δ_global + wΔ_pele`, `w = skin_weight·confiança`.
3. Amortecimento pela chave (`normal .85, underexposed .95, low/high_key .35, high_contrast .6`).
4. **Headroom**: aumento limitado para que o assunto claro (p99) não passe de `highlight_ceiling` (0,80 linear) e o topo (p99,9) de `0,99 + 0,15 EV`; com clipping já existente, sem aumento. (Evita clarear cena de fundo escuro cujo assunto branco já está bem exposto.)
5. Teto ±1,5 EV (`max_correction_stops`), arredondamento 0,05, viés do estilo, clamp final.
6. Previsões **pós-exposição** alimentam os demais controles:
   * **Highlights**: pressão = f(p99 acima do joelho 0,85, fração cortada) → 0…−60.
   * **Shadows**: p5 abaixo de V=0,12 → até +60 (atenuado em low-key; zero se a imagem já é "lavada").
   * **Blacks**: só aprofunda se p0,1 > 0,06 (e com ganho baixo); alivia se >1 % esmagado.
   * **Whites**: ganho baixo (+20 máx.) e nunca em low-key; reduz se cortado. (Evita o clássico "esticar histograma".)
   * **Contrast**: amplitude p95−p5 vs alvo 0,62, bônus se contraste local baixo, nunca positivo em alto contraste.

## 4. Balanço de branco (`white_balance/estimator.py`)
* A câmera é o *prior*: `n = 1/mult_WB` (resposta do iluminante, unidades brutas) → `XYZ = M⁻¹ n` com **M não normalizada** → CCT/Duv → `Tint = Duv · tint_per_duv`.
* A orientação de M (XYZ→cam ou transposta) é **verificada em cada arquivo** contra o `daylight_whitebalance` do LibRaw (M·D65 deve reproduzi-lo); se nenhuma orientação confere, a matriz é descartada e o WB fica "As Shot". `python main.py diagnose IMG.CR2` mostra tudo isso.
* Se Kelvin/tint da câmera forem implausíveis (mapeamento de matriz duvidoso) → "As Shot" com confiança 0,3.
* **Evidência da imagem**: dentre pixels pouco cromáticos (C* < 55, L* 22–92, sem clipping, sem pele) procura-se a **moda** num histograma 2D de (a*,b*) — superfícies neutras formam um cluster denso mesmo sob dominante.
  Estimativa no **espaço da câmera** (`C · rgb_médio`), nunca em sRGB.
* `confiança = (0,25 + 0,75·cobertura·coerência) · penalidade_de_deslocamento · plausibilidade`; abaixo de 0,35 a evidência é descartada.
* Aplicado = deslocamento × `confiança · image_trust(0,6)`, **limitado a ±700 K / ±12 tint**; abaixo de 120 K/3 tint mantém "As Shot".
* **Veto por pele**: se o ajuste tira o matiz da pele da faixa 34–76° (Lab) — que ela ocupava —, o ajuste é reduzido a 30 %.

## 5. Cor (`color/hsl.py`)
Por faixa (peso triangular até o centro vizinho; só pixels com S ≥ 0,12): fração, matiz médio/σ, saturação média, L*, croma, fração com canal cortado.
**Só corrige com evidência** e se a faixa tem ≥ 1 % da imagem:
* saturação média > 0,80 → reduz; canal cortado > 6 % → reduz mais;
* matiz: desvio médio > 14° **e** faixa compacta (σ ≤ 18°) → move metade do excesso;
* luminância: faixa escura (L* < 22) e croma > 25 → levanta.
Tudo escalonado pela presença e limitado (±8 matiz, ±15 sat, ±8 lum). **Pele**: Red/Orange/Yellow atenuados por `1 − 0,85·confiança` e limitados a ±4; se a pele deriva para vermelho/amarelo, Orange recebe um empurrão ≤ 5.
Sem pele detectada, nenhuma regra de pele é aplicada. Estilo + correção são somados e limitados por `limits.hsl_*`.

## 6. Pele (`skin/detector.py`)
Máscara = YCrCb (Cr 133–173, Cb 77–127) ∧ HSV (S 0,08–0,78) ∧ Lab (matiz 22–78°, croma 9–55, L* 25–92) ∧ sem clipping → abertura morfológica →
componentes conexos com área ≥ 0,3 % e compacidade ≥ 0,30. Confiança = `0,15 + 0,85(0,45·área + 0,35·concentração do matiz + 0,20·dominância)`,
teto 0,85 sem rosto confirmado, **× penalidade se a área passa de 30 % do quadro** (parede alaranjada ≠ pele). Saída: matiz/croma/L*, Y mediano e desvio (`toward_red|magenta|yellow`).

## 7. Curva tonal (`tone/curve.py`)
Pontos x = 0, 32, 64, 128, 192, 255. Curva do estilo + modulação: S-curve proporcional ao contraste estimado (±8 níveis), alívio de sombras esmagadas,
roll-off de realces sob pressão de clipping, **inclinação 128→192 limitada a 0,9–1,2 com pele**, desvio máximo |y−x| ≤ 14, extremos ancorados, monotonia estrita (inclinação ≥ 0,35).

## 8a. Ruído medido no RAW (`analysis/noise.py`)
O ISO sozinho não prevê o ruído visível: empurrar +2 EV em ISO 800 traz as sombras (baixo SNR) para os tons médios. Medimos o ruído no plano verde do RAW (recorte central, sem demosaico):
blocos 8×8, resíduo laplaciano (σ² = var/36), **os blocos mais planos por faixa de brilho** (textura não conta como ruído) e ajuste σ²(S) = a·S + b (ruído de disparo + leitura).
Ruído relativo σ/S no nível do sensor que, após a exposição aplicada, cai no tom de referência (`detail.noise_reference_y`) → `LuminanceSmoothing = estilo + (σ/S − noise_free_relative)·noise_gain`.
Nitidez, máscara e NR de cor usam o **ISO efetivo** (ISO + 0,8 × empurrão de exposição). Se o ruído não puder ser medido, volta à regra por ISO.
Limites: (1) cena inteira texturizada (sem áreas planas) superestima o ruído; (2) `noise_gain`/`noise_free_relative` **não calibrados**: o `calibrate` agora compara LuminanceSmoothing/ColorNoiseReduction/Sharpness e grava `noise_rel` por foto para ajustá-los; (3) a remoção em si é do Lightroom — o XMP só informa quanto aplicar.

## 8. Detalhe e lente
Nitidez −4/stop acima de ISO 400, máscara de bordas +10/stop; NR de luminância +9/stop, de cor +3/stop (limites configuráveis). Lente: `LensProfileEnable=1`, `LensProfileSetup=LensDefaults`, `AutoLateralCA=1`; perfil explícito via `lens.profiles`.

## 9. Consistência de lote (`pipeline/consistency.py`)
Por pasta e câmera (≥ 3 fotos, dentro de 1500 K da mediana): estimativas com confiança < 0,7 são puxadas até 60 % em direção à mediana; confiantes não mudam; outliers (outra luz) ficam de fora.

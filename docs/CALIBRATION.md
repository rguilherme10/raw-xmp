# Calibração

O sistema é determinístico e explicável; a primeira versão **não está calibrada** para suas câmeras/gosto. O fluxo:

1. Reúna 20–50 pares **`NOME.CR2` + `NOME.xmp`** (XMP que você considera bom, exportado do Lightroom/ACR) numa pasta.
2. `python main.py calibrate D:\PARES` → `output/calibration/`:
   * `calibration_summary.json` — por parâmetro: n, **viés**, MAE, RMSE, correlação;
   * `calibration_rows.csv` — algoritmo × humano, foto a foto (inclui HSL);
   * `calibration_features.csv` — percentis, ISO, chave, pele + valores humanos (insumo para uma eventual fase 2).
3. Interprete:
   * **viés de `Exposure2012`** → o resumo sugere `exposure.target_median` novo (`T·2^(−viés)`; só com ≥ 5 pares). Ponha-o em `profiles/cameras/<modelo>.yaml`.
   * viés de `Temperature` (quando o humano usou `Custom`) → `white_balance.kelvin_offset`; relação Tint ↔ Duv → `tint_per_duv`.
   * correlação baixa em Highlights/Shadows/Whites/Blacks → ajustar os ganhos em `exposure.*` (um parâmetro por vez; rodar `calibrate` de novo).
   * HSL: normalmente deve ficar perto de 0; se o humano sistematicamente mexe numa faixa, isso é **estilo** → vá para `profiles/styles/`.
4. Compare visualmente: `python main.py compare IMG.CR2 --xmp humano.xmp` e `--out` do gerado (prévia **aproximada**).

Regra de ouro: viés sistemático → config/estilo; erro disperso em cenas específicas → regra nova (e teste).

## Fase 2 (somente se necessário): aprendizado a partir de pares CR2 → XMP
Não implementada. Só faz sentido se, após calibrar, a correlação de Exposure/WB/curva continuar baixa **e** o erro for estruturado.

| Item | Proposta |
|---|---|
| Exemplos mínimos | ≥ 300 pares **por câmera/perfil** para exposição/contraste; ≥ 1000 para WB e HSL (muita variação de luz). Abaixo disso, ficar com regras. |
| Features | as de `calibration_features.csv`: percentis Y/V, clipping, fração de pele e matiz, estatísticas de a*b*, ISO, EV (abertura/velocidade/ISO), chave, razão neutra por canal, histograma V (64 bins) comprimido (PCA). **Sem pixels brutos.** |
| Modelo | **Residual sobre o determinístico**: prever `humano − algoritmo` com gradient boosting raso (LightGBM/sklearn) ou ridge; saídas monotonizadas e limitadas por `limits.*`. Curva: 3–4 coeficientes (PCA de curvas), não pontos livres. |
| Treino | por fotógrafo/estilo; validação **por ensaio** (GroupKFold por sessão, nunca por foto); métrica MAE por parâmetro e % de fotos com erro < tolerância visual (ex. ±0,3 EV, ±250 K). |
| Overfitting | poucos dados ⇒ modelo pequeno, regularização forte, *shrinkage* para o determinístico quando a confiança é baixa, comparação obrigatória contra a baseline determinística; se não ganhar em validação por grupo, descarta-se. |
| Câmeras | modelo (ou *offset*) por câmera; câmera nova começa só com regras. |
| Iluminações | estratificar/avaliar por faixa de Kelvin e por interno/externo; WB aprendido só com cobertura de luz suficiente, senão regras. |

## Registro de calibrações realizadas
**10 pares reais (Canon EOS Rebel T6i, ISO 100–800, `exemplo-para-calibrar/`)** — nota: o Lightroom só grava o `.xmp` ao salvar metadados (Ctrl+S); XMPs desatualizados traziam o preset colado (+1,31 em 6 fotos) e foram descartados.
| Parâmetro | Antes | Depois |
|---|---|---|
| Exposição — erro médio (10 pares) | 0,81 EV | **0,32 EV** (viés −0,07; 9/10 dentro de ±0,46) |
Descoberta: nas 4 fotos a pele fica em Y linear = 0,095–0,136 após a sua exposição (as medianas globais variam 0,010–0,159). Portanto
`exposure.skin_target_y` = 0,115 (era 0,24), peso da pele 0,9 (era 0,5), sem amortecimento low-key quando há pele, headroom medido por p99 (não p99,9),
teto de correção 2,5 EV. Quando a pele é o assunto, **a trava de realces não vale** (fotos com p99 de 0,93–0,98 receberam +0,6…+1,3 EV e Realces −54…−100); sem amortecimento. IMG_3004 (você expôs pelo rosto, fundo estourado, Realces −67): o detector marcava também piso bege e parede como pele (matiz Lab 63–67, Y 0,34–0,75) e media Y=0,17; os componentes de rosto/mãos têm matiz 40–53. Agora os componentes são ponderados por matiz (`skin.hue_prefer`: acima de 60 perdem peso), o erro nessa foto caiu de −1,06 para −0,61 EV e o MAE geral ficou em 0,29 EV. **Amostra pequena (4 fotos, uma sessão)**: reavalie com mais pares.
Balanço de branco: **não calibrado** — suas Temperature/Tint (4058–4910 K, tint −3…−6) diferem do as-shot calculado (5155–5560 K, tint +1…+2) e parecem
vir de WB sincronizado. O deslocamento de tint por imagem foi limitado a ±2. Para calibrar `white_balance.kelvin_offset` informe o Temperature/Tint que o Lightroom mostra em "As Shot" para uma foto.

**61 pares (T6i, sessão externa, ISO 200)** — análise de `calibration_rows.csv`
* Fotos sem pele (n=13 editadas individualmente): erro médio 0,14 EV.
* Fotos com pele (n=25): a pele fica em Y mediana **0,179** (IQR 0,165–0,192) após a sua exposição — contra 0,124 na sessão interna anterior. O alvo de pele **depende da sessão**:
  default global 0,15 (compromisso); para cada sessão rode `python main.py calibrate PASTA --style SESSAO --write-style SESSAO` (grava `exposure.skin_target_y`/`target_median` no YAML do estilo, só com ≥ 5 amostras individuais por grupo).
* 23 das 61 fotos têm a mesma exposição (+0,23): série com mesmos ajustes de câmera. O algoritmo variava −0,55…+0,65 nelas. Consistência de exposição em séries
  (`consistency.exposure.enabled`, desligada por padrão): fotos da mesma pasta com mesma câmera/lente/ISO/abertura/obturador têm a estimativa puxada (70%) para a mediana do grupo; outliers (> 0,8 EV) ficam de fora.
  Simulação com agrupamento só por ISO: MAE 0,344 → 0,270 EV (todas) e 0,333 → 0,112 EV nas 23 sincronizadas. O `calibrate` agora imprime esse ganho com os ajustes de câmera reais.
  Para ligar numa sessão: coloque `consistency: {exposure: {enabled: true}}` no YAML do estilo.

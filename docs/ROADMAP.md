# Roadmap

Divisão original reavaliada: a **validação do XMP** e o **relatório/confiança** foram antecipados (são pré-requisito para depurar qualquer algoritmo),
e o **lote/CLI** entrou na Fase 1 (sem ele não se mede nada em dezenas de fotos). Calibração depende de dados reais e continua adiante.

| Fase | Escopo | Estado |
|---|---|---|
| **1** | estrutura, leitura CR2, metadados, render, estatísticas, gerador XMP (model/serializer/parser), validação, relatórios, logs | ✅ feito e testado (sintético) |
| **2** | exposição, WB com confiança, curva tonal, HSL com evidência, contraste, ISO→nitidez/NR, lente | ✅ feito (constantes **não calibradas**) |
| **3** | pele (detecção, proteção, confiança), estilos (`base/portrait/landscape/neutral`), correção individual, regras por câmera | ✅ feito |
| **4** | lote paralelo, CLI, SKIP/overwrite/backup, recursivo/espelho, dry-run, consistência de WB, testes | ✅ feito |
| **5** | calibração com XMPs reais, antes/depois, ajuste dos algoritmos | 🟡 ferramentas prontas (`calibrate`, `compare`); **falta rodar com CR2 reais** |
| **6** | ML residual | ⏸ só se a Fase 5 mostrar erro estruturado (ver CALIBRATION.md) |

## Próximos passos concretos (ordem sugerida)
1. Rodar `validate --reference` com o XMP real; acrescentar ao `schema.py` os campos que ele tiver e o gerador não (ex.: perfis de câmera específicos, `Look`).
2. Abrir 5–10 XMPs gerados no Lightroom/ACR; corrigir qualquer incompatibilidade.
3. `calibrate` com 20+ pares por câmera → `profiles/cameras/<modelo>.yaml`.
4. Cascata de rosto (ou detector DNN open source) ligado em `skin.face_cascade` para subir a confiança de pele.
5. Perfis de lente: mapear em `lens.profiles` os nomes exatos dos `.lcp` usados.
6. Equalização de exposição entre fotos *da mesma cena* (rajadas), análogo à consistência de WB.
7. Ruído medido na imagem (não só ISO) para NR; máscaras locais (Gradiente/Radial) fora de escopo.

- Calibrar o caminho JPEG com pares JPG + JPG editado no Lightroom; validar a abertura no Lightroom/ACR.

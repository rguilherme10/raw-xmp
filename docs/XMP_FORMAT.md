# Formato XMP

> **Proveniência.** O esquema nasceu do vocabulário público `crs:*`. Depois foram fornecidos 3 XMPs reais (Lightroom/Bridge, Camera Raw 5.3 toolkit,
> `tests/data/ref_*.xmp`); o parser/gerador foi ajustado a eles e há testes sobre eles. **Ainda não foi possível abrir os XMPs *gerados* no Lightroom
> aqui** — a prova disso é o seu uso. Relato de uso: um XMP gerado (forma de atributos) foi aplicado no Lightroom com resultado visual correto.

## Duas formas equivalentes
* **atributos** (padrão): `<rdf:Description crs:Exposure2012="+0.35" …>`
* **elementos** (como os seus arquivos): `<crs:Exposure2012>0.35</crs:Exposure2012>`, números **sem sinal**.
Usando `--template` (ou `style.template`) o gerador adota a forma do template. `Look`, `Crop*`, `GrayMixer*` etc. do template são preservados;
`Temperature`/`Tint` do template **nunca** são herdados.

## Estrutura
```xml
<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core 7.0-c000">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description rdf:about="" xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
        crs:Version="16.0" crs:ProcessVersion="11.0" crs:WhiteBalance="Custom" crs:Temperature="4757" crs:Tint="-9"
        crs:Exposure2012="-0.95" crs:Contrast2012="+8" … crs:HasSettings="True">
      <crs:ToneCurvePV2012><rdf:Seq><rdf:li>0, 0</rdf:li> … <rdf:li>255, 255</rdf:li></rdf:Seq></crs:ToneCurvePV2012>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>
```
Exemplo completo gerado: [`examples/IMG_0006_warm_cast.xmp`](examples/IMG_0006_warm_cast.xmp).

## Campos escritos
| Grupo | Campos `crs:` |
|---|---|
| Versão | `Version`, `ProcessVersion` (11.0 = "2012"), `HasSettings`, `AlreadyApplied`, `RawFileName`, `ConvertToGrayscale` |
| Balanço de branco | `WhiteBalance` (`As Shot`/`Custom`), `Temperature`, `Tint` — **só em `Custom`** (ou `white_balance.write_as_shot_values: true`) |
| Tom | `Exposure2012`, `Contrast2012`, `Highlights2012`, `Shadows2012`, `Whites2012`, `Blacks2012`, `Texture`, `Clarity2012`, `Dehaze` |
| Presença | `Vibrance`, `Saturation` |
| Curvas | `ToneCurveName2012`, `ToneCurve`, `ToneCurvePV2012`, `ToneCurvePV2012Red/Green/Blue` (sequências `rdf:Seq`), `ParametricShadows/Darks/Lights/Highlights`, `Parametric*Split` |
| HSL | `HueAdjustment*`, `SaturationAdjustment*`, `LuminanceAdjustment*` × {Red, Orange, Yellow, Green, Aqua, Blue, Purple, Magenta} |
| Color grading | `SplitToningShadow/HighlightHue/Saturation`, `SplitToningBalance`, `ColorGradeMidtoneHue/Sat/Lum`, `ColorGradeShadow/HighlightLum`, `ColorGradeGlobalHue/Sat/Lum`, `ColorGradeBlending` |
| Calibração de câmera | `ShadowTint`, `Red/Green/BlueHue`, `Red/Green/BlueSaturation` |
| Perfil | `CameraProfile` |
| Detalhe | `Sharpness`, `SharpenRadius`, `SharpenDetail`, `SharpenEdgeMasking`, `LuminanceSmoothing`, `LuminanceNoiseReductionDetail/Contrast`, `ColorNoiseReduction`, `…Detail`, `…Smoothness` |
| Lente | `LensProfileEnable`, `LensProfileSetup`, `LensProfile*Scale`, `AutoLateralCA` (+ `LensProfileName/Filename` se mapeados) |
| Outros | `PostCropVignetteAmount`, `xmp:CreatorTool` |

## Formatação (como o Camera Raw escreve)
Inteiros assinados `+8`/`-3`/`0`; exposição `+0.35`/`0.00`; raio `+1.0`; temperatura sem sinal; booleanos `True`/`False`.
Tudo isso é centralizado em `rawxmp/xmp/schema.py` (`format_value`/`parse_value`), que também define faixas válidas e opções enumeradas.

## Camadas de código
* `xmp/schema.py` — registro de parâmetros (tipo, faixa, formato, opções).
* `xmp/model.py` — `XMPModel`: atributos já formatados + curvas + namespaces + extras preservados. **Sem XML.**
* `xmp/serializer.py` — `XMPSerializer`: `ElementTree` (escape correto, sem duplicatas) + envelope `xpacket`.
* `xmp/parser.py` — lê XMP existente (template, validação, calibração); elementos desconhecidos são preservados.
* `xmp/builder.py` — `Decision` → modelo, opcionalmente sobre um template.

## Validação (`validate`)
1. XML bem-formado; 2. `x:xmpmeta`/`rdf:RDF`/`rdf:Description`; 3. namespaces obrigatórios e URIs corretas;
4. parâmetros conhecidos (desconhecido = *aviso*); 5. tipo, inteiro/real, opções e **faixa do formato**; 6. **limites do config**;
7. curvas: ≥ 2 pontos, x estritamente crescente, y monotônico, 0..255, extremos 0 e 255; 8. HSL completo (24 campos);
9. com `--reference`: atributos/curvas/namespaces ausentes ou extras e **cobertura** da referência.

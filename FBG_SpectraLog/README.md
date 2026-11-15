# FBG SpectraLog

Grava o **espectro bruto** (comprimento de onda × potência) dos interrogadores **FBGS FBG-scan** em paralelo ao **ILLumiSense**, que só salva os comprimentos de onda de pico. Serve para análise óptica de FBGs uniformes e chirped: alargamento, divisão de pico, assimetria e caracterização de dano.

```
 Interrogador FBG-scan ──USB──> ILLumiSense ──(web service ILLWS, http://localhost:8002/ILLWS/)──> FBG SpectraLog
                                (continua gravando λ                                              (grava o espectro bruto,
                                 normalmente)                                                      picos, métricas e eventos)
```

* **Detecta sozinho** quando o ILLumiSense está aberto, conecta e passa a seguir os **canais ligados** (até 4).
* **Somente leitura.** Nenhuma configuração do ILLumiSense é alterada, e o ILLumiSense continua funcionando e gravando como sempre.
* **Não para com a tela desligada.** O Windows fica impedido de suspender enquanto o programa estiver aberto, e a tela pode apagar normalmente.
* **Gravação em ciclos.** Cada ciclo (por tempo, por número de espectros ou por rajada) vai para uma pasta própria, com `fsync` a cada 2 s. Uma queda de energia perde no máximo esse intervalo.
* **Reconexão e retomada automáticas.** Se o ILLumiSense fechar ou travar, a gravação continua sozinha quando ele voltar, e a lacuna fica registrada. Se o PC reiniciar, o ensaio é retomado na mesma pasta.
* **Frequência livre**, em Hz, em intervalo (s) ou em intervalo (min), em modo **contínuo** ou **intermitente** (rajadas periódicas, ex.: 10 s a cada 10 min).
* **Gráficos ao vivo** com estilo de figura científica: espectro (sobreposto ou grade 2×2), cascata (espectrograma), evolução temporal (Δλ Bragg, FWHM, potência, correlação com referência), além de tabela de métricas e exportação PNG/SVG.

---

## 1. Instalação

### Opção A: executável pronto (recomendado)
1. No GitHub, abra a aba **Actions**, entre na execução mais recente de **“FBG SpectraLog - executável Windows”** e baixe o artefato **FBG_SpectraLog_windows**.
2. Descompacte em uma pasta (ex.: `C:\FBG_SpectraLog`) e abra **`FBG_SpectraLog.exe`**.

### Opção B: a partir do código (Python 3.10 a 3.13)
1. Instale o Python de https://www.python.org/downloads/ marcando **“Add python.exe to PATH”**.
2. Dê dois cliques em **`INSTALAR.bat`** (só na primeira vez).
3. Abra o programa com **`INICIAR.bat`**.

Requisitos: Windows 10 ou 11, 64 bits, e o ILLumiSense instalado no mesmo computador.

---

## 2. Uso passo a passo

1. Abra o **ILLumiSense** como sempre, conecte o interrogador e **ligue os canais** que vai usar.
2. Abra o **FBG SpectraLog**. Em poucos segundos a faixa superior mostra
   `ILLumiSense: em execução` · `Web service: conectado` · `Equipamento: FBG-scan 804D · 3/4 canais`.
3. No painel **Configurações**:
   * **Canais.** Com “Seguir os canais ligados no ILLumiSense” marcado, grava exatamente o que está ligado lá. Desmarque para escolher manualmente.
   * **Aquisição.** Escolha a frequência (ex.: `1 Hz`, `30 s` ou `5 min`) e o modo (contínuo ou intermitente).
   * **Gravação em ciclos.** Escolha a pasta, o nome e o tamanho de cada ciclo (ex.: um arquivo por hora).
4. Clique em **▶ Iniciar ensaio** (F5).
5. Opcional: clique em **Capturar referência** (F8) antes de carregar a amostra. A partir daí, a tela mostra o espectro de referência tracejado, o deslocamento por correlação (Δλ ref) e a correlação ρ.
6. Para encerrar, use **■ Parar ensaio** (Shift+F5), que pede confirmação.

A aba **Eventos** registra conexões, quedas, rotações de ciclo e avisos. O mesmo conteúdo é gravado em `eventos.log` dentro da pasta do ensaio.

---

## 3. Ensaios longos e tela desligada

Desligar a **tela** não interrompe nada. O que poderia interromper um ensaio é o Windows **suspender** o PC, **desligar a porta USB** do interrogador ou **estrangular** programas em segundo plano. Enquanto o FBG SpectraLog estiver aberto:

* ele registra uma *power request* `SystemRequired + ExecutionRequired`, que você pode conferir com `powercfg /requests`;
* desativa o *EcoQoS*/power throttling para si próprio e para o processo do ILLumiSense;
* roda a aquisição em threads próprias, independentes da interface (minimizar a janela não afeta nada).

Para ensaios de vários dias, clique também em **Configurações › Energia › Otimizar plano de energia do Windows…**. Isso ajusta suspensão e hibernação para “nunca”, desativa a **suspensão seletiva de USB** e configura “fechar a tampa” como “não fazer nada”. O tempo de desligamento da tela não é alterado. Recomenda-se ainda:

* **pausar o Windows Update** durante o ensaio (Configurações › Windows Update › Pausar);
* no Gerenciador de Dispositivos, nas portas USB, desmarcar “Permitir que o computador desligue este dispositivo para economizar energia”;
* deixar notebooks na tomada.

**Se o PC reiniciar mesmo assim** (queda de energia ou atualização forçada), ao abrir o programa de novo aparece “Ensaio interrompido”. Após 60 s ele continua sozinho na mesma pasta, num novo ciclo. Marque “Abrir o FBG SpectraLog ao iniciar o Windows” para que isso aconteça sem ninguém no laboratório. O ILLumiSense também precisa abrir com o Windows: coloque um atalho dele na pasta `shell:startup`.

---

## 4. Arquivos gravados

```
Ensaios/2026-11-15_101500_ensaio_FBG/
├── ensaio_info.json      equipamento (tipo, SN, versões), configuração completa, início/fim, status
├── eventos.log           conexões, LACUNAS, ciclos, avisos (texto, uma linha por evento)
├── resumo_ciclos.csv     ciclo, início, fim, nº de amostras, primeira amostra
├── referencias/          espectros de referência capturados (λ, P)
├── ciclo_0001/
│   ├── amostras.csv      amostra, data_hora, t_unix, t_ensaio_s, rajada, canais_ok, duracao_aquisicao_ms
│   ├── CH1_potencia.csv  amostra, t_unix, t_ensaio_s, rajada, repetido, P0001 … PNNNN
│   ├── CH1_lambda.csv    amostra, t_unix, t_ensaio_s, rajada, L0001 … LNNNN        (nm)
│   ├── CH2_…             (um par de arquivos por canal)
│   ├── picos.csv         picos do próprio ILLumiSense: amostra, t_unix, t_ensaio_s, canal, fbg, lambda_nm, potencia
│   └── metricas.csv      métricas espectrais de cada pico (ver seção 5)
└── ciclo_0002/ …
```

* **Uma linha = um espectro.** A linha *i* de `CHn_potencia.csv` corresponde à linha *i* de `CHn_lambda.csv`, com o mesmo número de `amostra`.
* O eixo λ é gravado **em todo espectro** por padrão (dado bruto completo). Com “Eixo λ: somente quando mudar”, ele só é regravado quando muda, e os leitores abaixo completam as linhas automaticamente.
* `t_unix` = segundos desde 1970 (UTC), `t_ensaio_s` = segundos desde o início do ensaio.
* `repetido = 1` indica um espectro idêntico ao anterior, o que acontece quando a frequência pedida é maior que a taxa de varredura do ILLumiSense.
* O formato CSV é internacional (`,` e `.`) por padrão. A opção “Excel Brasil” usa `;` e `,`.
* Ciclos fechados podem ser compactados (`.csv.gz`, cerca de 4× menor). Os leitores em Python leem esses arquivos direto.

**Python**
```python
from spectralog.reader import load_test_channel
d = load_test_channel(r"C:\...\2026-11-15_101500_ensaio_FBG", ch=2)
d["P"], d["L"], d["t_ensaio"]          # matrizes (n_espectros × n_pontos) e tempo (s)
```
Veja também `ferramentas/exemplo_analise.py`.

**MATLAB:** `d = ler_ensaio_fbg(pasta, 2);` (arquivo `ferramentas/ler_ensaio_fbg.m`).

**HDF5 / .mat:** use **Ferramentas › Converter ensaio para HDF5 / MATLAB**.

**Visualizador:** em **Ferramentas › Visualizador de ensaios**, abra um ensaio salvo para percorrer os espectros, sobrepor N espectros ao longo do ensaio e ver a cascata completa.

---

## 5. Métricas espectrais

Calculadas para cada pico detectado (aba Espectro e `metricas.csv`):

| Métrica | Definição |
|---|---|
| λ máx | posição do máximo com interpolação parabólica sub-pixel |
| λ centroide | centroide da potência linear na região acima de −3 dB |
| P máx | potência de pico (unidade do ILLumiSense; dBm detectado automaticamente) |
| FWHM | largura a −3 dB (meia potência) |
| BW −X dB | largura a −X dB (padrão −10 dB), indicada para FBG chirped e espectros largos |
| Assimetria | (λ_dir − λ_máx)/(λ_máx − λ_esq) a −3 dB (1 = simétrico) |
| Skewness | 3º momento padronizado do pico |
| Sub-picos | máximos locais dentro da banda −X dB. Mais de 1 indica divisão de pico (deformação não uniforme, carga transversal, dano) |
| Δλ ref / ρ ref | deslocamento por correlação cruzada e correlação de Pearson em relação ao espectro de referência |

---

## 6. Frequência máxima

Cada espectro é lido do ILLumiSense por HTTP. O painel **Aquisição** mostra o tempo medido para ler todos os canais e a **frequência máxima estimada**. Se você pedir mais que isso, o programa grava o mais rápido possível e conta os instantes **perdidos**, visíveis ao passar o mouse sobre o indicador “Gravação”. Acima da taxa de varredura do ILLumiSense, os espectros se repetem e ficam marcados como `repetido`; use “Não gravar espectros repetidos” para descartá-los.

---

## 7. Problemas comuns

| Sintoma | O que verificar |
|---|---|
| “Web service: desconectado — conexão recusada” | O ILLumiSense está aberto e com o interrogador conectado? Abra **Diagnóstico da conexão**. Teste no navegador `http://localhost:8002/ILLWS/GetHWInformation/`. |
| Porta diferente de 8002 | A porta é lida automaticamente do `niembeddedws.conf` na pasta do ILLumiSense. Também dá para ajustar em Configurações › Conexão. |
| Canal aparece vazio | O canal está ligado no ILLumiSense? A numeração detectada está correta? Ela pode ser trocada em Conexão › Numeração (1–4 ou 0–3). |
| Firewall pergunta sobre o ILLumiSense | Permita o acesso em redes privadas. A comunicação é local (`127.0.0.1`). |

O botão **Diagnóstico da conexão** testa cada rota (somente leitura) e gera um relatório que pode ser copiado. Envie esse relatório se algo não funcionar.

---

## 8. Simulador (treino sem o equipamento)

**Ferramentas › Iniciar simulador do ILLumiSense** (ou `SIMULADOR.bat`) cria um ILLumiSense falso com a mesma API: três FBGs uniformes sob carga cíclica (CH1), uma FBG **chirped** com dano progressivo (CH2) e uma FBG com **divisão de pico** (CH3). Use **apenas com o ILLumiSense real fechado**.

## 9. Linha de comando

```bat
FBG_SpectraLog_console.exe --simulador [--porta 8002]
FBG_SpectraLog_console.exe --headless --taxa 2 --nome ensaio1 [--pasta D:\Ensaios] [--duracao 3600]
FBG_SpectraLog_console.exe --headless --retomar "D:\Ensaios\2026-11-15_101500_ensaio1"
```
Sem o executável: `python -m spectralog …` dentro da pasta `FBG_SpectraLog`.

---

### Sobre a interface com o ILLumiSense
As rotas usadas (`GetHWInformation`, `GetActiveChannels`, `GetSpectrum/:Channel`, `GetPeaks/:Channel`, …) foram obtidas do pacote `ILLWS.lvws` distribuído com o ILLumiSense (LabVIEW 21, servidor web embutido na porta 8002). O programa foi testado com o simulador incluído. **Na primeira vez com o equipamento real, rode o Diagnóstico da conexão** e confira se `GetSpectrum/1` devolve `SpecWav[...]` e `SpecPow[...]` com centenas de pontos.

Testes automatizados: `python -m pytest -q tests`.

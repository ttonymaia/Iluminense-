# Engenharia reversa do ILLumiSense (interrogador FBG)

Este documento registra o que foi descoberto nos arquivos deste repositório e
que serviu de base para a ferramenta `fbg_lab/` (aquisição do espectro bruto
e análise de chirp). Tudo foi obtido por análise estática dos arquivos, sem
executar o programa: o que não pôde ser confirmado está marcado como tal.

## 1. O que é cada coisa

| Arquivo | O que é |
|---|---|
| `ILLumiSense.exe` | Aplicação LabVIEW 2021 (21.0.1), 32 bits, da **FBGS** (tela de abertura "Splash FBGS"). Os VIs estão num arquivo LVAR/zip embutido no recurso RCDATA #2 do executável. |
| `ILLWS.lvws` | Pacote do **serviço web REST** "ILLWS" que roda dentro do ILLumiSense (zip com `WebService.ini`, `.lvwsdsc` e os VIs). |
| `niembeddedws.conf` | Configuração do servidor web embutido do LabVIEW: **HTTP na porta 8002** e HTTPS na 8003, carregando `ILLWS.lvws`. |
| `Sense2020Dll.dll` | SDK do motor espectral **BaySpec** (32 bits, stdcall). Faz aquisição, conversão para double, temperatura do termistor e (se houver arquivo `Calib_LT_<série>.dat`) o eixo λ. |
| `_USB20BSDevOpDLL.dll` | Camada de baixo nível BaySpec (classe C++ `USB20BSDevOp`, USB Cypress, EEPROM, FPGA, TEC). |
| `bayu2*.sys/.inf`, `BSUSBVistaWin7x64.*`, `*.spt`, `*.iic` | Drivers USB e firmware do espectrômetro (Cypress FX2). O `readme.txt` cita detectores InGaAs lineares SU256/SU512 e CCD Hamamatsu S10420. |
| `F13424-0019-001.calib` | Calibração do espectrômetro de série F13424-0019-001 (formato na seção 4). |
| `ISMMSDK HW01.dll`, `iSMMPlusSDK HW02.dll` | Watchdog / gerenciamento de hardware do PC industrial (WMI). Não relacionados ao espectro. |
| `TCP Receiver.exe`, `Data Receiver.vi`, `Interpret string.vi` | Exemplo de cliente do *streaming* TCP: recebe **apenas os picos** (ver seção 3.2). |

## 2. Fluxo interno do ILLumiSense

Reconstruído a partir dos nomes e dependências dos VIs extraídos do executável:

```
Sense2020Dll: DLL_Snapshot_Acquisition / continuous  ->  intensidade[512] + temperatura
        |
        v
Referencing with etalon.vi  (se houver etalon)      ou      Load BaySpec data if no etalon.vi
  (picos de um etalon de 200 GHz corrigem o eixo)           (tabela pixel->lambda vs temperatura do .calib)
        |
        v
"Spec Pow" + "Pixel Wavelengths"   (espectro completo, unidade "Power [%]")
        |
        v
Remove sidelobes.vi -> IICG/CoG Interpollate + Iterate analytical v8.vi
        |                     (centro de gravidade iterativo sobre os pontos acima do limiar)
        v
"Peak Wav" / "Peak Pow" por FBG  ->  tela, log (Save cont wav main.vi), TCP (TCP string create v7.vi)
```

Conclusão: **o espectro completo existe dentro do programa**; o que sai para o
usuário (TCP e log contínuo) é só o resultado do centro de gravidade. Uma FBG
com deformação não uniforme continua gerando *um* número (o CoG), e toda a
informação de forma — alargamento, assimetria, divisão do pico — é descartada.

## 3. Interfaces de saída

### 3.1 Serviço web ILLWS (porta 8002) — **o caminho usado pela ferramenta**

Extraído de `ILLWS.lvws/WebService.ini` e `WebService.lvwsdsc`. Todos os
métodos são `GET`, sem chave de API, resposta em JSON (rótulos dos
indicadores do LabVIEW). Base: `http://<pc>:8002/ILLWS`

| Rota | Resposta |
|---|---|
| `/GetSpectrum/:Channel` | `{"Error", "SpecWav": [double], "SpecPow": [double]}` — **espectro completo** |
| `/GetSpectrumBasic/:Channel` | `{"Error", "SpecPow": [double]}` (sem o eixo λ) |
| `/GetPeaks/:Channel` | `{"Error", "PeakWavelengths": [double], "PeakPowers": [double]}` |
| `/GetHWInformation/` | `{"Error", "TypeHW", "NumberOfChannels", "ScanRates": [u16]}` |
| `/GetSerialNumber/`, `/GetFWVersion/`, `/GetSWVersion/` | `{"Error", "SN" / "FWVersion" / "SWVersion"}` |
| `/GetScanRate/` · `/SetScanRate/:SetScanRate` | taxa de varredura (Hz) |
| `/GetActiveChannels/` · `/EnableChannel/:Enable/:Channel` | canais ativos |
| `/GetChannelSettings/:Channel` | `{"SensitivityMode", "IntegrationTime", "ThresholdLevel"}` |
| `/SetChannelSettings/:ThresholdLevel/:IntegrationTime/:SensitivityMode/:Channel` | altera ajustes do canal |
| `/AutoSetChannel/:Channel`, `/AutoSetExpectedPeaks/:Channel` | auto-ajuste |
| `/GetChannelStatus/:Channel` | `{"ErrorStatus": [i8]}` |
| `/EnableTCP/:Enable` | liga/desliga o *streaming* TCP |
| `/LoadSettings/`, `/SaveSettings/`, `/Reboot/` | gerenciamento |

Teste rápido no PC do interrogador, com o ILLumiSense aberto: abrir no
navegador `http://localhost:8002/ILLWS/GetHWInformation/`. Se responder, o
`fbglab` funciona. Se não responder, rode `python -m fbglab sondar` (testa
portas, prefixos e canais 0/1/2). **Não confirmado**: se a numeração de canal
começa em 0 ou em 1 nesta unidade (o `sondar` mostra).

Observação: o serviço aceita conexões de outros PCs da rede
(`WebServer.TcpAccess="c+*"` no `.ini`) — dá para adquirir de um notebook,
desde que o firewall do Windows libere a porta 8002.

### 3.2 *Streaming* TCP (somente picos)

`Data Receiver.vi` / `Interpret string.vi` mostram o formato: primeiro uma
leitura com o tamanho da mensagem ("Bytes to read"), depois uma *string*
tabulada com Date, Time, LineNumber, OpticalLineNumber, Qty of Optical Lines,
Qty of FBG's, System Status, Peak Wavelengths e Peak Powers. Não há espectro
nesse canal — é por isso que o fluxo atual do laboratório só vê o
"comprimento de onda cravado".

### 3.3 Botão "Save spectrum" (tela Settings)

O painel `Init settings.vi` tem um gráfico "Spectrum" (Wavelength [nm] x
Power [%]) e um botão "Save spectrum": salva **um** espectro em texto. Útil
para conferência; o comando `python -m fbglab importar` lê esses arquivos
(vírgula ou ponto decimal).

### 3.4 Acesso direto pela `Sense2020Dll.dll`

Protótipos obtidos por desmontagem (x86, `__stdcall`):

```c
WORD  DLL_Open_Device(const char* pasta_calib);   // NULL = sem Calib_LT; 0 = OK
                                                  // 1 aquisição contínua ativa, 3 nenhum/>1 dispositivo,
                                                  // 4 falha ao abrir, 5 série inválida, 8 Calib_LT inválido
int   DLL_Get_Pixel_Count(void);                  // 512
BOOL  DLL_Get_Device_SN(char buf[64]);
BOOL  DLL_Get_HW_FW_REV(DWORD*, DWORD*, DWORD*);
BOOL  DLL_Set_IntegrationTime_SampleRate(DWORD t_int_us /*>=20*/, DWORD taxa_Hz /*1..8000*/);
BOOL  DLL_Set_Sensor_Mode(WORD modo);             // sensibilidade
BOOL  DLL_Snapshot_Acquisition(double* lambda, double* intensidade, double* temp_C);
void  DLL_Close_Device(void);
// também existem: Start/Stop_Continuous_Acquisition, Get_Spectrum_Count,
// Get_IndexedSpectrum (18 argumentos), SearchPeaks, IRS_Calibration,
// Set/Get_OpticalSwitch(_AutoScan), Set/Get_GPIO
```

Detalhes vistos no código de `DLL_Snapshot_Acquisition`: lê uma linha do
detector, converte para double, calcula a temperatura do termistor
(*R*₀ = 10 kΩ) e, **só se** o arquivo `Calib_LT_<série>.dat` tiver sido
carregado, calcula λ(pixel) = c₀ + c₁·T + c₂·T² por pixel; caso contrário
devolve λ = índice do pixel. Como esse arquivo não está aqui, o modo DLL do
`fbglab` usa a tabela do `.calib` (seção 4).

Restrições do modo DLL: DLL de 32 bits → Python 32 bits; o USB é exclusivo →
**o ILLumiSense precisa estar fechado**; sem correção por etalon (exatidão
absoluta menor, mas variações relativas — que é o que importa para chirp —
são preservadas). A seleção de canal pela chave óptica
(`DLL_Set_OpticalSwitch`) não foi implementada.

## 4. Formato do arquivo `.calib`

Dados "achatados" do LabVIEW, *big-endian*:

| Campo | Conteúdo |
|---|---|
| `u32` + `char[n]` | série: `F13424-0019-001` |
| `u32 64, u32 1` + `f64[64]` | [0] = 22,5 °C; [1..63] = **picos de um etalon de referência**, 1500,474–1599,741 nm, espaçamento constante de **199,97 GHz** |
| `u32 851, u32 513` + `f64[851×513]` | tabela: cada linha = [temperatura, λ(pixel 0..511)], de −15,0 a 70,0 °C em passos de 0,1 °C |

Números desta unidade (`python -m fbglab calib`):

- 512 pixels; faixa a 25 °C: **1505,50–1592,99 nm**
- dispersão: **158–185 pm/pixel** (média 171 pm)
- deriva térmica do espectrômetro: ~14 pm/°C (compensada pela tabela/etalon)

## 5. Consequências para medir chirp

- Uma FBG típica tem 0,1–0,3 nm de largura: o pico ocupa só **2–4 pixels**.
  A forma vista é a refletância da rede convoluída com a resposta do
  instrumento (ordem de 0,2–0,3 nm; o ajuste T-matrix estima esse valor a
  partir da referência).
- Sensibilidade à deformação: Δλ/ε = λ(1 − pₑ) ≈ **1,21 pm/µε** em 1550 nm.
- Um gradiente linear que dá Δε entre as pontas da rede espalha o pico por
  W ≈ 1,21 pm/µε × Δε. Com médias de 10–30 espectros, os testes no simulador
  (modelo idealizado: rede uniforme, resposta gaussiana de 0,25 nm) mostram
  piso de detecção da ordem de **100–150 µε** de Δε (10–15 µε/mm numa rede de
  10 mm) e boa recuperação acima de ~250 µε. No equipamento real o piso deve
  ser medido: repita a referência várias vezes e veja a dispersão de Δε.
- O **sinal** do gradiente não é observável: a refletância de uma estrutura
  sem perdas é a mesma vista dos dois lados.
- **Saturação** do detector achata o topo do pico e imita chirp. Mantenha
  tempo de integração e sensibilidade **fixos** durante o ensaio (desligue o
  Autoset) e o pico abaixo de ~90 % da escala.

## 6. Como reproduzir a extração

```bash
# VIs do executável (recurso RCDATA #2 -> LVAR -> zip)
pip install pefile
git clone https://github.com/mefistotelis/pylabview
python - <<'EOF'
import pefile
pe = pefile.PE("ILLumiSense.exe")
rc = next(e for e in pe.DIRECTORY_ENTRY_RESOURCE.entries if e.id == 10)          # RT_RCDATA
d = next(e for e in rc.directory.entries if e.id == 2).directory.entries[0].data.struct
open("rc2.bin", "wb").write(pe.get_data(d.OffsetToData, d.Size))
EOF
PYTHONPATH=pylabview python -m pylabview.readRSRC -x -i rc2.bin -m out.xml   # gera out_LVzp.bin (zip)
unzip out_LVzp.bin                                                       # VIs do programa

# serviço web
unzip ILLWS.lvws    # WebService.ini tem as rotas; WebService.lvwsdsc os tipos de saída

# DLL
pip install capstone pefile   # desmontar as funções exportadas
```

Os VIs do executável vêm sem diagrama de blocos (só código compilado e
painéis frontais), então a lógica interna do CoG e da referência por etalon
não foi reconstruída linha a linha — apenas o papel de cada VI.

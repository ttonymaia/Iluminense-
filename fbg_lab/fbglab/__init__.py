"""fbglab - aquisicao e analise do espectro bruto do interrogador ILLumiSense.

O software original (ILLumiSense, FBGS, motor espectral BaySpec "Sense2020")
so entrega o comprimento de onda de pico (centro de gravidade) de cada FBG
pelo streaming TCP. Este pacote busca o espectro completo (todos os pixels
do CCD InGaAs) para permitir analisar a forma do pico: alargamento,
assimetria, divisao do pico e chirp induzido por deformacao nao uniforme.

Modulos:
    fontes    - de onde vem o espectro (servico web do ILLumiSense, DLL, simulador, arquivo)
    calib     - leitura do arquivo .calib (tabela pixel -> lambda vs temperatura, etalon)
    fisica    - modelo de matriz de transferencia (T-matrix) da FBG com perfil de deformacao
    analise   - metricas do pico e estimativa do chirp / gradiente de deformacao
    sessao    - gravacao e leitura dos ensaios (.npz + .json + .csv)
    graficos  - espectrograma (cascata), sobreposicao, evolucao das metricas
    rotina    - rotina de laboratorio guiada por protocolo (passos de carga)
"""

__version__ = "1.0.0"

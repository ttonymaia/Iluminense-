function d = ler_ensaio_fbg(pasta, canal, ciclos)
% LER_ENSAIO_FBG  Lê o espectro bruto gravado pelo FBG SpectraLog (MATLAB R2019b+).
%
%   d = ler_ensaio_fbg('C:\Ensaios\2026-11-15_101500_ensaio_FBG', 2);
%   d = ler_ensaio_fbg(pasta, 2, [1 2 3]);     % apenas os ciclos 1, 2 e 3
%
%   Campos de d:
%     d.P         (n x N)  potência de cada espectro (dBm ou u.a., como o ILLumiSense fornece)
%     d.L         (n x N)  comprimento de onda de cada espectro (nm)
%     d.amostra   (n x 1)  número da amostra
%     d.t_ensaio  (n x 1)  tempo desde o início do ensaio (s)
%     d.t         (n x 1)  data/hora (datetime)
%     d.rajada    (n x 1)  número da rajada (modo intermitente) ou NaN
%     d.repetido  (n x 1)  1 se o espectro era idêntico ao anterior
%
%   Exemplo:  imagesc(d.L(1,:), d.t_ensaio/60, d.P); axis xy
%             xlabel('\lambda (nm)'); ylabel('t (min)'); colorbar
%
%   Ciclos compactados (.csv.gz) devem ser descompactados antes (gunzip).

if nargin < 3, ciclos = []; end
lst = dir(fullfile(pasta, 'ciclo_*'));
lst = lst([lst.isdir]);
P = []; L = []; M = [];
for k = 1:numel(lst)
    num = sscanf(lst(k).name, 'ciclo_%d');
    if ~isempty(ciclos) && ~ismember(num, ciclos), continue; end
    fp = fullfile(pasta, lst(k).name, sprintf('CH%d_potencia.csv', canal));
    fl = fullfile(pasta, lst(k).name, sprintf('CH%d_lambda.csv', canal));
    if ~isfile(fp), continue; end
    A = ler_csv(fp);                         % amostra,t_unix,t_ensaio_s,rajada,repetido,P0001..
    if isempty(A), continue; end
    B = ler_csv(fl);                         % amostra,t_unix,t_ensaio_s,rajada,L0001..
    Pk = A(:, 6:end);
    Lv = B(:, 5:end);
    if size(B,1) == size(A,1) && isequal(B(:,1), A(:,1))
        Lk = Lv;
    else                                     % eixo λ gravado "somente quando mudar"
        idx = zeros(size(A,1),1);
        for i = 1:size(A,1)
            j = find(B(:,1) <= A(i,1), 1, 'last');
            if isempty(j), j = 1; end
            idx(i) = j;
        end
        Lk = Lv(idx, :);
    end
    P = [P; Pk]; L = [L; Lk]; M = [M; A(:, 1:5)]; %#ok<AGROW>
end
if isempty(M), error('Nenhum dado do CH%d em %s', canal, pasta); end
d.P = P; d.L = L;
d.amostra = M(:,1); d.t_ensaio = M(:,3); d.rajada = M(:,4); d.repetido = M(:,5);
d.t = datetime(M(:,2), 'ConvertFrom', 'posixtime', 'TimeZone', 'UTC');
d.t.TimeZone = 'local';
end

function A = ler_csv(f)
fid = fopen(f, 'r', 'n', 'UTF-8'); h = fgetl(fid); fclose(fid);
if count(h, ';') > count(h, ',')
    A = readmatrix(f, 'NumHeaderLines', 1, 'Delimiter', ';', 'DecimalSeparator', ',');
else
    A = readmatrix(f, 'NumHeaderLines', 1, 'Delimiter', ',');
end
A = A(~isnan(A(:,1)), :);   % descarta linha incompleta (queda de energia)
end

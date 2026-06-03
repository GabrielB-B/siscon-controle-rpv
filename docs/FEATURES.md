# Funcionalidades

## RPVs normais

- cadastro e edicao controlada de requisicoes individuais;
- controle de processo, beneficiario, documento, valores e status;
- pendencias documentais para registros incompletos;
- busca, filtros, ordenacao e paginação;
- alertas de processo e identificadores repetidos;
- historico de alteracoes sensiveis.

## RPVs dativos

- organizacao por C.I., lotes e itens;
- conciliacao de importacao;
- revisao de cabecalhos;
- separacao de itens com IRRF, sem IRRF e pendentes;
- filtros operacionais por responsavel, situacao e busca textual;
- cruzamentos com a fila principal quando necessario.

## Cotas mensais

- controle de saldo por ficha;
- consumo mensal;
- transferencia de saldo anterior;
- ajustes e movimentos;
- leitura operacional do mes atual;
- historico de cobertura para apoio a decisao.

## REINF

- visoes mensal e anual;
- conferencia fiscal por competencia;
- agrupamento e recortes por status;
- exportacoes para apoio operacional;
- separacao segura entre leitura operacional e leitura fiscal.

## BI operacional

- filtros por competencia, origem, grupo e responsavel;
- leitura executiva do ciclo;
- series mensais por grupo;
- pendencias e carteira em aberto;
- leitura dedicada de beneficiarios;
- exportacao CSV.

## Seguranca e operacao

- autenticacao com hash de senha;
- CSRF;
- troca obrigatoria de senha;
- recuperacao de senha com trilha local segura;
- throttling persistente em SQLite;
- healthcheck operacional;
- auditoria antes/depois de alteracoes sensiveis.

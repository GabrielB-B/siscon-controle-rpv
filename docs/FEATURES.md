# Funcionalidades

## Requisicoes Individuais

RPV significa Requisicao de Pequeno Valor. Nesta frente, o sistema controla pagamentos judiciais tratados individualmente no fluxo financeiro.

- Cadastro de processo e beneficiario.
- Controle de valor bruto, IRRF, valor liquido e status de pagamento.
- Validacao de documento CPF/CNPJ.
- Identificacao de registros sem IRRF.
- Pendencias documentais para casos incompletos.
- Historico de alteracoes sensiveis.
- Edicao controlada de valor bruto mediante confirmacao.

## Pagamentos em Lote

Alguns pagamentos chegam agrupados em C.I.s e lotes. A aplicacao separa lote, item e beneficiario para permitir acompanhamento detalhado sem perder a visao do conjunto.

- Organizacao por C.I., lote e item.
- Separacao de itens com IRRF, sem IRRF e pendentes.
- Regras de destino automatico durante importacao.
- Conferencia de duplicidades por documento e processo.
- Edicao controlada de campos sensiveis.

## Importacao Assistida

- Leitura de planilhas.
- Normalizacao de documentos, datas, status e valores.
- Bloqueio de duplicidades na planilha e no banco.
- Conciliacao de registros em estado inicial.
- Relatorios de saida para revisao operacional.

## REINF e BI Operacional

- Conferencia mensal e anual de pagamentos com IRRF.
- Agrupamento por beneficiario e competencia.
- Indicadores operacionais de status, pagamento e pendencias.
- Filtros por responsavel, periodo, situacao e busca textual.
- Competencia operacional baseada no mes de pagamento quando existe data de pagamento, com fallback para exercicio ou cadastro quando ainda esta em aberto.
- Filtros seguros de competencia aplicados na consulta e preservados na camada final para evitar leituras incorretas no BI.
- Visao consolidada para apoiar tomada de decisao e priorizacao da rotina financeira.

## Operacao e Seguranca

- Recuperacao de senha com entrega local segura para desenvolvimento e suporte a integracao real fora do Git.
- Healthcheck e auditoria operacional preventiva para verificar consistencia do dataset.
- Throttling persistente em SQLite para login e recuperacao de senha.
- Scripts de backup e publicacao preparados para separar ambiente de desenvolvimento e runtime.

## Contexto Financeiro

- Apoio ao controle de honorarios advocaticios pagos pelo Estado.
- Organizacao de fluxos tipicos de orgao publico estadual, como PGE.
- Separacao entre requisicoes individuais, pagamentos em lote e pendencias.
- Registro de status de empenho, ordens bancarias, IRRF e informacoes de conferencia fiscal.

## Auditoria

- Registro de usuario, data, hora e acao.
- Snapshot antes/depois de campos alterados.
- Visualizacao em historico por entidade.
- Destaque para alteracoes criticas e dados sensiveis.

## Usuarios e Acesso

- Login com senha criptografada.
- Perfis de usuario e administrador.
- Troca obrigatoria de senha.
- Recuperacao de senha por codigo.
- Controle de cadastro pendente.

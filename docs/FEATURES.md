# Funcionalidades

## Requisições Individuais

RPV significa Requisição de Pequeno Valor. Nesta frente, o sistema controla pagamentos judiciais tratados individualmente no fluxo financeiro.

- Cadastro de processo e beneficiário.
- Controle de valor bruto, IRRF, valor líquido e status de pagamento.
- Validação de documento CPF/CNPJ.
- Identificação de registros sem IRRF.
- Pendências documentais para casos incompletos.
- Histórico de alterações sensíveis.

## Pagamentos em Lote

Alguns pagamentos chegam agrupados em C.I.s e lotes. A aplicação separa lote, item e beneficiário para permitir acompanhamento detalhado sem perder a visão do conjunto.

- Organização por C.I., lote e item.
- Separação de itens com IRRF, sem IRRF e pendentes.
- Regras de destino automático durante importação.
- Conferência de duplicidades por documento e processo.
- Edição controlada de campos sensíveis.

## Importação Assistida

- Leitura de planilhas.
- Normalização de documentos, datas, status e valores.
- Bloqueio de duplicidades na planilha e no banco.
- Conciliação de registros em estado inicial.
- Relatórios de saída para revisão operacional.

## REINF E BI

- Conferência mensal e anual de pagamentos com IRRF.
- Agrupamento por beneficiário e competência.
- Indicadores operacionais de status, pagamento e pendências.
- Filtros por responsável, período, situação e busca textual.

## Auditoria

- Registro de usuário, data, hora e ação.
- Snapshot antes/depois de campos alterados.
- Visualização em histórico por entidade.
- Destaque para alterações críticas e dados sensíveis.

## Usuários e Acesso

- Login com senha criptografada.
- Perfis de usuário e administrador.
- Troca obrigatória de senha.
- Recuperação de senha por código.
- Controle de cadastro pendente.

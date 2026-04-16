# Notas de Portfólio

Este projeto demonstra uma aplicação operacional realista, com foco em confiabilidade, rastreabilidade e tratamento seguro de dados sensíveis.

## Habilidades Demonstradas

- Modelagem de domínio com SQLAlchemy.
- Evolução de schema com Alembic.
- Organização Flask por Blueprints.
- Regras fiscais e financeiras com `Decimal`.
- Importação de planilhas com validação e conciliação.
- Auditoria de alterações com snapshots.
- Controle de acesso e recuperação de senha.
- Dashboards operacionais com filtros.
- Testes automatizados de regras de negócio e fluxos web.
- Separação entre código versionado e dados sensíveis.
- Scripts locais para execução em Windows.

## Decisões de Produto

- A aplicação prioriza segurança operacional: dados sensíveis ficam fora do repositório.
- Importações não entram direto no fluxo sem validação.
- Duplicidades são bloqueadas ou encaminhadas para revisão.
- Histórico registra quem alterou, quando alterou e o que mudou.
- Campos sensíveis exigem confirmação antes de alteração.

## Como Apresentar

Use screenshots anonimizadas, dados fictícios e um banco local descartável. Evite imagens com nomes, documentos, processos, valores reais, IPs internos ou caminhos de máquina pessoal.

Sugestão de narrativa:

1. Contexto: controle operacional de pagamentos e pendências.
2. Problema: planilhas e fluxos manuais geram risco de erro e baixa rastreabilidade.
3. Solução: aplicação web com validação, auditoria, dashboards e importação assistida.
4. Resultado técnico: código modular, migrations, testes e isolamento de dados.

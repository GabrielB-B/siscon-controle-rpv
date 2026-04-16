# Notas De Portfolio

Este projeto demonstra uma aplicacao operacional realista, com foco em confiabilidade, rastreabilidade e tratamento seguro de dados sensiveis.

## Habilidades Demonstradas

- Modelagem de dominio com SQLAlchemy.
- Evolucao de schema com Alembic.
- Organizacao Flask por Blueprints.
- Regras fiscais e financeiras com `Decimal`.
- Importacao de planilhas com validacao e conciliacao.
- Auditoria de alteracoes com snapshots.
- Controle de acesso e recuperacao de senha.
- Dashboards operacionais com filtros.
- Testes automatizados de regras de negocio e fluxos web.
- Separacao entre codigo versionado e dados sensiveis.
- Scripts locais para execucao em Windows.

## Decisoes De Produto

- A aplicacao prioriza seguranca operacional: dados sensiveis ficam fora do repositorio.
- Importacoes nao entram direto no fluxo sem validacao.
- Duplicidades sao bloqueadas ou encaminhadas para revisao.
- Historico registra quem alterou, quando alterou e o que mudou.
- Campos sensiveis exigem confirmacao antes de alteracao.

## Como Apresentar

Use screenshots anonimizadas, dados ficticios e um banco local descartavel. Evite imagens com nomes, documentos, processos, valores reais, IPs internos ou caminhos de maquina pessoal.

Sugestao de narrativa:

1. Contexto: controle operacional de pagamentos e pendencias.
2. Problema: planilhas e fluxos manuais geram risco de erro e baixa rastreabilidade.
3. Solucao: aplicacao web com validacao, auditoria, dashboards e importacao assistida.
4. Resultado tecnico: codigo modular, migrations, testes e isolamento de dados.

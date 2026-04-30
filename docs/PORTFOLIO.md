# Notas de Portfolio

Este projeto demonstra uma aplicacao operacional realista, com foco em confiabilidade, rastreabilidade e tratamento seguro de dados sensiveis.

O dominio funcional envolve controle de Requisicoes de Pequeno Valor, com pagamentos de honorarios advocaticios realizados pelo Estado. A versao publica descreve o problema e a solucao sem expor dados reais, documentos internos ou bases de producao.

## Habilidades Demonstradas

- Modelagem de dominio com SQLAlchemy.
- Evolucao de schema com Alembic.
- Organizacao Flask por Blueprints.
- Regras fiscais e financeiras com `Decimal`.
- Importacao de planilhas com validacao e conciliacao.
- Auditoria de alteracoes com snapshots.
- Controle de acesso e recuperacao de senha.
- Dashboards operacionais com filtros.
- BI operacional para conferencia de pagamentos, pendencias, responsaveis e competencias.
- Observabilidade basica com healthcheck, logs e auditoria operacional.
- Protecao com throttling persistente em SQLite e separacao segura entre desenvolvimento e runtime.
- Testes automatizados de regras de negocio e fluxos web.
- Scripts locais para execucao em Windows.

## Decisoes de Produto

- A aplicacao prioriza seguranca operacional: dados sensiveis ficam fora do repositorio.
- Importacoes nao entram direto no fluxo sem validacao.
- Duplicidades sao bloqueadas ou encaminhadas para revisao.
- Historico registra quem alterou, quando alterou e o que mudou.
- Campos sensiveis exigem confirmacao antes de alteracao.
- O BI preserva a competencia correta de pagamento como regra de seguranca do dominio.

## Como Apresentar

Use screenshots anonimizadas, dados ficticios e um banco local descartavel. Evite imagens com nomes, documentos, processos, valores reais, IPs internos ou caminhos de maquina pessoal.

Sugestao de narrativa:

1. Contexto: controle operacional de honorarios advocaticios pagos via Requisicoes de Pequeno Valor.
2. Problema: planilhas e fluxos manuais geram risco de erro e baixa rastreabilidade.
3. Solucao: aplicacao web com validacao, auditoria, dashboards, observabilidade e importacao assistida.
4. Resultado tecnico: codigo modular, migrations, testes e isolamento de dados.

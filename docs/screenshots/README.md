# Guia de Screenshots

Esta pasta existe para receber capturas demonstrativas do sistema de forma segura e profissional.

## Objetivo

As imagens do portfolio devem:

1. mostrar a interface real do produto
2. preservar a qualidade visual do sistema
3. evitar exposicao de dados pessoais, operacionais ou sensiveis
4. nao parecer montagem artificial

## O que sanitizar

Antes de publicar, remova ou substitua:

1. nome do usuario no topo direito
2. nome do usuario no rodape lateral
3. documentos pessoais
4. numeros de processo identificaveis
5. valores reais sensiveis, quando necessario
6. contagens internas que exponham volume operacional real
7. barra do navegador, URL, hostname, query string ou caminho de rota

## Como sanitizar sem perder qualidade

Recomendacao:

1. use overlays com a mesma linguagem visual da interface
2. mantenha tipografia, espacamento e cor consistentes
3. substitua o nome por algo como `Usuario demonstracao`
4. prefira valores zerados quando a captura for apenas ilustrativa
5. exporte em `PNG` em alta resolucao

Evite:

1. blur pesado
2. tarja preta
3. recorte agressivo que destrua a leitura da tela
4. edicao que deixe a imagem com cara de gerada por IA

## Capturas recomendadas

1. `dashboard-home-clean.png`
2. `bi-operacional-clean.png`
3. `cotas-clean.png`

## Formato sugerido

Nomeie os arquivos assim:

- `dashboard-home-clean.png`
- `bi-operacional-clean.png`
- `cotas-clean.png`

## Nota de publicacao

Quando as imagens forem adicionadas ao README, use uma observacao curta como:

`Capturas demonstrativas com dados ficticios ou anonimizados, sem URL, rota real exposta ou identificadores internos.`

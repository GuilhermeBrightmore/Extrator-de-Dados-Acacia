EXTRATOR DE PERFIL DA PLATAFORMA ACÁCIA
========================================

FUNÇÃO
-----
Este aplicativo abre um arquivo HTML salvo de um perfil da Plataforma Acácia e extrai:

1. IDENTIDADE ACADÊMICA
   - Nome
   - Grande Área
   - Área
   - Instituição
   - Ano da primeira orientação concluída
   - ID e URL do Currículo Lattes
   - Data de atualização do Lattes
   - Caminho do perfil

2. MÉTRICAS TOPOLÓGICAS
   - Descendência (DS)
   - Índice Genealógico (IG)
   - Fecundidade (FC)
   - Fertilidade (FT)
   - Gerações (G)
   - Relações (R)
   - Primos (PR)

3. POSICIONAMENTO PERCENTÍLICO DE CADA MÉTRICA
   - Entre doutores orientadores
   - No mesmo ano da primeira orientação
   - Na mesma Grande Área
   - Na mesma Área

O aplicativo apresenta os dados em abas e permite exportar os resultados em CSV e JSON.

REQUISITOS
----------
- Windows 10 ou 11
- Python 3.10 ou superior
- Tkinter, normalmente incluído no Python para Windows
- Internet apenas na primeira execução, para instalar beautifulsoup4

COMO INSTALAR E ABRIR
---------------------
1. Extraia todo o conteúdo do arquivo ZIP para uma pasta.
2. Dê dois cliques em INICIAR_EXTRATOR.bat.
3. Na primeira execução, o programa instalará automaticamente beautifulsoup4.
4. Quando a janela abrir, clique em "Abrir HTML".
5. Selecione o arquivo HTML salvo do perfil da Plataforma Acácia.
6. Consulte as abas Identidade acadêmica, Métricas topológicas, Posicionamento e Resumo textual.
7. Use "Exportar CSV" ou "Exportar JSON" para salvar os dados.

COMO SALVAR O HTML DO PERFIL
----------------------------
1. Abra o perfil do pesquisador na Plataforma Acácia.
2. Pressione Ctrl+S no navegador.
3. Escolha o tipo "Página da Web, somente HTML" ou equivalente.
4. Salve o arquivo com extensão .html.
5. Abra esse arquivo no aplicativo.

EXECUÇÃO MANUAL
---------------
No Prompt de Comando, dentro da pasta do programa:

    py -m pip install -r requirements.txt
    py extrator_perfil_acacia.py

OBSERVAÇÃO METODOLÓGICA
-----------------------
Os valores de posicionamento são percentis comparativos disponibilizados no HTML da Plataforma Acácia. Eles não representam percentuais de produtividade. O aplicativo conserva até duas casas decimais na tela e os valores completos no JSON.

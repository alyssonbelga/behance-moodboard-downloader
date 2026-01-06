# Behance Moodboard Downloader

Script simples para macOS que baixa todas as imagens possíveis de um projeto do Behance a partir de uma URL.

## Requisitos

- macOS
- Python 3.10+ instalado

## Instalação do Python (para leigos)

1. Abra o navegador e acesse: https://www.python.org/downloads/
2. Baixe o instalador para macOS.
3. Abra o arquivo `.pkg` baixado e siga os passos.
4. Após instalar, abra o **Terminal** e confirme:

```bash
python3 --version
```

## Como preparar o projeto

1. Abra o Terminal.
2. Entre na pasta do projeto:

```bash
cd /caminho/para/behance-moodboard-downloader
```

3. Crie um ambiente virtual (recomendado):

```bash
python3 -m venv .venv
source .venv/bin/activate
```

4. Instale as dependências:

```bash
pip install -r requirements.txt
```

5. Instale os navegadores do Playwright:

```bash
python -m playwright install
```

## Como usar

```bash
python behance_downloader.py "https://www.behance.net/gallery/123456789/Meu-Projeto" -o ./saida
```

O script vai baixar todas as imagens encontradas e gerar um `manifest.json` na pasta de saída.

## O que o script faz

1. Baixa o HTML do projeto.
2. Extrai imagens de:
   - `<img src>`
   - `srcset`
   - CSS `background-image`
   - URLs do CDN do Behance
3. Se encontrar poucas imagens, renderiza a página com Playwright e repete a extração.
4. Deduplica URLs e prioriza versões maiores (ex: `project_modules_max`).
5. Baixa arquivos com nomes sequenciais.
6. Gera `manifest.json` com:
   - `url_original`
   - `url_imagem`
   - `arquivo_salvo`
   - `tamanho_bytes`
   - `status`
   - `erro`

## Observações

- Se a URL for inválida ou houver bloqueio, o script mostra mensagens claras de erro.
- Alguns projetos podem ter menos imagens disponíveis via HTML, por isso o fallback com Playwright.

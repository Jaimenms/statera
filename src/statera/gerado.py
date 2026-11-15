"""Cabeçalho único dos arquivos que a plataforma gera e que ficam no repositório de conteúdo.

Todo arquivo gerado começa com o mesmo aviso, na sintaxe de comentário do formato. O aviso diz que o
arquivo é gerado, por qual comando, a partir de quais fontes e como mudar. É a convenção "Code
generated ... DO NOT EDIT." (Go e muitas outras ferramentas), em português. Além do aviso, o
repositório de conteúdo marca os mesmos arquivos no `.gitattributes` (`linguist-generated`) e no
VS Code (`files.readonlyInclude`), conforme a especificação 01.
"""

from __future__ import annotations

MARCA = "ARQUIVO GERADO pela plataforma Statera. Não edite à mão: a próxima geração desfaz a mudança."


def linhas(
    comando: str, fontes: str, *, regenerar: str = "statera sync", ci: bool = True, nota: str | None = None
) -> list[str]:
    saida = [
        MARCA,
        f"Gerado por `{comando}`. Fontes: {fontes}.",
        f"Para mudar: edite as fontes e rode `{regenerar}`."
        + (" O CI falha se este arquivo estiver desatualizado." if ci else ""),
    ]
    return saida + (nota.splitlines() if nota else [])


def cabecalho(comando: str, fontes: str, *, estilo: str = "#", **opcoes) -> str:
    """O aviso como comentário: `#` (YAML, CODEOWNERS), `%%` (Mermaid), `xml` (XML, SVG, HTML), `md`."""
    texto = linhas(comando, fontes, **opcoes)
    if estilo in ("#", "%%"):
        return "".join(f"{estilo} {linha}\n" for linha in texto)
    if estilo == "xml":
        return "<!--\n" + "".join(f"  {linha}\n" for linha in texto) + "-->\n"
    if estilo == "md":
        return "> " + "  \n> ".join(texto) + "\n"
    raise ValueError(f"estilo de comentário desconhecido: {estilo}")


def em_html(documento: str, cabecalho_xml: str) -> str:
    """Põe o aviso logo depois do `<!doctype html>`: antes dele, o navegador entra em modo quirks."""
    doctype, separador, resto = documento.partition("\n")
    if doctype.lower().startswith("<!doctype"):
        return doctype + separador + cabecalho_xml + resto
    return cabecalho_xml + documento

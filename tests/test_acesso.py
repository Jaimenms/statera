"""Como o público acessa um produto: distribuições declaradas e visualizações (painéis e gráficos)."""

from statera import acesso
from statera.projeto import atual


def _painel(**meta) -> dict:
    base = {"product": "teste", "tool": "Power BI", "reads": "published-edition", "updated": "monthly",
            "sharing": "broad", "visualization": "dashboard"}  # fmt: skip
    return {"name": "painel_teste", "url": "https://example.org/painel", "meta": {**base, **meta}}


def test_painel_completo_nao_tem_problemas(projeto_minimo):
    assert acesso.problemas_visualizacao(atual(), _painel()) == []


def test_painel_publico_le_a_edicao_publicada(projeto_minimo):
    problemas = acesso.problemas_visualizacao(atual(), _painel(reads="current"))
    assert any("edição publicada" in p for p in problemas)


def test_painel_interno_pode_ler_o_motor(projeto_minimo):
    assert acesso.problemas_visualizacao(atual(), _painel(reads="current", sharing="restricted")) == []


def test_painel_sem_url_nem_ferramenta(projeto_minimo):
    e = _painel(tool=None)
    e["url"] = None
    problemas = acesso.problemas_visualizacao(atual(), e)
    assert any("url" in p for p in problemas) and any("tool" in p for p in problemas)


def test_grafico_exige_texto_alternativo_e_porta_com_os_numeros(projeto_minimo):
    graficos = [{"title": "Saldo por UF", "port": "nao-existe"}]
    problemas = acesso.problemas_visualizacao(atual(), _painel(visualization="chart", charts=graficos))
    assert any("alt_text" in p for p in problemas)
    assert any("porta 'nao-existe'" in p for p in problemas)


def test_produto_de_divulgacao_sem_forma_de_acesso(projeto_minimo):
    problemas = acesso.problemas_distribuicoes(atual().produto("teste"))
    assert any("forma de acesso" in p for p in problemas)


def test_visualizacao_com_autor_sem_nome(projeto_minimo):
    problemas = acesso.problemas_visualizacao(atual(), _painel(authors=[{"unit": "Assessoria"}]))
    assert any("autor sem nome" in p for p in problemas)


def test_receita_registra_a_autoria(tmp_path):
    from statera.consume import recipes

    arquivo = tmp_path / "01-exemplo.py"
    arquivo.write_text("# Recipe: Exemplo\n# Layers: gold\n# Classification: amplo\n# Authors: Maria (Assessoria)\n")
    assert recipes._cabecalho(arquivo)["authors"] == "Maria (Assessoria)"

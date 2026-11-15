from statera import projeto


def test_descobre_unidades_e_espaco_de_nomes(projeto_minimo):
    p = projeto_minimo
    assert [(u.tipo, u.nome) for u in p.unidades] == [
        ("product", "consumidor"), ("product", "dados-base"), ("product", "teste"),
    ]  # fmt: skip
    assert p.produto("teste").urn == "br.gov.exemplo.teste"
    assert p.processos()[-1] == p.raiz / "products/teste/2-design/processes/to-be.yaml"


def test_alinhamento_e_visibilidade_dos_produtos(projeto_minimo):
    from statera import portas

    base, consumidor = projeto_minimo.produto("dados-base"), projeto_minimo.produto("consumidor")
    assert (base.alinhamento, base.interno) == ("source", True)
    assert (consumidor.alinhamento, consumidor.interno) == ("consumer", False)
    assert [u.nome for u in portas.produtos_de_entrada(consumidor)] == ["dados-base"]
    assert portas.consumidores_autorizados(base) == {"consumidor": "teste"}


def test_pastas_de_dados_por_fase_e_isolamento_em_testes(projeto_minimo, tmp_path, monkeypatch):
    u = projeto_minimo.produto("teste")
    assert u.dados(7) == u.pasta / "7-disseminate" / "data"
    monkeypatch.setenv("STATERA_DADOS", str(tmp_path / "isolado"))
    isolado = projeto.atual()
    assert (
        isolado.produto("teste").dados(7) == (tmp_path / "isolado" / "products" / "teste" / "7-disseminate").resolve()
    )
    assert isolado.warehouse.parent == (tmp_path / "isolado" / "engine").resolve()

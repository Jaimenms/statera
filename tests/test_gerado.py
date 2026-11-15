"""O aviso de arquivo gerado: mesmo texto em todos os formatos, sem quebrar o formato."""

import xml.etree.ElementTree as ET

import yaml

from statera import gerado


def test_yaml_continua_valido_e_comeca_pelo_aviso():
    texto = gerado.cabecalho("statera dbt generate", "references/*/*.odcs.yaml") + "version: 2\n"
    assert texto.startswith(f"# {gerado.MARCA}\n")
    assert "`statera sync`" in texto
    assert yaml.safe_load(texto) == {"version": 2}


def test_xml_continua_valido():
    texto = gerado.cabecalho("statera bpmn", "to-be.yaml", estilo="xml") + '<svg xmlns="http://www.w3.org/2000/svg"/>'
    assert ET.fromstring(texto).tag.endswith("svg")
    assert gerado.MARCA in texto


def test_html_mantem_o_doctype_na_primeira_linha():
    aviso = gerado.cabecalho("statera bpmn", "to-be.yaml", estilo="xml")
    html = gerado.em_html("<!doctype html>\n<html></html>\n", aviso)
    assert html.startswith("<!doctype html>\n<!--")


def test_documentacao_sem_conferencia_no_ci():
    texto = gerado.cabecalho("statera docs", "os metadados", regenerar="statera docs", ci=False, estilo="md")
    assert texto.startswith("> ") and "CI" not in texto

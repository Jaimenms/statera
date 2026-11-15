"""The generated-file notice: the same text in every format, without breaking the format."""

import xml.etree.ElementTree as ET

import yaml

from statera import generated


def test_yaml_stays_valid_and_starts_with_the_warning():
    text = generated.header("statera dbt generate", "references/*/*.odcs.yaml") + "version: 2\n"
    assert text.startswith(f"# {generated.MARKER}\n")
    assert "`statera sync`" in text
    assert yaml.safe_load(text) == {"version": 2}


def test_xml_stays_valid():
    text = generated.header("statera bpmn", "to-be.yaml", style="xml") + '<svg xmlns="http://www.w3.org/2000/svg"/>'
    assert ET.fromstring(text).tag.endswith("svg")
    assert generated.MARKER in text


def test_html_keeps_the_doctype_on_the_first_line():
    notice = generated.header("statera bpmn", "to-be.yaml", style="xml")
    html = generated.as_html("<!doctype html>\n<html></html>\n", notice)
    assert html.startswith("<!doctype html>\n<!--")


def test_docs_without_ci_check():
    text = generated.header("statera docs", "the metadata", regenerate="statera docs", ci=False, style="md")
    assert text.startswith("> ") and "CI" not in text

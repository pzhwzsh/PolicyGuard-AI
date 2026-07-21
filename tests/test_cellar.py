import httpx

from policyguard.application.cellar import CellarClient


def test_cellar_resolves_language_expression_to_xhtml() -> None:
    rdf = b'''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
      xmlns:cdm="http://publications.europa.eu/ontology/cdm#">
      <rdf:Description><cdm:work_has_expression
        rdf:resource="http://publications.europa.eu/resource/oj/TEST.ENG"/></rdf:Description>
    </rdf:RDF>'''

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/celex/32013R0655"):
            return httpx.Response(200, content=rdf)
        assert str(request.url).endswith("/resource/oj/TEST.ENG")
        assert request.headers["accept"] == "application/xhtml+xml"
        return httpx.Response(200, content=b"<html><main><p>EU rule</p></main></html>")

    client = CellarClient(httpx.Client(transport=httpx.MockTransport(handler)))
    result = client.fetch_xhtml("32013R0655")
    assert result.expression_url.endswith("TEST.ENG")
    assert b"EU rule" in result.content

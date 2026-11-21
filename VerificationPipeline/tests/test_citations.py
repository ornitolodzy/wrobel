from prawdowid.pipelines.verification.nodes.citations import quote_in_text


def test_exact_quote_with_typographic_quotes():
    assert quote_in_text('pismo "nie pochodzi" z MSZ', "Rzecznik: pismo „nie pochodzi” z MSZ.")


def test_fabricated_quote_rejected():
    assert not quote_in_text("MSZ potwierdza żądania terytorialne", "MSZ dementuje fałszywe pismo.")

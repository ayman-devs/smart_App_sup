"""Automatic fact check: invented names / numbers are caught and removed.

The examples are real mistakes the 4B model made during testing.
"""

from app.fact_check import key_terms, remove_terms, unsupported_terms

EVIDENCE = ("TANGER CASABLANCA RABAT MARRAKECH FES. UN RÉSEAU DE 19 CAMPUS. "
            "ENSTA Bretagne, CentraleSupélec. Depuis 1986. "
            "LE MOT DE MOHAMMED EL RHABI Directeur Général de l’EMSI")


def test_invented_cities_are_caught():
    answer = ("Les campus se trouvent dans les villes suivantes (p. 5) : "
              "Casablanca, Rabat, Fès, Agadir, Meknès, Oujda.")
    assert unsupported_terms(answer, EVIDENCE) == ["Agadir", "Meknès", "Oujda"]


def test_accents_and_case_do_not_matter():
    # "Fès" in the answer, "FES" in the brochure: same word.
    assert unsupported_terms("Un campus à Fès et à Rabat (p. 5).", EVIDENCE) == []


def test_true_answer_passes():
    answer = "Le directeur général est Mohammed El Rhabi (p. 5)."
    assert unsupported_terms(answer, EVIDENCE) == []


def test_numbers_are_checked_but_page_citations_are_not():
    answer = "L'EMSI compte 19 campus depuis 1986 et 25 000 lauréats (p. 5, p. 7)."
    assert unsupported_terms(answer, EVIDENCE) == ["25", "000"]


def test_first_word_of_a_list_item_is_checked():
    answer = "Partenaires :\n- **ENSTA Bretagne** (p. 35)\n- Polytechnique Montréal"
    assert "ENSTA" in key_terms(answer)
    assert unsupported_terms(answer, EVIDENCE) == ["Polytechnique", "Montréal"]


def test_remove_terms_keeps_the_good_items():
    answer = "Villes (p. 5) : Casablanca, Rabat, Agadir et Oujda"
    cleaned = remove_terms(answer, ["Agadir", "Oujda"])
    assert "Agadir" not in cleaned and "Oujda" not in cleaned
    assert "Casablanca" in cleaned and "Rabat" in cleaned


def test_remove_terms_drops_a_line_that_is_only_invented():
    answer = "Partenaires :\n- ENSTA Bretagne\n- Polytechnique Montréal"
    cleaned = remove_terms(answer, ["Polytechnique", "Montréal"])
    assert cleaned == "Partenaires :\n- ENSTA Bretagne"


def test_drop_notes():
    from app.fact_check import drop_notes
    text = "Villes : Tanger, Rabat (p. 2)\n\n*(Note : Tangier apparaît plusieurs fois.)*\nNote: x"
    assert drop_notes(text) == "Villes : Tanger, Rabat (p. 2)"

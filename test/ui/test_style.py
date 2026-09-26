"""Tests de la feuille de style et du pied de page."""

import pytest

from assistant_regles.ui.style import construire_css, hex_vers_rgb, html_pied_de_page, image_en_data_uri

COULEURS = {"couleur_fond": "#0F0E0C", "couleur_secondaire": "#1C1915",
            "couleur_accent": "#8B1A1A", "couleur_bordure": "#3A3226"}


def test_hex_vers_rgb():
    assert hex_vers_rgb("#8B1A1A") == (139, 26, 26)


def test_image_absente_ou_non_configuree(tmp_path):
    assert image_en_data_uri(None) is None
    assert image_en_data_uri(tmp_path / "absente.jpg") is None


def test_image_encodee(tmp_path):
    image = tmp_path / "fond.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfaux")
    assert image_en_data_uri(image).startswith("data:image/png;base64,")


def test_format_image_refuse(tmp_path):
    image = tmp_path / "fond.gif"
    image.write_bytes(b"GIF89a")
    with pytest.raises(ValueError, match="gif"):
        image_en_data_uri(image)


def test_css_avec_image_et_voile():
    css = construire_css(**COULEURS, opacite_image=0.2, image_data_uri="data:image/png;base64,AAA")
    assert 'url("data:image/png;base64,AAA")' in css
    assert "rgba(15, 14, 12, 0.80)" in css      # voile = couleur de fond à 1 - 0,2
    assert ".msg-utilisateur" in css


def test_css_sans_image():
    css = construire_css(**COULEURS, opacite_image=0.2, image_data_uri=None)
    assert "url(" not in css


def test_pied_de_page_echappe_et_omet_les_champs_vides():
    pied = html_pied_de_page("A <b>B</b>", "a@b.fr", "", "https://linkedin.com/in/x", "Non officiel.")
    assert "A &lt;b&gt;B&lt;/b&gt;" in pied
    assert 'href="mailto:a@b.fr"' in pied and "LinkedIn" in pied
    assert "GitHub" not in pied


def test_pied_de_page_au_dessus_de_la_zone_de_saisie():
    """Le pied de page doit passer devant stBottom, sinon ses liens ne sont pas cliquables."""
    css = construire_css(**COULEURS, opacite_image=0.2, image_data_uri=None)
    bloc = css.split(".pied-de-page {")[1].split("}")[0]
    assert "z-index: 1000" in bloc


def test_lien_externe_securise():
    pied = html_pied_de_page("A", "", "https://github.com/x", "", "")
    assert 'href="https://github.com/x" target="_blank" rel="noopener noreferrer"' in pied

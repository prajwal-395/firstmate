"""Repository-wide source surveys for declared input and output contracts.

These scans depend on Python source below ``library/``. The selector
declares that dependency so source changes select this expensive survey.
"""

from library.tools import contract_audit


def test_the_surveys_agree():
    assert contract_audit.survey_problems() == []

"""The LICENSE is the stock Apache 2.0 text, byte for byte.

This exists because the previous check did not check. CI grepped for two
strings — "Apache License" and "Version 2.0, January 2004" — and then printed
"LICENSE is stock Apache-2.0", which any file carrying those two headers would
satisfy. The file in the repository had its body reworded in four places: the
definitions of Work, Contribution and Contributor, and "modifications" changed
to "transformations" in the definition of Derivative Works. The header greps
passed the whole time.

Two things went wrong as a result. GitHub's licensee could not match the text,
so the repository reported `NOASSERTION` and the About sidebar showed "View
license" rather than "Apache-2.0" — the submission asks for a detectable
licence, and an undetectable one is the exact failure. And the README claimed
the licence was "stock and unmodified", which was not true.

A hash is the only check that means what the sentence above says. Line endings
are normalised first because the repository checks out CRLF on Windows, which
changes every byte without changing a word.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

#: SHA-256 of https://www.apache.org/licenses/LICENSE-2.0.txt with LF endings.
CANONICAL_APACHE_2_0 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"

_LICENSE = Path(__file__).parents[1] / "LICENSE"


def _normalised() -> bytes:
    return _LICENSE.read_bytes().replace(b"\r\n", b"\n")


class TestTheLicenceIsDetectable:
    def test_it_exists(self) -> None:
        assert _LICENSE.is_file(), "the submission requires a LICENSE at the repository root"

    def test_it_is_the_canonical_text(self) -> None:
        """Not "contains the header" — is the text.

        If this fails, do not edit the assertion. Replace LICENSE with
        https://www.apache.org/licenses/LICENSE-2.0.txt verbatim. A licence
        that has been reworded is a different licence, whatever it is titled,
        and GitHub will not label it.
        """
        digest = hashlib.sha256(_normalised()).hexdigest()
        assert digest == CANONICAL_APACHE_2_0, (
            "LICENSE is not the stock Apache 2.0 text; GitHub will report "
            "NOASSERTION and the About section will not show the licence"
        )

    def test_the_appendix_survived(self) -> None:
        """The part people delete by accident.

        Without the appendix the text is still recognisable to a reader but no
        longer matches, and it is the section that tells a user how to apply
        the licence to their own work.
        """
        text = _normalised().decode("utf-8")
        assert "APPENDIX: How to apply the Apache License to your work" in text

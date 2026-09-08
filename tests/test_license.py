"""The LICENSE body is the stock Apache 2.0 text, apart from the one line
Apache leaves for a name.

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

What changed since: the copyright holder is now named on the appendix line that
Apache ships as `Copyright [yyyy] [name of copyright owner]`. That line sits
after END OF TERMS AND CONDITIONS, inside the template the appendix tells you
to copy into your own source files, so filling it in alters no term of the
licence — but it does alter the bytes, and a plain hash of the file would
reject it.

So the hash is taken after normalising that one line back to the template. The
protection is unchanged where it matters: reword a definition, drop a section,
or edit anything outside that line, and this still fails.

Line endings are normalised first because the repository checks out CRLF on
Windows, which changes every byte without changing a word.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

#: SHA-256 of https://www.apache.org/licenses/LICENSE-2.0.txt with LF endings.
CANONICAL_APACHE_2_0 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"

#: The appendix line Apache leaves blank, as shipped.
TEMPLATE_COPYRIGHT = "   Copyright [yyyy] [name of copyright owner]"

_LICENSE = Path(__file__).parents[1] / "LICENSE"


def _text() -> str:
    return _LICENSE.read_bytes().replace(b"\r\n", b"\n").decode("utf-8")


def _body() -> str:
    """The licence with the appendix copyright line put back to the template."""
    return re.sub(r"^   Copyright .*$", TEMPLATE_COPYRIGHT, _text(), count=1, flags=re.M)


class TestTheLicenceIsDetectable:
    def test_it_exists(self) -> None:
        assert _LICENSE.is_file(), "the submission requires a LICENSE at the repository root"

    def test_the_body_is_the_canonical_text(self) -> None:
        """Not "contains the header" — is the text.

        If this fails, do not edit the assertion. Replace LICENSE with
        https://www.apache.org/licenses/LICENSE-2.0.txt verbatim and re-apply
        the copyright name to the appendix line. A licence whose terms have
        been reworded is a different licence, whatever it is titled, and GitHub
        will not label it.
        """
        digest = hashlib.sha256(_body().encode("utf-8")).hexdigest()
        assert digest == CANONICAL_APACHE_2_0, (
            "the LICENSE body differs from stock Apache 2.0 somewhere other than "
            "the appendix copyright line; GitHub will report NOASSERTION and the "
            "About section will not show the licence"
        )

    def test_exactly_one_copyright_line_was_changed(self) -> None:
        """The allowance is one line, and only the one Apache left blank.

        Without this, the normalisation above would quietly forgive a copyright
        notice inserted anywhere in the terms.
        """
        changed = [
            (a, b)
            for a, b in zip(_text().split("\n"), _body().split("\n"), strict=True)
            if a != b
        ]
        assert len(changed) <= 1, f"more than the appendix line differs: {changed[:3]}"
        if changed:
            assert changed[0][1] == TEMPLATE_COPYRIGHT

    def test_the_copyright_names_a_holder(self) -> None:
        """A licence attributed to nobody is the thing this replaced."""
        assert "Copyright [yyyy]" not in _text(), (
            "the appendix copyright line is still the unfilled template"
        )
        assert re.search(r"^   Copyright \d{4} \S", _text(), flags=re.M), (
            "the appendix copyright line should read 'Copyright <year> <holder>'"
        )

    def test_the_appendix_survived(self) -> None:
        """The part people delete by accident.

        Without the appendix the text is still recognisable to a reader but no
        longer matches, and it is the section that tells a user how to apply
        the licence to their own work.
        """
        assert "APPENDIX: How to apply the Apache License to your work" in _text()

    def test_the_terms_are_intact(self) -> None:
        """A spot check on the four definitions that were reworded last time."""
        text = _text()
        for phrase in (
            '"Work" shall mean the work of authorship, whether in Source or',
            "editorial revisions, annotations, elaborations, or other modifications",
            '"Contribution" shall mean any work of authorship, including',
            '"Contributor" shall mean Licensor and any individual or Legal Entity',
        ):
            assert phrase in text, f"a definition was reworded: {phrase[:48]}"

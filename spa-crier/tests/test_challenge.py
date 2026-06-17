"""The verification-challenge solver is the highest-stakes pure logic: a wrong answer fails a post.

The canonical example is the real one Moltbook served us during signup."""

from __future__ import annotations

import pytest

from spa_crier import challenge


def test_real_lobster_challenge():
    # The exact garbled text Moltbook returned; answer was 25 + 7 = 32.00.
    text = (
        "A] LoBsTeR S^wImS[ aT tW/eN tY- FiV e] cE mMeT/eR s PeR] sE cOnD, uM- aNd[ ThEn+ A dDs] "
        "SeV eN~ tO/ iTs VeLoOcItY, wH-aTs] ThE/ nEw- SpEeD?"
    )
    assert challenge.solve_locally(text) == "32.00"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("What is 12 plus 8?", "20.00"),
        ("Subtract 5 from 20", "15.00"),
        ("multiply 6 times 7", "42.00"),
        ("What is one hundred divided by 4?", "25.00"),
        ("A crab has thirty legs and gains twelve more", "42.00"),
        ("forty minus fifteen", "25.00"),
    ],
)
def test_varied_clean_problems(text, expected):
    assert challenge.solve_locally(text) == expected


def test_word_number_parsing():
    assert challenge.extract_numbers("twenty five and seven") == [25.0, 7.0]
    assert challenge.extract_numbers("one hundred plus 3") == [100.0, 3.0]


def test_op_detection():
    assert challenge.detect_op("adds seven to") == "+"
    assert challenge.detect_op("loses three") == "-"
    assert challenge.detect_op("the product of") == "*"


def test_glued_extractor_reassembles_fragments():
    # Moltbook's real obfuscation injects spaces *inside* words: "tW eN tY FiV e" = twenty five.
    assert challenge.extract_numbers_glued("tW/eN tY- FiV e and SeV eN") == [25.0, 7.0]


def test_op_detection_through_in_word_spacing():
    # "a dds" should still read as "adds" (+); "mi nus" as "minus" (-).
    assert challenge.detect_op("um and then a dds seven") == "+"
    assert challenge.detect_op("it then mi nus three") == "-"


def test_unsolvable_returns_none():
    assert challenge.solve_locally("describe a sunset in detail") is None
    assert challenge.solve_locally("only one number: 5") is None


def test_format_two_decimals():
    assert challenge.format_answer(32) == "32.00"
    assert challenge.format_answer(1.5) == "1.50"

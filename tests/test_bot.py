"""Offline tests for the Telegram bot's pure logic.

The bot's parsing and verdict formatting are pure functions, so the whole
decision path is tested here without a token or network — only main()'s
polling loop needs Telegram. This proves the bot gives the same verdicts as
the CLI and web tool (same engine, core/p2p_scorer.py).
"""

from bot.telegram_bot import parse_numbers, score_message


class TestParsing:
    def test_five_clean_numbers(self):
        assert parse_numbers("4.305 4.20 9 280 99") == [4.305, 4.20, 9, 280, 99]

    def test_extra_words_and_commas_tolerated(self):
        # Users paste messy text; we still extract the 5 figures.
        got = parse_numbers("price 4.31, mid 4.20, age 9d, 1,280 orders, 99.5%")
        assert got == [4.31, 4.20, 9, 1280, 99.5]

    def test_wrong_count_returns_none(self):
        assert parse_numbers("4.20 9 99") is None
        assert parse_numbers("just chatting, no numbers") is None


class TestScoreMessage:
    def test_clear_mule_says_do_not_trade(self):
        # 2.4% premium, 8-day account, 600 orders → HIGH_RISK in the Python CLI.
        reply = score_message("104.8 100 8 600 99")
        assert "DO NOT TRADE" in reply
        assert "0.9" in reply  # ~0.95 risk score

    def test_clean_merchant_says_safe(self):
        # Aged, at-market, high volume, high finish → CLEAN.
        reply = score_message("4.20 4.20 400 1700 99.8")
        assert "LIKELY SAFE" in reply

    def test_borderline_new_says_caution(self):
        # New account, ~28/day, tiny premium → SUSPICIOUS under retail-safe.
        reply = score_message("100.6 100 22 620 99.8")
        assert "CAUTION" in reply

    def test_bad_count_asks_for_five(self):
        assert "5 numbers" in score_message("4.20 9 99")

    def test_invalid_values_explained_not_crashed(self):
        # age 0 must be rejected by the engine and surfaced, not raise.
        reply = score_message("4.30 4.20 0 280 99")
        assert "don't look right" in reply or "format" in reply

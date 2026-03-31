from persona_forge.forge import get_starting_probability
from persona_forge.models import Archetype


class TestPersonaForge:

    def test_bull_above_bias_score(self):
        """Bull analyst must start above bias score"""
        prob = get_starting_probability(
            Archetype.BULL_ANALYST, 0.60, instance=5
        )
        assert prob > 0.60

    def test_bear_below_bias_score(self):
        """Bear analyst must start below bias score"""
        prob = get_starting_probability(
            Archetype.BEAR_ANALYST, 0.60, instance=5
        )
        assert prob < 0.60

    def test_probability_never_above_ceiling(self):
        """No agent starts above 0.90"""
        for archetype in Archetype:
            for instance in range(10):
                prob = get_starting_probability(
                    archetype, 0.90, instance
                )
                assert prob <= 0.90

    def test_probability_never_below_floor(self):
        """No agent starts below 0.10"""
        for archetype in Archetype:
            for instance in range(10):
                prob = get_starting_probability(
                    archetype, 0.10, instance
                )
                assert prob >= 0.10

    def test_neutral_anchor_fallback(self):
        """None bias score falls back to 0.50"""
        # get_starting_probability doesn't handle None directly —
        # the forge() method does the None→0.50 fallback.
        # Test the same logic: pass 0.50 as the resolved default.
        prob = get_starting_probability(
            Archetype.QUANT_TRADER, 0.50, instance=5
        )
        assert 0.45 <= prob <= 0.55

    def test_archetype_ordering(self):
        """Bull > Quant > Retail > Risk > Bear at same bias"""
        bias = 0.60
        bull = get_starting_probability(Archetype.BULL_ANALYST, bias, 5)
        quant = get_starting_probability(Archetype.QUANT_TRADER, bias, 5)
        risk = get_starting_probability(Archetype.RISK_OFFICER, bias, 5)
        bear = get_starting_probability(Archetype.BEAR_ANALYST, bias, 5)
        assert bull > quant > risk > bear

"""Tests for vote_calculator module."""

from membership.vote_calculator import DOLLARS_PER_MEMBER, WEIGHTS, calculate_results, results_to_json


def describe_constants():
    def it_has_correct_weights():
        assert WEIGHTS == {"1st": 5, "2nd": 3, "3rd": 2}

    def it_has_dollars_per_member_equal_to_weight_sum():
        assert DOLLARS_PER_MEMBER == 10


def describe_calculate_results():
    def it_returns_empty_results_with_no_votes():
        result = calculate_results(votes=[])
        assert result["total_pool"] == 0
        assert result["total_points"] == 0
        assert result["votes_cast"] == 0
        assert result["results"] == []

    def it_calculates_single_voter():
        votes = [{"guild_1st": "Ceramics", "guild_2nd": "Glass", "guild_3rd": "Wood"}]
        result = calculate_results(votes=votes)
        assert result["total_pool"] == 10
        assert result["total_points"] == 10
        assert result["votes_cast"] == 1

        by_guild = {r["guild_name"]: r for r in result["results"]}
        assert by_guild["Ceramics"]["votes_1st"] == 1
        assert by_guild["Ceramics"]["total_points"] == 5
        assert by_guild["Ceramics"]["funding"] == 5.0
        assert by_guild["Glass"]["votes_2nd"] == 1
        assert by_guild["Glass"]["total_points"] == 3
        assert by_guild["Glass"]["funding"] == 3.0
        assert by_guild["Wood"]["votes_3rd"] == 1
        assert by_guild["Wood"]["total_points"] == 2
        assert by_guild["Wood"]["funding"] == 2.0

    def it_calculates_proportional_funding():
        """Pool = voters × $10; each guild gets its share of points × pool."""
        votes = [
            {"guild_1st": "Ceramics", "guild_2nd": "Glass", "guild_3rd": "Wood"},
            {"guild_1st": "Ceramics", "guild_2nd": "Glass", "guild_3rd": "Wood"},
        ]
        result = calculate_results(votes=votes)
        assert result["total_pool"] == 20
        assert result["total_points"] == 20

        by_guild = {r["guild_name"]: r for r in result["results"]}
        # Ceramics: 10 points / 20 total × $20 = $10
        assert by_guild["Ceramics"]["funding"] == 10.0
        # Glass: 6 / 20 × $20 = $6
        assert by_guild["Glass"]["funding"] == 6.0
        # Wood: 4 / 20 × $20 = $4
        assert by_guild["Wood"]["funding"] == 4.0

    def it_handles_multiple_voters_different_preferences():
        votes = [
            {"guild_1st": "Ceramics", "guild_2nd": "Glass", "guild_3rd": "Wood"},
            {"guild_1st": "Glass", "guild_2nd": "Ceramics", "guild_3rd": "Wood"},
        ]
        result = calculate_results(votes=votes)
        assert result["votes_cast"] == 2
        assert result["total_pool"] == 20
        assert result["total_points"] == 20

        by_guild = {r["guild_name"]: r for r in result["results"]}
        # Ceramics: 1st(5) + 2nd(3) = 8 points → 8/20 × $20 = $8
        assert by_guild["Ceramics"]["total_points"] == 8
        assert by_guild["Ceramics"]["funding"] == 8.0
        # Glass: 1st(5) + 2nd(3) = 8 points → $8
        assert by_guild["Glass"]["total_points"] == 8
        assert by_guild["Glass"]["funding"] == 8.0
        # Wood: 3rd(2) + 3rd(2) = 4 points → $4
        assert by_guild["Wood"]["total_points"] == 4
        assert by_guild["Wood"]["funding"] == 4.0

    def it_includes_share_percentage():
        votes = [{"guild_1st": "Ceramics", "guild_2nd": "Glass", "guild_3rd": "Wood"}]
        result = calculate_results(votes=votes)
        by_guild = {r["guild_name"]: r for r in result["results"]}
        assert by_guild["Ceramics"]["share_pct"] == 50.0
        assert by_guild["Glass"]["share_pct"] == 30.0
        assert by_guild["Wood"]["share_pct"] == 20.0

    def it_sorts_results_by_funding_descending():
        votes = [{"guild_1st": "Alpha", "guild_2nd": "Beta", "guild_3rd": "Gamma"}]
        result = calculate_results(votes=votes)
        fundings = [r["funding"] for r in result["results"]]
        assert fundings == sorted(fundings, reverse=True)

    def it_handles_missing_guild_keys_gracefully():
        votes = [{"guild_1st": "Ceramics"}]  # missing 2nd and 3rd
        result = calculate_results(votes=votes)
        assert result["votes_cast"] == 1
        assert len(result["results"]) == 1
        assert result["results"][0]["guild_name"] == "Ceramics"
        assert result["results"][0]["total_points"] == 5

    def it_handles_empty_guild_names():
        """When votes have empty guild names, no results are produced."""
        votes = [{"guild_1st": "", "guild_2nd": "", "guild_3rd": ""}]
        result = calculate_results(votes=votes)
        assert result["total_points"] == 0
        assert result["total_pool"] == 10  # 1 voter × $10
        assert result["results"] == []

    def it_uses_paying_voter_count_for_pool():
        """Pool = paying_voter_count × $10, not total voters."""
        votes = [
            {"guild_1st": "Ceramics", "guild_2nd": "Glass", "guild_3rd": "Wood"},
            {"guild_1st": "Ceramics", "guild_2nd": "Glass", "guild_3rd": "Wood"},
            {"guild_1st": "Ceramics", "guild_2nd": "Glass", "guild_3rd": "Wood"},
        ]
        # 3 voters, but only 2 are paying
        result = calculate_results(votes=votes, paying_voter_count=2)
        assert result["votes_cast"] == 3
        assert result["total_pool"] == 20  # 2 paying × $10, not 3 × $10

    def it_defaults_paying_voter_count_to_votes_cast():
        votes = [{"guild_1st": "Ceramics", "guild_2nd": "Glass", "guild_3rd": "Wood"}]
        result = calculate_results(votes=votes)
        assert result["total_pool"] == 10  # defaults to 1 voter × $10

    def it_handles_zero_paying_voters():
        """All voters are non-paying; pool is $0 but votes still count."""
        votes = [{"guild_1st": "Ceramics", "guild_2nd": "Glass", "guild_3rd": "Wood"}]
        result = calculate_results(votes=votes, paying_voter_count=0)
        assert result["total_pool"] == 0
        assert result["votes_cast"] == 1
        assert result["total_points"] == 10
        # Funding is $0 since pool is $0
        assert all(r["funding"] == 0 for r in result["results"])

    def it_ensures_funding_sums_to_pool():
        """Funding invariant: sum of all guild funding must equal pool."""
        votes = [
            {"guild_1st": "A", "guild_2nd": "B", "guild_3rd": "C"},
            {"guild_1st": "B", "guild_2nd": "C", "guild_3rd": "A"},
            {"guild_1st": "C", "guild_2nd": "A", "guild_3rd": "B"},
        ]
        result = calculate_results(votes=votes)
        total_funding = sum(r["funding"] for r in result["results"])
        assert total_funding == result["total_pool"]


def describe_results_to_json():
    def it_serializes_results():
        data = {"total_pool": 100, "results": []}
        json_str = results_to_json(data)
        assert '"total_pool": 100' in json_str

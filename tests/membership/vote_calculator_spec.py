"""Tests for vote_calculator module."""

from membership.vote_calculator import DOLLARS_PER_MEMBER, WEIGHTS, calculate_results, results_to_json


def describe_constants():
    def it_has_correct_weights():
        assert WEIGHTS == {"1st": 5, "2nd": 3, "3rd": 2}

    def it_has_dollars_per_member_equal_to_weight_sum():
        assert DOLLARS_PER_MEMBER == 10


def describe_calculate_results():
    def it_returns_empty_results_with_no_votes():
        result = calculate_results(votes=[], eligible_member_count=10)
        assert result["total_pool"] == 100
        assert result["total_weighted"] == 0
        assert result["non_vote_dollars"] == 100
        assert result["votes_cast"] == 0
        assert result["results"] == []

    def it_calculates_single_voter():
        votes = [{"guild_1st": "Ceramics", "guild_2nd": "Glass", "guild_3rd": "Wood"}]
        result = calculate_results(votes=votes, eligible_member_count=1)
        assert result["total_pool"] == 10
        assert result["total_weighted"] == 10
        assert result["non_vote_dollars"] == 0
        assert result["votes_cast"] == 1

        by_guild = {r["guild_name"]: r for r in result["results"]}
        assert by_guild["Ceramics"]["votes_1st"] == 1
        assert by_guild["Ceramics"]["weighted_amount"] == 5
        assert by_guild["Glass"]["votes_2nd"] == 1
        assert by_guild["Glass"]["weighted_amount"] == 3
        assert by_guild["Wood"]["votes_3rd"] == 1
        assert by_guild["Wood"]["weighted_amount"] == 2

    def it_redistributes_non_vote_dollars():
        votes = [{"guild_1st": "Ceramics", "guild_2nd": "Glass", "guild_3rd": "Wood"}]
        result = calculate_results(votes=votes, eligible_member_count=3)
        # Pool = 30, weighted = 10, non_vote = 20
        assert result["total_pool"] == 30
        assert result["non_vote_dollars"] == 20

        by_guild = {r["guild_name"]: r for r in result["results"]}
        # Ceramics: 5 weighted + (5/10 * 20) = 5 + 10 = 15
        assert by_guild["Ceramics"]["disbursement"] == 15.0
        # Glass: 3 + (3/10 * 20) = 3 + 6 = 9
        assert by_guild["Glass"]["disbursement"] == 9.0
        # Wood: 2 + (2/10 * 20) = 2 + 4 = 6
        assert by_guild["Wood"]["disbursement"] == 6.0

    def it_handles_multiple_voters():
        votes = [
            {"guild_1st": "Ceramics", "guild_2nd": "Glass", "guild_3rd": "Wood"},
            {"guild_1st": "Glass", "guild_2nd": "Ceramics", "guild_3rd": "Wood"},
        ]
        result = calculate_results(votes=votes, eligible_member_count=2)
        assert result["votes_cast"] == 2
        assert result["total_pool"] == 20
        assert result["total_weighted"] == 20
        assert result["non_vote_dollars"] == 0

        by_guild = {r["guild_name"]: r for r in result["results"]}
        # Ceramics: 1st(5) + 2nd(3) = 8
        assert by_guild["Ceramics"]["weighted_amount"] == 8
        # Glass: 1st(5) + 2nd(3) = 8
        assert by_guild["Glass"]["weighted_amount"] == 8
        # Wood: 3rd(2) + 3rd(2) = 4
        assert by_guild["Wood"]["weighted_amount"] == 4

    def it_sorts_results_by_disbursement_descending():
        votes = [{"guild_1st": "Alpha", "guild_2nd": "Beta", "guild_3rd": "Gamma"}]
        result = calculate_results(votes=votes, eligible_member_count=1)
        disbursements = [r["disbursement"] for r in result["results"]]
        assert disbursements == sorted(disbursements, reverse=True)

    def it_handles_missing_guild_keys_gracefully():
        votes = [{"guild_1st": "Ceramics"}]  # missing 2nd and 3rd
        result = calculate_results(votes=votes, eligible_member_count=1)
        assert result["votes_cast"] == 1
        assert len(result["results"]) == 1
        assert result["results"][0]["guild_name"] == "Ceramics"
        assert result["results"][0]["weighted_amount"] == 5

    def it_handles_zero_eligible_members():
        result = calculate_results(votes=[], eligible_member_count=0)
        assert result["total_pool"] == 0
        assert result["non_vote_dollars"] == 0

    def it_handles_zero_total_weighted_with_empty_guild_names():
        """When votes have empty guild names, total_weighted is 0; redistributed should be 0."""
        votes = [{"guild_1st": "", "guild_2nd": "", "guild_3rd": ""}]
        result = calculate_results(votes=votes, eligible_member_count=5)
        assert result["total_weighted"] == 0
        assert result["non_vote_dollars"] == 50
        assert result["results"] == []


def describe_results_to_json():
    def it_serializes_results():
        data = {"total_pool": 100, "results": []}
        json_str = results_to_json(data)
        assert '"total_pool": 100' in json_str

from evals.runners.run_business_mutations import run_mutants


def test_targeted_business_mutations_are_detected():
    result = run_mutants()
    assert result["counts"] == {"killed": 9, "survived": 0, "invalid": 0}
    assert result["mutation_score"] == 1.0
